"""Offline deterministic synthetic Phase 4 fixture. All values are fictional."""

import csv
import io
from itertools import pairwise

import pandas as pd

from ..config import SECTORS
from .data import CSV_COLUMNS, RESIDUAL
from .quality_definitions import METRICS, QUALITY_MODELS

DEMO_ASOF = "2024-10-31T22:00:00Z"


def _record(kind: str, issuer: str, day: str, **fields) -> dict:
    return (
        dict.fromkeys(CSV_COLUMNS, "")
        | {
            "kind": kind,
            "issuer_id": issuer,
            "observation_date": day,
            "observed_at": f"{day}T17:00:00Z",
            "published_at": f"{day}T18:00:00Z",
            "available_at": f"{day}T19:00:00Z",
            "status": "valid",
            "vendor": "SYNTHETIC_FIXTURE",
            "source": "sectorlab.fundamental_research.demo",
            "provenance": "Fictional deterministic fixture",
            "audit_ref": "FIXTURE-v1",
            "history_type": "synthetic",
            "data_license": "synthetic",
        }
        | fields
    )


def demo_csv() -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    quarters = pd.date_range("2024-10-01", periods=6, freq="QS").strftime("%Y-%m-%d")
    for index, ticker in enumerate(SECTORS):
        issuer = f"FICTIONAL_{ticker}"
        subindustry = next(iter(QUALITY_MODELS[ticker]))
        weights = {issuer: (0.94, subindustry), RESIDUAL: (0.06, "residual")}
        for issue, (weight, group) in weights.items():
            writer.writerow(
                _record(
                    "holding",
                    issue,
                    "2024-10-30",
                    ticker=ticker,
                    subindustry=group,
                    metric_definition="etf_nav_weight_v1",
                    basis="ETF_NAV",
                    value=weight,
                )
            )
        for stage, day in enumerate(("2024-07-29", "2024-09-27", "2024-10-30")):
            for analyst in range(4):
                for j, (start, end) in enumerate(pairwise(quarters)):
                    eps = 1.20 + j * 0.10 + analyst * 0.02 + stage * (index - 4) * 0.015
                    writer.writerow(
                        _record(
                            "eps",
                            issuer,
                            day,
                            analyst_id=f"A{analyst}",
                            metric_definition="quarterly_diluted_adjusted_eps_v1",
                            period_start=start,
                            period_end=end,
                            basis="USD/share:fictional-v1",
                            value=f"{eps:.4f}",
                        )
                    )
                signal = int(stage == 2) * (int(index >= analyst + 4) - int(index < analyst + 2))
                writer.writerow(
                    _record(
                        "recommendation",
                        issuer,
                        day,
                        analyst_id=f"A{analyst}",
                        metric_definition="analyst_rating_1_to_5_v1",
                        basis="ordinal",
                        value=3 + signal,
                    )
                )
        for pillar in QUALITY_MODELS[ticker][subindustry]:
            for metric in pillar:
                spec = METRICS[metric]
                value = spec.weak + (spec.strong - spec.weak) * (0.48 + 0.032 * index)
                denominator = spec.year_count or 1
                if spec.year_count is not None:
                    value = round(value * denominator)
                writer.writerow(
                    _record(
                        "quality",
                        issuer,
                        "2024-10-24",
                        period_start="2023-10-01",
                        period_end="2024-10-01",
                        basis="ratio",
                        metric_definition=f"{metric}_v1",
                        value=f"{value:.6f}",
                        denominator=denominator,
                    )
                )
    return buffer.getvalue().encode("utf-8")
