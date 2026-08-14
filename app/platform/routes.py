"""HTTP routes for the Clynotion practice shell + platform modules."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.auth import CurrentUser
from app.clinicians import clinician_store
from app.comply.registry import ensure_seeded_clocks, list_seed_catalog
from app.eligibility.service import eligibility_store
from app.grow.intake import intake_store
from app.ingest.sp_csv import ingest_store, reconcile_stale_ingest
from app.platform.due import due_engine, reconcile_supervision_drafts
from app.platform.practices import practice_store
from app.platform.schemas import (
    AttentionItemOut,
    ComplyCatalogItemOut,
    EligibilityCheckBody,
    EligibilityCheckOut,
    HomeBandsOut,
    HomeOut,
    IngestUploadBody,
    IngestUploadOut,
    IntakeCreateBody,
    IntakeEventOut,
    IntakePatchBody,
    ObligationOut,
    PracticeOut,
    PulseOut,
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
    return items


def _build_pulse(practice_id: str) -> PulseOut:
    bands = due_engine.bands(practice_id)
    open_count = len(due_engine.list_open(practice_id))
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


def _home_payload(user: CurrentUser) -> HomeOut:
    practice = practice_store.ensure(
        user.practice_id,
        display_name=user.practice_id,
    )
    # Tier-0 / Tier-1 feeds into due engine
    ensure_seeded_clocks(practice_id=user.practice_id, owner_user_id=user.user_id)
    reconcile_stale_ingest(practice_id=user.practice_id, owner_user_id=user.user_id)
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
    tools = [
        {
            "id": "supervision",
            "name": "Supervision notes",
            "href": "/#supervision",
            "description": "Clinical supervision capture and notes",
        },
        {
            "id": "comply",
            "name": "Compliance registry",
            "href": "/#comply",
            "description": "OPS-2 recurring clocks and credentialing calendar",
        },
        {
            "id": "ingest",
            "name": "SimplePractice ingest",
            "href": "/#ingest",
            "description": "Upload weekly/monthly SP CSV reports",
        },
        {
            "id": "intake",
            "name": "Intake log",
            "href": "/#intake",
            "description": "OPS-3 access-standard timestamps (case codes only)",
        },
        {
            "id": "eligibility",
            "name": "Eligibility",
            "href": "/#eligibility",
            "description": "OPS-5 eligibility checks (mock/manual; live adapters deferred)",
        },
    ]
    return HomeOut(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        practice=PracticeOut.model_validate(practice.to_public_dict()),
        tools=tools,
        bands=bands,
        attention=_build_attention(user.practice_id),
        pulse=_build_pulse(user.practice_id),
        note=(
            "Clynotion home — due items from every domain land here. "
            "Two-minute week: clear what's due, act on exceptions, close."
        ),
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
    ensure_seeded_clocks(practice_id=user.practice_id, owner_user_id=user.user_id)
    reconcile_stale_ingest(practice_id=user.practice_id, owner_user_id=user.user_id)
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


@router.post("/comply/seed", response_model=dict)
def comply_seed(user: CurrentUser) -> dict:
    codes = ensure_seeded_clocks(
        practice_id=user.practice_id, owner_user_id=user.user_id
    )
    return {"seeded": codes, "count": len(codes)}


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


@legacy.get("/home", response_model=HomeOut)
def legacy_attune_home(user: CurrentUser) -> HomeOut:
    return _home_payload(user)


@legacy.get("/practice", response_model=PracticeOut)
def legacy_attune_practice(user: CurrentUser) -> PracticeOut:
    return current_practice(user)
