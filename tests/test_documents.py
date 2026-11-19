import io
from xml.etree import ElementTree
from config import RunSpec, FieldRule
from generation import generate_record
from document_formats import documents


def test_business_documents_are_readable_and_values_round_trip():
    s = RunSpec(
        rows=3,
        fields=[
            FieldRule(name="id", generator="record_id", primary_key=True, length=64),
            FieldRule(name="name", generator="constant", value="日本語=literal"),
            FieldRule(
                name="quantity",
                data_type="integer",
                generator="integer",
                minimum=1,
                maximum=20,
            ),
            FieldRule(
                name="amount",
                data_type="decimal",
                generator="constant",
                value="12.50",
                scale=2,
            ),
            FieldRule(
                name="at",
                data_type="datetime",
                generator="timestamp",
                start="2026-11-13T00:00:00+00:00",
            ),
        ],
    )
    s.files.formats = ["xlsx", "xml", "parquet", "avro", "pdf"]
    rows = [generate_record(s, i) for i in range(3)]
    outputs = {name: data for name, ext, data, mime in documents(s, rows)}
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(outputs["xlsx"]), read_only=True)
    values = list(wb.active.values)
    assert values[1][1] == "日本語=literal" and len(values) == 4
    assert len(ElementTree.fromstring(outputs["xml"])) == 3
    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(outputs["parquet"]))
    assert table.num_rows == 3 and str(table.column("amount")[0].as_py()) == "12.50"
    import fastavro

    records = list(fastavro.reader(io.BytesIO(outputs["avro"])))
    assert len(records) == 3 and str(records[0]["amount"]) == "12.50"
    assert outputs["pdf"].startswith(b"%PDF-")


def test_excel_defaults_to_literal_formula_text():
    s = RunSpec(fields=[FieldRule(name="formula", generator="constant", value="=1+1")])
    s.files.formats = ["xlsx"]
    data = next(documents(s, [generate_record(s, 0)]))[2]
    from openpyxl import load_workbook

    cell = load_workbook(io.BytesIO(data)).active["A2"]
    assert cell.data_type == "s" and cell.value == "=1+1"
