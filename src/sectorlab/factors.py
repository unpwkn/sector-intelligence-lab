"""Point-in-time factor transformations and signal calculations.

All numeric outputs in the demo are SYNTHETIC. This module does not fetch data.
"""

from __future__ import annotations

import math
from typing import Mapping

import numpy as np
import pandas as pd

from .config import (
    BENCHMARK,
    HISTORY_MONTHS,
    MAX_STALE_DAYS,
    MIN_HISTORY,
    MOMENTUM_WEIGHTS,
    OPPORTUNITY_WEIGHTS,
    QUALITY_WEIGHTS,
    REVISION_WEIGHTS,
    SECTORS,
    VALUATION_METRICS,
)


def robust_value_score(
    value: float,
    prior_values: pd.Series | list[float],
    *,
    higher_is_cheap: bool,
    min_periods: int = MIN_HISTORY,
) -> float:
    """0-100 historical valuation score; no current observation in the baseline.

    1.4826 * MAD estimates standard deviation under a normal distribution.
    Return NaN rather than invent a score for insufficient history/zero MAD.
    """
    history = pd.Series(prior_values, dtype="float64").replace([np.inf, -np.inf], np.nan).dropna()
    if len(history) < min_periods or not math.isfinite(float(value)):
        return float("nan")
    median = float(history.median())
    mad = float((history - median).abs().median())
    if mad <= 1e-12:
        return float("nan")
    z = float(np.clip((float(value) - median) / (1.4826 * mad), -3, 3))
    if not higher_is_cheap:
        z = -z
    return float(np.clip(50 + 15 * z, 5, 95))


def valuation_scores(
    valuations: pd.DataFrame,
    asof: str | pd.Timestamp,
    *,
    min_periods: int = MIN_HISTORY,
    history_months: int = HISTORY_MONTHS,
) -> pd.DataFrame:
    """Compute complete sector-level valuation proxy scores.

    CSV fields: date,ticker,metric,value,available_at.
    `available_at` is the first time the data could be known, not filing period.
    Current and historical points must have available_at <= asof.
    """
    asof = pd.Timestamp(asof)
    required = {"date", "ticker", "metric", "value", "available_at"}
    if not required.issubset(valuations.columns):
        raise ValueError(f"valuations missing columns: {required - set(valuations.columns)}")
    data = valuations.copy()
    data["date"] = pd.to_datetime(data["date"])
    data["available_at"] = pd.to_datetime(data["available_at"])
    data = data.loc[(data["date"] <= asof) & (data["available_at"] <= asof)]
    rows: list[dict] = []
    for ticker, metrics in VALUATION_METRICS.items():
        parts: list[tuple[float, float]] = []
        status: list[str] = []
        for metric, (weight, higher_is_cheap) in metrics.items():
            sub = data.loc[(data["ticker"] == ticker) & (data["metric"] == metric)]
            sub = sub.sort_values(["date", "available_at"]).drop_duplicates("date", keep="last")
            if sub.empty:
                status.append(f"missing:{metric}")
                continue
            last_date = sub.iloc[-1]["date"]
            if (asof - last_date).days > MAX_STALE_DAYS:
                status.append(f"stale:{metric}")
                continue
            previous = sub.iloc[:-1].tail(history_months)["value"]
            score = robust_value_score(
                float(sub.iloc[-1]["value"]), previous,
                higher_is_cheap=higher_is_cheap, min_periods=min_periods,
            )
            if not np.isfinite(score):
                status.append(f"insufficient_history:{metric}")
            else:
                parts.append((weight, score))
        # Do not silently renormalize an incomplete sector.
        complete = len(parts) == len(metrics)
        rows.append({
            "ticker": ticker,
            "valuation": sum(weight * score for weight, score in parts) if complete else np.nan,
            "valuation_coverage": len(parts) / len(metrics),
            "valuation_status": "complete" if complete else ";".join(status),
        })
    return pd.DataFrame(rows)


def _relative_log_return(panel: pd.DataFrame, ticker: str, start: int, end: int) -> float:
    sec, spy = panel[ticker], panel[BENCHMARK]
    v = [sec.iloc[start], sec.iloc[end], spy.iloc[start], spy.iloc[end]]
    if not all(np.isfinite(x) and x > 0 for x in v):
        return np.nan
    return float(np.log(sec.iloc[end] / sec.iloc[start]) - np.log(spy.iloc[end] / spy.iloc[start]))


def momentum_scores(prices: pd.DataFrame, breadth: pd.DataFrame, asof: str | pd.Timestamp) -> pd.DataFrame:
    """Cross-sector momentum rankings (0-100) with complete breadth requirement.

    Prices are an adjusted-price *proxy* in demo. Production requires a validated
    total-return series. Breadth is 0-1 fraction from PIT constituents.
    """
    asof = pd.Timestamp(asof)
    required = {"date", "ticker", "adjusted_price_proxy", "available_at"}
    if not required.issubset(prices.columns):
        raise ValueError(f"prices missing columns: {required - set(prices.columns)}")
    p = prices.copy()
    p["date"] = pd.to_datetime(p["date"])
    p["available_at"] = pd.to_datetime(p["available_at"])
    p = p.loc[(p["date"] <= asof) & (p["available_at"] <= asof)]
    p = p.sort_values(["date", "available_at"]).drop_duplicates(["date", "ticker"], keep="last")
    panel = p.pivot(index="date", columns="ticker", values="adjusted_price_proxy").sort_index()
    panel = panel.ffill(limit=2)
    b = breadth.copy()
    if not {"date", "ticker", "breadth", "available_at"}.issubset(b.columns):
        raise ValueError("breadth missing required columns")
    b["date"] = pd.to_datetime(b["date"])
    b["available_at"] = pd.to_datetime(b["available_at"])
    b = b.loc[(b["date"] <= asof) & (b["available_at"] <= asof)]
    b = b.sort_values(["date", "available_at"]).drop_duplicates("ticker", keep="last")
    breadth_dict = b.set_index("ticker")["breadth"].to_dict()
    records = []
    for ticker in SECTORS:
        rec = {"ticker": ticker, "rs_6_1": np.nan, "rs_12_1": np.nan, "rs_3m": np.nan,
               "breadth": breadth_dict.get(ticker, np.nan)}
        if ticker in panel.columns and BENCHMARK in panel.columns and len(panel) >= 253:
            rec["rs_6_1"] = _relative_log_return(panel, ticker, -127, -22)
            rec["rs_12_1"] = _relative_log_return(panel, ticker, -253, -22)
            rec["rs_3m"] = _relative_log_return(panel, ticker, -64, -1)
        records.append(rec)
    frame = pd.DataFrame(records)
    for col in MOMENTUM_WEIGHTS:
        frame[col + "_score"] = frame[col].rank(pct=True) * 100
    frame["momentum"] = frame[[c + "_score" for c in MOMENTUM_WEIGHTS]].apply(
        lambda row: sum(row[k + "_score"] * w for k, w in MOMENTUM_WEIGHTS.items())
        if row.notna().all() else np.nan, axis=1,
    )
    frame["momentum_status"] = np.where(frame["momentum"].notna(), "complete", "incomplete")
    return frame[["ticker", "momentum", "momentum_status", "rs_6_1", "rs_12_1", "rs_3m", "breadth"]]


def _weighted_score(frame: pd.DataFrame, weights: Mapping[str, float], output: str) -> pd.DataFrame:
    required = {"ticker", *weights}
    if not required.issubset(frame.columns):
        raise ValueError(f"{output} missing columns: {required - set(frame.columns)}")
    values = frame[list(weights)].apply(pd.to_numeric, errors="coerce")
    in_range = values.ge(0).all(axis=1) & values.le(100).all(axis=1)
    frame = frame.copy()
    frame[output] = sum(values[k] * w for k, w in weights.items()).where(in_range)
    return frame[["ticker", output]]


def revision_scores(revisions: pd.DataFrame, asof: str | pd.Timestamp) -> pd.DataFrame:
    """Rank PIT revision inputs across sectors; raw signed revisions already constructed upstream.

    Revision breadth must be 0-1; rate inputs should be calendarized and denominators
    controlled upstream. This is a baseline ranking, not a consensus data collector.
    """
    asof = pd.Timestamp(asof)
    data = revisions.copy()
    for col in ("date", "available_at"):
        data[col] = pd.to_datetime(data[col])
    data = data.loc[(data["date"] <= asof) & (data["available_at"] <= asof)]
    data = data.sort_values(["date", "available_at"]).drop_duplicates("ticker", keep="last")
    for col in REVISION_WEIGHTS:
        data[col] = pd.to_numeric(data[col], errors="coerce")
        data[col] = data[col].rank(pct=True) * 100
    return _weighted_score(data, REVISION_WEIGHTS, "revision")


def quality_scores(quality: pd.DataFrame, asof: str | pd.Timestamp) -> pd.DataFrame:
    """Combine upstream sector-specific quality sub-scores, each normalized to 0-100."""
    asof = pd.Timestamp(asof)
    data = quality.copy()
    for col in ("date", "available_at"):
        data[col] = pd.to_datetime(data[col])
    data = data.loc[(data["date"] <= asof) & (data["available_at"] <= asof)]
    data = data.sort_values(["date", "available_at"]).drop_duplicates("ticker", keep="last")
    return _weighted_score(data, QUALITY_WEIGHTS, "quality")


def classify(valuation: float, rotation: float) -> str:
    if not (np.isfinite(valuation) and np.isfinite(rotation)):
        return "INSUFFICIENT_DATA"
    if valuation >= 65 and rotation >= 60:
        return "VALUATION_SUPPORTED_ROTATION"
    if valuation >= 65 and rotation <= 40:
        return "CHEAP_BUT_WEAK"
    if valuation <= 35 and rotation >= 60:
        return "EXPENSIVE_LEADERSHIP"
    if valuation <= 35 and rotation <= 40:
        return "EXPENSIVE_AND_WEAK"
    return "NEUTRAL_OR_MIXED"


def combine_signals(valuation: pd.DataFrame, momentum: pd.DataFrame,
                    revision: pd.DataFrame, quality: pd.DataFrame, *,
                    signal_date: str, provenance: str) -> pd.DataFrame:
    """Never silently impute or reweight missing pillars."""
    result = pd.DataFrame({"ticker": list(SECTORS)})
    for other in (valuation, momentum, revision, quality):
        result = result.merge(other, on="ticker", how="left", validate="one_to_one")
    columns = list(OPPORTUNITY_WEIGHTS)
    complete = result[columns].notna().all(axis=1)
    result["rotation"] = (.55 * result["momentum"] + .45 * result["revision"]).where(complete)
    result["opportunity"] = sum(result[k] * w for k, w in OPPORTUNITY_WEIGHTS.items()).where(complete)
    result["classification"] = [classify(v, r) for v, r in zip(result["valuation"], result["rotation"])]
    result["signal_date"] = signal_date
    result["provenance"] = provenance
    result["model_version"] = "0.1.0-RESEARCH"
    result["data_status"] = np.where(complete, "INPUTS_COMPLETE_UNVALIDATED", "INCOMPLETE")
    result = result.sort_values("opportunity", ascending=False, na_position="last")
    return result.reset_index(drop=True)
