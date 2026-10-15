import uuid

from fastapi import APIRouter, Response, status
from tpsarena_common.events import PartyMatchReady

from social import clients, parties
from social.config import get_settings
from social.deps import CurrentUserId, Session
from social.errors import APIError
from social.routes.friends import are_friends
from social.schemas import (
    ClassRequest,
    ErrorResponse,
    InviteResponse,
    LaunchRequest,
    LaunchResponse,
    PartyResponse,
    UserIdRequest,
)

router = APIRouter(tags=["parties"])

ERRORS = {
    400: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
}


def party_response(party: parties.Party) -> PartyResponse:
    return PartyResponse(
        party_id=party.party_id,
        leader_id=party.leader_id,
        status=party.status,
        member_ids=party.member_ids,
        version=party.version,
        classes=party.classes,
    )


async def party_of_member(party_id: str, user_id: uuid.UUID) -> parties.Party:
    """The party, if the caller is in it. Non-members get the same 404 as for a missing party."""
    party = await parties.get_party(party_id)
    if party is None or user_id not in party.member_ids:
        raise APIError(404, "party_not_found", "This party doesn't exist or you aren't in it.")
    return party


def raise_for(result: str) -> None:
    errors = {
        "not_found": APIError(404, "party_not_found", "This party doesn't exist or you aren't in it."),
        "not_member": APIError(404, "not_member", "This player isn't in the party."),
        "not_leader": APIError(403, "not_leader", "Only the party leader can do this."),
        "already_in_party": APIError(409, "already_in_party", "You are already in a party."),
        "not_in_lobby": APIError(409, "not_in_lobby", "The party is in a match."),
        "wrong_status": APIError(409, "not_in_lobby", "The party isn't in the lobby."),
        "party_full": APIError(409, "party_full", "The party is full."),
        "invalid_invite": APIError(404, "invite_not_found", "This invite doesn't exist or has expired."),
        "party_gone": APIError(404, "invite_not_found", "This invite doesn't exist or has expired."),
    }
    if result in errors:
        raise errors[result]


@router.post("/parties", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def create_party(user_id: CurrentUserId) -> PartyResponse:
    """A solo player is a party of 1: create one to play, even alone."""
    try:
        party_id = await parties.create(user_id)
    except ValueError as exc:
        raise_for(str(exc))
        raise
    return party_response(await parties.get_party(party_id))


@router.get("/parties/{party_id}", responses=ERRORS)
async def get_party(party_id: str, user_id: CurrentUserId) -> PartyResponse:
    return party_response(await party_of_member(party_id, user_id))


@router.post("/parties/{party_id}/invite", status_code=status.HTTP_201_CREATED, responses=ERRORS)
async def invite(party_id: str, body: UserIdRequest, user_id: CurrentUserId, session: Session) -> InviteResponse:
    """Any member can invite one of their friends while the party is in the lobby and not full."""
    settings = get_settings()
    party = await party_of_member(party_id, user_id)
    target = body.user_id
    if target == user_id:
        raise APIError(400, "cannot_invite_self", "You can't invite yourself.")
    if target in party.member_ids:
        raise APIError(409, "already_member", "This player is already in the party.")
    if party.status != "lobby":
        raise_for("not_in_lobby")
    if len(party.member_ids) >= settings.max_party_size:
        raise_for("party_full")
    if not await are_friends(session, user_id, target):
        raise APIError(403, "not_friends", "You can only invite your friends.")

    invite_id, expires_at = await parties.create_invite(party_id, user_id, target, settings.invite_ttl_seconds)
    return InviteResponse(invite_id=invite_id, expires_at=expires_at)


@router.post("/invites/{invite_id}/accept", responses=ERRORS)
async def accept_invite(invite_id: str, user_id: CurrentUserId) -> PartyResponse:
    """Joining another party leaves the one you are in."""
    invite_data = await parties.get_invite(invite_id)
    if invite_data is None or invite_data["to"] != str(user_id):
        raise_for("invalid_invite")
    party_id = invite_data["party_id"]

    current = await parties.current_party_id(user_id)
    if current == party_id:
        raise APIError(409, "already_member", "You are already in this party.")
    if current is not None:
        await parties.remove_member(current, user_id)

    result = await parties.join(invite_id, party_id, user_id, get_settings().max_party_size)
    raise_for(result)
    return party_response(await parties.get_party(party_id))


@router.post("/parties/{party_id}/leave", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def leave(party_id: str, user_id: CurrentUserId) -> Response:
    """If the leader leaves, another member becomes leader; the last member leaving disbands it."""
    result = await parties.remove_member(party_id, user_id)
    if result == "not_member":
        raise_for("not_found")
    raise_for(result)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/parties/{party_id}/kick", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def kick(party_id: str, body: UserIdRequest, user_id: CurrentUserId) -> Response:
    await party_of_member(party_id, user_id)
    if body.user_id == user_id:
        raise APIError(400, "cannot_kick_self", "Leave the party instead.")
    raise_for(await parties.remove_member(party_id, body.user_id, required_leader=user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/parties/{party_id}/leader", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def give_lead(party_id: str, body: UserIdRequest, user_id: CurrentUserId) -> Response:
    """Leader only: make another member the leader."""
    await party_of_member(party_id, user_id)
    raise_for(await parties.set_leader(party_id, user_id, body.user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/parties/{party_id}/class", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS)
async def set_class(party_id: str, body: ClassRequest, user_id: CurrentUserId) -> Response:
    """The class the caller plays, shown to the other members."""
    raise_for(await parties.set_class(party_id, user_id, body.class_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/parties/{party_id}/launch", responses={**ERRORS, 503: {"model": ErrorResponse}})
async def launch(party_id: str, body: LaunchRequest, user_id: CurrentUserId) -> LaunchResponse:
    """Leader only: reserve a fresh game server for the whole party (always a new match)."""
    party = await party_of_member(party_id, user_id)
    raise_for(await parties.set_status(party_id, "lobby", "launching", required_leader=user_id))

    try:
        allocation = await clients.allocate_server(
            party_id, party.member_ids, user_id, body.build_version, body.region, body.match_settings.model_dump()
        )
    except APIError:
        await parties.set_status(party_id, "launching", "lobby")
        raise

    match_ready = PartyMatchReady(
        party_id=party_id,
        server_id=allocation["server_id"],
        address=allocation["address"],
        port=allocation["port"],
        tickets=allocation["tickets"],
    )
    result = await parties.set_status(
        party_id, "launching", "in_match", server_id=allocation["server_id"], then=match_ready
    )
    if result != "ok":
        # The party changed during the allocation (e.g. everyone left): the reserved server
        # goes back to idle once its reservation runs out.
        raise APIError(409, "party_changed", "The party changed while the match was being prepared.")
    return LaunchResponse(
        server_id=allocation["server_id"],
        address=allocation["address"],
        port=allocation["port"],
        ticket=allocation["tickets"][str(user_id)],
    )
