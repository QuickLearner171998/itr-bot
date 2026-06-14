"""Schemas for the deterministic tax computation engine.

``TaxInput`` is the canonical, regime-agnostic consolidation of every income
head and deduction (built by merging validated documents). ``RegimeResult``
holds a fully traced computation for one regime; ``TaxComputation`` compares
both regimes and records the independent re-compute verification.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SalaryComponent(BaseModel):
    """Salary from a single employer (supports job changes in a year)."""

    employer_name: str = "Employer"
    gross_salary: float = 0.0
    exempt_allowances: float = 0.0
    professional_tax: float = 0.0
    tds: float = 0.0


class CapitalGains(BaseModel):
    stcg_111a: float = 0.0      # listed equity STT-paid, 20%
    ltcg_112a: float = 0.0      # listed equity STT-paid, 12.5% over 1.25L
    stcg_other: float = 0.0     # slab rate
    ltcg_other: float = 0.0     # 12.5% (Sec 112)
    vda_gain: float = 0.0       # crypto, flat 30%


class Deductions(BaseModel):
    amount_80c: float = 0.0
    amount_80ccd1b: float = 0.0      # NPS self (extra 50k)
    amount_80ccd2: float = 0.0       # employer NPS (allowed in both regimes)
    amount_80d_self: float = 0.0
    amount_80d_parents: float = 0.0
    amount_80tta: float = 0.0        # savings interest (derived)
    home_loan_interest: float = 0.0  # Sec 24(b)
    home_loan_self_occupied: bool = True
    home_loan_principal: float = 0.0  # part of 80C

    # Additional old-regime Chapter VIA deductions.
    amount_80e: float = 0.0          # education loan interest (no cap)
    amount_80eea: float = 0.0        # additional home-loan interest (affordable)
    amount_80dd: float = 0.0         # disabled dependent
    amount_80dd_severe: bool = False
    amount_80ddb: float = 0.0        # specified-disease treatment
    amount_80u: float = 0.0          # self disability
    amount_80u_severe: bool = False
    amount_80gg: float = 0.0         # rent paid (no HRA); raw rent, capped in engine

    # 80G donations, pre-classified by deduction category.
    donation_100_no_limit: float = 0.0
    donation_50_no_limit: float = 0.0
    donation_100_limit: float = 0.0   # subject to 10%-of-adjusted-GTI qualifying limit
    donation_50_limit: float = 0.0


class TaxInput(BaseModel):
    """Canonical consolidated input for the tax engine."""

    age: int = 30

    # Filing regime, inferred from Form 16 ("old"/"new"). New regime is the
    # statutory default when Form 16 does not state one.
    filing_regime: str = "new"

    salaries: list[SalaryComponent] = Field(default_factory=list)
    house_property_income: float = 0.0  # net (can be negative for self-occupied loss)

    # Let-out house property (drives net income when provided). If annual_rent is
    # set, net income is computed (NAV less municipal taxes, 30% std deduction,
    # 24(b) interest); otherwise ``house_property_income`` is used directly.
    let_out_annual_rent: float = 0.0
    let_out_municipal_taxes: float = 0.0

    # HRA exemption inputs (old regime, Sec 10(13A)). Use only for HRA not already
    # exempted inside Form 16's ``exempt_allowances``.
    hra_received: float = 0.0
    hra_rent_paid: float = 0.0
    hra_basic_da: float = 0.0
    hra_is_metro: bool = False

    savings_interest: float = 0.0
    fd_interest: float = 0.0
    dividend: float = 0.0
    family_pension: float = 0.0  # taxed under other sources; 1/3 std deduction
    other_income: float = 0.0

    agricultural_income: float = 0.0  # for rate purposes (partial integration)
    brought_forward_loss: float = 0.0  # set off against current income (old regime)

    capital_gains: CapitalGains = Field(default_factory=CapitalGains)
    deductions: Deductions = Field(default_factory=Deductions)

    # Taxes already paid and reliefs.
    tds_total: float = 0.0
    advance_tax: float = 0.0
    self_assessment_tax: float = 0.0
    relief_89: float = 0.0       # arrears relief
    relief_90_91: float = 0.0    # foreign tax credit (DTAA / unilateral)


class Discrepancy(BaseModel):
    """A figure that two source documents report differently.

    Surfaced to the user for confirmation instead of silently picking a value;
    ``chosen`` is the safe prefill (the larger figure to avoid under-reporting)
    but the user may override it on the review screen.
    """

    field: str                       # logical TaxInput key (e.g. "tds_total")
    label: str                       # human-readable field name
    sources: list[dict] = Field(default_factory=list)  # [{"doc","value"}]
    chosen: float = 0.0              # prefilled value
    note: str = ""


class ComputeStep(BaseModel):
    """One labelled line in the income-to-tax trace (drives the waterfall)."""

    key: str
    label: str
    amount: float
    kind: str = "add"  # add | subtract | total | tax | info


class RegimeResult(BaseModel):
    """Fully traced computation for one regime."""

    regime: str  # "old" | "new"
    steps: list[ComputeStep] = Field(default_factory=list)

    gross_total_income: float = 0.0
    total_deductions: float = 0.0
    total_income: float = 0.0
    tax_before_rebate: float = 0.0
    rebate_87a: float = 0.0
    surcharge: float = 0.0
    marginal_relief: float = 0.0
    cess: float = 0.0
    total_tax_liability: float = 0.0
    taxes_paid: float = 0.0
    refund_or_payable: float = 0.0  # positive => payable, negative => refund


class TaxComputation(BaseModel):
    """Computation for the chosen regime plus verification metadata."""

    result: RegimeResult
    regime: str  # "old" | "new" (the regime the user is filing under)
    verified: bool = False
    verification_note: str = ""
