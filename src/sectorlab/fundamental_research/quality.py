"""Sector-aware issuer quality research using as-filed ratio operands."""

from __future__ import annotations

from datetime import timedelta
from math import fsum

import pandas as pd

from ..config import QUALITY_WEIGHTS
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
from .quality_definitions import METRICS, QUALITY_MODELS

QUALITY_MAX_AGE_DAYS = 185


def _issuer_features(
    history: pd.DataFrame,
    model: tuple[tuple[str, ...], ...],
    asof: pd.Timestamp,
) -> dict:
    definitions = {f"{metric}_v1" for pillar in model for metric in pillar}
    relevant = history.loc[history["metric_definition"].isin(definitions)]
    # Use one coherent fiscal anchor. A missing or invalid new release cannot be
    # filled with an older fiscal period of that metric.
    latest_end = relevant["period_end"].max()
    latest = relevant.loc[relevant["period_end"] == latest_end]
    starts = latest["period_start"].nunique()
    anchor = latest.drop_duplicates("metric_definition", keep="last").set_index("metric_definition")
    features = {"quality_period_end_exclusive": latest_end}
    fresh_after = local_day(asof) - timedelta(days=QUALITY_MAX_AGE_DAYS)
    for component, pillar in zip(QUALITY_WEIGHTS, model, strict=True):
        scores, issues = [], []
        for metric in pillar:
            definition = f"{metric}_v1"
            if starts > 1:
                issues.append(f"{metric}:inconsistent_fiscal_anchor")
            elif definition not in anchor.index:
                issues.append(f"{metric}:missing_at_anchor")
            elif latest_end < fresh_after:
                issues.append(f"{metric}:stale")
            else:
                row = anchor.loc[definition]
                if row["status"] != "valid":
                    issues.append(f"{metric}:{row['status']}")
                else:
                    ratio = row["value"] / row["denominator"]
                    features[f"ratio_{metric}"] = ratio
                    scores.append(METRICS[metric].score(ratio))
        features[component] = None if issues else fsum(scores) / len(scores)
        features[f"{component}_status"] = ", ".join(issues) if issues else "available"
    return features


def quality_research(dataset: ResearchDataset, *, asof: str | pd.Timestamp) -> ResearchResult:
    """Return quality scores only for ETFs with adequate joint portfolio coverage."""
    asof = utc_instant(asof)
    visible = visible_rows(dataset, asof)
    holdings = holdings_at(visible, asof)
    observations = visible.loc[visible["kind"] == "quality"]
    histories = dict(tuple(observations.groupby("issuer_id")))
    features = {}
    for ticker, snapshot in holdings.items():
        for row in snapshot.itertuples(index=False):
            if row.issuer_id == RESIDUAL:
                continue
            model = QUALITY_MODELS[ticker].get(row.subindustry)
            if model is None:
                features[ticker, row.issuer_id] = {
                    f"{component}_status": "unmapped_subindustry" for component in QUALITY_WEIGHTS
                }
                continue
            features[ticker, row.issuer_id] = _issuer_features(
                histories.get(row.issuer_id, observations.iloc[:0]),
                model,
                asof,
            )
    sectors, issuers = aggregate(holdings, features, QUALITY_WEIGHTS)
    eligible = sectors["status"] == "eligible"
    sectors["quality_score"] = sum(
        sectors[f"{component}_joint_value"].astype(float) * weight
        for component, weight in QUALITY_WEIGHTS.items()
    )
    sectors.loc[eligible, "status"] = "complete"
    return ResearchResult(
        sectors,
        issuers,
        source_coverage(holdings, observations, dataset.history_type),
        asof,
        dataset.history_type,
        dataset.sha256,
    )
