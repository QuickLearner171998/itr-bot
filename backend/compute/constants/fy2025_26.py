"""Tax constants for FY 2025-26 (AY 2026-27).

All slabs, caps, and rates live here so a future year is a drop-in new module.
Slabs are expressed as ``(upper_bound, rate)`` tuples evaluated cumulatively;
``None`` upper bound means "and above".

Sources: Income Tax Act 1961 as amended by Finance Act 2025 (new-regime slabs,
Sec 87A rebate up to 12,00,000), and post-Budget-2024 capital-gains rates that
apply for the full FY 2025-26.
"""

from __future__ import annotations

Slab = tuple[float | None, float]

FINANCIAL_YEAR = "2025-26"
ASSESSMENT_YEAR = "2026-27"

# --- New regime (Sec 115BAC, default) ---------------------------------------
NEW_REGIME_SLABS: list[Slab] = [
    (400000, 0.00),
    (800000, 0.05),
    (1200000, 0.10),
    (1600000, 0.15),
    (2000000, 0.20),
    (2400000, 0.25),
    (None, 0.30),
]
NEW_STANDARD_DEDUCTION = 75000.0
# Sec 87A: full rebate if total income (excluding special-rate income) <= 12,00,000.
NEW_REBATE_INCOME_LIMIT = 1200000.0
NEW_REBATE_MAX = 60000.0  # tax on 12,00,000 under new slabs
NEW_FAMILY_PENSION_DEDUCTION_CAP = 25000.0

# --- Old regime --------------------------------------------------------------
OLD_REGIME_SLABS: list[Slab] = [
    (250000, 0.00),
    (500000, 0.05),
    (1000000, 0.20),
    (None, 0.30),
]
OLD_STANDARD_DEDUCTION = 50000.0
OLD_REBATE_INCOME_LIMIT = 500000.0
OLD_REBATE_MAX = 12500.0
# Age-based basic exemption (old regime only).
OLD_BASIC_EXEMPTION_SENIOR = 300000.0       # 60-79
OLD_BASIC_EXEMPTION_SUPER_SENIOR = 500000.0  # 80+
SENIOR_AGE = 60
SUPER_SENIOR_AGE = 80

# --- Chapter VIA deduction caps (old regime) --------------------------------
CAP_80C = 150000.0
CAP_80CCD1B = 50000.0
CAP_80D_SELF = 25000.0
CAP_80D_SELF_SENIOR = 50000.0
CAP_80D_PARENTS = 25000.0
CAP_80D_PARENTS_SENIOR = 50000.0
CAP_80TTA = 10000.0          # savings interest, < 60
CAP_80TTB = 50000.0          # interest, seniors
HOME_LOAN_SELF_OCCUPIED_CAP = 200000.0  # Sec 24(b) self-occupied

# 80CCD(2) employer NPS: % of salary, allowed in BOTH regimes.
EMPLOYER_NPS_CAP_RATE = 0.14  # new regime 14%; old regime 10% (private)
EMPLOYER_NPS_CAP_RATE_OLD = 0.10

# House property standard deduction.
HOUSE_PROPERTY_STD_DEDUCTION_RATE = 0.30

# --- Capital gains rates -----------------------------------------------------
STCG_111A_RATE = 0.20
LTCG_112A_RATE = 0.125
LTCG_112A_EXEMPTION = 125000.0
LTCG_OTHER_RATE = 0.125
VDA_RATE = 0.30

# --- Surcharge ---------------------------------------------------------------
# (threshold, rate) evaluated on the relevant income; marginal relief applied.
SURCHARGE_SLABS: list[tuple[float, float]] = [
    (5000000, 0.00),
    (10000000, 0.10),
    (20000000, 0.15),
    (50000000, 0.25),
    (float("inf"), 0.37),
]
# New regime caps surcharge at 25%.
NEW_REGIME_SURCHARGE_CAP = 0.25
# Surcharge on 111A/112A capital gains is capped at 15%.
CG_SURCHARGE_CAP = 0.15

# --- Cess --------------------------------------------------------------------
HEALTH_EDUCATION_CESS = 0.04
