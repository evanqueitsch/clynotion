"""Official exclusion sources.

LEIE: HHS-OIG downloadable UPDATED.csv.
  https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv
  The file's update date is the "Last Update" on
  https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/

SAM: GSA Exclusions API version 4.
  https://api.sam.gov/entity-information/v4/exclusions
  A missing SAM_API_KEY is "not run". It is never treated as clear.

PA Medicheck: DHS generates a full text file from the precluded-provider search
when every field is left blank.
  https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Medchk
  The search page says the list is updated daily.
  If that download is not a list, the result is manual check required.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Protocol
from zoneinfo import ZoneInfo

import logging

logger = logging.getLogger("app.compliance.sources")

EASTERN = ZoneInfo("America/New_York")

LEIE_CSV_URL = "https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv"
LEIE_PAGE_URL = "https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/"
SAM_URL = "https://api.sam.gov/entity-information/v4/exclusions"
MEDICHECK_URL = "https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Medchk"
SAM_KEY_ENV = "SAM_API_KEY"

_DATE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def iso_date(value: str) -> str:
    text = str(value or "").strip()
    if not text or set(text) <= {"0"}:
        return ""
    candidates = [text[:10], text[:8]]
    for raw in candidates:
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(raw, fmt).date().isoformat()
            except ValueError:
                continue
    return ""


def digits(value: str) -> str:
    found = "".join(ch for ch in (value or "") if ch.isdigit())
    if not found or set(found) == {"0"}:
        return ""
    return found


def norm_token(value: str) -> str:
    return "".join(ch for ch in (value or "").casefold() if ch.isalpha())


@dataclass
class ListedPerson:
    last: str
    first: str
    middle: str = ""
    npi: str = ""
    kind: str = ""
    state: str = ""
    active: bool = True
    dob: str = ""
    upin: str = ""
    address: str = ""
    city: str = ""
    zip_code: str = ""
    business: str = ""
    general: str = ""
    specialty: str = ""
    excl_date: str = ""
    rein_date: str = ""
    waiver_date: str = ""
    uei: str = ""
    record_status: str = ""
    termination_date: str = ""
    agency: str = ""

    def proof(self) -> dict[str, str]:
        npi = digits(self.npi)
        return {
            "last": self.last,
            "first": self.first,
            "middle": self.middle,
            "npi_masked": (("••••" + npi[-4:]) if len(npi) >= 4 else ""),
            "kind": self.kind,
            "state": self.state,
            "active": "yes" if self.active else "no",
        }

    def record(self) -> dict[str, str]:
        """Full list entry for the Admin comparison. Stored on the result, not in the audit log."""
        return {
            "last": self.last,
            "first": self.first,
            "middle": self.middle,
            "npi": digits(self.npi),
            "upin": self.upin,
            "dob": self.dob,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "zip": self.zip_code,
            "business": self.business,
            "general": self.general,
            "specialty": self.specialty,
            "exclusion_type": self.kind,
            "exclusion_date": self.excl_date,
            "reinstatement_date": self.rein_date,
            "waiver_date": self.waiver_date,
            "uei": self.uei,
            "record_status": self.record_status,
            "termination_date": self.termination_date,
            "agency": self.agency,
        }


@dataclass
class ListFile:
    name: str
    updated_on: str
    file_sha256: str
    rows: list[ListedPerson] = field(default_factory=list)
    ready: bool = True
    detail: str = ""
    reported_count: int = 0


class Fetcher(Protocol):
    def get(self, url: str, params: Optional[dict[str, str]] = None) -> tuple[int, str, bytes]: ...


class HttpxFetcher:
    def get(self, url: str, params: Optional[dict[str, str]] = None) -> tuple[int, str, bytes]:
        response = httpx.get(url, params=params, timeout=60, follow_redirects=True, headers={"User-Agent": "Clynotion"})
        return response.status_code, response.text, response.content


def leie_updated_on(page_text: str, fallback: str) -> str:
    """The downloads page prints the file date as MM-DD-YYYY above 'Last Update'."""
    compact = re.sub(r"\s+", " ", page_text or "")
    match = re.search(r"(\d{2}-\d{2}-\d{4}).{0,40}Last Update", compact, re.IGNORECASE)
    if not match:
        match = _DATE.search(compact)
    if not match:
        return fallback
    text = match.group(1) if match.lastindex else match.group(0)
    parts = _DATE.search(text)
    if not parts:
        return fallback
    month, day, year = parts.group(1), parts.group(2), parts.group(3)
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return fallback


def parse_leie(csv_text: str, *, updated_on: str, raw: bytes | None = None) -> ListFile:
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[ListedPerson] = []
    for item in reader:
        if not isinstance(item, dict):
            continue
        last = str(item.get("LASTNAME") or "").strip()
        first = str(item.get("FIRSTNAME") or "").strip()
        if not last and not first:
            continue
        rows.append(
            ListedPerson(
                last=last,
                first=first,
                middle=str(item.get("MIDNAME") or "").strip(),
                npi=digits(str(item.get("NPI") or "")),
                kind=str(item.get("EXCLTYPE") or "").strip(),
                state=str(item.get("STATE") or "").strip(),
                active=True,
                dob=iso_date(str(item.get("DOB") or "")),
                upin=str(item.get("UPIN") or "").strip(),
                address=str(item.get("ADDRESS") or "").strip(),
                city=str(item.get("CITY") or "").strip(),
                zip_code=str(item.get("ZIP") or "").strip(),
                business=str(item.get("BUSNAME") or "").strip(),
                general=str(item.get("GENERAL") or "").strip(),
                specialty=str(item.get("SPECIALTY") or "").strip(),
                excl_date=iso_date(str(item.get("EXCLDATE") or "")),
                rein_date=iso_date(str(item.get("REINDATE") or "")),
                waiver_date=iso_date(str(item.get("WAIVERDATE") or "")),
            )
        )
    payload = raw if raw is not None else csv_text.encode("utf-8")
    return ListFile(name="leie", updated_on=updated_on, file_sha256=file_sha256(payload), rows=rows)


def _medicheck_active(status: str, end: str, today: date) -> bool:
    folded = (status or "").casefold()
    if "reinstat" in folded or "withdraw" in folded:
        return False
    if end:
        for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
            try:
                ended = datetime.strptime(end[:10] if fmt == "%Y-%m-%d" else end.split(" ")[0], fmt).date()
            except ValueError:
                continue
            if ended < today:
                return False
            break
    return "preclud" in folded or "permanent" in folded or "terminat" in folded or "suspend" in folded


def parse_medicheck(csv_text: str, *, updated_on: str, today: date, raw: bytes | None = None) -> ListFile:
    sample = (csv_text or "").lstrip()[:400].casefold()
    if "nam_last_provr" not in sample and "providername" not in sample:
        return ListFile(
            name="medicheck",
            updated_on="",
            file_sha256="",
            ready=False,
            detail="manual check required",
        )
    reader = csv.DictReader(io.StringIO(csv_text))
    rows: list[ListedPerson] = []
    for item in reader:
        if not isinstance(item, dict):
            continue
        last = str(item.get("NAM_LAST_PROVR") or "").strip()
        first = str(item.get("NAM_FIRST_PROVR") or "").strip()
        status = str(item.get("Status") or "").strip()
        end = str(item.get("EndDate") or "").strip()
        active = _medicheck_active(status, end, today)
        if not active:
            continue
        rows.append(
            ListedPerson(
                last=last,
                first=first,
                middle=str(item.get("NAM_MIDDLE_PROVR") or "").strip(),
                npi=digits(str(item.get("IDN_NPI") or "")),
                kind=status,
                state="",
                active=True,
            )
        )
    payload = raw if raw is not None else csv_text.encode("utf-8")
    return ListFile(name="medicheck", updated_on=updated_on, file_sha256=file_sha256(payload), rows=rows)


def sam_configured() -> bool:
    return bool((os.environ.get(SAM_KEY_ENV) or "").strip())


def _sam_address_block(item: dict[str, Any]) -> dict[str, Any]:
    for key in ("exclusionPrimaryAddress", "exclusionAddress", "primaryAddress"):
        block = item.get(key)
        if isinstance(block, dict):
            return block
    return {}


def _sam_dicts(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if depth > 6 or len(found) > 40:
        return found
    if isinstance(payload, dict):
        ident = payload.get("exclusionIdentification")
        if isinstance(ident, dict) or isinstance(payload.get("exclusionDetails"), dict):
            found.append(payload)
        for value in payload.values():
            found.extend(_sam_dicts(value, depth + 1))
            if len(found) > 40:
                break
    elif isinstance(payload, list):
        for item in payload[:40]:
            found.extend(_sam_dicts(item, depth + 1))
            if len(found) > 40:
                break
    return found


def parse_sam(payload: dict[str, Any], *, queried_on: str, raw: bytes) -> tuple[str, list[ListedPerson]]:
    records = _sam_dicts(payload)
    rows: list[ListedPerson] = []
    for item in records:
            if not isinstance(item, dict):
                continue
            ident = item.get("exclusionIdentification") if isinstance(item.get("exclusionIdentification"), dict) else item
            details = item.get("exclusionDetails") if isinstance(item.get("exclusionDetails"), dict) else item
            address = _sam_address_block(item)
            actions = item.get("exclusionActions") if isinstance(item.get("exclusionActions"), dict) else {}
            action_list = actions.get("listOfActions") if isinstance(actions.get("listOfActions"), list) else []
            action = next((row for row in action_list if isinstance(row, dict)), {})
            other = item.get("exclusionOtherInformation") if isinstance(item.get("exclusionOtherInformation"), dict) else {}
            last = str(ident.get("lastName") or ident.get("exclusionName") or ident.get("entityName") or "").strip()
            first = str(ident.get("firstName") or "").strip()
            if not first and "," in last:
                bits = [part.strip() for part in last.split(",", 1)]
                if len(bits) == 2:
                    last, first = bits[0], bits[1]
            dob = str(
                ident.get("dateOfBirth")
                or ident.get("dob")
                or other.get("dateOfBirth")
                or other.get("dob")
                or ""
            )
            rows.append(
                ListedPerson(
                    last=last,
                    first=first,
                    middle=str(ident.get("middleName") or "").strip(),
                    npi=digits(str(ident.get("npi") or "")),
                    kind=str(details.get("exclusionType") or details.get("exclusionProgram") or "").strip(),
                    state=str(address.get("stateOrProvinceCode") or address.get("state") or address.get("stateProvince") or "").strip(),
                    active=str(action.get("recordStatus") or "").casefold() != "inactive",
                    dob=iso_date(dob),
                    address=" ".join(
                        part
                        for part in (
                            str(address.get("addressLine1") or "").strip(),
                            str(address.get("addressLine2") or "").strip(),
                        )
                        if part
                    ),
                    city=str(address.get("city") or "").strip(),
                    zip_code=str(address.get("zipCode") or address.get("zip") or "").strip(),
                    business=str(ident.get("entityName") or "").strip() if not first else "",
                    general=str(details.get("classificationType") or "").strip(),
                    specialty=str(details.get("occupation") or other.get("profession") or "").strip(),
                    excl_date=iso_date(str(action.get("activateDate") or details.get("exclusionDate") or "")),
                    uei=str(ident.get("ueiSAM") or ident.get("uei") or "").strip(),
                    record_status=str(action.get("recordStatus") or "").strip(),
                    termination_date=iso_date(str(action.get("terminationDate") or "")),
                    agency=str(details.get("excludingAgencyName") or details.get("excludingAgencyCode") or "").strip(),
                )
            )
    return file_sha256(raw), rows


def load_leie(fetcher: Fetcher, *, today: date) -> ListFile:
    status, page, _ = fetcher.get(LEIE_PAGE_URL)
    updated = leie_updated_on(page if status == 200 else "", today.isoformat())
    code, text, raw = fetcher.get(LEIE_CSV_URL)
    if code != 200 or "LASTNAME" not in (text[:400] if text else ""):
        return ListFile(name="leie", updated_on="", file_sha256="", ready=False, detail="source unavailable")
    return parse_leie(text, updated_on=updated, raw=raw)


def load_medicheck(fetcher: Fetcher, *, today: date) -> ListFile:
    code, text, raw = fetcher.get(MEDICHECK_URL)
    if code != 200:
        return ListFile(name="medicheck", updated_on="", file_sha256="", ready=False, detail="manual check required")
    return parse_medicheck(text, updated_on=today.isoformat(), today=today, raw=raw)


def load_sam_person(fetcher: Fetcher, *, first: str, last: str, npi: str, today: date) -> ListFile:
    key = (os.environ.get(SAM_KEY_ENV) or "").strip()
    if not key:
        return ListFile(name="sam", updated_on="", file_sha256="", ready=False, detail="not run: API key not configured")
    params = {"api_key": key, "classification": "Individual", "exclusionName": f"{first} {last}".strip()}
    code, text, raw = fetcher.get(SAM_URL, params)
    rows: list[ListedPerson] = []
    digest = file_sha256(raw or b"")
    if code != 200:
        return ListFile(name="sam", updated_on=today.isoformat(), file_sha256=digest, ready=False, detail="source unavailable")
    try:
        payload = json_loads(text)
    except ValueError:
        return ListFile(name="sam", updated_on=today.isoformat(), file_sha256=digest, ready=False, detail="source unavailable")
    digest, rows = parse_sam(payload, queried_on=today.isoformat(), raw=raw)
    reported = int(payload.get("totalRecords") or len(rows) or 0)
    if npi:
        code_n, text_n, raw_n = fetcher.get(SAM_URL, {"api_key": key, "npi": npi})
        if code_n == 200:
            try:
                extra_digest, extra = parse_sam(json_loads(text_n), queried_on=today.isoformat(), raw=raw_n)
            except ValueError:
                extra = []
                extra_digest = ""
            rows.extend(extra)
            digest = file_sha256((digest + extra_digest).encode("ascii"))
            reported += len(extra)
    return ListFile(
        name="sam",
        updated_on=today.isoformat(),
        file_sha256=digest,
        rows=rows,
        ready=True,
        detail="",
        reported_count=reported,
    )


def load_sam_detail(fetcher: Fetcher, *, first: str, last: str, today: date) -> ListFile:
    """Read exclusion records for one name. The log has a reason code, never the key or a name."""
    key = (os.environ.get(SAM_KEY_ENV) or "").strip()
    if not key:
        logger.warning("sam_detail_reread reason=missing_key status=0")
        return ListFile(name="sam", updated_on="", file_sha256="", ready=False, detail="not run: API key not configured")
    queries = [
        {"classification": "Individual", "exclusionName": f"{first} {last}".strip()},
        {"exclusionName": f"{last}, {first}".strip(", ")},
        {"exclusionName": last},
    ]
    last_reason = "no_query"
    last_status = 0
    chosen: list[ListedPerson] = []
    digest = ""
    reported = 0
    for extra in queries:
        params = {"api_key": key, **{key_name: value for key_name, value in extra.items() if value}}
        code, text, raw = fetcher.get(SAM_URL, params)
        last_status = code
        if code != 200:
            last_reason = f"http_{code}"
            continue
        try:
            payload = json_loads(text)
        except ValueError:
            last_reason = "unparsed"
            continue
        digest, found = parse_sam(payload, queried_on=today.isoformat(), raw=raw)
        reported = int(payload.get("totalRecords") or len(found) or 0)
        if reported and not found:
            last_reason = "unparsed"
            logger.warning("sam_detail_reread reason=unparsed status=%s keys=%s", code, ",".join(sorted(payload.keys())[:12]))
            continue
        if not found:
            last_reason = "no_rows"
            continue
        wanted_last = norm_token(last)
        named = [row for row in found if not wanted_last or norm_token(row.last) == wanted_last or wanted_last in norm_token(row.last)]
        if named:
            chosen = named
            last_reason = "matched"
            break
        if len(found) == 1 and "exclusionName" in extra and "," in str(extra.get("exclusionName") or ""):
            chosen = found
            last_reason = "single_record"
            break
        last_reason = "name_filter_dropped"
    if not chosen:
        logger.warning("sam_detail_reread reason=%s status=%s", last_reason, last_status)
        return ListFile(
            name="sam",
            updated_on=today.isoformat(),
            file_sha256=digest,
            rows=[],
            ready=False,
            detail=f"SAM detail was not stored ({last_reason}). The match result is unchanged.",
        )
        return ListFile(
            name="sam",
            updated_on=today.isoformat(),
            file_sha256=digest,
            rows=chosen,
            ready=True,
            detail="",
            reported_count=reported or len(chosen),
        )


def json_loads(text: str) -> dict[str, Any]:
    import json

    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("sam payload")
    return payload
