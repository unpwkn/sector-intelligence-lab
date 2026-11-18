"""Private Streamlit view for the price-only exploratory data contract."""

from pathlib import Path

import pandas as pd
import streamlit as st

from sectorlab.market_data.pipeline import SYNTHETIC, ValidationError
from sectorlab.market_data.price_only import load_price_only
from sectorlab.price_momentum import price_momentum_history

from .study import run_price_study


def show_price_only_research(root: Path | None, checkout: Path) -> None:
    st.warning(
        "Exploratory adjusted-price research. Corporate actions, point-in-time availability "
        "and total returns are unverified."
    )
    if root is None:
        st.info("Set SECTORLAB_PRICE_ONLY_DIR on your private dashboard host to view a snapshot.")
        return
    root = root.expanduser().resolve()
    if root.is_relative_to(checkout):
        st.error("Configure snapshot storage outside the repository.")
        return
    choices = (
        sorted(
            p.name
            for p in root.iterdir()
            if p.is_dir() and not p.is_symlink() and (p / "manifest.json").is_file()
        )
        if root.is_dir()
        else []
    )
    if not choices:
        st.info("No price-only snapshots found. See the Phase 6C workflow in the README.")
        return
    name = st.selectbox("Price-only snapshot", choices)
    path = root / name
    try:
        snapshot = load_price_only(path)
    except ValidationError as exc:
        st.error(str(exc))
        return
    m = snapshot.manifest
    if snapshot.research_classification == SYNTHETIC:
        st.error("SYNTHETIC OFFLINE FIXTURE — fictional prices.")
    st.caption(
        f"{snapshot.research_classification} · {m['data_assurance']} · {m['empirical_claim_status']}"
    )
    st.write(f"{m['provider']} · {m['start']} to {m['end']}")
    with st.expander("Session coverage and original retrievals", expanded=True):
        coverage = pd.DataFrame.from_dict(m["coverage"], orient="index")
        coverage["retrieved_at"] = pd.Series(m["retrievals"])
        st.dataframe(coverage.drop(columns="missing_sessions"))
        st.code(snapshot.manifest_sha256)
    dates = snapshot.sessions.strftime("%Y-%m-%d").tolist()
    cutoff = st.selectbox("Price-only momentum session", dates, index=len(dates) - 1)
    history = price_momentum_history(snapshot, asof=cutoff)
    rankings = history.loc[history.signal_date.eq(pd.Timestamp(cutoff))]
    st.dataframe(
        rankings[["ticker", "price_momentum_rank", "price_momentum_score", "status"]].sort_values(
            ["price_momentum_rank", "ticker"]
        ),
        hide_index=True,
    )
    if st.button("Run exploratory strategy comparisons"):
        try:
            study = run_price_study(path)
        except ValidationError as exc:
            st.error(str(exc))
            return
        st.subheader("Common-universe gross comparison")
        wealth = study.primary.daily.pivot(index="session", columns="strategy", values="gross_nav")
        st.line_chart(wealth)
        st.subheader("Common and dynamic universe cost scenarios")
        st.dataframe(
            study.metrics[
                [
                    "universe_policy",
                    "strategy",
                    "one_way_bps",
                    "basis",
                    "baseline_session",
                    "cagr",
                    "max_drawdown",
                    "annualized_portfolio_turnover",
                ]
            ],
            hide_index=True,
        )
        st.subheader("Exploratory rank IC by horizon")
        st.dataframe(study.inference, hide_index=True)
