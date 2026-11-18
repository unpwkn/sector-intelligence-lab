"""Read-only Streamlit viewer for the synthetic demo and local price research."""

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from sectorlab.backtest.dashboard import show_backtest_research
from sectorlab.config import SECTORS
from sectorlab.fundamental_research.dashboard import show_fundamental_research
from sectorlab.market_data import ValidationError, load_snapshot
from sectorlab.market_data.pipeline import SYNTHETIC
from sectorlab.price_momentum import price_momentum_history
from sectorlab.valuation_dashboard import show_valuation_research

ROOT = Path(__file__).resolve().parent
SIGNALS = ROOT / "data" / "processed" / "signals.csv"
MARKET = ROOT / "data" / "market"


def show_synthetic_demo() -> None:
    st.error("SYNTHETIC DEMO DATA — fictional prices and fundamentals.")
    if not SIGNALS.exists():
        st.info("Run `python scripts/make_demo_data.py` then `python scripts/run_pipeline.py`.")
        return

    signals = pd.read_csv(SIGNALS)
    if (
        signals.empty
        or "provenance" not in signals
        or not signals["provenance"].eq("SYNTHETIC_DEMO").all()
    ):
        st.error("The demo requires a signals file labeled SYNTHETIC_DEMO on every row.")
        return
    signals["Sector"] = signals["ticker"].map(SECTORS)
    valid = signals.dropna(subset=["valuation", "rotation", "opportunity"])

    c1, c2, c3 = st.columns(3)
    c1.metric("Sectors monitored", len(signals))
    c2.metric("Scored with complete inputs", len(valid))
    c3.metric("Model status", "Research / Not validated")

    st.subheader("Sector ranking")
    st.dataframe(
        signals[["ticker", "Sector", "valuation", "momentum", "revision", "quality",
                 "rotation", "opportunity", "classification", "data_status"]],
        use_container_width=True, hide_index=True,
    )

    st.subheader("Valuation vs Rotation")
    if valid.empty:
        st.info("No complete synthetic scores.")
    else:
        st.scatter_chart(valid, x="valuation", y="rotation", size="opportunity", color="ticker")

    st.caption("See `docs/METHODOLOGY.md` and `docs/DATA_POLICY.md` for the model and inputs.")


def show_price_research() -> None:
    st.caption(
        "Local snapshots use an adjusted-price proxy for price momentum. "
        "Total returns and historical data availability are unverified."
    )
    snapshots = (
        sorted(p.name for p in MARKET.iterdir() if p.is_dir() and not p.name.startswith("."))
        if MARKET.is_dir()
        else []
    )
    if not snapshots:
        st.info("No local market snapshots. See `docs/MARKET_DATA.md` for ingestion instructions.")
        return

    name = st.selectbox("Snapshot", snapshots)
    try:
        snapshot = load_snapshot(MARKET / name)
    except ValidationError as exc:
        st.error(f"Snapshot failed validation. {exc}")
        return

    manifest = snapshot.manifest
    if snapshot.research_classification == SYNTHETIC:
        st.error("SYNTHETIC OFFLINE FIXTURE — fictional prices.")
    else:
        st.caption("Retrospective adjusted-price research")
    st.caption(f"Snapshot provider: {manifest['provider']}")
    signal_dates = snapshot.sessions.strftime("%Y-%m-%d").tolist()
    signal_date = st.selectbox("Signal session (XNYS)", signal_dates, index=len(signal_dates) - 1)
    history = price_momentum_history(snapshot, asof=signal_date)
    scores = history.loc[history["signal_date"] == pd.Timestamp(signal_date)].copy()
    scores["Sector"] = scores["ticker"].map(SECTORS)
    scores = scores.sort_values(["price_momentum_rank", "ticker"])
    peer_count = scores["peer_count"].iloc[0]

    st.caption(
        f"{peer_count} of {len(SECTORS)} sectors have all three components on {signal_date}. "
        "A score requires at least two eligible sectors. Momentum components are log returns "
        "in log percentage points, relative to SPY."
    )
    for component in ("rs_6_1", "rs_12_1", "rs_3m"):
        scores[component] *= 100
    scores["relative_performance_3m"] *= 100
    st.dataframe(
        scores[
            ["price_momentum_rank", "ticker", "Sector", "price_momentum_score", "rs_6_1",
             "rs_12_1", "rs_3m", "relative_performance_3m", "status", "rs_6_1_status",
             "rs_12_1_status", "rs_3m_status"]
        ],
        column_config={
            "price_momentum_rank": st.column_config.NumberColumn("Rank", format="%d"),
            "price_momentum_score": st.column_config.NumberColumn("Price Momentum Score", format="%.2f"),
            "rs_6_1": st.column_config.NumberColumn("6–1", format="%.2f"),
            "rs_12_1": st.column_config.NumberColumn("12–1", format="%.2f"),
            "rs_3m": st.column_config.NumberColumn("3M", format="%.2f"),
            "relative_performance_3m": st.column_config.NumberColumn(
                "3M performance vs SPY", format="%.2f%%",
            ),
            "rs_6_1_status": "6–1 coverage",
            "rs_12_1_status": "12–1 coverage",
            "rs_3m_status": "3M coverage",
            "status": "Score status",
        },
        use_container_width=True, hide_index=True,
    )

    st.subheader("Price Momentum Score history")
    first_score = history.loc[history["status"] == "complete", "signal_date"].min()
    if pd.isna(first_score):
        st.info("No session through the selected date has enough data to rank the sectors.")
    else:
        plot_data = history.loc[history["signal_date"] >= first_score]
        chart = px.line(
            plot_data, x="signal_date", y="price_momentum_score", color="ticker",
            hover_data=["peer_count", "price_momentum_rank"],
            labels={
                "signal_date": "Signal date", "price_momentum_score": "Score",
                "ticker": "ETF", "peer_count": "Eligible sectors", "price_momentum_rank": "Rank",
            },
            range_y=[0, 100],
        )
        chart.update_traces(connectgaps=False)
        st.plotly_chart(chart, use_container_width=True)
        st.caption("Score changes can reflect changes in the eligible peer set.")

    with st.expander("Snapshot provenance and coverage"):
        st.write(
            f"Provider **{manifest['provider']}** · XNYS · {manifest['start']} to {manifest['end']}"
        )
        st.caption(
            f"Snapshot created {manifest['created_at']}. SHA-256 verified for prices.csv "
            "and raw source evidence."
        )
        st.code(f"Manifest SHA-256 {snapshot.manifest_sha256}")
        coverage = pd.DataFrame.from_dict(manifest["coverage"], orient="index")
        coverage.index.name = "Ticker"
        coverage["missing_count"] = coverage["missing_sessions"].str.len()
        coverage["retrieved_at"] = pd.Series(manifest["retrievals"])
        st.dataframe(
            coverage[["first_trading_session", "expected_sessions", "observations",
                      "missing_count", "retrieved_at"]],
            use_container_width=True,
        )


st.set_page_config(page_title="Sector Intelligence Lab", page_icon="📊", layout="wide")
st.title("Sector Intelligence Lab")
price_tab, valuation_tab, fundamental_tab, backtest_tab, demo_tab = st.tabs([
    "Price Momentum Research", "Sector Valuation Research", "Earnings and Quality",
    "Sector Rotation Backtest", "Synthetic Demo",
])
with price_tab:
    show_price_research()
with valuation_tab:
    show_valuation_research()
with fundamental_tab:
    show_fundamental_research(ROOT / "data" / "earnings_quality")
with backtest_tab:
    show_backtest_research(MARKET)
with demo_tab:
    show_synthetic_demo()
