"""Matched-analyst EPS revisions and recommendation breadth for ETF research."""

from __future__ import annotations

from datetime import timedelta
from math import fsum, isfinite

import numpy as np
import pandas as pd

from ..config import REVISION_WEIGHTS
from ..valuation_data import utc_instant
from .aggregation import (
    ResearchResult,
    aggregate,
    holdings_at,
    local_day,
    source_coverage,
    visible_rows,
)
from .data import RESIDUAL, ResearchDataset

MIN_ANALYSTS = 3
EPS_MAX_AGE_DAYS = 120
RATING_MAX_AGE_DAYS = 180
EPS_FLOOR = 0.25
MIN_SECTORS = 2


def calendarized_eps(
    quarters: dict[tuple[pd.Timestamp, pd.Timestamp], float],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> float | None:
    """Day-prorate quarters onto a fixed half-open interval; require an exact cover."""
    cursor = start
    parts = []
    for (quarter_start, quarter_end), eps in sorted(quarters.items()):
        left, right = max(start, quarter_start), min(end, quarter_end)
        if left >= right:
            continue
        if left != cursor or not isfinite(eps):
            return None
        parts.append(eps * ((right - left).days / (quarter_end - quarter_start).days))
        cursor = right
    if cursor != end:
        return None
    try:
        return fsum(parts)
    except OverflowError:
        return None


def guarded_revision(current: float, previous: float) -> float:
    """Bound revisions to [-2, 2] with a symmetric absolute EPS scale and USD floor."""
    scale = max(abs(current), abs(previous), EPS_FLOOR)
    return current / scale - previous / scale


def _eps_votes(
    history: pd.DataFrame,
    cutoff: pd.Timestamp,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, tuple[float, str, frozenset]]:
    latest = history.loc[
        (history["available_at"] <= cutoff)
        & (history["period_start"] < end)
        & (history["period_end"] > start)
    ].drop_duplicates(["analyst_id", "period_start", "period_end"], keep="last")
    fresh_after = local_day(cutoff) - timedelta(days=EPS_MAX_AGE_DAYS)
    result = {}
    for analyst, rows in latest.groupby("analyst_id"):
        if (
            not rows["status"].eq("valid").all()
            or rows["observation_date"].lt(fresh_after).any()
            or rows["basis"].nunique() != 1
        ):
            continue
        quarters = {
            (row.period_start, row.period_end): row.value for row in rows.itertuples(index=False)
        }
        estimate = calendarized_eps(quarters, start, end)
        if estimate is not None:
            result[analyst] = estimate, rows["basis"].iloc[0], frozenset(quarters)
    return result


def _eps_change(current: dict, previous: dict) -> tuple[float | None, int]:
    # Do not pool analysts on different fiscal calendars or corporate-action bases.
    matched = {}
    for analyst in sorted(current.keys() & previous.keys()):
        now, basis, periods = current[analyst]
        then, old_basis, old_periods = previous[analyst]
        if (basis, periods) == (old_basis, old_periods):
            matched.setdefault((basis, periods), []).append((now, then))
    count = sum(len(values) for values in matched.values())
    if len(matched) != 1 or count < MIN_ANALYSTS:
        return None, count
    pairs = next(iter(matched.values()))
    now, then = (fsum(value / count for value in series) for series in zip(*pairs, strict=True))
    return guarded_revision(now, then), count


def _ratings(history: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    latest = history.loc[history["available_at"] <= cutoff].drop_duplicates(
        "analyst_id", keep="last"
    )
    fresh_after = local_day(cutoff) - timedelta(days=RATING_MAX_AGE_DAYS)
    valid = latest.loc[(latest["status"] == "valid") & (latest["observation_date"] >= fresh_after)]
    return valid.set_index("analyst_id")["value"].to_dict()


def _issuer_features(
    eps: pd.DataFrame,
    ratings: pd.DataFrame,
    asof: pd.Timestamp,
    month: pd.Timestamp,
    quarter: pd.Timestamp,
) -> dict:
    start = local_day(asof) + timedelta(days=1)
    end = start + pd.DateOffset(months=12)
    current = _eps_votes(eps, asof, start, end)
    features = {}
    for component, cutoff in (("revision_1m", month), ("revision_3m", quarter)):
        value, count = _eps_change(current, _eps_votes(eps, cutoff, start, end))
        features[component] = value
        features[f"{component}_matched_analysts"] = count
        features[f"{component}_status"] = (
            "available" if value is not None else "insufficient_or_incompatible_estimates"
        )
    current_ratings, old_ratings = _ratings(ratings, asof), _ratings(ratings, quarter)
    deltas = [
        current_ratings[key] - old_ratings[key]
        for key in sorted(current_ratings.keys() & old_ratings.keys())
    ]
    features["revision_breadth"] = (
        float(np.sign(deltas).mean()) if len(deltas) >= MIN_ANALYSTS else None
    )
    features["revision_breadth_matched_analysts"] = len(deltas)
    features["revision_breadth_upgrades"] = sum(change > 0 for change in deltas)
    features["revision_breadth_downgrades"] = sum(change < 0 for change in deltas)
    features["revision_breadth_status"] = (
        "available" if len(deltas) >= MIN_ANALYSTS else "insufficient_matched_ratings"
    )
    return features


def earnings_research(dataset: ResearchDataset, *, asof: str | pd.Timestamp) -> ResearchResult:
    """Evaluate the as-of ETF universe on the same fixed forward earnings window."""
    asof = utc_instant(asof)
    visible = visible_rows(dataset, asof)
    holdings = holdings_at(visible, asof)
    observations = visible.loc[visible["kind"].isin(["eps", "recommendation"])]
    histories = dict(tuple(observations.groupby(["issuer_id", "kind"])))
    empty = observations.iloc[:0]
    month, quarter = (asof - pd.DateOffset(months=n) for n in (1, 3))
    issuer_features = {}
    features = {}
    for ticker, snapshot in holdings.items():
        for issuer in snapshot["issuer_id"]:
            if issuer == RESIDUAL:
                continue
            if issuer not in issuer_features:
                issuer_features[issuer] = _issuer_features(
                    histories.get((issuer, "eps"), empty),
                    histories.get((issuer, "recommendation"), empty),
                    asof,
                    month,
                    quarter,
                )
            features[ticker, issuer] = issuer_features[issuer]
    sectors, issuers = aggregate(holdings, features, REVISION_WEIGHTS)
    eligible = sectors["status"] == "eligible"
    peers = int(eligible.sum())
    sectors["peer_count"] = peers
    sectors["earnings_revision_score"] = np.nan
    for component in REVISION_WEIGHTS:
        sectors[f"{component}_score"] = np.nan
        if peers >= MIN_SECTORS:
            ranks = sectors.loc[eligible, f"{component}_joint_value"].rank(method="average")
            sectors.loc[eligible, f"{component}_score"] = 100 * (ranks - 1) / (peers - 1)
    if peers >= MIN_SECTORS:
        sectors.loc[eligible, "earnings_revision_score"] = sum(
            sectors.loc[eligible, f"{component}_score"] * weight
            for component, weight in REVISION_WEIGHTS.items()
        )
        sectors.loc[eligible, "status"] = "complete"
    else:
        sectors.loc[eligible, "status"] = "insufficient_sector_peers"
    return ResearchResult(
        sectors,
        issuers,
        source_coverage(holdings, observations, dataset.history_type),
        asof,
        dataset.history_type,
        dataset.sha256,
    )
