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


class TaxInput(BaseModel):
    """Canonical consolidated input for the tax engine."""

    age: int = 30

    salaries: list[SalaryComponent] = Field(default_factory=list)
    house_property_income: float = 0.0  # net (can be negative for self-occupied loss)

    savings_interest: float = 0.0
    fd_interest: float = 0.0
    dividend: float = 0.0
    other_income: float = 0.0

    capital_gains: CapitalGains = Field(default_factory=CapitalGains)
    deductions: Deductions = Field(default_factory=Deductions)

    # Taxes already paid.
    tds_total: float = 0.0
    advance_tax: float = 0.0
    self_assessment_tax: float = 0.0


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
    """Comparison of both regimes plus verification metadata."""

    old: RegimeResult
    new: RegimeResult
    recommended_regime: str
    recommended_savings: float = 0.0
    verified: bool = False
    verification_note: str = ""
