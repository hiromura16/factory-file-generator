# モジュール読解: 完了通知モードの説明が公開スキーマと実ハンドラーで欠けないことを確認する。
"""Completion-mode help must be available to schema and API callers."""

import ast
import json
import unittest
from pathlib import Path

from helptext import annotated_schemas


# 読解 CompletionModeHelpTests: 設定モデルと配布スキーマを接続先なしで比較する回帰試験。
class CompletionModeHelpTests(unittest.TestCase):
    # 読解 CompletionModeHelpTests.test_all_schema_kinds_have_completion_help: 全種類のスキーマで既定値・選択肢・説明が取得できることを確認する。
    def test_all_schema_kinds_have_completion_help(self):
        for kind in (None, "files", "sql", "stream"):
            with self.subTest(kind=kind):
                prop = annotated_schemas(kind)["run"]["$defs"]["FileOptions"][
                    "properties"
                ]["completion_mode"]
                self.assertEqual(prop["default"], "manifest-v2")
                self.assertEqual(
                    prop["enum"], ["manifest-v2", "lakehouse-products-local"]
                )
                self.assertEqual(prop["title"], "完了通知形式")
                self.assertIn("隣接SHA256", prop["description"])
                self.assertIn("下流取込の完了は保証しません", prop["description"])

    # 読解 CompletionModeHelpTests.test_published_property_matches_annotation: 配布済みJSONの対象項目が、現在の注釈生成結果と一致することを確認する。
    def test_published_property_matches_annotation(self):
        root = Path(__file__).resolve().parents[1]
        published = json.loads((root / "contracts/preset.schema.json").read_text())
        actual = annotated_schemas()["run"]
        self.assertEqual(
            published["$defs"]["FileOptions"]["properties"]["completion_mode"],
            actual["$defs"]["FileOptions"]["properties"]["completion_mode"],
        )

    # 読解 CompletionModeHelpTests.test_actual_schema_endpoint_body_without_startup: 固定ソースからルートの関数本体だけを取り出し、起動時の保存領域作成なしで応答を確認する。
    def test_actual_schema_endpoint_body_without_startup(self):
        # Execute the real read-only endpoint body, not store/runtime startup.
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "factorylib.py").read_text())
        create = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "create_app"
        )
        endpoint = next(
            n
            for n in create.body
            if isinstance(n, ast.FunctionDef) and n.name == "schemas"
        )
        endpoint.decorator_list = []
        namespace = {"kind": "files"}
        exec(  # noqa: S102 - fixed repository endpoint, no user input
            compile(
                ast.Module(body=[endpoint], type_ignores=[]),
                "<schema endpoint>",
                "exec",
            ),
            namespace,
        )
        self.assertEqual(namespace["schemas"](), annotated_schemas("files"))


if __name__ == "__main__":
    unittest.main()
