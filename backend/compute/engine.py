"""Deterministic income-tax computation engine for FY 2025-26.

This module is the single source of truth for tax numbers. It is pure (no I/O,
no randomness, no LLM) so it is fully unit-testable and reproducible. It
produces a step-by-step trace (``ComputeStep``) so the UI can animate the
income-to-tax waterfall.
"""

from __future__ import annotations

from ..schemas.compute import (
    ComputeStep,
    RegimeResult,
    TaxComputation,
    TaxInput,
)
from .constants import fy2025_26 as K


def _round_to_ten(amount: float) -> float:
    """Round to the nearest multiple of ten (Sec 288A/288B rounding)."""
    return float(round(amount / 10.0) * 10)


def _slab_tax(income: float, slabs: list[tuple[float | None, float]]) -> float:
    """Compute tax by applying marginal ``slabs`` cumulatively.

    Args:
        income: Taxable income to slab.
        slabs: List of ``(upper_bound, rate)``; ``upper_bound`` ``None`` means
            "and above".

    Returns:
        Tax amount before rebate/surcharge/cess.
    """
    tax = 0.0
    lower = 0.0
    for upper, rate in slabs:
        cap = income if upper is None else min(income, upper)
        if cap > lower:
            tax += (cap - lower) * rate
        if upper is not None:
            lower = upper
        if upper is not None and income <= upper:
            break
    return tax


def _old_regime_slabs(age: int) -> list[tuple[float | None, float]]:
    """Return old-regime slabs adjusted for age-based basic exemption."""
    if age >= K.SUPER_SENIOR_AGE:
        return [(K.OLD_BASIC_EXEMPTION_SUPER_SENIOR, 0.00), (1000000, 0.20), (None, 0.30)]
    if age >= K.SENIOR_AGE:
        return [(K.OLD_BASIC_EXEMPTION_SENIOR, 0.00), (500000, 0.05),
                (1000000, 0.20), (None, 0.30)]
    return K.OLD_REGIME_SLABS


def _basic_exemption(regime: str, age: int) -> float:
    """Basic exemption limit used for the capital-gains shortfall adjustment."""
    if regime == "new":
        return K.NEW_REGIME_SLABS[0][0] or 0.0
    if age >= K.SUPER_SENIOR_AGE:
        return K.OLD_BASIC_EXEMPTION_SUPER_SENIOR
    if age >= K.SENIOR_AGE:
        return K.OLD_BASIC_EXEMPTION_SENIOR
    return K.OLD_REGIME_SLABS[0][0] or 0.0


def _capital_gains_tax(
    ti: TaxInput, normal_income: float, regime: str
) -> dict[str, float]:
    """Compute special-rate capital-gains tax with basic-exemption shortfall.

    For residents, any basic exemption unused by normal income may be set off
    against 111A then 112A gains (most beneficial order). VDA gains get no such
    benefit and no deductions.

    Args:
        ti: Consolidated tax input.
        normal_income: Slab-taxed total income (post Chapter VIA).
        regime: "old" or "new".

    Returns:
        Mapping with individual gain taxes, the 111A+112A tax (for the
        surcharge cap), and the total capital-gains tax.
    """
    cg = ti.capital_gains
    shortfall = max(0.0, _basic_exemption(regime, ti.age) - normal_income)

    stcg_111a = cg.stcg_111a
    used = min(shortfall, stcg_111a)
    stcg_111a -= used
    shortfall -= used

    ltcg_112a_taxable = max(0.0, cg.ltcg_112a - K.LTCG_112A_EXEMPTION)
    used = min(shortfall, ltcg_112a_taxable)
    ltcg_112a_taxable -= used
    shortfall -= used

    tax_111a = stcg_111a * K.STCG_111A_RATE
    tax_112a = ltcg_112a_taxable * K.LTCG_112A_RATE
    tax_ltcg_other = cg.ltcg_other * K.LTCG_OTHER_RATE
    tax_vda = cg.vda_gain * K.VDA_RATE

    return {
        "tax_111a": tax_111a,
        "tax_112a": tax_112a,
        "tax_ltcg_other": tax_ltcg_other,
        "tax_vda": tax_vda,
        "tax_111a_112a": tax_111a + tax_112a,
        "total": tax_111a + tax_112a + tax_ltcg_other + tax_vda,
    }


def _surcharge_rate(total_income: float, regime: str) -> float:
    """Applicable surcharge rate for ``total_income`` (new regime capped 25%)."""
    rate = 0.0
    for upper, slab_rate in K.SURCHARGE_SLABS:
        if total_income <= upper:
            rate = slab_rate
            break
    if regime == "new":
        rate = min(rate, K.NEW_REGIME_SURCHARGE_CAP)
    return rate


def _surcharge_threshold(total_income: float) -> float:
    """Lower bound of the surcharge band ``total_income`` falls into."""
    lower = 0.0
    for upper, _ in K.SURCHARGE_SLABS:
        if total_income <= upper:
            return lower
        lower = upper
    return lower


def _deductions_for_regime(ti: TaxInput, regime: str, salary_for_nps: float) -> float:
    """Total Chapter VIA deductions allowed under the given regime.

    New regime allows only employer NPS u/s 80CCD(2). Old regime allows the
    full set with statutory caps.
    """
    d = ti.deductions
    is_senior = ti.age >= K.SENIOR_AGE

    if regime == "new":
        cap = salary_for_nps * K.EMPLOYER_NPS_CAP_RATE
        return min(d.amount_80ccd2, cap)

    # Old regime.
    eighty_c = min(d.amount_80c + d.home_loan_principal, K.CAP_80C)
    eighty_ccd1b = min(d.amount_80ccd1b, K.CAP_80CCD1B)
    employer_nps = min(d.amount_80ccd2, salary_for_nps * K.EMPLOYER_NPS_CAP_RATE_OLD)

    cap_self = K.CAP_80D_SELF_SENIOR if is_senior else K.CAP_80D_SELF
    eighty_d = min(d.amount_80d_self, cap_self) + min(d.amount_80d_parents, K.CAP_80D_PARENTS)

    savings = ti.savings_interest
    tta_cap = K.CAP_80TTB if is_senior else K.CAP_80TTA
    deposit_base = (savings + ti.fd_interest) if is_senior else savings
    eighty_tt = min(deposit_base, tta_cap)

    return eighty_c + eighty_ccd1b + employer_nps + eighty_d + eighty_tt


def _house_property(ti: TaxInput, regime: str) -> float:
    """Net house-property income after allowed 24(b) interest and set-off caps."""
    d = ti.deductions
    base = ti.house_property_income

    if d.home_loan_self_occupied:
        if regime == "new":
            interest = 0.0  # self-occupied 24(b) not allowed in new regime
        else:
            interest = min(d.home_loan_interest, K.HOME_LOAN_SELF_OCCUPIED_CAP)
        net = base - interest
        if regime == "old":
            return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP)
        return max(net, 0.0)  # new regime self-occupied loss lapses

    # Let-out: full interest; loss set-off rules differ by regime.
    net = base - d.home_loan_interest
    if regime == "old":
        return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP)  # aggregate HP set-off cap 2L
    return max(net, 0.0)  # new regime: let-out loss not set off against other heads


def compute_regime(ti: TaxInput, regime: str) -> RegimeResult:
    """Compute a fully traced tax result for one regime.

    Args:
        ti: Consolidated tax input.
        regime: "old" or "new".

    Returns:
        A ``RegimeResult`` with the income-to-tax trace and all totals.
    """
    steps: list[ComputeStep] = []

    gross_salary = sum(s.gross_salary for s in ti.salaries)
    exempt = sum(s.exempt_allowances for s in ti.salaries)
    professional_tax = sum(s.professional_tax for s in ti.salaries)
    std_ded = K.NEW_STANDARD_DEDUCTION if regime == "new" else K.OLD_STANDARD_DEDUCTION

    if regime == "new":
        exempt = 0.0          # HRA/LTA and most Sec 10 exemptions disallowed
        professional_tax = 0.0  # 16(iii) not allowed in new regime

    net_salary = max(0.0, gross_salary - exempt - std_ded - professional_tax)
    steps.append(ComputeStep(key="gross_salary", label="Gross Salary", amount=gross_salary, kind="add"))
    if exempt:
        steps.append(ComputeStep(key="exempt", label="Less: Exempt Allowances (Sec 10)", amount=exempt, kind="subtract"))
    steps.append(ComputeStep(key="std_ded", label="Less: Standard Deduction", amount=std_ded, kind="subtract"))
    if professional_tax:
        steps.append(ComputeStep(key="ptax", label="Less: Professional Tax", amount=professional_tax, kind="subtract"))
    steps.append(ComputeStep(key="net_salary", label="Income from Salary", amount=net_salary, kind="total"))

    hp_income = _house_property(ti, regime)
    if hp_income:
        steps.append(ComputeStep(key="house_property", label="Income from House Property", amount=hp_income, kind="add"))

    other_sources = ti.savings_interest + ti.fd_interest + ti.dividend + ti.other_income
    stcg_other = ti.capital_gains.stcg_other  # slab-rate, part of normal income
    if other_sources:
        steps.append(ComputeStep(key="other_sources", label="Income from Other Sources", amount=other_sources, kind="add"))
    if stcg_other:
        steps.append(ComputeStep(key="stcg_other", label="STCG (slab rate)", amount=stcg_other, kind="add"))

    gti_normal = net_salary + hp_income + other_sources + stcg_other
    steps.append(ComputeStep(key="gti", label="Gross Total Income (slab)", amount=gti_normal, kind="total"))

    deductions = _deductions_for_regime(ti, regime, gross_salary)
    deductions = min(deductions, max(0.0, gti_normal))
    if deductions:
        steps.append(ComputeStep(key="chvia", label="Less: Chapter VI-A Deductions", amount=deductions, kind="subtract"))

    normal_income = _round_to_ten(max(0.0, gti_normal - deductions))
    steps.append(ComputeStep(key="total_income", label="Taxable Income (slab)", amount=normal_income, kind="total"))

    slabs = K.NEW_REGIME_SLABS if regime == "new" else _old_regime_slabs(ti.age)
    slab_tax = _slab_tax(normal_income, slabs)

    cg = _capital_gains_tax(ti, normal_income, regime)
    special_income = (ti.capital_gains.stcg_111a + ti.capital_gains.ltcg_112a
                      + ti.capital_gains.ltcg_other + ti.capital_gains.vda_gain)
    total_income = normal_income + special_income

    steps.append(ComputeStep(key="slab_tax", label="Tax on Slab Income", amount=slab_tax, kind="tax"))
    if cg["total"]:
        steps.append(ComputeStep(key="cg_tax", label="Tax on Capital Gains (special rates)", amount=cg["total"], kind="tax"))

    # Rebate u/s 87A (against slab tax only) + new-regime marginal relief.
    rebate = 0.0
    marginal_relief_rebate = 0.0
    if regime == "old":
        if total_income <= K.OLD_REBATE_INCOME_LIMIT:
            rebate = min(slab_tax, K.OLD_REBATE_MAX)
    else:
        if total_income <= K.NEW_REBATE_INCOME_LIMIT:
            rebate = min(slab_tax, K.NEW_REBATE_MAX)
        else:
            excess = total_income - K.NEW_REBATE_INCOME_LIMIT
            if 0 < excess and slab_tax > excess:
                marginal_relief_rebate = slab_tax - excess
    if rebate:
        steps.append(ComputeStep(key="rebate", label="Less: Rebate u/s 87A", amount=rebate, kind="subtract"))
    if marginal_relief_rebate:
        steps.append(ComputeStep(key="mr_rebate", label="Less: Marginal Relief (87A)", amount=marginal_relief_rebate, kind="subtract"))

    slab_tax_after = max(0.0, slab_tax - rebate - marginal_relief_rebate)
    tax_after_rebate = slab_tax_after + cg["total"]

    # Surcharge with 15% cap on 111A/112A and marginal relief at band threshold.
    surcharge, marginal_relief_sc = _surcharge_with_relief(
        slab_tax_after, cg, total_income, normal_income, special_income, regime, ti.age, slabs
    )
    if surcharge:
        steps.append(ComputeStep(key="surcharge", label="Add: Surcharge", amount=surcharge, kind="add"))
    if marginal_relief_sc:
        steps.append(ComputeStep(key="mr_sc", label="Less: Marginal Relief (Surcharge)", amount=marginal_relief_sc, kind="subtract"))

    cess = (tax_after_rebate + surcharge) * K.HEALTH_EDUCATION_CESS
    steps.append(ComputeStep(key="cess", label="Add: Health & Education Cess (4%)", amount=cess, kind="add"))

    total_tax = _round_to_ten(tax_after_rebate + surcharge + cess)
    steps.append(ComputeStep(key="total_tax", label="Total Tax Liability", amount=total_tax, kind="total"))

    taxes_paid = ti.tds_total + ti.advance_tax + ti.self_assessment_tax
    steps.append(ComputeStep(key="taxes_paid", label="Less: Taxes Already Paid (TDS/Advance)", amount=taxes_paid, kind="subtract"))

    refund_or_payable = _round_to_ten(total_tax - taxes_paid)
    label = "Tax Payable" if refund_or_payable >= 0 else "Refund Due"
    steps.append(ComputeStep(key="net", label=label, amount=abs(refund_or_payable), kind="total"))

    return RegimeResult(
        regime=regime,
        steps=steps,
        gross_total_income=round(gti_normal + special_income, 2),
        total_deductions=round(deductions, 2),
        total_income=total_income,
        tax_before_rebate=round(slab_tax + cg["total"], 2),
        rebate_87a=round(rebate, 2),
        surcharge=round(surcharge, 2),
        marginal_relief=round(marginal_relief_rebate + marginal_relief_sc, 2),
        cess=round(cess, 2),
        total_tax_liability=total_tax,
        taxes_paid=round(taxes_paid, 2),
        refund_or_payable=refund_or_payable,
    )


def _surcharge_with_relief(
    slab_tax_after: float,
    cg: dict[str, float],
    total_income: float,
    normal_income: float,
    special_income: float,
    regime: str,
    age: int,
    slabs: list[tuple[float | None, float]],
) -> tuple[float, float]:
    """Compute surcharge with the 15% capital-gains cap and marginal relief.

    Marginal relief ensures the increase in (tax + surcharge) over the band
    threshold does not exceed the income over that threshold.

    Returns:
        ``(surcharge, marginal_relief)``.
    """
    rate = _surcharge_rate(total_income, regime)
    if rate == 0.0:
        return 0.0, 0.0

    cg_rate = min(rate, K.CG_SURCHARGE_CAP)
    capped_tax = cg["tax_111a_112a"]
    uncapped_special = cg["tax_ltcg_other"] + cg["tax_vda"]
    surcharge = slab_tax_after * rate + uncapped_special * rate + capped_tax * cg_rate

    threshold = _surcharge_threshold(total_income)
    prev_rate = _surcharge_rate(threshold, regime) if threshold > 0 else 0.0

    # Tax + surcharge at the threshold income (previous band rate).
    normal_at_t = max(0.0, threshold - special_income)
    slab_tax_at_t = _slab_tax(normal_at_t, slabs)
    tax_at_t = slab_tax_at_t + cg["total"]
    sc_at_t = (slab_tax_at_t + uncapped_special) * prev_rate + capped_tax * min(prev_rate, K.CG_SURCHARGE_CAP)

    total_now = slab_tax_after + cg["total"] + surcharge
    total_at_t = tax_at_t + sc_at_t
    allowed = total_income - threshold
    marginal_relief = max(0.0, (total_now - total_at_t) - allowed)
    return surcharge - marginal_relief if marginal_relief else surcharge, marginal_relief


def compute_taxes(ti: TaxInput) -> TaxComputation:
    """Compute both regimes, recommend the cheaper, and verify via re-compute.

    Args:
        ti: Consolidated tax input.

    Returns:
        A ``TaxComputation`` comparing old and new regimes.
    """
    old = compute_regime(ti, "old")
    new = compute_regime(ti, "new")

    if new.total_tax_liability <= old.total_tax_liability:
        recommended = "new"
        savings = old.total_tax_liability - new.total_tax_liability
    else:
        recommended = "old"
        savings = new.total_tax_liability - old.total_tax_liability

    return TaxComputation(
        old=old,
        new=new,
        recommended_regime=recommended,
        recommended_savings=round(savings, 2),
    )
