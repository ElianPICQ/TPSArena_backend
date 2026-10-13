import uuid

import httpx
import respx

from bff.config import get_settings

SERVERLIST = get_settings().serverlist_url


@respx.mock
async def test_list_forwards_query_and_token(client: httpx.AsyncClient):
    servers = {"servers": [{"server_id": str(uuid.uuid4()), "name": "EU #1"}]}
    route = respx.get(f"{SERVERLIST}/servers").mock(return_value=httpx.Response(200, json=servers))
    r = await client.get("/servers", params={"build_version": "0.1.0"}, headers={"authorization": "Bearer abc"})
    assert r.status_code == 200
    assert r.json() == servers
    sent = route.calls.last.request
    assert sent.url.params["build_version"] == "0.1.0"
    assert sent.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_join_ticket(client: httpx.AsyncClient):
    server_id = uuid.uuid4()
    ticket = {"ticket": "t", "address": "203.0.113.10", "port": 7000, "expires_in": 30}
    route = respx.post(f"{SERVERLIST}/servers/{server_id}/join-ticket").mock(
        return_value=httpx.Response(201, json=ticket)
    )
    r = await client.post(f"/servers/{server_id}/join-ticket", headers={"authorization": "Bearer abc"})
    assert r.status_code == 201
    assert r.json() == ticket
    assert route.calls.last.request.headers["authorization"] == "Bearer abc"


async def test_join_ticket_rejects_bad_server_id(client: httpx.AsyncClient):
    # Validated by the BFF so nothing odd ends up in the upstream URL.
    r = await client.post("/servers/..%2Fallocations/join-ticket")
    assert r.status_code in (404, 422)


@respx.mock
async def test_serverlist_unreachable(client: httpx.AsyncClient):
    respx.get(f"{SERVERLIST}/servers").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.get("/servers", params={"build_version": "0.1.0"})
    assert r.status_code == 503
    assert r.json()["error"] == "serverlist_unavailable"
