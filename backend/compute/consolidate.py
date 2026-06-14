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

    ti = TaxInput(age=age)

    # Filing regime: taken from Form 16 (employer's TDS regime). If any Form 16
    # states "old", use old; otherwise the statutory default (new).
    regimes = [str(v.get("regime") or "").strip().lower()
               for v in by_type.get(DocType.FORM16, [])]
    ti.filing_regime = "old" if any(r.startswith("o") for r in regimes) else "new"

    # Salary: one component per Form 16.
    f16_80c = f16_80ccd1b = f16_80ccd2 = f16_80d = 0.0
    for v in by_type.get(DocType.FORM16, []):
        ti.salaries.append(SalaryComponent(
            employer_name=str(v.get("employer_name") or "Employer"),
            gross_salary=_num(v.get("gross_salary")),
            exempt_allowances=_num(v.get("exempt_allowances")),
            professional_tax=_num(v.get("professional_tax")),
            tds=_num(v.get("tds")),
        ))
        f16_80c = max(f16_80c, _num(v.get("deduction_80c")))
        f16_80ccd1b = max(f16_80ccd1b, _num(v.get("deduction_80ccd1b")))
        f16_80ccd2 = max(f16_80ccd2, _num(v.get("deduction_80ccd2")))
        f16_80d = max(f16_80d, _num(v.get("deduction_80d")))

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

    # Other-source income: prefer the larger of certificate vs AIS, flag mismatch.
    cert_savings = cert_fd = 0.0
    for v in by_type.get(DocType.INTEREST_CERT, []):
        cert_savings += _num(v.get("savings_interest"))
        cert_fd += _num(v.get("fd_interest"))
    ais = by_type.get(DocType.AIS, [{}])[0] if by_type.get(DocType.AIS) else {}
    ti.savings_interest = _pick(discrepancies, "savings_interest", "Savings Interest", [
        (DocType.INTEREST_CERT, cert_savings),
        (DocType.AIS, _num(ais.get("savings_interest")))])
    ti.fd_interest = _pick(discrepancies, "fd_interest", "FD/RD Interest", [
        (DocType.INTEREST_CERT, cert_fd),
        (DocType.AIS, _num(ais.get("fd_interest")))])

    broker_dividend = sum(_num(v.get("dividend")) for v in by_type.get(DocType.BROKER_PNL, []))
    ti.dividend = _pick(discrepancies, "dividend", "Dividend", [
        (DocType.BROKER_PNL, broker_dividend),
        (DocType.AIS, _num(ais.get("dividend")))])
    ti.family_pension = _num(ais.get("family_pension"))

    # Capital gains: sum across brokers.
    cg = CapitalGains()
    for v in by_type.get(DocType.BROKER_PNL, []):
        cg.stcg_111a += _num(v.get("stcg_111a"))
        cg.ltcg_112a += _num(v.get("ltcg_112a"))
        cg.stcg_other += _num(v.get("stcg_other"))
        cg.ltcg_other += _num(v.get("ltcg_other"))
        cg.vda_gain += _num(v.get("vda_gain"))
    ti.capital_gains = cg

    # Taxes paid: prefer Form 26AS aggregates, else sum of per-document TDS.
    f26 = by_type.get(DocType.FORM26AS, [{}])[0] if by_type.get(DocType.FORM26AS) else {}
    tds_individual = (
        sum(s.tds for s in ti.salaries)
        + sum(_num(v.get("tds")) for v in by_type.get(DocType.FORM16A, []))
        + sum(_num(v.get("tds")) for v in by_type.get(DocType.INTEREST_CERT, []))
    )
    tds_26as = _num(f26.get("total_tds_salary")) + _num(f26.get("total_tds_other"))
    ti.tds_total = _pick(discrepancies, "tds_total", "Total TDS", [
        (DocType.FORM16, tds_individual),
        (DocType.FORM26AS, tds_26as)])
    ti.advance_tax = _num(f26.get("advance_tax"))
    ti.self_assessment_tax = _num(f26.get("self_assessment_tax"))

    return ti, discrepancies
