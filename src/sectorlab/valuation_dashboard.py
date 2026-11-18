"""Streamlit's independent valuation research view."""

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from .config import SECTORS, VALUATION_METRICS
from .valuation import METRIC_LABELS, SECTOR_NOTES, MetricValuation, evaluate_sector, metric_history
from .valuation_data import (
    MARKET_TZ,
    YIELD_METRICS,
    ValuationDataError,
    parse_valuation_csv,
)
from .valuation_demo import DEMO_ASOF, synthetic_valuation_csv

CONFIDENCE = {
    "synthetic": "Synthetic demonstration. Every observation is fictional.",
    "reconstructed": "Reconstructed history. Original publication vintages are unverified.",
    "point_in_time": "Declared point-in-time. Vendor vintages and rights are unverified.",
}
METRIC_EXPLANATIONS = {
    "forward_pe": "Price divided by forecast earnings for the documented fiscal horizon.",
    "fcf_yield": "Free cash flow relative to equity value, using the source's cash-flow recipe.",
    "ev_ebit": "Enterprise value divided by earnings before interest and taxes.",
    "ev_ebitda": (
        "Enterprise value divided by earnings before interest, taxes, depreciation and amortization."
    ),
    "dividend_yield": "Dividends relative to price under the documented payout definition.",
    "midcycle_fcf_yield": "Cycle-normalized free cash flow relative to equity value.",
    "ev_dacf": "Enterprise value divided by debt-adjusted cash flow.",
    "p_tbv": "Equity value divided by tangible common book value.",
    "midcycle_ev_ebitda": "Enterprise value divided by EBITDA estimated at mid-cycle conditions.",
    "p_affo": "Equity value divided by adjusted funds from operations.",
    "implied_cap_rate": "Property net operating income relative to implied property value.",
}
STATUSES = {
    "complete": "Complete",
    "invalid_denominator": "Invalid or missing denominator",
    "invalid_value": "Invalid or missing value",
    "invalid_scale": "Invalid normalization scale",
    "zero_mad": "Zero or negligible historical MAD",
    "insufficient_history": "Fewer than 48 valid prior months",
    "stale": "Observation is over 45 days old",
    "missing_observation": "No observation available by the cutoff",
    "not_comparable": "Metric is outside the SPY comparison policy",
    "missing_benchmark": "No SPY observation on the same date available by the cutoff",
    "incompatible_benchmark": "SPY definition, source or fiscal basis differs",
    "unaligned_benchmark": "SPY observation timestamp differs",
    "invalid_benchmark": "Invalid SPY value or denominator",
    "reconstructed_history": "Scoring unavailable for reconstructed history",
}


def _history_frame(history: list[MetricValuation], reconstructed: bool) -> pd.DataFrame:
    rows = []
    for entry in history:
        current = entry.current
        visible = (
            current is not None and current["valid_value"] and current["valid_denominator"]
            and entry.absolute.status != "stale"
        )
        rows.append({
            "Date": current["observation_date"] if reconstructed else entry.asof,
            "Valuation": current["value"] if visible else np.nan,
            "Sector Z": entry.absolute.z,
            "Relative Z": entry.relative.z,
            "Sector score": entry.absolute.score,
            "Relative score": entry.relative.score,
            "Sector percentile": entry.absolute.percentile,
            "Relative percentile": entry.relative.percentile,
            "SPY premium or spread": entry.relative_value,
        })
    return pd.DataFrame(rows)


def _chart(frame: pd.DataFrame, heading: str, columns: list[str], **kwargs) -> None:
    available = [column for column in columns if frame[column].notna().any()]
    if available:
        st.subheader(heading)
        chart = px.line(frame, x="Date", y=available, markers=True, **kwargs)
        chart.update_traces(connectgaps=False)
        if len(available) == 1:
            chart.update_layout(showlegend=False, yaxis_title=available[0])
        st.plotly_chart(chart, use_container_width=True)


def _readouts(entry: MetricValuation) -> None:
    st.write(f"**Historical sector valuation** · {STATUSES[entry.absolute.status]}")
    st.write(f"**Relative to SPY** · {STATUSES[entry.relative.status]}")
    st.caption(
        f"Valid reference months · {entry.absolute.n_history} sector, "
        f"{entry.relative.n_history} paired with SPY"
    )
    with st.expander("Observation provenance"):
        st.code(f"CSV SHA-256 {entry.input_sha256}")
        for label, record in [(entry.ticker, entry.current), ("SPY", entry.benchmark)]:
            if record is not None:
                st.write(f"**{label}**")
                st.dataframe(record[[
                    "observation_date", "observed_at", "published_at", "available_at", "vendor",
                    "source", "provenance", "fiscal_period", "metric_definition", "data_license",
                ]].astype(str).rename("Value").to_frame(), use_container_width=True)


def _score_text(value: float) -> str:
    return f"{value:.1f}" if np.isfinite(value) else "Unavailable"


def show_valuation_research() -> None:
    mode = st.radio("Valuation data", ["Import local CSV", "Synthetic demonstration"], horizontal=True)
    synthetic = mode == "Synthetic demonstration"
    if synthetic:
        payload = synthetic_valuation_csv()
    else:
        upload = st.file_uploader(
            "Valuation CSV", type=["csv"],
            help="Run Streamlit locally for private or licensed files. See docs/VALUATION_DATA.md.",
        )
        if upload is None:
            st.info("Import a valuation CSV or select the synthetic demonstration to explore the tab.")
            return
        payload = upload.getvalue()

    try:
        dataset = parse_valuation_csv(payload)
    except ValuationDataError as exc:
        st.error(f"Valuation CSV failed validation. {exc}")
        return

    st.warning(CONFIDENCE[dataset.history_type])
    sector_col, metric_col, cutoff_col = st.columns(3)
    ticker = sector_col.selectbox("Sector", list(SECTORS), format_func=lambda x: f"{x} · {SECTORS[x]}")
    metric = metric_col.selectbox("Valuation metric", list(VALUATION_METRICS[ticker]),
                                  format_func=METRIC_LABELS.__getitem__)
    now = pd.Timestamp.now(tz="UTC")
    default = pd.Timestamp(DEMO_ASOF) if synthetic else now
    day = cutoff_col.date_input(
        "As-of date (New York)", value=default.tz_convert(MARKET_TZ).date(),
        max_value=now.tz_convert(MARKET_TZ).date(), min_value=pd.Timestamp("1993-01-29").date(),
        key=f"valuation_cutoff_{mode}",
    )
    if day is None:
        st.info("Select an as-of date.")
        return
    cutoff = pd.Period(day, freq="D").end_time.tz_localize(MARKET_TZ).tz_convert("UTC")
    cutoff = min(cutoff, now)
    st.caption(f"Cutoff {cutoff.tz_convert(MARKET_TZ).isoformat()}. {SECTOR_NOTES[ticker]}")

    sector = evaluate_sector(dataset, ticker, asof=cutoff)
    entry = sector.metrics[metric]
    historical, relative, blended = st.columns(3)
    historical.metric("Sector historical score", _score_text(sector.historical_score))
    relative.metric("Sector relative score", _score_text(sector.relative_score))
    blended.metric("70/30 valuation score", _score_text(sector.blended_score))
    higher = VALUATION_METRICS[ticker][metric][1]
    direction = "Higher" if higher else "Lower"
    units = "decimal yield" if metric in YIELD_METRICS else "multiple"
    st.caption(
        f"{METRIC_EXPLANATIONS[metric]} Displayed as a {units}. "
        f"{direction} values are more attractive."
    )
    _readouts(entry)
    with st.expander("Sector metric coverage"):
        st.dataframe(pd.DataFrame([
            {
                "Metric": METRIC_LABELS[key],
                "Observation month": str(item.current["month"]) if item.current is not None else "—",
                "Historical": STATUSES[item.absolute.status],
                "Relative": STATUSES[item.relative.status],
            }
            for key, item in sector.metrics.items()
        ]), use_container_width=True, hide_index=True)

    history = metric_history(dataset, ticker, metric, asof=cutoff)
    if not history:
        st.info("No historical observations are visible at this cutoff.")
        return
    frame = _history_frame(history, dataset.history_type == "reconstructed")
    _chart(frame, "Historical valuation", ["Valuation"])
    _chart(frame, "Historical robust Z-scores", ["Sector Z", "Relative Z"])
    _chart(frame, "Historical attractiveness scores and percentiles",
           ["Sector score", "Relative score", "Sector percentile", "Relative percentile"],
           range_y=[0, 100])
    _chart(frame, "Historical SPY premium or yield spread", ["SPY premium or spread"])
