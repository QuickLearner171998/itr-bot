"""Intake: questionnaire, deterministic form selection, and document checklist.

The questionnaire is predefined and branching. Form selection and the checklist
are pure rule engines (no LLM) because they are eligibility-critical. An ADK
agent is used only to produce a friendly natural-language summary of the plan.
"""

from __future__ import annotations

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
        {"id": "has_home_loan", "text": "Do you have a home loan?", "type": "bool"},
        {"id": "has_let_out_property", "text": "Is any property let out (rented)?", "type": "bool",
         "depends_on": "num_house_properties"},
    ]},
    {"section": "Investments & capital gains", "questions": [
        {"id": "has_capital_gains", "text": "Did you sell shares, mutual funds, property or crypto?",
         "type": "bool"},
        {"id": "has_stcg", "text": "Any short-term capital gains?", "type": "bool",
         "depends_on": "has_capital_gains"},
        {"id": "ltcg_112a_above_125k", "text": "Are long-term equity gains above 1.25 lakh?",
         "type": "bool", "depends_on": "has_capital_gains"},
        {"id": "has_crypto_vda", "text": "Any crypto / virtual digital asset gains?", "type": "bool",
         "depends_on": "has_capital_gains"},
        {"id": "has_rsu_esop", "text": "Do you hold RSUs / ESOPs?", "type": "bool"},
        {"id": "has_unlisted_shares", "text": "Do you hold unlisted shares?", "type": "bool"},
    ]},
    {"section": "Other income", "questions": [
        {"id": "has_savings_interest", "text": "Savings bank interest?", "type": "bool"},
        {"id": "has_fd_interest", "text": "Fixed/recurring deposit interest?", "type": "bool"},
        {"id": "has_dividends", "text": "Dividend income?", "type": "bool"},
    ]},
    {"section": "Deductions & retirement", "questions": [
        {"id": "has_pf", "text": "Do you contribute to EPF/PF?", "type": "bool"},
        {"id": "has_nps", "text": "Do you contribute to NPS (self)?", "type": "bool"},
        {"id": "has_employer_nps", "text": "Does your employer contribute to NPS?", "type": "bool"},
        {"id": "claims_80c", "text": "Do you claim 80C (PPF, ELSS, LIC, etc.)?", "type": "bool"},
        {"id": "claims_80d", "text": "Do you claim 80D (health insurance)?", "type": "bool"},
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


def build_profile(answers: dict) -> UserProfile:
    """Build a ``UserProfile`` from raw questionnaire answers."""
    cleaned = dict(answers)
    if cleaned.get("preferred_regime") in ("auto", "", None):
        cleaned["preferred_regime"] = None
    valid = {k: v for k, v in cleaned.items() if k in UserProfile.model_fields}
    return UserProfile(**valid)


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
    if profile.has_home_loan:
        items.append(_HOW_TO[DocType.HOME_LOAN_CERT.value])
    if profile.claims_80c or profile.claims_80d or profile.has_nps:
        items.append(_HOW_TO[DocType.DEDUCTION_PROOF.value])
    if profile.has_fd_interest or profile.has_dividends:
        items.append(_HOW_TO[DocType.FORM16A.value])
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
