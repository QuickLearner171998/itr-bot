"""Document-intelligence schemas and the field-spec registry.

The registry is the single source of truth for *what* each document type
contains. It drives three things at once (plug-and-play):
  1. the extraction prompt sent to the LLM,
  2. per-field validation and UI rendering,
  3. the mapping of extracted values into the deterministic tax engine input.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocType(str, Enum):
    FORM16 = "form16"
    FORM16A = "form16a"
    FORM26AS = "form26as"
    AIS = "ais"
    BROKER_PNL = "broker_pnl"
    INTEREST_CERT = "interest_cert"
    HOME_LOAN_CERT = "home_loan_cert"
    DEDUCTION_PROOF = "deduction_proof"
    RENT_RECEIPT = "rent_receipt"
    DONATION_80G = "donation_80g"


class FieldType(str, Enum):
    MONEY = "money"
    TEXT = "text"
    NUMBER = "number"


class FieldSpec(BaseModel):
    """Definition of a single extractable field."""

    name: str
    label: str
    type: FieldType = FieldType.MONEY
    description: str
    required: bool = False


class DocSpec(BaseModel):
    """Definition of a document type: its label and the fields to extract."""

    doc_type: DocType
    title: str
    context_hint: str = ""  # Document-structure guidance injected into the extractor prompt.
    fields: list[FieldSpec]


class ExtractedField(BaseModel):
    """One field value returned by the extractor with trust metadata."""

    name: str
    label: str
    value: float | str | None = None
    confidence: float = 0.0
    source_hint: str | None = None
    flagged: bool = False


class ValidationIssue(BaseModel):
    """A problem detected by a validation layer."""

    severity: str  # "error" | "warning"
    message: str
    fields: list[str] = Field(default_factory=list)


class DocumentExtraction(BaseModel):
    """Full result of extracting + validating one uploaded document."""

    doc_type: DocType
    filename: str
    fields: list[ExtractedField] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    overall_confidence: float = 0.0
    status: str = "pending"  # pending | extracted | validated | needs_review

    def values(self) -> dict[str, float | str | None]:
        """Return a plain ``{name: value}`` mapping for downstream use."""
        return {f.name: f.value for f in self.fields}


# --- Registry ---------------------------------------------------------------

DOC_REGISTRY: dict[DocType, DocSpec] = {
    DocType.FORM16: DocSpec(
        doc_type=DocType.FORM16,
        title="Form 16 (Salary TDS Certificate)",
        context_hint=(
            "Form 16 has two parts. "
            "PART A (generated from TRACES): contains employer TAN, employee PAN, "
            "employer name/address, quarter-wise TDS deposit summary, and the "
            "period of employment (From/To dates). "
            "PART B (prepared by employer): contains the salary computation — "
            "Gross Salary broken into Sec 17(1) salary, 17(2) perquisites and "
            "17(3) profits in lieu; then Sec 10 exempt allowances (HRA, LTA, "
            "gratuity, leave encashment); then Sec 16 deductions (standard "
            "deduction, professional tax); then Net Taxable Salary; then "
            "Chapter VI-A deductions (80C, 80CCD(1B), 80CCD(2), 80D); then "
            "total taxable income, tax computed, 87A rebate, and total TDS. "
            "The employer also states which regime (old/new) was used. "
            "Look carefully at both parts — period dates are in Part A, "
            "perquisites and profits-in-lieu are often in Part B's salary table. "
            "CRITICAL: For Chapter VI-A deductions (80C, 80CCD(1B), 80CCD(2), 80D), "
            "always read from PART B of Form 16 (the 'Deductions under Chapter VI-A' table), "
            "NOT from Part A investment declarations. Part B shows the final allowed deductions. "
            "If this Form 16 consolidates income from a previous employer (shown as 'salary from other employer'), "
            "the taxable_salary field should include that consolidated amount."
        ),
        fields=[
            FieldSpec(name="employer_name", label="Employer Name", type=FieldType.TEXT,
                      description="Name of the employer / deductor.", required=True),
            FieldSpec(name="employer_tan", label="Employer TAN", type=FieldType.TEXT,
                      description="TAN of the employer."),
            FieldSpec(name="period_from", label="Employment From", type=FieldType.TEXT,
                      description="Start date of employment with this employer (dd/mm/yyyy or month name)."),
            FieldSpec(name="period_to", label="Employment To", type=FieldType.TEXT,
                      description="End date of employment with this employer (dd/mm/yyyy or month name)."),
            FieldSpec(name="gross_salary", label="Gross Salary (Sec 17(1)+17(2)+17(3))",
                      description="Total gross salary including perquisites and profits in lieu.",
                      required=True),
            FieldSpec(name="salary_17_1", label="Salary u/s 17(1)",
                      description="Basic salary, allowances and wages as per Sec 17(1)."),
            FieldSpec(name="perquisites_17_2", label="Perquisites u/s 17(2)",
                      description="Value of perquisites (rent-free accommodation, car, ESOP, etc.) as per Sec 17(2)."),
            FieldSpec(name="profits_in_lieu_17_3", label="Profits in Lieu of Salary u/s 17(3)",
                      description="Compensation on termination, ex-gratia, keyman insurance premium, etc."),
            FieldSpec(name="exempt_allowances", label="Exempt Allowances u/s 10",
                      description="Total allowances exempt under section 10 (HRA, LTA, gratuity, leave encashment, etc.)."),
            FieldSpec(name="standard_deduction", label="Standard Deduction",
                      description="Standard deduction u/s 16(ia)."),
            FieldSpec(name="professional_tax", label="Professional Tax u/s 16(iii)",
                      description="Professional tax / tax on employment."),
            FieldSpec(name="taxable_salary", label="Net Taxable Salary",
                      description="Gross salary minus exempt allowances and Sec 16 deductions — the salary chargeable to tax."),
            FieldSpec(name="deduction_80c", label="Deduction u/s 80C",
                      description="Use the 'Total deduction under section 80C, 80CCC and 80CCD(1)' line (often labeled (c) or (g)) from Part B Chapter VI-A — this is the capped consolidated amount (max ₹1,50,000), NOT the per-employer contribution line (d). This consolidated line correctly includes any carried-over amount from a previous employer."),
            FieldSpec(name="deduction_80ccd1b", label="Deduction u/s 80CCD(1B) (NPS self)",
                      description="NPS self-contribution deduction as per Part B Chapter VI-A table."),
            FieldSpec(name="deduction_80ccd2", label="Deduction u/s 80CCD(2) (Employer NPS)",
                      description="Employer NPS contribution deduction as per Part B Chapter VI-A table."),
            FieldSpec(name="deduction_80d", label="Deduction u/s 80D (Health Insurance)",
                      description="Medical insurance premium deduction as per Part B Chapter VI-A table. Enter 0 if not present."),
            FieldSpec(name="tds", label="Total TDS Deducted",
                      description=(
                          "Total TDS deducted and deposited on salary. "
                          "Look for the FINAL TDS line — labeled 'Total TDS Deducted', "
                          "'Tax Deducted at Source', or 'Less: Tax deducted at source'. "
                          "This equals Income Tax + Surcharge + Education Cess combined. "
                          "Do NOT use the 'Income Tax' or 'Tax on total income' line alone "
                          "(that is tax before cess). The correct value matches Form 26AS."
                      ), required=True),
            FieldSpec(name="regime", label="Tax Regime Used by Employer", type=FieldType.TEXT,
                      description="Whether employer computed under 'old' or 'new' regime."),
        ],
    ),
    DocType.FORM16A: DocSpec(
        doc_type=DocType.FORM16A,
        title="Form 16A (TDS on non-salary income)",
        fields=[
            FieldSpec(name="deductor_name", label="Deductor Name", type=FieldType.TEXT,
                      description="Name of the deductor."),
            FieldSpec(name="income_paid", label="Total Amount Paid/Credited",
                      description="Total income on which TDS was deducted."),
            FieldSpec(name="tds", label="Total TDS Deducted",
                      description="Total tax deducted at source."),
        ],
    ),
    DocType.FORM26AS: DocSpec(
        doc_type=DocType.FORM26AS,
        title="Form 26AS (Annual Tax Statement)",
        context_hint=(
            "Form 26AS is a multi-part tax credit statement from the IT department. "
            "PART A / A1 / B: TDS deducted by deductors — sum salary TDS (Part A) "
            "and non-salary TDS (Parts A1/B) separately. "
            "PART C: Tax paid directly — Advance Tax and Self-Assessment Tax with "
            "BSR codes and amounts. "
            "PART D: Refund paid to the taxpayer (if any) — record amount. "
            "PART F: TDS on immovable property purchases u/s 194IA — deducted by "
            "the buyer and credited here; this is a tax credit for the buyer. "
            "PART G / TCS: Tax Collected at Source (e.g. on purchase of car, "
            "gold, foreign remittance LRS) — these are also tax credits. "
            "SUM all TDS on salary rows for total_tds_salary; SUM all non-salary "
            "TDS rows for total_tds_other; SUM all TCS rows for tcs_total."
        ),
        fields=[
            FieldSpec(name="total_tds_salary", label="TDS on Salary",
                      description="Total TDS on salary across all deductors (Part A of Form 26AS)."),
            FieldSpec(name="total_tds_other", label="TDS on Other Income",
                      description="Total TDS on non-salary income (interest, dividend, etc.) — Parts A, A1, B."),
            FieldSpec(name="tcs_total", label="Total TCS",
                      description="Total Tax Collected at Source (Part B of Form 26AS, e.g. on car/property purchase)."),
            FieldSpec(name="advance_tax", label="Advance Tax Paid",
                      description="Total advance tax paid during the year (Part C)."),
            FieldSpec(name="self_assessment_tax", label="Self-Assessment Tax Paid",
                      description="Self-assessment tax paid (Part C)."),
            FieldSpec(name="tds_on_property_purchase", label="TDS on Property Purchase (194IA)",
                      description="TDS deducted by buyer and credited to seller on sale of immovable property (Part F). Acts as a tax credit for the buyer."),
            FieldSpec(name="refund_paid", label="Refund Received",
                      description="Income-tax refund received during the year (Part D). Reported in ITR for information."),
        ],
    ),
    DocType.AIS: DocSpec(
        doc_type=DocType.AIS,
        title="Annual Information Statement (AIS)",
        context_hint=(
            "The AIS is a comprehensive statement from the IT department (incometax.gov.in). "
            "PART A: General info (PAN, name, DOB) — skip. "
            "PART B has multiple sections — scan ALL of them: "
            "(1) TDS/TCS Information: rows of TDS/TCS deducted by various deductors. "
            "   Sum TDS u/s 192 salary rows → salary_reported (use AMOUNT PAID column, not TDS column). "
            "   IMPORTANT: Also look for 'TDS-Ann.II-SAL' section at the end (Part B7) — it shows each employer's "
            "   gross salary separately; sum these for salary_reported. "
            "   TDS u/s 194J or 194JA or 194JB (fees for professional/technical services) → professional_fees (sum all amounts). "
            "   TDS u/s 194A / 194 interest rows → fd_interest or savings_interest. "
            "   TDS u/s 194S (virtual digital assets) → vda_tds. "
            "   TCS rows → tcs_total. "
            "(2) SFT Information (Specified Financial Transactions from banks/brokers/RTA): "
            "   'Sale of securities / units of mutual fund' → sale_of_securities. "
            "   'Purchase of securities / units of mutual funds' → purchase_of_securities. "
            "   'Interest on deposits' → fd_interest. "
            "   'Interest on savings account' → savings_interest. "
            "   Dividend income (SFT-015) → sum all amounts → dividend (prefer SFT total over TDS-section total as it covers all companies). "
            "(3) Payment of Taxes: Advance Tax → advance_tax; Self-Assessment Tax → self_assessment_tax. "
            "(4) Other Information (Part B7): "
            "   Interest on Income-Tax Refund (u/s 244A) → interest_on_it_refund. "
            "   Family pension → family_pension. "
            "   Rent received → rent_received. "
            "   Interest on bonds/debentures → interest_on_bonds. "
            "Sum all TDS amounts (excluding TCS) for tds_total. "
            "The AIS often shows both 'reported value' and 'modified value' — always use the MODIFIED value if present, otherwise use the reported value."
        ),
        fields=[
            FieldSpec(name="salary_reported", label="Salary Reported",
                      description="Salary as reported by employers in the AIS (Annexure II / SFT data)."),
            FieldSpec(name="savings_interest", label="Savings Bank Interest",
                      description="Interest from savings accounts as reported to IT dept by banks."),
            FieldSpec(name="fd_interest", label="Interest on Deposits (FD/RD)",
                      description="Interest from term/recurring deposits."),
            FieldSpec(name="interest_on_bonds", label="Interest on Bonds / Govt Securities",
                      description="Interest on bonds, debentures and government securities."),
            FieldSpec(name="dividend", label="Dividend Income",
                      description="Total dividend received (reported by companies/MFs via TDS returns)."),
            FieldSpec(name="family_pension", label="Family Pension Received",
                      description="Family pension received during the year (taxed under other sources)."),
            FieldSpec(name="interest_on_it_refund", label="Interest on IT Refund (Sec 244A)",
                      description="Interest received from the income-tax dept on a refund. Taxable as other income."),
            FieldSpec(name="rent_received", label="Rent Received",
                      description="Rent received as reported by tenants under SFT / 194I TDS. Indicates let-out property income."),
            FieldSpec(name="sale_of_securities", label="Sale of Securities / MF Units",
                      description="Aggregate sale (credit) value of securities and mutual fund units as reported by broker/RTA."),
            FieldSpec(name="purchase_of_securities", label="Purchase of Securities / MF Units",
                      description="Aggregate purchase (debit) cost of securities and MF units. Helps estimate capital-gains cost basis when no broker P&L is available."),
            FieldSpec(name="vda_tds", label="TDS on Crypto / VDA (Sec 194S)",
                      description="TDS deducted on transfer of virtual digital assets. Indicates crypto income to disclose."),
            FieldSpec(name="advance_tax", label="Advance Tax Paid",
                      description="Advance tax payments as reflected in AIS (cross-check with Form 26AS)."),
            FieldSpec(name="self_assessment_tax", label="Self-Assessment Tax Paid",
                      description="Self-assessment tax as reflected in AIS."),
            FieldSpec(name="tcs_total", label="Total TCS",
                      description="Tax Collected at Source as reflected in AIS (e.g. TCS on car purchase, LRS remittances)."),
            FieldSpec(name="professional_fees", label="Professional / Freelance Income (Sec 194J)",
                      description="Total receipts from professional or technical services under Sec 194J/194JA/194JB (freelance, consulting, etc.). Sum all amounts paid by all deductors."),
            FieldSpec(name="tds_total", label="Total TDS Reported in AIS",
                      description="Aggregate TDS (excluding TCS) as per AIS — use as cross-check against Form 26AS."),
        ],
    ),
    DocType.BROKER_PNL: DocSpec(
        doc_type=DocType.BROKER_PNL,
        title="Broker Tax P&L Statement",
        fields=[
            FieldSpec(name="broker_name", label="Broker Name", type=FieldType.TEXT,
                      description="Name of the broker (Zerodha, Upstox, etc.)."),
            FieldSpec(name="stcg_111a", label="STCG (Listed Equity, Sec 111A)",
                      description="Short-term capital gains on STT-paid listed equity/equity MF."),
            FieldSpec(name="ltcg_112a", label="LTCG (Listed Equity, Sec 112A)",
                      description="Long-term capital gains on STT-paid listed equity/equity MF."),
            FieldSpec(name="stcg_other", label="STCG (Other, slab-rate)",
                      description="Short-term capital gains taxed at slab rate (debt MF, etc.)."),
            FieldSpec(name="ltcg_other", label="LTCG (Other, Sec 112)",
                      description="Long-term capital gains other than 112A."),
            FieldSpec(name="vda_gain", label="Crypto / VDA Gains",
                      description="Net gains from virtual digital assets."),
            FieldSpec(name="dividend", label="Dividend via Broker",
                      description="Dividend credited through the broker."),
        ],
    ),
    DocType.INTEREST_CERT: DocSpec(
        doc_type=DocType.INTEREST_CERT,
        title="Bank Interest Certificate",
        fields=[
            FieldSpec(name="savings_interest", label="Savings Account Interest",
                      description="Interest earned on savings accounts."),
            FieldSpec(name="fd_interest", label="Fixed/Recurring Deposit Interest",
                      description="Interest earned on FD/RD."),
            FieldSpec(name="tds", label="TDS on Interest",
                      description="Tax deducted on interest income."),
        ],
    ),
    DocType.HOME_LOAN_CERT: DocSpec(
        doc_type=DocType.HOME_LOAN_CERT,
        title="Home Loan Interest Certificate",
        fields=[
            FieldSpec(name="principal_repaid", label="Principal Repaid (80C)",
                      description="Principal repayment eligible u/s 80C."),
            FieldSpec(name="interest_paid", label="Interest Paid (Sec 24b)",
                      description="Interest paid on housing loan, deductible u/s 24(b)."),
            FieldSpec(name="is_self_occupied", label="Self-Occupied Property", type=FieldType.TEXT,
                      description="'yes' if the property is self-occupied, else 'no'."),
            FieldSpec(name="let_out_annual_rent", label="Annual Rent Received (let-out)",
                      description="Total rent received for the year if the property is let out."),
            FieldSpec(name="municipal_taxes", label="Municipal Taxes Paid",
                      description="Municipal/property taxes paid (deductible from let-out annual value)."),
        ],
    ),
    DocType.DEDUCTION_PROOF: DocSpec(
        doc_type=DocType.DEDUCTION_PROOF,
        title="Deduction Proof (80C / 80D / NPS / others)",
        fields=[
            FieldSpec(name="amount_80c", label="80C Investment Amount",
                      description="Eligible 80C investment (ELSS, PPF, LIC, etc.)."),
            FieldSpec(name="amount_80ccd1b", label="80CCD(1B) NPS Amount",
                      description="Self NPS contribution u/s 80CCD(1B)."),
            FieldSpec(name="amount_80d_self", label="80D Premium (Self/Family)",
                      description="Health insurance premium for self and family."),
            FieldSpec(name="amount_80d_parents", label="80D Premium (Parents)",
                      description="Health insurance premium for parents."),
            FieldSpec(name="amount_80e", label="80E Education Loan Interest",
                      description="Interest paid on an education loan (no upper cap)."),
            FieldSpec(name="amount_80eea", label="80EEA Additional Home-Loan Interest",
                      description="Additional interest on an affordable-housing loan."),
            FieldSpec(name="amount_80dd", label="80DD Disabled Dependent",
                      description="Maintenance/medical of a disabled dependent."),
            FieldSpec(name="amount_80dd_severe", label="80DD Severe Disability (yes/no)",
                      type=FieldType.TEXT, description="'yes' if dependent disability is 80%+ (severe)."),
            FieldSpec(name="amount_80ddb", label="80DDB Specified-Disease Treatment",
                      description="Expenditure on treatment of specified diseases."),
            FieldSpec(name="amount_80u", label="80U Self Disability",
                      description="Deduction for the taxpayer's own disability."),
            FieldSpec(name="amount_80u_severe", label="80U Severe Disability (yes/no)",
                      type=FieldType.TEXT, description="'yes' if the taxpayer's disability is 80%+ (severe)."),
            FieldSpec(name="amount_80gg", label="80GG Rent Paid (no HRA)",
                      description="Annual rent paid when no HRA is received."),
        ],
    ),
    DocType.RENT_RECEIPT: DocSpec(
        doc_type=DocType.RENT_RECEIPT,
        title="Rent Receipt / HRA Proof",
        fields=[
            FieldSpec(name="hra_received", label="HRA Received",
                      description="House Rent Allowance received from the employer for the year."),
            FieldSpec(name="rent_paid", label="Annual Rent Paid",
                      description="Total rent paid by the taxpayer during the year."),
            FieldSpec(name="basic_da", label="Basic Salary + DA",
                      description="Annual basic salary plus dearness allowance (HRA base)."),
            FieldSpec(name="is_metro", label="Metro City (yes/no)", type=FieldType.TEXT,
                      description="'yes' if the rented home is in a metro city (Delhi/Mumbai/Kolkata/Chennai)."),
        ],
    ),
    DocType.DONATION_80G: DocSpec(
        doc_type=DocType.DONATION_80G,
        title="80G Donation Receipt",
        fields=[
            FieldSpec(name="donation_100_no_limit", label="100% Deductible (no limit)",
                      description="Donations eligible for 100% deduction without a qualifying limit (e.g. PM CARES, National Defence Fund)."),
            FieldSpec(name="donation_50_no_limit", label="50% Deductible (no limit)",
                      description="Donations eligible for 50% deduction without a qualifying limit (e.g. PM's Drought Relief Fund)."),
            FieldSpec(name="donation_100_limit", label="100% Deductible (with 10% limit)",
                      description="Donations eligible for 100% deduction subject to 10%-of-adjusted-GTI limit."),
            FieldSpec(name="donation_50_limit", label="50% Deductible (with 10% limit)",
                      description="Donations eligible for 50% deduction subject to 10%-of-adjusted-GTI limit (most charities)."),
        ],
    ),
}
