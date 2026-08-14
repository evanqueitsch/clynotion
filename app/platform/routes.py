"""HTTP routes for the Clynotion practice shell."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth import CurrentUser
from app.platform.due import due_engine, reconcile_supervision_drafts
from app.platform.practices import practice_store
from app.platform.schemas import HomeBandsOut, HomeOut, ObligationOut, PracticeOut
from app.store import store

router = APIRouter(tags=["clynotion"])
legacy = APIRouter(prefix="/attune", tags=["legacy"])


def _obligation_out(ob) -> ObligationOut:
    return ObligationOut.model_validate(ob.to_public_dict())


def _home_payload(user: CurrentUser) -> HomeOut:
    practice = practice_store.ensure(
        user.practice_id,
        display_name=user.practice_id,
    )
    # Supervision drafts feed the due engine without changing the capture pipeline.
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
        }
    ]
    return HomeOut(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        practice=PracticeOut.model_validate(practice.to_public_dict()),
        tools=tools,
        bands=bands,
        note=(
            "Clynotion home — due items from every domain land here. "
            "Supervision notes is the first tool."
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
    """Open obligations for this practice, split into Home bands."""
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


@legacy.get("/home", response_model=HomeOut)
def legacy_attune_home(user: CurrentUser) -> HomeOut:
    """Deprecated alias — prefer GET /home."""
    return _home_payload(user)


@legacy.get("/practice", response_model=PracticeOut)
def legacy_attune_practice(user: CurrentUser) -> PracticeOut:
    """Deprecated alias — prefer GET /practice."""
    return current_practice(user)
