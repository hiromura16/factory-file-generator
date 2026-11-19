"""Completion-mode help must be available to schema and API callers."""

import ast
import json
import unittest
from pathlib import Path

from helptext import annotated_schemas


class CompletionModeHelpTests(unittest.TestCase):
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

    def test_published_property_matches_annotation(self):
        root = Path(__file__).resolve().parents[1]
        published = json.loads((root / "contracts/preset.schema.json").read_text())
        actual = annotated_schemas()["run"]
        self.assertEqual(
            published["$defs"]["FileOptions"]["properties"]["completion_mode"],
            actual["$defs"]["FileOptions"]["properties"]["completion_mode"],
        )

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
