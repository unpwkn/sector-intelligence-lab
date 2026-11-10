"""Run the illustrative research pipeline from CSV inputs."""
import argparse
from pathlib import Path

import pandas as pd

from sectorlab.factors import (
    combine_signals, momentum_scores, quality_scores,
    revision_scores, valuation_scores,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "demo")
    parser.add_argument("--asof", default="2026-10-30")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "signals.csv")
    parser.add_argument("--provenance", default="SYNTHETIC_DEMO")
    args = parser.parse_args()

    def load(name: str) -> pd.DataFrame:
        return pd.read_csv(args.data_dir / f"{name}.csv")

    v = valuation_scores(load("valuations"), args.asof)
    m = momentum_scores(load("prices"), load("breadth"), args.asof)
    e = revision_scores(load("revisions"), args.asof)
    q = quality_scores(load("quality"), args.asof)
    result = combine_signals(v, m, e, q, signal_date=args.asof, provenance=args.provenance)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.out, index=False)
    print(f"Saved {len(result)} sector signals to {args.out}")
    print("PROVENANCE:", args.provenance)
    print(result[["ticker", "valuation", "momentum", "revision", "quality",
                  "rotation", "opportunity", "classification", "data_status"]].to_string(index=False))


if __name__ == "__main__":
    main()
