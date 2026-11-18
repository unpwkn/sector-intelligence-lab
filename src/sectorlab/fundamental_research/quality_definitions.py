"""Versioned issuer-level quality definitions and research scoring anchors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    formula: str
    weak: float
    strong: float
    minimum: float | None = None
    maximum: float | None = None
    year_count: int | None = None

    def score(self, ratio: float) -> float:
        return 100 * min(1.0, max(0.0, (ratio - self.weak) / (self.strong - self.weak)))


# All flows are trailing twelve months at the anchor period end unless specified.
# Anchors are illustrative economic thresholds and deliberately uncalibrated.
METRICS = {
    "roic": Metric("NOPAT / average operating invested capital", 0, 0.20),
    "fcf_margin": Metric("(CFO less cash capex and share-based compensation) / revenue", 0, 0.20),
    "margin_retention": Metric(
        "Lowest annual operating margin / highest margin, last 3 FYs", 0, 0.85, maximum=1
    ),
    "net_debt_ebitda": Metric("(Debt less unrestricted cash) / EBITDA", 5, 0),
    "gross_margin": Metric("Gross profit / revenue", 0.10, 0.55, maximum=1),
    "capex_coverage": Metric(
        "Operating cash flow / (cash capex plus cash common dividends)", 0.7, 2
    ),
    "demand_retention": Metric(
        "Lowest annual real revenue / highest revenue, last 3 FYs", 0.65, 0.98, 0, 1
    ),
    "interest_coverage": Metric("EBIT / gross cash interest expense", 1, 8),
    "margin": Metric("Operating income / revenue", 0, 0.25, maximum=1),
    "positive_fcf_years": Metric(
        "FYs with positive CFO less cash capex / last 3 completed FYs", 0, 1, 0, 1, year_count=3
    ),
    "midcycle_roic": Metric(
        "Sum FY NOPAT / sum FY average operating invested capital, last 5 FYs", -0.02, 0.15
    ),
    "midcycle_leverage": Metric("Current net debt / mean annual EBITDA over last 5 FYs", 5, 0),
    "midcycle_fcf_years": Metric(
        "FYs with positive CFO less cash capex / last 5 completed FYs", 0.2, 1, 0, 1, year_count=5
    ),
    "midstream_coverage": Metric(
        "(EBITDA less cash interest and sustaining cash capex) / cash common dividends", 1, 2.5
    ),
    "rotce": Metric(
        "(Net income available to common plus after-tax intangible amortization) / "
        "average tangible common equity",
        0,
        0.20,
    ),
    "cet1": Metric(
        "CET1 regulatory capital / risk-weighted assets at anchor end", 0.07, 0.15, 0, 1
    ),
    "npl": Metric(
        "Nonaccrual and 90+ days past-due accruing loans / gross loans at anchor end",
        0.06,
        0.005,
        0,
        1,
    ),
    "bank_ppnr_retention": Metric(
        "Lowest annual pre-provision net revenue / highest, last 3 FYs", 0, 0.9, maximum=1
    ),
    "insurer_roe": Metric("Net income available to common / average common equity", 0, 0.15),
    "pc_combined_ratio": Metric(
        "(Incurred losses, loss adjustment and underwriting expenses) / net earned premiums",
        1.12,
        0.90,
        0,
    ),
    "insurer_rbc": Metric(
        "Consolidated applicable statutory total adjusted capital / "
        "authorized control level RBC on a consistently mapped insurer basis",
        2,
        5,
        0,
    ),
    "insurance_premium_retention": Metric(
        "Lowest annual real earned premiums / highest premiums, last 3 FYs", 0.7, 0.98, 0, 1
    ),
    "affo_coverage": Metric("Normalized AFFO / cash common dividends", 0.8, 1.7),
    "debt_due_24m": Metric(
        "Gross principal due in next 24 months / total gross debt at anchor end", 0.45, 0.05, 0, 1
    ),
    "unfinanced_maturities": Metric(
        "Debt principal due within 24 months less unrestricted cash "
        "and committed undrawn facilities / gross debt",
        0.35,
        0,
        maximum=1,
    ),
    "same_property_noi_retention": Metric(
        "Lowest annual same-property NOI / highest NOI over "
        "last 3 FYs for a consistently matched pool",
        0.65,
        0.98,
        maximum=1,
    ),
    "regulated_earnings_share": Metric(
        "Positive regulated segment operating earnings / total positive segment operating earnings",
        0.45,
        0.95,
        0,
        1,
    ),
    "utility_dividend_coverage": Metric(
        "Net income available to common / cash common dividends", 1, 2.5
    ),
    "ffo_debt": Metric(
        "(CFO before working-capital changes) / gross debt at anchor end", 0.05, 0.25
    ),
    "utility_earnings_retention": Metric(
        "Lowest annual regulated segment operating earnings / highest, last 3 FYs",
        0.55,
        0.95,
        maximum=1,
    ),
    "backlog_coverage": Metric(
        "Firm funded backlog expected to convert in next 12 months / trailing 12-month revenue",
        0.1,
        0.8,
        0,
    ),
    "rd_funding": Metric("(CFO plus expensed R&D less cash capex) / expensed R&D", 0.5, 3),
    "margin_floor": Metric(
        "Lowest annual operating income / same-year revenue, last 3 FYs", -0.05, 0.18, maximum=1
    ),
}

# Tuples are ordered profitability, financial strength, growth durability.
# A pillar with multiple metrics gives each equal weight and requires all of them.
QUALITY_MODELS = {
    "XLK": {
        "software": (("roic", "fcf_margin"), ("net_debt_ebitda",), ("margin_retention",)),
        "hardware": (("roic", "fcf_margin"), ("interest_coverage",), ("margin_floor",)),
        "semiconductors": (
            ("midcycle_roic", "fcf_margin"),
            ("midcycle_leverage",),
            ("midcycle_fcf_years", "margin_retention"),
        ),
    },
    "XLC": {
        "telecom": (("margin",), ("net_debt_ebitda", "capex_coverage"), ("demand_retention",)),
        "media": (("fcf_margin",), ("interest_coverage",), ("positive_fcf_years",)),
        "platforms": (("roic", "fcf_margin"), ("net_debt_ebitda",), ("margin_floor",)),
    },
    "XLY": {
        "retail": (("roic",), ("capex_coverage",), ("demand_retention",)),
        "autos": (("midcycle_roic",), ("midcycle_leverage",), ("midcycle_fcf_years",)),
        "services": (("margin",), ("interest_coverage",), ("demand_retention",)),
    },
    "XLP": {
        "staples_producers": (("gross_margin", "roic"), ("capex_coverage",), ("margin_floor",)),
        "staples_retail": (("roic",), ("interest_coverage",), ("demand_retention",)),
    },
    "XLE": {
        "upstream_integrated": (
            ("midcycle_roic",),
            ("midcycle_leverage",),
            ("midcycle_fcf_years",),
        ),
        "refining_services": (
            ("midcycle_roic",),
            ("midcycle_leverage",),
            ("midcycle_fcf_years", "margin_floor"),
        ),
        "midstream": (("roic",), ("midstream_coverage", "net_debt_ebitda"), ("demand_retention",)),
    },
    "XLF": {
        "banks": (("rotce",), ("cet1", "npl"), ("bank_ppnr_retention",)),
        "property_casualty": (
            ("insurer_roe", "pc_combined_ratio"),
            ("insurer_rbc",),
            ("insurance_premium_retention",),
        ),
        "life_insurance": (("insurer_roe",), ("insurer_rbc",), ("insurance_premium_retention",)),
        "fee_financials": (("margin",), ("interest_coverage",), ("margin_floor",)),
    },
    "XLV": {
        "profitable_biopharma": (("margin",), ("rd_funding",), ("margin_floor",)),
        "devices": (("gross_margin", "roic"), ("interest_coverage",), ("demand_retention",)),
        "providers": (("margin",), ("capex_coverage",), ("positive_fcf_years",)),
    },
    "XLI": {
        "contract_industrials": (("roic",), ("interest_coverage",), ("backlog_coverage",)),
        "transport": (("midcycle_roic",), ("capex_coverage",), ("midcycle_fcf_years",)),
        "commercial_industrials": (("roic",), ("net_debt_ebitda",), ("margin_floor",)),
    },
    "XLB": {
        "commodity_materials": (
            ("midcycle_roic",),
            ("midcycle_leverage",),
            ("midcycle_fcf_years",),
        ),
        "specialty_materials": (("gross_margin",), ("interest_coverage",), ("margin_retention",)),
    },
    "XLRE": {
        "equity_reits": (
            ("affo_coverage",),
            ("debt_due_24m", "unfinanced_maturities"),
            ("same_property_noi_retention",),
        ),
        "property_services": (("margin",), ("interest_coverage",), ("margin_floor",)),
    },
    "XLU": {
        "regulated_utilities": (
            ("regulated_earnings_share", "utility_dividend_coverage"),
            ("ffo_debt",),
            ("utility_earnings_retention",),
        ),
        "merchant_power": (("midcycle_roic",), ("midcycle_leverage",), ("midcycle_fcf_years",)),
    },
}
