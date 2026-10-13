"""Export every event format as JSON Schema: the contract for services not written in Python.

    python -m tpsarena_common.export_schemas            # writes schemas/events.json
    python -m tpsarena_common.export_schemas --check    # fails if the file is out of date

The file maps each event type to its stream and the JSON Schema of its `data` field (the model
without `event_id`, which travels as its own stream field). It is committed and released with
the package; e.g. the Real-time service (TypeScript) generates its types from it.
"""

import json
import sys
from pathlib import Path

from tpsarena_common.events import EVENT_TYPES

OUTPUT = Path(__file__).resolve().parent.parent / "schemas" / "events.json"


def data_schema(model) -> dict:
    schema = model.model_json_schema()
    schema["properties"].pop("event_id", None)
    if "event_id" in schema.get("required", []):
        schema["required"].remove("event_id")
    schema["additionalProperties"] = False
    return schema


def export() -> str:
    contract = {
        "package": "tpsarena-common",  # the version is the git tag it's read from (common-vX.Y.Z)
        "envelope": {
            "fields": ["event_id", "type", "data"],
            "description": "Stream entries: event_id (UUID string), type (key below), data (JSON matching the schema).",
        },
        "events": {
            type_: {"stream": model.STREAM, "schema": data_schema(model)}
            for type_, model in sorted(EVENT_TYPES.items())
        },
    }
    return json.dumps(contract, indent=2, sort_keys=False) + "\n"


def main() -> None:
    content = export()
    if "--check" in sys.argv:
        if not OUTPUT.exists() or OUTPUT.read_text() != content:
            sys.exit(f"{OUTPUT} is out of date: run python -m tpsarena_common.export_schemas")
        return
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(content)
    print(f"Wrote {OUTPUT} ({len(EVENT_TYPES)} event types)")


if __name__ == "__main__":
    main()
