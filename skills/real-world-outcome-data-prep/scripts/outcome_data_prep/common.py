from __future__ import annotations

import decimal
from collections.abc import Mapping
from decimal import Decimal
import hashlib
import json
import re


class ContractError(ValueError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def closed_object(value: object, keys: set[str], path: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(f"{path} must be an object")
    unknown = sorted(set(value) - keys)
    missing = sorted(keys - set(value))
    if unknown:
        raise ContractError(f"{path} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ContractError(f"{path} is missing fields: {', '.join(missing)}")
    return dict(value)


def require_string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{path} must be a non-empty string")
    return value


def require_identifier(value: object, path: str) -> str:
    result = require_string(value, path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", result):
        raise ContractError(f"{path} is not a canonical identifier")
    return result


def require_enum(value: object, allowed: set[str], path: str) -> str:
    result = require_string(value, path)
    if result not in allowed:
        raise ContractError(f"{path} is unsupported")
    return result


def require_string_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError(f"{path} must be a list")
    return [require_string(item, f"{path}[]") for item in value]


def require_numeric_string(value: object, path: str) -> str:
    result = require_string(value, path)
    try:
        parsed = Decimal(result)
    except decimal.InvalidOperation as exc:
        raise ContractError(f"{path} must be numeric") from exc
    if not parsed.is_finite():
        raise ContractError(f"{path} must be finite")
    return result


def require_numeric_string_or_number(value: object, path: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ContractError(f"{path} must be numeric")
    result = str(value)
    require_numeric_string(result, path)
    return result


def require_integer_string_or_int(value: object, path: str) -> int:
    if isinstance(value, bool):
        raise ContractError(f"{path} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{path} must be an integer") from exc
    if str(result) != str(value):
        raise ContractError(f"{path} must be a canonical integer")
    return result


def require_nonnegative_integer(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{path} must be a non-negative integer")
    return value
