"""Run and export an offline Phase 5 comparison."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from sectorlab.backtest import CostAssumptions, run_backtest
from sectorlab.backtest.fixture import make_fixture_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--snapshot", type=Path, help="Existing validated local market snapshot folder")
    sources.add_argument(
        "--fixture", action="store_true", help="Fictional offline software fixture"
    )
    parser.add_argument("--output", required=True, type=Path, help="New local export directory")
    parser.add_argument("--universe", choices=("common", "dynamic"), default="common")
    parser.add_argument(
        "--turnover-convention", choices=("half_l1", "etf_notional"), default="half_l1"
    )
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--full-spread-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--annual-risk-free", type=float, default=0.0, help="Annual decimal rate")
    args = parser.parse_args()

    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        costs = CostAssumptions(args.commission_bps, args.full_spread_bps, args.slippage_bps)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".phase5-", dir=args.output.parent) as tmp:
            export = Path(tmp) / "report"
            export.mkdir()
            source = (
                make_fixture_snapshot(export / "fixture_snapshot")
                if args.fixture
                else (args.snapshot)
            )
            result = run_backtest(
                source,
                universe=args.universe,
                costs=costs,
                annual_risk_free=args.annual_risk_free,
                turnover_convention=args.turnover_convention,
            )
            (export / "metadata.json").write_text(json.dumps(result.metadata, indent=2) + "\n")
            for name in ("decisions", "trades", "daily", "metrics", "monthly", "rolling_12m"):
                getattr(result, name).to_csv(export / f"{name}.csv", index=False)
            export.rename(args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    print(f"{result.metadata['research_classification']} ({args.universe}): {args.output}")


if __name__ == "__main__":
    main()
