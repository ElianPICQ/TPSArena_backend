from .conftest import new_player


async def test_default_profile(client):
    me, other = new_player(), new_player()
    r = await client.get(f"/profiles/{other.user_id}", headers=me.headers)
    assert r.status_code == 200
    assert r.json() == {"user_id": str(other.user_id), "bio": "", "avatar": "default"}


async def test_update_my_profile(client):
    me, other = new_player(), new_player()
    r = await client.patch("/profiles/me", json={"bio": "  Sniper main  "}, headers=me.headers)
    assert r.status_code == 200
    assert r.json()["bio"] == "Sniper main"

    r = await client.patch("/profiles/me", json={"avatar": "scout"}, headers=me.headers)
    assert r.json() == {"user_id": str(me.user_id), "bio": "Sniper main", "avatar": "scout"}
    assert (await client.get(f"/profiles/{me.user_id}", headers=other.headers)).json()["avatar"] == "scout"


async def test_profile_validation(client):
    me = new_player()
    assert (await client.patch("/profiles/me", json={"avatar": "unknown"}, headers=me.headers)).status_code == 422
    assert (await client.patch("/profiles/me", json={"bio": "x" * 201}, headers=me.headers)).status_code == 422


async def test_profiles_need_a_token(client):
    r = await client.get(f"/profiles/{new_player().user_id}")
    assert r.status_code == 401
