# モジュール読解: ファイル出力の入口。通常形式は成果物とv2マニフェスト、明示モードは専用ローカル処理へ渡す。
# 出力先と容量を検査し、確定済み成果物の自動ロールバックは行わない。
"""Configurable landing-directory and S3-compatible destinations."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import random
import shutil
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from PIL import Image, ImageColor, ImageDraw

from file_connections import Remote, check_nfs
from generation import (
    GenerationError,
    batch_limit,
    encode_json,
    generate_record,
    inject_fault,
    map_json,
    record_key,
    render,
)


def file_allowed_roots():
    """設定された出力許可ルートを、書き込みを伴わず取得する。"""
    data = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    return [
        Path(p)
        for p in os.environ.get(
            "FILE_ALLOWED_ROOTS", os.environ.get("LANDING_DIR", str(data / "exports"))
        ).split(os.pathsep)
        if p
    ]


# 読解 allowed_directory: 正規化した出力先が許可ルート内かを確認し、必要なディレクトリを作る。
def allowed_directory(value):
    roots = [root.resolve() for root in file_allowed_roots()]
    path = Path(value).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in roots):
        raise GenerationError("出力先は許可されたランディング領域内に指定してください")
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_directory_at(parent_fd, parts):
    """親ディレクトリから、シンボリックリンクを辿らず子ディレクトリを開く。"""
    fd = os.dup(parent_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        for part in parts:
            if part in {"", ".", ".."} or "/" in part:
                raise GenerationError("ディレクトリ階層の指定が不正です")
            try:
                child_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o750, dir_fd=fd)
                except FileExistsError:
                    pass
                child_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child_fd
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_quickstart_landing(path):
    """明示的に許可された絶対パスだけを、変更されないディレクトリ参照として開く。"""
    roots = [
        Path(os.path.abspath(root.expanduser())) for root in file_allowed_roots()
    ]
    if not path.is_absolute() or not any(path.is_relative_to(root) for root in roots):
        raise GenerationError("既定のランディングが出力許可ルート外です")
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        return open_directory_at(root_fd, path.parts[1:])
    except OSError as exc:
        raise GenerationError("既定のランディングを安全に開けません") from exc
    finally:
        os.close(root_fd)


def atomic_write_at(root_fd, relative, data):
    """固定したルートから辿り、一時ファイルを同期して上書きなしで確定する。"""
    directory_fd = open_directory_at(root_fd, relative.parts[:-1])
    name = relative.name
    temporary = name + ".part"
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640, dir_fd=directory_fd
        )
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.link(
                temporary,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        finally:
            os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


# 読解 atomic_write: 一時ファイルを同期してから最終名へ置換し、ディレクトリも同期する。
#
# 事前の存在検査は並行書込みとの排他ではない。呼出側で競合を避ける。
def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError("出力済みのファイルは上書きしません")
    temporary = path.with_name(path.name + ".part")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


# 読解 s3_client: 明示された認証とTLS設定からS3クライアントを構築する。
def s3_client(profile):
    if not profile.access_key or not profile.secret_key:
        raise GenerationError("アクセスキーとシークレットキーが必要です")
    if profile.ssl_mode != "off" and profile.endpoint_url.startswith("http:"):
        raise GenerationError("TLS有効時はHTTPSのエンドポイントを指定してください")
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=profile.endpoint_url or None,
        region_name=profile.region,
        aws_access_key_id=profile.access_key.get_secret_value()
        if profile.access_key
        else None,
        aws_secret_access_key=profile.secret_key.get_secret_value()
        if profile.secret_key
        else None,
        aws_session_token=profile.session_token.get_secret_value()
        if profile.session_token
        else None,
        verify=profile.ssl_mode == "verify",
        config=Config(
            connect_timeout=profile.timeout_seconds,
            read_timeout=profile.timeout_seconds,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


# 読解 test_connection: 接続先の参照・書込可否情報を確認する。データ生成は行わない。
def test_connection(profile):
    if profile.kind in {"local", "nfs"}:
        path = allowed_directory(profile.directory)
        if profile.kind == "nfs":
            check_nfs(path, profile.nfs_expected_source)
        return {
            "status": "writable" if os.access(path, os.W_OK) else "read_only",
            "directory": str(path),
            "free_gib": round(shutil.disk_usage(path).free / 1024**3, 2),
        }
    if profile.kind in {"smb", "ftp", "sftp"}:
        remote = Remote(profile)
        try:
            return remote.test()
        finally:
            remote.close()
    if profile.kind != "s3":
        raise GenerationError("ディレクトリまたはS3互換の接続先を指定してください")
    if not profile.access_key or not profile.secret_key:
        raise GenerationError("アクセスキーとシークレットキーが必要です")
    client = s3_client(profile)
    try:
        client.head_bucket(Bucket=profile.bucket)
    finally:
        client.close()
    return {"status": "connected", "bucket": profile.bucket, "prefix": profile.prefix}


# 読解 Destination: 実行の出力パスを束ね、ローカル・リモート・S3の書込みを振り分ける。
class Destination:
    # 読解 Destination.__init__: 接続と生成条件から実行用prefixを展開・検査し、必要な出力先を準備する。
    # NFSマウント確認やリモートクライアント生成を伴い得る。終了時はcloseで解放する。
    def __init__(self, profile, spec, job_id, *, local_root_fd=None):
        self.profile = profile
        self.local_root_fd = local_root_fd
        if local_root_fd is not None and profile.kind != "local":
            raise GenerationError("固定されたルートはローカル出力専用です")
        self.prefix = render(
            spec.files.path_template,
            {**spec.parameters, "run_key": spec.run_key, "job_id": job_id},
        )
        validate_path(self.prefix)
        validate_path(profile.prefix, empty=True)
        self.root = (
            allowed_directory(profile.directory)
            if local_root_fd is None and profile.kind in {"local", "nfs"}
            else None
        )
        if profile.kind == "nfs":
            check_nfs(self.root, profile.nfs_expected_source)
        self.remote = (
            Remote(profile) if profile.kind in {"smb", "ftp", "sftp"} else None
        )
        self.client = s3_client(profile) if profile.kind == "s3" else None
        if local_root_fd is None and not self.root and not self.client and not self.remote:
            raise GenerationError("ファイル出力先を指定してください")

    # 読解 Destination.key: 絶対パスと親参照を拒否し、設定済み接頭辞を含むS3キーを作る。
    def key(self, relative):
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise GenerationError("出力パスが不正です")
        return "/".join(
            p for p in [self.profile.prefix.strip("/"), self.prefix, relative] if p
        )

    # 読解 Destination.write: 出力先別の容量・パス・上書き条件に従って一成果物を書き込む。
    def write(self, relative, data, content_type):
        if self.local_root_fd is not None:
            capacity = os.fstatvfs(self.local_root_fd)
            if capacity.f_bavail * capacity.f_frsize < 2 * 1024**3:
                raise RuntimeError("disk_space_low")
            path = PurePosixPath(self.prefix) / relative
            validate_path(path.as_posix())
            atomic_write_at(self.local_root_fd, path, data)
        elif self.root:
            if shutil.disk_usage(self.root).free < 2 * 1024**3:
                raise RuntimeError("disk_space_low")
            path = (self.root / self.prefix / relative).resolve()
            if not path.is_relative_to(self.root):
                raise GenerationError("出力パスが許可範囲外です")
            atomic_write(path, data)
        elif self.remote:
            self.remote.write(self.prefix + "/" + relative, data)
        else:
            self.client.put_object(
                Bucket=self.profile.bucket,
                Key=self.key(relative),
                Body=data,
                ContentType=content_type,
                Metadata={"sha256": hashlib.sha256(data).hexdigest()},
                IfNoneMatch="*",
            )

    # 読解 Destination.close: 確保したリモート接続とS3クライアントを解放する。
    def close(self):
        if self.remote:
            self.remote.close()
        if self.client:
            self.client.close()


# 読解 image_bytes: 画像条件と再現キーから模様・ノイズ・欠陥を描き、符号化バイト列と欠陥有無を返す。
# ファイル書込みは行わず、乱数シードはキーから固定する。
def image_bytes(options, key):
    rng = random.Random(int(key[:16], 16))
    background = ImageColor.getrgb(options.background)
    foreground = ImageColor.getrgb(options.foreground)
    image = Image.new("RGB", (options.width, options.height), background)
    draw = ImageDraw.Draw(image)
    if options.pattern == "grid":
        spacing = max(8, min(options.width, options.height) // 12)
        for x in range(0, options.width, spacing):
            draw.line((x, 0, x, options.height), fill=foreground)
        for y in range(0, options.height, spacing):
            draw.line((0, y, options.width, y), fill=foreground)
    elif options.pattern == "gradient":
        for y in range(options.height):
            draw.line(
                (0, y, options.width, y),
                fill=tuple(
                    int(a + (b - a) * y / options.height)
                    for a, b in zip(background, foreground)
                ),
            )
    else:
        for _ in range(100):
            x, y = rng.randrange(options.width), rng.randrange(options.height)
            radius = max(2, min(options.width, options.height) // 80)
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius), fill=foreground
            )
    defective = rng.random() < options.defect_probability
    if defective:
        draw.line(
            (
                options.width // 4,
                options.height // 3,
                options.width * 3 // 4,
                options.height * 2 // 3,
            ),
            fill=ImageColor.getrgb(options.defect_color),
            width=max(2, options.width // 100),
        )
    if options.noise:
        raw = bytearray(image.tobytes())
        noise = rng.randbytes(len(raw))
        for i, value in enumerate(raw):
            raw[i] = max(
                0,
                min(255, value + (noise[i] % (2 * options.noise + 1)) - options.noise),
            )
        image = Image.frombytes("RGB", image.size, bytes(raw))
    output = io.BytesIO()
    image.save(
        output,
        format="PNG" if options.format == "png" else "JPEG",
        quality=options.quality,
    )
    return output.getvalue(), defective


# 読解 validate_path: 出力名として使える相対パスかを確認する。ファイルの存在は調べない。
def validate_path(value, empty=False):
    path = PurePosixPath(value)
    if (
        (not value and not empty)
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise GenerationError("出力パスは相対パスで指定してください。..は使えません")
    return value


# 読解 handle: 設定のcompletion_modeを判定し、製品CSV専用の明示モードは専用処理へ直ちに委譲する。
# 既定経路はバッチごとの成果物と任意v2マニフェストを生成する。部分成功の巻戻しはしない。
def handle(spec, ctx, profile, store, *, local_root_fd=None):
    if spec.files.completion_mode == "lakehouse-products-local":
        from lakehouse_export import handle_products

        return handle_products(spec, ctx, profile)
    destination = Destination(
        profile, spec, ctx.run_id, local_root_fd=local_root_fd
    )
    total_bytes = 0
    group_size = batch_limit(spec, spec.files.records_per_file)
    try:
        for offset in range(0, spec.rows, group_size):
            ctx.check()
            inject_fault(spec, ctx, "before_batch", offset // group_size + 1)
            count = min(group_size, spec.rows - offset)
            records = [
                generate_record(spec, spec.start_sequence + offset + i)
                for i in range(count)
            ]
            keys = [
                record_key(spec, spec.start_sequence + offset + i) for i in range(count)
            ]
            values = {
                **spec.parameters,
                **records[0],
                "run_key": spec.run_key,
                "job_id": ctx.run_id,
                "sequence": spec.start_sequence + offset,
                "record_id": keys[0],
            }
            filename = validate_path(render(spec.files.filename_template, values))
            if "/" in filename:
                raise GenerationError(
                    "ファイル名にはディレクトリ区切りを使用できません"
                )
            artifacts = []

            # 読解 handle.write: 形式別の試験用破損・圧縮を適用し、容量上限を確認して成果物を書き出す。
            # バイト数とチェックサムは実際の出力に対応させ、成果物一覧と出力履歴を更新する。
            def write(relative, data, content_type, compress=False):
                nonlocal total_bytes
                mode = spec.faults.artifact_mode if spec.faults.enabled else "none"
                if relative.startswith("json/"):
                    if mode == "truncate_json":
                        data = data[: max(1, len(data) // 2)]
                    elif mode == "invalid_json_utf8":
                        data = b"\xff" + data
                if relative.startswith("images/") and relative.endswith(
                    (".png", ".jpg")
                ):
                    if mode == "truncate_image":
                        data = data[: max(8, len(data) // 4)]
                    elif mode == "wrong_image_extension":
                        relative = relative.rsplit(".", 1)[0] + (
                            ".jpg" if relative.endswith(".png") else ".png"
                        )
                if (
                    relative.startswith("csv/")
                    and mode == "csv_header_mismatch"
                    and spec.files.csv_header
                ):
                    text = data.decode(spec.files.csv_encoding)
                    data = text.replace(
                        spec.fields[0].name, "unexpected_" + spec.fields[0].name, 1
                    ).encode(spec.files.csv_encoding)
                if compress and spec.files.compression == "gzip":
                    data = gzip.compress(data, mtime=0)
                    relative += ".gz"
                    content_type = "application/gzip"
                total_bytes += len(data)
                if total_bytes > spec.max_output_mb * 1024 * 1024:
                    raise RuntimeError("run_size_limit")
                destination.write(relative, data, content_type)
                artifacts.append(
                    {
                        "path": relative,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
                ctx.outputs.append(
                    {"path": destination.prefix + "/" + relative, "kind": profile.kind}
                )
                del ctx.outputs[:-10]

            if "json" in spec.files.formats:
                payloads = [
                    map_json(
                        spec.files.json_template,
                        row,
                        spec.parameters,
                        {
                            "sequence": spec.start_sequence + offset + i,
                            "record_key": keys[i],
                            "run_key": spec.run_key,
                        },
                    )
                    for i, row in enumerate(records)
                ]
                if spec.files.json_mode == "object":
                    for i, payload in enumerate(payloads):
                        write(
                            "json/" + filename + f"-{i:06d}.json",
                            (
                                encode_json(payload, pretty=spec.files.json_pretty)
                                + "\n"
                            ).encode(),
                            "application/json",
                            True,
                        )
                else:
                    content = (
                        "\n".join(encode_json(row) for row in payloads) + "\n"
                        if spec.files.json_mode == "jsonl"
                        else "["
                        + (",\n" if spec.files.json_pretty else ",").join(
                            encode_json(row, pretty=spec.files.json_pretty)
                            for row in payloads
                        )
                        + "]\n"
                    )
                    write(
                        "json/"
                        + filename
                        + (".jsonl" if spec.files.json_mode == "jsonl" else ".json"),
                        content.encode(),
                        "application/json",
                        True,
                    )
            if "csv" in spec.files.formats:
                buffer = io.StringIO(newline="")
                writer = csv.DictWriter(
                    buffer,
                    fieldnames=[f.name for f in spec.fields],
                    delimiter=spec.files.csv_delimiter,
                    quotechar=spec.files.csv_quotechar,
                    quoting={
                        "minimal": csv.QUOTE_MINIMAL,
                        "all": csv.QUOTE_ALL,
                        "nonnumeric": csv.QUOTE_NONNUMERIC,
                    }[spec.files.csv_quoting],
                    lineterminator="\r\n" if spec.files.csv_newline == "CRLF" else "\n",
                )
                if spec.files.csv_header:
                    writer.writeheader()
                writer.writerows(
                    {
                        k: encode_json(v)
                        if isinstance(v, (dict, list))
                        else spec.files.csv_null_value
                        if v is None
                        else v
                        for k, v in row.items()
                    }
                    for row in records
                )
                write(
                    "csv/" + filename + ".csv",
                    buffer.getvalue().encode(spec.files.csv_encoding),
                    "text/csv",
                    True,
                )
            from document_formats import documents

            for folder, extension, content, mime in documents(spec, records):
                write(
                    folder + "/" + filename + extension, content, mime, folder == "xml"
                )
            if "image" in spec.files.formats:
                for i, (key, row) in enumerate(zip(keys, records)):
                    if (spec.start_sequence + offset + i) % spec.files.image.every:
                        continue
                    ctx.check()
                    data, defective = image_bytes(spec.files.image, key)
                    extension = ".png" if spec.files.image.format == "png" else ".jpg"
                    path = "images/" + key + extension
                    write(
                        path, data, "image/png" if extension == ".png" else "image/jpeg"
                    )
                    if spec.files.write_sidecars:
                        metadata = {
                            "record": row,
                            "record_key": key,
                            "image_path": path,
                            "synthetic": True,
                            "synthetic_defect": defective,
                        }
                        write(
                            "images/" + key + ".meta.json",
                            (encode_json(metadata) + "\n").encode(),
                            "application/json",
                        )
            inject_fault(spec, ctx, "after_write", offset // group_size + 1)
            if (
                spec.faults.enabled
                and spec.faults.artifact_mode == "manifest_checksum_mismatch"
                and artifacts
            ):
                artifacts[0]["sha256"] = "0" * 64
            manifest = {
                "schema_version": "source.file-manifest.v2",
                "dataset": spec.name,
                "run_key": spec.run_key,
                "record_count": count,
                "first_sequence": spec.start_sequence + offset,
                "record_keys": keys,
                "artifacts": artifacts,
            }
            payload = (
                (json.dumps(manifest, ensure_ascii=False) + "\n").encode()
                if spec.files.write_manifest
                else b""
            )
            if total_bytes + len(payload) > spec.max_output_mb * 1024 * 1024:
                raise RuntimeError("run_size_limit")
            if payload:
                destination.write(
                    "manifests/" + filename + ".ready.json", payload, "application/json"
                )
            total_bytes += len(payload)
            ctx.progress(
                processed=count,
                files=len(artifacts) + bool(payload),
                bytes_written=sum(a["bytes"] for a in artifacts) + len(payload),
            )
            if offset + count < spec.rows:
                ctx.pace(offset + count)
    finally:
        destination.close()


# 読解 artifact: 実行の直近出力から生成画像を選び、保存先に応じた応答を返す。
def artifact(run, store):
    if run["spec"].get("version") != 2:
        raise HTTPException(404, "旧形式の画像は元のランディングから参照してください")
    spec = run["spec"]
    profile, _ = store.get_profile(
        spec["connection_id"], spec.get("connection_revision")
    )
    for item in reversed(run["outputs"]):
        relative = item["path"]
        if Path(relative).suffix not in {".png", ".jpg"}:
            continue
        if profile.kind in {"local", "nfs"}:
            root = allowed_directory(profile.directory)
            path = (root / relative).resolve()
            if path.is_relative_to(root) and path.is_file():
                return FileResponse(path)
        elif profile.kind in {"smb", "ftp", "sftp"}:
            remote = Remote(profile)
            try:
                data = remote.read(relative)
                if len(data) > 64 * 1024 * 1024:
                    raise GenerationError("画像が大きすぎます")
                return Response(
                    data,
                    media_type="image/png"
                    if relative.endswith(".png")
                    else "image/jpeg",
                )
            finally:
                remote.close()
        else:
            client = s3_client(profile)
            try:
                key = "/".join(p for p in [profile.prefix.strip("/"), relative] if p)
                result = client.get_object(Bucket=profile.bucket, Key=key)
                if result.get("ContentLength", 0) > 64 * 1024 * 1024:
                    raise GenerationError("画像が大きすぎます")
                data = result["Body"].read(64 * 1024 * 1024 + 1)
                return Response(
                    data,
                    media_type="image/png"
                    if relative.endswith(".png")
                    else "image/jpeg",
                )
            finally:
                client.close()
    raise HTTPException(404, "生成画像がありません")
