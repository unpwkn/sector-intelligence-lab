"""Read-only Streamlit view of a locally sourced Phase 5 comparison."""

from pathlib import Path

import streamlit as st

from sectorlab.backtest import CostAssumptions, run_backtest
from sectorlab.backtest.engine import SYNTHETIC

STRATEGY_LABELS = {
    "spy_buy_hold": "SPY buy and hold",
    "equal_weight_sectors": "Equal-weight sectors",
    "top_3_momentum": "Top 3 momentum",
    "top_5_momentum": "Top 5 momentum",
}


def show_backtest_research(local: Path) -> None:
    st.caption(
        "Monthly signals use verified local price snapshots. Trades are modeled at the next "
        "XNYS session close, with new holdings earning returns afterward."
    )
    snapshots = (
        sorted(
            folder.name
            for folder in local.iterdir()
            if not folder.name.startswith(".") and (folder / "manifest.json").is_file()
        )
        if local.is_dir()
        else []
    )
    if not snapshots:
        st.info("No local market snapshots. See `docs/BACKTESTING.md` for supported inputs.")
        return

    with st.form("phase5_backtest"):
        snapshot = st.selectbox("Backtest snapshot", snapshots)
        universe = st.selectbox(
            "Sector universe",
            ("common", "dynamic"),
            format_func={
                "common": "Common (all 11 sectors)",
                "dynamic": "Dynamic (at least 5)",
            }.get,
        )
        commission, spread, slippage = st.columns(3)
        commission_bps = commission.number_input("Commission per side (bps)", min_value=0.0)
        full_spread_bps = spread.number_input("Full bid–ask spread (bps)", min_value=0.0)
        slippage_bps = slippage.number_input("Slippage per side (bps)", min_value=0.0)
        submitted = st.form_submit_button("Run backtest")

    if submitted:
        st.session_state.pop("phase5_report", None)
        try:
            costs = CostAssumptions(commission_bps, full_spread_bps, slippage_bps)
            report = run_backtest(local / snapshot, universe=universe, costs=costs)
        except ValueError as exc:
            st.error(f"Backtest could not run. {exc}")
            return
        st.session_state["phase5_report"] = (snapshot, report)

    if "phase5_report" not in st.session_state:
        return
    selected_snapshot, report = st.session_state["phase5_report"]
    metadata = report.metadata
    classification = metadata["research_classification"]
    if classification == SYNTHETIC:
        st.error(f"{classification} — fictional prices. Results do not measure market performance.")
    else:
        st.warning(f"{classification} — historical availability and total returns are unverified.")

    costs = metadata["cost_assumptions"]
    st.caption(
        f"{selected_snapshot} · {metadata['universe_policy'].title()} universe · "
        f"{metadata['baseline_session']} to {metadata['last_session']} · "
        f"Commission {costs['commission_bps']:g} bps per side · "
        f"Spread {costs['full_spread_bps']:g} bps full width · "
        f"Slippage {costs['slippage_bps']:g} bps per side"
    )
    basis = st.radio("Return basis", ("net", "gross"), horizontal=True, key="phase5_basis")
    st.subheader("Growth of one dollar")
    daily = report.daily.assign(strategy=report.daily["strategy"].map(STRATEGY_LABELS))
    st.line_chart(daily.pivot(index="session", columns="strategy", values=f"{basis}_nav"))

    st.subheader("Performance metrics")
    metrics = report.metrics.loc[report.metrics["basis"].eq(basis)].copy()
    metrics["strategy"] = metrics["strategy"].map(STRATEGY_LABELS)
    st.dataframe(
        metrics.drop(
            columns=[
                "research_classification",
                "snapshot_manifest_sha256",
                "universe_policy",
                "basis",
            ]
        ),
        hide_index=True,
    )

    rolling = report.rolling_12m.loc[report.rolling_12m["basis"].eq(basis)].dropna(
        subset=["relative_return"]
    )
    st.subheader("Rolling 12-month relative return vs SPY")
    if rolling.empty:
        st.info("Rolling returns require 12 complete paired months.")
    else:
        rolling = rolling.assign(strategy=rolling["strategy"].map(STRATEGY_LABELS))
        st.line_chart(rolling.pivot(index="session", columns="strategy", values="relative_return"))

    with st.expander("Trade log and snapshot provenance"):
        st.code(metadata["snapshot_manifest_sha256"])
        st.json(metadata["snapshot_manifest"], expanded=False)
        st.dataframe(report.trades, hide_index=True)
