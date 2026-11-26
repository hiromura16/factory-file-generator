"""Add business document presets without replacing the existing normal/fault library."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import RunSpec, FieldRule  # noqa: E402
from scripts.preset_catalog import write_catalog  # noqa: E402


def build(directory: Path = ROOT / "presets") -> None:
    """既存シナリオを残し、文書形式の例を配布プリセットに追加する。"""
    directory.mkdir(parents=True, exist_ok=True)
    variants = [
        (
            "49-xlsx-basic", "Excel / 全列の表", ["xlsx"],
            "元の列をすべて含むExcelのワークシートを作ります。",
        ),
        (
            "50-xlsx-japanese", "Excel / 日本語とNULL", ["xlsx"],
            "日本語の稼働状態と欠測値を「設備データ」シートに出力します。",
        ),
        (
            "51-xml-records", "XML / UTF-8レコード", ["xml"],
            "recordsルートの下にrecord要素を並べたUTF-8のXMLを作ります。",
        ),
        (
            "52-xml-custom", "XML / 要素名を変更", ["xml"],
            "XMLのルート名をmeasurements、行要素名をsampleに変えて出力します。",
        ),
        (
            "53-parquet-snappy", "Parquet / Snappy", ["parquet"],
            "項目定義で型を固定し、Snappy圧縮を使ったParquetを作ります。",
        ),
        (
            "54-parquet-zstd", "Parquet / Zstandard", ["parquet"],
            "項目定義で型を固定し、Zstandard圧縮を使ったParquetを作ります。",
        ),
        (
            "55-avro-deflate", "Avro / スキーマ付き圧縮", ["avro"],
            "項目定義からスキーマを作り、deflate圧縮を使ったAvroを出力します。",
        ),
        (
            "56-avro-uncompressed", "Avro / 圧縮なし", ["avro"],
            "項目定義からスキーマを作り、圧縮をかけずにAvroを出力します。",
        ),
        (
            "57-pdf-landscape", "PDF / 横向きレポート", ["pdf"],
            "元の表を横向きのPDFに出力します。",
        ),
        (
            "58-pdf-selected", "PDF / 列指定・縦向き", ["pdf"],
            "設備ID・測定値・状態の3列を縦向きのPDFに出力します。",
        ),
        (
            "59-business-bundle", "業務文書 / 5形式を同時生成",
            ["xlsx", "xml", "parquet", "avro", "pdf"],
            "同じレコードをExcel、XML、Parquet、Avro、PDFの5形式に出力します。",
        ),
        (
            "60-decimal-documents", "金額・数量 / 型付き文書",
            ["xlsx", "parquet", "avro"],
            "数量と小数2桁の単価・金額をExcel、Parquet、Avroに出力します。",
        ),
        (
            "61-report-and-source", "CSV・Excel・PDF / 同じデータ",
            ["csv", "xlsx", "pdf"],
            "同じレコードからCSVの元データ、編集用のExcel、閲覧用のPDFを作ります。",
        ),
        (
            "62-json-and-xml", "JSON・XML / 構造の比較", ["json", "xml"],
            "同じレコードをJSONとXMLへ出力して構造を比較できます。",
        ),
    ]
    for identity, name, formats, content in variants:
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
            f"【想定】\n{name}の出力設定を確認する例です。\n\n"
            f"【生成内容】\n{content}同じバッチの全ファイルとSHA256を完了マニフェストに記録します。\n\n"
            "【確認事項】\n文字コード、列順、NULL、小数・日時の型、読み戻した件数を確認してください。"
            "Excelは既定で数式を実行せず文字列として保存します。"
            "ParquetとAvroは項目定義から型を固定し、PDFはページをまたぐ表にも対応します。\n\n"
            "【実行前】\n保存先プロファイルと出力容量を確認してください。"
            "形式ごとの詳細条件はファイル設定で変更できます。"
        )
        s = RunSpec.model_validate(s.model_dump())
        (directory / (identity + ".json")).write_text(
            s.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    write_catalog(directory)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "presets")
    build(parser.parse_args().output)
