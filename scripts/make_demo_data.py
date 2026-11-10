"""Generate deterministic, entirely SYNTHETIC inputs for a reproducible demo.

Never present the resulting scores as real ETF research or historical performance.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from sectorlab.config import BENCHMARK, SECTORS, VALUATION_METRICS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "demo"
OUT.mkdir(parents=True, exist_ok=True)
RNG = np.random.default_rng(20261110)
ASOF = pd.Timestamp("2026-10-30")


def metric_baseline(metric: str) -> float:
    return {
        "forward_pe": 22, "ev_ebit": 18, "ev_ebitda": 13,
        "midcycle_ev_ebitda": 11, "p_tbv": 1.8, "p_affo": 19,
        "fcf_yield": .045, "midcycle_fcf_yield": .06,
        "dividend_yield": .028, "ev_dacf": 7.5,
        "implied_cap_rate": .055,
    }[metric]


months = pd.date_range("2017-01-31", ASOF, freq="BME")
valuations = []
for ticker, metric_specs in VALUATION_METRICS.items():
    for metric in metric_specs:
        baseline = metric_baseline(metric) * RNG.uniform(.75, 1.25)
        process = 0.0
        for month in months:
            process = .88 * process + RNG.normal(0, .065)
            val = baseline * np.exp(process)
            valuations.append({"date": month.date(), "ticker": ticker, "metric": metric,
                               "value": round(float(val), 8), "available_at": month.date()})
pd.DataFrame(valuations).to_csv(OUT / "valuations.csv", index=False)

business_days = pd.bdate_range("2016-01-01", ASOF)
prices = []
common = RNG.normal(.00025, .009, len(business_days))
for ticker in [BENCHMARK, *SECTORS]:
    beta = 1.0 if ticker == BENCHMARK else RNG.uniform(.7, 1.3)
    residual = RNG.normal(.00005, .005, len(business_days))
    simulated = 100 * np.exp(np.cumsum(beta * common + residual))
    prices.extend({"date": day.date(), "ticker": ticker,
                   "adjusted_price_proxy": round(float(price), 6),
                   "available_at": day.date()}
                  for day, price in zip(business_days, simulated))
pd.DataFrame(prices).to_csv(OUT / "prices.csv", index=False)

revisions, breadth, quality = [], [], []
for ticker in SECTORS:
    revisions.append({"date": ASOF.date(), "available_at": ASOF.date(), "ticker": ticker,
                      "revision_1m": float(RNG.normal(.004, .015)),
                      "revision_3m": float(RNG.normal(.012, .04)),
                      "revision_breadth": float(RNG.uniform(.2, .8))})
    breadth.append({"date": ASOF.date(), "available_at": ASOF.date(), "ticker": ticker,
                    "breadth": float(RNG.uniform(.2, .8))})
    quality.append({"date": ASOF.date(), "available_at": ASOF.date(), "ticker": ticker,
                    "profitability": round(float(RNG.uniform(30, 95)), 2),
                    "financial_strength": round(float(RNG.uniform(30, 95)), 2),
                    "growth_durability": round(float(RNG.uniform(30, 95)), 2)})
for name, records in (("revisions.csv", revisions), ("breadth.csv", breadth),
                      ("quality.csv", quality)):
    pd.DataFrame(records).to_csv(OUT / name, index=False)
(OUT / "SYNTHETIC_DATA.txt").write_text(
    "ALL DATA IN THIS DIRECTORY ARE SIMULATED. NOT ACTUAL ETF PRICES, VALUATIONS, "
    "EARNINGS ESTIMATES OR INVESTMENT RESULTS. DO NOT CITE AS HISTORICAL EVIDENCE.\n",
    encoding="utf-8",
)
print("Created synthetic demo inputs in", OUT)
