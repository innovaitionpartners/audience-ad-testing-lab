"""Local-only CSV and JSONL loading without row persistence."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .common import ContractError


def load_rows(path_value: str | Path) -> tuple[list[str], list[dict[str, Any]]]:
    path = Path(path_value)
    suffix = path.suffix.casefold()
    try:
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise ContractError("input CSV must contain a header row")
                columns = [str(item) for item in reader.fieldnames]
                if len(columns) != len(set(columns)):
                    raise ContractError("input CSV contains duplicate column names")
                rows = [dict(row) for row in reader]
        elif suffix in {".jsonl", ".ndjson"}:
            rows = []
            columns = []
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ContractError(
                            f"input JSONL line {line_number} is invalid JSON"
                        ) from exc
                    if not isinstance(item, dict):
                        raise ContractError(
                            f"input JSONL line {line_number} must be an object"
                        )
                    if not columns:
                        columns = [str(key) for key in item]
                    elif set(item) != set(columns):
                        raise ContractError(
                            f"input JSONL line {line_number} has a different schema"
                        )
                    rows.append(dict(item))
        else:
            raise ContractError("input must be .csv, .jsonl, or .ndjson")
    except ContractError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ContractError(
            f"could not read private tabular input '{path}' as {suffix or 'an unknown format'}: {exc}"
        ) from exc
    if not columns:
        raise ContractError("input must contain at least one column")
    if not rows:
        raise ContractError("input must contain at least one data row")
    return columns, rows


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def as_float(value: Any) -> float | None:
    if is_missing(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
