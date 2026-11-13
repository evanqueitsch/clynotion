"""Reject a file that still looks like it contains claim PHI.

Allowed columns are payer, procedure, amounts, status, and pseudonymous keys.
Remark text is kept only after emails, phone numbers, SSNs, dates, and
person-name patterns are removed. This module does not log cell values.
"""

from __future__ import annotations

import re

_DEID_PREFIX = "This file must be de-identified first."
_DEID_TAIL = (
    " Remove patient names, dates of birth, member or subscriber IDs, SSNs, "
    "addresses, phone numbers, and email addresses, then upload again."
)

_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_BARE_SSN = re.compile(r"^\d{9}$")
_MEMBER_ID = re.compile(r"^[A-Z]{0,3}\d{8,}$", re.I)
_STREET = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'\- ]{1,40}\s+"
    r"(street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl)\b",
    re.I,
)
_FULL_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,2}-\d{1,2}-\d{4}\b")
_PERSON = re.compile(r"^[A-Z][a-z]+, [A-Z][a-z]+$|^[A-Z][a-z]+ [A-Z][a-z]+$")

_HEADER_PHRASES: tuple[tuple[str, str], ...] = (
    ("date of birth", "a date of birth"),
    ("birth date", "a date of birth"),
    ("birthdate", "a date of birth"),
    ("social security", "an SSN"),
    ("member id", "a member or subscriber ID"),
    ("subscriber id", "a member or subscriber ID"),
    ("subscriber number", "a member or subscriber ID"),
    ("insurance id", "a member or subscriber ID"),
    ("member number", "a member or subscriber ID"),
    ("policy number", "a member or subscriber ID"),
    ("patient name", "a patient name"),
    ("client name", "a patient name"),
    ("subscriber name", "a patient name"),
    ("insured name", "a patient name"),
    ("first name", "a patient name"),
    ("last name", "a patient name"),
    ("full name", "a patient name"),
    ("guarantor", "a patient name"),
    ("e mail", "an email address"),
    ("email", "an email address"),
    ("telephone", "a phone number"),
    ("mobile", "a phone number"),
    ("phone", "a phone number"),
    ("address", "an address"),
    ("street", "an address"),
    ("zip code", "an address"),
    ("postal", "an address"),
)
_HEADER_EXACT = {
    "patient": "a patient name",
    "client": "a patient name",
    "subscriber": "a member or subscriber ID",
    "insured": "a patient name",
    "member": "a member or subscriber ID",
    "dob": "a date of birth",
    "ssn": "an SSN",
    "city": "an address",
    "zip": "an address",
    "state": "an address",
}
_PAYER_HEADERS = {
    "payer",
    "payer name",
    "insurance",
    "insurance company",
    "insurance plan",
    "plan",
    "plan name",
    "carrier",
}
_AMOUNT_HEADERS = {
    "billed",
    "billed amount",
    "charge",
    "charges",
    "billed charges",
    "allowed",
    "allowed amount",
    "paid",
    "paid amount",
    "payment",
    "insurance payment",
    "amount",
}


class DeidentifyError(Exception):
    """The upload still looks identifiable. The message names the column, not the cell."""


def norm_header(value: str) -> str:
    text = (value or "").strip().lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def forbidden_header(header: str) -> str:
    text = norm_header(header)
    if not text:
        return ""
    if text in _HEADER_EXACT:
        return _HEADER_EXACT[text]
    tokens = set(text.split())
    if "ssn" in tokens or "dob" in tokens:
        return "an SSN" if "ssn" in tokens else "a date of birth"
    for phrase, reason in _HEADER_PHRASES:
        if phrase in text:
            return reason
    return ""


def column_kind(header: str) -> str:
    text = norm_header(header)
    if any(word in text for word in ("remark", "note", "comment", "message")) or text in {
        "denial reason",
        "remark text",
    }:
        return "remark"
    if text in _PAYER_HEADERS:
        return "payer"
    if text in _AMOUNT_HEADERS:
        return "amount"
    return "code"


def forbidden_value(value: str, kind: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if _EMAIL.search(text):
        return "an email address"
    if _PHONE.search(text):
        return "a phone number"
    if _SSN.search(text):
        return "an SSN"
    if _STREET.search(text):
        return "an address"
    if _FULL_DATE.search(text):
        return "a date of birth"
    if kind != "amount" and _BARE_SSN.fullmatch(text):
        return "an SSN"
    if kind != "amount" and _MEMBER_ID.fullmatch(text.replace(" ", "")):
        return "a member or subscriber ID"
    if kind != "payer" and _PERSON.fullmatch(text):
        return "a patient name"
    return ""


def check_table(headers: list[str], rows: list[list[str]]) -> None:
    """Raise DeidentifyError when a header or cell still looks identifiable."""
    kinds: list[str] = []
    for header in headers:
        reason = forbidden_header(header)
        if reason:
            raise DeidentifyError(
                f"{_DEID_PREFIX} Column '{header}' looks like {reason}.{_DEID_TAIL}"
            )
        kinds.append(column_kind(header))
    for row in rows:
        for index, raw in enumerate(row):
            if index >= len(kinds) or kinds[index] == "remark":
                continue
            reason = forbidden_value(str(raw or ""), kinds[index])
            if reason:
                label = headers[index] if index < len(headers) else "a column"
                raise DeidentifyError(
                    f"{_DEID_PREFIX} A value in column '{label}' looks like {reason}.{_DEID_TAIL}"
                )


def scrub_remark(value: str) -> str:
    """Drop identifiable fragments from an allowed remark. The rest stays."""
    text = str(value or "")
    text = _EMAIL.sub("[removed]", text)
    text = _PHONE.sub("[removed]", text)
    text = _SSN.sub("[removed]", text)
    text = _STREET.sub("[removed]", text)
    text = _FULL_DATE.sub("[removed]", text)
    text = re.sub(r"\b[A-Z][a-z]+, [A-Z][a-z]+\b", "[removed]", text)
    text = re.sub(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b", "[removed]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]
