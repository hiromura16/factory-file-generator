# モジュール読解: 固定製品CSVモードを一時領域で実行し、出力の整合性と不正条件下での非上書きを検証する。
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


# 読解 LocalExportTests: 製品CSVとSHA256完了印の新規発行・再試行・異常時の境界を検証する。
class LocalExportTests(unittest.TestCase):
    # 読解 LocalExportTests.setUp: 既知の出力ルートと製品プリセットをテストごとに用意し、終了時の片付けを登録する。
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

    # 読解 LocalExportTests.run_export: 固定の合成プロファイルでアダプターを呼び、観測用の実行コンテキストを返す。
    def run_export(self):
        ctx = SimpleNamespace(
            outputs=[], run_id="synthetic", check=Mock(), progress=Mock()
        )
        handle(self.spec, ctx, self.profile, None)
        return ctx

    # 読解 LocalExportTests.data: 検査する固定CSVの一時パスを返す。
    @property
    def data(self):
        return self.root / "csv/erp_products__abc123def456_0001.csv"

    # 読解 LocalExportTests.marker: 対象のCSVに対応する隣接完了印のパスを返す。
    @property
    def marker(self):
        return Path(str(self.data) + ".ready")

    # 読解 LocalExportTests.test_pair_and_identical_retry: CSV・完了印の内容と新規実績を確認し、同一再試行が書込なしになることを確かめる。
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

    # 読解 LocalExportTests.test_missing_marker_recovery: 同一CSVだけが残る状態から完了印だけ補えることを確かめる。
    def test_missing_marker_recovery(self):
        self.run_export()
        self.marker.unlink()
        before = self.data.stat().st_mtime_ns
        self.run_export()
        self.assertEqual(before, self.data.stat().st_mtime_ns)
        self.assertTrue(self.marker.is_file())

    # 読解 LocalExportTests.test_conflicts_do_not_overwrite: 既存CSVまたは完了印の内容相違を拒否し、既存内容を保持することを確かめる。
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

    # 読解 LocalExportTests.test_marker_without_payload_rejected: CSVがない状態の孤立した完了印を再利用しないことを確かめる。
    def test_marker_without_payload_rejected(self):
        self.run_export()
        self.data.unlink()
        with self.assertRaises(GenerationError):
            self.run_export()
        self.assertFalse(self.data.exists())

    # 読解 LocalExportTests.test_nonlocal_rejected_before_destination: リモート形式の指定が共有クライアントの構築前に拒否されることを確かめる。
    def test_nonlocal_rejected_before_destination(self):
        for kind in ("nfs", "smb", "ftp", "sftp", "s3"):
            self.profile.kind = kind
            with self.assertRaises(GenerationError):
                self.run_export()
        self.assertEqual(list(self.root.iterdir()), [])

    # 読解 LocalExportTests.test_missing_required_text_before_publication: 必須の文字列が欠ける行がCSVと印を一切発行しないことを確かめる。
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

    # 読解 LocalExportTests.test_reference_declared_types_and_decimal_precision: 固定列の型と小数の精度・スケールを変更した設定を拒否することを確かめる。
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

    # 読解 LocalExportTests.test_decimal_range_before_publication: 許容範囲外の標準サイクル値をファイル発行前に拒否することを確かめる。
    def test_decimal_range_before_publication(self):
        for value in ("0", "-1", "1000000000000000", None):
            self.spec.fields[4].value = value
            with self.assertRaises((GenerationError, ValueError)):
                self.run_export()
            self.assertEqual(list(self.root.iterdir()), [])

    # 読解 LocalExportTests.test_unsupported_spec: 固定モードの対応外になるオプションの組合せを拒否することを確かめる。
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

    # 読解 LocalExportTests.test_symlink_and_hardlink_rejected: リンクで差し替えられた出力階層や既存成果物を信用しないことを確かめる。
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

    # 読解 LocalExportTests.test_data_marker_symlinks_rejected: CSVと完了印の最終名に置かれたシンボリックリンクを受け入れないことを確かめる。
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

    # 読解 LocalExportTests.test_lock_symlink_rejected: ロックのパスがシンボリックリンクの場合に処理しないことを確かめる。
    def test_lock_symlink_rejected(self):
        (self.root / "csv").mkdir()
        (self.root / "csv/.lakehouse-export.lock").symlink_to(self.root / "missing")
        with self.assertRaises(OSError):
            self.run_export()
        self.assertFalse((self.root / "missing").exists())

    # 読解 LocalExportTests.test_concurrent_identical_retry: 同じ出力の同時実行で二重発行が起きないことを確かめる。
    def test_concurrent_identical_retry(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            # コールバック: 並列mapの番号を使わず同じ製品出力を呼び、同一内容の競合時に追加書込みを重ねないことを試験する。
            list(pool.map(lambda _: self.run_export(), range(2)))
        self.assertEqual(
            self.marker.read_text(),
            hashlib.sha256(self.data.read_bytes()).hexdigest() + "\n",
        )

    # 読解 LocalExportTests.test_marker_write_failure_recovers: 完了印の書込みだけ失敗した後、同じ内容の再実行で欠けた印だけ補えることを確かめる。
    def test_marker_write_failure_recovers(self):
        import lakehouse_export

        original = lakehouse_export.publish

        # 読解 LocalExportTests.test_marker_write_failure_recovers.fail_marker: 完了印の発行だけ合成エラーにして、先にCSVが確定した失敗状態を作る。
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

    # 読解 LocalExportTests.test_no_clobber_external_creation: 最終名を別の操作が先に作る競合で、既存ファイルが置換されないことを確かめる。
    def test_no_clobber_external_creation(self):
        import lakehouse_export

        original = os.link

        # 読解 LocalExportTests.test_no_clobber_external_creation.race: リンク公開の直前に対象名へ別内容を書き、公開処理と協調しない競合を模擬する。
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

    # 読解 LocalExportTests.test_default_stays_v2: 特別モード未指定なら従来のv2マニフェスト出力となることを確かめる。
    def test_default_stays_v2(self):
        self.spec.files.completion_mode = "manifest-v2"
        self.spec.files.path_template = "native"
        self.profile.directory = str(self.root)
        self.run_export()
        self.assertEqual(len(list(self.root.rglob("*.ready.json"))), 1)
        self.assertEqual(len(list(self.root.rglob("*.csv.ready"))), 0)


if __name__ == "__main__":
    unittest.main()
