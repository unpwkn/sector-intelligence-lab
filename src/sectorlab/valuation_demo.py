"""Generate fictional valuations for offline tests and the explicit demo mode."""

import csv
import io
import math
from datetime import timedelta

import pandas as pd

from .config import BENCHMARK, VALUATION_METRICS
from .valuation_data import BENCHMARK_METRICS, CSV_COLUMNS, MARKET_TZ, YIELD_METRICS

DEMO_ASOF = "2026-01-04T00:00:00Z"

def synthetic_valuation_csv() -> bytes:
    """Return the same synthetic, clearly labeled CSV on every run."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    universe = VALUATION_METRICS | {BENCHMARK: sorted(BENCHMARK_METRICS)}
    observations = [
        (month, (month.end_time.normalize() + timedelta(hours=16)).tz_localize(MARKET_TZ))
        for month in pd.period_range("2019-01", "2025-12", freq="M")
    ]
    for index, (ticker, metrics) in enumerate(universe.items()):
        for metric in metrics:
            for i, (month, observed) in enumerate(observations):
                published = observed + timedelta(days=1)
                wave = math.sin(i / (5 + index % 4) + index) + 0.2 * math.cos(i / 2)
                value = (0.04 + 0.01 * wave) if metric in YIELD_METRICS else (16 + 5 * wave)
                writer.writerow({
                    "ticker": ticker,
                    "metric": metric,
                    "observation_date": observed.date().isoformat(),
                    "observed_at": observed.isoformat(),
                    "value": f"{value:.8f}",
                    "denominator": 100,
                    "fiscal_period": "FY1" if metric == "forward_pe" else "demo_period",
                    "metric_definition": f"synthetic:{metric}:v1",
                    "vendor": "synthetic",
                    "source": "sectorlab deterministic fixture",
                    "provenance": f"fictional:{ticker}:{metric}:{month}",
                    "published_at": published.isoformat(),
                    "available_at": (published + timedelta(hours=1)).isoformat(),
                    "history_type": "synthetic",
                    "data_license": "synthetic",
                })
    return output.getvalue().encode("utf-8")
