"""Save a validated daily-price snapshot from Alpha Vantage or an offline CSV."""

import argparse
import json
from pathlib import Path

from sectorlab.market_data import AlphaVantageProvider, ProviderError, import_csv, ingest

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="Local daily-price CSV with canonical fields")
    parser.add_argument("--provenance", type=Path, help="Required source declaration for --csv")
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
    if (args.csv is None) != (args.provenance is None):
        parser.error("--csv and --provenance must be supplied together")
    output = ROOT / "data" / "market" / args.snapshot
    try:
        if args.csv is not None:
            path = import_csv(
                args.csv, args.provenance, args.start, args.end, output,
                allow_missing=args.allow_missing,
            )
        else:
            path = ingest(
                AlphaVantageProvider(), args.start, args.end, output,
                allow_missing=args.allow_missing,
            )
    except (OSError, ProviderError, ValueError) as exc:
        parser.error(str(exc))
    manifest = json.loads((path / "manifest.json").read_bytes())
    print(f"{manifest['research_classification']}: {path}")


if __name__ == "__main__":
    main()
