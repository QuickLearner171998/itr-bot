"""Generate an independent ground-truth tax computation report from local documents.

IMPORTANT: The tax computation in this script is intentionally written from
scratch, independent of backend/compute/engine.py. This makes it a true GT —
any mismatch between this script and the app's engine reveals a bug in one of
them.

Only the document *extraction* uses the app's LLM pipeline (because reading
PDFs is not the thing being tested). The extracted raw numbers feed into an
independent reimplementation of Indian income-tax rules for FY 2025-26 / AY 2026-27.

Usage:
    uv run python scripts/generate_gt_report.py [--docs-dir "~/Documents/itr 2026"] [--out gt_report.md]

Document passwords via DOC_PASSWORDS env-var (JSON):
    DOC_PASSWORDS='{"ais": "pan+dob"}' uv run python scripts/generate_gt_report.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Only extraction helpers are imported from the app — not engine or consolidate.
from backend.agents.doc_intel.extract import extract_document
from backend.app.events import bus  # noqa: F401 needed by extract_document internals
from backend.schemas.documents import DocType

_SESSION = "gt_report_independent"


# ---------------------------------------------------------------------------
# Independent FY 2025-26 tax rules — NO imports from backend/compute/
# ---------------------------------------------------------------------------

def _round10(x: float) -> float:
    """Round to the nearest multiple of ten (Sec 288A/288B)."""
    return float(round(x / 10.0) * 10)


# Old regime slabs (age < 60)
_OLD_SLABS_BELOW60 = [
    (250_000,   0.00),
    (500_000,   0.05),
    (1_000_000, 0.20),
    (None,      0.30),
]

# Old regime slabs (60 <= age < 80)
_OLD_SLABS_SENIOR = [
    (300_000,   0.00),
    (500_000,   0.05),
    (1_000_000, 0.20),
    (None,      0.30),
]

# Old regime slabs (age >= 80)
_OLD_SLABS_SUPERSЕНIOR = [
    (500_000,   0.00),
    (1_000_000, 0.20),
    (None,      0.30),
]

# New regime slabs (FY 2025-26: updated per Budget 2025)
_NEW_SLABS = [
    (400_000,   0.00),
    (800_000,   0.05),
    (1_200_000, 0.10),
    (1_600_000, 0.15),
    (2_000_000, 0.20),
    (2_400_000, 0.25),
    (None,      0.30),
]

_OLD_STD_DEDUCTION = 50_000
_NEW_STD_DEDUCTION = 75_000
_NEW_BASIC_EXEMPTION = 400_000  # nil slab upper bound
_NEW_REBATE87A_LIMIT = 1_200_000  # new regime rebate limit (post-Budget 2025)
_OLD_REBATE87A_LIMIT = 500_000
_NEW_REBATE87A_AMOUNT = 60_000   # max rebate new
_OLD_REBATE87A_AMOUNT = 12_500   # max rebate old
_CESS_RATE = 0.04
_LTCG_112A_EXEMPT = 125_000
_LTCG_112A_RATE = 0.125
_STCG_111A_RATE = 0.20
_LTCG_OTHER_RATE = 0.125   # Sec 112 (post 23-Jul-2024) — listed/unlisted non-112A LTCG
_VDA_RATE = 0.30           # Sec 115BBH virtual digital assets

# Chapter VI-A caps (old regime)
_CAP_80C = 150_000
_CAP_80CCD1B = 50_000
_CAP_80D_PARENTS = 25_000
_CAP_80D_PARENTS_SENIOR = 50_000
_CAP_80EEA = 150_000
_CAP_80DD_NORMAL = 75_000
_CAP_80DD_SEVERE = 125_000
_CAP_80U_NORMAL = 75_000
_CAP_80U_SEVERE = 125_000
_CAP_80DDB_NORMAL = 40_000
_CAP_80DDB_SENIOR = 100_000
_CAP_80TTA = 10_000
_CAP_80TTB = 50_000        # seniors: savings + FD interest
_CAP_80GG_ANNUAL = 60_000
_RATE_80GG_INCOME = 0.25
_RATE_80GG_RENT_MINUS_INCOME = 0.10
_RATE_80G_QUALIFYING = 0.10
_EMPLOYER_NPS_CAP_NEW = 0.14   # 80CCD(2): 14% of salary (new regime)
_EMPLOYER_NPS_CAP_OLD = 0.10   # 10% (old / private)
_HOME_LOAN_SELF_OCCUPIED_CAP = 200_000
_HP_STD_DEDUCTION_RATE = 0.30

# Family pension standard deduction (Sec 57(iia))
_FP_DED_RATE = 1.0 / 3.0
_FP_CAP_OLD = 15_000
_FP_CAP_NEW = 25_000

# Surcharge: (upper_bound_inclusive, rate) cumulative. Old regime full ladder;
# new regime caps at 25%. Surcharge on 111A/112A gains capped at 15%.
_SURCHARGE_SLABS = [
    (5_000_000,  0.00),
    (10_000_000, 0.10),
    (20_000_000, 0.15),
    (50_000_000, 0.25),
    (float("inf"), 0.37),
]
_NEW_SURCHARGE_CAP = 0.25
_CG_SURCHARGE_CAP = 0.15


def _slab_tax(income: float, slabs: list[tuple]) -> float:
    """Compute slab tax on income using the given slab table."""
    tax = 0.0
    prev = 0.0
    for upper, rate in slabs:
        if upper is None:
            tax += max(0.0, income - prev) * rate
        else:
            band = min(income, upper) - prev
            if band > 0:
                tax += band * rate
            if income <= upper:
                break
        prev = upper
    return tax


def _old_slabs(age: int) -> list[tuple]:
    if age >= 80:
        return _OLD_SLABS_SUPERSЕНIOR
    if age >= 60:
        return _OLD_SLABS_SENIOR
    return _OLD_SLABS_BELOW60


def _basic_exemption_old(age: int) -> float:
    if age >= 80:
        return 500_000
    if age >= 60:
        return 300_000
    return 250_000


def _surcharge_rate(total_income: float, regime: str) -> float:
    """Marginal surcharge rate (before marginal relief), new regime capped 25%."""
    rate = 0.0
    for upper, slab_rate in _SURCHARGE_SLABS:
        if total_income <= upper:
            rate = slab_rate
            break
    if regime == "new":
        rate = min(rate, _NEW_SURCHARGE_CAP)
    return rate


def _surcharge_threshold(total_income: float) -> float:
    """Lower bound of the surcharge band ``total_income`` falls into."""
    lower = 0.0
    for upper, _ in _SURCHARGE_SLABS:
        if total_income <= upper:
            return lower
        lower = upper
    return lower


def _hra_exemption(hra_received: float, rent_paid: float, basic_da: float,
                   is_metro: bool) -> float:
    """Sec 10(13A) HRA exemption — minimum of three limits."""
    if hra_received <= 0 or rent_paid <= 0:
        return 0.0
    metro_pct = 0.50 if is_metro else 0.40
    limit1 = hra_received
    limit2 = rent_paid - 0.10 * basic_da
    limit3 = metro_pct * basic_da
    return max(0.0, min(limit1, limit2, limit3))


def _chapter_via(raw: dict, age: int, gti_normal: float,
                 savings_interest: float, fd_interest: float,
                 gross_salary: float, hra_exempt: float) -> tuple[float, list[tuple[str, float]]]:
    """Old-regime Chapter VI-A deductions, independently capped per tax law.

    Args:
        raw: Consolidated extracted figures.
        age: Filer age (drives senior 80D/80TTB/80DDB limits).
        gti_normal: Slab gross total income (for 80G/80GG income-based limits).
        savings_interest: Savings-bank interest (80TTA / part of 80TTB base).
        fd_interest: Deposit interest (80TTB base for seniors).
        gross_salary: Salary base for the 80CCD(2) percentage cap.
        hra_exempt: HRA exemption claimed (blocks 80GG when > 0).

    Returns:
        ``(total_deduction, [(label, amount), ...])`` with one entry per
        non-zero component.
    """
    def g(k):
        v = raw.get(k)
        return float(v) if v not in (None, "") else 0.0

    def yes(k):
        return str(raw.get(k) or "").strip().lower().startswith(("y", "t"))

    is_senior = age >= 60
    lines: list[tuple[str, float]] = []

    eighty_c = min(g("amount_80c"), _CAP_80C)
    eighty_ccd1b = min(g("amount_80ccd1b"), _CAP_80CCD1B)
    employer_nps = min(g("amount_80ccd2"), gross_salary * _EMPLOYER_NPS_CAP_OLD)
    d80_self = min(g("amount_80d_self"), _CAP_80D_PARENTS_SENIOR if is_senior else _CAP_80D_PARENTS)
    d80_par = min(g("amount_80d_parents"), _CAP_80D_PARENTS_SENIOR if is_senior else _CAP_80D_PARENTS)

    if is_senior:
        eighty_tt = min(savings_interest + fd_interest, _CAP_80TTB)
        tt_label = "80TTB Interest (Senior; cap ₹50,000)"
    else:
        eighty_tt = min(savings_interest, _CAP_80TTA)
        tt_label = "80TTA Savings Interest (cap ₹10,000)"

    eighty_e = g("amount_80e")
    eighty_eea = min(g("amount_80eea"), _CAP_80EEA)
    eighty_dd = (_CAP_80DD_SEVERE if yes("amount_80dd_severe") else _CAP_80DD_NORMAL) if g("amount_80dd") > 0 else 0.0
    eighty_u = (_CAP_80U_SEVERE if yes("amount_80u_severe") else _CAP_80U_NORMAL) if g("amount_80u") > 0 else 0.0
    eighty_ddb = min(g("amount_80ddb"), _CAP_80DDB_SENIOR if is_senior else _CAP_80DDB_NORMAL)

    eighty_gg = 0.0
    if g("amount_80gg") > 0 and hra_exempt <= 0:
        eighty_gg = max(0.0, min(_CAP_80GG_ANNUAL, _RATE_80GG_INCOME * gti_normal,
                                 g("amount_80gg") - _RATE_80GG_RENT_MINUS_INCOME * gti_normal))

    base = (eighty_c + eighty_ccd1b + employer_nps + d80_self + d80_par + eighty_tt
            + eighty_e + eighty_eea + eighty_dd + eighty_u + eighty_ddb + eighty_gg)
    no_limit = g("donation_100_no_limit") + 0.5 * g("donation_50_no_limit")
    pool = max(0.0, _RATE_80G_QUALIFYING * max(0.0, gti_normal - base))
    d100 = min(g("donation_100_limit"), pool)
    d50 = min(g("donation_50_limit"), pool - d100)
    eighty_g = no_limit + d100 + 0.5 * d50

    for label, amt in [
        (f"80C (incl. home-loan principal; cap ₹{_CAP_80C:,})", eighty_c),
        (f"80CCD(1B) NPS Self (cap ₹{_CAP_80CCD1B:,})", eighty_ccd1b),
        ("80CCD(2) Employer NPS (cap 10% salary)", employer_nps),
        ("80D Health Insurance — Self/Family", d80_self),
        ("80D Health Insurance — Parents", d80_par),
        (tt_label, eighty_tt),
        ("80E Education Loan Interest", eighty_e),
        (f"80EEA Additional Home-Loan Interest (cap ₹{_CAP_80EEA:,})", eighty_eea),
        ("80DD Disabled Dependent", eighty_dd),
        ("80U Self Disability", eighty_u),
        ("80DDB Specified-Disease Treatment", eighty_ddb),
        ("80GG Rent Paid (no HRA)", eighty_gg),
        ("80G Donations", eighty_g),
    ]:
        if amt > 0:
            lines.append((label, amt))

    return base + eighty_g, lines


def _compute_gt(raw: dict, age: int) -> dict:
    """Independent FY 2025-26 tax computation for both regimes.

    Reimplements Indian income-tax rules from first principles over the
    consolidated extracted figures: all income heads (salary, house property,
    other sources, professional fees, capital gains), Chapter VI-A deductions,
    Sec 87A rebate with new-regime marginal relief, surcharge with the 15%
    capital-gains cap and marginal relief, and cess.

    Args:
        raw: Consolidated extracted figures (see :func:`_consolidate`).
        age: Filer age.

    Returns:
        Dict keyed ``"old"``/``"new"`` with a step trace and all totals.
    """
    def g(k, default=0.0):
        v = raw.get(k, default)
        return float(v) if v not in (None, "") else default

    gross_salary      = g("gross_salary")
    exempt_allowances = g("exempt_allowances")
    professional_tax  = g("professional_tax")
    hra_received      = g("hra_received")
    hra_rent_paid     = g("hra_rent_paid")
    hra_basic_da      = g("hra_basic_da")
    hra_is_metro      = bool(raw.get("hra_is_metro", False))

    savings_interest = g("savings_interest")
    fd_interest      = g("fd_interest")
    bond_interest    = g("interest_on_bonds")
    dividend         = g("dividend")
    it_refund_int    = g("interest_on_it_refund")
    family_pension   = g("family_pension")
    other_income     = g("other_income")
    professional_fees = g("professional_fees")

    let_out_rent    = g("let_out_annual_rent")
    municipal_taxes = g("let_out_municipal_taxes")
    home_interest   = g("home_loan_interest")
    self_occupied   = bool(raw.get("home_loan_self_occupied", False))

    stcg_111a  = g("stcg_111a")
    ltcg_112a  = g("ltcg_112a")
    stcg_other = g("stcg_other")
    ltcg_other = g("ltcg_other")
    vda_gain   = g("vda_gain")

    taxes_paid = (g("tds_total") + g("tcs_total") + g("advance_tax")
                  + g("self_assessment_tax") + g("tds_on_property_purchase"))

    results: dict = {}

    for regime in ("old", "new"):
        steps: list[dict] = []

        def add(label, amount, kind="info"):
            steps.append({"label": label, "amount": amount, "kind": kind})

        std_ded = _NEW_STD_DEDUCTION if regime == "new" else _OLD_STD_DEDUCTION
        add("Gross Salary", gross_salary, "add")

        hra_exempt = 0.0
        if regime == "old":
            sec10 = exempt_allowances
            if sec10 <= 0:
                hra_exempt = _hra_exemption(hra_received, hra_rent_paid, hra_basic_da, hra_is_metro)
                sec10 = hra_exempt
            if sec10 > 0:
                add("Less: Sec 10 Exempt Allowances (incl. HRA/LTA)", sec10, "subtract")
            add("Less: Standard Deduction u/s 16(ia)", std_ded, "subtract")
            if professional_tax:
                add("Less: Professional Tax u/s 16(iii)", professional_tax, "subtract")
            net_salary = max(0.0, gross_salary - sec10 - std_ded - professional_tax)
        else:
            add("Less: Standard Deduction u/s 16(ia)", std_ded, "subtract")
            net_salary = max(0.0, gross_salary - std_ded)
        add("Income from Salary", net_salary, "info")

        # House property
        hp_income = 0.0
        if let_out_rent > 0:
            nav = max(0.0, let_out_rent - municipal_taxes)
            std_hp = nav * _HP_STD_DEDUCTION_RATE
            net_hp = nav - std_hp - home_interest
            hp_income = max(net_hp, -_HOME_LOAN_SELF_OCCUPIED_CAP) if regime == "old" else max(net_hp, 0.0)
            add("Let-out Annual Rent", let_out_rent, "add")
            if municipal_taxes:
                add("Less: Municipal Taxes", municipal_taxes, "subtract")
            add("Less: 30% Standard Deduction on NAV", std_hp, "subtract")
            if home_interest:
                add("Less: Home-Loan Interest u/s 24(b)", home_interest, "subtract")
            add("Income from House Property", hp_income, "info")
        elif self_occupied and home_interest and regime == "old":
            hp_income = -min(home_interest, _HOME_LOAN_SELF_OCCUPIED_CAP)
            add("House Property: Self-Occupied Loss u/s 24(b)", hp_income, "add")

        # Other sources
        fp_cap = _FP_CAP_NEW if regime == "new" else _FP_CAP_OLD
        fp_ded = min(family_pension * _FP_DED_RATE, fp_cap) if family_pension else 0.0
        other_sources = (savings_interest + fd_interest + bond_interest + dividend
                         + it_refund_int + family_pension + other_income)
        if other_sources:
            add("— Income from Other Sources —", 0, "info")
            for lbl, amt in [("Savings Interest", savings_interest), ("FD / Deposit Interest", fd_interest),
                             ("Interest on Bonds", bond_interest), ("Dividend", dividend),
                             ("Interest on IT Refund", it_refund_int), ("Family Pension", family_pension),
                             ("Other Income", other_income)]:
                if amt:
                    add(f"  {lbl}", amt, "add")
            if fp_ded:
                add("  Less: Family Pension Deduction (Sec 57)", fp_ded, "subtract")
        if professional_fees:
            add("Income from Professional Services (Sec 194J)", professional_fees, "add")
        if stcg_other:
            add("STCG — slab rate (other than 111A)", stcg_other, "add")

        gti_normal = (net_salary + hp_income + other_sources - fp_ded
                      + professional_fees + stcg_other)
        add("Gross Total Income (slab)", gti_normal, "total")

        # Chapter VI-A
        if regime == "old":
            total_ded, ded_lines = _chapter_via(
                raw, age, gti_normal, savings_interest, fd_interest, gross_salary, hra_exempt)
        else:
            total_ded = min(g("amount_80ccd2"), gross_salary * _EMPLOYER_NPS_CAP_NEW)
            ded_lines = [("80CCD(2) Employer NPS (cap 14% salary)", total_ded)] if total_ded > 0 else []
        total_ded = min(total_ded, max(0.0, gti_normal))
        if ded_lines:
            add("— Chapter VI-A Deductions —", 0, "info")
            for lbl, amt in ded_lines:
                add(f"  {lbl}", amt, "subtract")
            add("Total Deductions (Chapter VI-A)", total_ded, "subtract")

        normal_income = _round10(max(0.0, gti_normal - total_ded))
        add("Taxable Income (slab)", normal_income, "total")

        slabs = _NEW_SLABS if regime == "new" else _old_slabs(age)
        slab_tax = _slab_tax(normal_income, slabs)
        add("Tax on Slab Income", slab_tax, "tax")

        # Capital gains with basic-exemption shortfall set-off (residents).
        basic_exempt = _NEW_BASIC_EXEMPTION if regime == "new" else _basic_exemption_old(age)
        shortfall = max(0.0, basic_exempt - normal_income)
        stcg_111a_t = stcg_111a
        used = min(shortfall, stcg_111a_t); stcg_111a_t -= used; shortfall -= used
        ltcg_112a_t = max(0.0, ltcg_112a - _LTCG_112A_EXEMPT)
        used = min(shortfall, ltcg_112a_t); ltcg_112a_t -= used; shortfall -= used

        tax_111a = stcg_111a_t * _STCG_111A_RATE
        tax_112a = ltcg_112a_t * _LTCG_112A_RATE
        tax_ltcg_other = ltcg_other * _LTCG_OTHER_RATE
        tax_vda = vda_gain * _VDA_RATE
        cg_tax = tax_111a + tax_112a + tax_ltcg_other + tax_vda
        special_income = stcg_111a + ltcg_112a + ltcg_other + vda_gain
        total_income = normal_income + special_income

        if special_income:
            add("— Capital Gains (special rates) —", 0, "info")
            if stcg_111a:
                add("  STCG u/s 111A (20%)", stcg_111a, "add")
            if ltcg_112a:
                add("  LTCG u/s 112A (₹1,25,000 exempt, 12.5%)", ltcg_112a, "add")
            if ltcg_other:
                add("  LTCG other (Sec 112, 12.5%)", ltcg_other, "add")
            if vda_gain:
                add("  VDA / Crypto Gains (30%)", vda_gain, "add")
            add("Tax on Capital Gains (special rates)", cg_tax, "tax")

        # Rebate u/s 87A (against slab tax only) + new-regime marginal relief.
        rebate = mr_rebate = 0.0
        if regime == "old":
            if total_income <= _OLD_REBATE87A_LIMIT:
                rebate = min(slab_tax, _OLD_REBATE87A_AMOUNT)
        else:
            if total_income <= _NEW_REBATE87A_LIMIT:
                rebate = min(slab_tax, _NEW_REBATE87A_AMOUNT)
            else:
                excess = total_income - _NEW_REBATE87A_LIMIT
                if 0 < excess < slab_tax:
                    mr_rebate = slab_tax - excess
        if rebate:
            add("Less: Rebate u/s 87A", rebate, "subtract")
        if mr_rebate:
            add("Less: Marginal Relief (87A)", mr_rebate, "subtract")

        slab_tax_after = max(0.0, slab_tax - rebate - mr_rebate)
        tax_after_rebate = slab_tax_after + cg_tax

        # Surcharge with 15% CG cap and marginal relief at the band threshold.
        surcharge, mr_sc = _surcharge_with_relief(
            slab_tax_after, tax_111a + tax_112a, tax_ltcg_other + tax_vda,
            cg_tax, total_income, special_income, regime, slabs)
        if surcharge:
            add("Add: Surcharge", surcharge, "add")
        if mr_sc:
            add("Less: Marginal Relief (Surcharge)", mr_sc, "subtract")

        cess = (tax_after_rebate + surcharge) * _CESS_RATE
        add("Add: Health & Education Cess (4%)", cess, "add")

        total_tax = _round10(tax_after_rebate + surcharge + cess)
        add("Total Tax Liability", total_tax, "total")
        add("Less: Taxes Paid (TDS/TCS/Advance/SAT)", taxes_paid, "subtract")

        payable = _round10(total_tax - taxes_paid)
        add("Tax Payable / (Refund)", payable, "total")

        results[regime] = {
            "steps": steps,
            "gross_total_income": gti_normal + special_income,
            "taxable_income": total_income,
            "total_deductions": total_ded,
            "slab_tax": slab_tax,
            "cg_tax": cg_tax,
            "tax_before_rebate": slab_tax + cg_tax,
            "rebate_87a": rebate + mr_rebate,
            "surcharge": surcharge,
            "cess": cess,
            "total_tax": total_tax,
            "taxes_paid": taxes_paid,
            "payable": payable,
        }

    return results


def _surcharge_with_relief(
    slab_tax_after: float, capped_cg_tax: float, uncapped_cg_tax: float,
    cg_tax_total: float, total_income: float, special_income: float,
    regime: str, slabs: list[tuple],
) -> tuple[float, float]:
    """Surcharge with the 15% cap on 111A/112A gains and marginal relief.

    Marginal relief ensures the rise in (tax + surcharge) over the band
    threshold does not exceed the income above that threshold.

    Args:
        slab_tax_after: Slab tax after 87A rebate.
        capped_cg_tax: Tax on 111A + 112A gains (surcharge capped at 15%).
        uncapped_cg_tax: Tax on other LTCG + VDA gains (full surcharge rate).
        cg_tax_total: Total capital-gains tax.
        total_income: Normal + special income (drives the surcharge rate).
        special_income: Gross special-rate gains (for the threshold split).
        regime: "old" or "new".
        slabs: Slab table for re-computing tax at the band threshold.

    Returns:
        ``(surcharge, marginal_relief)``.
    """
    rate = _surcharge_rate(total_income, regime)
    if rate == 0.0:
        return 0.0, 0.0

    cg_rate = min(rate, _CG_SURCHARGE_CAP)
    surcharge = slab_tax_after * rate + uncapped_cg_tax * rate + capped_cg_tax * cg_rate

    threshold = _surcharge_threshold(total_income)
    prev_rate = _surcharge_rate(threshold, regime) if threshold > 0 else 0.0
    normal_at_t = max(0.0, threshold - special_income)
    slab_tax_at_t = _slab_tax(normal_at_t, slabs)
    sc_at_t = (slab_tax_at_t + uncapped_cg_tax) * prev_rate + capped_cg_tax * min(prev_rate, _CG_SURCHARGE_CAP)

    total_now = slab_tax_after + cg_tax_total + surcharge
    total_at_t = slab_tax_at_t + cg_tax_total + sc_at_t
    allowed = total_income - threshold
    marginal_relief = max(0.0, (total_now - total_at_t) - allowed)
    return surcharge - marginal_relief, marginal_relief


# ---------------------------------------------------------------------------
# Document discovery and extraction (reuses app LLM pipeline for PDF reading)
# ---------------------------------------------------------------------------

def _discover_docs(root: pathlib.Path) -> list[tuple[DocType, pathlib.Path]]:
    """Walk the root folder and return (DocType, file_path) pairs."""
    aliases = {
        "26as": DocType.FORM26AS, "ais": DocType.AIS, "form16": DocType.FORM16,
        "pnl": DocType.BROKER_PNL, "broker": DocType.BROKER_PNL,
        "interest": DocType.INTEREST_CERT, "interest_cert": DocType.INTEREST_CERT,
        "home_loan": DocType.HOME_LOAN_CERT, "deductions": DocType.DEDUCTION_PROOF,
        "rent": DocType.RENT_RECEIPT,
    }
    for dt in DocType:
        aliases[dt.value] = dt

    found: list[tuple[DocType, pathlib.Path]] = []
    if not root.exists():
        print(f"[warn] docs-dir not found: {root}", file=sys.stderr)
        return found

    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        dt = aliases.get(sub.name.lower())
        if dt is None:
            print(f"[skip] unknown folder '{sub.name}'", file=sys.stderr)
            continue
        for f in sorted(sub.iterdir()):
            if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".xls", ".csv"):
                found.append((dt, f))
    return found


async def _extract_all(
    doc_files: list[tuple[DocType, pathlib.Path]],
    passwords: dict[str, str],
) -> list:
    MIME = {
        ".pdf": "application/pdf", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel", ".csv": "text/csv",
    }
    results = []
    for i, (dt, fpath) in enumerate(doc_files):
        data = fpath.read_bytes()
        mime = MIME.get(fpath.suffix.lower(), "application/pdf")
        pw = passwords.get(dt.value) or passwords.get(fpath.stem.lower())
        print(f"  [{i+1}/{len(doc_files)}] {dt.value}: {fpath.name} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            ext = await extract_document(
                session_id=_SESSION, doc_type=dt, filename=fpath.name,
                data=data, mime=mime, password=pw, upload_id=fpath.stem,
            )
            print(f"ok ({time.time()-t0:.1f}s, conf={ext.overall_confidence:.2f})")
            results.append((dt, fpath.name, ext))
        except Exception as exc:
            print(f"FAILED: {exc}")
    return results


def _consolidate(extracted: list) -> dict:
    """Independently consolidate per-document extractions into one raw dict.

    Reimplements the cross-document reconciliation from tax-reporting first
    principles (not from the app's ``consolidate.py``):

      * Salary, TDS and Form-16 deductions are summed across all Form 16s.
      * Where the same figure is reported by multiple sources (e.g. interest in
        both the bank certificate and AIS, salary in Form 16 vs AIS), the larger
        value is taken so nothing is under-reported.
      * Capital gains and donations are summed across all brokers / receipts.
      * Home-loan principal is folded into 80C; let-out rent drives house
        property income.

    Args:
        extracted: List of ``(DocType, filename, DocumentExtraction)``.

    Returns:
        Flat raw dict consumed by :func:`_compute_gt`.
    """
    def n(v) -> float:
        if v in (None, ""):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace(",", "").replace("\u20b9", "").strip()
        return float(s) if s.replace(".", "", 1).lstrip("-").isdigit() else 0.0

    by_type: dict[DocType, list[dict]] = {}
    for dt, _fn, ext in extracted:
        by_type.setdefault(dt, []).append({f.name: f.value for f in ext.fields})

    f16 = by_type.get(DocType.FORM16, [])
    ais = by_type.get(DocType.AIS, [{}])[0] if by_type.get(DocType.AIS) else {}
    f26 = by_type.get(DocType.FORM26AS, [{}])[0] if by_type.get(DocType.FORM26AS) else {}
    raw: dict = {}

    # --- Salary (sum Form 16s) + AIS reconcile (take the larger salary) ---
    f16_gross = sum(n(v.get("gross_salary")) for v in f16)
    raw["gross_salary"] = max(f16_gross, n(ais.get("salary_reported")))
    raw["exempt_allowances"] = sum(n(v.get("exempt_allowances")) for v in f16)
    raw["professional_tax"] = sum(n(v.get("professional_tax")) for v in f16)
    f16_tds = sum(n(v.get("tds")) for v in f16)
    regimes = [str(v.get("regime") or "").strip().lower() for v in f16]
    raw["filing_regime"] = "old" if any(r.startswith("o") for r in regimes) else "new"

    # Form-16 Chapter VI-A (statutory caps guard against mis-read rows).
    f16_80c = max((min(n(v.get("deduction_80c")), _CAP_80C) for v in f16), default=0.0)
    f16_80ccd1b = max((min(n(v.get("deduction_80ccd1b")), _CAP_80CCD1B) for v in f16), default=0.0)
    f16_80ccd2 = max((n(v.get("deduction_80ccd2")) for v in f16), default=0.0)
    f16_80d = max((min(n(v.get("deduction_80d")), 50_000) for v in f16), default=0.0)

    # --- Deduction proofs ---
    proofs = by_type.get(DocType.DEDUCTION_PROOF, [])
    def proof_max(key):
        return max((n(v.get(key)) for v in proofs), default=0.0)

    # --- Home loan / house property ---
    home_interest = sum(n(v.get("interest_paid")) for v in by_type.get(DocType.HOME_LOAN_CERT, []))
    home_principal = sum(n(v.get("principal_repaid")) for v in by_type.get(DocType.HOME_LOAN_CERT, []))
    let_out_rent = sum(n(v.get("let_out_annual_rent")) for v in by_type.get(DocType.HOME_LOAN_CERT, []))
    municipal = sum(n(v.get("municipal_taxes")) for v in by_type.get(DocType.HOME_LOAN_CERT, []))
    self_occ = any(str(v.get("is_self_occupied") or "").strip().lower().startswith(("y", "t"))
                   for v in by_type.get(DocType.HOME_LOAN_CERT, []))
    ais_rent = n(ais.get("rent_received"))
    if let_out_rent == 0 and ais_rent > 0:
        let_out_rent = ais_rent
    raw["let_out_annual_rent"] = let_out_rent
    raw["let_out_municipal_taxes"] = municipal
    raw["home_loan_interest"] = home_interest
    raw["home_loan_self_occupied"] = self_occ and let_out_rent == 0

    # --- HRA (recompute from rent receipt only if employer granted no Sec 10) ---
    if raw["exempt_allowances"] <= 0:
        rr = by_type.get(DocType.RENT_RECEIPT, [])
        raw["hra_received"] = max((n(v.get("hra_received")) for v in rr), default=0.0)
        raw["hra_rent_paid"] = max((n(v.get("rent_paid")) for v in rr), default=0.0)
        raw["hra_basic_da"] = max((n(v.get("basic_da")) for v in rr), default=0.0)
        raw["hra_is_metro"] = any(str(v.get("is_metro") or "").strip().lower().startswith(("y", "t")) for v in rr)

    # --- Chapter VI-A (larger of Form 16 / proof; principal folded into 80C) ---
    raw["amount_80c"] = max(f16_80c, proof_max("amount_80c"), home_principal)
    raw["amount_80ccd1b"] = max(f16_80ccd1b, proof_max("amount_80ccd1b"))
    raw["amount_80ccd2"] = f16_80ccd2
    raw["amount_80d_self"] = max(f16_80d, proof_max("amount_80d_self"))
    raw["amount_80d_parents"] = proof_max("amount_80d_parents")
    raw["amount_80e"] = sum(n(v.get("amount_80e")) for v in proofs)
    raw["amount_80eea"] = proof_max("amount_80eea")
    raw["amount_80dd"] = proof_max("amount_80dd")
    raw["amount_80dd_severe"] = any(str(v.get("amount_80dd_severe") or "").lower().startswith(("y", "t")) for v in proofs)
    raw["amount_80ddb"] = proof_max("amount_80ddb")
    raw["amount_80u"] = proof_max("amount_80u")
    raw["amount_80u_severe"] = any(str(v.get("amount_80u_severe") or "").lower().startswith(("y", "t")) for v in proofs)
    raw["amount_80gg"] = sum(n(v.get("amount_80gg")) for v in proofs)
    for key in ("donation_100_no_limit", "donation_50_no_limit", "donation_100_limit", "donation_50_limit"):
        raw[key] = sum(n(v.get(key)) for v in by_type.get(DocType.DONATION_80G, []))

    # --- Other-source income (larger of certificate vs AIS) ---
    cert = by_type.get(DocType.INTEREST_CERT, [])
    cert_savings = sum(n(v.get("savings_interest")) for v in cert)
    cert_fd = sum(n(v.get("fd_interest")) for v in cert)
    raw["savings_interest"] = max(cert_savings, n(ais.get("savings_interest")))
    raw["fd_interest"] = max(cert_fd, n(ais.get("fd_interest")))
    raw["interest_on_bonds"] = n(ais.get("interest_on_bonds"))
    broker_div = sum(n(v.get("dividend")) for v in by_type.get(DocType.BROKER_PNL, []))
    raw["dividend"] = max(broker_div, n(ais.get("dividend")))
    raw["family_pension"] = n(ais.get("family_pension"))
    raw["interest_on_it_refund"] = n(ais.get("interest_on_it_refund"))
    raw["professional_fees"] = n(ais.get("professional_fees"))

    # --- Capital gains (sum across brokers) ---
    for key in ("stcg_111a", "ltcg_112a", "stcg_other", "ltcg_other", "vda_gain"):
        raw[key] = sum(n(v.get(key)) for v in by_type.get(DocType.BROKER_PNL, []))

    # --- Taxes paid (larger of per-document vs 26AS aggregate) ---
    tds_individual = (f16_tds
                      + sum(n(v.get("tds")) for v in by_type.get(DocType.FORM16A, []))
                      + sum(n(v.get("tds")) for v in cert))
    tds_26as = n(f26.get("total_tds_salary")) + n(f26.get("total_tds_other"))
    raw["tds_total"] = max(tds_individual, tds_26as)
    raw["tcs_total"] = max(n(f26.get("tcs_total")), n(ais.get("tcs_total")))
    raw["advance_tax"] = max(n(f26.get("advance_tax")), n(ais.get("advance_tax")))
    raw["self_assessment_tax"] = max(n(f26.get("self_assessment_tax")), n(ais.get("self_assessment_tax")))
    raw["tds_on_property_purchase"] = n(f26.get("tds_on_property_purchase"))

    return raw


def _inr(v: float) -> str:
    return f"₹{v:,.0f}"


def _render_steps(steps: list) -> list[str]:
    lines = []
    for s in steps:
        sign = "+" if s["kind"] == "add" else ("-" if s["kind"] == "subtract" else " ")
        lines.append(f"  {sign} {s['label']:<52} {_inr(s['amount']):>14}")
    return lines


def _write_report(
    out: pathlib.Path,
    doc_files: list[tuple[DocType, pathlib.Path]],
    extracted: list,
    raw: dict,
    gt: dict,
) -> None:
    lines: list[str] = [
        "# Independent Ground-Truth Tax Report — FY 2025-26 / AY 2026-27",
        "",
        "> **This computation is written from scratch, independent of the app's**",
        "> **`backend/compute/engine.py`. Use it to verify the app's output.**",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 1. Documents processed",
        "",
    ]
    for dt, fpath in doc_files:
        match = next((e for d, fn, e in extracted if fn == fpath.name), None)
        conf = f"{match.overall_confidence:.2f}" if match else "—"
        lines.append(f"- `{dt.value}` / `{fpath.name}` (extraction confidence: {conf})")
    lines.append("")

    lines += ["## 2. Raw extracted numbers (merged)", ""]
    for k, v in sorted(raw.items()):
        if v not in (None, "", 0, 0.0, False):
            lines.append(f"- `{k}`: {_inr(float(v)) if isinstance(v, (int, float)) else v}")
    lines.append("")

    for regime in ("old", "new"):
        r = gt[regime]
        label = "Old Regime" if regime == "old" else "New Regime"
        sec = "3a" if regime == "old" else "3b"
        lines += [
            f"## {sec}. GT Tax Computation — {label}",
            "",
            "```",
            *_render_steps(r["steps"]),
            "```",
            "",
            "| Item | Amount |",
            "| --- | --- |",
            f"| Gross Total Income | {_inr(r['gross_total_income'])} |",
            f"| Total Chapter VI-A Deductions | {_inr(r['total_deductions'])} |",
            f"| Taxable Income | {_inr(r['taxable_income'])} |",
            f"| Slab Tax | {_inr(r['slab_tax'])} |",
            f"| CG Tax (special rates) | {_inr(r['cg_tax'])} |",
            f"| Tax before rebate | {_inr(r['tax_before_rebate'])} |",
            f"| Rebate u/s 87A | {_inr(r['rebate_87a'])} |",
            f"| Surcharge | {_inr(r['surcharge'])} |",
            f"| Cess (4%) | {_inr(r['cess'])} |",
            f"| **Total Tax Liability** | **{_inr(r['total_tax'])}** |",
            f"| Taxes Paid (TDS/TCS/Advance/SAT) | {_inr(r['taxes_paid'])} |",
            f"| **Tax Payable / (Refund)** | **{_inr(r['payable'])}** |",
            "",
        ]

    old = gt["old"]
    new = gt["new"]
    lines += [
        "## 4. Regime comparison",
        "",
        "| Regime | Total Tax | Payable / (Refund) |",
        "| --- | --- | --- |",
        f"| Old | {_inr(old['total_tax'])} | {_inr(old['payable'])} |",
        f"| New | {_inr(new['total_tax'])} | {_inr(new['payable'])} |",
        "",
        f"**Better regime (lower total tax):** "
        f"{'OLD' if old['total_tax'] <= new['total_tax'] else 'NEW'} "
        f"(saves {_inr(abs(old['total_tax'] - new['total_tax']))})",
        "",
        "---",
        "",
        "## 5. Tax rules used (FY 2025-26)",
        "",
        "### Old regime slabs (age < 60)",
        "| Slab | Rate |",
        "| --- | --- |",
        "| Up to ₹2,50,000 | Nil |",
        "| ₹2,50,001 – ₹5,00,000 | 5% |",
        "| ₹5,00,001 – ₹10,00,000 | 20% |",
        "| Above ₹10,00,000 | 30% |",
        "",
        "### New regime slabs (Budget 2025)",
        "| Slab | Rate |",
        "| --- | --- |",
        "| Up to ₹4,00,000 | Nil |",
        "| ₹4,00,001 – ₹8,00,000 | 5% |",
        "| ₹8,00,001 – ₹12,00,000 | 10% |",
        "| ₹12,00,001 – ₹16,00,000 | 15% |",
        "| ₹16,00,001 – ₹20,00,000 | 20% |",
        "| ₹20,00,001 – ₹24,00,000 | 25% |",
        "| Above ₹24,00,000 | 30% |",
        "",
        "- Standard deduction: Old ₹50,000 / New ₹75,000",
        "- Rebate 87A: Old — taxable ≤ ₹5L → max ₹12,500 | New — taxable ≤ ₹12L → max ₹60,000",
        "- LTCG u/s 112A: ₹1,25,000 exempt, 12.5% above",
        "- STCG u/s 111A: 20%",
        "- Cess: 4% on (tax + surcharge)",
        "- Surcharge: 10% for income > ₹50L (old/new), 15% > ₹1Cr (old), 25% > ₹2Cr (new)",
        "",
        "---",
        "_This is an independent GT. Any discrepancy with the app signals a potential bug._",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nGT report written to: {out.resolve()}")


async def main() -> None:
    p = argparse.ArgumentParser(description="Independent GT tax report.")
    p.add_argument("--docs-dir", default=str(pathlib.Path.home() / "Documents" / "itr 2026"))
    p.add_argument("--out", default="gt_report.md")
    p.add_argument("--age", type=int, default=27)
    args = p.parse_args()

    docs_dir = pathlib.Path(args.docs_dir).expanduser()
    out_path = pathlib.Path(args.out)
    passwords: dict[str, str] = json.loads(os.environ.get("DOC_PASSWORDS", "{}"))

    print(f"Docs dir : {docs_dir}")
    doc_files = _discover_docs(docs_dir)
    if not doc_files:
        print("No documents found.", file=sys.stderr); sys.exit(1)

    print(f"Found {len(doc_files)} file(s). Extracting (using LLM cache where possible)...")
    extracted = await _extract_all(doc_files, passwords)

    print("\nMerging extracted fields...")
    raw = _consolidate(extracted)

    print("Computing GT tax (independent of engine.py)...")
    gt = _compute_gt(raw, age=args.age)

    old, new = gt["old"], gt["new"]
    print(f"\n{'='*55}")
    print(f"  OLD: total tax = {_inr(old['total_tax']):<14}  payable = {_inr(old['payable'])}")
    print(f"  NEW: total tax = {_inr(new['total_tax']):<14}  payable = {_inr(new['payable'])}")
    print(f"{'='*55}")

    _write_report(out_path, doc_files, extracted, raw, gt)


if __name__ == "__main__":
    asyncio.run(main())
