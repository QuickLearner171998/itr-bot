"""Generate a detailed ground-truth tax computation report from local documents.

Usage:
    uv run python scripts/generate_gt_report.py [--docs-dir "~/Documents/itr 2026"] [--out gt_report.md]

Extracts every document in the given folder tree, consolidates them into a
``TaxInput``, runs the tax engine for both regimes, and writes a Markdown
report suitable for manual verification.

Folder layout expected (sub-folders named after DocType values):
    form16/   -> Form 16 PDFs (one per employer)
    26as/     -> Form 26AS PDF or image
    ais/      -> AIS PDF
    broker_pnl/ -> Broker P&L PDFs or Excel/CSV
    interest_cert/ -> Bank interest certificates
    <any other DocType>/ -> matched automatically

Document passwords can be supplied via DOC_PASSWORDS env-var as JSON:
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

from backend.agents.doc_intel.extract import extract_document
from backend.compute.consolidate import consolidate_detailed
from backend.compute.engine import compute_regime
from backend.schemas.documents import DOC_REGISTRY, DocType
from backend.app.event_bus import bus

# Silence SSE bus during CLI run
_SESSION = "gt_report"


def _inr(v: float) -> str:
    """Format a float as Indian-locale rupee string."""
    return f"₹{v:,.0f}"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate GT tax report from local docs.")
    p.add_argument(
        "--docs-dir", default=str(pathlib.Path.home() / "Documents" / "itr 2026"),
        help="Root folder with sub-folders named after DocType values.")
    p.add_argument("--out", default="gt_report.md", help="Output Markdown file.")
    p.add_argument("--age", type=int, default=27, help="Filer age (for senior citizen rules).")
    return p.parse_args()


def _discover_docs(root: pathlib.Path) -> list[tuple[DocType, pathlib.Path]]:
    """Walk the root folder and return (DocType, file_path) pairs."""
    mapping: dict[str, DocType] = {}
    for dt in DocType:
        # match sub-folder name to DocType value; also handle aliases
        mapping[dt.value] = dt

    # common aliases
    aliases = {"26as": DocType.FORM26AS, "ais": DocType.AIS, "form16": DocType.FORM16,
                "pnl": DocType.BROKER_PNL, "broker": DocType.BROKER_PNL,
                "interest": DocType.INTEREST_CERT}
    mapping.update(aliases)

    found: list[tuple[DocType, pathlib.Path]] = []
    if not root.exists():
        print(f"[warn] docs-dir not found: {root}", file=sys.stderr)
        return found

    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        dt = mapping.get(sub.name.lower())
        if dt is None:
            print(f"[skip] unknown folder '{sub.name}' (not a DocType)", file=sys.stderr)
            continue
        for f in sorted(sub.iterdir()):
            if f.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".xlsx", ".xls", ".csv"):
                found.append((dt, f))
    return found


async def _extract_all(
    doc_files: list[tuple[DocType, pathlib.Path]],
    passwords: dict[str, str],
) -> list:
    """Extract all documents concurrently and return DocumentExtraction objects."""
    MIME = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
    }

    tasks = []
    for dt, fpath in doc_files:
        data = fpath.read_bytes()
        mime = MIME.get(fpath.suffix.lower(), "application/pdf")
        pw = passwords.get(dt.value) or passwords.get(fpath.stem.lower())
        tasks.append(extract_document(
            session_id=_SESSION,
            doc_type=dt,
            filename=fpath.name,
            data=data,
            mime=mime,
            password=pw,
            upload_id=fpath.stem,
        ))

    results = []
    for i, coro in enumerate(tasks):
        dt, fpath = doc_files[i]
        print(f"  Extracting [{i+1}/{len(tasks)}] {dt.value}: {fpath.name} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            ext = await coro
            elapsed = time.time() - t0
            conf = ext.overall_confidence
            print(f"ok ({elapsed:.1f}s, conf={conf:.2f})")
            results.append(ext)
        except Exception as exc:
            print(f"FAILED: {exc}")
    return results


def _render_steps(steps) -> str:
    lines = []
    for s in steps:
        sign = "+" if s.kind == "add" else ("-" if s.kind == "subtract" else " ")
        lines.append(f"  {sign} {s.label:<48} {_inr(s.amount):>14}")
    return "\n".join(lines)


def _write_report(
    out_path: pathlib.Path,
    doc_files: list[tuple[DocType, pathlib.Path]],
    extractions: list,
    ti,
    discrepancies: list,
    old_result,
    new_result,
) -> None:
    """Write the full Markdown GT report."""
    lines: list[str] = []

    lines += [
        "# ITR Ground-Truth Tax Report",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "## 1. Documents processed",
        "",
    ]
    for dt, fpath in doc_files:
        lines.append(f"- `{dt.value}` → `{fpath.name}`")
    lines.append("")

    lines += [
        "## 2. Extraction summary",
        "",
        "| Doc | Fields extracted | Overall confidence |",
        "| --- | --- | --- |",
    ]
    for ext in extractions:
        filled = sum(1 for f in ext.fields if f.value not in (None, "", 0))
        lines.append(
            f"| {ext.doc_type} | {filled}/{len(ext.fields)} | {ext.overall_confidence:.2f} |"
        )
    lines.append("")

    lines += [
        "## 3. Consolidated TaxInput",
        "",
        "### Salaries",
        "",
    ]
    for i, s in enumerate(ti.salaries):
        lines.append(f"**Employer {i+1}: {s.employer_name or 'Unknown'}**")
        lines.append(f"- Gross salary:        {_inr(s.gross_salary)}")
        lines.append(f"- Exempt allowances:   {_inr(s.exempt_allowances)}")
        lines.append(f"- Professional tax:    {_inr(s.professional_tax)}")
        lines.append(f"- TDS deducted:        {_inr(s.tds)}")
        lines.append("")

    lines += [
        "### Other income",
        f"- Savings interest: {_inr(ti.savings_interest)}",
        f"- FD/RD interest:   {_inr(ti.fd_interest)}",
        f"- Dividend:         {_inr(ti.dividend)}",
        f"- Other income:     {_inr(ti.other_income)}",
        f"- Family pension:   {_inr(ti.family_pension)}",
        "",
        "### Capital gains",
        f"- STCG 111A (equity):  {_inr(ti.capital_gains.stcg_111a)}",
        f"- LTCG 112A (equity):  {_inr(ti.capital_gains.ltcg_112a)}",
        f"- STCG other:          {_inr(ti.capital_gains.stcg_other)}",
        f"- LTCG other:          {_inr(ti.capital_gains.ltcg_other)}",
        f"- Crypto/VDA:          {_inr(ti.capital_gains.vda_gain)}",
        "",
        "### Deductions (old regime)",
        f"- 80C:            {_inr(ti.deductions.amount_80c)}",
        f"- 80CCD(1B):      {_inr(ti.deductions.amount_80ccd1b)}",
        f"- 80CCD(2):       {_inr(ti.deductions.amount_80ccd2)}",
        f"- 80D self:       {_inr(ti.deductions.amount_80d_self)}",
        f"- 80D parents:    {_inr(ti.deductions.amount_80d_parents)}",
        f"- Home loan int:  {_inr(ti.deductions.home_loan_interest)}",
        f"- 80E:            {_inr(ti.deductions.amount_80e)}",
        "",
        "### Taxes paid",
        f"- TDS total:          {_inr(ti.tds_total)}",
        f"- Advance tax:        {_inr(ti.advance_tax)}",
        f"- Self-assessment:    {_inr(ti.self_assessment_tax)}",
        "",
        f"**Filing regime (from Form 16):** {ti.filing_regime.upper()}",
        "",
    ]

    if discrepancies:
        lines += ["## 4. Cross-source discrepancies", ""]
        for d in discrepancies:
            src_str = ", ".join(f"{s['doc']}: {_inr(s['value'])}" for s in d.sources)
            lines.append(f"- **{d.label}**: {src_str}")
            if d.note:
                lines.append(f"  > {d.note}")
        lines.append("")
    else:
        lines += ["## 4. Cross-source discrepancies", "", "None detected.", ""]

    # --- Tax computation ---
    for label, result in [("Old regime", old_result), ("New regime", new_result)]:
        lines += [
            f"## 5{'a' if label.startswith('Old') else 'b'}. Tax computation — {label}",
            "",
            "```",
            _render_steps(result.steps),
            "```",
            "",
            f"| | |",
            f"| --- | --- |",
            f"| **Gross total income** | {_inr(result.gross_total_income)} |",
            f"| **Total tax liability** | {_inr(result.tax_before_cess)} |",
            f"| Surcharge | {_inr(result.surcharge)} |",
            f"| Cess (4%) | {_inr(result.cess)} |",
            f"| **Total tax + cess** | {_inr(result.total_tax)} |",
            f"| TDS / advance tax paid | {_inr(result.taxes_paid)} |",
            f"| **Tax payable / (refund)** | {_inr(result.tax_payable)} |",
            "",
        ]

    # Summary comparison
    lines += [
        "## 6. Regime comparison summary",
        "",
        f"| Regime | Total tax | Tax payable / (refund) |",
        f"| --- | --- | --- |",
        f"| Old | {_inr(old_result.total_tax)} | {_inr(old_result.tax_payable)} |",
        f"| New | {_inr(new_result.total_tax)} | {_inr(new_result.tax_payable)} |",
        "",
        f"**Recommended regime (lower tax):** "
        f"{'OLD' if old_result.total_tax <= new_result.total_tax else 'NEW'}",
        f"**Form 16 regime elected:** {ti.filing_regime.upper()}",
        "",
        "---",
        "_This report is auto-generated from extracted documents and serves as a_",
        "_ground-truth baseline. Verify all figures against original documents._",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to: {out_path.resolve()}")


async def main() -> None:
    args = _parse_args()
    docs_dir = pathlib.Path(args.docs_dir).expanduser()
    out_path = pathlib.Path(args.out)
    passwords: dict[str, str] = json.loads(os.environ.get("DOC_PASSWORDS", "{}"))

    print(f"Scanning: {docs_dir}")
    doc_files = _discover_docs(docs_dir)
    if not doc_files:
        print("No documents found. Check --docs-dir.", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(doc_files)} file(s). Extracting...")
    extractions = await _extract_all(doc_files, passwords)

    print("\nConsolidating...")
    ti, discrepancies = consolidate_detailed(extractions, age=args.age)

    print("Computing tax...")
    old_result = compute_regime(ti, "old")
    new_result = compute_regime(ti, "new")

    print(f"\n{'='*55}")
    print(f"  OLD regime: tax={_inr(old_result.total_tax)}  payable={_inr(old_result.tax_payable)}")
    print(f"  NEW regime: tax={_inr(new_result.total_tax)}  payable={_inr(new_result.tax_payable)}")
    print(f"  Form 16 elected: {ti.filing_regime.upper()}")
    print(f"{'='*55}\n")

    _write_report(out_path, doc_files, extractions, ti, discrepancies, old_result, new_result)


if __name__ == "__main__":
    asyncio.run(main())
