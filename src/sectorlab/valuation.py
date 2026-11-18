"""Past-only valuation diagnostics for validated monthly ETF observations."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import BENCHMARK, HISTORY_MONTHS, MAX_STALE_DAYS, MIN_HISTORY, VALUATION_METRICS
from .valuation_data import BENCHMARK_METRICS, MARKET_TZ, ValuationDataset, utc_instant

Z_LIMIT = 3.0
MAD_SCALE = 1.4826
COMPARABILITY = ["vendor", "source", "fiscal_period", "metric_definition"]

METRIC_LABELS = {
    "forward_pe": "Forward P/E",
    "fcf_yield": "FCF Yield",
    "ev_ebit": "EV/EBIT",
    "ev_ebitda": "EV/EBITDA",
    "dividend_yield": "Dividend Yield",
    "midcycle_fcf_yield": "Mid-cycle FCF Yield",
    "ev_dacf": "EV/DACF",
    "p_tbv": "P/TBV",
    "midcycle_ev_ebitda": "Mid-cycle EV/EBITDA",
    "p_affo": "P/AFFO",
    "implied_cap_rate": "Implied Cap Rate",
}

SECTOR_NOTES = {
    "XLK": "Check how stock compensation and capitalized costs affect cash flow and EBIT.",
    "XLC": "Advertising, media and telecom differ in capital intensity and lease treatment.",
    "XLY": "Retail, autos and platforms differ in leases, working capital and captive finance.",
    "XLP": "Cash flow and dividend yields depend on payout policy and working capital.",
    "XLE": "Use cycle assumptions known at each date. EV/DACF depends on cash flow adjustments.",
    "XLF": "Tangible book is most interpretable for banks. Insurers and fee businesses differ.",
    "XLV": "Biotech losses and R&D treatment can change coverage and distort aggregate multiples.",
    "XLI": "Mid-cycle EBITDA requires a fixed normalization rule and a consistent lease basis.",
    "XLB": "Normalize EBITDA and FCF using contemporaneous commodity and utilization assumptions.",
    "XLRE": "AFFO and property NOI require consistent adjustments and property-level coverage.",
    "XLU": "Regulated and merchant businesses differ in leverage, capex and payout capacity.",
}


@dataclass(frozen=True)
class Normalization:
    status: str
    n_history: int = 0
    median: float = np.nan
    mad: float = np.nan
    z: float = np.nan
    attractiveness_z: float = np.nan
    score: float = np.nan
    percentile: float = np.nan


def normalize_valuation(
    value: float, history: pd.Series | list[float], *, higher_is_cheap: bool,
    min_periods: int = MIN_HISTORY,
) -> Normalization:
    """Normalize a current value against caller-supplied prior observations."""
    prior = np.asarray(history, dtype=float)
    prior = prior[np.isfinite(prior)]
    count = len(prior)
    if not np.isfinite(value):
        return Normalization("invalid_value", count)
    if count < min_periods:
        return Normalization("insufficient_history", count)
    with np.errstate(over="ignore", invalid="ignore"):
        median = float(np.median(prior))
        mad = float(np.median(np.abs(prior - median)))
        scale = MAD_SCALE * mad
        z = (float(value) - median) / scale if scale > 1e-12 * MAD_SCALE else np.nan
    if mad <= 1e-12:
        return Normalization("zero_mad", count, median, mad)
    if not np.isfinite(z) or not np.isfinite(scale):
        return Normalization("invalid_scale", count, median, mad)

    directed = z if higher_is_cheap else -z
    clipped = float(np.clip(directed, -Z_LIMIT, Z_LIMIT))
    score = 50 + (50 / Z_LIMIT) * clipped
    better_than = (prior < value) if higher_is_cheap else (prior > value)
    percentile = float(100 * (better_than.sum() + 0.5 * (prior == value).sum()) / count)
    return Normalization("complete", count, median, mad, z, clipped, score, percentile)


@dataclass(frozen=True)
class MetricValuation:
    ticker: str
    metric: str
    asof: pd.Timestamp
    input_sha256: str
    history_type: str
    current: pd.Series | None
    absolute: Normalization
    benchmark: pd.Series | None
    relative_value: float
    relative: Normalization


@dataclass(frozen=True)
class SectorValuation:
    metrics: dict[str, MetricValuation]
    historical_score: float
    relative_score: float
    blended_score: float


def _monthly(records: pd.DataFrame, ticker: str) -> pd.DataFrame:
    # CSV is ordered by date and vintage within each ticker/metric. The latest
    # vintage of the latest visible date in each calendar month is authoritative.
    return records.loc[records["ticker"].eq(ticker)].drop_duplicates("month", keep="last")


def _status(current: pd.Series, asof: pd.Timestamp) -> str:
    if (asof.tz_convert(MARKET_TZ).date() - current["observation_date"].date()).days > MAX_STALE_DAYS:
        return "stale"
    if not current["valid_denominator"]:
        return "invalid_denominator"
    if not current["valid_value"]:
        return "invalid_value"
    return "complete"


def _reference(rows: pd.DataFrame, current: pd.Series) -> pd.DataFrame:
    """Take valid observations from the same uninterrupted definition regime."""
    month = current["month"]
    prior = rows.loc[rows["month"].between(month - HISTORY_MONTHS, month - 1)]
    matches = prior[COMPARABILITY].eq(current[COMPARABILITY], axis="columns").all(axis=1)
    # A later return to a previously used definition starts a new reference history.
    last_change = prior.loc[~matches, "month"].max()
    if pd.notna(last_change):
        prior = prior.loc[prior["month"].gt(last_change)]
    return prior.loc[prior["valid_denominator"] & prior["valid_value"]]


def _relative_values(sector: pd.Series, benchmark: pd.Series, metric: str) -> pd.Series:
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        return sector.div(benchmark).sub(1) if metric == "forward_pe" else sector.sub(benchmark)


def _compare(
    metric: str, current: pd.Series, prior: pd.DataFrame, bench: pd.DataFrame,
    *, higher_is_cheap: bool,
) -> tuple[pd.Series | None, float, Normalization]:
    if metric not in BENCHMARK_METRICS:
        return None, np.nan, Normalization("not_comparable")
    same_date = bench.loc[bench["observation_date"].eq(current["observation_date"])]
    if same_date.empty:
        return None, np.nan, Normalization("missing_benchmark")
    benchmark = same_date.iloc[0]
    if not current[COMPARABILITY].equals(benchmark[COMPARABILITY]):
        return benchmark, np.nan, Normalization("incompatible_benchmark")
    if current["observed_at"] != benchmark["observed_at"]:
        return benchmark, np.nan, Normalization("unaligned_benchmark")
    if not benchmark["valid_denominator"] or not benchmark["valid_value"]:
        return benchmark, np.nan, Normalization("invalid_benchmark")

    past_spy = _reference(bench, benchmark)
    paired = prior.merge(past_spy, on=["observation_date", "observed_at"], suffixes=("_sector", "_spy"))
    history = _relative_values(paired["value_sector"], paired["value_spy"], metric)
    current_pair = _relative_values(
        pd.Series([current["value"]]), pd.Series([benchmark["value"]]), metric,
    ).iloc[0]
    normal = normalize_valuation(current_pair, history, higher_is_cheap=higher_is_cheap)
    return benchmark, float(current_pair) if np.isfinite(current_pair) else np.nan, normal


def evaluate_metric(
    dataset: ValuationDataset, ticker: str, metric: str, *, asof: str | pd.Timestamp,
) -> MetricValuation:
    """Evaluate one sector metric and its optional SPY comparison at an inclusive cutoff."""
    _, higher_is_cheap = VALUATION_METRICS[ticker][metric]
    asof = utc_instant(asof)
    records = dataset._records
    rows = records.loc[records["metric"].eq(metric) & records["available_at"].le(asof)]
    sector = _monthly(rows, ticker)
    identity = (ticker, metric, asof, dataset.sha256, dataset.history_type)
    if sector.empty:
        status = Normalization("missing_observation")
        return MetricValuation(*identity, None, status, None, np.nan, status)
    current = sector.iloc[-1]
    if dataset.history_type == "reconstructed":
        status = Normalization("reconstructed_history")
        return MetricValuation(*identity, current, status, None, np.nan, status)

    status = _status(current, asof)
    if status != "complete":
        missing = Normalization(status)
        return MetricValuation(*identity, current, missing, None, np.nan, missing)
    prior = _reference(sector, current)
    absolute = normalize_valuation(current["value"], prior["value"], higher_is_cheap=higher_is_cheap)
    benchmark, value, relative = _compare(
        metric, current, prior, _monthly(rows, BENCHMARK), higher_is_cheap=higher_is_cheap,
    )
    return MetricValuation(*identity, current, absolute, benchmark, value, relative)


def evaluate_sector(
    dataset: ValuationDataset, ticker: str, *, asof: str | pd.Timestamp,
) -> SectorValuation:
    """Return separate historical and relative scores plus the strict 70/30 blend."""
    weights = VALUATION_METRICS[ticker]
    metrics = {metric: evaluate_metric(dataset, ticker, metric, asof=asof) for metric in weights}

    def weighted_score(component: str, required: list[str]) -> float:
        scores = {key: getattr(metrics[key], component).score for key in required}
        if not scores or not all(np.isfinite(score) for score in scores.values()):
            return np.nan
        if len({metrics[key].current["month"] for key in required}) != 1:
            return np.nan
        weighted = sum(weights[key][0] * value for key, value in scores.items())
        return float(np.clip(weighted / sum(weights[key][0] for key in required), 0, 100))

    historical = weighted_score("absolute", list(weights))
    relative = weighted_score("relative", [key for key in weights if key in BENCHMARK_METRICS])
    return SectorValuation(metrics, historical, relative, 0.70 * historical + 0.30 * relative)


def metric_history(
    dataset: ValuationDataset, ticker: str, metric: str, *, asof: str | pd.Timestamp,
) -> list[MetricValuation]:
    """Replay past monthly decisions, then evaluate the exact final cutoff."""
    asof = utc_instant(asof)
    rows = dataset._records
    visible = rows.loc[
        rows["ticker"].eq(ticker) & rows["metric"].eq(metric) & rows["available_at"].le(asof)
    ]
    if visible.empty:
        return []
    if dataset.history_type == "reconstructed":
        return [
            MetricValuation(
                ticker, metric, asof, dataset.sha256, dataset.history_type, row,
                Normalization("reconstructed_history"), None, np.nan,
                Normalization("reconstructed_history"),
            )
            for _, row in _monthly(visible, ticker).iterrows()
        ]

    start = visible["available_at"].min().tz_convert(MARKET_TZ).tz_localize(None).to_period("M")
    final = asof.tz_convert(MARKET_TZ).tz_localize(None).to_period("M")
    cutoffs = [
        utc_instant(month.end_time.tz_localize(MARKET_TZ))
        for month in pd.period_range(start, final - 1, freq="M")
    ]
    return [evaluate_metric(dataset, ticker, metric, asof=cutoff) for cutoff in [*cutoffs, asof]]
