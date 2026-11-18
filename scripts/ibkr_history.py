"""Capture or replay private daily ETF history from a local read-only TWS or IB Gateway."""

import argparse
from pathlib import Path

from sectorlab.market_data.ibkr import _json_bytes, capture_history, publish_archive, verify_archive
from sectorlab.market_data.ibkr_tws import connect_tws
from sectorlab.market_data.providers import ProviderError

ROOT = Path(__file__).resolve().parents[1]


def _private_path(path: str) -> Path:
    original = Path(path).expanduser()
    if original.is_symlink():
        raise argparse.ArgumentTypeError("Choose a private path without a final symlink")
    resolved = original.resolve()
    if resolved.is_relative_to(ROOT):
        raise argparse.ArgumentTypeError("Keep IBKR history and evidence outside the repository")
    return resolved


def _report(report: dict) -> None:
    # Detailed coverage, failed sessions and broker request codes stay inside the archive.
    print(f"Evaluated {len(report['instruments'])} ETFs.")
    if report["phase6a_export_eligible"]:
        print("Phase 6A retrospective export passed independent reconciliation.")
    else:
        print("Phase 6A export blocked. Review report.json in the private archive.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    capture = modes.add_parser("capture", help="Request daily data through your local IBKR session")
    capture.add_argument("--start", required=True)
    capture.add_argument("--end", required=True)
    capture.add_argument(
        "--port", type=int, required=True, help="Configured local TWS/Gateway port"
    )
    capture.add_argument("--client-id", type=int, default=106)
    capture.add_argument("--timeout", type=int, default=60, help="Seconds per callback request")
    capture.add_argument("--output", type=_private_path, required=True)
    capture.add_argument("--references", type=_private_path)

    verify = modes.add_parser("verify", help="Check private archive hashes and replay analysis")
    verify.add_argument("--archive", type=_private_path, required=True)

    reconcile = modes.add_parser("reconcile", help="Replay archived bars with independent evidence")
    reconcile.add_argument("--archive", type=_private_path, required=True)
    reconcile.add_argument("--references", type=_private_path, required=True)
    reconcile.add_argument("--output", type=_private_path, required=True)

    args = parser.parse_args()
    try:
        if args.mode == "verify":
            report, _ = verify_archive(args.archive)
        else:
            if args.output.exists():
                raise FileExistsError("Choose a new private archive directory")
            references = args.references.read_bytes() if args.references is not None else None
            if args.mode == "capture":
                if not (1 <= args.port <= 65535 and args.client_id > 0 and args.timeout > 0):
                    parser.error("Use a valid port, positive client id and positive timeout")
                with connect_tws(args.port, args.client_id, args.timeout) as transport:
                    raw = _json_bytes(capture_history(transport, args.start, args.end))
            else:
                _, raw = verify_archive(args.archive)
            report = publish_archive(raw, args.output, references)
    except (OSError, ProviderError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    _report(report)
    return 0 if report["phase6a_export_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
