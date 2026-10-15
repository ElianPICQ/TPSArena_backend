import json
import uuid

import httpx
import respx

from web_bff.config import get_settings

SOCIAL = get_settings().social_url
AUTH = get_settings().auth_url
REALTIME = get_settings().realtime_url
TOKEN = {"authorization": "Bearer abc"}


@respx.mock
async def test_friends_get_names(client):
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    listing = {
        "friends": [{"user_id": a, "since": "x"}],
        "incoming": [{"request_id": str(uuid.uuid4()), "user_id": b, "created_at": "x"}],
        "outgoing": [{"request_id": str(uuid.uuid4()), "user_id": c, "created_at": "x"}],
    }
    respx.get(f"{SOCIAL}/friends").mock(return_value=httpx.Response(200, json=listing))
    respx.get(f"{AUTH}/users").mock(
        return_value=httpx.Response(200, json={"users": [{"user_id": a, "username": "alice"}, {"user_id": b, "username": "bob"}]})
    )

    body = (await client.get("/friends", headers=TOKEN)).json()
    assert body["friends"][0]["username"] == "alice"
    assert body["incoming"][0]["username"] == "bob"
    assert body["outgoing"][0]["username"] is None  # unknown to Auth


@respx.mock
async def test_friend_actions_are_forwarded(client):
    target, request_id = str(uuid.uuid4()), str(uuid.uuid4())
    send = respx.post(f"{SOCIAL}/friends/requests").mock(
        return_value=httpx.Response(201, json={"status": "pending", "request_id": request_id})
    )
    accept = respx.post(f"{SOCIAL}/friends/requests/{request_id}/accept").mock(return_value=httpx.Response(204))
    decline = respx.post(f"{SOCIAL}/friends/requests/{request_id}/decline").mock(return_value=httpx.Response(204))
    remove = respx.delete(f"{SOCIAL}/friends/{target}").mock(
        return_value=httpx.Response(404, json={"error": "not_friends", "message": "..."})
    )

    r = await client.post("/friends/requests", json={"user_id": target}, headers=TOKEN)
    assert (r.status_code, r.json()["status"]) == (201, "pending")
    assert json.loads(send.calls.last.request.content) == {"user_id": target}
    assert send.calls.last.request.headers["authorization"] == "Bearer abc"
    assert (await client.post(f"/friends/requests/{request_id}/accept", headers=TOKEN)).status_code == 204
    assert (await client.post(f"/friends/requests/{request_id}/decline", headers=TOKEN)).status_code == 204
    r = await client.delete(f"/friends/{target}", headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (404, "not_friends")
    assert accept.called and decline.called and remove.called


@respx.mock
async def test_user_search_passes_query(client):
    route = respx.get(f"{AUTH}/users/search").mock(return_value=httpx.Response(200, json={"users": []}))
    r = await client.get("/users/search", params={"username": "ali"}, headers=TOKEN)
    assert r.status_code == 200
    assert route.calls.last.request.url.params["username"] == "ali"


@respx.mock
async def test_presence_and_history(client):
    friend = str(uuid.uuid4())
    presence = respx.get(f"{REALTIME}/presence").mock(return_value=httpx.Response(200, json={"presence": []}))
    history = respx.get(f"{REALTIME}/conversations/{friend}/messages").mock(
        return_value=httpx.Response(200, json={"messages": [], "next_cursor": None})
    )
    assert (await client.get("/presence", params={"ids": friend}, headers=TOKEN)).json() == {"presence": []}
    assert presence.calls.last.request.url.params["ids"] == friend
    r = await client.get(f"/conversations/{friend}/messages", params={"limit": 30, "before": "12"}, headers=TOKEN)
    assert r.json() == {"messages": [], "next_cursor": None}
    assert dict(history.calls.last.request.url.params) == {"limit": "30", "before": "12"}


@respx.mock
async def test_social_and_realtime_down(client):
    respx.get(f"{SOCIAL}/friends").mock(side_effect=httpx.ConnectError("refused"))
    respx.get(f"{REALTIME}/presence").mock(side_effect=httpx.ConnectError("refused"))
    assert (await client.get("/friends", headers=TOKEN)).json()["error"] == "social_unavailable"
    assert (await client.get("/presence", headers=TOKEN)).json()["error"] == "realtime_unavailable"
