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

import asyncio
import io
import shutil
from functools import lru_cache

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
    """Extract text from a (possibly encrypted) PDF.

    pypdf's ``decrypt`` must be called before accessing pages. AIS PDFs may use
    an owner password; we try the str form first, then the bytes form as a
    fallback (some PDF writers require the latter).
    """
    from pypdf import PasswordType

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        if not password:
            raise ValueError("PDF is encrypted but no password was provided.")
        # The IT-dept AIS portal uses lowercase PAN + DOB; some other PDFs use
        # uppercase. Try all variants before giving up.
        candidates = [password, password.lower(), password.upper(),
                      password.encode(), password.lower().encode(), password.upper().encode()]
        result = PasswordType.NOT_DECRYPTED
        for candidate in candidates:
            result = reader.decrypt(candidate)  # type: ignore[arg-type]
            if result != PasswordType.NOT_DECRYPTED:
                break
        if result == PasswordType.NOT_DECRYPTED:
            raise ValueError("Incorrect PDF password — could not decrypt the file.")
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _render_pdf_images(data: bytes, password: str | None, max_pages: int = 3) -> list[bytes]:
    """Render leading PDF pages to PNG bytes (only if poppler is available)."""
    if shutil.which("pdftoppm") is None:
        return []
    from pdf2image import convert_from_bytes  # local import; optional dependency

    kwargs = {"first_page": 1, "last_page": max_pages, "fmt": "png"}
    if password:
        kwargs["userpw"] = password
        kwargs["ownerpw"] = password
    images = convert_from_bytes(data, **kwargs)
    out: list[bytes] = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


@lru_cache(maxsize=None)
def _extractor_agent(doc_type: DocType):
    """Single-pass agent that extracts and self-critiques in one LLM call.

    Cached per doc_type so the LlmAgent + LiteLlm objects are built once and
    reused across all uploads — avoids repeated model/runner construction overhead.
    """
    spec = DOC_REGISTRY[doc_type]
    field_lines = "\n".join(
        f"  - {f.name} ({f.type.value}): {f.description}" for f in spec.fields)
    context_section = (
        f"\nDOCUMENT STRUCTURE GUIDE:\n{spec.context_hint}\n"
        if spec.context_hint else "")
    instruction = (
        "You are an expert Indian tax document extractor and verifier. "
        f"You will be given the text and/or image of a '{spec.title}'."
        f"{context_section}\n"
        f"Extract ONLY these fields:\n{field_lines}\n\n"
        "EXTRACTION RULES:\n"
        "- Money values are plain numbers (no commas/symbols).\n"
        "- SUM across multiple rows when the field description says to sum.\n"
        "- For AIS: prefer 'modified value' over 'reported value' when both exist.\n"
        "- If a field is genuinely absent, set value null and confidence 0.\n"
        "- Never guess; reflect uncertainty in a low confidence score.\n"
        "\nSELF-CRITIQUE: After extracting, re-verify each value against the source — "
        "correct any wrong values, fill missed fields, and adjust confidence scores. "
        "Pay special attention to summing rows that should be aggregated and fields "
        "that may appear in a different section of the document.\n"
        "\nReturn STRICT JSON only: {\"fields\": {<name>: {\"value\": <number-or-string-or-null>, "
        "\"confidence\": <0..1>, \"source_hint\": <short where-found note>}}}. "
        "Output only the JSON object.")
    return build_agent(
        name=f"extract_{doc_type.value}",
        model_id=settings.extraction_model,
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
        # Run blocking pypdf/pdf2image calls in a thread so concurrent extractions
        # don't starve each other on the async event loop.
        text = await asyncio.to_thread(_read_pdf_text, data, password)
        if len(text.strip()) < 80:  # likely scanned -> need vision
            images = await asyncio.to_thread(_render_pdf_images, data, password)
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
    fields = _to_fields(doc_type, parsed)

    # Retry only if fields are still flagged after the combined extract+critique pass.
    for attempt in range(settings.max_extraction_retries):
        if not any(f.flagged for f in fields):
            break
        await bus.emit(session_id, EventType.AGENT_STEP,
                       f"Re-extraction pass {attempt + 1} on {spec.title}...",
                       **ev)
        retry_prompt = (
            f"{source_block}\n\nPREVIOUS ATTEMPT (has low-confidence fields — fix them):\n{response}")
        response = await run_agent(extractor, retry_prompt,
                                   images=images, image_mime="image/png")
        new_parsed = parse_json(response)
        if new_parsed:
            parsed = new_parsed
            fields = _to_fields(doc_type, parsed)

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
