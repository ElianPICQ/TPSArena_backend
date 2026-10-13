from tpsarena_common.events import MatchPlayerResult

from stats.progression import level_for, xp_for, xp_to_next


def test_level_curve():
    assert xp_to_next(1) == 1000
    assert xp_to_next(2) == 1250
    assert level_for(0) == (1, 0)
    assert level_for(999) == (1, 999)
    assert level_for(1000) == (2, 0)
    assert level_for(2249) == (2, 1249)
    assert level_for(2250) == (3, 0)


def test_xp_per_match():
    import uuid

    uid = uuid.uuid4()
    assert xp_for(MatchPlayerResult(user_id=uid, kills=0, deaths=3, placement=5)) == 100
    assert xp_for(MatchPlayerResult(user_id=uid, kills=7, deaths=0, placement=1)) == 100 + 70 + 200
