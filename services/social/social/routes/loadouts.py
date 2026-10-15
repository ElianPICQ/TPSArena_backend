from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import delete, insert, select

from social.deps import CurrentUserId, Session
from social.models import Loadout
from social.routes.friends import ensure_players
from social.schemas import ErrorResponse, LoadoutItem, LoadoutsResponse, LoadoutsUpdate

router = APIRouter(tags=["loadouts"])


def _item(row: Loadout) -> LoadoutItem:
    return LoadoutItem(
        slot=row.slot, name=row.name, primary_weapon=row.primary_weapon, secondary_weapon=row.secondary_weapon
    )


@router.get("/loadouts/me", responses={401: {"model": ErrorResponse}})
async def get_my_loadouts(user_id: CurrentUserId, session: Session) -> LoadoutsResponse:
    """An empty list means the player never saved classes: the game shows its defaults."""
    rows = (await session.scalars(select(Loadout).where(Loadout.user_id == user_id).order_by(Loadout.slot))).all()
    return LoadoutsResponse(loadouts=[_item(row) for row in rows])


@router.put("/loadouts/me", responses={401: {"model": ErrorResponse}})
async def replace_my_loadouts(body: LoadoutsUpdate, user_id: CurrentUserId, session: Session) -> LoadoutsResponse:
    """Replaces every saved class at once (the game edits the whole list)."""
    await ensure_players(session, user_id)
    await session.execute(delete(Loadout).where(Loadout.user_id == user_id))
    if body.loadouts:
        now = datetime.now(UTC)
        await session.execute(
            insert(Loadout),
            [{"user_id": user_id, **item.model_dump(), "updated_at": now} for item in body.loadouts],
        )
    await session.commit()
    return LoadoutsResponse(loadouts=sorted(body.loadouts, key=lambda item: item.slot))
