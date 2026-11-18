"""Write the Report #001 evidence template or private research artifacts."""

from __future__ import annotations

import importlib.metadata
import json
import tempfile
from pathlib import Path

import pandas as pd

from sectorlab.market_data.pipeline import RETROSPECTIVE
from sectorlab.market_data.price_only import load_price_only

from .study import PriceStudy, run_price_study

TITLE = "Sector Momentum & Rotation — Exploratory Retrospective Study"
SECTIONS = (
    "Executive Summary",
    "Research Question and Null Hypothesis",
    "Data Source, Coverage and Limitations",
    "Signal Construction",
    "Strategy and Execution Methodology",
    "Performance vs SPY and Equal-weight Sectors",
    "Risk and Drawdown Analysis",
    "Turnover and Transaction Cost Sensitivity",
    "Rank IC and Forward Return Diagnostics",
    "Robustness and Regime Analysis",
    "Research Limitations",
    "Conclusion and Future Validation",
)
METHOD = {
    1: (
        "Do higher Phase 2 momentum scores precede higher relative returns across the sector ETFs? "
        "The null is zero mean monthly cross-sectional rank IC at each prespecified 1-, 3-, and "
        "6-month horizon. Strategy differences are descriptive and reported separately."
    ),
    3: (
        "The Phase 2 score uses 40% 6–1 relative momentum, 40% 12–1 relative momentum, and "
        "20% 3-month relative momentum. Inputs are XNYS session closes. The longest offset is "
        "252 sessions, requiring 253 session slots through the decision session. Weights and "
        "lookbacks were fixed before analysis."
    ),
    4: (
        "Phase 5 compares SPY buy and hold, equal-weight sectors, Top 3 Momentum and Top 5 "
        "Momentum. The primary common universe requires all 11 sectors. Signals occur at the "
        "last XNYS session close of each calendar month. Trades execute at the next close "
        "and earn the following session's return. Portfolios start in cash. The annual "
        "risk-free rate is 0% and annual turnover uses half the L1 change in weights, including "
        "cash, divided by elapsed years."
    ),
    10: (
        "A modern adjusted-price vintage can embed subsequent distributions and corrections. "
        "Historical availability, the full corporate-action ledger and exact reinvestment "
        "are unverified. The ETF universe was selected in the present. Simulated costs "
        "exclude taxes, execution uncertainty and market impact beyond the assumed charge. "
        "Valuation, earnings revision, quality and Opportunity Scores are outside this analysis."
    ),
}
TEMPLATE = {
    0: "**Evidence pending.** No licensed, validated adjusted-close panel was supplied for this report.",
    2: (
        "Required evidence includes all 11 sector ETFs plus SPY, contiguous post-listing XNYS "
        "sessions, source and retrieval vintage, adjustment basis, raw checksum, license "
        "reference and sufficient warm-up. IBKR requires original TWS ADJUSTED_LAST evidence."
    ),
    5: "Pending validated prices and an executable common-universe sample.",
    6: "Pending aligned daily NAV paths and completed month-end observations.",
    7: "Pending trade and NAV outputs for gross, 5 bps and 10 bps one-way assumptions.",
    8: "Pending complete forward windows and enough monthly observations for overlap-aware inference.",
    9: "Pending a dynamic-universe sensitivity and lagged SPY regime splits.",
    11: (
        "The study is awaiting evidence. Confirm research and publication rights, replay "
        "the private snapshot, and obtain independent action and original-vintage evidence "
        "before considering stronger performance claims."
    ),
}


def _table(frame: pd.DataFrame) -> str:
    columns = frame.columns.tolist()
    if frame.empty:
        return "No complete observations in this sample."

    def cell(value: object) -> str:
        if pd.isna(value):
            return "—"
        if isinstance(value, float):
            return f"{value:.6g}"
        return str(value).replace("|", r"\|").replace("\n", " ")

    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    lines.extend(
        "| " + " | ".join(map(cell, row)) + " |" for row in frame.itertuples(index=False, name=None)
    )
    return "\n".join(lines)


def _market_sections(study: PriceStudy) -> dict[int, str]:
    m = study.snapshot.manifest
    source = m["provenance"]
    raw = "sha256 " + source["raw_sha256"]
    coverage = pd.DataFrame.from_dict(m["coverage"], orient="index").rename_axis("ticker")
    gross = study.metrics.query("universe_policy == 'common' and one_way_bps == 0")
    common_window = study.sensitivity.query("universe_policy == 'common' and one_way_bps == 0")
    return {
        0: (
            f"The common-universe comparison spans {study.primary.metadata['baseline_session']} "
            f"through {study.primary.metadata['last_session']}. The study describes adjusted-price "
            "proxy returns. The rank IC analysis tests exploratory associations."
        ),
        2: (
            f"Provider {m['provider']}. Source identifier {source['source_id']}. Original latest "
            f"retrieval {source['retrieved_at']}. Source reference {source['source_reference']}. "
            f"License reference {source['license_reference']}. Raw evidence {raw}. "
            f"Manifest SHA-256 `{study.snapshot.manifest_sha256}`. "
            f"Requested window {m['start']} through {m['end']}.\n\n"
            + _table(coverage.drop(columns="missing_sessions").reset_index())
        ),
        5: (
            "Growth comparisons use wealth ratios over the common window. Performance values "
            "are decimals and hit rates use complete calendar months.\n\n"
            + _table(
                gross[["strategy", "cagr", "win_rate", "win_rate_vs_spy", "complete_month_count"]]
            )
            + "\n\n"
            + _table(common_window[["strategy", "relative_vs_spy", "relative_vs_equal_weight"]])
            + "\n\n![Common-universe adjusted-price growth](growth.png)"
        ),
        6: (
            _table(
                gross[
                    [
                        "strategy",
                        "annualized_volatility",
                        "sharpe_ratio",
                        "max_drawdown",
                        "information_ratio_vs_spy",
                        "tracking_error_vs_spy",
                    ]
                ]
            )
            + "\n\n![Common-universe drawdowns](drawdowns.png)\n\n"
            + (
                "![Rolling 12-month relative wealth against SPY](relative.png)"
                if study.primary.rolling_12m.relative_return.notna().any()
                else "The sample has no complete rolling 12-month comparison."
            )
        ),
        7: (
            "Charges are hypothetical all-in rates on modeled ETF buys and sells, including "
            "entry. Gross uses 0 bps. Net scenarios use 5 or 10 bps per traded dollar. "
            "All strategies, including SPY, pay the scenario rate.\n\n"
            + _table(
                study.metrics.query("universe_policy == 'common'")[
                    [
                        "strategy",
                        "one_way_bps",
                        "basis",
                        "cagr",
                        "annualized_portfolio_turnover",
                    ]
                ]
            )
        ),
        8: (
            "Each IC is a Spearman correlation across the 11 sectors, with average ranks for ties. "
            "The forward return is the sector wealth ratio divided by SPY's, minus one. Entry "
            "and exit use the next session close after each corresponding month-end. Incomplete "
            "windows and constant cross-sections supply no IC. Overlapping ICs are evaluated with "
            "Newey–West Bartlett HAC on calendar months using at least the horizon's lag count "
            "when sample length permits. Intervals and p-values are asymptotic and unadjusted "
            "for multiple comparisons. Inference requires at least 24 valid months and more "
            "than twice the lag count. Forward spread averages are descriptive.\n\n"
            + _table(study.inference)
            + "\n\n"
            + _table(study.forward_summary)
        ),
        9: (
            "The dynamic universe follows the Phase 5 minimum of five scored sectors and may "
            "start earlier. The common-window sensitivity rebases all paths at the primary "
            "baseline with prior dynamic holdings carried in. Its equal-weight benchmark is "
            "the equal-weight strategy under that universe. Regimes use the sign of "
            "SPY's trailing 126-session return at the decision close. Regime ICs are "
            "descriptive and subgroup sample sizes can be small.\n\n"
            + _table(study.sensitivity)
            + "\n\n"
            + _table(study.regimes)
        ),
        11: (
            "Interpret portfolio statistics as sample descriptions and the IC tests as exploratory "
            "predictive evidence. Independent total-return reconciliation, an original-vintage "
            "dataset, a prespecified holdout and execution evidence are needed to support stronger claims."
        ),
    }


def _charts(study: PriceStudy, output: Path) -> None:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import PercentFormatter

    paths = study.primary.daily.pivot(index="session", columns="strategy", values="gross_nav")
    rolling = study.primary.rolling_12m.query("basis == 'gross'").pivot(
        index="session",
        columns="strategy",
        values="relative_return",
    )
    series = [
        ("growth", "Growth of one dollar", paths),
        ("drawdowns", "Drawdown from previous peak", paths.div(paths.cummax()).sub(1)),
    ]
    if rolling.notna().any().any():
        series.append(("relative", "Rolling 12-month relative wealth vs SPY", rolling))
    for filename, label, values in series:
        fig = Figure(figsize=(9, 4.2), layout="constrained")
        FigureCanvasAgg(fig)
        axes = fig.subplots()
        for strategy, column in values.items():
            axes.plot(column.index, column, label=strategy.replace("_", " "))
        fig.suptitle("Exploratory retrospective adjusted-price proxy")
        axes.set_title(label)
        if filename != "growth":
            axes.yaxis.set_major_formatter(PercentFormatter(1))
        axes.grid(alpha=0.2)
        axes.legend(loc="best", fontsize="small")
        fig.savefig(output / f"{filename}.png", dpi=160)


def write_report(output: Path, snapshot_path: Path | None = None) -> Path:
    """Generate a template unless verified declared-market inputs are available."""
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    snapshot = load_price_only(snapshot_path) if snapshot_path is not None else None
    study = (
        run_price_study(snapshot_path)
        if snapshot is not None and snapshot.research_classification == RETROSPECTIVE
        else None
    )
    sections = METHOD | (_market_sections(study) if study else TEMPLATE)
    status = (
        "RETROSPECTIVE_ADJUSTED_PRICE_PROXY · PRICE_ONLY_UNRECONCILED · EXPLORATORY"
        if study
        else "TEMPLATE — EMPIRICAL EVIDENCE PENDING"
    )
    text = f"# Report #001\n\n## {TITLE}\n\n**{status}**\n\n"
    for index, name in enumerate(SECTIONS):
        text += f"### {index + 1}. {name}\n\n{sections[index]}\n\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".report-001-", dir=output.parent) as tmp:
        stage = Path(tmp) / "report"
        stage.mkdir(mode=0o700)
        (stage / "README.md").write_text(text.rstrip() + "\n", encoding="utf-8")
        if study is not None:
            _charts(study, stage)
            tables = {
                "performance": study.metrics,
                "sensitivity": study.sensitivity,
                "forward_returns": study.forward,
                "monthly_rank_ic": study.monthly_ic,
                "rank_ic_inference": study.inference,
                "forward_summary": study.forward_summary,
                "regimes": study.regimes,
                "rolling_12m": study.rolling_12m,
                "primary_trades": study.primary.trades,
                "primary_daily": study.primary.daily,
                "primary_decisions": study.primary.decisions,
            }
            labels = {
                "research_classification": study.snapshot.research_classification,
                "data_assurance": study.snapshot.manifest["data_assurance"],
                "empirical_claim_status": study.snapshot.manifest["empirical_claim_status"],
                "snapshot_manifest_sha256": study.snapshot.manifest_sha256,
            }
            for name, frame in tables.items():
                frame.assign(**labels).to_csv(stage / f"{name}.csv", index=False)
            metadata = study.primary.metadata | {
                "one_way_cost_bps": [0, 5, 10],
                "universe_policies": ["common", "dynamic"],
                "matplotlib_version": importlib.metadata.version("matplotlib"),
            }
            (stage / "metadata.json").write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    return output / "README.md"
