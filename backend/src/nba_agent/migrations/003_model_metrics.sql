-- Existing conversation records stay intact and are deliberately not backfilled.
ALTER TABLE session_details
    ADD COLUMN metrics_started_at timestamptz,
    ADD COLUMN input_tokens bigint CHECK (input_tokens >= 0),
    ADD COLUMN output_tokens bigint CHECK (output_tokens >= 0),
    ADD COLUMN cached_input_tokens bigint CHECK (cached_input_tokens >= 0),
    ADD COLUMN total_tokens bigint GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    ADD COLUMN estimated_cost_usd numeric CHECK (estimated_cost_usd >= 0),
    ADD COLUMN model_call_count bigint CHECK (model_call_count >= 0),
    ADD COLUMN user_message_count bigint CHECK (user_message_count >= 0),
    ADD COLUMN usage_complete boolean,
    ADD COLUMN cost_complete boolean,
    ADD COLUMN last_activity_at timestamptz;

-- Defaults apply only to sessions created after this migration.
ALTER TABLE session_details
    ALTER COLUMN metrics_started_at SET DEFAULT now(),
    ALTER COLUMN input_tokens SET DEFAULT 0,
    ALTER COLUMN output_tokens SET DEFAULT 0,
    ALTER COLUMN cached_input_tokens SET DEFAULT 0,
    ALTER COLUMN estimated_cost_usd SET DEFAULT 0,
    ALTER COLUMN model_call_count SET DEFAULT 0,
    ALTER COLUMN user_message_count SET DEFAULT 0,
    ALTER COLUMN usage_complete SET DEFAULT true,
    ALTER COLUMN cost_complete SET DEFAULT true,
    ALTER COLUMN last_activity_at SET DEFAULT now();

ALTER TABLE message_details ADD CONSTRAINT message_session_identity UNIQUE (session_id, message_id);
CREATE TABLE model_metrics (
    id uuid PRIMARY KEY,
    session_id uuid NOT NULL REFERENCES session_details(id),
    user_message_id uuid NOT NULL,
    stage text NOT NULL CHECK (stage IN ('gatekeeper', 'analyst')),
    call_sequence integer NOT NULL CHECK (call_sequence > 0),
    provider text NOT NULL,
    model text NOT NULL,
    started_at timestamptz NOT NULL,
    duration_ms bigint NOT NULL CHECK (duration_ms >= 0),
    status text NOT NULL CHECK (status IN ('completed', 'error', 'cancelled')),
    error_type text,
    input_tokens bigint CHECK (input_tokens >= 0),
    output_tokens bigint CHECK (output_tokens >= 0),
    cached_input_tokens bigint CHECK (cached_input_tokens >= 0),
    total_tokens bigint GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    estimated_cost_usd numeric CHECK (estimated_cost_usd >= 0),
    pricing_status text NOT NULL CHECK (pricing_status IN ('priced', 'unavailable', 'usage_unavailable')),
    pricing_metadata jsonb NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id, user_message_id) REFERENCES message_details(session_id, message_id),
    UNIQUE (user_message_id, call_sequence),
    CHECK ((pricing_status = 'priced') = (estimated_cost_usd IS NOT NULL)),
    CHECK ((pricing_status = 'usage_unavailable') = (input_tokens IS NULL)),
    CHECK ((input_tokens IS NULL) = (output_tokens IS NULL)),
    CHECK ((input_tokens IS NULL) = (cached_input_tokens IS NULL)),
    CHECK (cached_input_tokens <= input_tokens)
);
CREATE INDEX model_metrics_session_order_idx ON model_metrics(session_id, started_at, id);
COMMENT ON TABLE model_metrics IS
    'One non-streaming model request per row, including retries and failures. USD estimates, not invoices. No prompts or response bodies.';
