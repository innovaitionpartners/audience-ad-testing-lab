"""Shared strict-contract helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse


ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){7,15}(?!\w)")
HANDLE_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")
# Five hundred characters preserves enough local context for qualitative
# synthesis while limiting identity leakage and reusable-package bloat.
DEFAULT_EXCERPT_MAXIMUM = 500


class ContractError(ValueError):
    """One deterministic contract failure."""


def require_object(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown:
        raise ContractError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{path} is missing fields: {', '.join(missing)}")
    return value


def require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ContractError(f"{path} must be {'a string' if allow_empty else 'a non-empty string'}")
    return value


def require_identifier(value: Any, path: str) -> str:
    text = require_string(value, path)
    if not ID_RE.fullmatch(text):
        raise ContractError(f"{path} must be a canonical lowercase hyphenated identifier")
    return text


def require_array(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be an array")
    if nonempty and not value:
        raise ContractError(f"{path} must not be empty")
    return value


def require_string_array(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    values = require_array(value, path, nonempty=nonempty)
    result = [require_string(item, f"{path}[{index}]") for index, item in enumerate(values)]
    if len(set(result)) != len(result):
        raise ContractError(f"{path} must contain unique values")
    return result


def require_enum(value: Any, allowed: set[str], path: str) -> str:
    text = require_string(value, path)
    if text not in allowed:
        raise ContractError(f"{path} must be one of: {', '.join(sorted(allowed))}")
    return text


def require_timestamp(value: Any, path: str) -> datetime:
    text = require_string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{path} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{path} must include a timezone")
    return parsed.astimezone(timezone.utc)


def require_url(value: Any, path: str, *, allow_empty: bool = False) -> str:
    text = require_string(value, path, allow_empty=allow_empty)
    if not text and allow_empty:
        return text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{path} must be an HTTP or HTTPS URL")
    return text


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def sanitize_excerpt(
    value: Any,
    *,
    maximum: int = DEFAULT_EXCERPT_MAXIMUM,
) -> str:
    text = require_string(value, "text", allow_empty=True)
    text = EMAIL_RE.sub("[redacted email]", text)
    text = PHONE_RE.sub("[redacted phone]", text)
    text = HANDLE_RE.sub("[redacted handle]", text)
    text = " ".join(text.split())
    if len(text) > maximum:
        text = text[: maximum - 1].rstrip() + "…"
    return text


def write_new_bytes(path_value: str | Path, value: bytes, label: str) -> Path:
    """Write one new file without replacing an existing path."""

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


def create_new_directory(path_value: str | Path, label: str) -> Path:
    """Create one new output directory without replacing existing content."""

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


def get_path(value: Any, path: str | None) -> Any:
    if path is None:
        return None
    current = value
    if path == "":
        return current
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            return None
    return current


def numeric_mapping(value: Any) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int | float] = {}
    for key, item in value.items():
        if not isinstance(key, str) or isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        if item != item or item in (float("inf"), float("-inf")):
            continue
        result[key] = item
    return result
