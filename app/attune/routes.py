"""HTTP routes for the Attune practice shell."""

from __future__ import annotations

from fastapi import APIRouter

from app.attune.practices import practice_store
from app.attune.schemas import AttuneHomeOut, PracticeOut
from app.auth import CurrentUser

router = APIRouter(prefix="/attune", tags=["attune"])


@router.get("/home", response_model=AttuneHomeOut)
def attune_home(user: CurrentUser) -> AttuneHomeOut:
    practice = practice_store.ensure(
        user.practice_id,
        display_name=user.practice_id,
    )
    tools = [
        {
            "id": "clynotion",
            "name": "Clynotion",
            "href": "/#clynotion",
            "description": "Clinical supervision capture and notes",
        }
    ]
    return AttuneHomeOut(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        practice=PracticeOut.model_validate(practice.to_public_dict()),
        tools=tools,
        note=(
            "Attune is the practice product. Clynotion is the first tool inside it. "
            "Meeting bot capture is deferred."
        ),
    )


@router.get("/practice", response_model=PracticeOut)
def current_practice(user: CurrentUser) -> PracticeOut:
    practice = practice_store.ensure(user.practice_id, display_name=user.practice_id)
    return PracticeOut.model_validate(practice.to_public_dict())
