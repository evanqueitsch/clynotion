"""Offer-letter merge rules ported from the fill_offer prototype, plus licensed rate."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lxml import etree

from app.hiring.fill import (
    FIELDS_PATH,
    TEMPLATE_PATH,
    OfferFillError,
    fill_docx,
    load_fields_spec,
    load_values,
    render_docx,
)
from app.hiring.pdf import docx_to_pdf, soffice_available

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

FULL = {
    "employment_type": "full-time",
    "offer_date": "12/01/2026",
    "candidate_full_name": "Jordan Example",
    "candidate_credentials": "LPC",
    "candidate_first_name": "Jordan",
    "hourly_rate": "$45.00",
    "caseload_range": "25-30",
    "qsehra_monthly_amount": "$250",
    "qsehra_wait_days": "60",
    "start_date": "01/05/2027",
    "response_deadline": "12/08/2026",
}

PART = {
    "employment_type": "part-time",
    "offer_date": "12/02/2026",
    "candidate_full_name": "Riley Sample",
    "candidate_credentials": "LCSW",
    "candidate_first_name": "Riley",
    "hourly_rate": "$42.50",
    "caseload_range": "12-18",
    "start_date": "01/12/2027",
    "response_deadline": "12/09/2026",
    "supervisor_name": "Alex Supervisor",
}


def _paras(blob: bytes) -> list[str]:
    root = etree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read("word/document.xml"))
    found = []
    for paragraph in root.iter(W + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(W + "t")).strip()
        if text:
            found.append(text)
    return found


def _text(blob: bytes) -> str:
    return "\n".join(_paras(blob))


def test_full_time_fill_keeps_benefits_and_signature_media() -> None:
    _values, blob = render_docx(FULL)
    text = _text(blob)
    assert "Jordan Example, LPC" in text
    assert "Full-Time Outpatient Clinician" in text
    assert "25–30" in text
    assert "SIMPLE IRA" in text
    assert "Health coverage." in text
    assert "$250" in text
    assert "{{" not in text
    template = zipfile.ZipFile(TEMPLATE_PATH)
    filled = zipfile.ZipFile(io.BytesIO(blob))
    for name in template.namelist():
        if name.startswith("word/media/"):
            assert template.read(name) == filled.read(name)


def test_part_time_removes_both_paragraphs_and_rejects_qsehra() -> None:
    _values, blob = render_docx(PART)
    text = _text(blob)
    assert "Part-Time Outpatient Clinician" in text
    assert "part-time role" in text
    assert "SIMPLE IRA" not in text
    assert "Health coverage." not in text
    assert "QSEHRA" not in text
    assert "{{" not in text
    for field in ("qsehra_monthly_amount", "qsehra_wait_days"):
        with pytest.raises(OfferFillError, match="only for full-time"):
            load_values({**PART, field: "$250" if "amount" in field else "60"}, load_fields_spec())


def test_part_time_accepts_blank_qsehra_and_full_time_requires_each() -> None:
    load_values({**PART, "qsehra_monthly_amount": "", "qsehra_wait_days": ""}, load_fields_spec())
    for field in ("qsehra_monthly_amount", "qsehra_wait_days"):
        body = dict(FULL)
        body.pop(field, None)
        with pytest.raises(OfferFillError, match="is required"):
            load_values(body, load_fields_spec())


def test_unknown_derived_braces_and_employment_type() -> None:
    spec = load_fields_spec()
    with pytest.raises(OfferFillError, match="does not use"):
        load_values({**FULL, "nickname": "Jo"}, spec)
    for derived in ("schedule_paragraph", "benefit_time_off", "benefit_license_ceu", "licensed_rate_clause"):
        with pytest.raises(OfferFillError, match="filled in automatically"):
            load_values({**FULL, derived: "nope"}, spec)
    with pytest.raises(OfferFillError, match="braces"):
        load_values({**FULL, "candidate_first_name": "Jo{{x}}"}, spec)
    with pytest.raises(OfferFillError, match="Employment type"):
        load_values({**FULL, "employment_type": "Full-Time"}, spec)
    with pytest.raises(OfferFillError, match="Employment type"):
        load_values({**FULL, "employment_type": "contract"}, spec)


def test_regex_formats_and_caseload_dash() -> None:
    spec = load_fields_spec()
    bad = {
        "hourly_rate": "forty",
        "licensed_rate": "nope",
        "qsehra_monthly_amount": "lots",
        "qsehra_wait_days": "soon",
        "offer_date": "tomorrow",
    }
    for field, value in bad.items():
        with pytest.raises(OfferFillError) as caught:
            load_values({**FULL, field: value}, spec)
        assert caught.value.field == field
        assert field not in str(caught.value)
    values = load_values({**FULL, "caseload_range": "25 - 30"}, spec)
    assert values["caseload_range"] == "25–30"
    words = load_values({**FULL, "caseload_range": "25 to 30"}, spec)
    assert words["caseload_range"] == "25–30"
    endash = load_values({**FULL, "caseload_range": "25–30"}, spec)
    assert endash["caseload_range"] == "25–30"


def test_plain_amounts_normalize_to_letter_format() -> None:
    spec = load_fields_spec()
    values = load_values(
        {
            **FULL,
            "hourly_rate": "40",
            "licensed_rate": "40.5",
            "qsehra_monthly_amount": "100",
            "qsehra_wait_days": "060 days",
            "caseload_range": "25-30",
            "offer_date": "2026-12-01",
            "start_date": "1/5/2027",
        },
        spec,
    )
    assert values["hourly_rate"] == "$40.00"
    assert values["licensed_rate"] == "$40.50"
    assert values["qsehra_monthly_amount"] == "$100"
    assert values["qsehra_wait_days"] == "60"
    assert values["caseload_range"] == "25–30"
    assert values["offer_date"] == "12/01/2026"
    assert values["start_date"] == "01/05/2027"
    symbols = load_values(
        {
            **FULL,
            "hourly_rate": "$40",
            "licensed_rate": "$1,250.00",
            "qsehra_monthly_amount": "$250.50",
            "caseload_range": "8 — 12",
        },
        spec,
    )
    assert symbols["hourly_rate"] == "$40.00"
    assert symbols["licensed_rate"] == "$1,250.00"
    assert symbols["qsehra_monthly_amount"] == "$250.50"
    assert symbols["caseload_range"] == "8–12"
    with pytest.raises(OfferFillError, match="Hourly rate must be a dollar amount.") as caught:
        load_values({**FULL, "hourly_rate": "40 dollars"}, spec)
    assert caught.value.field == "hourly_rate"


def test_credentials_comma_position_and_supervisor_defaults() -> None:
    spec = load_fields_spec()
    blank, blob = render_docx({**FULL, "candidate_credentials": ""})
    assert blank["candidate_credentials"] == ""
    assert "Jordan Example," not in _text(blob)
    assert "Jordan Example" in _text(blob)
    kept = _text(render_docx(FULL)[1])
    assert "Jordan Example, LPC" in kept
    values = load_values(dict(FULL), spec)
    assert values["position_title"] == "Full-Time Outpatient Clinician"
    part = load_values(dict(PART), spec)
    assert part["position_title"] == "Part-Time Outpatient Clinician"
    override = load_values({**FULL, "position_title": "Staff Clinician"}, spec)
    assert override["position_title"] == "Staff Clinician"
    default_name = next(field["default"] for field in spec["fields"] if field["name"] == "supervisor_name")
    defaulted = load_values({key: value for key, value in FULL.items() if key != "supervisor_name"}, spec)
    assert defaulted["supervisor_title"] == "Director"
    assert defaulted["supervisor_name"] == default_name


def test_licensed_rate_clause_is_optional() -> None:
    without = _text(render_docx(FULL)[1])
    assert "pre-licensure" not in without
    assert "licensed rate" not in without.casefold() or "License, certification" in without
    _values, blob = render_docx({**FULL, "licensed_rate": "$55.00"})
    text = _text(blob)
    assert "The pre-licensure rate of $45.00 per hour applies until licensure" in text
    assert "the licensed rate of $55.00 per hour applies from then on" in text
    assert "{{" not in text
    part = _text(render_docx({**PART, "licensed_rate": "$50.00"})[1])
    assert "pre-licensure rate of $42.50" in part
    assert "licensed rate of $50.00" in part


def test_real_dates_reject_tbd_and_asap() -> None:
    with pytest.raises(OfferFillError, match="Start date"):
        load_values({**FULL, "start_date": "TBD"}, load_fields_spec())
    with pytest.raises(OfferFillError, match="Response deadline"):
        load_values({**FULL, "response_deadline": "ASAP"}, load_fields_spec())
    with pytest.raises(OfferFillError, match="must be a real date"):
        load_values({**FULL, "start_date": "02/31/2027"}, load_fields_spec())


def test_placeholder_split_across_runs(tmp_path: Path) -> None:
    paragraphs = (
        "<w:p>"
        "<w:r><w:t>Hello {{candidate_</w:t></w:r>"
        "<w:r><w:t>first_name}}</w:t></w:r>"
        "</w:p>"
    )
    blob = _package(tmp_path / "split.docx", paragraphs)
    values = load_values(FULL, load_fields_spec())
    filled = fill_docx(values, template=blob)
    assert "Hello Jordan" in _text(filled)
    assert "{{" not in _text(filled)


def test_leftover_placeholder_blocks_output(tmp_path: Path) -> None:
    document = Path(TEMPLATE_PATH)
    raw = zipfile.ZipFile(document).read("word/document.xml")
    broken = raw.replace(b"{{hourly_rate}}", b"{{not_a_field}}")
    path = tmp_path / "broken.docx"
    _rewrite(document, path, {"word/document.xml": broken})
    with pytest.raises(OfferFillError, match="unfilled placeholder"):
        fill_docx(load_values(FULL, load_fields_spec()), template=path)


def test_remove_prefix_must_match_one_paragraph(tmp_path: Path) -> None:
    spec = load_fields_spec()
    values = load_values(PART, spec)
    none_doc = _package(tmp_path / "none.docx", _body("Nothing to remove here."))
    with pytest.raises(OfferFillError, match="found 0"):
        fill_docx(values, template=none_doc)
    twice = _body("Participation in the Company's SIMPLE IRA plan") + _body(
        "Participation in the Company's SIMPLE IRA plan"
    )
    two = _package(tmp_path / "two.docx", twice)
    with pytest.raises(OfferFillError, match="found 2"):
        fill_docx(values, template=two)


def test_docprops_title_and_byte_stable_except_modified() -> None:
    stamp = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    values = load_values(FULL, load_fields_spec())
    first = fill_docx(values, modified=stamp)
    second = fill_docx(values, modified=stamp)
    assert first == second
    core = zipfile.ZipFile(io.BytesIO(first)).read("docProps/core.xml")
    assert b"Offer of Employment" in core
    assert b"Full-Time Outpatient Clinician" in core


def test_pdf_renders_without_placeholders() -> None:
    if not soffice_available():
        pytest.skip("LibreOffice is not installed")
    _values, docx = render_docx({**FULL, "licensed_rate": "$55.00", "supervisor_name": "Alex Supervisor"})
    pdf = docx_to_pdf(docx)
    assert pdf.startswith(b"%PDF")
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(pdf)).pages)
    assert "{{" not in text
    assert "Jordan Example" in text
    assert "pre-licensure rate of $45.00" in text
    assert "licensed rate of $55.00" in text
    assert "Alex Supervisor" in text


def _body(text: str) -> str:
    return (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def _package(path: Path, paragraphs: str) -> Path:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path


def _rewrite(source: Path, dest: Path, replacements: dict[str, bytes]) -> None:
    incoming = zipfile.ZipFile(source)
    with zipfile.ZipFile(dest, "w") as outgoing:
        for item in incoming.infolist():
            data = replacements.get(item.filename, incoming.read(item.filename))
            outgoing.writestr(item, data)


def test_fields_file_is_the_shipped_asset() -> None:
    assert FIELDS_PATH.is_file()
    assert TEMPLATE_PATH.is_file()
