import json

from tpsarena_common.export_schemas import OUTPUT, export


def test_committed_schema_is_up_to_date():
    # A changed event model must come with a re-exported contract (python -m tpsarena_common.export_schemas).
    assert OUTPUT.read_text() == export()


def test_schema_describes_data_without_event_id():
    contract = json.loads(export())
    match_ready = contract["events"]["party.match_ready"]
    assert match_ready["stream"] == "social.events"
    assert "event_id" not in match_ready["schema"]["properties"]
    assert set(match_ready["schema"]["required"]) >= {"party_id", "server_id", "address", "port", "tickets"}
    assert contract["events"]["player.match"]["stream"] == "player.match"
