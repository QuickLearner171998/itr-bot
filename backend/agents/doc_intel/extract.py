"""Document intelligence: high-accuracy extraction with a self-critique loop.

Pipeline per document:
  1. Read the file (PDF text via pypdf, decrypting AIS with its password; or
     image bytes for scans) -> source context.
  2. Extractor agent (strongest model) returns structured JSON with per-field
     value + confidence + source hint.
  3. Self-critique agent re-reads the source vs the JSON, correcting values and
     flagging anything uncertain; looped up to ``max_extraction_retries``.
  4. Intra-document arithmetic validation produces issues.
Field values are streamed to the UI one-by-one so extraction looks live.
"""

from __future__ import annotations

import io
import shutil

from pypdf import PdfReader

from ...app.config import settings
from ...app.events import bus
from ...app.logging_setup import get_logger
from ...compute.validators import validate_document
from ...schemas.documents import (
    DOC_REGISTRY,
    DocType,
    DocumentExtraction,
    ExtractedField,
)
from ...schemas.events import EventType
from ..llm import build_agent, parse_json, run_agent

logger = get_logger(__name__)


def _read_pdf_text(data: bytes, password: str | None) -> str:
    """Extract text from a (possibly encrypted) PDF."""
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and password:
        reader.decrypt(password)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _render_pdf_images(data: bytes, password: str | None, max_pages: int = 3) -> list[bytes]:
    """Render leading PDF pages to PNG bytes (only if poppler is available)."""
    if shutil.which("pdftoppm") is None:
        return []
    from pdf2image import convert_from_bytes  # local import; optional dependency

    kwargs = {"first_page": 1, "last_page": max_pages, "fmt": "png"}
    if password:
        kwargs["userpw"] = password
    images = convert_from_bytes(data, **kwargs)
    out: list[bytes] = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _extractor_agent(doc_type: DocType):
    spec = DOC_REGISTRY[doc_type]
    field_lines = "\n".join(
        f"  - {f.name} ({f.type.value}): {f.description}" for f in spec.fields)
    instruction = (
        "You are an expert Indian tax document extractor. You will be given the "
        f"text and/or image of a '{spec.title}'. Extract ONLY these fields:\n"
        f"{field_lines}\n\n"
        "Rules:\n"
        "- Return STRICT JSON: {\"fields\": {<name>: {\"value\": <number-or-string-or-null>, "
        "\"confidence\": <0..1>, \"source_hint\": <short where-found note>}}}.\n"
        "- Money values are plain numbers (no commas/symbols).\n"
        "- If a field is genuinely absent, set value null and confidence 0.\n"
        "- Never guess; reflect uncertainty in a low confidence.\n"
        "- Do not output anything except the JSON object.")
    return build_agent(
        name=f"extract_{doc_type.value}",
        model_id=settings.extraction_model,
        instruction=instruction,
        reasoning_effort=settings.extraction_reasoning_effort)


def _critic_agent(doc_type: DocType):
    spec = DOC_REGISTRY[doc_type]
    instruction = (
        f"You are a meticulous reviewer of extracted '{spec.title}' data. You are "
        "given the source text/image and a candidate JSON extraction. Re-verify "
        "every value against the source. Correct wrong values, fill confidently "
        "missing ones, and lower confidence (and set value null) where unsupported. "
        "Return the SAME strict JSON schema: {\"fields\": {<name>: {\"value\":..., "
        "\"confidence\":..., \"source_hint\":...}}}. Output only JSON.")
    return build_agent(
        name=f"critic_{doc_type.value}",
        model_id=settings.validation_model,
        instruction=instruction,
        reasoning_effort=settings.extraction_reasoning_effort)


def _to_fields(doc_type: DocType, parsed: dict) -> list[ExtractedField]:
    """Convert parsed JSON into ordered ``ExtractedField`` list per the spec."""
    raw = parsed.get("fields", {}) if isinstance(parsed, dict) else {}
    fields: list[ExtractedField] = []
    for spec_field in DOC_REGISTRY[doc_type].fields:
        cell = raw.get(spec_field.name, {}) if isinstance(raw, dict) else {}
        if not isinstance(cell, dict):
            cell = {"value": cell, "confidence": 0.5}
        value = cell.get("value")
        confidence = float(cell.get("confidence") or 0.0)
        fields.append(ExtractedField(
            name=spec_field.name, label=spec_field.label, value=value,
            confidence=confidence, source_hint=cell.get("source_hint"),
            flagged=confidence < 0.6 and value not in (None, "")))
    return fields


async def extract_document(
    session_id: str,
    doc_type: DocType,
    filename: str,
    data: bytes,
    mime: str = "application/pdf",
    password: str | None = None,
    upload_id: str | None = None,
) -> DocumentExtraction:
    """Extract, self-critique, validate, and stream one document.

    Args:
        session_id: Owning session (for SSE + logging correlation).
        doc_type: Which document type this is.
        filename: Original filename.
        data: Raw file bytes.
        mime: MIME type ("application/pdf" or an image type).
        password: Optional password (e.g. AIS PDF).
        upload_id: Unique id for this uploaded file; echoed on every emitted
            event so the UI can track concurrent uploads of the same doc type
            independently.

    Returns:
        The validated ``DocumentExtraction``.
    """
    spec = DOC_REGISTRY[doc_type]
    ev = {"doc_type": doc_type.value, "upload_id": upload_id}
    await bus.emit(session_id, EventType.DOC_STARTED, f"Reading {spec.title}...",
                   filename=filename, **ev)

    images: list[bytes] = []
    text = ""
    if mime == "application/pdf":
        text = _read_pdf_text(data, password)
        if len(text.strip()) < 80:  # likely scanned -> need vision
            images = _render_pdf_images(data, password)
    else:
        images = [data]

    source_block = f"SOURCE TEXT:\n{text}" if text else "SOURCE: see attached image(s)."

    await bus.emit(session_id, EventType.AGENT_STEP,
                   f"Extracting fields from {spec.title} with {settings.extraction_model}...",
                   **ev)
    extractor = _extractor_agent(doc_type)
    response = await run_agent(extractor, source_block,
                               images=images, image_mime="image/png")
    parsed = parse_json(response)

    # Self-critique feedback loop.
    for attempt in range(settings.max_extraction_retries):
        fields = _to_fields(doc_type, parsed)
        if not any(f.flagged for f in fields):
            break
        await bus.emit(session_id, EventType.AGENT_STEP,
                       f"Self-critique pass {attempt + 1} on {spec.title}...",
                       **ev)
        critic = _critic_agent(doc_type)
        critique_prompt = f"{source_block}\n\nCANDIDATE JSON:\n{response}"
        response = await run_agent(critic, critique_prompt,
                                   images=images, image_mime="image/png")
        new_parsed = parse_json(response)
        if new_parsed:
            parsed = new_parsed

    fields = _to_fields(doc_type, parsed)

    # Stream each field to the UI so the panel fills in live.
    for field in fields:
        await bus.emit(
            session_id, EventType.DOC_FIELD, None,
            name=field.name, label=field.label,
            value=field.value, confidence=field.confidence,
            source_hint=field.source_hint, flagged=field.flagged, **ev)

    extraction = DocumentExtraction(
        doc_type=doc_type, filename=filename, fields=fields, status="extracted")
    extraction.issues = validate_document(extraction)
    confident = [f.confidence for f in fields if f.value not in (None, "")]
    extraction.overall_confidence = round(sum(confident) / len(confident), 3) if confident else 0.0
    extraction.status = "needs_review" if (
        any(i.severity == "error" for i in extraction.issues)
        or any(f.flagged for f in fields)) else "validated"

    await bus.emit(session_id, EventType.DOC_VALIDATED,
                   f"{spec.title}: {extraction.status} (confidence {extraction.overall_confidence:.0%})",
                   status=extraction.status,
                   confidence=extraction.overall_confidence,
                   issues=[i.model_dump() for i in extraction.issues], **ev)
    await bus.emit(session_id, EventType.DOC_COMPLETED, None,
                   extraction=extraction.model_dump(), **ev)
    logger.info("document extracted", extra={
        "doc_type": doc_type.value, "confidence": extraction.overall_confidence,
        "status": extraction.status})
    return extraction
