"""Friends and friend requests: forwarded to Social, with player names from Auth added."""

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from web_bff import upstream
from web_bff.names import usernames

router = APIRouter(tags=["friends"])


async def forward(request: Request, path: str, method: str = "POST") -> Response:
    return await upstream.forward(request, request.app.state.social_client, "social", path, method)


@router.get("/users/search")
async def search_users(request: Request) -> Response:
    """Query: username. To add a friend by name (Auth: public fields only, rate limited).

    Not under /auth/: the refresh cookie (Path=/api/auth) has no reason to come along.
    """
    return await upstream.forward(request, request.app.state.auth_client, "auth", "/users/search", "GET")


@router.get("/friends")
async def list_friends(request: Request) -> Response:
    """Friends and pending requests, each with `username` (null if Auth is unreachable)."""
    answer = await upstream.send(request, request.app.state.social_client, "social", "/friends", "GET")
    if isinstance(answer, Response) or not answer.is_success:
        return answer if isinstance(answer, Response) else upstream.relay(answer)
    listing = answer.json()
    entries = listing["friends"] + listing["incoming"] + listing["outgoing"]
    names = await usernames(request, [e["user_id"] for e in entries]) or {}
    for entry in entries:
        entry["username"] = names.get(entry["user_id"])
    return JSONResponse(content=listing)


@router.post("/friends/requests")
async def send_friend_request(request: Request) -> Response:
    """Body: user_id. `accepted` right away if that player had already asked you."""
    return await forward(request, "/friends/requests")


@router.post("/friends/requests/{request_id}/accept")
async def accept_friend_request(request_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/friends/requests/{request_id}/accept")


@router.post("/friends/requests/{request_id}/decline")
async def decline_friend_request(request_id: uuid.UUID, request: Request) -> Response:
    """Also cancels a request you sent."""
    return await forward(request, f"/friends/requests/{request_id}/decline")


@router.delete("/friends/{friend_id}")
async def remove_friend(friend_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/friends/{friend_id}", "DELETE")
