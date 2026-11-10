"""Baseline model parameters. Hypotheses, not optimized weights."""

SECTORS = {
    "XLK": "Information Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLU": "Utilities",
}
BENCHMARK = "SPY"

# Each entry: metric -> (within-sector weight, higher_is_cheap).
# Sector-level metrics are indicative proxies only; production requires subindustry
# decomposition for XLF/XLC/XLY/XLRE and normalized cycle assumptions for XLE/XLB.
VALUATION_METRICS = {
    "XLK": {"forward_pe": (.50, False), "fcf_yield": (.30, True), "ev_ebit": (.20, False)},
    "XLC": {"forward_pe": (.50, False), "fcf_yield": (.30, True), "ev_ebitda": (.20, False)},
    "XLY": {"forward_pe": (.50, False), "fcf_yield": (.30, True), "ev_ebitda": (.20, False)},
    "XLP": {"forward_pe": (.50, False), "fcf_yield": (.30, True), "dividend_yield": (.20, True)},
    "XLE": {"midcycle_fcf_yield": (.50, True), "ev_ebitda": (.30, False), "ev_dacf": (.20, False)},
    "XLF": {"p_tbv": (.50, False), "forward_pe": (.30, False), "dividend_yield": (.20, True)},
    "XLV": {"forward_pe": (.50, False), "ev_ebitda": (.30, False), "fcf_yield": (.20, True)},
    "XLI": {"midcycle_ev_ebitda": (.50, False), "forward_pe": (.30, False), "fcf_yield": (.20, True)},
    "XLB": {"midcycle_ev_ebitda": (.50, False), "midcycle_fcf_yield": (.30, True), "forward_pe": (.20, False)},
    "XLRE": {"p_affo": (.50, False), "implied_cap_rate": (.30, True), "dividend_yield": (.20, True)},
    "XLU": {"forward_pe": (.50, False), "dividend_yield": (.30, True), "ev_ebitda": (.20, False)},
}

OPPORTUNITY_WEIGHTS = {"valuation": .40, "momentum": .25, "revision": .20, "quality": .15}
MOMENTUM_WEIGHTS = {"rs_6_1": .35, "rs_12_1": .35, "rs_3m": .15, "breadth": .15}
REVISION_WEIGHTS = {"revision_3m": .45, "revision_1m": .30, "revision_breadth": .25}
QUALITY_WEIGHTS = {"profitability": .40, "financial_strength": .35, "growth_durability": .25}

MIN_HISTORY = 48
HISTORY_MONTHS = 84
MAX_STALE_DAYS = 45
