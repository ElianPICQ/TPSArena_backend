"""Profiles, friends and parties: forwarded to Social, with player names from Auth added."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import JSONResponse

from bff import upstream
from bff.names import usernames

router = APIRouter(tags=["social"])

InviteId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


def client(request: Request):
    return request.app.state.social_client


async def forward(request: Request, path: str, method: str = "POST") -> Response:
    return await upstream.forward(request, client(request), "social", path, method)


async def send(request: Request, path: str, method: str):
    return await upstream.send(request, client(request), "social", path, method)


async def party_with_names(request: Request, path: str, method: str) -> Response:
    """Party answers get a `members` list (name and class of each) next to `member_ids`."""
    answer = await send(request, path, method)
    if isinstance(answer, Response) or not answer.is_success:
        return answer if isinstance(answer, Response) else upstream.relay(answer)
    party = answer.json()
    names = await usernames(request, party["member_ids"]) or {}
    classes = party.get("classes", {})
    party["members"] = [
        {"user_id": i, "username": names.get(i), "class_id": classes.get(i)} for i in party["member_ids"]
    ]
    return JSONResponse(status_code=answer.status_code, content=party)


# --- Profiles ---


@router.get("/profiles/{user_id}")
async def get_profile(user_id: uuid.UUID, request: Request) -> Response:
    answer = await send(request, f"/profiles/{user_id}", "GET")
    if isinstance(answer, Response) or not answer.is_success:
        return answer if isinstance(answer, Response) else upstream.relay(answer)
    names = await usernames(request, [str(user_id)])
    if names is None:
        return upstream.unavailable("auth")
    # Social returns a default profile for any id: only Auth knows which accounts exist.
    if str(user_id) not in names:
        return JSONResponse(status_code=404, content={"error": "user_not_found", "message": "This player doesn't exist."})
    return JSONResponse(content={**answer.json(), "username": names[str(user_id)]})


@router.patch("/profiles/me")
async def update_my_profile(request: Request) -> Response:
    return await forward(request, "/profiles/me", "PATCH")


# --- Friends ---


@router.get("/friends")
async def list_friends(request: Request) -> Response:
    """Friends and pending requests, each with `username` (null if Auth is unreachable)."""
    answer = await send(request, "/friends", "GET")
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
    return await forward(request, "/friends/requests")


@router.post("/friends/requests/{request_id}/accept")
async def accept_friend_request(request_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/friends/requests/{request_id}/accept")


@router.post("/friends/requests/{request_id}/decline")
async def decline_friend_request(request_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/friends/requests/{request_id}/decline")


@router.delete("/friends/{friend_id}")
async def remove_friend(friend_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/friends/{friend_id}", "DELETE")


# --- Parties ---


@router.post("/parties")
async def create_party(request: Request) -> Response:
    return await party_with_names(request, "/parties", "POST")


@router.get("/parties/{party_id}")
async def get_party(party_id: uuid.UUID, request: Request) -> Response:
    return await party_with_names(request, f"/parties/{party_id}", "GET")


@router.post("/parties/{party_id}/invite")
async def invite(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/invite")


@router.post("/invites/{invite_id}/accept")
async def accept_invite(invite_id: InviteId, request: Request) -> Response:
    return await party_with_names(request, f"/invites/{invite_id}/accept", "POST")


@router.post("/parties/{party_id}/leave")
async def leave(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/leave")


@router.post("/parties/{party_id}/kick")
async def kick(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/kick")


@router.post("/parties/{party_id}/leader")
async def give_lead(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/leader")


@router.put("/parties/{party_id}/class")
async def set_class(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/class", "PUT")


@router.post("/parties/{party_id}/launch")
async def launch(party_id: uuid.UUID, request: Request) -> Response:
    return await forward(request, f"/parties/{party_id}/launch")
