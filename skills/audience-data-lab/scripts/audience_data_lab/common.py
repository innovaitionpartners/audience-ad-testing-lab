"""Strict validation and canonical serialization helpers."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class ContractError(ValueError):
    """Raised when an input or output violates a strict contract."""


def exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown:
        raise ContractError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{path} is missing fields: {', '.join(missing)}")
    return dict(value)


def require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path} must be a string")
    if not allow_empty and not value.strip():
        raise ContractError(f"{path} must not be empty")
    return value


def require_nullable_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return require_string(value, path)


def require_identifier(value: Any, path: str) -> str:
    text = require_string(value, path)
    if not _ID_RE.fullmatch(text):
        raise ContractError(f"{path} must be a lowercase hyphenated identifier")
    return text


def require_timestamp(value: Any, path: str) -> str:
    text = require_string(value, path)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{path} must be an ISO-8601 timestamp") from exc
    return text


def require_boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{path} must be a boolean")
    return value


def require_integer(
    value: Any,
    path: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ContractError(f"{path} must be at most {maximum}")
    return value


def require_number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{path} must be finite")
    if minimum is not None and number < minimum:
        raise ContractError(f"{path} must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise ContractError(f"{path} must be at most {maximum}")
    return number


def require_enum(value: Any, allowed: set[str], path: str) -> str:
    text = require_string(value, path)
    if text not in allowed:
        raise ContractError(f"{path} must be one of: {', '.join(sorted(allowed))}")
    return text


def require_string_list(
    value: Any,
    path: str,
    *,
    nonempty: bool = False,
) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    if nonempty and not value:
        raise ContractError(f"{path} must not be empty")
    result = [require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise ContractError(f"{path} must not contain duplicates")
    return result


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError(
            f"could not hash private input file '{path}': {exc}"
        ) from exc
    return f"sha256:{digest.hexdigest()}"


def create_new_directory(path_value: str | Path, label: str) -> Path:
    """Create one new output directory without overwriting existing content."""

    path = Path(path_value)
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ContractError(
            f"{label} already exists: {path}. Choose a new path; existing outputs are never overwritten."
        ) from exc
    except OSError as exc:
        raise ContractError(f"could not create {label} '{path}': {exc}") from exc
    return path


def write_new_bytes(path_value: str | Path, value: bytes, label: str) -> Path:
    """Write one new file atomically with respect to path creation."""

    path = Path(path_value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError as exc:
        raise ContractError(
            f"{label} already exists: {path}. Choose a new path; existing outputs are never overwritten."
        ) from exc
    except OSError as exc:
        raise ContractError(f"could not write {label} '{path}': {exc}") from exc
    return path


def sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values), key=lambda item: (item.casefold(), item))
