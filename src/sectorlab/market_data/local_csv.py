"""Read an offline daily bar export together with its source declaration."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .providers import FIELDS, _unique_object

CSV_FILE = "raw/observations.csv"
PROVENANCE_FILE = "raw/provenance.json"
CSV_ENDPOINT = "local_csv"
CSV_FIELDS = {
    "source",
    "source_reference",
    "license_reference",
    "retrieved_at",
    "data_kind",
    "ohlc_basis",
    "adjusted_close_basis",
    "corporate_action_method",
}


@dataclass(frozen=True)
class LocalCsv:
    """Canonical CSV input and the exact bytes used for import and replay."""

    records: pd.DataFrame
    provenance: dict
    retrieved_at: datetime
    evidence: dict[str, bytes]

    @classmethod
    def read(cls, path: Path, provenance: Path) -> LocalCsv:
        return cls.parse(path.read_bytes(), provenance.read_bytes())

    @classmethod
    def parse(cls, observations: bytes, declaration: bytes) -> LocalCsv:
        provenance = json.loads(declaration, object_pairs_hook=_unique_object)
        if (
            not isinstance(provenance, dict)
            or set(provenance) != CSV_FIELDS
            or any(not isinstance(value, str) or not value.strip() for value in provenance.values())
        ):
            raise ValueError(
                "CSV provenance requires exactly these text fields: " + ", ".join(sorted(CSV_FIELDS))
            )
        if provenance["data_kind"] not in ("market", "synthetic"):
            raise ValueError("CSV data_kind must be market or synthetic")
        if provenance["ohlc_basis"] != "as_traded":
            raise ValueError("CSV requires as_traded OHLC")
        if provenance["adjusted_close_basis"] != "split_and_distribution_adjusted":
            raise ValueError("CSV requires split_and_distribution_adjusted close")
        retrieved_at = datetime.fromisoformat(provenance["retrieved_at"])
        if retrieved_at.tzinfo is None:
            raise ValueError("CSV retrieved_at must be timezone-aware")

        with io.StringIO(observations.decode("utf-8-sig"), newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, [])
            if len(header) != len(FIELDS) or set(header) != set(FIELDS):
                raise ValueError("CSV header must contain each daily price field exactly once")
            rows = []
            for row in reader:
                if len(row) != len(header):
                    raise ValueError(f"Wrong number of CSV fields at line {reader.line_num}")
                rows.append(row)
        if not rows:
            raise ValueError("CSV contains no price observations")
        return cls(
            pd.DataFrame(rows, columns=header),
            provenance,
            retrieved_at,
            {CSV_FILE: observations, PROVENANCE_FILE: declaration},
        )
