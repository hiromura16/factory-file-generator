import gzip
import json

import pytest
from PIL import Image

from config import ConnectionProfile, RunSpec
from generation import GenerationError
from file_adapter import atomic_write, handle, image_bytes
from conftest import Context


def test_custom_json_csv_compression_images_and_manifest(tmp_path):
    profile = ConnectionProfile(
        id="local", name="landing", kind="local", directory=str(tmp_path / "exports")
    )
    spec = RunSpec(rows=4, target_rate=0)
    spec.files.records_per_file = 2
    spec.files.compression = "gzip"
    spec.files.csv_delimiter = ";"
    spec.files.csv_quoting = "all"
    spec.files.csv_newline = "CRLF"
    spec.files.json_template = {
        "device": {"id": "$row.entity_id"},
        "measurement": "$row.value",
    }
    spec.files.image.width = 64
    spec.files.image.height = 48
    spec.files.image.every = 2
    spec.files.image.defect_probability = 1
    ctx = Context()
    handle(spec, ctx, profile, None)
    root = tmp_path / "exports" / spec.run_key / "test-job"
    manifests = list(root.glob("manifests/*.ready.json"))
    assert len(manifests) == 2
    data = json.loads(gzip.decompress(next(root.glob("json/*.gz")).read_bytes()))
    assert "device" in data[0] and "measurement" in data[0]
    csv = gzip.decompress(next(root.glob("csv/*.gz")).read_bytes()).decode("utf-8-sig")
    assert '"id";"sequence_no"' in csv and "\r\n" in csv
    assert len(list(root.glob("images/*.png"))) == 2
    for image in root.glob("images/*.png"):
        assert Image.open(image).size == (64, 48)
    for manifest in manifests:
        for artifact in json.loads(manifest.read_text())["artifacts"]:
            import hashlib

            content = (root / artifact["path"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
    assert ctx.stats["processed"] == 4 and ctx.stats["files"] == 10


def test_one_object_per_file_and_path_templates(tmp_path):
    profile = ConnectionProfile(
        id="local", name="local", kind="local", directory=str(tmp_path / "exports")
    )
    spec = RunSpec(
        rows=3,
        target_rate=0,
        parameters={"area": "custom"},
        fields=[{"name": "key", "generator": "sequence", "data_type": "integer"}],
    )
    spec.files.formats = ["json"]
    spec.files.json_mode = "object"
    spec.files.path_template = "{area}/{job_id}"
    spec.files.filename_template = "row-{sequence:04d}"
    spec.files.write_manifest = False
    handle(spec, Context(), profile, None)
    files = list((tmp_path / "exports/custom/test-job/json").glob("*.json"))
    assert len(files) == 3 and isinstance(json.loads(files[0].read_text()), dict)
    spec.files.path_template = "../escape"
    with pytest.raises(GenerationError):
        handle(spec, Context(), profile, None)


def test_deterministic_image_and_no_overwrite(tmp_path):
    spec = RunSpec()
    spec.files.image.width = 32
    spec.files.image.height = 32
    assert image_bytes(spec.files.image, "abcd" * 8) == image_bytes(
        spec.files.image, "abcd" * 8
    )
    target = tmp_path / "x"
    atomic_write(target, b"first")
    with pytest.raises(FileExistsError):
        atomic_write(target, b"overwrite")
    assert target.read_bytes() == b"first" and not list(tmp_path.glob("*.part"))


def test_nfs_profile_refuses_non_nfs_directory(tmp_path):
    profile = ConnectionProfile(
        id="nfs", name="NFS", kind="nfs", directory=str(tmp_path / "exports")
    )
    from file_adapter import test_connection

    with pytest.raises(GenerationError, match="NFS"):
        test_connection(profile)


def test_interrupted_bundle_has_no_ready_marker_and_checksum_corruption_is_explicit(
    tmp_path,
):
    from config import FaultOptions
    from factorylib import Runtime

    profile = ConnectionProfile(
        id="local", name="local", kind="local", directory=str(tmp_path / "exports")
    )
    spec = RunSpec(
        rows=3,
        target_rate=0,
        faults=FaultOptions(enabled=True, stage="after_write", action="fail"),
    )
    spec.files.formats = ["json"]
    runtime = Runtime("files", lambda s, c: handle(s, c, profile, None), 100000000)
    job = runtime.start(spec)
    runtime.thread.join(timeout=5)
    assert runtime.get(job["id"])["status"] == "failed"
    root = tmp_path / "exports" / spec.run_key / job["id"]
    assert list(root.glob("json/*.json")) and not list(
        root.glob("manifests/*.ready.json")
    )
    spec.run_key = "bad-checksum"
    spec.faults = FaultOptions(enabled=True, artifact_mode="manifest_checksum_mismatch")
    handle(spec, Context(), profile, None)
    manifest = json.loads(
        next(
            (tmp_path / "exports/bad-checksum/test-job/manifests").glob("*.json")
        ).read_text()
    )
    assert manifest["artifacts"][0]["sha256"] == "0" * 64
