"""Interoperable business documents; generated strings stay literal in spreadsheets."""

import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape


def arrow_type(field):
    """宣言された型をArrow型へ変換し、固定小数の桁数とUTCの日時型を保つ。"""
    import pyarrow as pa

    return {
        "integer": pa.int64(),
        "number": pa.float64(),
        "decimal": pa.decimal128(field.precision, field.scale),
        "boolean": pa.bool_(),
        "datetime": pa.timestamp("us", tz="UTC"),
    }.get(field.data_type, pa.string())


def typed_rows(spec, records):
    """宣言の列順で固定小数・UTC日時・JSONを変換する。欠落した値はNoneのまま返す。"""
    result = []
    for row in records:
        out = {}
        for f in spec.fields:
            v = row.get(f.name)
            if v is not None:
                if f.data_type == "decimal":
                    v = Decimal(str(v))
                elif f.data_type == "datetime":
                    v = datetime.fromisoformat(v).astimezone(timezone.utc)
                elif f.data_type == "json":
                    v = json.dumps(v, ensure_ascii=False, allow_nan=False)
            out[f.name] = v
        result.append(out)
    return result


def documents(spec, records):
    """交換用文書のバイト列と形式を返す。通常の表計算文字列は数式として扱わない。"""
    options = spec.files
    formats = set(options.formats)
    names = [f.name for f in spec.fields]
    if "xml" in formats:
        root = Element(options.xml_root)
        for row in records:
            item = SubElement(root, options.xml_row)
            for name in names:
                cell = SubElement(item, name)
                v = row.get(name)
                if v is None:
                    cell.set("null", "true")
                else:
                    cell.text = (
                        json.dumps(v, ensure_ascii=False)
                        if isinstance(v, (dict, list, bool))
                        else str(v)
                    )
        yield (
            "xml",
            ".xml",
            tostring(root, encoding="utf-8", xml_declaration=True),
            "application/xml",
        )
    if "xlsx" in formats:
        from openpyxl import Workbook

        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(options.xlsx_sheet)
        from openpyxl.cell import WriteOnlyCell

        sheet.append(names)
        for row in records:
            cells = []
            for name in names:
                value = row.get(name)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                cell = WriteOnlyCell(sheet, value=value)
                if isinstance(value, str) and not options.xlsx_allow_formulas:
                    cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)
        output = io.BytesIO()
        workbook.save(output)
        yield (
            "xlsx",
            ".xlsx",
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if "parquet" in formats:
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = pa.schema(
            [
                pa.field(f.name, arrow_type(f), nullable=not f.primary_key)
                for f in spec.fields
            ]
        )
        table = pa.Table.from_pylist(typed_rows(spec, records), schema=schema)
        output = io.BytesIO()
        pq.write_table(
            table,
            output,
            compression=None
            if options.parquet_compression == "none"
            else options.parquet_compression,
        )
        yield "parquet", ".parquet", output.getvalue(), "application/vnd.apache.parquet"
    if "avro" in formats:
        import fastavro

        fields = []
        for f in spec.fields:
            dtype = {
                "integer": "long",
                "number": "double",
                "boolean": "boolean",
                "datetime": {"type": "long", "logicalType": "timestamp-micros"},
                "decimal": {
                    "type": "bytes",
                    "logicalType": "decimal",
                    "precision": f.precision,
                    "scale": f.scale,
                },
            }.get(f.data_type, "string")
            entry = {
                "name": f.name,
                "type": dtype if f.primary_key else ["null", dtype],
            }
            if not f.primary_key:
                entry["default"] = None
            fields.append(entry)
        schema = {
            "type": "record",
            "name": options.avro_record_name,
            "namespace": options.avro_namespace,
            "fields": fields,
        }
        output = io.BytesIO()
        fastavro.writer(
            output, schema, typed_rows(spec, records), codec=options.avro_codec
        )
        yield "avro", ".avro", output.getvalue(), "application/avro"
    if "pdf" in formats:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
        )
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors

        font = "HeiseiKakuGo-W5"
        if font not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(font))
        output = io.BytesIO()
        page = landscape(A4) if options.pdf_landscape else A4
        doc = SimpleDocTemplate(
            output,
            pagesize=page,
            leftMargin=24,
            rightMargin=24,
            topMargin=24,
            bottomMargin=24,
        )
        style = ParagraphStyle(
            "data",
            fontName=font,
            fontSize=options.pdf_font_size,
            leading=options.pdf_font_size + 3,
            wordWrap="CJK",
        )
        columns = options.pdf_columns or names
        if set(columns) - set(names):
            raise ValueError("PDF列に未定義の項目があります")
        cells = [[Paragraph(escape(name), style) for name in columns]]
        for row in records:
            cells.append(
                [
                    Paragraph(
                        escape(
                            json.dumps(row.get(k), ensure_ascii=False)
                            if isinstance(row.get(k), (dict, list))
                            else str(row.get(k) if row.get(k) is not None else "NULL")
                        ),
                        style,
                    )
                    for k in columns
                ]
            )
        table = Table(
            cells,
            colWidths=[(page[0] - 48) / len(columns)] * len(columns),
            repeatRows=1,
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d6e5e9")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        doc.build([Paragraph(escape(spec.name), style), Spacer(1, 12), table])
        yield "pdf", ".pdf", output.getvalue(), "application/pdf"
