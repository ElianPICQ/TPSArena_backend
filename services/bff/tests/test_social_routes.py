import uuid

import httpx
import pytest
import respx

from bff.config import get_settings

SOCIAL = get_settings().social_url
AUTH = get_settings().auth_url
TOKEN = {"authorization": "Bearer abc"}


def auth_names(names: dict[str, str]):
    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params["ids"].split(",")
        return httpx.Response(200, json={"users": [{"user_id": i, "username": names[i]} for i in ids if i in names]})

    return answer


@respx.mock
async def test_profile_gets_username(client):
    user_id = str(uuid.uuid4())
    respx.get(f"{SOCIAL}/profiles/{user_id}").mock(
        return_value=httpx.Response(200, json={"user_id": user_id, "bio": "hi", "avatar": "scout"})
    )
    auth = respx.get(f"{AUTH}/users").mock(side_effect=auth_names({user_id: "alice"}))

    r = await client.get(f"/profiles/{user_id}", headers=TOKEN)
    assert r.json() == {"user_id": user_id, "bio": "hi", "avatar": "scout", "username": "alice"}
    assert auth.calls.last.request.headers["authorization"] == "Bearer abc"


@respx.mock
async def test_profile_of_unknown_player_is_404(client):
    user_id = str(uuid.uuid4())
    respx.get(f"{SOCIAL}/profiles/{user_id}").mock(
        return_value=httpx.Response(200, json={"user_id": user_id, "bio": "", "avatar": "default"})
    )
    respx.get(f"{AUTH}/users").mock(side_effect=auth_names({}))
    r = await client.get(f"/profiles/{user_id}", headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (404, "user_not_found")


@respx.mock
async def test_profile_with_auth_down_is_503(client):
    user_id = str(uuid.uuid4())
    respx.get(f"{SOCIAL}/profiles/{user_id}").mock(
        return_value=httpx.Response(200, json={"user_id": user_id, "bio": "", "avatar": "default"})
    )
    respx.get(f"{AUTH}/users").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.get(f"/profiles/{user_id}", headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (503, "auth_unavailable")


@respx.mock
async def test_social_errors_pass_through_without_calling_auth(client):
    respx.get(f"{SOCIAL}/friends").mock(return_value=httpx.Response(401, json={"error": "token_expired", "message": "."}))
    auth = respx.get(f"{AUTH}/users")
    r = await client.get("/friends", headers=TOKEN)
    assert (r.status_code, r.json()["error"]) == (401, "token_expired")
    assert not auth.called


@respx.mock
async def test_friends_get_usernames(client):
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    listing = {
        "friends": [{"user_id": a, "since": "2026-10-13T00:00:00Z"}],
        "incoming": [{"request_id": str(uuid.uuid4()), "user_id": b, "created_at": "2026-10-13T00:00:00Z"}],
        "outgoing": [{"request_id": str(uuid.uuid4()), "user_id": c, "created_at": "2026-10-13T00:00:00Z"}],
    }
    respx.get(f"{SOCIAL}/friends").mock(return_value=httpx.Response(200, json=listing))
    auth = respx.get(f"{AUTH}/users").mock(side_effect=auth_names({a: "alice", b: "bob"}))

    r = await client.get("/friends", headers=TOKEN)
    body = r.json()
    assert body["friends"][0]["username"] == "alice"
    assert body["incoming"][0]["username"] == "bob"
    assert body["outgoing"][0]["username"] is None  # unknown to Auth
    assert auth.call_count == 1  # one batch for everyone


@respx.mock
async def test_friends_still_listed_when_auth_is_down(client):
    a = str(uuid.uuid4())
    listing = {"friends": [{"user_id": a, "since": "2026-10-13T00:00:00Z"}], "incoming": [], "outgoing": []}
    respx.get(f"{SOCIAL}/friends").mock(return_value=httpx.Response(200, json=listing))
    respx.get(f"{AUTH}/users").mock(side_effect=httpx.ConnectError("refused"))
    r = await client.get("/friends", headers=TOKEN)
    assert r.status_code == 200
    assert r.json()["friends"][0]["username"] is None


@pytest.mark.parametrize(
    ("method", "path", "upstream_path"),
    [
        ("POST", "/parties", "/parties"),
        ("GET", "/parties/{party}", "/parties/{party}"),
        ("POST", "/invites/abc_DEF-1/accept", "/invites/abc_DEF-1/accept"),
    ],
)
@respx.mock
async def test_party_answers_get_member_names(client, method, path, upstream_path):
    party_id = str(uuid.uuid4())
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    party = {"party_id": party_id, "leader_id": a, "status": "lobby", "member_ids": [a, b], "version": 2}
    respx.request(method, SOCIAL + upstream_path.format(party=party_id)).mock(
        return_value=httpx.Response(200, json=party)
    )
    respx.get(f"{AUTH}/users").mock(side_effect=auth_names({a: "alice", b: "bob"}))

    r = await client.request(method, path.format(party=party_id), headers=TOKEN)
    assert r.json()["members"] == [{"user_id": a, "username": "alice"}, {"user_id": b, "username": "bob"}]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/profiles/me"),
        ("POST", "/friends/requests"),
        ("POST", "/friends/requests/{id}/accept"),
        ("POST", "/friends/requests/{id}/decline"),
        ("DELETE", "/friends/{id}"),
        ("POST", "/parties/{id}/invite"),
        ("POST", "/parties/{id}/leave"),
        ("POST", "/parties/{id}/kick"),
        ("POST", "/parties/{id}/launch"),
    ],
)
@respx.mock
async def test_forwarded_routes(client, method, path):
    path = path.format(id=uuid.uuid4())
    route = respx.request(method, SOCIAL + path).mock(return_value=httpx.Response(204))
    r = await client.request(method, path, json={"x": 1}, headers=TOKEN)
    assert r.status_code == 204
    sent = route.calls.last.request
    assert sent.headers["authorization"] == "Bearer abc"
    assert sent.content == b'{"x":1}'


async def test_ids_are_validated_before_forwarding(client):
    assert (await client.get("/parties/not-a-uuid")).status_code == 422
    assert (await client.post("/invites/bad%20id/accept")).status_code == 422
