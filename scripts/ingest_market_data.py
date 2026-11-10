"""Download a new local market-price snapshot; never alter demo or signal files."""

import argparse
from pathlib import Path

from sectorlab.market_data import AlphaVantageProvider, ingest

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="Inclusive completed session YYYY-MM-DD")
    parser.add_argument("--snapshot", required=True, help="New directory name under data/market/")
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Retain and report gaps; never fill missing prices",
    )
    args = parser.parse_args()
    if (
        not args.snapshot
        or Path(args.snapshot).name != args.snapshot
        or args.snapshot in (".", "..")
    ):
        parser.error("--snapshot must be a single new directory name")
    path = ingest(
        AlphaVantageProvider(),
        args.start,
        args.end,
        ROOT / "data" / "market" / args.snapshot,
        allow_missing=args.allow_missing,
    )
    print(f"Saved adjusted-price proxy snapshot: {path} (not verified total returns)")


if __name__ == "__main__":
    main()
