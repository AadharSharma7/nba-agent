-- Preserve existing conversations. Unowned legacy sessions are owner-only.
CREATE TABLE browser_sessions (
    id uuid PRIMARY KEY,
    token_hash text NOT NULL UNIQUE,
    kind text NOT NULL CHECK (kind IN ('visitor','owner')),
    credential_version text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX browser_sessions_expiry_idx ON browser_sessions(expires_at);
ALTER TABLE session_details ADD COLUMN visitor_id uuid REFERENCES browser_sessions(id);
CREATE INDEX session_details_visitor_idx ON session_details(visitor_id, created_at DESC);
CREATE INDEX session_details_created_idx ON session_details(created_at DESC, id);
CREATE INDEX message_details_visible_idx ON message_details(session_id, sequence_number)
    WHERE user_visible;
CREATE INDEX model_metrics_started_idx ON model_metrics(started_at);
CREATE TABLE web_rate_limits (
    key text PRIMARY KEY,
    window_start bigint NOT NULL,
    attempts integer NOT NULL CHECK (attempts > 0)
);
CREATE TABLE web_auth_state (
    key text PRIMARY KEY,
    counter bigint NOT NULL
);
