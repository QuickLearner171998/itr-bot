"""Unit tests for the deterministic tax engine, consolidation, and rule layers.

Covers the scenarios called out in the plan: single salaried, job change,
dividends, PF/NPS deductions, capital gains, RSU/ESOP form selection, regime
comparison, rebate boundaries, and the independent re-computation check.
"""

from __future__ import annotations

from backend.compute.consolidate import consolidate, consolidate_detailed
from backend.compute.engine import compute_regime, compute_taxes
from backend.compute.recompute import verify
from backend.compute.validators import validate_final_return
from backend.schemas.compute import (
    CapitalGains,
    Deductions,
    SalaryComponent,
    TaxInput,
)
from backend.schemas.documents import (
    DocType,
    DocumentExtraction,
    ExtractedField,
)
from backend.agents.intake import build_profile, select_form
from backend.schemas.profile import ITRForm


def _salary(ti: TaxInput) -> TaxInput:
    return ti


def test_new_regime_rebate_zero_tax_at_12L_taxable():
    # Gross 12.75L - 75k std = 12L taxable -> 87A rebate -> nil tax.
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1275000)])
    res = compute_regime(ti, "new")
    assert res.total_income == 1200000
    assert res.total_tax_liability == 0.0


def test_old_regime_slab_basic():
    # 12.25L taxable old regime: 12500 + 100000 + 67500 = 180000 + 4% cess.
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1275000)])
    res = compute_regime(ti, "old")
    assert res.total_income == 1225000
    assert res.total_tax_liability == 187200.0


def test_job_change_aggregates_two_employers():
    ti = TaxInput(salaries=[
        SalaryComponent(employer_name="A", gross_salary=900000),
        SalaryComponent(employer_name="B", gross_salary=800000),
    ])
    res = compute_regime(ti, "new")
    # 17L - 75k = 16.25L taxable.
    assert res.total_income == 1625000


def test_capital_gains_ltcg_112a_exemption_and_rate():
    # 2.8L LTCG 112A: (2.8L - 1.25L) * 12.5% = 19375 (plus salary tax).
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=2200000)],
                  capital_gains=CapitalGains(ltcg_112a=280000))
    res = compute_regime(ti, "new")
    cg_step = next(s for s in res.steps if s.key == "cg_tax")
    assert round(cg_step.amount) == 19375


def test_dividends_and_interest_added_to_other_sources():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=800000)],
                  dividend=50000, savings_interest=8000, fd_interest=20000)
    res = compute_regime(ti, "new")
    gti = next(s for s in res.steps if s.key == "gti")
    # 8L - 75k = 725000 net salary + 78000 other = 803000.
    assert round(gti.amount) == 803000


def test_old_regime_deductions_pf_nps_80c_capped():
    ti = TaxInput(
        salaries=[SalaryComponent(gross_salary=1500000)],
        deductions=Deductions(amount_80c=200000, amount_80ccd1b=50000,
                              amount_80d_self=25000),
        savings_interest=12000)
    res = compute_regime(ti, "old")
    ded = next(s for s in res.steps if s.key == "chvia")
    # 80C capped 150k + 80CCD1B 50k + 80D 25k + 80TTA 10k = 235000.
    assert round(ded.amount) == 235000


def test_compute_taxes_uses_chosen_regime():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1275000)])
    comp = compute_taxes(ti, "new")
    assert comp.regime == "new"
    assert comp.result.total_tax_liability == 0.0
    comp_old = compute_taxes(ti, "old")
    assert comp_old.regime == "old"
    assert comp_old.result.total_tax_liability == 187200.0


def test_independent_recompute_agrees():
    ti = TaxInput(
        salaries=[SalaryComponent(gross_salary=2200000, exempt_allowances=200000,
                                  professional_tax=2400)],
        deductions=Deductions(amount_80c=150000, amount_80ccd1b=50000),
        capital_gains=CapitalGains(ltcg_112a=280000), savings_interest=12000)
    comp = compute_taxes(ti, "old")
    ok, _ = verify(ti, comp)
    assert ok is True


def test_surcharge_high_income():
    # 60L salary triggers 10% surcharge band; verify a non-zero surcharge.
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=6000000)])
    res = compute_regime(ti, "new")
    assert res.surcharge > 0


def test_form_selection_itr2_for_stcg():
    profile = build_profile({"has_capital_gains": True, "has_stcg": True})
    decision = select_form(profile)
    assert decision.form == ITRForm.ITR2


def test_form_selection_itr2_for_rsu():
    profile = build_profile({"has_rsu_esop": True})
    assert select_form(profile).form == ITRForm.ITR2


def test_form_selection_itr1_for_simple_salaried():
    profile = build_profile({"age": 30, "has_savings_interest": True, "claims_80c": True})
    assert select_form(profile).form == ITRForm.ITR1


def test_consolidate_merges_documents():
    docs = [
        DocumentExtraction(doc_type=DocType.FORM16, filename="f16.pdf", fields=[
            ExtractedField(name="gross_salary", label="Gross", value=1200000, confidence=0.95),
            ExtractedField(name="tds", label="TDS", value=60000, confidence=0.95),
            ExtractedField(name="deduction_80c", label="80C", value=150000, confidence=0.9),
        ]),
        DocumentExtraction(doc_type=DocType.BROKER_PNL, filename="pnl.pdf", fields=[
            ExtractedField(name="ltcg_112a", label="LTCG", value=300000, confidence=0.9),
        ]),
    ]
    ti = consolidate(docs, age=30)
    assert sum(s.gross_salary for s in ti.salaries) == 1200000
    assert ti.tds_total == 60000
    assert ti.capital_gains.ltcg_112a == 300000
    assert ti.deductions.amount_80c == 150000


def test_hra_not_double_counted_when_employer_already_exempted():
    # Form 16 already exempts allowances (employer gave HRA). A rent receipt
    # must NOT be recomputed on top, else the exemption is double-counted.
    docs = [
        DocumentExtraction(doc_type=DocType.FORM16, filename="f16.pdf", fields=[
            ExtractedField(name="gross_salary", label="Gross", value=1500000, confidence=0.95),
            ExtractedField(name="exempt_allowances", label="Exempt", value=200000, confidence=0.95),
        ]),
        DocumentExtraction(doc_type=DocType.RENT_RECEIPT, filename="rent.pdf", fields=[
            ExtractedField(name="hra_received", label="HRA", value=200000, confidence=0.9),
            ExtractedField(name="rent_paid", label="Rent", value=300000, confidence=0.9),
            ExtractedField(name="basic_da", label="Basic", value=900000, confidence=0.9),
        ]),
    ]
    ti = consolidate(docs, age=30)
    assert ti.hra_received == 0.0  # not recomputed; employer exemption stands
    res = compute_regime(ti, "old")
    exempt = next(s for s in res.steps if s.key == "exempt")
    assert round(exempt.amount) == 200000  # only the Form 16 exemption, not doubled


def test_hra_computed_when_employer_gave_no_exemption():
    # No employer exempt allowance -> rent receipt HRA is claimed at filing.
    docs = [
        DocumentExtraction(doc_type=DocType.FORM16, filename="f16.pdf", fields=[
            ExtractedField(name="gross_salary", label="Gross", value=1500000, confidence=0.95),
        ]),
        DocumentExtraction(doc_type=DocType.RENT_RECEIPT, filename="rent.pdf", fields=[
            ExtractedField(name="hra_received", label="HRA", value=200000, confidence=0.9),
            ExtractedField(name="rent_paid", label="Rent", value=300000, confidence=0.9),
            ExtractedField(name="basic_da", label="Basic", value=900000, confidence=0.9),
        ]),
    ]
    ti = consolidate(docs, age=30)
    assert ti.hra_received == 200000.0


def test_consolidate_flags_cross_source_discrepancy():
    # Interest certificate and AIS report different savings interest -> flagged,
    # and the larger (safe) value is chosen as the prefill.
    docs = [
        DocumentExtraction(doc_type=DocType.INTEREST_CERT, filename="cert.pdf", fields=[
            ExtractedField(name="savings_interest", label="Savings", value=8000, confidence=0.9),
        ]),
        DocumentExtraction(doc_type=DocType.AIS, filename="ais.pdf", fields=[
            ExtractedField(name="savings_interest", label="Savings", value=11000, confidence=0.9),
        ]),
    ]
    ti, discrepancies = consolidate_detailed(docs, age=30)
    assert ti.savings_interest == 11000
    flagged = next(d for d in discrepancies if d.field == "savings_interest")
    assert flagged.chosen == 11000
    assert len(flagged.sources) == 2


def test_consolidate_no_discrepancy_when_sources_agree():
    docs = [
        DocumentExtraction(doc_type=DocType.INTEREST_CERT, filename="cert.pdf", fields=[
            ExtractedField(name="savings_interest", label="Savings", value=8000, confidence=0.9),
        ]),
        DocumentExtraction(doc_type=DocType.AIS, filename="ais.pdf", fields=[
            ExtractedField(name="savings_interest", label="Savings", value=8000, confidence=0.9),
        ]),
    ]
    _, discrepancies = consolidate_detailed(docs, age=30)
    assert not any(d.field == "savings_interest" for d in discrepancies)


def test_final_validation_blocks_itr1_with_stcg():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=900000)],
                  capital_gains=CapitalGains(stcg_111a=50000))
    profile = build_profile({})
    issues = validate_final_return(ti, ITRForm.ITR1, profile)
    assert any(i.severity == "error" for i in issues)


def test_senior_citizen_higher_exemption():
    ti = TaxInput(age=65, salaries=[SalaryComponent(gross_salary=350000)])
    res = compute_regime(ti, "old")
    # 3L net (350k-50k std), senior exemption 3L -> nil tax.
    assert res.total_tax_liability == 0.0


def test_hra_exemption_old_regime():
    # Least of: HRA 240000, rent 300000 - 10%*1000000 = 200000, 50%*1000000 = 500000.
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1500000)],
                  hra_received=240000, hra_rent_paid=300000, hra_basic_da=1000000,
                  hra_is_metro=True)
    res = compute_regime(ti, "old")
    exempt = next(s for s in res.steps if s.key == "exempt")
    assert round(exempt.amount) == 200000


def test_hra_not_allowed_new_regime():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1500000)],
                  hra_received=240000, hra_rent_paid=300000, hra_basic_da=1000000)
    res = compute_regime(ti, "new")
    assert not any(s.key == "exempt" for s in res.steps)


def test_let_out_house_property_30pct_deduction():
    # NAV 300000 - 0 municipal; 30% std = 90000; interest 50000 -> net 160000.
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1000000)],
                  let_out_annual_rent=300000,
                  deductions=Deductions(home_loan_interest=50000, home_loan_self_occupied=False))
    res = compute_regime(ti, "old")
    hp = next(s for s in res.steps if s.key == "house_property")
    assert round(hp.amount) == 160000


def test_80g_50pct_with_qualifying_limit():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=1500000)],
                  deductions=Deductions(donation_50_limit=100000))
    res = compute_regime(ti, "old")
    # Limited to 10% of adjusted GTI then 50%; just assert a positive 80G effect.
    ded = next(s for s in res.steps if s.key == "chvia")
    assert ded.amount > 0


def test_family_pension_deduction_capped():
    ti = TaxInput(salaries=[SalaryComponent(gross_salary=800000)], family_pension=120000)
    res = compute_regime(ti, "old")
    fp = next(s for s in res.steps if s.key == "fp_ded")
    # 1/3 of 120000 = 40000, capped at 15000 (old).
    assert round(fp.amount) == 15000


def test_relief_reduces_total_tax():
    base = compute_regime(TaxInput(salaries=[SalaryComponent(gross_salary=1500000)]), "old")
    with_relief = compute_regime(
        TaxInput(salaries=[SalaryComponent(gross_salary=1500000)], relief_89=10000), "old")
    assert base.total_tax_liability - with_relief.total_tax_liability == 10000


def test_recompute_agrees_with_all_heads():
    ti = TaxInput(
        salaries=[SalaryComponent(gross_salary=1800000, exempt_allowances=50000,
                                  professional_tax=2400)],
        hra_received=180000, hra_rent_paid=240000, hra_basic_da=900000, hra_is_metro=True,
        let_out_annual_rent=240000,
        family_pension=90000,
        deductions=Deductions(
            amount_80c=150000, amount_80ccd1b=50000, amount_80d_self=25000,
            amount_80e=40000, amount_80ddb=30000, home_loan_interest=120000,
            home_loan_self_occupied=False, donation_50_limit=50000),
        savings_interest=12000, fd_interest=30000)
    comp = compute_taxes(ti, "old")
    ok, note = verify(ti, comp)
    assert ok is True, note
