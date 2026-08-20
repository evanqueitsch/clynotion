"""HR onboarding & employee compliance (spreadsheet replacement).

PII zone: status + dates + short notes + Drive links only.
Never persist SSN, bank details, DOB, or clearance document contents.
"""

from __future__ import annotations

from app.hr.onboarding import onboarding_store

__all__ = ["onboarding_store"]
