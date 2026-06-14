"""Guidance agent: build the guided, copy-paste filing walkthrough.

Because the portal has no third-party prefill API, this produces a
schedule-by-schedule walkthrough that mirrors the income-tax e-filing screens
with every computed value the user must copy in. The structure is deterministic
(derived from the consolidated input and chosen regime); an ADK agent adds a
short friendly intro.
"""

from __future__ import annotations

from ..app.config import settings
from ..schemas.compute import RegimeResult, TaxInput
from ..schemas.profile import ITRForm
from .llm import build_agent, run_agent


def _money(value: float) -> str:
    return f"{round(value):,}"


def build_guided_filing(
    ti: TaxInput, result: RegimeResult, form: ITRForm
) -> list[dict]:
    """Build the ordered guided-filing sections mirroring the portal.

    Args:
        ti: Consolidated tax input.
        result: The recommended regime's computation.
        form: Selected ITR form.

    Returns:
        A list of section dicts (title, portal_path, fields, note).
    """
    regime_label = "New Regime (115BAC, default)" if result.regime == "new" else "Old Regime"
    gross_salary = sum(s.gross_salary for s in ti.salaries)
    exempt = sum(s.exempt_allowances for s in ti.salaries)
    std = 75000 if result.regime == "new" else 50000
    ptax = 0 if result.regime == "new" else sum(s.professional_tax for s in ti.salaries)
    cg = ti.capital_gains

    sections: list[dict] = [
        {"title": "1. Start the return", "portal_path": "e-File > Income Tax Returns > File Income Tax Return",
         "fields": [
             {"label": "Assessment Year", "value": settings.assessment_year, "note": "FY 2025-26"},
             {"label": "Mode", "value": "Online", "note": "Select 'Start New filing'"},
             {"label": "Status", "value": "Individual", "note": ""},
             {"label": "ITR Form", "value": form.value, "note": "As determined for your profile"},
             {"label": "Tax Regime", "value": regime_label,
              "note": "Choose this regime under Personal Information > select 'Opting out' only for old regime"}],
         "note": "Most fields may already be prefilled by the portal; verify each against the values below."},
        {"title": "2. Salary details (Schedule S)",
         "portal_path": "Income Sources > Salary",
         "fields": [
             {"label": "Gross Salary (Sec 17)", "value": _money(gross_salary),
              "note": "Sum across all employers" + (" (job change)" if len(ti.salaries) > 1 else "")},
             {"label": "Exempt allowances u/s 10", "value": _money(exempt),
              "note": "Not allowed under new regime" if result.regime == "new" else "HRA/LTA etc."},
             {"label": "Standard Deduction u/s 16(ia)", "value": _money(std), "note": ""},
             {"label": "Professional Tax u/s 16(iii)", "value": _money(ptax),
              "note": "Not allowed under new regime" if result.regime == "new" else ""}],
         "note": "If you changed jobs, add each employer separately under the salary schedule."},
    ]

    if ti.house_property_income or ti.deductions.home_loan_interest:
        sections.append({
            "title": "3. House property",
            "portal_path": "Income Sources > House Property",
            "fields": [
                {"label": "Type", "value": "Self-occupied" if ti.deductions.home_loan_self_occupied else "Let-out", "note": ""},
                {"label": "Interest on housing loan (Sec 24b)", "value": _money(ti.deductions.home_loan_interest),
                 "note": "Capped at 2,00,000 for self-occupied (old regime); not allowed for self-occupied in new regime"}],
            "note": ""})

    other = []
    if ti.savings_interest:
        other.append({"label": "Savings bank interest", "value": _money(ti.savings_interest), "note": "Sec 80TTA up to 10,000 (old regime)"})
    if ti.fd_interest:
        other.append({"label": "Interest on deposits (FD/RD)", "value": _money(ti.fd_interest), "note": ""})
    if ti.dividend:
        other.append({"label": "Dividend income", "value": _money(ti.dividend), "note": "Taxed at slab rate"})
    if other:
        sections.append({"title": "4. Other sources",
                         "portal_path": "Income Sources > Other Sources",
                         "fields": other, "note": ""})

    if form == ITRForm.ITR2 and (cg.stcg_111a or cg.ltcg_112a or cg.stcg_other or cg.ltcg_other or cg.vda_gain):
        cg_fields = []
        if cg.stcg_111a:
            cg_fields.append({"label": "STCG u/s 111A (listed equity)", "value": _money(cg.stcg_111a), "note": "Taxed at 20%"})
        if cg.ltcg_112a:
            cg_fields.append({"label": "LTCG u/s 112A (listed equity)", "value": _money(cg.ltcg_112a),
                              "note": "First 1,25,000 exempt; balance at 12.5%. Enter scrip-wise in Schedule 112A."})
        if cg.stcg_other:
            cg_fields.append({"label": "STCG (other, slab rate)", "value": _money(cg.stcg_other), "note": ""})
        if cg.ltcg_other:
            cg_fields.append({"label": "LTCG u/s 112 (other)", "value": _money(cg.ltcg_other), "note": "12.5%"})
        if cg.vda_gain:
            cg_fields.append({"label": "Crypto/VDA gains", "value": _money(cg.vda_gain), "note": "Flat 30% in Schedule VDA"})
        sections.append({"title": "5. Capital gains (Schedule CG)",
                         "portal_path": "Income Sources > Capital Gains",
                         "fields": cg_fields,
                         "note": "Enter listed-equity gains scrip-wise in Schedule 112A; totals flow to Schedule CG."})

    d = ti.deductions
    ded_fields = []
    if result.regime == "old":
        if d.amount_80c:
            ded_fields.append({"label": "80C", "value": _money(min(d.amount_80c, 150000)), "note": "Max 1,50,000"})
        if d.amount_80ccd1b:
            ded_fields.append({"label": "80CCD(1B) NPS", "value": _money(min(d.amount_80ccd1b, 50000)), "note": "Max 50,000"})
        if d.amount_80d_self or d.amount_80d_parents:
            ded_fields.append({"label": "80D Health Insurance", "value": _money(d.amount_80d_self + d.amount_80d_parents), "note": ""})
        if ti.savings_interest:
            ded_fields.append({"label": "80TTA savings interest", "value": _money(min(ti.savings_interest, 10000)), "note": ""})
    if d.amount_80ccd2:
        ded_fields.append({"label": "80CCD(2) Employer NPS", "value": _money(d.amount_80ccd2), "note": "Allowed in both regimes"})
    if ded_fields:
        sections.append({"title": "6. Deductions (Chapter VI-A)",
                         "portal_path": "Deductions > Chapter VI-A",
                         "fields": ded_fields,
                         "note": "New regime allows only 80CCD(2) employer NPS." if result.regime == "new" else ""})

    sections.append({"title": "7. Taxes paid",
                     "portal_path": "Tax Paid > TDS / Advance Tax",
                     "fields": [
                         {"label": "Total TDS", "value": _money(ti.tds_total), "note": "Verify against Form 26AS"},
                         {"label": "Advance tax", "value": _money(ti.advance_tax), "note": ""},
                         {"label": "Self-assessment tax", "value": _money(ti.self_assessment_tax), "note": ""}],
                     "note": ""})

    payable = result.refund_or_payable
    sections.append({"title": "8. Verify computation & submit",
                     "portal_path": "Confirm > Preview & Submit",
                     "fields": [
                         {"label": "Total Income", "value": _money(result.total_income), "note": ""},
                         {"label": "Total Tax Liability", "value": _money(result.total_tax_liability), "note": ""},
                         {"label": ("Tax Payable" if payable >= 0 else "Refund Due"),
                          "value": _money(abs(payable)), "note": ""}],
                     "note": "After submitting, e-verify within 30 days (Aadhaar OTP / net-banking) or the return is invalid."})
    return sections


async def guidance_intro(form: ITRForm, regime: str, payable: float) -> str:
    """Generate a short friendly intro for the guided-filing page."""
    agent = build_agent(
        name="guidance_assistant",
        model_id=settings.orchestration_model,
        instruction=(
            "You are a helpful Indian tax assistant. Write a short (2-3 sentence) "
            "encouraging intro for a step-by-step ITR filing walkthrough. Mention the "
            "form and regime. Do not invent numbers."))
    state = "a refund" if payable < 0 else "tax payable"
    return await run_agent(
        agent, f"Form: {form.value}. Regime: {regime}. Outcome: {state}.")
