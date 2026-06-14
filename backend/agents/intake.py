"""Intake: questionnaire, deterministic form selection, and document checklist.

The questionnaire is predefined and branching. Form selection and the checklist
are pure rule engines (no LLM) because they are eligibility-critical. An ADK
agent is used only to produce a friendly natural-language summary of the plan.
"""

from __future__ import annotations

from collections.abc import Callable

from ..app.config import settings
from ..schemas.documents import DocType
from ..schemas.profile import (
    ChecklistItem,
    FormDecision,
    ITRForm,
    UserProfile,
)
from .llm import build_agent, run_agent

# --- Predefined branching questionnaire -------------------------------------
# Each question id matches a UserProfile field so answers map directly.
QUESTIONNAIRE: list[dict] = [
    {"section": "About you", "questions": [
        {"id": "age", "text": "Your age", "type": "number", "default": 30,
         "help": "Determines senior-citizen benefits under the old regime."},
        {"id": "residential_status", "text": "Residential status", "type": "choice",
         "options": ["resident", "rnor", "non_resident"], "default": "resident",
         "help": "Non-residents and RNOR cannot use ITR-1."},
    ]},
    {"section": "Salary", "questions": [
        {"id": "changed_jobs", "text": "Did you change jobs during FY 2025-26?", "type": "bool",
         "help": "If yes, you will have multiple Form 16s to combine."},
        {"id": "num_employers", "text": "How many employers did you have?", "type": "number",
         "default": 1, "depends_on": "changed_jobs"},
        {"id": "total_income_above_50l", "text": "Is your total income above 50 lakh?", "type": "bool",
         "help": "Above 50 lakh forces ITR-2."},
    ]},
    {"section": "House property", "questions": [
        {"id": "num_house_properties", "text": "How many house properties do you own?",
         "type": "number", "default": 0},
        {"id": "has_home_loan", "text": "Do you have a home loan?", "type": "bool",
         "extractable": True},
        {"id": "has_let_out_property", "text": "Is any property let out (rented)?", "type": "bool",
         "depends_on": "num_house_properties"},
    ]},
    {"section": "Investments & capital gains", "questions": [
        {"id": "has_capital_gains", "text": "Did you sell shares, mutual funds, property or crypto?",
         "type": "bool", "extractable": True},
        {"id": "has_stcg", "text": "Any short-term capital gains?", "type": "bool",
         "depends_on": "has_capital_gains", "extractable": True},
        {"id": "ltcg_112a_above_125k", "text": "Are long-term equity gains above 1.25 lakh?",
         "type": "bool", "depends_on": "has_capital_gains", "extractable": True},
        {"id": "has_crypto_vda", "text": "Any crypto / virtual digital asset gains?", "type": "bool",
         "depends_on": "has_capital_gains", "extractable": True},
        {"id": "has_rsu_esop", "text": "Do you hold RSUs / ESOPs?", "type": "bool"},
        {"id": "has_unlisted_shares", "text": "Do you hold unlisted shares?", "type": "bool"},
    ]},
    {"section": "Other income", "questions": [
        {"id": "has_savings_interest", "text": "Savings bank interest?", "type": "bool",
         "extractable": True},
        {"id": "has_fd_interest", "text": "Fixed/recurring deposit interest?", "type": "bool",
         "extractable": True},
        {"id": "has_dividends", "text": "Dividend income?", "type": "bool", "extractable": True},
    ]},
    {"section": "Deductions & retirement", "questions": [
        {"id": "has_pf", "text": "Do you contribute to EPF/PF?", "type": "bool"},
        {"id": "has_nps", "text": "Do you contribute to NPS (self)?", "type": "bool",
         "extractable": True},
        {"id": "has_employer_nps", "text": "Does your employer contribute to NPS?", "type": "bool",
         "extractable": True},
        {"id": "claims_80c", "text": "Do you claim 80C (PPF, ELSS, LIC, etc.)?", "type": "bool",
         "extractable": True},
        {"id": "claims_80d", "text": "Do you claim 80D (health insurance)?", "type": "bool",
         "extractable": True},
    ]},
    {"section": "Other flags", "questions": [
        {"id": "is_company_director", "text": "Are you a director in any company?", "type": "bool"},
        {"id": "has_foreign_assets_income", "text": "Any foreign assets or foreign income?", "type": "bool"},
        {"id": "has_brought_forward_losses", "text": "Any brought-forward losses to carry?", "type": "bool"},
        {"id": "agricultural_income_above_5k", "text": "Agricultural income above 5,000?", "type": "bool"},
    ]},
    {"section": "Regime", "questions": [
        {"id": "preferred_regime", "text": "Preferred tax regime", "type": "choice",
         "options": ["auto", "old", "new"], "default": "auto",
         "help": "Auto compares both regimes and recommends the cheaper one."},
    ]},
]


# Sentinel a bool question carries when the user answers "not sure". Such fields
# are left at their default and recorded so documents resolve them later.
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
    if cleaned.get("preferred_regime") in ("auto", "", None):
        cleaned["preferred_regime"] = None
    unsure = [k for k, v in cleaned.items()
              if v == UNSURE and k in UserProfile.model_fields]
    valid = {k: v for k, v in cleaned.items()
             if k in UserProfile.model_fields and v != UNSURE}
    valid["unsure_fields"] = unsure
    return UserProfile(**valid)


# Maps an extractable profile flag to a predicate over the consolidated input.
_UNSURE_RESOLVERS: dict[str, "Callable[[TaxInput], bool]"] = {
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


def build_checklist(profile: UserProfile) -> list[ChecklistItem]:
    """Build the per-user document checklist from the profile.

    Args:
        profile: The user profile.

    Returns:
        Ordered list of required/optional documents with how-to-get steps.
    """
    items = [_HOW_TO[DocType.FORM16.value], _HOW_TO[DocType.FORM26AS.value],
             _HOW_TO[DocType.AIS.value]]

    if profile.has_savings_interest or profile.has_fd_interest:
        items.append(_HOW_TO[DocType.INTEREST_CERT.value])
    if profile.has_capital_gains or profile.has_crypto_vda or profile.has_rsu_esop:
        items.append(_HOW_TO[DocType.BROKER_PNL.value])
    if profile.has_home_loan or profile.num_house_properties > 0:
        items.append(_HOW_TO[DocType.HOME_LOAN_CERT.value])
    if profile.claims_80c or profile.claims_80d or profile.has_nps:
        items.append(_HOW_TO[DocType.DEDUCTION_PROOF.value])
    if profile.has_fd_interest or profile.has_dividends:
        items.append(_HOW_TO[DocType.FORM16A.value])
    # HRA/rent and donations apply broadly; offer them as optional.
    items.append(_HOW_TO[DocType.RENT_RECEIPT.value])
    items.append(_HOW_TO[DocType.DONATION_80G.value])
    return items


async def summarize_plan(profile: UserProfile, decision: FormDecision,
                         checklist: list[ChecklistItem]) -> str:
    """Use an ADK agent to write a short, friendly summary of the filing plan."""
    agent = build_agent(
        name="intake_assistant",
        model_id=settings.orchestration_model,
        instruction=(
            "You are a friendly Indian tax assistant. Given a user's profile, the "
            "selected ITR form with reasons, and a document checklist, write a short "
            "(3-4 sentence) plain-English summary of what the user needs to do next. "
            "Be encouraging and specific. Do not invent facts."))
    docs = ", ".join(c.title for c in checklist)
    prompt = (
        f"Selected form: {decision.form.value}. Reasons: {'; '.join(decision.reasons)}. "
        f"Documents to gather: {docs}. Profile flags: changed_jobs={profile.changed_jobs}, "
        f"capital_gains={profile.has_capital_gains}, home_loan={profile.has_home_loan}.")
    return await run_agent(agent, prompt)
