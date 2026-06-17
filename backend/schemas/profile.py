"""User profile and questionnaire schemas.

The intake questionnaire answers populate ``UserProfile``; a deterministic rule
engine then reads the profile to pick the ITR form and the document checklist.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ResidentialStatus(str, Enum):
    RESIDENT = "resident"
    RNOR = "rnor"
    NON_RESIDENT = "non_resident"


class ITRForm(str, Enum):
    ITR1 = "ITR-1"
    ITR2 = "ITR-2"
    UNSUPPORTED = "unsupported"


class UserProfile(BaseModel):
    """Structured answers from the intake questionnaire.

    Every flag maps to an eligibility rule for ITR-1 vs ITR-2 or to a required
    document. Defaults are the simplest (single-employer salaried) case.
    """

    # Personal / residency.
    residential_status: ResidentialStatus = ResidentialStatus.RESIDENT
    age: int = 30

    # Salary.
    changed_jobs: bool = False
    num_employers: int = 1

    # Income > 50 lakh (forces ITR-2).
    total_income_above_50l: bool = False

    # House property.
    num_house_properties: int = 0
    has_let_out_property: bool = False
    has_home_loan: bool = False

    # Capital gains / investments.
    has_capital_gains: bool = False
    has_stcg: bool = False
    ltcg_112a_above_125k: bool = False
    has_unlisted_shares: bool = False
    has_crypto_vda: bool = False
    has_rsu_esop: bool = False

    # Other sources.
    has_savings_interest: bool = False
    has_fd_interest: bool = False
    has_dividends: bool = False

    # Retirement / deductions.
    has_pf: bool = False
    has_nps: bool = False
    has_employer_nps: bool = False
    claims_80c: bool = False
    claims_80d: bool = False

    # Professional / freelance income (Sec 44ADA / 194J) — disqualifies ITR-1.
    has_professional_income: bool = False

    # Disqualifiers for ITR-1.
    is_company_director: bool = False
    has_foreign_assets_income: bool = False
    has_brought_forward_losses: bool = False
    agricultural_income_above_5k: bool = False

    # Questions the user answered "not sure" on; resolved from documents later.
    unsure_fields: list[str] = Field(default_factory=list)


class FormDecision(BaseModel):
    """Output of the deterministic form-selection rule engine."""

    form: ITRForm
    reasons: list[str] = Field(default_factory=list)


class ChecklistItem(BaseModel):
    """One required document plus instructions on how to obtain it."""

    doc_type: str
    title: str
    required: bool
    why: str
    how_to_get: list[str]
    source: str
    covered_by: str | None = None  # e.g. "AIS" when data already extracted
