"""Decision table for a denied or rejected claim.

Suggestions never recommend changing a diagnosis or CPT to get paid.
Documentation gaps are flagged for clinician review only.
"""

from __future__ import annotations

COMPLIANCE_NOTE = (
    "Suggestions never recommend changing a diagnosis or CPT to get paid. "
    "Documentation gaps are flagged for clinician review only."
)

CORRECT_RESUBMIT = "correct_resubmit"
RECONSIDERATION = "reconsideration"
APPEAL = "appeal"
BILL_CLIENT = "bill_client"
WRITE_OFF = "write_off"
FIX_CREDENTIALING = "fix_credentialing"
CLINICIAN_REVIEW = "clinician_review"

ACTION_LABELS = {
    CORRECT_RESUBMIT: "Correct and resubmit in SimplePractice",
    RECONSIDERATION: "Request reconsideration",
    APPEAL: "Appeal",
    BILL_CLIENT: "Bill the client",
    WRITE_OFF: "Write off with approval",
    FIX_CREDENTIALING: "Fix credentialing",
    CLINICIAN_REVIEW: "Flag for clinician review only",
}

# CARC (reason code, group prefix removed) -> (category, action).
_CARC: dict[str, tuple[str, str]] = {
    "4": ("coding", CORRECT_RESUBMIT),
    "5": ("place_of_service", CORRECT_RESUBMIT),
    "6": ("coding", CORRECT_RESUBMIT),
    "16": ("missing_information", CORRECT_RESUBMIT),
    "18": ("duplicate", WRITE_OFF),
    "22": ("coordination", CORRECT_RESUBMIT),
    "29": ("timely_filing", WRITE_OFF),
    "97": ("bundling", CORRECT_RESUBMIT),
    "107": ("related_service", CORRECT_RESUBMIT),
    "109": ("wrong_payer", CORRECT_RESUBMIT),
    "181": ("coding", CORRECT_RESUBMIT),
    "187": ("coding", CORRECT_RESUBMIT),
    "189": ("coding", CORRECT_RESUBMIT),
    "226": ("missing_information", CORRECT_RESUBMIT),
    "227": ("missing_information", CORRECT_RESUBMIT),
    "252": ("missing_information", CORRECT_RESUBMIT),
    "A1": ("missing_information", CORRECT_RESUBMIT),
    "9": ("documentation", CLINICIAN_REVIEW),
    "10": ("documentation", CLINICIAN_REVIEW),
    "11": ("documentation", CLINICIAN_REVIEW),
    "146": ("documentation", CLINICIAN_REVIEW),
    "167": ("documentation", CLINICIAN_REVIEW),
    "240": ("documentation", CLINICIAN_REVIEW),
    "B22": ("documentation", CLINICIAN_REVIEW),
    "15": ("authorization", RECONSIDERATION),
    "39": ("authorization", RECONSIDERATION),
    "197": ("authorization", RECONSIDERATION),
    "198": ("authorization", RECONSIDERATION),
    "50": ("medical_necessity", APPEAL),
    "55": ("medical_necessity", APPEAL),
    "57": ("medical_necessity", APPEAL),
    "150": ("medical_necessity", APPEAL),
    "151": ("medical_necessity", APPEAL),
    "1": ("patient_responsibility", BILL_CLIENT),
    "2": ("patient_responsibility", BILL_CLIENT),
    "3": ("patient_responsibility", BILL_CLIENT),
    "27": ("coverage_ended", BILL_CLIENT),
    "96": ("noncovered", BILL_CLIENT),
    "119": ("benefit_maximum", BILL_CLIENT),
    "204": ("noncovered", BILL_CLIENT),
    "23": ("contractual", WRITE_OFF),
    "45": ("contractual", WRITE_OFF),
    "59": ("contractual", WRITE_OFF),
    "131": ("contractual", WRITE_OFF),
    "246": ("contractual", WRITE_OFF),
    "253": ("sequestration", WRITE_OFF),
    "38": ("credentialing", FIX_CREDENTIALING),
    "147": ("credentialing", FIX_CREDENTIALING),
    "170": ("credentialing", FIX_CREDENTIALING),
    "185": ("credentialing", FIX_CREDENTIALING),
    "242": ("credentialing", FIX_CREDENTIALING),
    "243": ("credentialing", FIX_CREDENTIALING),
    "B7": ("credentialing", FIX_CREDENTIALING),
}


def carc_code(value: str) -> str:
    text = (value or "").strip().upper().replace(" ", "")
    if not text:
        return ""
    if "-" in text:
        text = text.split("-")[-1]
    return text


def decide(claim_status: str, carc: str) -> tuple[str, str]:
    """Return (category, action) for a denied or rejected claim."""
    status = (claim_status or "").strip().lower()
    code = carc_code(carc)
    if code in _CARC:
        return _CARC[code]
    if status == "rejected":
        return ("clearinghouse_reject", CORRECT_RESUBMIT)
    return ("unspecified", RECONSIDERATION)


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, ACTION_LABELS[RECONSIDERATION])
