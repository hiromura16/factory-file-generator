# モジュール読解: 文書形式の利用例に使う配布プリセットを組み立てる。
"""Add business document presets without replacing the existing normal/fault library."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import RunSpec, FieldRule


# 読解 build: 文書出力の代表シナリオを設定モデルで組み立て、所定の配布ファイルへ書き出す。
def build():
    root = Path(__file__).resolve().parent.parent / "presets"
    variants = [
        ("49-xlsx-basic", "Excel / 全列の表", ["xlsx"]),
        ("50-xlsx-japanese", "Excel / 日本語とNULL", ["xlsx"]),
        ("51-xml-records", "XML / UTF-8レコード", ["xml"]),
        ("52-xml-custom", "XML / 要素名を変更", ["xml"]),
        ("53-parquet-snappy", "Parquet / Snappy", ["parquet"]),
        ("54-parquet-zstd", "Parquet / Zstandard", ["parquet"]),
        ("55-avro-deflate", "Avro / スキーマ付き圧縮", ["avro"]),
        ("56-avro-uncompressed", "Avro / 圧縮なし", ["avro"]),
        ("57-pdf-landscape", "PDF / 横向きレポート", ["pdf"]),
        ("58-pdf-selected", "PDF / 列指定・縦向き", ["pdf"]),
        (
            "59-business-bundle",
            "業務文書 / 5形式を同時生成",
            ["xlsx", "xml", "parquet", "avro", "pdf"],
        ),
        (
            "60-decimal-documents",
            "金額・数量 / 型付き文書",
            ["xlsx", "parquet", "avro"],
        ),
        (
            "61-report-and-source",
            "CSV・Excel・PDF / 同じデータ",
            ["csv", "xlsx", "pdf"],
        ),
        ("62-json-and-xml", "JSON・XML / 構造の比較", ["json", "xml"]),
    ]
    for identity, name, formats in variants:
        s = RunSpec(name=name, rows=20, target_rate=0, run_key=identity)
        s.files.formats = formats
        s.files.records_per_file = 20
        if identity.startswith("50-"):
            s.parameters["statuses"] = ["正常", "点検中", "停止"]
            s.fields[5].null_probability = 0.25
            s.files.xlsx_sheet = "設備データ"
        if identity.startswith("52-"):
            s.files.xml_root = "measurements"
            s.files.xml_row = "sample"
        if identity.startswith("54-"):
            s.files.parquet_compression = "zstd"
        if identity.startswith("56-"):
            s.files.avro_codec = "null"
        if identity.startswith("58-"):
            s.files.pdf_columns = ["entity_id", "value", "status"]
            s.files.pdf_landscape = False
        if identity.startswith("60-"):
            s.fields = [
                FieldRule(
                    name="id", generator="record_id", primary_key=True, length=64
                ),
                FieldRule(
                    name="quantity",
                    data_type="integer",
                    generator="integer",
                    minimum=1,
                    maximum=50,
                ),
                FieldRule(
                    name="unit_price",
                    data_type="decimal",
                    generator="choice",
                    choices=["10.00", "12.50", "99.99"],
                    scale=2,
                ),
                FieldRule(
                    name="amount",
                    data_type="decimal",
                    generator="product",
                    sources=["quantity", "unit_price"],
                    precision=24,
                    scale=2,
                ),
            ]
        s.description = (
            "【想定】\n業務連携・検査レポート・型付き分析データとして"
            + name
            + "を受け渡すケース。\n\n【生成内容】\n20レコードを"
            + ", ".join(formats)
            + "形式で出力します。同じバッチの全ファイルとSHA256を完了マニフェストへ記録。\n\n【確認事項】\n文字コード、列順、NULL、小数・日時の型、読み戻した件数を確認してください。Excelは既定で数式を実行せず文字列として保存。Parquet/Avroは項目定義から型を固定し、PDFは日本語の表を複数ページで出力します。\n\n【実行前】\n保存先プロファイルと出力容量を確認してください。形式ごとの詳細条件はファイル設定で編集できます。"
        )
        s = RunSpec.model_validate(s.model_dump())
        (root / (identity + ".json")).write_text(s.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    build()
