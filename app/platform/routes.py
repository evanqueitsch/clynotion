"""HTTP routes for the Clynotion practice shell."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth import CurrentUser
from app.platform.practices import practice_store
from app.platform.schemas import HomeOut, PracticeOut

router = APIRouter(tags=["clynotion"])
legacy = APIRouter(prefix="/attune", tags=["legacy"])


def _home_payload(user: CurrentUser) -> HomeOut:
    practice = practice_store.ensure(
        user.practice_id,
        display_name=user.practice_id,
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
        note=(
            "Clynotion is the practice product. Supervision notes is the first tool. "
            "Meeting bot capture is deferred."
        ),
    )


@router.get("/home", response_model=HomeOut)
def clynotion_home(user: CurrentUser) -> HomeOut:
    return _home_payload(user)


@router.get("/practice", response_model=PracticeOut)
def current_practice(user: CurrentUser) -> PracticeOut:
    practice = practice_store.ensure(user.practice_id, display_name=user.practice_id)
    return PracticeOut.model_validate(practice.to_public_dict())


@legacy.get("/home", response_model=HomeOut)
def legacy_attune_home(user: CurrentUser) -> HomeOut:
    """Deprecated alias — prefer GET /home."""
    return _home_payload(user)


@legacy.get("/practice", response_model=PracticeOut)
def legacy_attune_practice(user: CurrentUser) -> PracticeOut:
    """Deprecated alias — prefer GET /practice."""
    return current_practice(user)
