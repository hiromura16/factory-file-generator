"""Opt-in single-batch local product fixture export; no remote destinations."""

import csv
import fcntl
import hashlib
import io
import os
import re
import secrets
import stat
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from generation import GenerationError, generate_record

COLUMNS = (
    "product_id",
    "product_name",
    "product_family",
    "unit",
    "standard_cycle_seconds",
    "updated_at",
)


@contextmanager
def directory(path):
    """Open existing absolute directories without following any symlink."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise GenerationError("Absolute local output root required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            sticky_system = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
            if info.st_uid not in (0, os.getuid()) or (
                info.st_mode & 0o022 and not sticky_system
            ):
                raise GenerationError("Untrusted output ancestor")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise GenerationError("Output directory must be privately controlled")
        yield fd
    finally:
        os.close(fd)


def existing(fd, name):
    """既存ファイルを親ディレクトリ記述子から開く。未作成ならNoneを返す。"""
    try:
        file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(file, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
        ):
            raise GenerationError("Unsafe existing export object")
        data = stream.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise GenerationError("Export size exceeded")
        return data


def publish(fd, name, content):
    """同期済みの一時ファイルを最終名にリンクし、同名ファイルの上書きを防ぐ。"""
    temporary = ".export-" + secrets.token_hex(16) + ".part"
    file = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o640,
        dir_fd=fd,
    )
    try:
        with os.fdopen(file, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-clobber; even non-cooperating creation cannot be overwritten.
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=fd)
        os.fsync(fd)


def publish_pair(fd, name, data):
    """ロック中にCSVと完了印の状態を照合し、必要なファイルだけを発行する。

    CSVだけが残っている場合は内容を確かめ、同じ内容のときだけ完了印を補う。
    """
    marker = name + ".ready"
    expected = (hashlib.sha256(data).hexdigest() + "\n").encode("ascii")
    lock = os.open(
        ".lakehouse-export.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=fd,
    )
    try:
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise GenerationError("Unsafe export lock")
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous, ready = existing(fd, name), existing(fd, marker)
        if previous is not None and previous != data:
            raise GenerationError("Existing payload conflict")
        if ready is not None and (ready != expected or previous is None):
            raise GenerationError("Existing completion marker conflict")
        if previous is None:
            publish(fd, name, data)
        if ready is None:
            # Re-read finalized bytes before publishing the completion signal.
            if existing(fd, name) != data:
                raise GenerationError("Finalized payload changed")
            publish(fd, marker, expected)
        return (
            int(previous is None) + int(ready is None),
            (len(data) if previous is None else 0)
            + (len(expected) if ready is None else 0),
        )
    finally:
        os.close(lock)


def handle_products(spec, ctx, profile):
    """Narrow draft mode, not arbitrary files or a replacement for all sources."""
    options = spec.files
    if (
        profile.kind != "local"
        or profile.prefix
        or options.path_template != "."
        or options.filename_template != "erp_products__{run_key}_{sequence:04d}"
        or not re.fullmatch(r"[a-f0-9]{12}", spec.run_key)
        or not 1 <= spec.start_sequence <= 9999
        or spec.start_sequence + spec.rows - 1 > 9999
        or not options.write_manifest
        or spec.rows > 1000
        or spec.rows > options.records_per_file
        or spec.rows > spec.batch_size
        or options.formats != ["csv"]
        or options.compression != "none"
        or options.csv_encoding != "utf-8"
        or not options.csv_header
        or options.csv_delimiter != ","
        or options.csv_quotechar != '"'
        or options.csv_quoting != "minimal"
        or options.csv_newline != "LF"
        or options.csv_null_value != ""
        or options.write_sidecars
        or spec.faults.enabled
        or spec.anomalies
        or spec.task != "generate"
        or tuple(field.name for field in spec.fields) != COLUMNS
        or tuple(field.data_type for field in spec.fields)
        != ("string", "string", "string", "string", "decimal", "datetime")
        or spec.fields[4].precision != 18
        or spec.fields[4].scale != 3
    ):
        raise GenerationError("Unsupported local Lakehouse product configuration")
    root = Path(profile.directory)
    allowed = os.environ.get("FILE_ALLOWED_ROOTS", "").split(os.pathsep)
    if not any(value and root.is_relative_to(Path(value)) for value in allowed):
        raise GenerationError("Output root not explicitly allowed")
    rows = [generate_record(spec, spec.start_sequence + i) for i in range(spec.rows)]
    for row in rows:
        if any(
            not isinstance(row[column], str) or not row[column].strip()
            for column in COLUMNS[:4]
        ) or any(row[column] is None for column in COLUMNS[4:]):
            raise GenerationError("Missing product value")
        try:
            cycle = Decimal(str(row["standard_cycle_seconds"]))
            timestamp = datetime.fromisoformat(str(row["updated_at"]))
            valid_cycle = (
                cycle.is_finite()
                and 0 < cycle <= Decimal("999999999999999.999")
                and cycle == cycle.quantize(Decimal("0.001"))
            )
        except (ValueError, ArithmeticError) as error:
            raise GenerationError("Invalid product value") from error
        if not valid_cycle or timestamp.tzinfo is None:
            raise GenerationError("Invalid product value")
    if len({row["product_id"] for row in rows}) != len(rows):
        raise GenerationError("Duplicate product keys")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    data = buffer.getvalue().encode("utf-8")
    if len(data) > min(16 * 1024 * 1024, spec.max_output_mb * 1024 * 1024):
        raise GenerationError("Export size exceeded")
    name = f"erp_products__{spec.run_key}_{spec.start_sequence:04d}.csv"
    ctx.check()
    with directory(root) as root_fd:
        try:
            os.mkdir("csv", 0o750, dir_fd=root_fd)
        except FileExistsError:
            pass
        # Open relative to pinned parent, never resolve a mutable path again.
        fd = os.open(
            "csv", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise GenerationError("Export directory must be privately controlled")
            files, byte_count = publish_pair(fd, name, data)
        finally:
            os.close(fd)
    ctx.outputs.append({"path": "csv/" + name, "kind": "local"})
    ctx.progress(processed=len(rows), files=files, bytes_written=byte_count)
