"""Deterministic, aggregate-only Tier 4 validation packages.

The validation archive deliberately does not extend either portable panel
package format.  It is a small, closed archive that authenticates a previously
validated v2/v3 panel package by digest and carries only C1 validation records.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping
import zipfile

from ...common import ContractError, canonical_json_bytes, sha256_json
from .contracts import (
    EVALUATION_VERSION,
    TIER4_CLAIM_VERSION,
    authenticate_preregistration_design,
    project_shared_outcome_evidence,
    validate_claim_family,
    validate_comparison,
    validate_held_out_evaluation,
    validate_preregistration,
    validate_shared_outcome_evidence,
    validate_tier4_claim,
    validate_validation_observation,
)


SIBLING_SCRIPTS = Path(__file__).resolve().parents[5] / "audience-ad-testing-lab" / "scripts"
CANONICAL_REPORT_TEMPLATE = (
    Path(__file__).resolve().parents[4]
    / "assets" / "panel-validation-report-template.html"
)
if str(SIBLING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SIBLING_SCRIPTS))

from audience_lab.audience_package import (  # noqa: E402
    ARCHIVE_FILES as V2_ARCHIVE_FILES,
    PACKAGE_SCHEMA_VERSION as V2_PACKAGE_SCHEMA_VERSION,
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSION_RATIO,
    MAX_ENTRY_BYTES,
    MAX_TOTAL_BYTES,
    PackageSafetyError,
    PackageValidationError,
    _archive_bytes,
    _validate_report_html,
    read_safe_archive_members,
)
from audience_lab.audience_package_dispatch import validate_supported_audience_package  # noqa: E402
from audience_lab.audience_package_v3 import (  # noqa: E402
    archive_files_v3_for_manifest,
    read_v3_archive_manifest,
    read_v3_archive_members,
)


VALIDATION_PACKAGE_VERSION = "audience-panel-validation-package-v1"
VALIDATION_GENERATOR_VERSION = "1.0.0"
SUPPORTED_VALIDATION_GENERATOR_VERSIONS = frozenset({VALIDATION_GENERATOR_VERSION})

# Eleven payload members plus the manifest are the intentionally fixed C1
# archive world.  The claim result slot is selected below, never both.
PACKAGE_PREFIX_FILES = (
    "panel-validation-preregistration.json",
    "panel-shared-outcome-evidence.json",
    "panel-validation-observations.json",
    "panel-synthetic-outcome-comparisons.json",
    "panel-validation-claim-family.json",
    "panel-held-out-evaluation.json",
)
PACKAGE_SUFFIX_FILES = (
    "panel-validation-report.html",
    "panel-validation-report-manifest.json",
    "source-inventory.json",
    "README.txt",
)
COMMON_PACKAGE_FILES = PACKAGE_PREFIX_FILES + PACKAGE_SUFFIX_FILES
CLAIM_MEMBER = "panel-tier4-claim.json"
NEGATIVE_MEMBER = "panel-tier4-negative-result.json"
MANIFEST_MEMBER = "package-manifest.json"
PACKAGE_FILES = PACKAGE_PREFIX_FILES + (CLAIM_MEMBER,) + PACKAGE_SUFFIX_FILES
ARCHIVE_FILES = PACKAGE_FILES + (MANIFEST_MEMBER,)


def _archive_files(result_member: str) -> tuple[str, ...]:
    return PACKAGE_PREFIX_FILES + (result_member,) + PACKAGE_SUFFIX_FILES + (MANIFEST_MEMBER,)

MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + 5 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_PII_KEYS = frozenset({
    "person_id", "individual_id", "user_id", "customer_id", "respondent_id",
    "email", "phone", "address", "ip_address", "first_name",
    "last_name", "full_name", "account_id", "device_id",
})
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){7,15}(?!\w)")
_MANIFEST_KEYS = {
    "schema_version", "generator_version", "panel_binding", "generated_at",
    "claim_kind", "bindings", "files",
}
_MANIFEST_BINDING_KEYS = {
    "preregistration_sha256", "shared_evidence_sha256", "observations_sha256",
    "observation_member_sha256", "comparisons_sha256", "comparison_member_sha256",
    "evaluation_sha256", "claim_family_sha256", "claim_or_negative_sha256",
    "report_sha256", "report_manifest_sha256", "panel_package_sha256",
}
_FILE_KEYS = {"path", "sha256", "byte_count"}
REPORT_MANIFEST_VERSION = "panel-validation-report-manifest-v1"
_REPORT_MANIFEST_KEYS = {
    "schema_version", "panel_binding", "evaluation_sha256", "result_sha256",
    "report_sha256", "report_byte_count",
}
_SOURCE_INVENTORY_KEYS = {"schema_version", "aggregate_only", "sources"}
_SOURCE_ROW_KEYS = {"source_id", "source_sha256", "permission_confirmed"}
SOURCE_INVENTORY_VERSION = "panel-validation-source-inventory-v1"


class ValidationPackageError(ContractError):
    """The validation package does not meet the closed C1 contract."""


class ValidationPackageSafetyError(ValidationPackageError):
    """An archive or output path cannot safely be used."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise ValidationPackageError("JSON contains non-canonical values") from exc


def _reject_pii(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValidationPackageError(f"{path} contains a non-string key")
            if key.casefold() in _PII_KEYS:
                raise ValidationPackageError(f"{path}.{key} is a forbidden person-level or PII field")
            _reject_pii(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_pii(child, f"{path}[{index}]")
    elif isinstance(value, str) and (_EMAIL.search(value) or _PHONE.search(value)):
        raise ValidationPackageError(f"{path} contains forbidden person-level or PII data")


def _read_input(value: object, name: str) -> object:
    if isinstance(value, Path):
        try:
            raw = value.read_bytes()
        except OSError as exc:
            raise ValidationPackageError(f"could not read {name}") from exc
    elif isinstance(value, str):
        raw = Path(value).read_bytes()
    elif isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, Mapping) or isinstance(value, list):
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    else:
        raise ValidationPackageError(f"{name} must be a JSON path or JSON value")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationPackageError(f"{name} must contain UTF-8 JSON") from exc


def _read_bytes_input(value: object, name: str) -> bytes:
    if isinstance(value, Path):
        try: return value.read_bytes()
        except OSError as exc: raise ValidationPackageError(f"could not read {name}") from exc
    if isinstance(value, str):
        try: return Path(value).read_bytes()
        except OSError as exc: raise ValidationPackageError(f"could not read {name}") from exc
    if isinstance(value, (bytes, bytearray)): return bytes(value)
    raise ValidationPackageError(f"{name} must be a byte path or byte value")


def _input_documents(inputs: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(inputs, Mapping):
        raise ValidationPackageError("inputs must be a mapping")
    required = set(COMMON_PACKAGE_FILES) - {"README.txt"}
    supplied = set(inputs)
    result_keys = {CLAIM_MEMBER, NEGATIVE_MEMBER} & supplied
    expected = required | result_keys
    if len(result_keys) != 1 or supplied != expected:
        raise ValidationPackageError("inputs must contain the exact validation member allowlist and exactly one claim or negative result")
    result = {name: _read_input(value, name) for name, value in inputs.items() if name != "panel-validation-report.html"}
    result["panel-validation-report.html"] = _read_bytes_input(inputs["panel-validation-report.html"], "panel-validation-report.html")
    return result


def _panel_snapshot(panel_package_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    try:
        raw = _archive_bytes(
            panel_package_path, max_archive_bytes=MAX_ARCHIVE_BYTES,
        )
        with tempfile.TemporaryDirectory(
            prefix="audience-tier4-panel-snapshot-",
        ) as directory:
            snapshot = Path(directory) / "panel-package.zip"
            snapshot.write_bytes(raw)
            snapshot.chmod(0o400)
            snapshot_raw, manifest_bytes = read_v3_archive_manifest(snapshot)
            if snapshot_raw != raw:
                raise ValidationPackageError(
                    "panel package snapshot bytes changed during validation"
                )
            validation = dict(validate_supported_audience_package(snapshot))
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if manifest.get("schema_version") == V2_PACKAGE_SCHEMA_VERSION:
                members = read_safe_archive_members(
                    raw, allowed_files=V2_ARCHIVE_FILES,
                )
            else:
                members = read_v3_archive_members(
                    raw,
                    allowed_files=archive_files_v3_for_manifest(manifest),
                )
            panel = json.loads(
                members["saved-audience-panel.json"].decode("utf-8"),
            )
    except (PackageSafetyError, PackageValidationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationPackageError("panel package is not an authenticated v2 or v3 package") from exc
    package_hash = validation.get("package_zip_sha256")
    if not isinstance(package_hash, str) or not _SHA.fullmatch(package_hash):
        raise ValidationPackageError("panel package digest is invalid")
    binding = {
        "panel_id": validation.get("panel_id"),
        "panel_version": validation.get("panel_version"),
        "panel_sha256": "sha256:" + _sha(members["saved-audience-panel.json"]),
        "package_sha256": "sha256:" + package_hash,
    }
    return binding, validation


def _negative_result(value: object, *, evaluation: Mapping[str, object]) -> dict[str, object]:
    """Validate the deliberately compact non-claim result representation."""
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "evaluation_binding", "panel_binding", "claim_scope",
        "status", "limitations", "negative_result_sha256",
    }:
        raise ValidationPackageError("negative result keys do not match the package contract")
    document = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if document["schema_version"] != "panel-tier4-negative-result-v1":
        raise ValidationPackageError("negative result schema version is invalid")
    if document["status"] not in {"tier4_not_supported", "evaluated_with_limitations", "invalid"}:
        raise ValidationPackageError("negative result status is invalid")
    if document["status"] != evaluation["decision"]["status"]:
        raise ValidationPackageError("negative result status must match evaluation")
    expected_binding = {"evaluation_id": evaluation["evaluation_id"], "evaluation_sha256": evaluation["evaluation_sha256"]}
    if document["evaluation_binding"] != expected_binding or document["panel_binding"] != evaluation["panel_binding"] or document["claim_scope"] != evaluation["claim_scope"]:
        raise ValidationPackageError("negative result does not exactly bind the evaluation")
    if not isinstance(document["limitations"], list):
        raise ValidationPackageError("negative result limitations must be an array")
    digest = document["negative_result_sha256"]
    unhashed = dict(document); unhashed["negative_result_sha256"] = None
    if not isinstance(digest, str) or not _PREFIXED_SHA.fullmatch(digest) or sha256_json(unhashed) != digest:
        raise ValidationPackageError("negative result self-hash is invalid")
    _reject_pii(document)
    return document


def _report_manifest(
    value: object, *, report: bytes, evaluation: Mapping[str, object],
    result: Mapping[str, object], panel_binding: Mapping[str, object],
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _REPORT_MANIFEST_KEYS:
        raise ValidationPackageError("validation report manifest keys do not match the allowlist")
    checked = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    result_digest = result["claim_sha256"] if result.get("schema_version") == TIER4_CLAIM_VERSION else result["negative_result_sha256"]
    if checked["schema_version"] != REPORT_MANIFEST_VERSION or checked["panel_binding"] != panel_binding:
        raise ValidationPackageError("validation report manifest identity is invalid")
    if checked["evaluation_sha256"] != evaluation["evaluation_sha256"] or checked["result_sha256"] != result_digest:
        raise ValidationPackageError("validation report manifest bindings are invalid")
    if checked["report_sha256"] != _sha(report) or checked["report_byte_count"] != len(report):
        raise ValidationPackageError("validation report manifest does not bind exact report bytes")
    return checked


def _collection_hash(values: list[Mapping[str, object]], field: str) -> str:
    ordered = sorted(values, key=lambda item: str(item.get("block_binding", {}).get("block_id", "")))
    return sha256_json([item[field] for item in ordered])


def _source_inventory(value: object, *, shared: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _SOURCE_INVENTORY_KEYS:
        raise ValidationPackageError("source inventory keys do not match the package contract")
    if value.get("schema_version") != SOURCE_INVENTORY_VERSION or value.get("aggregate_only") is not True:
        raise ValidationPackageError("source inventory must be aggregate_only under the supported schema")
    rows = value.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValidationPackageError("source inventory sources must be a non-empty array")
    source = shared["source"]
    expected = {"source_id": source["source_id"], "source_sha256": source["source_sha256"], "permission_confirmed": source["permission_confirmed"]}
    checked: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != _SOURCE_ROW_KEYS:
            raise ValidationPackageError("source inventory source row keys do not match the allowlist")
        if dict(row) != expected or row["permission_confirmed"] is not True:
            raise ValidationPackageError("source inventory rows must exactly bind permissioned shared evidence sources")
        checked.append(dict(row))
    if checked != [expected]:
        raise ValidationPackageError("source inventory must contain one exact shared evidence source row")
    return {"schema_version": SOURCE_INVENTORY_VERSION, "aggregate_only": True, "sources": checked}


def _validated_documents(
    documents: Mapping[str, object],
    panel_binding: Mapping[str, object],
    *,
    authority_registry: object,
) -> tuple[dict[str, object], str]:
    registration, approval = authenticate_preregistration_design(
        documents["panel-validation-preregistration.json"],
        authority_registry=authority_registry,
    )
    shared = validate_shared_outcome_evidence(documents["panel-shared-outcome-evidence.json"])
    observations_raw = documents["panel-validation-observations.json"]
    comparisons_raw = documents["panel-synthetic-outcome-comparisons.json"]
    if not isinstance(observations_raw, list) or not observations_raw:
        raise ValidationPackageError("validation observations must be a non-empty array")
    if not isinstance(comparisons_raw, list) or not comparisons_raw:
        raise ValidationPackageError("synthetic outcome comparisons must be a non-empty array")
    observations = sorted(
        [validate_validation_observation(item) for item in observations_raw],
        key=lambda item: str(item["observation_id"]),
    )
    comparisons = sorted(
        [validate_comparison(item) for item in comparisons_raw],
        key=lambda item: str(item["block_binding"]["block_id"]),
    )
    if len({item["observation_id"] for item in observations}) != len(observations):
        raise ValidationPackageError("validation observations contain duplicate IDs")
    if len({item["block_binding"]["block_id"] for item in comparisons}) != len(comparisons):
        raise ValidationPackageError("synthetic outcome comparisons contain duplicate blocks")
    evaluation = validate_held_out_evaluation(documents["panel-held-out-evaluation.json"])
    family = validate_claim_family(documents["panel-validation-claim-family.json"])
    for member in family["member_preregistrations"]:
        authenticate_preregistration_design(
            member, authority_registry=authority_registry,
        )
    from .evaluation import evaluate_held_out_ordering

    replayed_evaluation = evaluate_held_out_ordering(
        registration=registration,
        comparisons=comparisons,
        claim_family=family,
        evaluated_at=str(evaluation["evaluated_at"]),
        design_approval=approval,
        authority_registry=authority_registry,
    )
    if evaluation != replayed_evaluation:
        raise ValidationPackageError(
            "packaged evaluation must exactly equal the result replayed from "
            "the authenticated preregistration, comparisons, and claim family"
        )
    for name, value in documents.items():
        if name != "panel-validation-report.html": _reject_pii(value, name)
    report = documents["panel-validation-report.html"]
    if not isinstance(report, bytes): raise ValidationPackageError("validation report must be bytes")
    try: _validate_report_html(report.decode("utf-8"))
    except (UnicodeDecodeError, PackageSafetyError) as exc: raise ValidationPackageError("validation report must be self-contained safe HTML") from exc
    checked = {
        "panel-validation-preregistration.json": registration,
        "panel-shared-outcome-evidence.json": shared,
        "panel-validation-observations.json": observations,
        "panel-synthetic-outcome-comparisons.json": comparisons,
        "panel-held-out-evaluation.json": evaluation,
        "panel-validation-claim-family.json": family,
        "source-inventory.json": _source_inventory(documents["source-inventory.json"], shared=shared),
        "panel-validation-report.html": report,
    }
    if any(item["panel_binding"] != panel_binding for item in [registration, *observations, *comparisons, evaluation]):
        raise ValidationPackageError("validation documents do not bind the exact authenticated panel package")
    if any(item["claim_scope"] != registration["claim_scope"] for item in observations) or registration["claim_scope"] != evaluation["claim_scope"]:
        raise ValidationPackageError("validation documents do not share the exact claim scope")
    embedded_observations = sorted(
        [
            observation
            for comparison in comparisons
            for observation in comparison["observations"]
        ],
        key=lambda item: str(item["observation_id"]),
    )
    if observations != embedded_observations:
        raise ValidationPackageError(
            "packaged observations must exactly equal the observations "
            "evaluated inside the comparison collection"
        )
    if shared != project_shared_outcome_evidence(observations[0]):
        raise ValidationPackageError(
            "packaged shared evidence must be the canonical first evaluated "
            "observation projection"
        )
    if any(item["registration_binding"]["registration_sha256"] != registration["registration_sha256"] for item in comparisons):
        raise ValidationPackageError("comparisons do not bind the preregistration")
    if evaluation["registration_binding"]["registration_sha256"] != registration["registration_sha256"]:
        raise ValidationPackageError("evaluation does not bind the preregistration")
    if evaluation["comparisons"] != comparisons:
        raise ValidationPackageError(
            "evaluation must embed the exact packaged comparison collection"
        )
    family_index = family["member_registration_ids"].index(registration["registration_id"])
    if family["member_preregistrations"][family_index]["registration_sha256"] != registration["registration_sha256"]:
        raise ValidationPackageError("claim family does not bind the preregistration")
    if family["member_comparison_sha256"][family_index] != _collection_hash(comparisons, "comparison_sha256"):
        raise ValidationPackageError("claim family does not bind the exact comparison collection")
    inventory = [{"block_id": item["block_binding"]["block_id"], "comparison_sha256": item["comparison_sha256"]} for item in sorted(comparisons, key=lambda item: str(item["block_binding"]["block_id"]))]
    if evaluation["block_inventory"] != inventory:
        raise ValidationPackageError("evaluation block inventory does not bind the exact comparison collection")
    if CLAIM_MEMBER in documents:
        claim = validate_tier4_claim(documents[CLAIM_MEMBER])
        if evaluation["decision"]["status"] != "tier4_supported" or claim["status"] != "active":
            raise ValidationPackageError("only an active claim may accompany a supported evaluation")
        if claim["panel_binding"] != panel_binding or claim["claim_scope"] != evaluation["claim_scope"]:
            raise ValidationPackageError("claim does not exactly bind the evaluation scope")
        if claim["evaluation_binding"] != {"evaluation_id": evaluation["evaluation_id"], "evaluation_sha256": evaluation["evaluation_sha256"]}:
            raise ValidationPackageError("claim does not exactly bind the evaluation")
        from .evaluation import issue_tier4_claim

        replayed_claim = issue_tier4_claim(
            evaluation=evaluation,
            issued_at=str(claim["issued_at"]),
            expires_at=str(claim["expires_at"]),
            design_approval=approval,
            authority_registry=authority_registry,
        )
        if claim != replayed_claim:
            raise ValidationPackageError(
                "packaged claim must exactly equal canonical claim issuance"
            )
        from .reporting import (
            build_validation_report_payload,
            render_validation_report_bytes,
        )

        expected_report = render_validation_report_bytes(
            payload=build_validation_report_payload(
                registration=registration,
                evaluation=evaluation,
                claim=claim,
                as_of=str(evaluation["evaluated_at"]),
                authority_registry=authority_registry,
            ),
            template_path=CANONICAL_REPORT_TEMPLATE,
        )
        if report != expected_report:
            raise ValidationPackageError(
                "validation report bytes must exactly equal the canonical "
                "projection of the authenticated evaluation and claim"
            )
        checked[CLAIM_MEMBER] = claim
        checked["panel-validation-report-manifest.json"] = _report_manifest(documents["panel-validation-report-manifest.json"], report=report, evaluation=evaluation, result=claim, panel_binding=panel_binding)
        return checked, "claim"
    if evaluation["decision"]["status"] == "tier4_supported":
        raise ValidationPackageError("a supported evaluation requires an active claim, not a negative result")
    negative = _negative_result(documents[NEGATIVE_MEMBER], evaluation=evaluation)
    from .reporting import (
        build_validation_report_payload,
        render_validation_report_bytes,
    )

    expected_report = render_validation_report_bytes(
        payload=build_validation_report_payload(
            registration=registration,
            evaluation=evaluation,
            claim=None,
            as_of=str(evaluation["evaluated_at"]),
            authority_registry=authority_registry,
        ),
        template_path=CANONICAL_REPORT_TEMPLATE,
    )
    if report != expected_report:
        raise ValidationPackageError(
            "validation report bytes must exactly equal the canonical "
            "projection of the authenticated negative evaluation"
        )
    checked[NEGATIVE_MEMBER] = negative
    checked["panel-validation-report-manifest.json"] = _report_manifest(documents["panel-validation-report-manifest.json"], report=report, evaluation=evaluation, result=negative, panel_binding=panel_binding)
    return checked, "negative"


def _readme(panel_binding: Mapping[str, object], claim_kind: str) -> bytes:
    status = "active Tier 4 claim" if claim_kind == "claim" else "negative, limited, or invalid Tier 4 result"
    return (
        "Audience Panel Tier 4 validation package\n\n"
        f"Panel: {panel_binding['panel_id']} version {panel_binding['panel_version']}\n"
        f"Result: {status}\n\n"
        "This archive contains aggregate-only validation evidence. It does not contain raw, person-level, or targeting records. "
        "The referenced panel package is authenticated by the exact package digest in package-manifest.json.\n"
    ).encode("utf-8")


def _manifest(files: Mapping[str, bytes], *, panel_binding: Mapping[str, object], generated_at: str, claim_kind: str) -> dict[str, object]:
    result_member = CLAIM_MEMBER if claim_kind == "claim" else NEGATIVE_MEMBER
    observations = json.loads(files["panel-validation-observations.json"])
    comparisons = json.loads(files["panel-synthetic-outcome-comparisons.json"])
    document_hashes = {
        "preregistration_sha256": json.loads(files["panel-validation-preregistration.json"])["registration_sha256"],
        "shared_evidence_sha256": json.loads(files["panel-shared-outcome-evidence.json"])["shared_evidence_sha256"],
        "observations_sha256": sha256_json(observations),
        "observation_member_sha256": [item["observation_sha256"] for item in observations],
        "comparisons_sha256": sha256_json(comparisons),
        "comparison_member_sha256": [item["comparison_sha256"] for item in comparisons],
        "evaluation_sha256": json.loads(files["panel-held-out-evaluation.json"])["evaluation_sha256"],
        "claim_family_sha256": json.loads(files["panel-validation-claim-family.json"])["family_sha256"],
        "claim_or_negative_sha256": json.loads(files[result_member])["claim_sha256" if claim_kind == "claim" else "negative_result_sha256"],
        "report_sha256": _sha(files["panel-validation-report.html"]),
        "report_manifest_sha256": _sha(files["panel-validation-report-manifest.json"]),
        "panel_package_sha256": panel_binding["package_sha256"],
    }
    return {
        "schema_version": VALIDATION_PACKAGE_VERSION,
        "generator_version": VALIDATION_GENERATOR_VERSION,
        "panel_binding": dict(panel_binding),
        "generated_at": generated_at,
        "claim_kind": claim_kind,
        "bindings": document_hashes,
        "files": {name: {"path": name, "sha256": _sha(files[name]), "byte_count": len(files[name])} for name in sorted(files)},
    }


def _zip_bytes(files: Mapping[str, bytes], archive_files: tuple[str, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        for name in archive_files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.extra = b""; info.comment = b""
            archive.writestr(info, files[name])
        archive.comment = b""
    return output.getvalue()


def _safe_members(raw: bytes, allowed: tuple[str, ...]) -> dict[str, bytes]:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValidationPackageSafetyError("validation package archive is too large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValidationPackageSafetyError("validation package is not a ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) != len(allowed) or len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValidationPackageSafetyError("validation package member count is invalid")
        names = [info.filename for info in infos]
        if tuple(names) != allowed or len(set(names)) != len(names):
            raise ValidationPackageSafetyError("validation package members do not match the exact ordered allowlist")
        total = 0
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            if path.is_absolute() or len(path.parts) != 1 or "\\" in info.filename or "\x00" in info.filename or info.flag_bits & 1:
                raise ValidationPackageSafetyError("validation package contains an unsafe path")
            if info.is_dir() or (info.create_system == 3 and stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                raise ValidationPackageSafetyError("validation package members must be regular files")
            if info.file_size > MAX_ENTRY_BYTES or ((mode & 0o777) != 0o600) or info.date_time != (1980, 1, 1, 0, 0, 0) or info.compress_type != zipfile.ZIP_STORED:
                raise ValidationPackageSafetyError("validation package metadata is not canonical")
            total += info.file_size
            if total > MAX_TOTAL_BYTES or (info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO):
                raise ValidationPackageSafetyError("validation package exceeds a safety limit")
        try:
            return {info.filename: archive.read(info) for info in infos}
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
            raise ValidationPackageSafetyError("validation package member data is corrupt") from exc


def _output_root(output_dir: Path) -> None:
    if ".." in output_dir.parts:
        raise ValidationPackageSafetyError("output path must not contain parent traversal")
    absolute = output_dir.absolute(); current = Path(absolute.anchor)
    aliases = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
    for part in absolute.parts[1:]:
        current = current / part
        if (current.exists() or current.is_symlink()) and current.is_symlink():
            if current not in aliases or current.resolve() != aliases[current]:
                raise ValidationPackageSafetyError("output path contains a symlink component")
    if output_dir.exists() or output_dir.is_symlink():
        raise ValidationPackageSafetyError("output directory already exists; validation packages never replace a target")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    # Re-check after creating parents. The complete package is assembled in a
    # private sibling directory and only then atomically renamed into place.
    _reject_output_parent_symlinks(output_dir)


def _reject_output_parent_symlinks(output_dir: Path) -> None:
    absolute = output_dir.absolute(); current = Path(absolute.anchor)
    aliases = {Path("/var"): Path("/private/var"), Path("/tmp"): Path("/private/tmp")}
    for part in absolute.parts[1:]:
        current = current / part
        if (current.exists() or current.is_symlink()) and current.is_symlink():
            if current not in aliases or current.resolve() != aliases[current]:
                raise ValidationPackageSafetyError("output path contains a symlink component")


def _atomic_write_new(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".validation-package-", dir=path.parent)
    temp = Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        # link(2) is an atomic create-if-absent publication in the same
        # directory.  Unlike exists()+replace(), it can never overwrite a
        # concurrently created final path.
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise ValidationPackageSafetyError("validation package output already exists") from exc
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
            temp.unlink()
            os.fsync(directory)
        finally: os.close(directory)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """Atomically publish a directory only when the target name is absent."""

    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    if sys.platform == "darwin":
        function = getattr(libc, "renamex_np", None)
        if function is None:
            raise ValidationPackageSafetyError(
                "atomic no-replace directory publication is unavailable"
            )
        function.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(source_bytes, target_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise ValidationPackageSafetyError(
                "atomic no-replace directory publication is unavailable"
            )
        function.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        result = function(
            -100, source_bytes, -100, target_bytes, 0x00000001,
        )
    else:
        raise ValidationPackageSafetyError(
            "atomic no-replace directory publication is unsupported on this "
            "platform"
        )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValidationPackageSafetyError(
            "output directory already exists; validation packages never "
            "replace a target"
        )
    raise ValidationPackageSafetyError(
        "validation package directory could not be published atomically"
    ) from OSError(error_number, os.strerror(error_number))


def build_validation_package(
    *,
    inputs: Mapping[str, Path],
    panel_package_path: Path,
    output_dir: Path,
    authority_registry: object,
) -> Path:
    """Build a no-clobber deterministic aggregate-only validation archive."""
    documents = _input_documents(inputs)
    panel_binding, _panel_validation = _panel_snapshot(Path(panel_package_path))
    checked, claim_kind = _validated_documents(
        documents, panel_binding, authority_registry=authority_registry,
    )
    result_member = CLAIM_MEMBER if claim_kind == "claim" else NEGATIVE_MEMBER
    archive_files = _archive_files(result_member)
    files = {name: (value if isinstance(value, bytes) else _canonical(value)) for name, value in checked.items()}
    files["README.txt"] = _readme(panel_binding, claim_kind)
    files[MANIFEST_MEMBER] = _canonical(_manifest(
        files, panel_binding=panel_binding,
        generated_at=checked["panel-held-out-evaluation.json"]["evaluated_at"], claim_kind=claim_kind,
    ))
    raw = _zip_bytes(files, archive_files)
    root = Path(output_dir)
    _output_root(root)
    stage = Path(tempfile.mkdtemp(
        prefix=f".{root.name}.validation-stage-",
        dir=root.parent,
    ))
    os.chmod(stage, 0o700)
    try:
        for name in archive_files:
            _atomic_write_new(stage / name, files[name])
        zip_path = stage / "audience-panel-validation-package.zip"
        _atomic_write_new(zip_path, raw)
        validate_validation_package(
            zip_path, authority_registry=authority_registry,
        )
        if root.exists() or root.is_symlink():
            raise ValidationPackageSafetyError(
                "output directory already exists; validation packages never "
                "replace a target"
            )
        _rename_directory_no_replace(stage, root)
        directory = os.open(root.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return root / "audience-panel-validation-package.zip"


def validate_validation_package(
    path: Path, *, authority_registry: object,
) -> dict[str, object]:
    """Authenticate an exact C1 validation archive without extracting it."""
    try:
        raw = _archive_bytes(path, max_archive_bytes=MAX_ARCHIVE_BYTES)
    except PackageSafetyError as exc:
        raise ValidationPackageSafetyError(str(exc)) from exc
    # The last member cannot be known until the claim-kind field is parsed, but
    # safe metadata can be checked first with each of the two closed worlds.
    candidates: list[dict[str, bytes]] = []
    for result_member in (CLAIM_MEMBER, NEGATIVE_MEMBER):
        try:
            candidates.append(_safe_members(raw, _archive_files(result_member)))
        except ValidationPackageSafetyError:
            continue
    if len(candidates) != 1:
        raise ValidationPackageSafetyError("validation package does not use one exact archive member world")
    files = candidates[0]
    try:
        manifest = json.loads(files[MANIFEST_MEMBER].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationPackageError("validation package manifest is invalid JSON") from exc
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS:
        raise ValidationPackageError("validation package manifest keys do not match the allowlist")
    if manifest.get("schema_version") != VALIDATION_PACKAGE_VERSION or manifest.get("generator_version") not in SUPPORTED_VALIDATION_GENERATOR_VERSIONS:
        raise ValidationPackageError("unsupported validation package schema or generator")
    if files[MANIFEST_MEMBER] != _canonical(manifest):
        raise ValidationPackageError("validation package manifest is not canonical JSON")
    claim_kind = manifest.get("claim_kind")
    if claim_kind not in {"claim", "negative"}:
        raise ValidationPackageError("validation package claim kind is invalid")
    expected_files = PACKAGE_PREFIX_FILES + ((CLAIM_MEMBER if claim_kind == "claim" else NEGATIVE_MEMBER),) + PACKAGE_SUFFIX_FILES
    if not isinstance(manifest.get("files"), Mapping) or set(manifest["files"]) != set(expected_files):
        raise ValidationPackageError("validation package manifest file allowlist is invalid")
    for name in expected_files:
        record = manifest["files"][name]
        if not isinstance(record, Mapping) or set(record) != _FILE_KEYS or record.get("path") != name or record.get("sha256") != _sha(files[name]) or record.get("byte_count") != len(files[name]):
            raise ValidationPackageError(f"validation package manifest binding is invalid for {name}")
    if not isinstance(manifest.get("panel_binding"), Mapping) or set(manifest["panel_binding"]) != {"panel_id", "panel_version", "panel_sha256", "package_sha256"}:
        raise ValidationPackageError("validation package panel binding is invalid")
    if not isinstance(manifest.get("bindings"), Mapping) or set(manifest["bindings"]) != _MANIFEST_BINDING_KEYS:
        raise ValidationPackageError("validation package binding allowlist is invalid")
    if not isinstance(manifest.get("generated_at"), str):
        raise ValidationPackageError("validation package generation timestamp is invalid")
    documents: dict[str, object] = {}
    for name in expected_files:
        if name in {"README.txt", "panel-validation-report.html"}:
            continue
        try: documents[name] = json.loads(files[name].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise ValidationPackageError(f"{name} is invalid JSON") from exc
    documents["panel-validation-report.html"] = files["panel-validation-report.html"]
    checked, actual_kind = _validated_documents(
        documents,
        manifest["panel_binding"],
        authority_registry=authority_registry,
    )
    if actual_kind != claim_kind:
        raise ValidationPackageError("validation package claim kind does not match contents")
    expected_readme = _readme(manifest["panel_binding"], claim_kind)
    if files["README.txt"] != expected_readme:
        raise ValidationPackageError("validation package README is not canonical")
    rebuilt_files = {name: (value if isinstance(value, bytes) else _canonical(value)) for name, value in checked.items()}
    rebuilt_files["README.txt"] = expected_readme
    expected_manifest = _manifest(rebuilt_files, panel_binding=manifest["panel_binding"], generated_at=checked["panel-held-out-evaluation.json"]["evaluated_at"], claim_kind=claim_kind)
    if manifest != expected_manifest:
        raise ValidationPackageError("validation package cross-bindings do not match the authenticated documents")
    rebuilt_files[MANIFEST_MEMBER] = _canonical(expected_manifest)
    expected_raw = _zip_bytes(rebuilt_files, _archive_files(CLAIM_MEMBER if claim_kind == "claim" else NEGATIVE_MEMBER))
    if raw != expected_raw:
        raise ValidationPackageError("validation package ZIP bytes are not deterministic")
    return {
        "schema_version": VALIDATION_PACKAGE_VERSION, "status": "valid",
        "panel_binding": dict(manifest["panel_binding"]), "claim_kind": claim_kind,
        "claim_id": checked.get(CLAIM_MEMBER, {}).get("claim_id"),
        "claim_sha256": checked.get(CLAIM_MEMBER, {}).get("claim_sha256"),
        "claim_scope_sha256": sha256_json(checked["panel-held-out-evaluation.json"]["claim_scope"]),
        "package_manifest_sha256": _sha(files[MANIFEST_MEMBER]), "package_manifest_byte_count": len(files[MANIFEST_MEMBER]),
        "package_zip_sha256": _sha(raw), "package_zip_byte_count": len(raw),
        "evaluation": checked["panel-held-out-evaluation.json"],
        "claim": checked.get(CLAIM_MEMBER),
    }


__all__ = [
    "ARCHIVE_FILES", "CLAIM_MEMBER", "COMMON_PACKAGE_FILES", "MANIFEST_MEMBER",
    "NEGATIVE_MEMBER", "PACKAGE_FILES", "VALIDATION_GENERATOR_VERSION",
    "VALIDATION_PACKAGE_VERSION", "ValidationPackageError", "ValidationPackageSafetyError",
    "build_validation_package", "validate_validation_package",
]
