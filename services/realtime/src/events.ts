// Stream events in the format shared with the Python services (tpsarena_common.events):
// fields event_id, type, data (JSON). Validated against the contract (contracts/events.json),
// like pydantic validates them in Python.
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Ajv, type ValidateFunction } from "ajv";
import addFormatsModule from "ajv-formats";
import type { Redis } from "ioredis";
import { EVENT_STREAMS, type EventDataByType, type EventType } from "./generated/events.js";

const addFormats = addFormatsModule as unknown as (ajv: Ajv) => Ajv;

const contractPath = join(dirname(fileURLToPath(import.meta.url)), "..", "contracts", "events.json");
const contract = JSON.parse(readFileSync(contractPath, "utf8")) as {
  events: Record<string, { stream: string; schema: object }>;
};

const ajv = new Ajv({ strict: false });
addFormats(ajv);
const validators = new Map<string, ValidateFunction>(
  Object.entries(contract.events).map(([type, { schema }]) => [type, ajv.compile(schema)]),
);

export interface StreamEvent<T extends EventType = EventType> {
  eventId: string;
  type: T;
  data: EventDataByType[T];
}

/** Any event, as a union: `switch (event.type)` narrows `event.data`. */
export type AnyEvent = { [K in EventType]: StreamEvent<K> }[EventType];

export class UndecodableEvent extends Error {}

export function decode(fields: Record<string, string>): AnyEvent {
  const { event_id: eventId, type } = fields;
  const validate = validators.get(type ?? "");
  if (!eventId || !type || !validate) throw new UndecodableEvent(`unknown event type ${type}`);
  let data: unknown;
  try {
    data = JSON.parse(fields.data ?? "");
  } catch {
    throw new UndecodableEvent("data is not JSON");
  }
  if (!validate(data)) throw new UndecodableEvent(ajv.errorsText(validate.errors));
  return { eventId, type, data } as AnyEvent;
}

export function encode<T extends EventType>(type: T, data: EventDataByType[T], eventId = randomUUID()): string[] {
  const validate = validators.get(type)!;
  if (!validate(data)) throw new Error(`invalid ${type}: ${ajv.errorsText(validate.errors)}`);
  return ["event_id", eventId, "type", type, "data", JSON.stringify(data)];
}

export async function publish<T extends EventType>(
  redis: Redis,
  type: T,
  data: EventDataByType[T],
  maxlen = 100_000,
): Promise<string | null> {
  return redis.xadd(EVENT_STREAMS[type], "MAXLEN", "~", maxlen, "*", ...encode(type, data));
}
