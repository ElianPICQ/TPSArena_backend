import uuid

import pytest
from pydantic import ValidationError

from tpsarena_common.events import PartyMatchReady, PartyUpdated, UnknownEvent, UserDeleted, decode, encode


def test_roundtrip_with_nested_data():
    user_id = uuid.uuid4()
    e = PartyMatchReady(party_id="p", server_id=uuid.uuid4(), address="127.0.0.1", port=7000, tickets={user_id: "t"})
    fields = encode(e)
    assert set(fields) == {"event_id", "type", "data"}
    assert fields["type"] == "party.match_ready"
    assert all(isinstance(v, str) for v in fields.values())
    assert decode(fields) == e
    assert PartyMatchReady.STREAM == "social.events"


def test_each_event_keeps_its_own_stream_and_type():
    assert (UserDeleted.STREAM, UserDeleted.TYPE) == ("user.deleted", "user.deleted")
    assert (PartyUpdated.STREAM, PartyUpdated.TYPE) == ("social.events", "party.updated")


def test_unknown_type():
    with pytest.raises(UnknownEvent):
        decode({"event_id": str(uuid.uuid4()), "type": "nope", "data": "{}"})


def test_bad_payload():
    with pytest.raises(ValidationError):
        decode({"event_id": str(uuid.uuid4()), "type": "user.deleted", "data": '{"user_id": "not-a-uuid"}'})


def test_match_finished_rejects_duplicate_players():
    from datetime import UTC, datetime

    from tpsarena_common.events import MatchFinished

    user_id = uuid.uuid4()
    result = {"user_id": user_id, "kills": 1, "deaths": 0, "placement": 1}
    with pytest.raises(ValidationError):
        MatchFinished(
            match_id=uuid.uuid4(),
            server_id=uuid.uuid4(),
            map="arena",
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            players=[result, result],
        )
