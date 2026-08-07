from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import uuid


class SourceSnapshotError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSnapshot:
    original_path: Path
    staged_path: Path
    byte_length: int
    source_sha256: str
    media_type: str
    stat_identity: tuple[int, int, int, int, int]


def stable_stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _prepare_staging_root(staging_root: Path) -> None:
    try:
        staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        staged_root_stat = staging_root.lstat()
        if stat.S_ISLNK(staged_root_stat.st_mode) or not stat.S_ISDIR(
            staged_root_stat.st_mode
        ):
            raise SourceSnapshotError(
                "staging root must be a non-symlink directory"
            )
        os.chmod(staging_root, 0o700)
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError("protected staging root could not be prepared") from exc


def _cleanup_rejected_staged_source(
    *,
    staged_path: Path,
    output: int | None,
) -> None:
    securely_truncated = False
    if output is not None:
        try:
            os.ftruncate(output, 0)
        except OSError:
            pass
        try:
            os.fsync(output)
            securely_truncated = os.fstat(output).st_size == 0
        except OSError:
            securely_truncated = False
        try:
            os.close(output)
        except OSError:
            pass

    deleted = False
    try:
        staged_path.unlink(missing_ok=True)
        deleted = True
    except OSError:
        deleted = False

    if not deleted and not securely_truncated:
        raise SourceSnapshotError("rejected staged source cleanup failed")


def snapshot_source(
    path: Path,
    *,
    staging_root: Path,
    byte_limit: int = 50_000_000,
    after_open_hook: Callable[[], None] | None = None,
) -> SourceSnapshot:
    if isinstance(byte_limit, bool) or not isinstance(byte_limit, int) or byte_limit < 0:
        raise SourceSnapshotError("byte limit must be a non-negative integer")

    source_path = Path(path)
    protected_root = Path(staging_root)
    try:
        before = source_path.lstat()
    except OSError as exc:
        raise SourceSnapshotError("source must be one non-symlink regular file") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise SourceSnapshotError("source must be one non-symlink regular file")

    try:
        descriptor = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise SourceSnapshotError("source identity changed while opening") from exc

    staged_path: Path | None = None
    output: int | None = None
    accepted = False
    try:
        opened = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_uid,
        ):
            raise SourceSnapshotError("source identity changed while opening")
        if opened.st_size > byte_limit:
            raise SourceSnapshotError("source exceeds the admitted byte limit")
        if after_open_hook is not None:
            after_open_hook()

        _prepare_staging_root(protected_root)
        staged_path = protected_root / f"source-{uuid.uuid4().hex}.bin"
        try:
            output = os.open(
                staged_path,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
        except OSError as exc:
            raise SourceSnapshotError("protected staged source could not be created") from exc

        digest = hashlib.sha256()
        length = 0
        try:
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                length += len(chunk)
                if length > byte_limit:
                    raise SourceSnapshotError(
                        "source exceeds the admitted byte limit"
                    )
                digest.update(chunk)
                remaining = memoryview(chunk)
                while remaining:
                    written = os.write(output, remaining)
                    if written <= 0:
                        raise SourceSnapshotError("staged source write failed")
                    remaining = remaining[written:]
            os.fsync(output)
        except SourceSnapshotError:
            raise
        except OSError as exc:
            raise SourceSnapshotError("source could not be staged") from exc
        after = os.fstat(descriptor)
        if (
            stable_stat_identity(opened) != stable_stat_identity(after)
            or length != after.st_size
        ):
            raise SourceSnapshotError("source changed while read")

        snapshot = SourceSnapshot(
            original_path=source_path,
            staged_path=staged_path,
            byte_length=length,
            source_sha256="sha256:" + digest.hexdigest(),
            media_type="application/octet-stream",
            stat_identity=stable_stat_identity(after),
        )
        os.close(output)
        output = None
        accepted = True
        return snapshot
    except SourceSnapshotError:
        raise
    except OSError as exc:
        raise SourceSnapshotError("source changed while read") from exc
    finally:
        os.close(descriptor)
        if staged_path is not None and not accepted:
            _cleanup_rejected_staged_source(
                staged_path=staged_path,
                output=output,
            )
