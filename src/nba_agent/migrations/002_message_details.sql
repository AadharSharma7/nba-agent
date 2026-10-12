-- The prototype conversation logs may be discarded; NBA statistics are retained.
DROP TABLE chat_turns;
DELETE FROM session_details;

CREATE TABLE message_details (
    message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id uuid NOT NULL REFERENCES session_details(id),
    sequence_number bigint NOT NULL CHECK (sequence_number > 0),
    role text NOT NULL CHECK (role IN ('user', 'gate', 'analyst', 'tool', 'application')),
    message_type text NOT NULL CHECK (message_type IN (
        'user_message', 'agent_output', 'gate_decision', 'tool_request',
        'tool_result', 'tool_error', 'validation_feedback', 'final_answer'
    )),
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    tool_call_id text,
    user_visible boolean NOT NULL DEFAULT false,
    status text CHECK (status IN ('answer', 'clarification', 'unsupported', 'refused', 'error')),
    metadata jsonb NOT NULL DEFAULT '{}',
    UNIQUE (session_id, sequence_number)
);
COMMENT ON TABLE message_details IS
    'Ordered conversation messages; no separate turn/request identifier. Excludes private model reasoning and system prompts.';
