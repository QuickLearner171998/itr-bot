"""HTTP + SSE API for the ITR bot.

All endpoints are session-scoped. Long-running agent work (extraction,
reconciliation, computation) streams progress over the SSE endpoint while the
POST returns the final structured result.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from sse_starlette.sse import EventSourceResponse

from ..agents.chat import answer as chat_answer
from ..agents.chat import answer_stream as chat_answer_stream
from ..agents.doc_intel.extract import extract_document
from ..agents.guidance import build_guided_filing, guidance_intro
from ..agents.intake import (
    QUESTIONNAIRE,
    build_checklist,
    build_profile,
    resolve_unsure_flags,
    select_form,
    summarize_plan,
)
from ..agents.orchestrator import run_computation
from ..agents.reconcile import reconcile_documents
from ..compute.consolidate import consolidate
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


@router.get("/questionnaire")
def get_questionnaire() -> dict:
    """Return the predefined branching questionnaire."""
    return {"sections": QUESTIONNAIRE}


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


@router.post("/session/{session_id}/intake")
async def submit_intake(session_id: str, answers: dict = Body(...)) -> dict:
    """Process questionnaire answers: pick form and build the checklist."""
    _require(session_id)
    profile = build_profile(answers)
    decision = select_form(profile)
    checklist = build_checklist(profile)
    summary = await summarize_plan(profile, decision, checklist)

    payload = {
        "profile": profile.model_dump(),
        "decision": decision.model_dump(),
        "checklist": [c.model_dump() for c in checklist],
        "summary": summary,
    }
    store.update(session_id, payload)
    return payload


@router.post("/session/{session_id}/documents")
async def upload_document(
    session_id: str,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    password: str | None = Form(default=None),
) -> dict:
    """Upload and extract one document (progress streams over SSE)."""
    _require(session_id)
    dtype = DocType(doc_type)
    data = await file.read()
    store.upload_dir(session_id).joinpath(file.filename or dtype.value).write_bytes(data)

    extraction = await extract_document(
        session_id, dtype, file.filename or dtype.value, data,
        mime=file.content_type or "application/pdf", password=password)

    state = store.get(session_id)
    docs = state.get("documents", {})
    # Multi-upload doc types are stored as lists; single-upload types as a dict.
    if dtype in _MULTI_UPLOAD_TYPES:
        slot = docs.get(dtype.value)
        if isinstance(slot, list):
            slot.append(extraction.model_dump())
        else:
            slot = [extraction.model_dump()]
        docs[dtype.value] = slot
    else:
        docs[dtype.value] = extraction.model_dump()
    store.update(session_id, {"documents": docs})
    return extraction.model_dump()


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


@router.post("/session/{session_id}/compute")
async def compute(session_id: str) -> dict:
    """Consolidate documents and run the streamed computation pipeline."""
    _require(session_id)
    state = store.get(session_id)
    docs = _flatten_docs(state)
    profile = UserProfile(**state.get("profile", {}))
    form = ITRForm(state.get("decision", {}).get("form", ITRForm.ITR2.value))

    ti: TaxInput = consolidate(docs, age=profile.age)

    extra: dict = {}
    if profile.unsure_fields:
        notes = resolve_unsure_flags(profile, ti)
        decision = select_form(profile)
        checklist = build_checklist(profile)
        for note in notes:
            await bus.emit(session_id, EventType.AGENT_STEP,
                           f"Resolved from documents: {note}")
        extra = {
            "profile": profile.model_dump(),
            "decision": decision.model_dump(),
            "checklist": [c.model_dump() for c in checklist],
        }
        form = decision.form

    result = await run_computation(session_id, ti, form, profile)

    store.update(session_id, {"tax_input": ti.model_dump(), **extra, **result})
    return {"tax_input": ti.model_dump(), **extra, **result}


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
