"""Validation layers: intra-document arithmetic and final-return rule checks.

These functions never mutate data; they only return issues so the UI can show
warnings/errors and (for errors) block progression.
"""

from __future__ import annotations

from ..schemas.compute import TaxInput
from ..schemas.documents import DocType, DocumentExtraction, ValidationIssue
from ..schemas.profile import ITRForm, UserProfile

TOLERANCE = 10.0  # rupee tolerance for arithmetic/reconciliation checks


def _num(value: object) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").replace("\u20b9", "").strip()
    return float(cleaned) if cleaned.replace(".", "", 1).lstrip("-").isdigit() else 0.0


def validate_document(doc: DocumentExtraction) -> list[ValidationIssue]:
    """Run intra-document arithmetic/consistency checks for one document.

    Args:
        doc: The extracted document.

    Returns:
        A list of validation issues (possibly empty).
    """
    issues: list[ValidationIssue] = []
    v = doc.values()

    if doc.doc_type == DocType.FORM16:
        gross = _num(v.get("gross_salary"))
        if gross <= 0:
            issues.append(ValidationIssue(
                severity="error", message="Form 16 gross salary missing or zero.",
                fields=["gross_salary"]))
        tds = _num(v.get("tds"))
        if tds < 0:
            issues.append(ValidationIssue(
                severity="error", message="TDS cannot be negative.", fields=["tds"]))
        std = _num(v.get("standard_deduction"))
        if std and std not in (50000.0, 75000.0):
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Standard deduction {std:.0f} is not 50,000 (old) or 75,000 (new).",
                fields=["standard_deduction"]))
        d80d = _num(v.get("deduction_80d"))
        if d80d > 50000:
            issues.append(ValidationIssue(
                severity="warning",
                message=(
                    f"80D deduction {d80d:,.0f} exceeds the maximum cap (₹50,000 for senior citizens, "
                    f"₹25,000 otherwise). This may be a misread of the 80C row in Part B — "
                    f"please verify and correct if needed."
                ),
                fields=["deduction_80d"]))

    if doc.doc_type == DocType.BROKER_PNL:
        for f in ("stcg_111a", "ltcg_112a", "stcg_other", "ltcg_other", "vda_gain"):
            if _num(v.get(f)) and abs(_num(v.get(f))) > 1e9:
                issues.append(ValidationIssue(
                    severity="warning", message=f"{f} looks implausibly large.", fields=[f]))

    # Low-confidence fields always warrant review.
    for field in doc.fields:
        if field.value not in (None, "") and field.confidence < 0.6:
            issues.append(ValidationIssue(
                severity="warning",
                message=f"Low confidence on '{field.label}' - please verify.",
                fields=[field.name]))
    return issues


def reconcile(docs: list[DocumentExtraction]) -> list[ValidationIssue]:
    """Cross-document reconciliation (Form 16 vs 26AS, AIS vs broker).

    Args:
        docs: All extracted documents.

    Returns:
        Mismatch issues with explanatory messages.
    """
    issues: list[ValidationIssue] = []
    by_type: dict[DocType, list[dict[str, object]]] = {}
    for doc in docs:
        by_type.setdefault(doc.doc_type, []).append(doc.values())

    # Salary TDS: Form 16 total vs 26AS salary TDS.
    f16_tds = sum(_num(v.get("tds")) for v in by_type.get(DocType.FORM16, []))
    f26 = by_type.get(DocType.FORM26AS, [{}])[0] if by_type.get(DocType.FORM26AS) else {}
    tds_26as = _num(f26.get("total_tds_salary"))
    if f16_tds and tds_26as and abs(f16_tds - tds_26as) > TOLERANCE:
        issues.append(ValidationIssue(
            severity="warning",
            message=(f"Salary TDS mismatch: Form 16 shows {f16_tds:.0f} but "
                     f"Form 26AS shows {tds_26as:.0f}."),
            fields=["tds"]))

    # Dividend: AIS vs broker.
    ais = by_type.get(DocType.AIS, [{}])[0] if by_type.get(DocType.AIS) else {}
    ais_div = _num(ais.get("dividend"))
    broker_div = sum(_num(v.get("dividend")) for v in by_type.get(DocType.BROKER_PNL, []))
    if ais_div and broker_div and abs(ais_div - broker_div) > TOLERANCE:
        issues.append(ValidationIssue(
            severity="warning",
            message=(f"Dividend mismatch: AIS reports {ais_div:.0f} but broker(s) "
                     f"report {broker_div:.0f}. Some dividends may be credited directly to bank."),
            fields=["dividend"]))

    # Salary: Form 16 gross vs AIS salary.
    f16_gross = sum(_num(v.get("gross_salary")) for v in by_type.get(DocType.FORM16, []))
    ais_salary = _num(ais.get("salary_reported"))
    if f16_gross and ais_salary and abs(f16_gross - ais_salary) > max(TOLERANCE, 0.02 * f16_gross):
        issues.append(ValidationIssue(
            severity="warning",
            message=(f"Salary mismatch: Form 16 total {f16_gross:.0f} vs AIS {ais_salary:.0f}."),
            fields=["gross_salary"]))
    return issues


def validate_final_return(ti: TaxInput, form: ITRForm, profile: UserProfile) -> list[ValidationIssue]:
    """Final-return rule validation against ITR-1/ITR-2 eligibility constraints.

    Args:
        ti: Consolidated tax input.
        form: The selected ITR form.
        profile: The user profile.

    Returns:
        Issues; an "error" indicates the chosen form is invalid for the data.
    """
    issues: list[ValidationIssue] = []
    cg = ti.capital_gains
    total_income = (
        sum(s.gross_salary for s in ti.salaries)
        + ti.savings_interest + ti.fd_interest + ti.dividend + ti.other_income
        + ti.family_pension + ti.professional_fees
        + ti.house_property_income + ti.let_out_annual_rent
        + cg.stcg_111a + cg.ltcg_112a + cg.stcg_other + cg.ltcg_other + cg.vda_gain
    )

    # Professional / freelance income requires ITR-2 or ITR-3 (not ITR-1).
    if ti.professional_fees > 0:
        issues.append(ValidationIssue(
            severity="error" if form == ITRForm.ITR1 else "warning",
            message=(
                f"Professional / freelance income ₹{ti.professional_fees:,.0f} detected "
                f"(Sec 194J from AIS). ITR-1 cannot be used — file ITR-2 or ITR-3. "
                f"This income must be declared under 'Profits & Gains from Business/Profession'."
            ),
            fields=["professional_fees"]))

    if form == ITRForm.ITR1:
        if total_income > 5000000:
            issues.append(ValidationIssue(
                severity="error",
                message="Total income exceeds 50 lakh - ITR-1 not allowed, use ITR-2."))
        if cg.stcg_111a or cg.stcg_other or cg.ltcg_other or cg.vda_gain:
            issues.append(ValidationIssue(
                severity="error",
                message="STCG / non-112A gains present - ITR-1 not allowed, use ITR-2."))
        if cg.ltcg_112a > 125000:
            issues.append(ValidationIssue(
                severity="error",
                message="LTCG u/s 112A exceeds 1.25 lakh - ITR-1 not allowed, use ITR-2."))

    if cg.vda_gain and not profile.has_crypto_vda:
        issues.append(ValidationIssue(
            severity="warning",
            message="Crypto/VDA gains detected though not declared in questionnaire."))
    return issues
