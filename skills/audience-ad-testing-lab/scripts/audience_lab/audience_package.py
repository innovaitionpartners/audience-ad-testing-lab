"""Deterministic, portable audience research package compiler and validator."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
from html import escape
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any, BinaryIO, Mapping
from urllib.parse import urlparse
import zipfile

from .audience_research import AudienceResearchValidationError, require_valid_audience_research_pair


PACKAGE_SCHEMA_VERSION = "audience-panel-package-v2"
DEFAULT_GENERATOR_VERSION = "1.0.0"
SUPPORTED_GENERATOR_VERSIONS = frozenset({"1.0.0"})
PACKAGE_FILES = (
    "persona-research-brief.json",
    "saved-audience-panel.json",
    "research-sources.csv",
    "audience-research-report.html",
    "README.txt",
)
ARCHIVE_FILES = PACKAGE_FILES + ("package-manifest.json",)
CSV_COLUMNS = (
    "evidence_id", "source_label", "source_url", "date", "source_type",
    "confidence", "collection_method", "supported_findings", "permitted_uses", "limits",
)
MAX_ARCHIVE_ENTRIES = 16
MAX_ENTRY_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 75 * 1024 * 1024
MAX_COMPRESSION_RATIO = 20.0
_MANIFEST_KEYS = {
    "schema_version", "panel_id", "panel_version", "brief_id", "generated_at",
    "generator_version", "files",
}
_MANIFEST_FILE_KEYS = {"path", "sha256", "byte_count"}
_CSS_EXTERNAL_RE = re.compile(
    r"(?is)(?:@import\b|url\s*\(|image-set\s*\(|\bimage\s*\(|\blocal\s*\(|"
    r"(?:https?|file|data|javascript|vbscript):|(?<!:)//)"
)
_REPORT_TEMPLATE_V1 = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ad Testing Lab — Audience Research Report</title>
<style>
:root{color-scheme:light;--ink:#1f2933;--muted:#52606d;--paper:#fff;--wash:#f4f7f5;--accent:#136f63;--line:#d9e2df}*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.55 Arial,sans-serif}header,main{width:min(1180px,calc(100% - 48px));margin:auto}header{padding:64px 0 32px}h1{font-size:clamp(2rem,5vw,4rem);line-height:1.05;margin:.15em 0;font-weight:600}h2{font-size:1.55rem;margin:0 0 18px}h3{font-size:1.15rem}h4{margin-bottom:4px}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.08em;text-transform:uppercase}section{background:var(--paper);border-top:3px solid var(--accent);margin:0 0 24px;padding:28px 32px}dl{display:grid;grid-template-columns:150px 1fr;gap:8px 20px}dt{font-weight:700}dd{margin:0}.meta,.empty{color:var(--muted);font-size:.92rem}.insight,.segment,.mindset{border-top:1px solid var(--line);padding-top:16px;margin-top:16px}.trait-grid,.coverage-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 24px}.coverage-grid div{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:7px 0}.coverage-grid span{color:var(--muted)}a{color:var(--accent);text-underline-offset:3px}@media(max-width:700px){header,main{width:min(100% - 24px,1180px)}section{padding:22px 20px}.trait-grid,.coverage-grid,dl{grid-template-columns:1fr}dt{margin-top:8px}}@media print{body{background:#fff}header,main{width:100%}section{break-inside:avoid;border:1px solid var(--line)}}
</style>
</head>
<body>
{{REPORT_BODY}}
</body>
</html>
'''


class PackageValidationError(ValueError):
    """The package contents do not match the canonical contract."""


class PackageSafetyError(PackageValidationError):
    """The archive or output path is unsafe to read or materialize."""


class _SelfContainedReportParser(HTMLParser):
    """Reject executable/asset-bearing HTML while allowing citation anchors."""

    _FORBIDDEN_TAGS = {
        "script", "img", "iframe", "video", "audio", "source", "track", "embed",
        "object", "link", "form", "input", "button", "svg", "math", "base", "meta",
    }
    _EXTERNAL_ATTRIBUTES = {"src", "srcset", "poster", "action", "formaction", "data", "xlink:href"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.error: str | None = None
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.casefold()
        if lower_tag == "style":
            self._style_depth += 1
        # The one static charset declaration and viewport declaration are safe.
        if lower_tag in self._FORBIDDEN_TAGS and lower_tag != "meta":
            self.error = f"report contains forbidden <{tag}> content"
            return
        attributes = {name.casefold(): value for name, value in attrs}
        if lower_tag == "meta" and not (
            set(attributes).issubset({"charset"}) or set(attributes).issubset({"name", "content"})
        ):
            self.error = "report contains an unsafe meta directive"
            return
        if self._EXTERNAL_ATTRIBUTES.intersection(attributes):
            self.error = "report contains an external asset attribute"
            return
        if "style" in attributes and _CSS_EXTERNAL_RE.search(attributes.get("style") or ""):
            self.error = "report style contains an external reference"
            return
        if any(name.startswith("on") for name in attributes):
            self.error = "report contains an executable event attribute"
            return
        if "href" in attributes:
            if lower_tag != "a":
                self.error = "only citation anchors may use href"
                return
            href = attributes.get("href") or ""
            parsed = urlparse(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                self.error = "report citation links must use HTTP or HTTPS"

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth and _CSS_EXTERNAL_RE.search(data):
            self.error = "report style contains an external reference"

    handle_startendtag = handle_starttag


@dataclass(frozen=True)
class PackageBuildResult:
    output_dir: Path
    brief_path: Path
    panel_path: Path
    sources_csv_path: Path
    report_path: Path
    readme_path: Path
    manifest_path: Path
    package_zip_path: Path
    package_manifest_sha256: str
    package_zip_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "built",
            "output_dir": str(self.output_dir),
            "panel_id": json.loads(self.panel_path.read_text(encoding="utf-8"))["panel_id"],
            "panel_version": json.loads(self.panel_path.read_text(encoding="utf-8"))["version"],
            "package_manifest_sha256": self.package_manifest_sha256,
            "package_zip_sha256": self.package_zip_sha256,
            "package_zip_path": str(self.package_zip_path),
        }


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _formula_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    probe = text.lstrip(" \t\r\n")
    if probe.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def _csv_bytes_v1(brief: Mapping[str, Any], panel: Mapping[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\r\n", extrasaction="raise")
    writer.writeheader()
    if panel["persona_research"]["source_state"] == "no_research_sources":
        return output.getvalue().encode("utf-8")
    finding_support: dict[str, list[str]] = {}
    for finding in brief["findings"]:
        for evidence_id in finding["evidence_ids"]:
            finding_support.setdefault(evidence_id, []).append(finding["finding_id"])
    for source in brief["evidence_sources"]:
        row = {
            "evidence_id": source["evidence_id"],
            "source_label": source["source_label"],
            "source_url": source["source_url"] or "",
            "date": source["date"],
            "source_type": source["type"],
            "confidence": source["confidence"],
            "collection_method": source["collection_method"],
            "supported_findings": "; ".join(sorted(finding_support.get(source["evidence_id"], []))),
            "permitted_uses": "; ".join(source["permitted_uses"]),
            "limits": source["limits"],
        }
        writer.writerow({key: _formula_safe(value) for key, value in row.items()})
    return output.getvalue().encode("utf-8")


def _list_v1(items: list[Any], empty: str = "None documented") -> str:
    if not items:
        return f'<p class="empty">{escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in items) + "</ul>"


def _source_link_v1(source: Mapping[str, Any]) -> str:
    label = escape(str(source["source_label"]))
    url = source.get("source_url")
    if isinstance(url, str):
        parsed = urlparse(url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return f'<a href="{escape(url, quote=True)}" rel="noreferrer noopener">{label}</a>'
    return label


def _report_body_v1(brief: Mapping[str, Any], panel: Mapping[str, Any]) -> str:
    provisional = panel["persona_research"]["source_state"] == "no_research_sources"
    title = "Provisional — no research sources" if provisional else "Audience research report"
    target = brief["target_audience"]
    findings = "".join(
        '<article class="insight"><h3>{}</h3><p>{}</p><p class="meta">Confidence: {} · Boundary: {}</p>{}</article>'.format(
            escape(item["category"].replace("_", " ").title()), escape(item["statement"]),
            escape(item["confidence"]), escape(item["inference_boundary"]),
            _list_v1(item["creative_implications"], "No creative implication documented"),
        ) for item in brief["findings"]
    ) or '<p class="empty">No research findings were used for this provisional panel.</p>'
    archetypes_by_segment: dict[str, list[Mapping[str, Any]]] = {}
    for item in panel["persona_archetypes"]:
        archetypes_by_segment.setdefault(item["segment_id"], []).append(item)
    segments = []
    for segment in panel["segments"]:
        mindsets = []
        for archetype in archetypes_by_segment.get(segment["segment_id"], []):
            mindsets.append(
                '<div class="mindset"><h4>{}</h4><p>{}</p><div class="trait-grid">'
                '<div><strong>Needs and motivations</strong>{}</div><div><strong>Objections and anxieties</strong>{}</div>'
                '<div><strong>Triggers</strong>{}</div><div><strong>Proof requirements</strong>{}</div></div>'
                '<p class="meta">Evidence strength: {} · {}</p></div>'.format(
                    escape(archetype["display_name"]), escape(archetype["decision_context"]),
                    _list_v1(archetype["motivations"]), _list_v1(archetype["objections"] + archetype["anxieties"]),
                    _list_v1(archetype["triggers"]), _list_v1(archetype["proof_needs"]),
                    escape(archetype["evidence_strength"]), escape(archetype["inference_boundary"]),
                )
            )
        segments.append(
            '<article class="segment"><h3>{}</h3><p>{}</p><p><strong>Why this segment exists:</strong> {}</p>'
            '<h4>Creative implications</h4>{}<h4>Mindsets in this segment</h4>{}</article>'.format(
                escape(segment["name"]), escape(segment["description"]),
                escape(next((h["why_it_matters_for_ad_testing"] for h in brief["segment_hypotheses"] if h["segment_id"] == segment["segment_id"]), "User-defined provisional segment.")),
                _list_v1(segment["creative_implications"]), "".join(mindsets),
            )
        )
    gaps = "".join(
        f'<li><strong>{escape(g["gap"])}</strong> {escape(g["impact_on_panel"])} Mitigation: {escape(g["mitigation"])}</li>'
        for g in brief["evidence_gaps"]
    ) or "<li>No evidence gaps documented.</li>"
    sources = "".join(
        f'<li>{_source_link_v1(source)} <span class="meta">{escape(source["type"].replace("_", " "))}, {escape(source["date"])}, {escape(source["confidence"])} confidence. {escape(source["limits"])}</span></li>'
        for source in brief["evidence_sources"]
    ) or "<li>No research sources were used.</li>"
    implications = [x for finding in brief["findings"] for x in finding["creative_implications"]]
    source_types = sorted({source["type"].replace("_", " ") for source in brief["evidence_sources"]})
    coverage = "".join(
        f'<div><strong>{escape(name.replace("_", " ").title())}</strong><span>{escape(brief["coverage"][name].title())}</span></div>'
        for name in sorted(brief["coverage"])
    )
    decision_criteria = [
        finding["statement"] for finding in brief["findings"]
        if finding["category"] in {"decision_criteria", "proof_needs", "fears_objections", "buying_triggers"}
    ]
    refresh = panel["refresh_conditions"]
    return f'''<header><p class="eyebrow">Ad Testing Lab</p><h1>{escape(title)}</h1><p>{escape(panel["panel_name"])} · Version {escape(panel["version"])}</p></header>
<main>
<section><h2>Research objective and target audience</h2><p><strong>{escape(target["audience"])}</strong></p><dl><dt>Category</dt><dd>{escape(target["category"])}</dd><dt>Market</dt><dd>{escape(target["market"])}</dd><dt>Geography</dt><dd>{escape(target["geography"])}</dd><dt>Buying context</dt><dd>{escape(target["buying_context"])}</dd></dl></section>
<section><h2>How this audience was developed</h2><p>Mode: {escape(brief["research_mode"].replace("_", " "))}. Depth: {escape(brief["research_depth"].replace("_", " "))}. Research period: {escape(brief["created_at"][:10])} to {escape(brief["updated_at"][:10])}. Source types: {escape(", ".join(source_types) if source_types else "none")}.</p><div class="coverage-grid">{coverage}</div><p>{"This is a provisional audience built without research sources. It must be refreshed before reuse." if provisional else "The segments and mindsets below were compiled from the approved evidence listed in this report."}</p></section>
<section><h2>Major audience insights</h2>{findings}</section>
<section><h2>Researched segments and mindsets</h2>{''.join(segments)}</section>
<section><h2>What shapes the decision</h2>{_list_v1(decision_criteria, "No research-backed decision criteria are available for this provisional panel.")}</section>
<section><h2>Messaging and creative implications</h2>{_list_v1(implications, "No research-backed implications are available for this provisional panel.")}</section>
<section><h2>Evidence gaps and refresh conditions</h2><ul>{gaps}</ul><p>Review after {escape(refresh["review_after"][:10])}, or when any of these occurs:</p>{_list_v1(refresh["triggers"])}</section>
<section><h2>Sources</h2><ol>{sources}</ol></section>
</main>'''


def _template_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "audience-research-report-template.html"


def _report_bytes_v1(brief: Mapping[str, Any], panel: Mapping[str, Any], *, use_asset: bool) -> bytes:
    template = _template_path().read_text(encoding="utf-8") if use_asset else _REPORT_TEMPLATE_V1
    _validate_report_html(template)
    if use_asset and template != _REPORT_TEMPLATE_V1:
        raise PackageValidationError(
            "generator 1.0.0 template changed; preserve v1 and add a new generator version"
        )
    if template.count("{{REPORT_BODY}}") != 1:
        raise PackageValidationError("report template must contain exactly one REPORT_BODY marker")
    rendered = template.replace("{{REPORT_BODY}}", _report_body_v1(brief, panel)).rstrip("\n") + "\n"
    _validate_report_html(rendered)
    return rendered.encode("utf-8")


def _validate_report_html(html: str) -> None:
    parser = _SelfContainedReportParser()
    parser.feed(html)
    parser.close()
    if parser.error:
        raise PackageSafetyError(parser.error)


def _readme_bytes_v1(brief: Mapping[str, Any], panel: Mapping[str, Any]) -> bytes:
    provisional = panel["persona_research"]["source_state"] == "no_research_sources"
    state = (
        "PROVISIONAL: no research sources were used. This package is for the immediate run only, "
        "must not be registered or reused, and expires " + str(panel["persona_research"]["expires_at"]) + "."
        if provisional else
        "APPROVED RESEARCH-BACKED PANEL: this immutable version may be registered and reused while its refresh conditions remain current."
    )
    text = f"""Ad Testing Lab audience package

Panel: {panel['panel_name']} ({panel['panel_id']} version {panel['version']})
Research brief: {brief['brief_id']}

{state}

Start with audience-research-report.html. research-sources.csv is formatted for spreadsheets. The JSON files are the canonical reusable records.

Treat this package as potentially confidential. Do not add raw CRM records, names, emails, phone numbers, account IDs, precise individual coordinates, or transcript speaker identities.

Review the panel when: {'; '.join(panel['refresh_conditions']['triggers'])}. Scheduled review: {panel['refresh_conditions']['review_after']}.
"""
    return text.rstrip("\n").encode("utf-8") + b"\n"


def _derived_files(
    brief: Mapping[str, Any], panel: Mapping[str, Any], generator_version: str, *, use_asset: bool
) -> dict[str, bytes]:
    """Dispatch immutable derived-output rules by the manifest generator version."""

    if generator_version != "1.0.0":
        supported = ", ".join(sorted(SUPPORTED_GENERATOR_VERSIONS))
        raise PackageValidationError(
            f"unsupported generator_version {generator_version!r}; supported versions: {supported}"
        )
    return {
        "research-sources.csv": _csv_bytes_v1(brief, panel),
        "audience-research-report.html": _report_bytes_v1(brief, panel, use_asset=use_asset),
        "README.txt": _readme_bytes_v1(brief, panel),
    }


def _manifest(brief: Mapping[str, Any], panel: Mapping[str, Any], files: Mapping[str, bytes], generator_version: str) -> dict[str, Any]:
    generated_at = brief["approval"]["approved_at"]
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "panel_id": panel["panel_id"], "panel_version": panel["version"],
        "brief_id": brief["brief_id"], "generated_at": generated_at,
        "generator_version": generator_version,
        "files": {
            name: {"path": name, "sha256": _sha256(files[name]), "byte_count": len(files[name])}
            for name in sorted(PACKAGE_FILES)
        },
    }


def _zip_bytes(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        for name in ARCHIVE_FILES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            info.extra = b""
            info.comment = b""
            archive.writestr(info, files[name])
        archive.comment = b""
    return output.getvalue()


def _atomic_write(path: Path, data: bytes) -> None:
    fd, raw = tempfile.mkstemp(prefix=".audience-package-", dir=path.parent)
    temp = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise


def _reject_output_symlink_components(path: Path) -> None:
    """Reject symlink traversal while tolerating macOS's fixed /var and /tmp aliases."""

    if ".." in path.parts:
        raise PackageSafetyError("output path must not contain parent-directory traversal")
    absolute = path.absolute()
    current = Path(absolute.anchor)
    platform_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
    }
    for part in absolute.parts[1:]:
        current = current / part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            permitted_target = platform_aliases.get(current)
            if permitted_target is None or current.resolve() != permitted_target:
                raise PackageSafetyError(f"output path contains a symlink component: {current}")


def build_audience_package(brief: Mapping[str, Any], panel: Mapping[str, Any], output_dir: Path | str, *, generator_version: str = DEFAULT_GENERATOR_VERSION) -> PackageBuildResult:
    """Validate inputs, then atomically materialize a deterministic package directory."""
    require_valid_audience_research_pair(brief, panel)
    if generator_version not in SUPPORTED_GENERATOR_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_GENERATOR_VERSIONS))
        raise PackageValidationError(
            f"unsupported generator_version {generator_version!r}; supported versions: {supported}"
        )
    root = Path(output_dir)
    _reject_output_symlink_components(root)
    if root.exists() and (root.is_symlink() or not root.is_dir() or any(root.iterdir())):
        raise PackageSafetyError("output directory must be absent or an empty real directory")
    root.parent.mkdir(parents=True, exist_ok=True)
    _reject_output_symlink_components(root)
    brief_bytes = _canonical_json(brief)
    panel_bytes = _canonical_json(panel)
    files: dict[str, bytes] = {
        "persona-research-brief.json": brief_bytes,
        "saved-audience-panel.json": panel_bytes,
    }
    files.update(_derived_files(brief, panel, generator_version, use_asset=True))
    files["package-manifest.json"] = _canonical_json(_manifest(brief, panel, files, generator_version))
    zip_data = _zip_bytes(files)
    stage = Path(tempfile.mkdtemp(prefix=".audience-package-dir-", dir=root.parent))
    os.chmod(stage, 0o700)
    try:
        for name in ARCHIVE_FILES:
            _atomic_write(stage / name, files[name])
        _atomic_write(stage / "audience-panel-package.zip", zip_data)
        validate_package_archive(stage / "audience-panel-package.zip")
        if root.exists():
            root.rmdir()
        os.replace(stage, root)
        os.chmod(root, 0o700)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    zip_path = root / "audience-panel-package.zip"
    return PackageBuildResult(
        root, root / ARCHIVE_FILES[0], root / ARCHIVE_FILES[1], root / ARCHIVE_FILES[2],
        root / ARCHIVE_FILES[3], root / ARCHIVE_FILES[4], root / ARCHIVE_FILES[5], zip_path,
        _sha256(files["package-manifest.json"]), _sha256(zip_data),
    )


def _archive_bytes(
    source: Path | str | bytes | bytearray | BinaryIO,
    *,
    max_archive_bytes: int | None = None,
) -> bytes:
    archive_limit = (
        MAX_TOTAL_BYTES + 5 * 1024 * 1024
        if max_archive_bytes is None
        else max_archive_bytes
    )
    if (
        isinstance(archive_limit, bool)
        or not isinstance(archive_limit, int)
        or archive_limit < 1
    ):
        raise PackageSafetyError("archive byte limit is invalid")
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        if len(data) > archive_limit:
            raise PackageSafetyError("archive is too large")
        return data
    if hasattr(source, "read"):
        data = source.read()
        if not isinstance(data, bytes):
            raise PackageSafetyError("archive stream must be binary")
        if len(data) > archive_limit:
            raise PackageSafetyError("archive is too large")
        return data
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise PackageSafetyError("archive path must be a regular file, not a symlink")
    if path.stat().st_size > archive_limit:
        raise PackageSafetyError("archive is too large")
    return path.read_bytes()


def _safe_archive_infos(
    raw: bytes,
    *,
    entry_size_overrides: Mapping[str, int] | None = None,
    max_total_bytes: int | None = None,
) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    """Open a ZIP and validate metadata before any member bytes are read."""

    overrides = dict(entry_size_overrides or {})
    for name, limit in overrides.items():
        if (
            not isinstance(name, str)
            or not name
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < MAX_ENTRY_BYTES
        ):
            raise PackageSafetyError("archive entry size override is invalid")
    total_limit = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    if (
        isinstance(total_limit, bool)
        or not isinstance(total_limit, int)
        or total_limit < MAX_TOTAL_BYTES
    ):
        raise PackageSafetyError("archive total size limit is invalid")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageSafetyError("invalid ZIP archive") from exc
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        archive.close()
        raise PackageSafetyError("archive has too many entries")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        archive.close()
        raise PackageSafetyError("archive contains duplicate filenames")
    total = 0
    for info in infos:
        name = info.filename
        pure = PurePosixPath(name)
        if pure.is_absolute() or len(pure.parts) != 1 or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in name or "\x00" in name:
            archive.close()
            raise PackageSafetyError("archive contains an unsafe path")
        if info.flag_bits & 0x1:
            archive.close()
            raise PackageSafetyError("encrypted archive entries are forbidden")
        unix_mode = info.external_attr >> 16
        if info.create_system == 3 and stat.S_IFMT(unix_mode) not in {0, stat.S_IFREG}:
            archive.close()
            raise PackageSafetyError("archive entries must be regular files")
        entry_limit = overrides.get(name, MAX_ENTRY_BYTES)
        if info.is_dir() or info.file_size > entry_limit:
            archive.close()
            raise PackageSafetyError("archive entry is a directory or exceeds the size limit")
        total += info.file_size
        if total > total_limit:
            archive.close()
            raise PackageSafetyError("archive exceeds the total uncompressed size limit")
        if info.compress_size == 0:
            if info.file_size:
                archive.close()
                raise PackageSafetyError("archive entry has an invalid compression ratio")
        elif info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            archive.close()
            raise PackageSafetyError("archive entry exceeds the compression ratio limit")
    return archive, infos


def read_safe_archive_manifest(
    source: Path | str | bytes | bytearray | BinaryIO,
    *,
    entry_size_overrides: Mapping[str, int] | None = None,
    max_total_bytes: int | None = None,
    max_archive_bytes: int | None = None,
) -> tuple[bytes, bytes]:
    """Return one safe ZIP snapshot and only its manifest member bytes.

    This behavior-neutral boundary lets a caller choose a validator from the
    exact manifest version before any non-manifest package member is read.
    """

    total_limit = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    raw = _archive_bytes(
        source,
        max_archive_bytes=(
            total_limit + 5 * 1024 * 1024
            if max_archive_bytes is None
            else max_archive_bytes
        ),
    )
    archive, infos = _safe_archive_infos(
        raw,
        entry_size_overrides=entry_size_overrides,
        max_total_bytes=total_limit,
    )
    with archive:
        manifests = [info for info in infos if info.filename == "package-manifest.json"]
        if len(manifests) != 1:
            raise PackageSafetyError("archive must contain exactly one package manifest")
        try:
            return raw, archive.read(manifests[0])
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
            raise PackageSafetyError("archive member data is corrupt or unsafe") from exc


def read_safe_archive_members(
    source: Path | str | bytes | bytearray | BinaryIO,
    *,
    allowed_files: tuple[str, ...],
    entry_size_overrides: Mapping[str, int] | None = None,
    max_total_bytes: int | None = None,
    max_archive_bytes: int | None = None,
) -> dict[str, bytes]:
    """Safely materialize one exact, caller-supplied package member allowlist."""

    total_limit = MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
    raw = _archive_bytes(
        source,
        max_archive_bytes=(
            total_limit + 5 * 1024 * 1024
            if max_archive_bytes is None
            else max_archive_bytes
        ),
    )
    archive, infos = _safe_archive_infos(
        raw,
        entry_size_overrides=entry_size_overrides,
        max_total_bytes=total_limit,
    )
    with archive:
        names = [info.filename for info in infos]
        if set(names) != set(allowed_files) or len(names) != len(allowed_files):
            raise PackageSafetyError("archive filenames do not match the package allowlist")
        try:
            return {info.filename: archive.read(info) for info in infos}
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError) as exc:
            raise PackageSafetyError("archive member data is corrupt or unsafe") from exc


def _safe_read_package_archive(source: Path | str | bytes | bytearray | BinaryIO) -> dict[str, bytes]:
    """Compatibility wrapper for the v2 package member allowlist."""

    return read_safe_archive_members(source, allowed_files=ARCHIVE_FILES)


def _validate_manifest(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _MANIFEST_KEYS:
        raise PackageValidationError("manifest keys do not match the package contract")
    if value.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageValidationError("manifest schema version is invalid")
    for key in ("panel_id", "panel_version", "brief_id", "generated_at", "generator_version"):
        if not isinstance(value.get(key), str) or not value[key].strip() or any(ord(char) < 32 for char in value[key]):
            raise PackageValidationError(f"manifest {key} is invalid")
    if value["generator_version"] not in SUPPORTED_GENERATOR_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_GENERATOR_VERSIONS))
        raise PackageValidationError(
            f"unsupported generator_version {value['generator_version']!r}; supported versions: {supported}"
        )
    files = value.get("files")
    if not isinstance(files, Mapping) or set(files) != set(PACKAGE_FILES):
        raise PackageValidationError("manifest file allowlist is invalid")
    for name in PACKAGE_FILES:
        record = files[name]
        if not isinstance(record, Mapping) or set(record) != _MANIFEST_FILE_KEYS or record.get("path") != name:
            raise PackageValidationError(f"manifest record is invalid for {name}")
        digest = record.get("sha256")
        count = record.get("byte_count")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PackageValidationError(f"manifest hash is invalid for {name}")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise PackageValidationError(f"manifest byte count is invalid for {name}")
    return value


def validate_package_archive(source: Path | str | bytes | bytearray | BinaryIO) -> dict[str, Any]:
    """Validate an untrusted portable ZIP without extracting it."""
    raw = _archive_bytes(source)
    files = _safe_read_package_archive(raw)
    try:
        manifest = _validate_manifest(json.loads(files["package-manifest.json"].decode("utf-8")))
        brief = json.loads(files["persona-research-brief.json"].decode("utf-8"))
        panel = json.loads(files["saved-audience-panel.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError("package JSON is invalid") from exc
    try:
        canonical_manifest = _canonical_json(manifest)
        canonical_brief = _canonical_json(brief)
        canonical_panel = _canonical_json(panel)
    except (TypeError, ValueError) as exc:
        raise PackageValidationError("package JSON contains a non-canonical value") from exc
    if files["package-manifest.json"] != canonical_manifest:
        raise PackageValidationError("package manifest is not canonical JSON")
    if files["persona-research-brief.json"] != canonical_brief or files["saved-audience-panel.json"] != canonical_panel:
        raise PackageValidationError("brief and panel must use canonical JSON encoding")
    try:
        require_valid_audience_research_pair(brief, panel)
    except AudienceResearchValidationError as exc:
        raise PackageValidationError(f"brief/panel validation failed: {exc}") from exc
    if (manifest["panel_id"], manifest["panel_version"], manifest["brief_id"]) != (panel["panel_id"], panel["version"], brief["brief_id"]):
        raise PackageValidationError("manifest identity does not match brief and panel")
    if manifest["generated_at"] != brief["approval"]["approved_at"]:
        raise PackageValidationError("manifest generated_at must equal the approval timestamp")
    for name in PACKAGE_FILES:
        record = manifest["files"][name]
        if record["byte_count"] != len(files[name]) or record["sha256"] != _sha256(files[name]):
            raise PackageValidationError(f"manifest hash or byte count mismatch for {name}")
    try:
        html = files["audience-research-report.html"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageValidationError("report must be UTF-8") from exc
    _validate_report_html(html)
    expected = _derived_files(brief, panel, manifest["generator_version"], use_asset=False)
    if any(files[name] != expected[name] for name in expected):
        raise PackageValidationError("compiled package files do not match their canonical JSON sources")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION, "status": "valid",
        "panel_id": panel["panel_id"], "panel_version": panel["version"], "brief_id": brief["brief_id"],
        "panel_sha256": _sha256(files["saved-audience-panel.json"]),
        "brief_sha256": _sha256(files["persona-research-brief.json"]),
        "package_manifest_sha256": _sha256(files["package-manifest.json"]),
        "package_zip_sha256": _sha256(raw),
        "package_manifest_byte_count": len(files["package-manifest.json"]), "package_zip_byte_count": len(raw),
    }


def read_validated_package_archive(
    source: Path | str | bytes | bytearray | BinaryIO,
) -> dict[str, object]:
    """Read, validate, and return one immutable archive-byte snapshot."""

    archive_bytes = _archive_bytes(source)
    validation = validate_package_archive(archive_bytes)
    members = _safe_read_package_archive(archive_bytes)
    return {
        "archive_bytes": archive_bytes,
        "validation": dict(validation),
        "members": dict(members),
    }


def _safe_extract_package_archive(
    source: Path | str | bytes | bytearray | BinaryIO,
    destination: Path | str,
    *,
    allowed_root: Path | str,
) -> dict[str, Any]:
    """Private extraction API for trusted consumers after full validation."""
    raw = _archive_bytes(source)
    validation = validate_package_archive(raw)
    files = _safe_read_package_archive(raw)
    target = Path(destination)
    if target.exists():
        raise PackageSafetyError("extraction destination must not already exist")
    root = Path(allowed_root).resolve()
    resolved_target = target.resolve()
    if resolved_target != root and root not in resolved_target.parents:
        raise PackageSafetyError("extraction destination is outside the allowed root")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise PackageSafetyError("extraction destination parent must not be a symlink")
    temp = Path(tempfile.mkdtemp(prefix=".audience-extract-", dir=target.parent))
    os.chmod(temp, 0o700)
    try:
        for name in ARCHIVE_FILES:
            _atomic_write(temp / name, files[name])
        os.replace(temp, target)
        os.chmod(target, 0o700)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return validation


__all__ = [
    "ARCHIVE_FILES", "DEFAULT_GENERATOR_VERSION", "PACKAGE_FILES", "PACKAGE_SCHEMA_VERSION",
    "SUPPORTED_GENERATOR_VERSIONS",
    "PackageBuildResult", "PackageSafetyError", "PackageValidationError",
    "build_audience_package", "read_safe_archive_manifest", "read_safe_archive_members",
    "read_validated_package_archive",
    "validate_package_archive",
]
