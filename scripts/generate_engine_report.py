"""Run the EXACT backend pipeline on local documents and report the result.

This mirrors what the app does in production: extract every document with the
LLM pipeline, consolidate them with ``backend/compute/consolidate.py``, then run
``backend/compute/engine.py`` for both regimes. It deliberately reuses the same
extraction step as ``generate_gt_report.py`` so a SINGLE extraction run feeds
both the backend engine and the independent ground-truth computation — making
the two directly comparable (extraction is an LLM call and is not deterministic
across separate runs).

Output sections:
  1. Documents processed
  2. Consolidated TaxInput the engine actually used
  3. Engine computation (old + new)
  4. Regime comparison
  5. Reconciliation discrepancies raised by consolidation
  6. GT vs Engine totals on the same extraction (the apples-to-apples compare)

Usage:
    uv run python scripts/generate_engine_report.py [--docs-dir ...] [--out engine_report.md] [--age 27]

Document passwords via DOC_PASSWORDS env-var (JSON), same as the GT script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))  # repo root (for `backend` imports)
sys.path.insert(0, str(_HERE))         # sibling scripts (reuse GT helpers)

import generate_gt_report as gt  # noqa: E402  reuse discovery/extraction/render helpers

from backend.compute.consolidate import consolidate_detailed  # noqa: E402
from backend.compute.engine import compare_regimes  # noqa: E402
from backend.schemas.compute import RegimeResult, TaxInput  # noqa: E402


def _ti_summary(ti: TaxInput) -> dict:
    """Flatten the consolidated ``TaxInput`` into the figures the engine used.

    Args:
        ti: Consolidated tax input produced by the backend consolidation.

    Returns:
        An ordered, human-readable mapping of every non-trivial input figure.
    """
    cg = ti.capital_gains
    d = ti.deductions
    out: dict[str, object] = {
        "filing_regime": ti.filing_regime,
        "salary_components": len(ti.salaries),
        "gross_salary (sum)": sum(s.gross_salary for s in ti.salaries),
        "exempt_allowances (sum)": sum(s.exempt_allowances for s in ti.salaries),
        "professional_tax (sum)": sum(s.professional_tax for s in ti.salaries),
        "professional_fees": ti.professional_fees,
        "savings_interest": ti.savings_interest,
        "fd_interest": ti.fd_interest,
        "interest_on_bonds": ti.interest_on_bonds,
        "dividend": ti.dividend,
        "family_pension": ti.family_pension,
        "interest_on_it_refund": ti.interest_on_it_refund,
        "let_out_annual_rent": ti.let_out_annual_rent,
        "let_out_municipal_taxes": ti.let_out_municipal_taxes,
        "home_loan_interest": d.home_loan_interest,
        "stcg_111a": cg.stcg_111a,
        "ltcg_112a": cg.ltcg_112a,
        "stcg_other": cg.stcg_other,
        "ltcg_other": cg.ltcg_other,
        "vda_gain": cg.vda_gain,
        "amount_80c": d.amount_80c,
        "amount_80ccd1b": d.amount_80ccd1b,
        "amount_80ccd2": d.amount_80ccd2,
        "amount_80d_self": d.amount_80d_self,
        "amount_80d_parents": d.amount_80d_parents,
        "amount_80e": d.amount_80e,
        "tds_total": ti.tds_total,
        "tcs_total": ti.tcs_total,
        "advance_tax": ti.advance_tax,
        "self_assessment_tax": ti.self_assessment_tax,
        "tds_on_property_purchase": ti.tds_on_property_purchase,
    }
    return out


def _engine_totals(r: RegimeResult) -> list[str]:
    """Render the totals table for one engine ``RegimeResult``."""
    return [
        "| Item | Amount |",
        "| --- | --- |",
        f"| Gross Total Income | {gt._inr(r.gross_total_income)} |",
        f"| Total Chapter VI-A Deductions | {gt._inr(r.total_deductions)} |",
        f"| Taxable Income | {gt._inr(r.total_income)} |",
        f"| Tax before rebate | {gt._inr(r.tax_before_rebate)} |",
        f"| Rebate u/s 87A | {gt._inr(r.rebate_87a)} |",
        f"| Surcharge | {gt._inr(r.surcharge)} |",
        f"| Marginal Relief | {gt._inr(r.marginal_relief)} |",
        f"| Cess (4%) | {gt._inr(r.cess)} |",
        f"| **Total Tax Liability** | **{gt._inr(r.total_tax_liability)}** |",
        f"| Taxes Paid | {gt._inr(r.taxes_paid)} |",
        f"| **Tax Payable / (Refund)** | **{gt._inr(r.refund_or_payable)}** |",
        "",
    ]


def _render_engine_steps(r: RegimeResult) -> list[str]:
    """Adapt engine ``ComputeStep`` objects to the GT step renderer."""
    steps = [{"label": s.label, "amount": s.amount, "kind": s.kind} for s in r.steps]
    return gt._render_steps(steps)


def _write_report(out, doc_files, extracted, ti, discrepancies, comparison, gt_res) -> None:
    """Write the engine report plus the GT-vs-engine comparison."""
    import time
    lines: list[str] = [
        "# Backend Engine Tax Report — FY 2025-26 / AY 2026-27",
        "",
        "> **Produced by the EXACT backend pipeline: `extract` -> `consolidate` -> `engine`.**",
        "> **Section 6 compares it to the independent GT on the SAME extraction.**",
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

    lines += ["## 2. Consolidated TaxInput (engine inputs)", ""]
    for k, v in _ti_summary(ti).items():
        if isinstance(v, (int, float)) and v == 0:
            continue
        shown = gt._inr(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        lines.append(f"- `{k}`: {shown}")
    lines.append("")

    for regime, sec in (("old", "3a"), ("new", "3b")):
        r = getattr(comparison, regime)
        label = "Old Regime" if regime == "old" else "New Regime"
        lines += [
            f"## {sec}. Engine Computation — {label}",
            "",
            "```",
            *_render_engine_steps(r),
            "```",
            "",
            *_engine_totals(r),
        ]

    lines += [
        "## 4. Regime comparison",
        "",
        "| Regime | Total Tax | Payable / (Refund) |",
        "| --- | --- | --- |",
        f"| Old | {gt._inr(comparison.old.total_tax_liability)} | {gt._inr(comparison.old.refund_or_payable)} |",
        f"| New | {gt._inr(comparison.new.total_tax_liability)} | {gt._inr(comparison.new.refund_or_payable)} |",
        "",
        f"**Engine-recommended regime:** {comparison.recommended.upper()} "
        f"(saves {gt._inr(comparison.savings)})",
        "",
        f"**Filing regime (from Form 16):** {ti.filing_regime.upper()}",
        "",
        "## 5. Reconciliation discrepancies raised by consolidation",
        "",
    ]
    if discrepancies:
        for d in discrepancies:
            srcs = ", ".join(f"{s.get('doc')}={gt._inr(float(s.get('value', 0)))}" for s in d.sources)
            lines.append(f"- **{d.label}** (chosen {gt._inr(d.chosen)}): {srcs}")
            lines.append(f"  - {d.note}")
    else:
        lines.append("- None.")
    lines.append("")

    # Section 6: side-by-side on the SAME extraction.
    lines += [
        "## 6. GT vs Engine (same extraction — apples-to-apples)",
        "",
        "| Regime | Metric | GT | Engine | Diff |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for regime in ("old", "new"):
        g = gt_res[regime]
        e = getattr(comparison, regime)
        rows = [
            ("Gross Total Income", g["gross_total_income"], e.gross_total_income),
            ("Total Deductions", g["total_deductions"], e.total_deductions),
            ("Taxable Income", g["taxable_income"], e.total_income),
            ("Total Tax Liability", g["total_tax"], e.total_tax_liability),
            ("Taxes Paid", g["taxes_paid"], e.taxes_paid),
            ("Payable / (Refund)", g["payable"], e.refund_or_payable),
        ]
        for metric, gv, ev in rows:
            diff = gv - ev
            mark = "" if abs(diff) < 1 else "  ⚠"
            lines.append(f"| {regime.upper()} | {metric} | {gt._inr(gv)} | {gt._inr(ev)} | {gt._inr(diff)}{mark} |")
    lines += [
        "",
        "---",
        "_Engine = backend source of truth. GT = independent reimplementation. "
        "Any ⚠ row is a genuine GT-vs-engine divergence on identical inputs._",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nEngine report written to: {out.resolve()}")


async def main() -> None:
    p = argparse.ArgumentParser(description="Backend-engine tax report (exact pipeline).")
    p.add_argument("--docs-dir", default=str(pathlib.Path.home() / "Documents" / "itr 2026"))
    p.add_argument("--out", default="engine_report.md")
    p.add_argument("--age", type=int, default=27)
    args = p.parse_args()

    gt._load_dotenv()
    docs_dir = pathlib.Path(args.docs_dir).expanduser()
    out_path = pathlib.Path(args.out)
    passwords: dict[str, str] = json.loads(os.environ.get("DOC_PASSWORDS", "{}"))

    print(f"Docs dir : {docs_dir}")
    doc_files = gt._discover_docs(docs_dir)
    if not doc_files:
        print("No documents found.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(doc_files)} file(s). Extracting...")
    extracted = await gt._extract_all(doc_files, passwords)

    # Backend pipeline (exact).
    docs = [ext for _dt, _fn, ext in extracted]
    ti, discrepancies = consolidate_detailed(docs, age=args.age)
    comparison = compare_regimes(ti)

    # Independent GT on the SAME extraction.
    raw = gt._consolidate(extracted)
    gt_res = gt._compute_gt(raw, age=args.age)

    print(f"\n{'='*60}")
    print(f"  ENGINE  OLD tax={comparison.old.total_tax_liability:>12,.0f}  "
          f"NEW tax={comparison.new.total_tax_liability:>12,.0f}")
    print(f"  GT      OLD tax={gt_res['old']['total_tax']:>12,.0f}  "
          f"NEW tax={gt_res['new']['total_tax']:>12,.0f}")
    print(f"{'='*60}")

    _write_report(out_path, doc_files, extracted, ti, discrepancies, comparison, gt_res)


if __name__ == "__main__":
    asyncio.run(main())
