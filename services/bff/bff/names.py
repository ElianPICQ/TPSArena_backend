"""Player names come from Auth (`GET /users?ids=`); the other services only know user ids."""

import logging

import httpx
from fastapi import Request

log = logging.getLogger(__name__)

BATCH = 100  # Auth's limit per request


async def usernames(request: Request, user_ids: list[str]) -> dict[str, str] | None:
    """user_id -> username for the ids that exist. None if Auth can't be reached."""
    client: httpx.AsyncClient = request.app.state.auth_client
    headers = {"authorization": request.headers.get("authorization", "")}
    unique = list(dict.fromkeys(user_ids))
    names: dict[str, str] = {}
    try:
        for start in range(0, len(unique), BATCH):
            ids = ",".join(unique[start : start + BATCH])
            response = await client.get("/users", params={"ids": ids}, headers=headers)
            response.raise_for_status()
            names.update({u["user_id"]: u["username"] for u in response.json()["users"]})
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("Could not fetch usernames: %s", exc)
        return None
    return names
