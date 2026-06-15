"""HTTP + SSE API for the ITR bot.

All endpoints are session-scoped. Long-running agent work (extraction,
reconciliation, computation) streams progress over the SSE endpoint while the
POST returns the final structured result.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from ..agents.chat import answer as chat_answer
from ..agents.chat import answer_stream as chat_answer_stream
from ..agents.doc_intel.extract import extract_document
from ..agents.guidance import build_guided_filing, guidance_intro
from ..agents.intake import (
    GAP_QUESTIONS,
    build_base_checklist,
    build_profile_from_docs_and_gaps,
    infer_profile_fields,
    select_form,
)
from ..agents.orchestrator import run_computation
from ..agents.reconcile import reconcile_documents
from ..compute.consolidate import consolidate_detailed
from ..schemas.compute import TaxInput
from ..schemas.documents import DocType, DocumentExtraction

# Doc types where a filer may upload more than one file (e.g. multiple employers,
# brokers, or deductors). Stored as a list in session state.
_MULTI_UPLOAD_TYPES = {
    DocType.FORM16,       # one per employer
    DocType.FORM16A,      # one per non-salary deductor
    DocType.BROKER_PNL,   # one per broker
    DocType.INTEREST_CERT,  # one per bank
    DocType.DONATION_80G,   # one per recipient / batch
}
from ..schemas.events import EventType
from ..schemas.profile import ITRForm, UserProfile
from .events import bus
from .logging_setup import get_logger, session_id_var
from .store import store

logger = get_logger(__name__)
router = APIRouter(prefix="/api")

# Strong references to in-flight background extraction tasks (prevents GC).
_bg_tasks: set[asyncio.Task] = set()


def _require(session_id: str) -> None:
    if not store.exists(session_id):
        raise HTTPException(status_code=404, detail="Unknown session")
    session_id_var.set(session_id)


def _flatten_docs(state: dict) -> list[DocumentExtraction]:
    """Flatten the session document store into a flat list of DocumentExtraction.

    Multi-upload slots (Form 16, broker P&L, etc.) are stored as lists; single
    slots as dicts. Both are normalised here.
    """
    docs: list[DocumentExtraction] = []
    for value in state.get("documents", {}).values():
        if isinstance(value, list):
            docs.extend(DocumentExtraction(**d) for d in value)
        else:
            docs.append(DocumentExtraction(**value))
    return docs


@router.post("/session")
def create_session() -> dict:
    """Create a new filing session."""
    return {"session_id": store.create()}


@router.get("/base-checklist")
def get_base_checklist() -> dict:
    """Return the docs-first upload checklist (mandatory + optional documents)."""
    return {"checklist": [c.model_dump() for c in build_base_checklist()]}


@router.post("/chat")
async def chat(payload: dict = Body(...)) -> dict:
    """Answer an in-scope ITR/portal/website question (simple help chatbot)."""
    message = str(payload.get("message", "")).strip()
    if not message:
        return {"reply": "Ask me anything about filing your ITR or using this site."}
    history = payload.get("history") or []
    reply = await chat_answer(message, history)
    return {"reply": reply or "Sorry, I could not generate a reply right now."}


@router.post("/chat/stream")
async def chat_stream(payload: dict = Body(...)) -> EventSourceResponse:
    """Stream a chat reply token-by-token over SSE.

    Emits ``status`` (thinking), ``delta`` (text chunks), and ``done`` events;
    ``error`` is emitted if generation fails.
    """
    message = str(payload.get("message", "")).strip()
    history = payload.get("history") or []

    async def gen():
        if not message:
            yield {"event": "delta", "data": json.dumps(
                {"text": "Ask me anything about filing your ITR or using this site."})}
            yield {"event": "done", "data": "{}"}
            return
        yield {"event": "status", "data": json.dumps({"state": "thinking"})}
        produced = False
        try:
            async for chunk in chat_answer_stream(message, history):
                if chunk:
                    produced = True
                    yield {"event": "delta", "data": json.dumps({"text": chunk})}
        except Exception:
            logger.exception("chat stream failed")
            yield {"event": "error", "data": json.dumps(
                {"text": "Sorry, I hit an error. Please try again."})}
            return
        if not produced:
            yield {"event": "delta", "data": json.dumps(
                {"text": "Sorry, I could not generate a reply right now."})}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(gen())


@router.get("/session/{session_id}/state")
def get_state(session_id: str) -> dict:
    """Return the full stored state for a session."""
    _require(session_id)
    return store.get(session_id)


_CG_SIGNAL_FIELDS = ("stcg_111a", "ltcg_112a", "stcg_other", "ltcg_other", "vda_gain")


@router.post("/session/{session_id}/analyze-gaps")
async def analyze_gaps(session_id: str) -> dict:
    """Infer everything possible from documents; return only the unanswerable gaps.

    Consolidates the uploaded documents, derives every profile flag the
    documents reveal (income heads, regime, capital-gains signals), and returns
    the minimal set of questions that genuinely cannot be read from any document
    along with a friendly summary of what was inferred and any optional doc the
    AIS suggests is missing.
    """
    _require(session_id)
    state = store.get(session_id)
    docs = _flatten_docs(state)
    age = int(state.get("profile", {}).get("age", 30))
    ti, discrepancies = consolidate_detailed(docs, age=age)

    inferred = infer_profile_fields(ti)
    store.update(session_id, {
        "tax_input": ti.model_dump(),
        "discrepancies": [d.model_dump() for d in discrepancies],
        "inferred_profile": inferred,
    })

    summary: list[str] = []
    gross = sum(s.gross_salary for s in ti.salaries)
    if gross:
        summary.append(f"Salary: \u20b9{gross:,.0f} across {len(ti.salaries)} employer(s)")
    summary.append(f"Filing regime (from Form 16): {ti.filing_regime.upper()}")
    if ti.tds_total:
        summary.append(f"TDS / taxes paid: \u20b9{ti.tds_total:,.0f}")
    if inferred.get("has_savings_interest") or inferred.get("has_fd_interest"):
        summary.append("Interest income detected")
    if inferred.get("has_dividends"):
        summary.append("Dividend income detected")
    if inferred.get("has_capital_gains"):
        summary.append("Capital-gains activity detected")
    if ti.professional_fees > 0:
        summary.append(
            f"⚠ Professional/freelance income ₹{ti.professional_fees:,.0f} detected "
            f"(Sec 194J from AIS) — ITR-2 required, not ITR-1")

    suggested: list[dict] = []
    has_cg = any(getattr(ti.capital_gains, f) for f in _CG_SIGNAL_FIELDS)
    if has_cg and "broker_pnl" not in state.get("documents", {}):
        suggested.append({
            "doc_type": "broker_pnl",
            "title": "Broker Tax P&L",
            "why": "Your AIS shows securities activity. Upload it for an accurate "
                   "short/long-term split, or confirm the figures on the next screen."})

    return {
        "regime": ti.filing_regime,
        "inferred": summary,
        "gap_questions": GAP_QUESTIONS,
        "suggested_docs": suggested,
    }


@router.post("/session/{session_id}/submit-gaps")
async def submit_gaps(session_id: str, answers: dict = Body(...)) -> dict:
    """Build the profile from inferred fields + gap answers and select the form."""
    _require(session_id)
    state = store.get(session_id)
    if state.get("tax_input"):
        ti = TaxInput(**state["tax_input"])
    else:
        ti, _ = consolidate_detailed(_flatten_docs(state), age=int(answers.get("age", 30)))

    profile = build_profile_from_docs_and_gaps(ti, answers)
    decision = select_form(profile)
    payload = {"profile": profile.model_dump(), "decision": decision.model_dump()}
    store.update(session_id, payload)
    return payload


@router.post("/session/{session_id}/documents")
async def upload_document(
    session_id: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    password: str | None = Form(default=None),
    upload_id: str | None = Form(default=None),
) -> dict:
    """Accept a document and start extraction in the background.

    The file is persisted immediately and extraction is scheduled as a
    background task so the client can keep uploading without waiting. Progress
    and the final result stream over SSE, tagged with ``upload_id`` so the UI
    tracks concurrent uploads of the same doc type independently.
    """
    _require(session_id)
    dtype = DocType(doc_type)
    upload_id = upload_id or uuid.uuid4().hex[:12]
    data = await file.read()
    filename = file.filename or dtype.value
    store.upload_dir(session_id).joinpath(filename).write_bytes(data)

    task = asyncio.create_task(_extract_and_store(
        session_id, dtype, filename, data,
        file.content_type or "application/pdf", password, upload_id))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return {"upload_id": upload_id, "doc_type": dtype.value, "status": "queued"}


async def _extract_and_store(
    session_id: str, dtype: DocType, filename: str, data: bytes,
    mime: str, password: str | None, upload_id: str,
) -> None:
    """Run extraction for one uploaded file, using the content cache when available.

    Cache is keyed by SHA-256 of the raw file bytes so the same file never costs
    an LLM call twice, regardless of filename or session. Encrypted files are cached
    by their ciphertext, so a wrong-password retry will miss (correct behaviour).
    """
    session_id_var.set(session_id)
    try:
        cached = store.cache_get(data)
        if cached:
            await bus.emit(session_id, EventType.DOC_COMPLETED,
                           f"Loaded {dtype.value} from cache",
                           doc_type=dtype.value, upload_id=upload_id)
            await store.add_document(
                session_id, dtype.value, cached,
                multi=dtype in _MULTI_UPLOAD_TYPES)
            return

        extraction = await extract_document(
            session_id, dtype, filename, data,
            mime=mime, password=password, upload_id=upload_id)
        store.cache_set(data, extraction.model_dump())
        await store.add_document(
            session_id, dtype.value, extraction.model_dump(),
            multi=dtype in _MULTI_UPLOAD_TYPES)
    except ValueError as exc:
        logger.warning("extraction input error", extra={
            "doc_type": dtype.value, "upload_id": upload_id, "error": str(exc)})
        await bus.emit(session_id, EventType.ERROR, str(exc),
                       doc_type=dtype.value, upload_id=upload_id)
    except Exception:
        logger.exception("background extraction failed",
                         extra={"doc_type": dtype.value, "upload_id": upload_id})
        await bus.emit(session_id, EventType.ERROR,
                       f"Extraction failed for {dtype.value}. Please retry.",
                       doc_type=dtype.value, upload_id=upload_id)


@router.post("/session/{session_id}/documents/{doc_type}/review")
def review_document(
    session_id: str, doc_type: str, edits: dict = Body(...),
    index: int = 0,
) -> dict:
    """Apply human review edits to extracted field values.

    Edits map ``{field_name: new_value}``; edited fields get confidence 1.0.
    For multi-upload doc types (Form 16, broker P&L, etc.) ``index`` selects
    which uploaded copy to edit (0-based).
    """
    _require(session_id)
    state = store.get(session_id)
    docs = state.get("documents", {})
    if doc_type not in docs:
        raise HTTPException(status_code=404, detail="Document not extracted yet")
    slot = docs[doc_type]
    if isinstance(slot, list):
        if index >= len(slot):
            raise HTTPException(status_code=404, detail="Index out of range")
        raw = slot[index]
    else:
        raw = slot
    doc = DocumentExtraction(**raw)
    for field in doc.fields:
        if field.name in edits:
            field.value = edits[field.name]
            field.confidence = 1.0
            field.flagged = False
    doc.status = "validated"
    if isinstance(slot, list):
        slot[index] = doc.model_dump()
        docs[doc_type] = slot
    else:
        docs[doc_type] = doc.model_dump()
    store.update(session_id, {"documents": docs})
    return doc.model_dump()


@router.post("/session/{session_id}/reconcile")
async def run_reconcile(session_id: str) -> dict:
    """Run cross-document reconciliation."""
    _require(session_id)
    state = store.get(session_id)
    docs = _flatten_docs(state)
    issues, explanation = await reconcile_documents(session_id, docs)
    payload = {"reconciliation": {
        "issues": [i.model_dump() for i in issues], "explanation": explanation}}
    store.update(session_id, payload)
    return payload["reconciliation"]


def _consolidate_from_docs(session_id: str, state: dict) -> dict:
    """Consolidate documents into a prefilled, editable ``TaxInput`` for review.

    Builds the canonical input and surfaces cross-source discrepancies. The
    result is persisted as the ``tax_input`` the review screen lets the user
    override; profile/form selection happens earlier in the gap step.
    """
    docs = _flatten_docs(state)
    age = int(state.get("profile", {}).get("age", 30))
    ti, discrepancies = consolidate_detailed(docs, age=age)
    payload = {
        "tax_input": ti.model_dump(),
        "discrepancies": [d.model_dump() for d in discrepancies],
    }
    store.update(session_id, payload)
    return payload


@router.post("/session/{session_id}/consolidate")
def consolidate_review(session_id: str) -> dict:
    """Build the prefilled, editable consolidated input and its discrepancies."""
    _require(session_id)
    return _consolidate_from_docs(session_id, store.get(session_id))


@router.put("/session/{session_id}/tax-input")
def override_tax_input(session_id: str, payload: dict = Body(...)) -> dict:
    """Persist user overrides to the consolidated input (docs are prefill only).

    The full edited ``TaxInput`` is sent back and stored verbatim, so any value
    the user changed on the review screen wins over the document-derived value.
    """
    _require(session_id)
    ti = TaxInput(**payload)
    store.update(session_id, {"tax_input": ti.model_dump()})
    return ti.model_dump()


@router.post("/session/{session_id}/compute")
async def compute(session_id: str) -> dict:
    """Run the streamed computation, honouring any user-edited consolidated input."""
    _require(session_id)
    state = store.get(session_id)

    # Use the (possibly user-overridden) consolidated input if the review step
    # produced one; otherwise consolidate fresh from documents now.
    if not state.get("tax_input"):
        _consolidate_from_docs(session_id, state)
        state = store.get(session_id)

    ti = TaxInput(**state["tax_input"])
    profile = UserProfile(**state.get("profile", {}))
    form = ITRForm(state.get("decision", {}).get("form", ITRForm.ITR2.value))

    result = await run_computation(session_id, ti, form, profile)

    store.update(session_id, {"tax_input": ti.model_dump(), **result})
    return {"tax_input": ti.model_dump(), **result}


@router.get("/session/{session_id}/guidance")
async def guidance(session_id: str) -> dict:
    """Build the guided, copy-paste filing walkthrough for the chosen regime."""
    _require(session_id)
    state = store.get(session_id)
    if "tax_input" not in state or "computation" not in state:
        raise HTTPException(status_code=409, detail="Run computation first")
    ti = TaxInput(**state["tax_input"])
    from ..schemas.compute import TaxComputation
    computation = TaxComputation(**state["computation"])
    form = ITRForm(state.get("decision", {}).get("form", ITRForm.ITR2.value))
    result = computation.result

    sections = build_guided_filing(ti, result, form)
    intro = await guidance_intro(form, result.regime, result.refund_or_payable)
    payload = {"intro": intro, "sections": sections,
               "regime": result.regime, "form": form.value}
    store.update(session_id, {"guidance": payload})
    return payload


@router.get("/stream/{session_id}")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    """SSE endpoint: streams all progress events for a session to the UI."""
    _require(session_id)
    queue = bus.subscribe(session_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": event.type.value,
                       "data": json.dumps(event.model_dump(), default=str)}
        finally:
            bus.unsubscribe(session_id, queue)

    return EventSourceResponse(event_generator())
