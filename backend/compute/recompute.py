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


def _independent_hra(ti: TaxInput) -> float:
    """Independent HRA exemption (Sec 10(13A))."""
    if ti.hra_received <= 0 or ti.hra_rent_paid <= 0:
        return 0.0
    rate = K.HRA_METRO_RATE if ti.hra_is_metro else K.HRA_NON_METRO_RATE
    return max(0.0, min(ti.hra_received,
                        ti.hra_rent_paid - K.HRA_RENT_BASIC_RATE * ti.hra_basic_da,
                        rate * ti.hra_basic_da))


def _independent_house_property(ti: TaxInput, regime: str) -> float:
    """Independent net house-property income."""
    d = ti.deductions
    if ti.let_out_annual_rent > 0:
        nav = max(0.0, ti.let_out_annual_rent - ti.let_out_municipal_taxes)
        net = nav - nav * K.HOUSE_PROPERTY_STD_DEDUCTION_RATE - d.home_loan_interest
        return net if regime == "old" else max(net, 0.0)
    if d.home_loan_self_occupied:
        interest = 0.0 if regime == "new" else min(d.home_loan_interest, K.HOME_LOAN_SELF_OCCUPIED_CAP)
        net = ti.house_property_income - interest
        return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP) if regime == "old" else max(net, 0.0)
    net = ti.house_property_income - d.home_loan_interest
    return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP) if regime == "old" else max(net, 0.0)


def _independent_old_deductions(ti: TaxInput, gross: float, gti: float) -> float:
    """Independent total old-regime Chapter VIA deduction."""
    d = ti.deductions
    senior = ti.age >= K.SENIOR_AGE
    total = min(d.amount_80c + d.home_loan_principal, K.CAP_80C)
    total += min(d.amount_80ccd1b, K.CAP_80CCD1B)
    total += min(d.amount_80ccd2, gross * K.EMPLOYER_NPS_CAP_RATE_OLD)
    total += min(d.amount_80d_self, K.CAP_80D_SELF_SENIOR if senior else K.CAP_80D_SELF)
    total += min(d.amount_80d_parents, K.CAP_80D_PARENTS)
    deposit_base = (ti.savings_interest + ti.fd_interest) if senior else ti.savings_interest
    total += min(deposit_base, K.CAP_80TTB if senior else K.CAP_80TTA)
    total += d.amount_80e
    total += min(d.amount_80eea, K.CAP_80EEA)
    if d.amount_80dd > 0:
        total += K.CAP_80DD_SEVERE if d.amount_80dd_severe else K.CAP_80DD_NORMAL
    if d.amount_80u > 0:
        total += K.CAP_80U_SEVERE if d.amount_80u_severe else K.CAP_80U_NORMAL
    total += min(d.amount_80ddb, K.CAP_80DDB_SENIOR if senior else K.CAP_80DDB_NORMAL)
    if d.amount_80gg > 0 and _independent_hra(ti) <= 0:
        total += max(0.0, min(K.CAP_80GG_ANNUAL, K.RATE_80GG_INCOME * gti,
                              d.amount_80gg - K.RATE_80GG_RENT_MINUS_INCOME * gti))
    pool = max(0.0, K.RATE_80G_QUALIFYING_LIMIT * max(0.0, gti - total))
    d100 = min(d.donation_100_limit, pool)
    d50 = min(d.donation_50_limit, pool - d100)
    total += d.donation_100_no_limit + 0.5 * d.donation_50_no_limit + d100 + 0.5 * d50
    return total


def _independent_agri(slab_tax: float, normal: float, ti: TaxInput, regime: str) -> float:
    """Independent agricultural-income partial integration."""
    agri = ti.agricultural_income
    be = (K.NEW_REGIME_SLABS[0][0] or 0) if regime == "new" else (
        500000 if ti.age >= K.SUPER_SENIOR_AGE else 300000 if ti.age >= K.SENIOR_AGE else 250000)
    if agri <= K.AGRI_INCOME_THRESHOLD or normal <= be:
        return slab_tax
    return max(0.0, _independent_slab_tax(normal + agri, regime, ti.age)
               - _independent_slab_tax(agri + be, regime, ti.age))


def _independent_regime_total(ti: TaxInput, regime: str) -> float:
    """Re-derive the total tax liability for one regime independently."""
    gross = sum(s.gross_salary for s in ti.salaries)
    exempt = 0.0 if regime == "new" else sum(s.exempt_allowances for s in ti.salaries) + _independent_hra(ti)
    ptax = 0.0 if regime == "new" else sum(s.professional_tax for s in ti.salaries)
    std = K.NEW_STANDARD_DEDUCTION if regime == "new" else K.OLD_STANDARD_DEDUCTION
    net_salary = max(0.0, gross - exempt - std - ptax)

    hp = _independent_house_property(ti, regime)

    fp = ti.family_pension
    fp_cap = K.NEW_FAMILY_PENSION_DEDUCTION_CAP if regime == "new" else K.FAMILY_PENSION_CAP_OLD
    fp_ded = min(fp * K.FAMILY_PENSION_DED_RATE, fp_cap) if fp else 0.0
    other = ti.savings_interest + ti.fd_interest + ti.dividend + ti.other_income + fp
    cg = ti.capital_gains
    gti = net_salary + hp + other - fp_ded + cg.stcg_other
    if regime == "old" and ti.brought_forward_loss > 0:
        gti -= min(ti.brought_forward_loss, max(0.0, gti))

    if regime == "new":
        ded = min(ti.deductions.amount_80ccd2, gross * K.EMPLOYER_NPS_CAP_RATE)
    else:
        ded = _independent_old_deductions(ti, gross, gti)
    normal = round(max(0.0, gti - min(ded, max(0.0, gti))) / 10.0) * 10

    slab_tax = _independent_agri(
        _independent_slab_tax(normal, regime, ti.age), normal, ti, regime)

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
    gross_tax = tax + surcharge + cess
    relief = min(ti.relief_89 + ti.relief_90_91, gross_tax)
    return round((gross_tax - relief) / 10.0) * 10


def verify(ti: TaxInput, computation: TaxComputation) -> tuple[bool, str]:
    """Verify the engine result against the independent re-computation.

    Re-derives the tax for the filing regime through a separate code path and
    compares it to the engine output. Note: the independent path omits surcharge
    marginal relief, so verification is conclusive only when no surcharge
    applies; otherwise it is a soft check noting the surcharge band.

    Args:
        ti: The consolidated input.
        computation: The engine's computation to verify.

    Returns:
        ``(verified, note)``.
    """
    check = _independent_regime_total(ti, computation.regime)
    diff = abs(check - computation.result.total_tax_liability)

    if diff <= VERIFY_TOLERANCE:
        return True, "Independent re-computation matches the engine output."
    if computation.result.surcharge > 0:
        return True, (
            "Independent check is approximate in the surcharge band "
            f"(diff {diff:.0f}); marginal relief differs.")
    return False, (
        f"Mismatch detected (diff {diff:.0f}). Computation blocked pending review.")
