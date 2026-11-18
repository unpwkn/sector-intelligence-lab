"""Local research controls and views for the independent Phase 4 engines."""

from pathlib import Path

import pandas as pd
import streamlit as st

from ..config import QUALITY_WEIGHTS, REVISION_WEIGHTS, SECTORS
from .aggregation import ResearchResult
from .data import ResearchDataError, load_research_csv, parse_research_csv
from .demo import DEMO_ASOF, demo_csv
from .earnings import earnings_research
from .quality import quality_research


def _render(result: ResearchResult, components: dict[str, float], score: str) -> None:
    sectors = result.sectors.copy()
    sectors.insert(0, "sector", sectors.index.map(SECTORS))
    columns = ["sector", score, "status", "joint_covered_weight", "joint_issuer_count"]
    for component in components:
        columns.append(component)
        if f"{component}_score" in sectors:
            columns.append(f"{component}_score")
        columns.extend([f"{component}_covered_weight", f"{component}_status"])
    st.dataframe(sectors[columns])
    if not result.issuers.empty:
        with st.expander("Issuer observations and coverage"):
            st.dataframe(result.issuers, hide_index=True)


def show_fundamental_research(local: Path) -> None:
    """Keep real input, demo input, and Phase 0 scores in distinct controls and views."""
    st.subheader("Earnings and quality research")
    mode = st.radio(
        "Data source",
        ["Local point-in-time CSV", "Synthetic fixture"],
        horizontal=True,
    )
    earnings_view, quality_view, coverage_view = st.tabs(
        [
            "Earnings Revision Research",
            "Fundamental Quality Research",
            "Sector Coverage and Source Confidence",
        ]
    )
    try:
        if mode == "Synthetic fixture":
            st.error("SYNTHETIC FIXTURE — all issuers, weights and observations are fictional.")
            dataset = parse_research_csv(demo_csv())
            default_asof = DEMO_ASOF
        else:
            paths = sorted(local.glob("*.csv"))
            if not paths:
                for view in (earnings_view, quality_view, coverage_view):
                    view.info("No local Phase 4 CSVs. See docs/EARNINGS_QUALITY_DATA.md.")
                return
            name = st.selectbox("Local Phase 4 bundle", [path.name for path in paths])
            dataset = load_research_csv(local / name)
            if dataset.history_type != "point_in_time":
                raise ResearchDataError("The local point-in-time view requires point_in_time rows")
            st.warning(
                "Point-in-time provenance is declared by the importer. Check the audit evidence."
            )
            default_asof = pd.Timestamp.now(tz="UTC").floor("s").isoformat()
        asof = st.text_input(
            "Research cutoff (ISO-8601 with UTC offset)",
            value=default_asof,
            key=f"phase4-asof-{mode}",
        )
        earnings = earnings_research(dataset, asof=asof)
        quality = quality_research(dataset, asof=asof)
    except (ValueError, OSError, OverflowError) as exc:
        st.error(f"Phase 4 input failed validation. {exc}")
        return

    with earnings_view:
        st.caption(
            "EPS components show guarded revision fractions. Breadth shows net matched "
            "analyst upgrades over 3 months. Component scores are eligible-sector ranks."
        )
        _render(earnings, REVISION_WEIGHTS, "earnings_revision_score")
    with quality_view:
        st.caption("Pillars are 0–100 using subindustry metrics and fixed research anchors.")
        _render(quality, QUALITY_WEIGHTS, "quality_score")
    with coverage_view:
        st.caption(f"Research cutoff {earnings.asof.isoformat()}")
        st.code(f"Bundle SHA-256 {earnings.sha256}")
        st.caption(
            "Source coverage counts visible candidate inputs. Component coverage identifies "
            "the weight that passed the research rules."
        )
        for label, result, components in (
            ("Earnings coverage", earnings, REVISION_WEIGHTS),
            ("Quality coverage", quality, QUALITY_WEIGHTS),
        ):
            st.markdown(f"**{label}**")
            columns = [
                "holdings_date",
                "holdings_available_at",
                "joint_covered_weight",
                "issuer_count",
                "status",
                *(f"{component}_covered_weight" for component in components),
            ]
            st.dataframe(result.sectors[columns])
            st.dataframe(result.source_coverage, hide_index=True)
