import uuid

import httpx
import respx

from web_bff.config import get_settings

STATS = get_settings().stats_url
AUTH = get_settings().auth_url
TOKEN = {"authorization": "Bearer abc"}


def names(known: dict[str, str]):
    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["ids"].split(",")
        return httpx.Response(200, json={"users": [{"user_id": i, "username": known[i]} for i in ids if i in known]})

    return answer


@respx.mock
async def test_leaderboard_gets_names_and_passes_query(client):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    board = {"type": "kills", "total_players": 2, "entries": [
        {"rank": 1, "user_id": a, "value": 50}, {"rank": 2, "user_id": b, "value": 10}]}
    route = respx.get(f"{STATS}/leaderboards/kills").mock(return_value=httpx.Response(200, json=board))
    respx.get(f"{AUTH}/users").mock(side_effect=names({a: "alice", b: "bob"}))

    r = await client.get("/leaderboards/kills", params={"limit": 2}, headers=TOKEN)
    assert [e["username"] for e in r.json()["entries"]] == ["alice", "bob"]
    assert route.calls.last.request.url.params["limit"] == "2"
    assert route.calls.last.request.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_around(client):
    a = str(uuid.uuid4())
    board = {"type": "xp", "total_players": 1, "player_rank": 1, "entries": [{"rank": 1, "user_id": a, "value": 5}]}
    respx.get(f"{STATS}/leaderboards/xp/around/{a}").mock(return_value=httpx.Response(200, json=board))
    respx.get(f"{AUTH}/users").mock(side_effect=names({a: "alice"}))
    r = await client.get(f"/leaderboards/xp/around/{a}", headers=TOKEN)
    assert r.json()["entries"][0]["username"] == "alice"


@respx.mock
async def test_match_with_deleted_player(client):
    a = str(uuid.uuid4())
    match_id = str(uuid.uuid4())
    match = {"match_id": match_id, "map": "arena", "started_at": "x", "ended_at": "y", "players": [
        {"user_id": a, "kills": 1, "deaths": 0, "placement": 1, "xp_gained": 310},
        {"user_id": None, "kills": 0, "deaths": 1, "placement": 2, "xp_gained": 100}]}
    respx.get(f"{STATS}/matches/{match_id}").mock(return_value=httpx.Response(200, json=match))
    auth = respx.get(f"{AUTH}/users").mock(side_effect=names({a: "alice"}))

    r = await client.get(f"/matches/{match_id}", headers=TOKEN)
    assert [p["username"] for p in r.json()["players"]] == ["alice", None]
    assert auth.calls.last.request.url.params["ids"] == a  # null ids aren't sent


@respx.mock
async def test_forwarded_and_errors(client):
    user_id = str(uuid.uuid4())
    respx.get(f"{STATS}/players/{user_id}/stats").mock(return_value=httpx.Response(200, json={"wins": 3}))
    respx.get(f"{STATS}/progression/{user_id}").mock(return_value=httpx.Response(200, json={"level": 2}))
    respx.get(f"{STATS}/leaderboards/deaths").mock(return_value=httpx.Response(422, json={"detail": []}))

    assert (await client.get(f"/players/{user_id}/stats", headers=TOKEN)).json() == {"wins": 3}
    assert (await client.get(f"/progression/{user_id}", headers=TOKEN)).json() == {"level": 2}
    assert (await client.get("/leaderboards/deaths", headers=TOKEN)).status_code == 422
    assert (await client.get("/leaderboards/Bad..Type", headers=TOKEN)).status_code == 422


@respx.mock
async def test_stats_down(client):
    respx.get(f"{STATS}/leaderboards/xp").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.get("/leaderboards/xp", headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (503, "stats_unavailable")


@respx.mock
async def test_match_history_passes_cursor(client):
    user_id = str(uuid.uuid4())
    page = {"matches": [], "next_cursor": None}
    route = respx.get(f"{STATS}/players/{user_id}/matches").mock(return_value=httpx.Response(200, json=page))
    r = await client.get(f"/players/{user_id}/matches", params={"limit": 10, "before": "abc="}, headers=TOKEN)
    assert r.json() == page
    assert dict(route.calls.last.request.url.params) == {"limit": "10", "before": "abc="}
