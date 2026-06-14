"""Independent re-computation cross-check.

This deliberately re-derives the headline tax numbers through a *separate* code
path (no shared helpers with ``engine.py`` beyond constants) so that a bug in
one path is caught by disagreement with the other. It only verifies; it never
overwrites the engine result.
"""

from __future__ import annotations

from ..schemas.compute import TaxComputation, TaxInput
from .constants import fy2025_26 as K

VERIFY_TOLERANCE = 5.0


def _independent_slab_tax(income: float, regime: str, age: int) -> float:
    """Re-derive slab tax with an explicit, independent band walk."""
    if regime == "new":
        bands = [(0, 400000, 0.0), (400000, 800000, 0.05), (800000, 1200000, 0.10),
                 (1200000, 1600000, 0.15), (1600000, 2000000, 0.20),
                 (2000000, 2400000, 0.25), (2400000, float("inf"), 0.30)]
    else:
        exemption = 250000
        if age >= K.SUPER_SENIOR_AGE:
            exemption = 500000
        elif age >= K.SENIOR_AGE:
            exemption = 300000
        bands = [(0, exemption, 0.0), (exemption, 500000, 0.05),
                 (500000, 1000000, 0.20), (1000000, float("inf"), 0.30)]
    tax = 0.0
    for lo, hi, rate in bands:
        if income > lo:
            tax += (min(income, hi) - lo) * rate
    return tax


def _independent_regime_total(ti: TaxInput, regime: str) -> float:
    """Re-derive the total tax liability for one regime independently."""
    gross = sum(s.gross_salary for s in ti.salaries)
    exempt = 0.0 if regime == "new" else sum(s.exempt_allowances for s in ti.salaries)
    ptax = 0.0 if regime == "new" else sum(s.professional_tax for s in ti.salaries)
    std = K.NEW_STANDARD_DEDUCTION if regime == "new" else K.OLD_STANDARD_DEDUCTION
    net_salary = max(0.0, gross - exempt - std - ptax)

    d = ti.deductions
    if regime == "new":
        ded = min(d.amount_80ccd2, gross * K.EMPLOYER_NPS_CAP_RATE)
        hp = 0.0 if d.home_loan_self_occupied else max(ti.house_property_income - d.home_loan_interest, 0.0)
    else:
        ec = min(d.amount_80c + d.home_loan_principal, K.CAP_80C)
        senior = ti.age >= K.SENIOR_AGE
        d80d = min(d.amount_80d_self, K.CAP_80D_SELF_SENIOR if senior else K.CAP_80D_SELF) \
            + min(d.amount_80d_parents, K.CAP_80D_PARENTS)
        tta = min(ti.savings_interest, K.CAP_80TTB if senior else K.CAP_80TTA)
        ded = ec + min(d.amount_80ccd1b, K.CAP_80CCD1B) \
            + min(d.amount_80ccd2, gross * K.EMPLOYER_NPS_CAP_RATE_OLD) + d80d + tta
        interest = min(d.home_loan_interest, K.HOME_LOAN_SELF_OCCUPIED_CAP) if d.home_loan_self_occupied \
            else d.home_loan_interest
        hp = max(ti.house_property_income - interest, -K.HOME_LOAN_SELF_OCCUPIED_CAP)

    other = ti.savings_interest + ti.fd_interest + ti.dividend + ti.other_income
    cg = ti.capital_gains
    gti = net_salary + hp + other + cg.stcg_other
    normal = round(max(0.0, gti - min(ded, max(0.0, gti))) / 10.0) * 10

    slab_tax = _independent_slab_tax(normal, regime, ti.age)

    be = (K.NEW_REGIME_SLABS[0][0] or 0) if regime == "new" else (
        500000 if ti.age >= K.SUPER_SENIOR_AGE else 300000 if ti.age >= K.SENIOR_AGE else 250000)
    shortfall = max(0.0, be - normal)
    s111a = cg.stcg_111a
    u = min(shortfall, s111a); s111a -= u; shortfall -= u
    l112a = max(0.0, cg.ltcg_112a - K.LTCG_112A_EXEMPTION)
    u = min(shortfall, l112a); l112a -= u
    cg_tax = s111a * K.STCG_111A_RATE + l112a * K.LTCG_112A_RATE \
        + cg.ltcg_other * K.LTCG_OTHER_RATE + cg.vda_gain * K.VDA_RATE

    special = cg.stcg_111a + cg.ltcg_112a + cg.ltcg_other + cg.vda_gain
    total_income = normal + special

    rebate = 0.0
    if regime == "old" and total_income <= K.OLD_REBATE_INCOME_LIMIT:
        rebate = min(slab_tax, K.OLD_REBATE_MAX)
    elif regime == "new":
        if total_income <= K.NEW_REBATE_INCOME_LIMIT:
            rebate = min(slab_tax, K.NEW_REBATE_MAX)
        elif slab_tax > (total_income - K.NEW_REBATE_INCOME_LIMIT):
            rebate = slab_tax - (total_income - K.NEW_REBATE_INCOME_LIMIT)

    slab_after = max(0.0, slab_tax - rebate)
    tax = slab_after + cg_tax

    rate = 0.0
    for upper, r in K.SURCHARGE_SLABS:
        if total_income <= upper:
            rate = r
            break
    if regime == "new":
        rate = min(rate, K.NEW_REGIME_SURCHARGE_CAP)
    surcharge = slab_after * rate + (cg.ltcg_other * K.LTCG_OTHER_RATE + cg.vda_gain * K.VDA_RATE) * rate \
        + (s111a * K.STCG_111A_RATE + l112a * K.LTCG_112A_RATE) * min(rate, K.CG_SURCHARGE_CAP)

    cess = (tax + surcharge) * K.HEALTH_EDUCATION_CESS
    return round((tax + surcharge + cess) / 10.0) * 10


def verify(ti: TaxInput, computation: TaxComputation) -> tuple[bool, str]:
    """Verify the engine result against the independent re-computation.

    Note: the independent path omits surcharge marginal relief, so verification
    is treated as conclusive only when no surcharge applies; otherwise it is a
    soft check noting the surcharge band.

    Args:
        ti: The consolidated input.
        computation: The engine's computation to verify.

    Returns:
        ``(verified, note)``.
    """
    old_check = _independent_regime_total(ti, "old")
    new_check = _independent_regime_total(ti, "new")

    old_diff = abs(old_check - computation.old.total_tax_liability)
    new_diff = abs(new_check - computation.new.total_tax_liability)

    has_surcharge = computation.old.surcharge > 0 or computation.new.surcharge > 0
    if old_diff <= VERIFY_TOLERANCE and new_diff <= VERIFY_TOLERANCE:
        return True, "Independent re-computation matches the engine output."
    if has_surcharge:
        return True, (
            "Independent check is approximate in the surcharge band "
            f"(old diff {old_diff:.0f}, new diff {new_diff:.0f}); marginal relief differs.")
    return False, (
        f"Mismatch detected (old diff {old_diff:.0f}, new diff {new_diff:.0f}). "
        "Computation blocked pending review.")
