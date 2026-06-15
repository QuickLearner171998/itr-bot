"""Intake: questionnaire, deterministic form selection, and document checklist.

The questionnaire is predefined and branching. Form selection and the checklist
are pure rule engines (no LLM) because they are eligibility-critical. An ADK
agent is used only to produce a friendly natural-language summary of the plan.
"""

from __future__ import annotations

from collections.abc import Callable

from ..schemas.documents import DocType
from ..schemas.profile import (
    ChecklistItem,
    FormDecision,
    ITRForm,
    UserProfile,
)

# --- Gap questions ----------------------------------------------------------
# Only the things that genuinely CANNOT be read from Form 16 / AIS / 26AS, and
# that affect ITR-1 vs ITR-2 eligibility. Everything else is inferred from the
# uploaded documents to keep manual effort minimal. Bool toggles default off so
# a typical filer confirms in a single click.
GAP_QUESTIONS: list[dict] = [
    {"id": "age", "text": "Your age", "type": "number", "default": 30,
     "help": "Determines senior-citizen benefits under the old regime."},
    {"id": "residential_status", "text": "Residential status", "type": "choice",
     "options": ["resident", "rnor", "non_resident"], "default": "resident",
     "help": "Non-residents and RNOR cannot use ITR-1."},
    {"id": "num_house_properties", "text": "How many house properties do you own?",
     "type": "number", "default": 0,
     "help": "More than two properties requires ITR-2."},
    {"id": "has_unlisted_shares", "text": "Do you hold unlisted shares?", "type": "bool"},
    {"id": "has_rsu_esop", "text": "Do you hold RSUs / ESOPs?", "type": "bool"},
    {"id": "is_company_director", "text": "Are you a director in any company?", "type": "bool"},
    {"id": "has_foreign_assets_income", "text": "Any foreign assets or foreign income?",
     "type": "bool"},
    {"id": "has_brought_forward_losses", "text": "Any brought-forward losses to carry?",
     "type": "bool"},
    {"id": "agricultural_income_above_5k", "text": "Agricultural income above 5,000?",
     "type": "bool"},
]


# Sentinel a bool answer carries when the user is "not sure".
UNSURE = "unsure"


def build_profile(answers: dict) -> UserProfile:
    """Build a ``UserProfile`` from raw questionnaire answers.

    Bool answers set to :data:`UNSURE` are not applied to the profile; instead
    their field names are collected into ``unsure_fields`` so the document stage
    can resolve them from extracted figures.

    Args:
        answers: Raw questionnaire answers keyed by question id.

    Returns:
        A ``UserProfile`` with unsure bool answers deferred.
    """
    cleaned = dict(answers)
    unsure = [k for k, v in cleaned.items()
              if v == UNSURE and k in UserProfile.model_fields]
    valid = {k: v for k, v in cleaned.items()
             if k in UserProfile.model_fields and v != UNSURE}
    valid["unsure_fields"] = unsure
    return UserProfile(**valid)


# Maps an extractable profile flag to a predicate over the consolidated input.
_UNSURE_RESOLVERS: dict[str, "Callable[[TaxInput], bool]"] = {
    "has_professional_income": lambda ti: ti.professional_fees > 0,
    "has_capital_gains": lambda ti: any((
        ti.capital_gains.stcg_111a, ti.capital_gains.ltcg_112a,
        ti.capital_gains.stcg_other, ti.capital_gains.ltcg_other,
        ti.capital_gains.vda_gain)),
    "has_stcg": lambda ti: (ti.capital_gains.stcg_111a + ti.capital_gains.stcg_other) > 0,
    "ltcg_112a_above_125k": lambda ti: ti.capital_gains.ltcg_112a > 125_000,
    "has_crypto_vda": lambda ti: ti.capital_gains.vda_gain > 0,
    "has_savings_interest": lambda ti: ti.savings_interest > 0,
    "has_fd_interest": lambda ti: ti.fd_interest > 0,
    "has_dividends": lambda ti: ti.dividend > 0,
    "has_home_loan": lambda ti: ti.deductions.home_loan_interest > 0,
    "claims_80c": lambda ti: ti.deductions.amount_80c > 0,
    "claims_80d": lambda ti: (ti.deductions.amount_80d_self
                              + ti.deductions.amount_80d_parents) > 0,
    "has_nps": lambda ti: ti.deductions.amount_80ccd1b > 0,
    "has_employer_nps": lambda ti: ti.deductions.amount_80ccd2 > 0,
}


def resolve_unsure_flags(profile: UserProfile, ti: TaxInput) -> list[str]:
    """Resolve "not sure" questionnaire answers from extracted documents.

    For every field the user marked unsure, infer its boolean value from the
    consolidated ``TaxInput`` and write it back onto ``profile``. Resolved
    fields are removed from ``profile.unsure_fields``.

    Args:
        profile: User profile carrying ``unsure_fields`` to resolve.
        ti: Consolidated tax input built from the uploaded documents.

    Returns:
        Human-readable notes describing each resolution.
    """
    notes: list[str] = []
    remaining: list[str] = []
    for field in profile.unsure_fields:
        resolver = _UNSURE_RESOLVERS.get(field)
        if resolver is None:
            remaining.append(field)
            continue
        value = bool(resolver(ti))
        setattr(profile, field, value)
        notes.append(f"{field} = {value} (from documents)")
    profile.unsure_fields = remaining
    return notes


# Income heads summed for the 50-lakh ITR-2 threshold.
def _total_income(ti) -> float:
    cg = ti.capital_gains
    return (sum(s.gross_salary for s in ti.salaries)
            + ti.savings_interest + ti.fd_interest + ti.dividend
            + ti.family_pension + ti.other_income + ti.let_out_annual_rent
            + cg.stcg_111a + cg.ltcg_112a + cg.stcg_other + cg.ltcg_other + cg.vda_gain)


def infer_profile_fields(ti) -> dict:
    """Infer every document-derivable profile flag from the consolidated input.

    Reuses the same predicates that resolve "not sure" answers, plus the
    salary-count and 50-lakh threshold derivations, so the user is never asked
    anything the documents already answer.

    Args:
        ti: Consolidated ``TaxInput`` built from uploaded documents.

    Returns:
        Mapping of profile field names to inferred values.
    """
    fields: dict = {flag: bool(pred(ti)) for flag, pred in _UNSURE_RESOLVERS.items()}
    fields["total_income_above_50l"] = _total_income(ti) > 5_000_000
    n = len(ti.salaries)
    if n:
        fields["num_employers"] = n
        fields["changed_jobs"] = n > 1
    return fields


def build_profile_from_docs_and_gaps(ti, gap_answers: dict) -> UserProfile:
    """Combine document-inferred fields with the user's gap answers.

    Gap answers (the un-inferable, form-critical questions) take precedence over
    inference. Returns a fully populated ``UserProfile`` for form selection.
    """
    merged = dict(infer_profile_fields(ti))
    merged.update({k: v for k, v in gap_answers.items() if v != UNSURE})
    return build_profile(merged)


def select_form(profile: UserProfile) -> FormDecision:
    """Deterministically select ITR-1 vs ITR-2 from the profile.

    Implements the official ITR-1 disqualifiers; anything that disqualifies
    ITR-1 (and is still an individual non-business case) maps to ITR-2.

    Args:
        profile: The user profile from the questionnaire.

    Returns:
        A ``FormDecision`` with the chosen form and the reasons.
    """
    reasons: list[str] = []

    if profile.residential_status != profile.residential_status.RESIDENT:
        reasons.append("Not a resident (RNOR/NRI) - ITR-1 not allowed.")
    if profile.total_income_above_50l:
        reasons.append("Total income above 50 lakh.")
    if profile.has_stcg:
        reasons.append("Short-term capital gains present.")
    if profile.ltcg_112a_above_125k:
        reasons.append("LTCG u/s 112A above 1.25 lakh.")
    if profile.has_crypto_vda:
        reasons.append("Crypto/VDA gains present.")
    if profile.has_unlisted_shares:
        reasons.append("Holds unlisted shares.")
    if profile.has_rsu_esop:
        reasons.append("RSU/ESOP perquisite/deferral reporting.")
    if profile.has_professional_income:
        reasons.append("Has professional/freelance income (Sec 194J) — requires ITR-2/ITR-3.")
    if profile.is_company_director:
        reasons.append("Is a company director.")
    if profile.has_foreign_assets_income:
        reasons.append("Has foreign assets/income.")
    if profile.has_brought_forward_losses:
        reasons.append("Has brought-forward losses to carry forward.")
    if profile.agricultural_income_above_5k:
        reasons.append("Agricultural income above 5,000.")
    if profile.num_house_properties > 2:
        reasons.append("More than two house properties.")

    if reasons:
        return FormDecision(form=ITRForm.ITR2, reasons=reasons)
    return FormDecision(
        form=ITRForm.ITR1,
        reasons=["Resident salaried with simple income - ITR-1 (Sahaj) applies."])


# --- Document checklist content ---------------------------------------------
_HOW_TO: dict[str, ChecklistItem] = {
    DocType.FORM16.value: ChecklistItem(
        doc_type=DocType.FORM16.value, title="Form 16 (from each employer)", required=True,
        why="Primary record of salary, exemptions, deductions and TDS.",
        source="Employer / TRACES",
        how_to_get=[
            "Ask your employer's HR/payroll portal for Form 16 (Part A + Part B).",
            "If you changed jobs, collect a separate Form 16 from every employer.",
            "Verify Part A TDS matches Form 26AS."]),
    DocType.FORM26AS.value: ChecklistItem(
        doc_type=DocType.FORM26AS.value, title="Form 26AS (Annual Tax Statement)", required=True,
        why="Authoritative record of all TDS, advance tax and self-assessment tax.",
        source="incometax.gov.in / TRACES",
        how_to_get=[
            "Login to incometax.gov.in.",
            "e-File > Income Tax Returns > View Form 26AS, then continue to TRACES.",
            "Download the statement for AY 2026-27."]),
    DocType.AIS.value: ChecklistItem(
        doc_type=DocType.AIS.value, title="Annual Information Statement (AIS)", required=True,
        why="Consolidated third-party report of interest, dividend and securities transactions.",
        source="incometax.gov.in",
        how_to_get=[
            "Login to incometax.gov.in.",
            "Open the AIS tile (Services > AIS).",
            "Download the AIS PDF. Its password is your PAN (lowercase) + date of birth (DDMMYYYY)."]),
    DocType.INTEREST_CERT.value: ChecklistItem(
        doc_type=DocType.INTEREST_CERT.value, title="Bank Interest Certificate", required=False,
        why="Exact savings and FD/RD interest for the year.",
        source="Bank net-banking",
        how_to_get=[
            "Login to your bank's net-banking.",
            "Download the interest certificate / TDS certificate for FY 2025-26."]),
    DocType.BROKER_PNL.value: ChecklistItem(
        doc_type=DocType.BROKER_PNL.value, title="Broker Tax P&L Statement", required=False,
        why="Scrip-wise STCG/LTCG classification needed for capital gains.",
        source="Broker (Zerodha, Upstox, Groww, etc.)",
        how_to_get=[
            "Open your broker console (e.g. Zerodha Console).",
            "Download the Tradewise Tax P&L for FY 2025-26 (not the symbol-wise report)."]),
    DocType.HOME_LOAN_CERT.value: ChecklistItem(
        doc_type=DocType.HOME_LOAN_CERT.value, title="Home Loan Interest Certificate", required=False,
        why="Interest (Sec 24b) and principal (80C) for housing loan deductions.",
        source="Lender",
        how_to_get=[
            "Download the provisional/final interest certificate from your lender's portal for FY 2025-26."]),
    DocType.DEDUCTION_PROOF.value: ChecklistItem(
        doc_type=DocType.DEDUCTION_PROOF.value, title="Deduction Proofs (80C / 80D / NPS)", required=False,
        why="Support deductions not already captured in Form 16 (old regime).",
        source="Investment/insurance providers",
        how_to_get=[
            "Collect ELSS/PPF/LIC statements, NPS (80CCD-1B) receipt, and health-insurance premium receipts."]),
    DocType.FORM16A.value: ChecklistItem(
        doc_type=DocType.FORM16A.value, title="Form 16A (non-salary TDS)", required=False,
        why="TDS on interest or other non-salary income.",
        source="Deductor / TRACES",
        how_to_get=["Download from the deductor (bank/company) if TDS was deducted on non-salary income."]),
    DocType.RENT_RECEIPT.value: ChecklistItem(
        doc_type=DocType.RENT_RECEIPT.value, title="Rent Receipts / HRA Proof", required=False,
        why="Compute HRA exemption (Sec 10(13A)) or 80GG rent deduction.",
        source="Landlord / salary slip",
        how_to_get=[
            "Collect rent receipts (or the rent agreement + bank proof) for FY 2025-26.",
            "Note your annual basic + DA from a salary slip and whether the home is in a metro city.",
            "Landlord PAN is required if annual rent exceeds 1,00,000."]),
    DocType.DONATION_80G.value: ChecklistItem(
        doc_type=DocType.DONATION_80G.value, title="80G Donation Receipts", required=False,
        why="Claim deduction for eligible donations under Sec 80G.",
        source="Donee institution",
        how_to_get=[
            "Collect stamped 80G receipts showing the donee PAN and the deduction category.",
            "Ensure the donation reflects in your AIS (ARN/donation reference)."]),
}


# Optional doc types offered up front (beyond the three mandatory ones), so the
# filer can drop everything in one place without first answering a questionnaire.
_BASE_OPTIONAL = [
    DocType.INTEREST_CERT, DocType.BROKER_PNL, DocType.HOME_LOAN_CERT,
    DocType.DEDUCTION_PROOF, DocType.FORM16A, DocType.RENT_RECEIPT, DocType.DONATION_80G,
]


def build_base_checklist() -> list[ChecklistItem]:
    """Document checklist shown before any questionnaire (docs-first flow).

    Form 16, Form 26AS and AIS are the mandatory backbone; the rest are optional
    and only matter if the filer has that income/deduction. Inference and the
    review step decide what (if anything) is still needed.
    """
    base = [_HOW_TO[DocType.FORM16.value], _HOW_TO[DocType.FORM26AS.value],
            _HOW_TO[DocType.AIS.value]]
    base.extend(_HOW_TO[dt.value] for dt in _BASE_OPTIONAL)
    return base
