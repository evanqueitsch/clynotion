"""HTTP routes for the Clynotion practice shell + platform modules."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.auth import CurrentUser, HrAdminUser
from app.clinicians import clinician_store
from app.comply.credentials import credential_store
from app.comply.registry import ensure_seeded_clocks, list_seed_catalog
from app.eligibility.service import eligibility_store
from app.grow.intake import intake_store
from app.hr.onboarding import onboarding_store
from app.ingest.sp_csv import ingest_store
from app.platform.catalog import catalog_by_unit, crosswalk_rows
from app.platform.due import due_engine, reconcile_supervision_drafts
from app.platform.practices import practice_store
from app.platform.schemas import (
    AttentionItemOut,
    ComplyCatalogItemOut,
    ComplianceItemCreateBody,
    EligibilityCheckBody,
    EligibilityCheckOut,
    EmployeeCreateBody,
    EmployeePatchBody,
    HomeBandsOut,
    HomeOut,
    IngestUploadBody,
    IngestUploadOut,
    IntakeCreateBody,
    IntakeEventOut,
    IntakePatchBody,
    ObligationOut,
    OnboardingPatchBody,
    PracticeOut,
    PulseOut,
    SupervisionEntryCreateBody,
)
from app.store import store

router = APIRouter(tags=["clynotion"])
legacy = APIRouter(prefix="/attune", tags=["legacy"])


def _obligation_out(ob) -> ObligationOut:
    return ObligationOut.model_validate(
        {k: v for k, v in ob.to_public_dict().items() if k in ObligationOut.model_fields}
    )


def _build_attention(practice_id: str) -> list[AttentionItemOut]:
    items: list[AttentionItemOut] = []
    bands = due_engine.bands(practice_id)
    if any(o.source == "ingest" and o.source_ref.startswith("fail:") for o in bands["overdue"]):
        items.append(
            AttentionItemOut(
                code="ingest_failed",
                title="A SimplePractice report failed to ingest",
                domain="platform",
                href="/#ingest",
            )
        )
    if any(o.source_ref == "stale:documentation" for o in bands["overdue"]):
        items.append(
            AttentionItemOut(
                code="ingest_stale",
                title="Weekly documentation report is missing or stale",
                domain="platform",
                href="/#ingest",
            )
        )
    unsigned = [
        o
        for o in due_engine.list_open(practice_id)
        if o.source == "ingest" and o.source_ref.startswith("unsigned:")
    ]
    if unsigned:
        items.append(
            AttentionItemOut(
                code="unsigned_aging",
                title=unsigned[0].title,
                domain="comply",
                href="/#comply",
            )
        )
    if any(o.source == "ops3" for o in due_engine.list_open(practice_id)):
        items.append(
            AttentionItemOut(
                code="access_clock",
                title="Intake access-standard clock needs attention",
                domain="grow",
                href="/#intake",
            )
        )
    if any(o.source == "hr_onboarding" for o in due_engine.list_open(practice_id)):
        items.append(
            AttentionItemOut(
                code="hr_onboarding",
                title="Onboarding / clearance item needs attention",
                domain="people",
                href="/#onboarding",
            )
        )
    return items


def _build_pulse(practice_id: str) -> PulseOut:
    bands = due_engine.bands(practice_id)
    open_count = len(bands["overdue"]) + len(bands["this_week"])
    latest = ingest_store.latest_ok(practice_id, "documentation")
    age_days = None
    unsigned_rows = 0
    if latest is not None:
        unsigned_rows = latest.unsigned_aging_count
        try:
            uploaded = datetime.fromisoformat(latest.uploaded_at.replace("Z", "+00:00"))
            if uploaded.tzinfo is None:
                uploaded = uploaded.replace(tzinfo=timezone.utc)
            age_days = max(0, int((datetime.now(timezone.utc) - uploaded).total_seconds() // 86400))
        except ValueError:
            age_days = None
    headcount = len(clinician_store.list_for_practice(practice_id))
    access = intake_store.access_performance(practice_id)
    elig = eligibility_store.list_for_practice(practice_id)
    elig_30 = 0
    cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
    for row in elig:
        try:
            ts = datetime.fromisoformat(row.checked_at.replace("Z", "+00:00")).timestamp()
            if ts >= cutoff:
                elig_30 += 1
        except ValueError:
            continue
    return PulseOut(
        open_obligations=open_count,
        overdue_count=len(bands["overdue"]),
        unsigned_aging_rows=unsigned_rows,
        open_headcount=headcount,
        documentation_ingest_age_days=age_days,
        access_pct_met=access.get("pct_met"),
        eligibility_checks_30d=elig_30,
    )


def _home_note(user: CurrentUser, open_count: int) -> str:
    raw = (user.username or "").strip()
    if "@" in raw:
        raw = ""
    first = raw.split()[0] if raw else ""
    hello = f"Hi, {first}." if first else "Hello."
    if open_count == 0:
        return f"{hello} Everything's caught up."
    return f"{hello} Here's what needs you."


def _module_ids(units: list[dict]) -> list[str]:
    ids: list[str] = []
    for unit in units:
        for tool in unit.get("tools") or []:
            if tool.get("coming_soon") or tool.get("status") == "planned":
                continue
            ids.append(str(tool.get("id") or ""))
    return ids


def _open_tools(units: list[dict]) -> list[dict[str, str]]:
    tools: list[dict[str, str]] = []
    for unit in units:
        for tool in unit.get("tools") or []:
            if tool.get("coming_soon") or tool.get("status") != "live" or not tool.get("href"):
                continue
            tools.append(
                {
                    "id": str(tool.get("id") or ""),
                    "name": str(tool.get("name") or ""),
                    "href": str(tool.get("href") or ""),
                    "description": str(tool.get("description") or ""),
                }
            )
    return tools


def _employee_rollout_on(user: CurrentUser) -> bool:
    from app.employees.store import employees_store

    return employees_store.rollout_enabled(user.practice_id)


def _visible_units(user: CurrentUser) -> list[dict]:
    """Admin sees every catalog module. Everyone else sees granted live modules."""
    from app.access.resolve import permissions_for, sees_every_module

    full = sees_every_module(user)
    perms = permissions_for(user)
    units = _time_entry_nav(catalog_by_unit(include_planned=True), user)
    visible: list[dict] = []
    for unit in units:
        kept: list[dict] = []
        for tool in unit.get("tools") or []:
            if tool.get("id") == "employee_view":
                if not full and not _employee_rollout_on(user):
                    continue
                tool["status"] = "live"
                tool["href"] = "/#employee"
                tool["coming_soon"] = False
                tool["open_label"] = "Open Employee View"
            if tool.get("status") == "planned":
                if not full:
                    continue
                tool["href"] = ""
                tool["open_label"] = ""
                tool["coming_soon"] = True
                kept.append(tool)
                continue
            tool_perm = f"tool:{tool.get('id')}"
            unit_perm = f"unit:{unit.get('id')}"
            if tool.get("requires_explicit_grant"):
                granted = full or tool_perm in perms
            else:
                granted = full or tool_perm in perms or unit_perm in perms
            if granted:
                kept.append(tool)
        if kept:
            visible.append({**unit, "tools": kept})
    return visible


def _home_payload(user: CurrentUser) -> HomeOut:
    from app.access.resolve import landing_for, sees_every_module

    practice = practice_store.ensure(
        user.practice_id,
        display_name=user.practice_id,
    )
    landing = landing_for(user)
    if landing == "none":
        return HomeOut(
            user_id=user.user_id,
            username=user.username,
            email=user.email,
            practice=PracticeOut.model_validate(practice.to_public_dict()),
            tools=[],
            units=[],
            bands=HomeBandsOut(),
            attention=[],
            pulse=PulseOut(),
            note="You do not have access yet. Contact Evan.",
            full_catalog=False,
        )
    units = _visible_units(user)
    tools = _open_tools(units)
    if not sees_every_module(user):
        only_time = _module_ids(units) == ["time_entry"]
        if only_time and not tools:
            entry = next(tool for unit in units for tool in unit["tools"] if tool.get("id") == "time_entry")
            tools = [
                {
                    "id": "time_entry",
                    "name": str(entry.get("name") or "Time Entry"),
                    "href": str(entry.get("href") or ""),
                    "description": str(entry.get("description") or ""),
                }
            ]
        bands = HomeBandsOut()
        from app.access.resolve import permissions_for

        if "tool:credentialing" in permissions_for(user):
            from app.credentialing.reminders import sync_reminders

            sync_reminders(user.practice_id, user.user_id)
            raw = due_engine.bands(user.practice_id)
            bands = HomeBandsOut(
                overdue=[
                    _obligation_out(o) for o in raw["overdue"] if o.domain == "credentialing"
                ],
                this_week=[
                    _obligation_out(o) for o in raw["this_week"] if o.domain == "credentialing"
                ],
            )
        return HomeOut(
            user_id=user.user_id,
            username=user.username,
            email=user.email,
            practice=PracticeOut.model_validate(practice.to_public_dict()),
            tools=tools,
            units=units,
            bands=bands,
            attention=[],
            pulse=PulseOut(),
            note="Time Entry" if only_time else _home_note(user, 0).split(". ")[0] + ".",
            full_catalog=False,
        )
    # Home reads the due register. Credentialing reminders are included for Admins.
    from app.credentialing.reminders import sync_reminders

    sync_reminders(user.practice_id, user.user_id)
    onboarding_store.reconcile_due(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    reconcile_supervision_drafts(
        practice_id=user.practice_id,
        owner_user_id=user.user_id,
        sessions=store.list_for_practice(user.practice_id),
    )
    bands_raw = due_engine.bands(user.practice_id)
    bands = HomeBandsOut(
        overdue=[_obligation_out(o) for o in bands_raw["overdue"]],
        this_week=[_obligation_out(o) for o in bands_raw["this_week"]],
    )
    pulse = _build_pulse(user.practice_id)
    return HomeOut(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        practice=PracticeOut.model_validate(practice.to_public_dict()),
        tools=tools,
        units=units,
        bands=bands,
        attention=_build_attention(user.practice_id),
        pulse=pulse,
        note=_home_note(user, pulse.open_obligations),
        full_catalog=True,
    )


@router.get("/home", response_model=HomeOut)
def clynotion_home(user: CurrentUser) -> HomeOut:
    return _home_payload(user)


@router.get("/practice", response_model=PracticeOut)
def current_practice(user: CurrentUser) -> PracticeOut:
    practice = practice_store.ensure(user.practice_id, display_name=user.practice_id)
    return PracticeOut.model_validate(practice.to_public_dict())


@router.get("/due", response_model=HomeBandsOut)
def due_bands(user: CurrentUser) -> HomeBandsOut:
    onboarding_store.reconcile_due(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    reconcile_supervision_drafts(
        practice_id=user.practice_id,
        owner_user_id=user.user_id,
        sessions=store.list_for_practice(user.practice_id),
    )
    bands_raw = due_engine.bands(user.practice_id)
    return HomeBandsOut(
        overdue=[_obligation_out(o) for o in bands_raw["overdue"]],
        this_week=[_obligation_out(o) for o in bands_raw["this_week"]],
    )


@router.post("/due/{obligation_id}/complete", response_model=ObligationOut)
def complete_obligation(obligation_id: str, user: CurrentUser) -> ObligationOut:
    """One-click complete — no confirmation, no required note."""
    ob = due_engine.complete_by_id(obligation_id, practice_id=user.practice_id)
    if ob is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    return _obligation_out(ob)


@router.get("/comply/catalog", response_model=list[ComplyCatalogItemOut])
def comply_catalog(user: CurrentUser) -> list[ComplyCatalogItemOut]:
    _ = user
    return [ComplyCatalogItemOut.model_validate(row) for row in list_seed_catalog()]


@router.get("/catalog")
def product_catalog(user: CurrentUser) -> list[dict]:
    """Business units → tools the signed-in person is allowed to see."""
    return _visible_units(user)


def _time_entry_nav(units: list[dict], user: CurrentUser) -> list[dict]:
    """Show weekly hours only to selected staff, plus admins and Melissa."""
    from app.hr.time_entry import nav_for_user

    nav = nav_for_user(user)
    for unit in units:
        for tool in unit.get("tools") or []:
            if tool.get("id") != "time_entry":
                continue
            tool["href"] = nav["href"]
            tool["status"] = nav["status"]
            tool["description"] = nav["description"]
            tool["open_label"] = nav["open_label"]
    return units


@router.get("/catalog/crosswalk")
def product_crosswalk(user: CurrentUser) -> list[dict]:
    """Flat master list: item → authorities (+ legacy aliases)."""
    _ = user
    return crosswalk_rows()


@router.post("/comply/seed", response_model=dict)
def comply_seed(user: CurrentUser) -> dict:
    codes = ensure_seeded_clocks(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    n = credential_store.reconcile(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    return {"seeded": codes, "count": len(codes), "credential_clocks": n}


@router.get("/comply/credentials")
def list_credential_clocks(user: CurrentUser) -> list[dict]:
    credential_store.reconcile(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    return credential_store.matrix(user.practice_id)


@router.post("/comply/credentials/{clinician_id}/{clock_id}/complete")
def complete_credential_clock(
    clinician_id: str, clock_id: str, user: CurrentUser
) -> dict:
    try:
        return credential_store.set_completed(
            practice_id=user.practice_id,
            clinician_id=clinician_id,
            clock_id=clock_id,
            owner_user_id=user.user_id,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail="clinician not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/ingest/uploads", response_model=list[IngestUploadOut])
def list_ingest_uploads(user: CurrentUser) -> list[IngestUploadOut]:
    return [
        IngestUploadOut.model_validate(u.to_public_dict())
        for u in ingest_store.list_for_practice(user.practice_id)
    ]


@router.post("/ingest/upload", response_model=IngestUploadOut)
def upload_ingest_csv(user: CurrentUser, body: IngestUploadBody) -> IngestUploadOut:
    try:
        up = ingest_store.ingest_csv(
            practice_id=user.practice_id,
            report_type=body.report_type,
            csv_text=body.csv_text,
            uploaded_by=user.user_id,
            column_map=body.column_map,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return IngestUploadOut.model_validate(up.to_public_dict())


@router.get("/intake", response_model=list[IntakeEventOut])
def list_intake(user: CurrentUser) -> list[IntakeEventOut]:
    return [
        IntakeEventOut.model_validate(e.to_public_dict())
        for e in intake_store.list_for_practice(user.practice_id)
    ]


@router.post("/intake", response_model=IntakeEventOut)
def create_intake(user: CurrentUser, body: IntakeCreateBody) -> IntakeEventOut:
    try:
        ev = intake_store.create(
            practice_id=user.practice_id,
            case_code=body.case_code,
            channel=body.channel,  # type: ignore[arg-type]
            triage=body.triage,  # type: ignore[arg-type]
            created_by=user.user_id,
            request_at=body.request_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return IntakeEventOut.model_validate(ev.to_public_dict())


@router.patch("/intake/{intake_id}", response_model=IntakeEventOut)
def patch_intake(
    intake_id: str, user: CurrentUser, body: IntakePatchBody
) -> IntakeEventOut:
    try:
        ev = intake_store.update(
            practice_id=user.practice_id,
            intake_id=intake_id,
            date_offered=body.date_offered,
            date_scheduled=body.date_scheduled,
            outcome=body.outcome,  # type: ignore[arg-type]
            owner_user_id=user.user_id,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail="intake not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return IntakeEventOut.model_validate(ev.to_public_dict())


@router.get("/intake/performance")
def intake_performance(user: CurrentUser) -> dict:
    return intake_store.access_performance(user.practice_id)


@router.get("/eligibility", response_model=list[EligibilityCheckOut])
def list_eligibility(user: CurrentUser) -> list[EligibilityCheckOut]:
    return [
        EligibilityCheckOut.model_validate(r.to_public_dict())
        for r in eligibility_store.list_for_practice(user.practice_id)
    ]


@router.post("/eligibility/check", response_model=EligibilityCheckOut)
def create_eligibility_check(
    user: CurrentUser, body: EligibilityCheckBody
) -> EligibilityCheckOut:
    try:
        row = eligibility_store.record_check(
            practice_id=user.practice_id,
            case_code=body.case_code,
            payer=body.payer,
            plan=body.plan,
            service_date=body.service_date,
            checked_by=user.user_id,
            method=body.method,  # type: ignore[arg-type]
            outcome=body.outcome,  # type: ignore[arg-type]
            coverage_detail=body.coverage_detail,
            evidence_ref=body.evidence_ref,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return EligibilityCheckOut.model_validate(row.to_public_dict())


# --- HR onboarding & compliance (owner / back-office admin) ---


@router.get("/hr/roster")
def hr_roster(user: HrAdminUser) -> list[dict]:
    onboarding_store.ensure_seeded(user.practice_id)
    onboarding_store.reconcile_due(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    return onboarding_store.roster_rows(user.practice_id)


@router.get("/hr/dashboard")
def hr_compliance_dashboard(user: HrAdminUser) -> dict:
    onboarding_store.ensure_seeded(user.practice_id)
    return onboarding_store.compliance_dashboard(user.practice_id)


@router.post("/hr/employees")
def hr_create_employee(user: HrAdminUser, body: EmployeeCreateBody) -> dict:
    try:
        emp = onboarding_store.create_employee(
            practice_id=user.practice_id,
            display_name=body.display_name,
            title=body.title,
            license_type=body.license_type,  # type: ignore[arg-type]
            license_number=body.license_number,
            license_expiry=body.license_expiry,
            supervisor_id=body.supervisor_id,
            start_date=body.start_date,
            drive_folder_url=body.drive_folder_url,
            updated_by=user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return onboarding_store.hire_detail(user.practice_id, emp.employee_id)


@router.get("/hr/employees/{employee_id}")
def hr_hire_detail(employee_id: str, user: HrAdminUser) -> dict:
    try:
        return onboarding_store.hire_detail(user.practice_id, employee_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="employee not found") from e


@router.patch("/hr/employees/{employee_id}")
def hr_patch_employee(
    employee_id: str, user: HrAdminUser, body: EmployeePatchBody
) -> dict:
    try:
        onboarding_store.patch_employee(
            user.practice_id, employee_id, body.model_dump(exclude_none=True)
        )
        return onboarding_store.hire_detail(user.practice_id, employee_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="employee not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.patch("/hr/employees/{employee_id}/onboarding")
def hr_patch_onboarding(
    employee_id: str, user: HrAdminUser, body: OnboardingPatchBody
) -> dict:
    try:
        onboarding_store.patch_onboarding(
            user.practice_id,
            employee_id,
            steps=body.steps or None,
            notes=body.notes,
            updated_by=user.user_id,
        )
        return onboarding_store.hire_detail(user.practice_id, employee_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="employee not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/hr/employees/{employee_id}/compliance")
def hr_add_compliance(
    employee_id: str, user: HrAdminUser, body: ComplianceItemCreateBody
) -> dict:
    try:
        onboarding_store.add_compliance_item(
            practice_id=user.practice_id,
            employee_id=employee_id,
            type=body.type,  # type: ignore[arg-type]
            issue_date=body.issue_date,
            expiry_date=body.expiry_date,
            renewal_interval_months=body.renewal_interval_months,
            document_drive_url=body.document_drive_url,
            updated_by=user.user_id,
        )
        return onboarding_store.hire_detail(user.practice_id, employee_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="employee not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/hr/employees/{employee_id}/supervision")
def hr_add_supervision(
    employee_id: str, user: HrAdminUser, body: SupervisionEntryCreateBody
) -> dict:
    try:
        onboarding_store.add_supervision_entry(
            practice_id=user.practice_id,
            supervisee_id=employee_id,
            supervisor_id=body.supervisor_id,
            date_str=body.date,
            duration_minutes=body.duration_minutes,
            format=body.format,  # type: ignore[arg-type]
            notes=body.notes,
            signed_by_both=body.signed_by_both,
        )
        return onboarding_store.hire_detail(user.practice_id, employee_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="employee not found") from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@legacy.get("/home", response_model=HomeOut)
def legacy_attune_home(user: CurrentUser) -> HomeOut:
    return _home_payload(user)


@legacy.get("/practice", response_model=PracticeOut)
def legacy_attune_practice(user: CurrentUser) -> PracticeOut:
    return current_practice(user)


def _mount_time_routes() -> None:
    from app.hr.time_routes import router as time_router

    router.include_router(time_router)


_mount_time_routes()
