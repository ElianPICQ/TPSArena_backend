from .conftest import new_player

CLASS = {"slot": 0, "name": "  Assaut  ", "primary_weapon": "smg", "secondary_weapon": "laser"}


async def test_no_loadouts_by_default(client):
    r = await client.get("/loadouts/me", headers=new_player().headers)
    assert r.status_code == 200
    assert r.json() == {"loadouts": []}


async def test_replace_my_loadouts(client):
    me, other = new_player(), new_player()
    second = {"slot": 3, "name": "Démolition", "primary_weapon": "rocket", "secondary_weapon": "poop"}
    r = await client.put("/loadouts/me", json={"loadouts": [second, CLASS]}, headers=me.headers)
    assert r.status_code == 200
    expected = [{**CLASS, "name": "Assaut"}, second]
    assert r.json() == {"loadouts": expected}
    assert (await client.get("/loadouts/me", headers=me.headers)).json() == {"loadouts": expected}

    # The whole list is replaced; other players are untouched.
    await client.put("/loadouts/me", json={"loadouts": [second]}, headers=me.headers)
    assert (await client.get("/loadouts/me", headers=me.headers)).json() == {"loadouts": [second]}
    assert (await client.get("/loadouts/me", headers=other.headers)).json() == {"loadouts": []}


async def test_loadout_validation(client):
    me = new_player()

    async def put(loadouts):
        return (await client.put("/loadouts/me", json={"loadouts": loadouts}, headers=me.headers)).status_code

    assert await put([{**CLASS, "primary_weapon": "katana"}]) == 422      # a secondary as primary
    assert await put([{**CLASS, "secondary_weapon": "bazooka"}]) == 422   # unknown weapon
    assert await put([{**CLASS, "name": "   "}]) == 422
    assert await put([{**CLASS, "name": "x" * 25}]) == 422
    assert await put([{**CLASS, "slot": 10}]) == 422
    assert await put([CLASS, CLASS]) == 422                              # same slot twice
    assert await put([{**CLASS, "slot": i} for i in range(11)]) == 422   # more than 10
    assert await put([{**CLASS, "slot": i} for i in range(10)]) == 200


async def test_loadouts_need_a_token(client):
    assert (await client.get("/loadouts/me")).status_code == 401
    assert (await client.put("/loadouts/me", json={"loadouts": []})).status_code == 401
