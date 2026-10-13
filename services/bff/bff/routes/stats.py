"""Match results, player stats, progression and leaderboards: forwarded to Stats,
with player names from Auth added to leaderboards and match results."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import JSONResponse

from bff import upstream
from bff.names import usernames

router = APIRouter(tags=["stats"])

BoardType = Annotated[str, Path(pattern=r"^[a-z_]{1,32}$")]


async def forward(request: Request, path: str) -> Response:
    return await upstream.forward(request, request.app.state.stats_client, "stats", path, "GET")


async def with_names(request: Request, path: str, list_field: str) -> Response:
    """Adds `username` to each item of `list_field` (null for deleted players or if Auth is down)."""
    answer = await upstream.send(request, request.app.state.stats_client, "stats", path, "GET")
    if isinstance(answer, Response) or not answer.is_success:
        return answer if isinstance(answer, Response) else upstream.relay(answer)
    body = answer.json()
    items = body[list_field]
    names = await usernames(request, [i["user_id"] for i in items if i["user_id"]]) or {}
    for item in items:
        item["username"] = names.get(item["user_id"]) if item["user_id"] else None
    return JSONResponse(content=body)


@router.get("/leaderboards/{type}")
async def get_leaderboard(type: BoardType, request: Request) -> Response:
    """Query: offset, limit."""
    return await with_names(request, f"/leaderboards/{type}", "entries")


@router.get("/leaderboards/{type}/around/{user_id}")
async def get_leaderboard_around(type: BoardType, user_id: uuid.UUID, request: Request) -> Response:
    """Query: radius."""
    return await with_names(request, f"/leaderboards/{type}/around/{user_id}", "entries")


@router.get("/matches/{match_id}")
async def get_match(match_id: uuid.UUID, request: Request) -> Response:
    return await with_names(request, f"/matches/{match_id}", "players")


@router.get("/players/{user_id}/stats")
async def get_player_stats(user_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/players/{user_id}/stats")


@router.get("/players/{user_id}/matches")
async def get_player_matches(user_id: uuid.UUID, request: Request) -> Response:
    """Query: limit, before (next_cursor from the previous page)."""
    return await forward(request, f"/players/{user_id}/matches")


@router.get("/progression/{user_id}")
async def get_progression(user_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/progression/{user_id}")
