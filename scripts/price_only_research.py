"""Import private price-only evidence or generate exploratory Report #001."""

import argparse
from csv import Error as CSVError
from pathlib import Path

from sectorlab.market_data.price_only import import_ibkr_price_only, import_price_only
from sectorlab.price_research.report import write_report

ROOT = Path(__file__).resolve().parents[1]


def _private(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.is_relative_to(ROOT):
        raise ValueError(
            "Keep price-only evidence, snapshots and empirical outputs outside the checkout"
        )
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="command", required=True)
    csv = modes.add_parser("import-csv", help="Import a non-IBKR adjusted-close export")
    csv.add_argument("--observations", type=Path, required=True)
    csv.add_argument("--provenance", type=Path, required=True)
    csv.add_argument("--start", required=True)
    csv.add_argument("--end", required=True)
    csv.add_argument("--output", type=Path, required=True)
    ibkr = modes.add_parser(
        "import-ibkr", help="Extract ADJUSTED_LAST from a verified Phase 6B archive"
    )
    ibkr.add_argument("--archive", type=Path, required=True)
    ibkr.add_argument("--source-reference", required=True)
    ibkr.add_argument("--license-reference", required=True)
    ibkr.add_argument("--output", type=Path, required=True)
    report = modes.add_parser("report", help="Generate Report #001")
    mode = report.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", type=Path)
    mode.add_argument("--template", action="store_true")
    report.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "import-csv":
            snapshot = import_price_only(
                _private(args.observations),
                _private(args.provenance),
                args.start,
                args.end,
                _private(args.output),
            )
        elif args.command == "import-ibkr":
            snapshot = import_ibkr_price_only(
                _private(args.archive),
                _private(args.output),
                source_reference=args.source_reference,
                license_reference=args.license_reference,
            )
        else:
            report_path = (
                write_report(args.output)
                if args.template
                else write_report(_private(args.output), _private(args.snapshot))
            )
            print(report_path)
            return
    except ImportError:
        parser.error("Install .[research] locally to generate empirical charts")
    except (CSVError, KeyError, TypeError, ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"{snapshot.research_classification} · {snapshot.manifest['data_assurance']}")


if __name__ == "__main__":
    main()
