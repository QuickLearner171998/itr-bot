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
import math
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
    """Round down to nearest 10 (per Income Tax Act rounding rule)."""
    return math.floor(x / 10) * 10


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
    """Marginal surcharge rate (before marginal relief)."""
    if regime == "new":
        # New regime: flat 25% for income > 5Cr; 15% for > 2Cr; 10% for > 50L
        if total_income > 50_000_000:
            return 0.25
        if total_income > 20_000_000:
            return 0.15
        if total_income > 5_000_000:
            return 0.10
        return 0.0
    else:
        # Old regime
        if total_income > 50_000_000:
            return 0.37
        if total_income > 20_000_000:
            return 0.25
        if total_income > 10_000_000:
            return 0.15
        if total_income > 5_000_000:
            return 0.10
        return 0.0


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


def _80tta(savings_interest: float) -> float:
    return min(savings_interest, 10_000)


def _compute_gt(raw: dict, age: int) -> dict:
    """Independent GT computation.

    Args:
        raw: Flat dict of extracted numbers (keys match field names from DocType specs).
        age: Filer age.

    Returns:
        Dict with full step-by-step breakdown for old and new regimes.
    """
    def g(k, default=0.0):
        v = raw.get(k, default)
        return float(v) if v is not None else default

    # --- Income ---
    gross_salary     = g("gross_salary")
    exempt_allowances = g("exempt_allowances")   # Sec 10 (employer-certified in Form 16)
    professional_tax  = g("professional_tax")
    hra_received      = g("hra_received")
    hra_rent_paid     = g("hra_rent_paid")
    hra_basic_da      = g("hra_basic_da")
    hra_is_metro      = bool(raw.get("hra_is_metro", False))

    savings_interest = g("savings_interest")
    fd_interest      = g("fd_interest")
    dividend         = g("dividend")
    other_income     = g("other_income")
    family_pension   = g("family_pension")
    let_out_rent     = g("let_out_annual_rent")
    municipal_taxes  = g("let_out_municipal_taxes")

    stcg_111a = g("stcg_111a")
    ltcg_112a = g("ltcg_112a")
    stcg_other = g("stcg_other")
    ltcg_other = g("ltcg_other")
    vda_gain   = g("vda_gain")

    # --- Deductions ---
    d_80c       = min(g("amount_80c"), 150_000)
    d_80ccd1b   = min(g("amount_80ccd1b"), 50_000)
    d_80ccd2    = g("amount_80ccd2")            # no cap (employer NPS)
    d_80d_self  = min(g("amount_80d_self"), 25_000 if age < 60 else 50_000)
    d_80d_par   = min(g("amount_80d_parents"), 25_000)   # assume parents < 60
    d_80e       = g("amount_80e")
    d_80eea     = g("amount_80eea")

    # --- Taxes paid ---
    tds_total  = g("tds_total")
    advance_tax = g("advance_tax")
    self_assess = g("self_assessment_tax")
    taxes_paid  = tds_total + advance_tax + self_assess

    results = {}

    for regime in ("old", "new"):
        steps = []

        def add(label, amount, kind="info"):
            steps.append({"label": label, "amount": amount, "kind": kind})

        # Salary
        add("Gross Salary", gross_salary, "add")

        if regime == "old":
            # Sec 10 exemptions (employer-certified)
            sec10 = exempt_allowances
            # If no employer exemption, recompute HRA from rent receipt
            hra_ex = 0.0
            if sec10 <= 0:
                hra_ex = _hra_exemption(hra_received, hra_rent_paid, hra_basic_da, hra_is_metro)
                sec10 = hra_ex
            if sec10 > 0:
                add("Less: Sec 10 Exempt Allowances (incl. HRA/LTA)", sec10, "subtract")
            std_ded = _OLD_STD_DEDUCTION
            add("Less: Standard Deduction u/s 16(ia)", std_ded, "subtract")
            add("Less: Professional Tax u/s 16(iii)", professional_tax, "subtract")
            net_salary = max(0.0, gross_salary - sec10 - std_ded - professional_tax)
        else:
            # New regime: no Sec 10 exemptions, no professional tax deduction
            std_ded = _NEW_STD_DEDUCTION
            add("Less: Standard Deduction u/s 16(ia)", std_ded, "subtract")
            net_salary = max(0.0, gross_salary - std_ded)

        add("Net Salary", net_salary, "info")

        # Other income
        if savings_interest:
            add("Savings Interest", savings_interest, "add")
        if fd_interest:
            add("FD / RD Interest", fd_interest, "add")
        if dividend:
            add("Dividend", dividend, "add")
        if family_pension:
            std_fp = min(family_pension * 0.333, 15_000)
            add("Family Pension (gross)", family_pension, "add")
            add("Less: Standard deduction on family pension", std_fp, "subtract")
            family_pension = family_pension - std_fp
        if other_income:
            add("Other income", other_income, "add")

        # House property
        hp_income = 0.0
        if let_out_rent > 0:
            nav = let_out_rent - municipal_taxes
            std_hp = nav * 0.30   # 30% standard deduction on NAV
            hp_income = nav - std_hp
            add("Let-out rent", let_out_rent, "add")
            add("Less: Municipal taxes", municipal_taxes, "subtract")
            add("Less: 30% standard deduction on house property", std_hp, "subtract")
            add("Income from House Property", hp_income, "info")

        # Slab income (excluding special-rate CG)
        slab_income = (net_salary + savings_interest + fd_interest + dividend
                       + family_pension + other_income + hp_income + stcg_other)

        # Capital gains (special rate)
        ltcg_112a_taxable = max(0.0, ltcg_112a - _LTCG_112A_EXEMPT)
        if ltcg_112a:
            add(f"LTCG u/s 112A (₹1,25,000 exempt, 12.5%)", ltcg_112a, "add")
        if stcg_111a:
            add("STCG u/s 111A (20%)", stcg_111a, "add")
        if vda_gain:
            add("Crypto/VDA gains (30%)", vda_gain, "add")

        gti = slab_income + ltcg_112a_taxable + stcg_111a + ltcg_other + vda_gain
        add("Gross Total Income", gti, "info")

        # Chapter VI-A deductions (only old regime)
        total_ded = 0.0
        if regime == "old":
            add("— Chapter VI-A Deductions —", 0, "info")
            if d_80c:
                add("80C (cap ₹1,50,000)", d_80c, "subtract"); total_ded += d_80c
            if d_80ccd1b:
                add("80CCD(1B) NPS self (cap ₹50,000)", d_80ccd1b, "subtract"); total_ded += d_80ccd1b
            if d_80ccd2:
                add("80CCD(2) Employer NPS", d_80ccd2, "subtract"); total_ded += d_80ccd2
            if d_80d_self:
                add("80D Health Insurance self/family", d_80d_self, "subtract"); total_ded += d_80d_self
            if d_80d_par:
                add("80D Health Insurance parents", d_80d_par, "subtract"); total_ded += d_80d_par
            tta = _80tta(savings_interest)
            if tta:
                add("80TTA Savings Interest (cap ₹10,000)", tta, "subtract"); total_ded += tta
            if d_80e:
                add("80E Education Loan Interest", d_80e, "subtract"); total_ded += d_80e
        else:
            # New regime: only 80CCD(2) allowed
            if d_80ccd2:
                add("80CCD(2) Employer NPS (allowed in new regime)", d_80ccd2, "subtract")
                total_ded += d_80ccd2

        taxable_income = max(0.0, gti - total_ded)
        # For slab tax computation, split off special-rate CG
        taxable_slab = max(0.0, slab_income - (total_ded if regime == "old" else d_80ccd2))
        add("Taxable Income (slab)", taxable_slab, "info")

        # Tax on slab income
        if regime == "old":
            slab_tax = _slab_tax(taxable_slab, _old_slabs(age))
        else:
            slab_tax = _slab_tax(taxable_slab, _NEW_SLABS)
        add("Tax on Slab Income", slab_tax, "info")

        # Tax on special-rate CG
        cg_tax = 0.0
        if ltcg_112a_taxable > 0:
            t = ltcg_112a_taxable * _LTCG_112A_RATE
            cg_tax += t
            add(f"  Tax on LTCG u/s 112A (12.5%)", t, "info")
        if stcg_111a > 0:
            t = stcg_111a * _STCG_111A_RATE
            cg_tax += t
            add(f"  Tax on STCG u/s 111A (20%)", t, "info")
        if ltcg_other > 0:
            t = ltcg_other * 0.20
            cg_tax += t
            add(f"  Tax on LTCG other (20%)", t, "info")
        if vda_gain > 0:
            t = vda_gain * 0.30
            cg_tax += t
            add(f"  Tax on VDA gains (30%)", t, "info")

        tax_before_rebate = slab_tax + cg_tax

        # Rebate u/s 87A
        rebate = 0.0
        if regime == "old":
            if taxable_income <= _OLD_REBATE87A_LIMIT:
                rebate = min(tax_before_rebate, _OLD_REBATE87A_AMOUNT)
        else:
            if taxable_slab <= _NEW_REBATE87A_LIMIT:
                rebate = min(slab_tax, _NEW_REBATE87A_AMOUNT)
        if rebate:
            add("Less: Rebate u/s 87A", rebate, "subtract")

        tax_after_rebate = max(0.0, tax_before_rebate - rebate)

        # Surcharge
        total_income_for_surcharge = gti - total_ded  # taxable income
        sr_rate = _surcharge_rate(total_income_for_surcharge, regime)
        surcharge = tax_after_rebate * sr_rate
        if surcharge:
            add(f"Surcharge ({sr_rate*100:.0f}%)", surcharge, "add")

        # Cess
        cess = (tax_after_rebate + surcharge) * _CESS_RATE
        add("Health & Education Cess (4%)", cess, "add")

        total_tax = _round10(tax_after_rebate + surcharge + cess)
        add("Total Tax Liability", total_tax, "info")
        add("Less: TDS / Advance Tax Paid", taxes_paid, "subtract")

        payable = total_tax - taxes_paid
        add("Tax Payable / (Refund)", payable, "info")

        results[regime] = {
            "steps": steps,
            "gross_total_income": gti,
            "taxable_income": taxable_income,
            "total_deductions": total_ded,
            "slab_tax": slab_tax,
            "cg_tax": cg_tax,
            "tax_before_rebate": tax_before_rebate,
            "rebate_87a": rebate,
            "surcharge": surcharge,
            "cess": cess,
            "total_tax": total_tax,
            "taxes_paid": taxes_paid,
            "payable": payable,
        }

    return results


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


def _merge_raw(extracted: list) -> dict:
    """Flatten extracted fields from all docs into a single raw dict.

    Fields from higher-priority docs overwrite lower-priority ones.
    Priority: Form16 > 26AS > AIS > others (for the same key).
    For multi-employer docs, salary totals are summed.
    """
    PRIORITY = {
        DocType.FORM16: 10, DocType.FORM26AS: 8, DocType.AIS: 6,
        DocType.BROKER_PNL: 5, DocType.INTEREST_CERT: 5,
        DocType.HOME_LOAN_CERT: 4, DocType.DEDUCTION_PROOF: 4,
        DocType.RENT_RECEIPT: 4, DocType.FORM16A: 3,
    }

    # Accumulate salary components across multiple Form 16s
    salary_keys = {"gross_salary", "exempt_allowances", "professional_tax", "tds", "tds_total"}
    raw: dict = {}
    seen_priority: dict[str, int] = {}
    salary_totals: dict[str, float] = {k: 0.0 for k in salary_keys}
    form16_count = 0

    for dt, fname, ext in extracted:
        pri = PRIORITY.get(dt, 3)
        fmap = {f.name: f.value for f in ext.fields if f.value not in (None, "")}

        if dt == DocType.FORM16:
            form16_count += 1
            for k in salary_keys:
                v = fmap.get(k, fmap.get("tds_deducted") if k == "tds" else None)
                if v is not None:
                    try:
                        salary_totals[k] += float(v)
                    except (TypeError, ValueError):
                        pass
            # Non-salary Form16 fields: take last seen (multiple employers)
            for k, v in fmap.items():
                if k not in salary_keys and k != "tds_deducted":
                    if pri >= seen_priority.get(k, -1):
                        raw[k] = v
                        seen_priority[k] = pri
        else:
            for k, v in fmap.items():
                if pri >= seen_priority.get(k, -1):
                    raw[k] = v
                    seen_priority[k] = pri

    # Merge summed salary totals back
    if form16_count > 0:
        raw.update(salary_totals)
        # tds_total from Form16 is sum of per-employer TDS
        raw["tds_total"] = salary_totals.get("tds_total") or salary_totals.get("tds", 0.0)

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
            f"| TDS + Advance Tax Paid | {_inr(r['taxes_paid'])} |",
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
    raw = _merge_raw(extracted)

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
