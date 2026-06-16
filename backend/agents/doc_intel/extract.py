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

import pdfplumber
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


def _extract_tables_as_text(data: bytes, password: str | None) -> str:
    """Extract all tables from a PDF using pdfplumber and format them as labelled
    row-column text.

    pdfplumber preserves the geometric alignment of cells, meaning row labels
    (e.g. "(g) 80D Health Insurance") stay attached to their corresponding
    values. This pre-structured text replaces raw pypdf output for table-heavy
    documents (Form 16, AIS), eliminating the row-misalignment hallucination
    that occurs when an LLM reads flat text where column alignment is lost.

    Returns empty string when pdfplumber finds no tables or the PDF is image-based.

    Args:
        data: Raw PDF bytes.
        password: Optional decryption password.

    Returns:
        Human-readable table text with ``[Page N > Table M]`` section headers.
    """
    sections: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data), password=password) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables()
                for tbl_num, table in enumerate(tables, start=1):
                    if not table:
                        continue
                    lines: list[str] = [f"[Page {page_num} > Table {tbl_num}]"]
                    for row in table:
                        cells = [str(c or "").replace("\n", " ").strip() for c in row]
                        non_empty = [c for c in cells if c]
                        if non_empty:
                            lines.append("  " + " | ".join(cells))
                    if len(lines) > 1:
                        sections.append("\n".join(lines))
    except Exception:
        return ""
    return "\n\n".join(sections)


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
        "- When STRUCTURED TABLES are provided, use them as the primary source for "
        "  numeric fields — they preserve row-column alignment exactly as in the document. "
        "  Use RAW TEXT only for non-table fields (names, dates, regime, period).\n"
        "- For table fields: read the row label first, then take the value from the "
        "  correct column (prefer 'Deductible Amount' over 'Gross Amount' for deductions).\n"
        "- Money values are plain numbers (no commas/symbols).\n"
        "- SUM across multiple rows when the field description says to sum.\n"
        "- For AIS: prefer 'modified value' over 'reported value' when both exist.\n"
        "- If a field is genuinely absent, set value null and confidence 0.\n"
        "- Never guess; reflect uncertainty in a low confidence score.\n"
        "- source_hint must quote the exact table row label and column where the "
        "  value was found (e.g. 'Page 4 > Table 1, row (g), Deductible Amount column').\n"
        "\nSELF-CRITIQUE: After extracting, re-verify each numeric value against its "
        "table row label — confirm the label matches the field you are extracting "
        "(e.g. '80D' label for health insurance, not '80C' label for life insurance). "
        "Correct any row-mismatches and adjust confidence scores.\n"
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


_FIELD_RULES: dict[DocType, dict[str, tuple[float, float]]] = {
    # doc_type -> {field_name: (min_valid, max_valid)}
    DocType.FORM16: {
        "deduction_80c":    (0, 150000),
        "deduction_80ccd1b": (0, 50000),
        "deduction_80d":    (0, 50000),
        "standard_deduction": (0, 75001),
        "professional_tax": (0, 2500),
    },
}


def _is_suspicious(doc_type: DocType, field: ExtractedField) -> bool:
    """Return True if the field value violates a known statutory range.

    Args:
        doc_type: Document type being validated.
        field: The extracted field to check.

    Returns:
        True when the value is outside the allowed range.
    """
    if field.value in (None, ""):
        return False
    rules = _FIELD_RULES.get(doc_type, {})
    if field.name not in rules:
        return False
    lo, hi = rules[field.name]
    try:
        v = float(str(field.value).replace(",", "").replace("₹", "").strip())
    except (ValueError, TypeError):
        return False
    return not (lo <= v <= hi)


def _verify_source_hints(fields: list[ExtractedField], doc_text: str) -> list[ExtractedField]:
    """Penalise fields whose source_hint text cannot be found in the document.

    When the model extracts a value it invents (proximity-bias hallucination),
    the source_hint it fabricates typically also won't match any span in the
    actual document text. A simple substring check catches the most egregious
    cases and lowers confidence so the field is flagged for human review.

    Args:
        fields: Extracted fields with source hints.
        doc_text: The raw document text used for extraction.

    Returns:
        The same list with adjusted confidence / flagged status where grounding
        fails.
    """
    if not doc_text:
        return fields
    text_lower = doc_text.lower()
    updated: list[ExtractedField] = []
    for f in fields:
        hint = (f.source_hint or "").strip().lower()
        # Only verify numeric fields with a non-trivial hint that's more than
        # a generic label. Skip null/absent values.
        if f.value in (None, "") or not hint or len(hint) < 6:
            updated.append(f)
            continue
        # Extract key words from the hint and check at least one appears.
        # We don't require the full hint (OCR noise / formatting differences)
        # but at least 1 distinctive word should appear in the document.
        hint_words = [w for w in hint.split() if len(w) > 4
                      and w not in {"from", "table", "total", "column", "section",
                                    "value", "part", "amount", "deduction"}]
        if hint_words and not any(w in text_lower for w in hint_words):
            # Source hint not grounded — penalise confidence and flag.
            new_confidence = min(f.confidence, 0.55)
            updated.append(ExtractedField(
                name=f.name, label=f.label, value=f.value,
                confidence=new_confidence,
                source_hint=f.source_hint + " [unverified — hint not found in document]",
                flagged=True))
        else:
            updated.append(f)
    return updated


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
    table_text = ""
    if mime == "application/pdf":
        # Run blocking pypdf/pdf2image calls in a thread so concurrent extractions
        # don't starve each other on the async event loop.
        text, table_text = await asyncio.gather(
            asyncio.to_thread(_read_pdf_text, data, password),
            asyncio.to_thread(_extract_tables_as_text, data, password),
        )
        if len(text.strip()) < 80:  # likely scanned -> need vision
            images = await asyncio.to_thread(_render_pdf_images, data, password)
    else:
        images = [data]

    # Build source block: table-structured text is prepended (highest fidelity),
    # followed by full raw text as fallback context for non-table sections.
    # The LLM sees clean row-column labels for table fields, eliminating the
    # row-misalignment hallucination that occurs with raw flat text.
    if table_text:
        source_block = (
            "STRUCTURED TABLES (row-column aligned — use these for table fields):\n"
            f"{table_text}\n\n"
            "RAW TEXT (for non-table context: dates, names, period, regime):\n"
            f"{text}"
        )
    elif text:
        source_block = f"SOURCE TEXT:\n{text}"
    else:
        source_block = "SOURCE: see attached image(s)."

    await bus.emit(session_id, EventType.AGENT_STEP,
                   f"Extracting fields from {spec.title} with {settings.extraction_model}...",
                   **ev)
    extractor = _extractor_agent(doc_type)
    response = await run_agent(extractor, source_block,
                               images=images, image_mime="image/png")
    parsed = parse_json(response)
    fields = _to_fields(doc_type, parsed)

    # Source-hint grounding check: verify hints against all available text.
    full_text = table_text + "\n" + text if table_text else text
    fields = _verify_source_hints(fields, full_text)

    # Targeted retry: re-extract ONLY fields that violate a known statutory
    # range (a deterministic trigger pointing at a genuine error). Merely
    # low-confidence-but-plausible values are intentionally NOT retried — they
    # are surfaced to the user for review instead. Re-rolling plausible values
    # was a source of run-to-run tax variance without reliably improving
    # accuracy, so it is no longer done.
    for attempt in range(settings.max_extraction_retries):
        suspicious = [f for f in fields if _is_suspicious(doc_type, f)]
        targets = {f.name: f for f in suspicious}
        if not targets:
            break
        target_list = "\n".join(
            f"  - {f.name} (current value: {f.value}, confidence: {f.confidence:.2f}, "
            f"issue: value is outside the expected statutory range)"
            for f in targets.values())
        await bus.emit(session_id, EventType.AGENT_STEP,
                       f"Targeted re-extraction pass {attempt + 1}: fixing {len(targets)} field(s) in {spec.title}...",
                       **ev)
        retry_prompt = (
            f"{source_block}\n\n"
            f"TARGETED FIX REQUIRED — re-read ONLY these fields from the source document:\n"
            f"{target_list}\n\n"
            f"For each field above, find the exact value in the document and explain where you found it. "
            f"Return STRICT JSON: {{\"fields\": {{<name>: {{\"value\": <value>, "
            f"\"confidence\": <0..1>, \"source_hint\": <exact text or table cell location>}}}}}}"
        )
        retry_response = await run_agent(extractor, retry_prompt,
                                         images=images, image_mime="image/png")
        retry_parsed = parse_json(retry_response)
        if retry_parsed:
            # Merge only the targeted fields back into parsed — leave others untouched.
            merged_fields = parsed.get("fields", {})
            for name, cell in (retry_parsed.get("fields") or {}).items():
                if name in targets:
                    merged_fields[name] = cell
            parsed["fields"] = merged_fields
            fields = _to_fields(doc_type, parsed)
            fields = _verify_source_hints(fields, full_text)

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
