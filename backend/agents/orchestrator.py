"""Orchestration of the compute stage: deterministic engine + verification.

This coordinates the must-be-correct chain: run the deterministic engine for
both regimes, stream the recommended regime's waterfall steps, run the
independent re-computation cross-check, and run final-return rule validation.
The independent check can only flag/block; it never overwrites a value.
"""

from __future__ import annotations

import asyncio

from ..app.events import bus
from ..app.logging_setup import get_logger
from ..compute.engine import compute_taxes
from ..compute.recompute import verify
from ..compute.validators import validate_final_return
from ..schemas.compute import TaxComputation, TaxInput
from ..schemas.events import EventType
from ..schemas.profile import ITRForm, UserProfile

logger = get_logger(__name__)


async def run_computation(
    session_id: str, ti: TaxInput, form: ITRForm, profile: UserProfile
) -> dict:
    """Run and stream the full computation pipeline.

    Args:
        session_id: Owning session (for SSE/logging).
        ti: Consolidated tax input.
        form: Selected ITR form.
        profile: User profile (for final-rule validation).

    Returns:
        Dict with the ``TaxComputation``, verification result, and final issues.
    """
    regime = ti.filing_regime
    await bus.emit(session_id, EventType.AGENT_STEP,
                   f"Running deterministic tax engine ({regime.upper()} regime, from Form 16)...")
    computation: TaxComputation = compute_taxes(ti, regime)

    chosen = computation.result
    for step in chosen.steps:
        await bus.emit(session_id, EventType.COMPUTE_STEP, step.label,
                       key=step.key, label=step.label, amount=step.amount,
                       kind=step.kind, regime=chosen.regime)
        await asyncio.sleep(0.12)  # paces the live waterfall animation

    await bus.emit(session_id, EventType.AGENT_STEP,
                   "Independent re-computation cross-check...")
    verified, note = verify(ti, computation)
    computation.verified = verified
    computation.verification_note = note
    await bus.emit(session_id, EventType.VERIFICATION, note, verified=verified)

    final_issues = validate_final_return(ti, form, profile)
    for issue in final_issues:
        await bus.emit(session_id, EventType.RECON_FLAG, issue.message,
                       severity=issue.severity, fields=issue.fields, stage="final")

    blocking = [i for i in final_issues if i.severity == "error"] or not verified
    payable = chosen.refund_or_payable
    verb = "payable" if payable >= 0 else "refund"
    await bus.emit(session_id, EventType.COMPUTE_DONE,
                   f"{regime.upper()} regime: tax {chosen.total_tax_liability:,.0f} "
                   f"({verb} {abs(payable):,.0f})",
                   computation=computation.model_dump(),
                   blocking=bool(blocking))
    logger.info("computation done", extra={
        "regime": regime, "verified": verified, "blocking": bool(blocking)})
    return {
        "computation": computation.model_dump(),
        "verified": verified,
        "verification_note": note,
        "final_issues": [i.model_dump() for i in final_issues],
    }
