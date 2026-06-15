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


def _hra_exemption(ti: TaxInput) -> float:
    """HRA exemption u/s 10(13A): least of received, rent-10% basic, rate*basic.

    Returns 0 unless explicit HRA inputs are provided (i.e. HRA not already
    exempted inside Form 16). Old regime only; the caller gates by regime.
    """
    if ti.hra_received <= 0 or ti.hra_rent_paid <= 0:
        return 0.0
    basic = ti.hra_basic_da
    rate = K.HRA_METRO_RATE if ti.hra_is_metro else K.HRA_NON_METRO_RATE
    return max(0.0, min(
        ti.hra_received,
        ti.hra_rent_paid - K.HRA_RENT_BASIC_RATE * basic,
        rate * basic))


def _donation_80g(d, gti_after_other: float) -> float:
    """80G donation deduction with the 10%-of-adjusted-GTI qualifying limit.

    No-limit donations are deducted at their category rate; limited donations are
    first capped to the qualifying pool (100% category consumed first), then the
    category rate applied.
    """
    no_limit = d.donation_100_no_limit + 0.5 * d.donation_50_no_limit
    pool = max(0.0, K.RATE_80G_QUALIFYING_LIMIT * gti_after_other)
    d100 = min(d.donation_100_limit, pool)
    pool -= d100
    d50 = min(d.donation_50_limit, pool)
    return no_limit + d100 + 0.5 * d50


def _deductions_for_regime(
    ti: TaxInput, regime: str, salary_for_nps: float, gti_normal: float
) -> float:
    """Total Chapter VIA deductions allowed under the given regime.

    New regime allows only employer NPS u/s 80CCD(2). Old regime allows the full
    set with statutory caps, including 80E/80EEA/80DD/80DDB/80U/80GG and 80G.

    Args:
        ti: Consolidated tax input.
        regime: "old" or "new".
        salary_for_nps: Gross salary base for the 80CCD(2) percentage cap.
        gti_normal: Slab-income GTI, used for 80G/80GG income-based limits.

    Returns:
        Total allowable Chapter VIA deduction.
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

    eighty_e = d.amount_80e
    eighty_eea = min(d.amount_80eea, K.CAP_80EEA)
    eighty_dd = (K.CAP_80DD_SEVERE if d.amount_80dd_severe else K.CAP_80DD_NORMAL) \
        if d.amount_80dd > 0 else 0.0
    eighty_u = (K.CAP_80U_SEVERE if d.amount_80u_severe else K.CAP_80U_NORMAL) \
        if d.amount_80u > 0 else 0.0
    eighty_ddb = min(d.amount_80ddb, K.CAP_80DDB_SENIOR if is_senior else K.CAP_80DDB_NORMAL)

    eighty_gg = 0.0
    if d.amount_80gg > 0 and _hra_exemption(ti) <= 0:
        eighty_gg = max(0.0, min(
            K.CAP_80GG_ANNUAL,
            K.RATE_80GG_INCOME * gti_normal,
            d.amount_80gg - K.RATE_80GG_RENT_MINUS_INCOME * gti_normal))

    base = (eighty_c + eighty_ccd1b + employer_nps + eighty_d + eighty_tt
            + eighty_e + eighty_eea + eighty_dd + eighty_u + eighty_ddb + eighty_gg)
    eighty_g = _donation_80g(d, max(0.0, gti_normal - base))
    return base + eighty_g


def _house_property(ti: TaxInput, regime: str) -> float:
    """Net house-property income after allowed 24(b) interest and set-off caps.

    When ``let_out_annual_rent`` is provided, the let-out net is computed from the
    annual value less municipal taxes, the 30% standard deduction, and full 24(b)
    interest. Otherwise the legacy self-occupied / direct-income path is used.
    """
    d = ti.deductions

    if ti.let_out_annual_rent > 0:
        nav = max(0.0, ti.let_out_annual_rent - ti.let_out_municipal_taxes)
        std = nav * K.HOUSE_PROPERTY_STD_DEDUCTION_RATE
        net = nav - std - d.home_loan_interest
        if regime == "old":
            return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP)
        return max(net, 0.0)  # new regime: let-out loss not set off against other heads

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

    # Let-out via direct net income input; loss set-off rules differ by regime.
    net = base - d.home_loan_interest
    if regime == "old":
        return max(net, -K.HOME_LOAN_SELF_OCCUPIED_CAP)  # aggregate HP set-off cap 2L
    return max(net, 0.0)


def _agri_adjusted_slab_tax(
    slab_tax: float, normal_income: float, ti: TaxInput,
    slabs: list[tuple[float | None, float]], regime: str
) -> float:
    """Apply agricultural-income partial integration to slab tax.

    Tax = tax(normal + agri) - tax(agri + basic exemption), when agri income
    exceeds the threshold and normal income exceeds the basic exemption.
    """
    agri = ti.agricultural_income
    be = _basic_exemption(regime, ti.age)
    if agri <= K.AGRI_INCOME_THRESHOLD or normal_income <= be:
        return slab_tax
    tax_with = _slab_tax(normal_income + agri, slabs)
    tax_agri = _slab_tax(agri + be, slabs)
    return max(0.0, tax_with - tax_agri)


def _emit_deduction_steps(
    steps: list[ComputeStep], ti: TaxInput, regime: str,
    salary_for_nps: float, gti_normal: float
) -> None:
    """Append individual Chapter VI-A deduction lines to ``steps``.

    Mirrors the logic in ``_deductions_for_regime`` but emits one
    ``ComputeStep`` per non-zero component so the UI shows the full breakup.
    """
    d = ti.deductions
    is_senior = ti.age >= K.SENIOR_AGE

    def _s(key: str, label: str, amount: float) -> None:
        if amount > 0:
            steps.append(ComputeStep(key=key, label=f"  {label}", amount=amount, kind="subtract"))

    if regime == "new":
        cap = salary_for_nps * K.EMPLOYER_NPS_CAP_RATE
        _s("80ccd2", "80CCD(2) Employer NPS", min(d.amount_80ccd2, cap))
        return

    # Old regime — full Chapter VI-A.
    eighty_c_base = min(d.amount_80c + d.home_loan_principal, K.CAP_80C)
    _s("80c", f"80C (incl. ELSS/PPF/LIC/home loan principal; cap ₹{int(K.CAP_80C):,})", eighty_c_base)

    _s("80ccd1b", f"80CCD(1B) NPS Self (cap ₹{int(K.CAP_80CCD1B):,})",
       min(d.amount_80ccd1b, K.CAP_80CCD1B))

    employer_nps = min(d.amount_80ccd2, salary_for_nps * K.EMPLOYER_NPS_CAP_RATE_OLD)
    _s("80ccd2", "80CCD(2) Employer NPS", employer_nps)

    cap_self = K.CAP_80D_SELF_SENIOR if is_senior else K.CAP_80D_SELF
    eighty_d_self = min(d.amount_80d_self, cap_self)
    _s("80d_self", f"80D Health Insurance — Self/Family (cap ₹{int(cap_self):,})", eighty_d_self)
    eighty_d_par = min(d.amount_80d_parents, K.CAP_80D_PARENTS)
    _s("80d_parents", f"80D Health Insurance — Parents (cap ₹{int(K.CAP_80D_PARENTS):,})", eighty_d_par)

    savings = ti.savings_interest
    tta_cap = K.CAP_80TTB if is_senior else K.CAP_80TTA
    deposit_base = (savings + ti.fd_interest) if is_senior else savings
    eighty_tt = min(deposit_base, tta_cap)
    tta_label = f"80TTB Interest (Senior; cap ₹{int(tta_cap):,})" if is_senior else f"80TTA Savings Interest (cap ₹{int(tta_cap):,})"
    _s("80tta", tta_label, eighty_tt)

    _s("80e", "80E Education Loan Interest (no cap)", d.amount_80e)
    _s("80eea", f"80EEA Additional Home Loan Interest (cap ₹{int(K.CAP_80EEA):,})",
       min(d.amount_80eea, K.CAP_80EEA))

    if d.amount_80dd > 0:
        dd_amt = K.CAP_80DD_SEVERE if d.amount_80dd_severe else K.CAP_80DD_NORMAL
        _s("80dd", f"80DD Disabled Dependent ({'severe' if d.amount_80dd_severe else 'normal'})", dd_amt)

    if d.amount_80u > 0:
        u_amt = K.CAP_80U_SEVERE if d.amount_80u_severe else K.CAP_80U_NORMAL
        _s("80u", f"80U Self Disability ({'severe' if d.amount_80u_severe else 'normal'})", u_amt)

    _s("80ddb", "80DDB Specified Disease Treatment",
       min(d.amount_80ddb, K.CAP_80DDB_SENIOR if is_senior else K.CAP_80DDB_NORMAL))

    if d.amount_80gg > 0 and _hra_exemption(ti) <= 0:
        eighty_gg = max(0.0, min(
            K.CAP_80GG_ANNUAL,
            K.RATE_80GG_INCOME * gti_normal,
            d.amount_80gg - K.RATE_80GG_RENT_MINUS_INCOME * gti_normal))
        _s("80gg", "80GG Rent Paid (no HRA received)", eighty_gg)

    base = (eighty_c_base + min(d.amount_80ccd1b, K.CAP_80CCD1B) + employer_nps
            + eighty_d_self + eighty_d_par + eighty_tt + d.amount_80e
            + min(d.amount_80eea, K.CAP_80EEA))
    eighty_g = _donation_80g(d, max(0.0, gti_normal - base))
    _s("80g", "80G Donations", eighty_g)


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

    hra_exemption = 0.0
    if regime == "new":
        exempt = 0.0
        professional_tax = 0.0
    else:
        hra_exemption = _hra_exemption(ti)
        exempt += hra_exemption

    net_salary = max(0.0, gross_salary - exempt - std_ded - professional_tax)

    # Show per-employer breakdown when there are multiple Form 16s.
    if len(ti.salaries) > 1:
        steps.append(ComputeStep(key="salary_header", label="— Salary Income —", amount=0, kind="info"))
        for i, s in enumerate(ti.salaries):
            steps.append(ComputeStep(
                key=f"salary_{i}", label=f"  {s.employer_name or f'Employer {i+1}'} (Gross)",
                amount=s.gross_salary, kind="add"))
    steps.append(ComputeStep(key="gross_salary", label="Gross Salary", amount=gross_salary, kind="add"))
    if exempt:
        if hra_exemption and exempt != hra_exemption:
            steps.append(ComputeStep(key="exempt_allowances", label="  Less: Exempt Allowances (HRA/LTA etc.)", amount=exempt - hra_exemption, kind="subtract"))
            steps.append(ComputeStep(key="hra_exemption", label="  Less: HRA Exemption u/s 10(13A)", amount=hra_exemption, kind="subtract"))
        elif hra_exemption:
            steps.append(ComputeStep(key="hra_exemption", label="  Less: HRA Exemption u/s 10(13A)", amount=hra_exemption, kind="subtract"))
        else:
            steps.append(ComputeStep(key="exempt", label="  Less: Exempt Allowances (Sec 10)", amount=exempt, kind="subtract"))
    steps.append(ComputeStep(key="std_ded", label="  Less: Standard Deduction", amount=std_ded, kind="subtract"))
    if professional_tax:
        steps.append(ComputeStep(key="ptax", label="  Less: Professional Tax u/s 16(iii)", amount=professional_tax, kind="subtract"))
    steps.append(ComputeStep(key="net_salary", label="Income from Salary", amount=net_salary, kind="total"))

    hp_income = _house_property(ti, regime)
    if hp_income:
        steps.append(ComputeStep(key="house_property", label="Income from House Property", amount=hp_income, kind="add"))

    fp = ti.family_pension
    fp_cap = K.NEW_FAMILY_PENSION_DEDUCTION_CAP if regime == "new" else K.FAMILY_PENSION_CAP_OLD
    fp_deduction = min(fp * K.FAMILY_PENSION_DED_RATE, fp_cap) if fp else 0.0
    other_sources = (ti.savings_interest + ti.fd_interest + ti.interest_on_bonds
                     + ti.dividend + ti.interest_on_it_refund + ti.other_income + fp)
    stcg_other = ti.capital_gains.stcg_other

    if other_sources:
        steps.append(ComputeStep(key="other_sources_header", label="— Income from Other Sources —", amount=0, kind="info"))
        if ti.savings_interest:
            steps.append(ComputeStep(key="savings_interest", label="  Savings Interest", amount=ti.savings_interest, kind="add"))
        if ti.fd_interest:
            steps.append(ComputeStep(key="fd_interest", label="  FD / Deposit Interest", amount=ti.fd_interest, kind="add"))
        if ti.interest_on_bonds:
            steps.append(ComputeStep(key="bond_interest", label="  Interest on Bonds / Debentures", amount=ti.interest_on_bonds, kind="add"))
        if ti.dividend:
            steps.append(ComputeStep(key="dividend", label="  Dividend Income", amount=ti.dividend, kind="add"))
        if ti.interest_on_it_refund:
            steps.append(ComputeStep(key="it_refund_interest", label="  Interest on IT Refund (Sec 244A)", amount=ti.interest_on_it_refund, kind="add"))
        if fp:
            steps.append(ComputeStep(key="family_pension", label="  Family Pension", amount=fp, kind="add"))
        if ti.other_income:
            steps.append(ComputeStep(key="other_income", label="  Other Income", amount=ti.other_income, kind="add"))
        steps.append(ComputeStep(key="other_sources", label="Income from Other Sources", amount=other_sources, kind="total"))
    if fp_deduction:
        steps.append(ComputeStep(key="fp_ded", label="  Less: Family Pension Deduction (Sec 57)", amount=fp_deduction, kind="subtract"))

    # Professional / freelance income (Sec 194J) — taxed at slab rate.
    # Requires ITR-2; shown as a separate income head.
    prof_fees = ti.professional_fees
    if prof_fees:
        steps.append(ComputeStep(
            key="prof_fees", label="Income from Professional Services (Sec 44ADA / PGBP)",
            amount=prof_fees, kind="add"))

    if stcg_other:
        steps.append(ComputeStep(key="stcg_other", label="STCG — slab rate (other than 111A)", amount=stcg_other, kind="add"))

    gti_normal = net_salary + hp_income + other_sources - fp_deduction + prof_fees + stcg_other

    # Brought-forward loss set-off (old regime) against gross total income.
    if regime == "old" and ti.brought_forward_loss > 0:
        setoff = min(ti.brought_forward_loss, max(0.0, gti_normal))
        if setoff:
            steps.append(ComputeStep(key="bf_loss", label="Less: Brought-Forward Loss Set-off", amount=setoff, kind="subtract"))
            gti_normal -= setoff

    steps.append(ComputeStep(key="gti", label="Gross Total Income (slab)", amount=gti_normal, kind="total"))

    deductions = _deductions_for_regime(ti, regime, gross_salary, gti_normal)
    deductions = min(deductions, max(0.0, gti_normal))
    if deductions:
        steps.append(ComputeStep(key="chvia_header", label="— Chapter VI-A Deductions —", amount=0, kind="info"))
        _emit_deduction_steps(steps, ti, regime, gross_salary, gti_normal)
        steps.append(ComputeStep(key="chvia", label="Total Deductions (Chapter VI-A)", amount=deductions, kind="subtract"))

    normal_income = _round_to_ten(max(0.0, gti_normal - deductions))
    steps.append(ComputeStep(key="total_income", label="Taxable Income (slab)", amount=normal_income, kind="total"))

    slabs = K.NEW_REGIME_SLABS if regime == "new" else _old_regime_slabs(ti.age)
    slab_tax = _agri_adjusted_slab_tax(
        _slab_tax(normal_income, slabs), normal_income, ti, slabs, regime)

    cg = _capital_gains_tax(ti, normal_income, regime)
    special_income = (ti.capital_gains.stcg_111a + ti.capital_gains.ltcg_112a
                      + ti.capital_gains.ltcg_other + ti.capital_gains.vda_gain)
    total_income = normal_income + special_income

    if special_income:
        steps.append(ComputeStep(key="cg_header", label="— Capital Gains (special rates) —", amount=0, kind="info"))
        if ti.capital_gains.stcg_111a:
            steps.append(ComputeStep(key="stcg_111a", label="  STCG u/s 111A (listed equity, 20%)", amount=ti.capital_gains.stcg_111a, kind="add"))
        if ti.capital_gains.ltcg_112a:
            steps.append(ComputeStep(key="ltcg_112a", label=f"  LTCG u/s 112A (listed equity; ₹{int(K.LTCG_112A_EXEMPTION):,} exempt, 12.5%)", amount=ti.capital_gains.ltcg_112a, kind="add"))
        if ti.capital_gains.ltcg_other:
            steps.append(ComputeStep(key="ltcg_other", label="  LTCG other (Sec 112, 12.5%)", amount=ti.capital_gains.ltcg_other, kind="add"))
        if ti.capital_gains.vda_gain:
            steps.append(ComputeStep(key="vda", label="  VDA / Crypto Gains (flat 30%)", amount=ti.capital_gains.vda_gain, kind="add"))

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

    gross_tax = tax_after_rebate + surcharge + cess
    relief = ti.relief_89 + ti.relief_90_91
    relief = min(relief, gross_tax)
    if relief:
        steps.append(ComputeStep(key="relief", label="Less: Relief (Sec 89 / 90 / 91)", amount=relief, kind="subtract"))

    total_tax = _round_to_ten(gross_tax - relief)
    steps.append(ComputeStep(key="total_tax", label="Total Tax Liability", amount=total_tax, kind="total"))

    taxes_paid = (ti.tds_total + ti.tcs_total + ti.advance_tax
                  + ti.self_assessment_tax + ti.tds_on_property_purchase)
    steps.append(ComputeStep(key="taxes_paid", label="Less: Taxes Already Paid (TDS/TCS/Advance)", amount=taxes_paid, kind="subtract"))

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


def compute_taxes(ti: TaxInput, regime: str) -> TaxComputation:
    """Compute the tax for the regime the user is filing under.

    Args:
        ti: Consolidated tax input.
        regime: The chosen filing regime, "old" or "new".

    Returns:
        A ``TaxComputation`` holding the traced result for that regime.
    """
    return TaxComputation(result=compute_regime(ti, regime), regime=regime)
