-- Friend-to-friend messages. Deleted after 90 days (cleanup job) and on user.deleted.
CREATE TABLE chat.messages (
    id            bigserial PRIMARY KEY,
    from_user_id  uuid NOT NULL,
    to_user_id    uuid NOT NULL,
    body          text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 500),
    created_at    timestamptz NOT NULL DEFAULT now(),
    delivered_at  timestamptz
);

-- Messages waiting for a player when they come online.
CREATE INDEX messages_undelivered ON chat.messages (to_user_id, id) WHERE delivered_at IS NULL;
-- A conversation's history, newest first (both directions use the same pair order).
CREATE INDEX messages_conversation ON chat.messages (
    LEAST(from_user_id, to_user_id), GREATEST(from_user_id, to_user_id), id DESC
);
CREATE INDEX messages_created_at ON chat.messages (created_at);
