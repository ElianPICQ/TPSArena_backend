"""Calls to other services, for when Social needs an answer right away."""

import uuid

import httpx

from social.config import get_settings
from social.errors import APIError

_auth: httpx.AsyncClient | None = None
_serverlist: httpx.AsyncClient | None = None


def auth_client() -> httpx.AsyncClient:
    global _auth
    if _auth is None:
        settings = get_settings()
        _auth = httpx.AsyncClient(base_url=settings.auth_url, timeout=settings.upstream_timeout_seconds)
    return _auth


def serverlist_client() -> httpx.AsyncClient:
    global _serverlist
    if _serverlist is None:
        settings = get_settings()
        _serverlist = httpx.AsyncClient(
            base_url=settings.serverlist_url,
            timeout=settings.upstream_timeout_seconds,
            headers={"authorization": f"Bearer {settings.social_service_secret}"},
        )
    return _serverlist


async def close() -> None:
    for client in (_auth, _serverlist):
        if client is not None:
            await client.aclose()


async def user_exists(user_id: uuid.UUID, authorization: str) -> bool:
    """Ask Auth, with the calling player's token (Social has no account list of its own)."""
    try:
        response = await auth_client().get(
            "/users", params={"ids": str(user_id)}, headers={"authorization": authorization}
        )
        response.raise_for_status()
    except httpx.HTTPError:
        raise APIError(503, "auth_unavailable", "Can't check this player right now, try again.")
    return any(u["user_id"] == str(user_id) for u in response.json()["users"])


async def allocate_server(
    party_id: str,
    member_ids: list[uuid.UUID],
    leader_id: uuid.UUID,
    build_version: str,
    region: str | None,
    match_settings: dict,
) -> dict:
    """Reserve a game server for the party, which gets `match_settings` and the leader (who starts
    the match from the lobby) with the reservation. Returns the Server List allocation (address,
    port, tickets)."""
    body = {
        "party_id": party_id,
        "member_ids": [str(m) for m in member_ids],
        "leader_id": str(leader_id),
        "build_version": build_version,
        "region": region,
        "match_settings": match_settings,
    }
    try:
        response = await serverlist_client().post("/allocations", json=body)
    except httpx.RequestError:
        raise APIError(503, "serverlist_unavailable", "Can't reach the server list, try again.")
    if response.status_code == 503:
        raise APIError(503, "no_server_available", "No game server is available, try again shortly.")
    if response.status_code != 201:
        raise APIError(502, "allocation_failed", "The server list refused the allocation.")
    return response.json()
