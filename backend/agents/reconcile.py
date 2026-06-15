"""Reconciliation agent: cross-source matching with plain-English explanations.

The matching itself is deterministic (see ``compute.validators.reconcile``); an
ADK agent only turns the detected mismatches into clear guidance. Results are
streamed so the reconciliation dashboard updates live.
"""

from __future__ import annotations

from ..app.config import settings
from ..app.events import bus
from ..compute.validators import reconcile as deterministic_reconcile
from ..schemas.documents import DocumentExtraction, ValidationIssue
from ..schemas.events import EventType
from .llm import build_agent, run_agent


async def reconcile_documents(
    session_id: str, docs: list[DocumentExtraction]
) -> tuple[list[ValidationIssue], str]:
    """Run cross-document reconciliation and explain the result.

    Args:
        session_id: Owning session (for SSE/logging).
        docs: All extracted documents.

    Returns:
        ``(issues, explanation)`` where issues is the mismatch list and
        explanation is a short natural-language note for the user.
    """
    await bus.emit(session_id, EventType.AGENT_STEP,
                   "Reconciling Form 16, Form 26AS, AIS and broker data...")
    issues = deterministic_reconcile(docs)

    for issue in issues:
        await bus.emit(session_id, EventType.RECON_FLAG, issue.message,
                       severity=issue.severity, fields=issue.fields)

    if issues:
        agent = build_agent(
            name="reconciler",
            model_id=settings.orchestration_model,
            instruction=(
                "You are a tax reconciliation assistant for an Indian ITR filing flow. "
                "You are given mismatches detected across a taxpayer's documents "
                "(Form 16, Form 26AS, AIS, broker P&L).\n\n"
                "Write ONE line per mismatch, in this exact format:\n"
                "  <Label>: <most likely cause in <=12 words> -> <one concrete action>.\n\n"
                "Rules:\n"
                "- Be concise and specific. No greetings, no reassurance, no hedging, "
                "no generic 'consult a professional' filler.\n"
                "- Lead with the single most probable cause, not a list of possibilities.\n"
                "- Reuse only the numbers given; never invent figures.\n"
                "- Prefer the higher value as the safe figure when a TDS/income source "
                "disagrees, and say which document is usually authoritative "
                "(26AS/AIS for tax credits and reported income; Form 16 for salary breakup).\n"
                "- Max one line per mismatch; no preamble or summary paragraph."))
        prompt = "Mismatches:\n" + "\n".join(f"- {i.message}" for i in issues)
        explanation = await run_agent(agent, prompt)
    else:
        explanation = "All sources reconcile within tolerance. No mismatches found."

    await bus.emit(session_id, EventType.RECON_DONE, explanation,
                   issue_count=len(issues))
    return issues, explanation
