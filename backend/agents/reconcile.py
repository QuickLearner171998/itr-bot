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
                "You are a tax reconciliation assistant. Given a list of mismatches "
                "found across a taxpayer's documents, write a short, calm explanation "
                "(2-4 sentences) of what they mean and what the user should check. "
                "Do not invent numbers."))
        prompt = "Mismatches:\n" + "\n".join(f"- {i.message}" for i in issues)
        explanation = await run_agent(agent, prompt)
    else:
        explanation = "All sources reconcile within tolerance. No mismatches found."

    await bus.emit(session_id, EventType.RECON_DONE, explanation,
                   issue_count=len(issues))
    return issues, explanation
