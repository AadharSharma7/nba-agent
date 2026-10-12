"""Ordered conversation records, excluding system prompts and private reasoning."""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from nba_agent.domain import ModelMetric


def json_value(value):
    return json.loads(json.dumps(value, default=str))


def structured_content(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


@dataclass
class Message:
    role: str
    message_type: str
    content: object
    message_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tool_call_id: str | None = None
    user_visible: bool = False
    status: str | None = None
    metadata: dict = field(default_factory=dict)


def agent_messages(messages, role: str, calls: list[dict]) -> list[Message]:
    """Capture every public model output and tool/retry exchange, including failed runs.

    Structured output tools are agent outputs, not NBA data tool calls. Tool
    records fill in results when an exception intentionally stops the agent loop.
    """
    events = []
    returned = set()
    by_call = {c["tool_call_id"]: c for c in calls}
    for message in messages:
        usage_pending = isinstance(message, ModelResponse)
        for part in message.parts:
            event = None
            if isinstance(message, ModelResponse):
                if isinstance(part, ToolCallPart):
                    event = Message(
                        role,
                        "tool_request"
                        if part.tool_name == "search_nba_data_tool"
                        else "agent_output",
                        {"tool": part.tool_name, "arguments": structured_content(part.args)}
                        if part.tool_name == "search_nba_data_tool"
                        else structured_content(part.args),
                        created_at=message.timestamp,
                        tool_call_id=part.tool_call_id,
                    )
                elif isinstance(part, TextPart):
                    event = Message(
                        role, "agent_output", part.content, created_at=message.timestamp
                    )
                if event and usage_pending:
                    event.metadata = {
                        "usage": json_value(asdict(message.usage)),
                        "model": message.model_name,
                    }
                    usage_pending = False
            elif isinstance(part, ToolReturnPart) and part.tool_name == "search_nba_data_tool":
                returned.add(part.tool_call_id)
                event = Message(
                    "tool",
                    "tool_result",
                    structured_content(part.content),
                    created_at=part.timestamp,
                    tool_call_id=part.tool_call_id,
                    metadata={"elapsed_ms": by_call.get(part.tool_call_id, {}).get("elapsed_ms")},
                )
            elif isinstance(part, RetryPromptPart):
                event = Message(
                    "application",
                    "validation_feedback",
                    json_value(part.content),
                    created_at=part.timestamp,
                    tool_call_id=part.tool_call_id,
                    metadata={"agent": role, "tool": part.tool_name},
                )
            if event:
                events.append(event)
    for call in calls:
        if call["tool_call_id"] in returned or "validation_error" in call:
            continue
        events.append(
            Message(
                "tool",
                "tool_error" if "error" in call else "tool_result",
                call.get("result", {"error": call.get("error", "tool_interrupted")}),
                created_at=call["completed_at"],
                tool_call_id=call["tool_call_id"],
                metadata={"elapsed_ms": call["elapsed_ms"]},
            )
        )
    return sorted(events, key=lambda event: event.created_at)


async def append_messages(
    pool: AsyncConnectionPool,
    session_id: UUID,
    messages: list[Message],
    *,
    metrics: list[ModelMetric] | None = None,
) -> None:
    if not messages and metrics is None:
        return
    async with pool.connection() as conn:
        # Serialize messages, call metrics and session totals in one transaction.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (str(session_id),)
        )
        cur = await conn.execute(
            "SELECT COALESCE(MAX(sequence_number),0) AS last FROM message_details WHERE session_id=%s",
            (session_id,),
        )
        sequence = (await cur.fetchone())["last"]
        for message in messages:
            cur = await conn.execute(
                """INSERT INTO message_details
                (message_id,session_id,sequence_number,role,message_type,content,created_at,
                 tool_call_id,user_visible,status,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (message_id) DO NOTHING RETURNING message_id""",
                (
                    message.message_id,
                    session_id,
                    sequence + 1,
                    message.role,
                    message.message_type,
                    Jsonb(json_value(message.content)),
                    message.created_at,
                    message.tool_call_id,
                    message.user_visible,
                    message.status,
                    Jsonb(json_value(message.metadata)),
                ),
            )
            if await cur.fetchone():
                sequence += 1
                await conn.execute(
                    """UPDATE session_details SET
                    user_message_count=COALESCE(user_message_count,0)+%s,
                    metrics_started_at=COALESCE(metrics_started_at,%s),
                    last_activity_at=GREATEST(last_activity_at,%s)
                    WHERE id=%s""",
                    (
                        int(message.role == "user"),
                        message.created_at,
                        message.created_at,
                        session_id,
                    ),
                )
        if metrics is not None:
            await persist_metrics(conn, session_id, metrics)


async def persist_metrics(
    conn: AsyncConnection, session_id: UUID, metrics: list[ModelMetric]
) -> None:
    """Called under append_messages' session lock and transaction; retries cannot inflate totals."""
    for metric in metrics:
        await conn.execute(
            """INSERT INTO model_metrics
            (id,session_id,user_message_id,stage,call_sequence,provider,model,started_at,
             duration_ms,status,error_type,input_tokens,output_tokens,cached_input_tokens,
             estimated_cost_usd,pricing_status,pricing_metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING""",
            (
                metric.id,
                session_id,
                metric.user_message_id,
                metric.stage,
                metric.call_sequence,
                metric.provider,
                metric.model,
                metric.started_at,
                metric.duration_ms,
                metric.status,
                metric.error_type,
                metric.input_tokens,
                metric.output_tokens,
                metric.cached_input_tokens,
                metric.estimated_cost_usd,
                metric.pricing_status,
                Jsonb(json_value(metric.pricing_metadata)),
            ),
        )
    # Sum the ledger, never both message usage and final-answer usage summaries.
    # Unknown-only totals stay NULL; mixed totals are known subtotals with complete=false.
    await conn.execute(
        """UPDATE session_details SET
        input_tokens=totals.input_tokens, output_tokens=totals.output_tokens,
        cached_input_tokens=totals.cached_input_tokens, estimated_cost_usd=totals.cost,
        model_call_count=totals.calls,
        usage_complete=COALESCE(session_details.usage_complete,false) AND totals.usage_complete,
        cost_complete=COALESCE(session_details.cost_complete,false) AND totals.cost_complete
        FROM (
            SELECT count(*) calls,
            CASE WHEN count(*)=0 THEN 0 ELSE sum(input_tokens) END input_tokens,
            CASE WHEN count(*)=0 THEN 0 ELSE sum(output_tokens) END output_tokens,
            CASE WHEN count(*)=0 THEN 0 ELSE sum(cached_input_tokens) END cached_input_tokens,
            CASE WHEN count(*)=0 THEN 0 ELSE sum(estimated_cost_usd) END cost,
            COALESCE(bool_and(input_tokens IS NOT NULL),true) usage_complete,
            COALESCE(bool_and(pricing_status='priced'),true) cost_complete
            FROM model_metrics WHERE session_id=%s
        ) totals WHERE id=%s""",
        (session_id, session_id),
    )


async def conversation_context(pool, session_id: UUID):
    async with pool.connection() as conn:
        cur = await conn.execute(
            """SELECT u.content AS user_content, a.content AS answer_content
            FROM message_details a
            JOIN LATERAL (
                SELECT content FROM message_details
                WHERE session_id=a.session_id AND role='user'
                  AND sequence_number<a.sequence_number
                ORDER BY sequence_number DESC LIMIT 1
            ) u ON true
            WHERE a.session_id=%s AND a.user_visible AND a.message_type='final_answer'
              AND a.status IN ('answer','clarification','unsupported')
            ORDER BY a.sequence_number DESC LIMIT 4""",
            (session_id,),
        )
        history = await cur.fetchall()
    return [
        {
            "user": row["user_content"]["message"],
            "assistant": row["answer_content"]["answer"][:1600],
        }
        for row in reversed(history)
    ]
