"""Synthetic-only export tests; no external endpoints or existing landing data."""

import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from config import RunSpec
from file_adapter import handle
from generation import GenerationError


class LocalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {"FILE_ALLOWED_ROOTS": str(self.root), "DATA_DIR": str(self.root)},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "subprocess.Popen",
        ):
            guard = patch(
                target, side_effect=AssertionError("Network/process forbidden")
            )
            guard.start()
            self.addCleanup(guard.stop)
        self.spec = RunSpec.model_validate(
            json.loads(
                (
                    Path(__file__).parents[1] / "examples/lakehouse-products-local.json"
                ).read_text()
            )
        )
        self.profile = SimpleNamespace(
            kind="local", prefix="", directory=str(self.root)
        )

    def run_export(self):
        ctx = SimpleNamespace(
            outputs=[], run_id="synthetic", check=Mock(), progress=Mock()
        )
        handle(self.spec, ctx, self.profile, None)
        return ctx

    @property
    def data(self):
        return self.root / "csv/erp_products__abc123def456_0001.csv"

    @property
    def marker(self):
        return Path(str(self.data) + ".ready")

    def test_pair_and_identical_retry(self):
        self.run_export()
        before = (self.data.stat().st_mtime_ns, self.marker.stat().st_mtime_ns)
        self.assertEqual(
            self.marker.read_text(),
            hashlib.sha256(self.data.read_bytes()).hexdigest() + "\n",
        )
        self.assertIn("product_id,product_name", self.data.read_text())
        ctx = self.run_export()
        ctx.progress.assert_called_once_with(processed=2, files=0, bytes_written=0)
        self.assertEqual(
            before, (self.data.stat().st_mtime_ns, self.marker.stat().st_mtime_ns)
        )

    def test_missing_marker_recovery(self):
        self.run_export()
        self.marker.unlink()
        before = self.data.stat().st_mtime_ns
        self.run_export()
        self.assertEqual(before, self.data.stat().st_mtime_ns)
        self.assertTrue(self.marker.is_file())

    def test_conflicts_do_not_overwrite(self):
        for target in ("payload", "marker"):
            with self.subTest(target=target):
                if self.data.exists():
                    self.data.unlink()
                if self.marker.exists():
                    self.marker.unlink()
                self.run_export()
                file = self.data if target == "payload" else self.marker
                file.write_bytes(b"conflict")
                before = (self.data.read_bytes(), self.marker.read_bytes())
                with self.assertRaises(GenerationError):
                    self.run_export()
                self.assertEqual(
                    before, (self.data.read_bytes(), self.marker.read_bytes())
                )

    def test_marker_without_payload_rejected(self):
        self.run_export()
        self.data.unlink()
        with self.assertRaises(GenerationError):
            self.run_export()
        self.assertFalse(self.data.exists())

    def test_nonlocal_rejected_before_destination(self):
        for kind in ("nfs", "smb", "ftp", "sftp", "s3"):
            self.profile.kind = kind
            with self.assertRaises(GenerationError):
                self.run_export()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_required_text_before_publication(self):
        for index in range(4):
            for value in (None, "", "   "):
                with self.subTest(field=index, value=value):
                    spec = self.spec.model_copy(deep=True)
                    self.spec.fields[index].generator = "constant"
                    self.spec.fields[index].value = value
                    with self.assertRaises(GenerationError):
                        self.run_export()
                    self.assertEqual(list(self.root.iterdir()), [])
                    self.spec = spec

    def test_reference_declared_types_and_decimal_precision(self):
        for index in range(6):
            spec = self.spec.model_copy(deep=True)
            self.spec.fields[index].data_type = "json"
            with self.assertRaises(GenerationError):
                self.run_export()
            self.spec = spec
        for attribute, value in (("precision", 19), ("scale", 4)):
            spec = self.spec.model_copy(deep=True)
            setattr(self.spec.fields[4], attribute, value)
            with self.assertRaises(GenerationError):
                self.run_export()
            self.spec = spec
        self.assertEqual(list(self.root.iterdir()), [])

    def test_decimal_range_before_publication(self):
        for value in ("0", "-1", "1000000000000000", None):
            self.spec.fields[4].value = value
            with self.assertRaises((GenerationError, ValueError)):
                self.run_export()
            self.assertEqual(list(self.root.iterdir()), [])

    def test_unsupported_spec(self):
        changes = [
            ("formats", ["json"]),
            ("path_template", "../outside"),
            ("compression", "gzip"),
            ("write_manifest", False),
        ]
        for field, value in changes:
            old = getattr(self.spec.files, field)
            setattr(self.spec.files, field, value)
            with self.assertRaises(GenerationError):
                self.run_export()
            setattr(self.spec.files, field, old)
        self.spec.faults.enabled = True
        with self.assertRaises(GenerationError):
            self.run_export()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_and_hardlink_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "csv").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_export()
        (self.root / "csv").unlink()
        self.run_export()
        os.link(self.data, outside / "crash-remnant")
        with self.assertRaises(GenerationError):
            self.run_export()

    def test_data_marker_symlinks_rejected(self):
        self.run_export()
        self.marker.unlink()
        self.marker.symlink_to(self.data)
        with self.assertRaises(OSError):
            self.run_export()
        self.marker.unlink()
        self.data.unlink()
        self.data.symlink_to(self.root / "missing")
        with self.assertRaises(OSError):
            self.run_export()

    def test_lock_symlink_rejected(self):
        (self.root / "csv").mkdir()
        (self.root / "csv/.lakehouse-export.lock").symlink_to(self.root / "missing")
        with self.assertRaises(OSError):
            self.run_export()
        self.assertFalse((self.root / "missing").exists())

    def test_concurrent_identical_retry(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            # 同じ出力を並行実行し、競合してもファイルが増えないことを確かめる。
            list(pool.map(lambda _: self.run_export(), range(2)))
        self.assertEqual(
            self.marker.read_text(),
            hashlib.sha256(self.data.read_bytes()).hexdigest() + "\n",
        )

    def test_marker_write_failure_recovers(self):
        import lakehouse_export

        original = lakehouse_export.publish

        def fail_marker(fd, name, data):
            if name.endswith(".ready"):
                raise OSError("synthetic interrupted marker")
            return original(fd, name, data)

        with (
            patch.object(lakehouse_export, "publish", side_effect=fail_marker),
            self.assertRaises(OSError),
        ):
            self.run_export()
        self.assertTrue(self.data.exists())
        self.assertFalse(self.marker.exists())
        self.run_export()
        self.assertTrue(self.marker.exists())

    def test_no_clobber_external_creation(self):
        import lakehouse_export

        original = os.link

        def race(src, dst, **kwargs):
            fd = os.open(
                dst,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=kwargs["dst_dir_fd"],
            )
            os.write(fd, b"other-publisher")
            os.close(fd)
            return original(src, dst, **kwargs)

        with (
            patch.object(lakehouse_export.os, "link", side_effect=race),
            self.assertRaises(FileExistsError),
        ):
            self.run_export()
        self.assertEqual(self.data.read_bytes(), b"other-publisher")
        self.assertFalse(self.marker.exists())

    def test_default_stays_v2(self):
        self.spec.files.completion_mode = "manifest-v2"
        self.spec.files.path_template = "native"
        self.profile.directory = str(self.root)
        self.run_export()
        self.assertEqual(len(list(self.root.rglob("*.ready.json"))), 1)
        self.assertEqual(len(list(self.root.rglob("*.csv.ready"))), 0)


if __name__ == "__main__":
    unittest.main()
