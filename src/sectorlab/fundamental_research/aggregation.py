"""Common holdings alignment and component coverage reporting."""

from dataclasses import dataclass
from math import fsum, isclose

import pandas as pd

from ..config import SECTORS
from ..valuation_data import MARKET_TZ
from .data import RESIDUAL, ResearchDataset

MIN_WEIGHT = 0.80
HOLDING_MAX_AGE_DAYS = 35


@dataclass(frozen=True)
class ResearchResult:
    sectors: pd.DataFrame
    issuers: pd.DataFrame
    source_coverage: pd.DataFrame
    asof: pd.Timestamp
    history_type: str
    sha256: str


def local_day(instant: pd.Timestamp) -> pd.Timestamp:
    return instant.tz_convert(MARKET_TZ).normalize().tz_localize(None)


def visible_rows(dataset: ResearchDataset, asof: pd.Timestamp) -> pd.DataFrame:
    return dataset._rows.loc[dataset._rows["available_at"] <= asof]


def holdings_at(visible: pd.DataFrame, asof: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Select an entire known snapshot per ETF, including its latest available correction."""
    assets = visible.loc[visible["kind"] == "holding"]
    result = {}
    for ticker, history in assets.groupby("ticker"):
        latest = history.iloc[-1]  # Ingestion sorts by observation time, then availability.
        age = (local_day(asof) - latest["observation_date"]).days
        if age <= HOLDING_MAX_AGE_DAYS:
            result[ticker] = history.loc[
                (history["observation_date"] == latest["observation_date"])
                & (history["available_at"] == latest["available_at"])
            ]
    return result


def sufficient_weight(weight: float) -> bool:
    return weight >= MIN_WEIGHT or isclose(weight, MIN_WEIGHT, rel_tol=0, abs_tol=1e-12)


def aggregate(
    holdings: dict[str, pd.DataFrame],
    features: dict[tuple[str, str], dict],
    components: dict[str, float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries, details = [], []
    for ticker in SECTORS:
        snapshot = holdings.get(ticker)
        issuer_rows = []
        if snapshot is not None:
            for holding in snapshot.itertuples(index=False):
                issuer_rows.append(
                    {
                        "ticker": ticker,
                        "issuer_id": holding.issuer_id,
                        "subindustry": holding.subindustry,
                        "weight": holding.value,
                    }
                    | features.get((ticker, holding.issuer_id), {})
                )
        details.extend(issuer_rows)
        available = [
            row
            for row in issuer_rows
            if all(row.get(component) is not None for component in components)
        ]
        joint_weight = fsum(row["weight"] for row in available)
        summary = {
            "ticker": ticker,
            "holdings_date": snapshot.iloc[0]["observation_date"] if snapshot is not None else None,
            "holdings_available_at": (
                snapshot.iloc[0]["available_at"] if snapshot is not None else None
            ),
            "issuer_count": sum(row["issuer_id"] != RESIDUAL for row in issuer_rows),
            "joint_covered_weight": joint_weight if snapshot is not None else None,
            "joint_issuer_count": len(available),
            "status": (
                "missing_or_stale_holdings"
                if snapshot is None
                else "eligible"
                if sufficient_weight(joint_weight)
                else "insufficient_joint_weight"
            ),
        }
        for component in components:
            covered = [row for row in issuer_rows if row.get(component) is not None]
            weight = fsum(row["weight"] for row in covered)
            summary[f"{component}_covered_weight"] = weight if snapshot is not None else None
            summary[f"{component}_issuer_count"] = len(covered)
            summary[f"{component}_status"] = (
                "missing_or_stale_holdings"
                if snapshot is None
                else "available"
                if sufficient_weight(weight)
                else "insufficient_weight"
            )
            summary[component] = (
                fsum(row["weight"] * row[component] for row in covered) / weight
                if sufficient_weight(weight)
                else None
            )
            # The composite's constituents are identical across its components.
            summary[f"{component}_joint_value"] = (
                fsum(row["weight"] * row[component] for row in available) / joint_weight
                if sufficient_weight(joint_weight)
                else None
            )
        summaries.append(summary)
    return pd.DataFrame(summaries).set_index("ticker"), pd.DataFrame(details)


def source_coverage(
    holdings: dict[str, pd.DataFrame],
    observations: pd.DataFrame,
    history_type: str,
) -> pd.DataFrame:
    """Describe visible candidate inputs. Usability is reported by component and issuer."""
    summaries = []
    for ticker in SECTORS:
        snapshot = holdings.get(ticker)
        if snapshot is None:
            summaries.append({"ticker": ticker, "confidence": "no_fresh_holdings"})
            continue
        inputs = observations.loc[observations["issuer_id"].isin(snapshot["issuer_id"])]
        source_columns = [
            "kind",
            "vendor",
            "data_license",
            "issuer_id",
            "available_at",
            "source",
            "audit_ref",
        ]
        rows = pd.concat([snapshot[source_columns], inputs[source_columns]])
        for (kind, vendor, license_type), source in rows.groupby(
            ["kind", "vendor", "data_license"]
        ):
            ids = set(source["issuer_id"]) - {RESIDUAL}
            summaries.append(
                {
                    "ticker": ticker,
                    "kind": kind,
                    "vendor": vendor,
                    "license": license_type,
                    "confidence": "synthetic" if history_type == "synthetic" else "declared_pit",
                    "visible_rows": len(source),
                    "issuer_weight_with_visible_rows": fsum(
                        snapshot.loc[snapshot["issuer_id"].isin(ids), "value"]
                    ),
                    "last_available_at": source["available_at"].max(),
                    "sources": tuple(sorted(set(source["source"]))),
                    "audit_refs": tuple(sorted(set(source["audit_ref"]))),
                }
            )
    return pd.DataFrame(summaries)
