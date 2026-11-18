"""Blank forms shipped with the app. English only. Not a public static path."""

from __future__ import annotations

from pathlib import Path

FORMS_DIR = Path(__file__).resolve().parent / "assets" / "forms"

FORMS: tuple[dict[str, str], ...] = (
    {
        "form_key": "uscis_i9",
        "title": "Form I-9",
        "publisher": "USCIS",
        "edition": "01/20/25",
        "expires_on": "2027-05-31",
        "source_url": "https://www.uscis.gov/i-9",
        "filename": "uscis_i9.pdf",
        "applies_to": "every hire, paper, in person",
    },
    {
        "form_key": "uscis_i9_instructions",
        "title": "Form I-9 instructions",
        "publisher": "USCIS",
        "edition": "01/20/25",
        "expires_on": "2027-05-31",
        "source_url": "https://www.uscis.gov/i-9",
        "filename": "uscis_i9_instructions.pdf",
        "applies_to": "with the I-9",
    },
    {
        "form_key": "irs_w4",
        "title": "Form W-4",
        "publisher": "IRS",
        "edition": "2026",
        "expires_on": "",
        "source_url": "https://www.irs.gov/forms-pubs/about-form-w-4",
        "filename": "irs_w4_2026.pdf",
        "applies_to": "payroll, status only",
        "january_check": "1",
    },
    {
        "form_key": "pa_clgs_32_6",
        "title": "PA Residency Certification CLGS-32-6",
        "publisher": "PA DCED",
        "edition": "05/17",
        "expires_on": "",
        "source_url": "https://dced.pa.gov/local-government/local-income-tax-information/",
        "filename": "pa_clgs_32_6.pdf",
        "applies_to": "every hire, status only",
    },
    {
        "form_key": "pa_rev_419",
        "title": "REV-419 nonwithholding certificate",
        "publisher": "PA Department of Revenue",
        "edition": "09/20",
        "expires_on": "",
        "source_url": "https://www.revenue.pa.gov/FormsandPublications/",
        "filename": "pa_rev_419.pdf",
        "applies_to": "only when an exemption applies",
    },
    {
        "form_key": "h4c_employee_info",
        "title": "Employee information and acknowledgment",
        "publisher": "H4C",
        "edition": "2026-11",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_employee_info.pdf",
        "applies_to": "every hire",
    },
    {
        "form_key": "h4c_packet",
        "title": "New hire packet",
        "publisher": "H4C",
        "edition": "2026-11",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_packet.pdf",
        "applies_to": "every hire",
    },
    {
        "form_key": "h4c_welcome_template",
        "title": "Welcome message template",
        "publisher": "H4C",
        "edition": "2026-11",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_welcome.docx",
        "applies_to": "admin sends until email copy is approved for this template",
    },
    {
        "form_key": "h4c_hipaa_nda",
        "title": "HIPAA confidentiality agreement",
        "publisher": "H4C",
        "edition": "2026-08",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_hipaa.pdf",
        "applies_to": "every hire",
    },
    {
        "form_key": "h4c_supervision",
        "title": "Clinical supervision agreement",
        "publisher": "H4C",
        "edition": "2026-11",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_supervision.pdf",
        "applies_to": "hires who are not fully licensed",
    },
    {
        "form_key": "pa_standard_application",
        "title": "PA Standard Application",
        "publisher": "Keystone First",
        "edition": "12/06",
        "expires_on": "",
        "source_url": "https://www.keystonefirstpa.com/provider/credentialing/paper",
        "filename": "pa_standard_application.pdf",
        "applies_to": "credentialing handoff, direct client access",
    },
    {
        "form_key": "h4c_employment_agreement",
        "title": "Employment agreement",
        "publisher": "H4C",
        "edition": "",
        "expires_on": "",
        "source_url": "",
        "filename": "",
        "applies_to": "every hire, file not shipped",
    },
    {
        "form_key": "h4c_confidentiality",
        "title": "Confidentiality, non-competition and non-solicitation",
        "publisher": "H4C",
        "edition": "",
        "expires_on": "",
        "source_url": "",
        "filename": "h4c_confidentiality.pdf",
        "applies_to": "every hire",
    },
    {
        "form_key": "h4c_handbook",
        "title": "Handbook acknowledgment",
        "publisher": "H4C",
        "edition": "",
        "expires_on": "",
        "source_url": "",
        "filename": "",
        "applies_to": "every hire, file not shipped",
    },
    {
        "form_key": "h4c_qsehra",
        "title": "QSEHRA enrollment",
        "publisher": "H4C",
        "edition": "",
        "expires_on": "",
        "source_url": "",
        "filename": "",
        "applies_to": "full-time benefits, file not shipped",
    },
    {
        "form_key": "h4c_simple_ira",
        "title": "SIMPLE IRA enrollment",
        "publisher": "H4C",
        "edition": "",
        "expires_on": "",
        "source_url": "",
        "filename": "",
        "applies_to": "full-time benefits, file not shipped",
    },
    {
        "form_key": "h4c_pa_clearances",
        "title": "PA clearances",
        "publisher": "H4C",
        "edition": "2026-11",
        "expires_on": "",
        "source_url": "https://www.pa.gov/agencies/dhs/resources/keep-kids-safe/child-abuse-clearances",
        "filename": "h4c_pa_clearances.pdf",
        "applies_to": "client-facing roles",
    },
)

BENEFIT_KEYS = frozenset({"h4c_qsehra", "h4c_simple_ira"})
CLIENT_KEYS = frozenset({"pa_standard_application", "h4c_pa_clearances"})
OPTIONAL_KEYS = frozenset({"pa_rev_419"})


def packet_catalog(
    employment_type: str,
    direct_client_access: bool,
    position: str,
    credentials: str = "",
) -> list[dict[str, str | bool]]:
    """Forms for the packet step. Missing files stay visible and cannot be selected."""
    from app.hiring.checklist import supervision_required

    rows: list[dict[str, str | bool]] = []
    clinical = "lsw" in (position or "").casefold() or "clinician" in (position or "").casefold()
    supervised = clinical and supervision_required(credentials)
    for spec in FORMS:
        key = spec["form_key"]
        if key in {"irs_w4", "h4c_welcome_template"}:
            continue
        if employment_type == "part-time" and key in BENEFIT_KEYS:
            continue
        if key in CLIENT_KEYS and not direct_client_access:
            continue
        if key == "h4c_supervision" and not supervised:
            continue
        available = bool(spec.get("filename"))
        rows.append(
            {
                "form_key": key,
                "title": spec["title"],
                "available": available,
                "default": available and key not in OPTIONAL_KEYS,
                "note": "" if available else "not in the library yet",
            }
        )
    return rows


def form_path(filename: str) -> Path:
    return FORMS_DIR / filename
