import uuid

import httpx
import respx

from bff.config import get_settings

REALTIME = get_settings().realtime_url
TOKEN = {"authorization": "Bearer abc"}


@respx.mock
async def test_presence_forwards_ids_and_token(client):
    answer = {"presence": [{"user_id": "u", "status": "online", "activity": "menu"}]}
    route = respx.get(f"{REALTIME}/presence").mock(return_value=httpx.Response(200, json=answer))
    r = await client.get("/presence", params={"ids": "a,b"}, headers=TOKEN)
    assert r.json() == answer
    assert route.calls.last.request.url.params["ids"] == "a,b"
    assert route.calls.last.request.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_conversation_passes_cursor(client):
    friend = uuid.uuid4()
    page = {"messages": [], "next_cursor": None}
    route = respx.get(f"{REALTIME}/conversations/{friend}/messages").mock(return_value=httpx.Response(200, json=page))
    r = await client.get(f"/conversations/{friend}/messages", params={"limit": 5, "before": "42"}, headers=TOKEN)
    assert r.json() == page
    assert dict(route.calls.last.request.url.params) == {"limit": "5", "before": "42"}


@respx.mock
async def test_realtime_down(client):
    respx.get(f"{REALTIME}/presence").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.get("/presence", params={"ids": "a"}, headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (503, "realtime_unavailable")


async def test_conversation_id_is_validated(client):
    assert (await client.get("/conversations/not-a-uuid/messages")).status_code == 422
