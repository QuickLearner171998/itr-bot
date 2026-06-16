"""Merge validated document extractions into a single canonical ``TaxInput``.

Where multiple documents report the same figure (e.g. savings interest in both
the interest certificate and AIS), the larger reported value is taken so that
nothing is under-reported; the reconciliation layer separately surfaces any
mismatch for the user to confirm.
"""

from __future__ import annotations

from ..schemas.compute import (
    CapitalGains,
    Deductions,
    Discrepancy,
    SalaryComponent,
    TaxInput,
)
from ..schemas.documents import DOC_REGISTRY, DocType, DocumentExtraction

# Two source figures differing by more than this (rupees) raise a discrepancy.
_DISCREPANCY_TOLERANCE = 1.0


def _num(value: object) -> float:
    """Coerce an extracted value to a float, treating blanks/None as 0."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("\u20b9", "").strip()
    return float(cleaned) if cleaned.replace(".", "", 1).lstrip("-").isdigit() else 0.0


def _yes(value: object) -> bool:
    """Interpret an extracted text flag as a boolean ('yes'/'y'/'true')."""
    return str(value or "").strip().lower().startswith(("y", "t"))


def _doc_title(doc_type: DocType) -> str:
    """Human-readable title for a doc type (falls back to the enum value)."""
    spec = DOC_REGISTRY.get(doc_type)
    return spec.title if spec else doc_type.value


def _pick(
    discrepancies: list[Discrepancy], field: str, label: str,
    candidates: list[tuple[DocType, float]],
) -> float:
    """Choose the safe (largest) figure and record a discrepancy if sources differ.

    Args:
        discrepancies: Accumulator the discrepancy is appended to when found.
        field: Logical ``TaxInput`` key.
        label: Human-readable field name.
        candidates: ``(doc_type, value)`` pairs from each reporting source.

    Returns:
        The chosen (largest) value; 0.0 when no source reports it.
    """
    reported = [(dt, v) for dt, v in candidates if v > 0]
    if not reported:
        return 0.0
    chosen = max(v for _, v in reported)
    spread = chosen - min(v for _, v in reported)
    if len(reported) > 1 and spread > _DISCREPANCY_TOLERANCE:
        discrepancies.append(Discrepancy(
            field=field, label=label, chosen=chosen,
            sources=[{"doc": _doc_title(dt), "value": v} for dt, v in reported],
            note="Sources disagree; confirm or override the value."))
    return chosen


def consolidate(docs: list[DocumentExtraction], age: int = 30) -> TaxInput:
    """Build a consolidated ``TaxInput`` (see :func:`consolidate_detailed`)."""
    ti, _ = consolidate_detailed(docs, age)
    return ti


def consolidate_detailed(
    docs: list[DocumentExtraction], age: int = 30
) -> tuple[TaxInput, list[Discrepancy]]:
    """Build a ``TaxInput`` and a list of cross-source discrepancies.

    Where multiple documents report the same figure, the larger value is chosen
    as a safe prefill (nothing under-reported) but any disagreement is recorded
    as a :class:`Discrepancy` so the user can confirm or override it rather than
    the system silently assuming.

    Args:
        docs: Validated document extractions.
        age: Taxpayer age (drives senior-citizen rules downstream).

    Returns:
        ``(tax_input, discrepancies)``.
    """
    discrepancies: list[Discrepancy] = []
    by_type: dict[DocType, list[dict[str, object]]] = {}
    for doc in docs:
        by_type.setdefault(doc.doc_type, []).append(doc.values())

    # Pre-extract single-instance docs used in multiple places.
    ais: dict = by_type.get(DocType.AIS, [{}])[0] if by_type.get(DocType.AIS) else {}
    f26: dict = by_type.get(DocType.FORM26AS, [{}])[0] if by_type.get(DocType.FORM26AS) else {}

    ti = TaxInput(age=age)

    # Filing regime: taken from Form 16 (employer's TDS regime). If any Form 16
    # states "old", use old; otherwise the statutory default (new).
    regimes = [str(v.get("regime") or "").strip().lower()
               for v in by_type.get(DocType.FORM16, [])]
    ti.filing_regime = "old" if any(r.startswith("o") for r in regimes) else "new"

    # Salary: one component per Form 16.
    f16_80c = f16_80ccd1b = f16_80ccd2 = f16_80d = 0.0
    f16_gross_total = 0.0
    f16_tds_total = 0.0
    for v in by_type.get(DocType.FORM16, []):
        gross = _num(v.get("gross_salary"))
        tds = _num(v.get("tds"))
        f16_gross_total += gross
        f16_tds_total += tds
        ti.salaries.append(SalaryComponent(
            employer_name=str(v.get("employer_name") or "Employer"),
            period_from=str(v.get("period_from") or ""),
            period_to=str(v.get("period_to") or ""),
            gross_salary=gross,
            salary_17_1=_num(v.get("salary_17_1")),
            perquisites_17_2=_num(v.get("perquisites_17_2")),
            profits_in_lieu_17_3=_num(v.get("profits_in_lieu_17_3")),
            exempt_allowances=_num(v.get("exempt_allowances")),
            professional_tax=_num(v.get("professional_tax")),
            taxable_salary=_num(v.get("taxable_salary")),
            tds=tds,
        ))
        # Hard caps on each deduction: the statutory maximum acts as a guard
        # against the model reading the wrong row in a Chapter VI-A table.
        f16_80c = max(f16_80c, min(_num(v.get("deduction_80c")), 150000.0))      # Sec 80C cap
        f16_80ccd1b = max(f16_80ccd1b, min(_num(v.get("deduction_80ccd1b")), 50000.0))  # Sec 80CCD(1B) cap
        f16_80ccd2 = max(f16_80ccd2, _num(v.get("deduction_80ccd2")))            # no fixed cap; engine applies %
        # 80D capped at ₹50,000 (senior citizen max) to guard against model
        # misreading the 80C row (₹1,50,000) as the 80D row.
        f16_80d = max(f16_80d, min(_num(v.get("deduction_80d")), 50000.0))

        # Implausibility guard: TDS cannot exceed gross salary.
        if tds > gross * 1.05:
            discrepancies.append(Discrepancy(
                field="tds", label="TDS Implausibly High vs Gross Salary",
                sources=[{"doc": str(v.get("employer_name") or "Form 16"), "value": tds}],
                chosen=tds,
                note=(f"Extracted TDS ({tds:,.0f}) exceeds gross salary ({gross:,.0f}). "
                      "Please verify the TDS figure — it may have been read from the wrong line.")))

    # Deduction proofs (override if larger).
    proof_80c = proof_80ccd1b = proof_80d_self = proof_80d_parents = 0.0
    proof_80e = proof_80eea = proof_80dd = proof_80ddb = proof_80u = proof_80gg = 0.0
    dd_severe = u_severe = False
    for v in by_type.get(DocType.DEDUCTION_PROOF, []):
        proof_80c = max(proof_80c, _num(v.get("amount_80c")))
        proof_80ccd1b = max(proof_80ccd1b, _num(v.get("amount_80ccd1b")))
        proof_80d_self = max(proof_80d_self, _num(v.get("amount_80d_self")))
        proof_80d_parents = max(proof_80d_parents, _num(v.get("amount_80d_parents")))
        proof_80e += _num(v.get("amount_80e"))
        proof_80eea = max(proof_80eea, _num(v.get("amount_80eea")))
        proof_80dd = max(proof_80dd, _num(v.get("amount_80dd")))
        proof_80ddb = max(proof_80ddb, _num(v.get("amount_80ddb")))
        proof_80u = max(proof_80u, _num(v.get("amount_80u")))
        proof_80gg += _num(v.get("amount_80gg"))
        dd_severe = dd_severe or _yes(v.get("amount_80dd_severe"))
        u_severe = u_severe or _yes(v.get("amount_80u_severe"))

    # 80G donations (sum across receipts).
    don_100_nl = don_50_nl = don_100_l = don_50_l = 0.0
    for v in by_type.get(DocType.DONATION_80G, []):
        don_100_nl += _num(v.get("donation_100_no_limit"))
        don_50_nl += _num(v.get("donation_50_no_limit"))
        don_100_l += _num(v.get("donation_100_limit"))
        don_50_l += _num(v.get("donation_50_limit"))

    # Home loan + let-out house property.
    home_interest = 0.0
    home_principal = 0.0
    self_occupied = True
    let_out_rent = 0.0
    municipal_taxes = 0.0
    for v in by_type.get(DocType.HOME_LOAN_CERT, []):
        home_interest += _num(v.get("interest_paid"))
        home_principal += _num(v.get("principal_repaid"))
        self_occupied = _yes(v.get("is_self_occupied")) if v.get("is_self_occupied") else self_occupied
        let_out_rent += _num(v.get("let_out_annual_rent"))
        municipal_taxes += _num(v.get("municipal_taxes"))
    if let_out_rent > 0:
        self_occupied = False
        ti.let_out_annual_rent = let_out_rent
        ti.let_out_municipal_taxes = municipal_taxes

    # HRA exemption inputs (rent receipt). Section 10(13A) exemption must be
    # counted exactly once. If the employer already granted Section 10
    # exemptions in Form 16 (``exempt_allowances`` > 0), HRA is treated as
    # already netted there and we do NOT recompute it from the rent receipt --
    # otherwise the exemption would be deducted twice and tax under-stated. HRA
    # is recomputed from the receipt only when the employer granted nothing
    # (i.e. the filer is claiming it for the first time at filing).
    employer_exempt = sum(s.exempt_allowances for s in ti.salaries)
    if employer_exempt <= 0:
        for v in by_type.get(DocType.RENT_RECEIPT, []):
            ti.hra_received = max(ti.hra_received, _num(v.get("hra_received")))
            ti.hra_rent_paid = max(ti.hra_rent_paid, _num(v.get("rent_paid")))
            ti.hra_basic_da = max(ti.hra_basic_da, _num(v.get("basic_da")))
            ti.hra_is_metro = ti.hra_is_metro or _yes(v.get("is_metro"))

    ti.deductions = Deductions(
        amount_80c=max(f16_80c, proof_80c, home_principal),
        amount_80ccd1b=max(f16_80ccd1b, proof_80ccd1b),
        amount_80ccd2=f16_80ccd2,
        amount_80d_self=max(f16_80d, proof_80d_self),
        amount_80d_parents=proof_80d_parents,
        home_loan_interest=home_interest,
        home_loan_self_occupied=self_occupied,
        home_loan_principal=0.0,  # folded into amount_80c to avoid double counting
        amount_80e=proof_80e,
        amount_80eea=proof_80eea,
        amount_80dd=proof_80dd,
        amount_80dd_severe=dd_severe,
        amount_80ddb=proof_80ddb,
        amount_80u=proof_80u,
        amount_80u_severe=u_severe,
        amount_80gg=proof_80gg,
        donation_100_no_limit=don_100_nl,
        donation_50_no_limit=don_50_nl,
        donation_100_limit=don_100_l,
        donation_50_limit=don_50_l,
    )

    # Professional / freelance income from AIS (Sec 194J receipts).
    # Not present in Form 16; requires ITR-2.
    ti.professional_fees = _num(ais.get("professional_fees"))

    # Cross-source salary check: Form 16 gross vs AIS salary_reported.
    # AIS salary is an employer-reported figure independent of Form 16 — a
    # mismatch can mean mis-matched PANs, unreported employer, or data-entry error.
    # When AIS reports more salary than Form 16, the surplus is an unaccounted
    # employer's income. Add it as a synthetic salary component so it is taxed.
    ais_salary = _num(ais.get("salary_reported"))
    if f16_gross_total > 0 and ais_salary > 0:
        surplus = ais_salary - f16_gross_total
        if surplus > 1000:
            # AIS reports more salary than the uploaded Form 16(s). Auto-add the
            # gap as income so nothing is under-reported, and attach a clear
            # explanation of why the gap exists and what to re-check.
            ti.salaries.append(SalaryComponent(
                employer_name="[AIS: Additional salary not in Form 16]",
                gross_salary=surplus,
                taxable_salary=surplus,
            ))
            discrepancies.append(Discrepancy(
                field="salary_gross",
                label="Salary: AIS higher than Form 16",
                sources=[
                    {"doc": "Form 16 (total)", "value": f16_gross_total},
                    {"doc": "AIS (salary reported)", "value": ais_salary},
                ],
                chosen=ais_salary,
                note=(
                    f"AIS reports ₹{ais_salary:,.0f} of salary but your Form 16(s) total "
                    f"₹{f16_gross_total:,.0f} — a gap of ₹{surplus:,.0f}. This usually means a "
                    "previous employer's salary, arrears, or perquisites are missing from the "
                    "uploaded Form 16. The gap has been added as income so your tax is not "
                    "under-reported. To verify: (1) upload any missing employer's Form 16; "
                    "(2) in AIS, check every entry under 'Salary (Section 192)'; "
                    "(3) confirm the PAN and period match. Edit this figure on the review "
                    "screen if the AIS amount is wrong.")))
        elif surplus < -1000:
            # Form 16 reports more than AIS — flag for confirmation too.
            _pick(discrepancies, "salary_gross", "Gross Salary (Form 16 vs AIS)", [
                (DocType.FORM16, f16_gross_total),
                (DocType.AIS, ais_salary)])

    # Cross-source salary TDS check: Form 16 TDS sum vs 26AS salary TDS.
    # These should match to the rupee; a mismatch means an employer failed to
    # deposit or the filer is using a wrong Form 16.
    tds_26as_salary = _num(f26.get("total_tds_salary")) if f26 else 0.0
    if f16_tds_total > 0 and tds_26as_salary > 0:
        _pick(discrepancies, "tds_salary", "Salary TDS (Form 16 vs 26AS)", [
            (DocType.FORM16, f16_tds_total),
            (DocType.FORM26AS, tds_26as_salary)])

    # Other-source income: prefer the larger of certificate vs AIS, flag mismatch.
    cert_savings = cert_fd = 0.0
    for v in by_type.get(DocType.INTEREST_CERT, []):
        cert_savings += _num(v.get("savings_interest"))
        cert_fd += _num(v.get("fd_interest"))
    ti.savings_interest = _pick(discrepancies, "savings_interest", "Savings Interest", [
        (DocType.INTEREST_CERT, cert_savings),
        (DocType.AIS, _num(ais.get("savings_interest")))])
    ti.fd_interest = _pick(discrepancies, "fd_interest", "FD/RD Interest", [
        (DocType.INTEREST_CERT, cert_fd),
        (DocType.AIS, _num(ais.get("fd_interest")))])
    ti.interest_on_bonds = _num(ais.get("interest_on_bonds"))

    broker_dividend = sum(_num(v.get("dividend")) for v in by_type.get(DocType.BROKER_PNL, []))
    ti.dividend = _pick(discrepancies, "dividend", "Dividend", [
        (DocType.BROKER_PNL, broker_dividend),
        (DocType.AIS, _num(ais.get("dividend")))])
    ti.family_pension = _num(ais.get("family_pension"))
    ti.interest_on_it_refund = _num(ais.get("interest_on_it_refund"))

    # Rent received from AIS (SFT reporting): if present and no home-loan doc
    # provided, surface it so the filer discloses let-out income.
    ais_rent = _num(ais.get("rent_received"))
    if ais_rent > 0 and ti.let_out_annual_rent == 0:
        ti.let_out_annual_rent = ais_rent

    # VDA from AIS (194S TDS signals crypto income even without broker P&L).
    ais_vda_tds = _num(ais.get("vda_tds"))

    # Capital gains: sum across brokers.
    cg = CapitalGains()
    for v in by_type.get(DocType.BROKER_PNL, []):
        cg.stcg_111a += _num(v.get("stcg_111a"))
        cg.ltcg_112a += _num(v.get("ltcg_112a"))
        cg.stcg_other += _num(v.get("stcg_other"))
        cg.ltcg_other += _num(v.get("ltcg_other"))
        cg.vda_gain += _num(v.get("vda_gain"))
    ti.capital_gains = cg

    # When AIS shows securities sold but no broker P&L provided, flag it so the
    # user knows capital gains need to be declared. We cannot compute the actual
    # gain (cost basis and holding period are needed), but we surface a discrepancy
    # so the review screen prompts for a broker Tax P&L upload.
    ais_sale = _num(ais.get("sale_of_securities"))
    has_broker = bool(by_type.get(DocType.BROKER_PNL))
    if ais_sale > 0 and not has_broker:
        discrepancies.append(Discrepancy(
            field="capital_gains",
            label="Capital Gains (securities sold — broker P&L missing)",
            sources=[{"doc": "Annual Information Statement (AIS)",
                      "value": ais_sale}],
            chosen=0.0,
            note=(
                f"AIS shows ₹{ais_sale:,.0f} in securities/MF sales. Upload your "
                "broker Tax P&L to compute STCG/LTCG accurately. Without it, "
                "capital gains are set to zero and your tax may be under-stated."
            )))
    if ais_vda_tds > 0 and cg.vda_gain == 0:
        discrepancies.append(Discrepancy(
            field="vda_gain",
            label="Crypto / VDA Income (TDS detected — gain amount unknown)",
            sources=[{"doc": "Annual Information Statement (AIS)",
                      "value": ais_vda_tds}],
            chosen=0.0,
            note=(
                f"AIS shows ₹{ais_vda_tds:,.0f} TDS on virtual digital assets (Sec 194S). "
                "Please enter your crypto gain amount manually for accurate tax computation."
            )))

    # Taxes paid: prefer Form 26AS aggregates, else sum of per-document TDS.
    tds_individual = (
        sum(s.tds for s in ti.salaries)
        + sum(_num(v.get("tds")) for v in by_type.get(DocType.FORM16A, []))
        + sum(_num(v.get("tds")) for v in by_type.get(DocType.INTEREST_CERT, []))
    )
    tds_26as = _num(f26.get("total_tds_salary")) + _num(f26.get("total_tds_other"))
    ti.tds_total = _pick(discrepancies, "tds_total", "Total TDS", [
        (DocType.FORM16, tds_individual),
        (DocType.FORM26AS, tds_26as)])

    # TCS: sum from 26AS and AIS (cross-check).
    tcs_26as = _num(f26.get("tcs_total"))
    tcs_ais = _num(ais.get("tcs_total"))
    ti.tcs_total = _pick(discrepancies, "tcs_total", "Total TCS", [
        (DocType.FORM26AS, tcs_26as),
        (DocType.AIS, tcs_ais)])

    # Advance tax / SAT: 26AS is authoritative; AIS used as cross-check.
    ti.advance_tax = _pick(discrepancies, "advance_tax", "Advance Tax", [
        (DocType.FORM26AS, _num(f26.get("advance_tax"))),
        (DocType.AIS, _num(ais.get("advance_tax")))])
    ti.self_assessment_tax = _pick(discrepancies, "self_assessment_tax", "Self-Assessment Tax", [
        (DocType.FORM26AS, _num(f26.get("self_assessment_tax"))),
        (DocType.AIS, _num(ais.get("self_assessment_tax")))])

    ti.tds_on_property_purchase = _num(f26.get("tds_on_property_purchase"))

    return ti, discrepancies
