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
        fields=[
            FieldSpec(name="employer_name", label="Employer Name", type=FieldType.TEXT,
                      description="Name of the employer / deductor.", required=True),
            FieldSpec(name="employer_tan", label="Employer TAN", type=FieldType.TEXT,
                      description="TAN of the employer."),
            FieldSpec(name="gross_salary", label="Gross Salary (Sec 17(1)+17(2)+17(3))",
                      description="Total gross salary including perquisites and profits in lieu.",
                      required=True),
            FieldSpec(name="exempt_allowances", label="Exempt Allowances u/s 10",
                      description="Total allowances exempt under section 10 (HRA, LTA, etc.)."),
            FieldSpec(name="standard_deduction", label="Standard Deduction",
                      description="Standard deduction u/s 16(ia)."),
            FieldSpec(name="professional_tax", label="Professional Tax u/s 16(iii)",
                      description="Professional tax / tax on employment."),
            FieldSpec(name="deduction_80c", label="Deduction u/s 80C",
                      description="Aggregate 80C deduction reported by employer."),
            FieldSpec(name="deduction_80ccd1b", label="Deduction u/s 80CCD(1B) (NPS self)",
                      description="Additional NPS contribution deduction."),
            FieldSpec(name="deduction_80ccd2", label="Deduction u/s 80CCD(2) (Employer NPS)",
                      description="Employer contribution to NPS."),
            FieldSpec(name="deduction_80d", label="Deduction u/s 80D (Health Insurance)",
                      description="Medical insurance premium deduction."),
            FieldSpec(name="tds", label="Total TDS Deducted",
                      description="Total tax deducted at source on salary.", required=True),
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
        fields=[
            FieldSpec(name="total_tds_salary", label="TDS on Salary",
                      description="Total TDS on salary across all deductors."),
            FieldSpec(name="total_tds_other", label="TDS on Other Income",
                      description="Total TDS on non-salary income (interest, dividend, etc.)."),
            FieldSpec(name="advance_tax", label="Advance Tax Paid",
                      description="Total advance tax paid during the year."),
            FieldSpec(name="self_assessment_tax", label="Self-Assessment Tax Paid",
                      description="Self-assessment tax paid."),
        ],
    ),
    DocType.AIS: DocSpec(
        doc_type=DocType.AIS,
        title="Annual Information Statement (AIS)",
        fields=[
            FieldSpec(name="salary_reported", label="Salary Reported",
                      description="Salary as reported by employers in AIS."),
            FieldSpec(name="savings_interest", label="Savings Bank Interest",
                      description="Interest from savings accounts."),
            FieldSpec(name="fd_interest", label="Interest on Deposits (FD/RD)",
                      description="Interest from term deposits."),
            FieldSpec(name="dividend", label="Dividend Income",
                      description="Total dividend received."),
            FieldSpec(name="family_pension", label="Family Pension Received",
                      description="Family pension received during the year (taxed under other sources)."),
            FieldSpec(name="sale_of_securities", label="Sale of Securities / Units",
                      description="Aggregate sale value of securities reported."),
            FieldSpec(name="tds_total", label="Total TDS/TCS Reported",
                      description="Total TDS/TCS reported in AIS."),
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
