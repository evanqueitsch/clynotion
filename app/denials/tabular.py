"""CSV and XLSX readers for de-identified claim exports. No third-party parser."""

from __future__ import annotations

import csv
import io
import zipfile
from xml.etree import ElementTree as ET

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("That file is not a readable CSV.")


def read_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = _decode_csv(data)
    reader = csv.reader(io.StringIO(text))
    rows = [list(row) for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        raise ValueError("That file has no rows.")
    headers = [str(cell).strip() for cell in rows[0]]
    body = [[str(cell).strip() for cell in row] for row in rows[1:]]
    return headers, body


def _column_index(ref: str) -> int:
    index = 0
    for char in ref:
        if not char.isalpha():
            break
        index = index * 26 + ord(char.upper()) - 64
    return index


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{_MAIN}t")).strip()
    value = cell.find("m:v", _NS)
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            return shared[int(value.text)].strip()
        except (IndexError, ValueError):
            return ""
    return value.text.strip()


def read_xlsx(data: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError("That file is not a readable spreadsheet.") from exc
    with archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", _NS):
                shared.append("".join(node.text or "" for node in item.iter(f"{_MAIN}t")))
        sheet = "xl/worksheets/sheet1.xml"
        if sheet not in names:
            raise ValueError("That spreadsheet has no first sheet.")
        root = ET.fromstring(archive.read(sheet))
        table: list[list[str]] = []
        for row in root.findall("m:sheetData/m:row", _NS):
            values: list[str] = []
            for cell in row.findall("m:c", _NS):
                column = _column_index(cell.attrib.get("r", "A"))
                while len(values) < max(column - 1, 0):
                    values.append("")
                values.append(_cell_text(cell, shared))
            if any(item.strip() for item in values):
                table.append(values)
    if not table:
        raise ValueError("That file has no rows.")
    width = max(len(row) for row in table)
    padded = [row + [""] * (width - len(row)) for row in table]
    headers = [cell.strip() for cell in padded[0]]
    body = [[cell.strip() for cell in row] for row in padded[1:]]
    return headers, body


def read_table(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return read_xlsx(data)
    if name.endswith(".csv") or name.endswith(".txt") or not name:
        return read_csv(data)
    raise ValueError("Upload a CSV or XLSX file.")
