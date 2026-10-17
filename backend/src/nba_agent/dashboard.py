"""Owner-only read queries; model_metrics remains the accounting source of truth."""

from datetime import UTC
from uuid import UUID

from fastapi import HTTPException
from psycopg_pool import AsyncConnectionPool

from nba_agent.database import read_connection
from nba_agent.domain import ModelMetricsPage
from nba_agent.web_models import DashboardFilter, DashboardOverview, DashboardPage, OwnerSession

# Fixed fragments; user values are bound parameters throughout.
SESSION_CTE = """WITH sessions AS (
    SELECT s.id,s.created_at,coalesce(s.last_activity_at,s.created_at) last_activity_at,
      coalesce(left(first_message.title,120),'Empty conversation') title,
      coalesce(last_message.status,'pending') status,
      (SELECT count(*) FROM message_details m WHERE m.session_id=s.id AND m.user_visible) message_count,
      s.total_tokens,s.estimated_cost_usd,s.usage_complete,s.cost_complete,s.model_call_count,
      CASE WHEN last_message.metadata->>'elapsed_ms' ~ '^[0-9]+$'
           THEN (last_message.metadata->>'elapsed_ms')::bigint END elapsed_ms
    FROM session_details s
    LEFT JOIN LATERAL (SELECT content->>'message' title FROM message_details
        WHERE session_id=s.id AND role='user' AND user_visible ORDER BY sequence_number LIMIT 1) first_message ON true
    LEFT JOIN LATERAL (SELECT status,metadata FROM message_details
        WHERE session_id=s.id AND message_type='final_answer' AND user_visible
        ORDER BY sequence_number DESC LIMIT 1) last_message ON true
    WHERE (%s::timestamptz IS NULL OR s.created_at >= %s)
      AND (%s::timestamptz IS NULL OR s.created_at <= %s)
) """


def date_params(filters: DashboardFilter) -> tuple:
    return (filters.date_from, filters.date_from, filters.date_to, filters.date_to)


async def sessions(pool: AsyncConnectionPool, filters: DashboardFilter) -> DashboardPage:
    query = filters.q.strip()
    where = """ FROM sessions s WHERE (%s='all' OR s.status=%s)
        AND (%s='' OR EXISTS(SELECT 1 FROM message_details m WHERE m.session_id=s.id
            AND m.user_visible AND strpos(lower(coalesce(m.content->>'message',m.content->>'answer','')),lower(%s))>0))"""
    params = (*date_params(filters), filters.status, filters.status, query, query)
    async with read_connection(pool) as conn:
        cur = await conn.execute(SESSION_CTE + "SELECT count(*) count" + where, params)
        total = (await cur.fetchone())["count"]
        cur = await conn.execute(
            SESSION_CTE
            + "SELECT s.*"
            + where
            + " ORDER BY s.created_at DESC,s.id DESC LIMIT %s OFFSET %s",
            (*params, filters.limit, filters.offset),
        )
        rows = await cur.fetchall()
        if query:
            for row in rows:
                cur = await conn.execute(
                    """SELECT substring(text FROM greatest(1,strpos(lower(text),lower(%s))-50) FOR 220) snippet
                    FROM (SELECT coalesce(content->>'message',content->>'answer','') text,sequence_number
                      FROM message_details WHERE session_id=%s AND user_visible) messages
                    WHERE strpos(lower(text),lower(%s))>0 ORDER BY sequence_number LIMIT 1""",
                    (query, row["id"], query),
                )
                result = await cur.fetchone()
                row["snippet"] = result["snippet"] if result else None
    return DashboardPage(
        sessions=rows,
        total=total,
        next_offset=filters.offset + filters.limit if filters.offset + len(rows) < total else None,
    )


async def session_detail(
    pool: AsyncConnectionPool, session_id: UUID, after_sequence: int, limit: int, query: str = ""
) -> OwnerSession:
    async with read_connection(pool) as conn:
        cur = await conn.execute(
            SESSION_CTE + "SELECT * FROM sessions WHERE id=%s", (None, None, None, None, session_id)
        )
        session = await cur.fetchone()
        if session is None:
            raise HTTPException(404, "Session not found.")
        cur = await conn.execute(
            """SELECT message_id,sequence_number,role,message_type,content,created_at,user_visible,status,tool_call_id
            FROM message_details WHERE session_id=%s AND sequence_number>%s
            AND (%s='' OR (user_visible AND
                strpos(lower(coalesce(content->>'message',content->>'answer','')),lower(%s))>0))
            ORDER BY sequence_number LIMIT %s""",
            (session_id, after_sequence, query.strip(), query.strip(), limit + 1),
        )
        rows = await cur.fetchall()
    return OwnerSession(
        session=session,
        messages=rows[:limit],
        next_after_sequence=rows[limit - 1]["sequence_number"] if len(rows) > limit else None,
    )


async def metrics(
    pool: AsyncConnectionPool, session_id: UUID, limit: int, offset: int
) -> ModelMetricsPage:
    async with read_connection(pool) as conn:
        cur = await conn.execute("SELECT id FROM session_details WHERE id=%s", (session_id,))
        if not await cur.fetchone():
            raise HTTPException(404, "Session not found.")
        cur = await conn.execute(
            "SELECT * FROM model_metrics WHERE session_id=%s ORDER BY started_at,id LIMIT %s OFFSET %s",
            (session_id, limit + 1, offset),
        )
        rows = await cur.fetchall()
    return ModelMetricsPage(
        session_id=session_id,
        metrics=rows[:limit],
        next_offset=offset + limit if len(rows) > limit else None,
    )


async def overview(pool: AsyncConnectionPool, filters: DashboardFilter) -> DashboardOverview:
    # Consistent cohort: sessions started in the date range, including all their calls.
    async with read_connection(pool) as conn:
        cur = await conn.execute(SESSION_CTE + "SELECT * FROM sessions", date_params(filters))
        rows = await cur.fetchall()
        ids = [r["id"] for r in rows]
        cur = await conn.execute(
            """SELECT model,count(*) calls,sum(input_tokens) input_tokens,sum(output_tokens) output_tokens,
            sum(cached_input_tokens) cached_input_tokens,sum(estimated_cost_usd) estimated_cost_usd,
            bool_and(input_tokens IS NOT NULL) usage_complete,bool_and(estimated_cost_usd IS NOT NULL) cost_complete
            FROM model_metrics WHERE session_id=ANY(%s) GROUP BY model ORDER BY model""",
            (ids,),
        )
        models = await cur.fetchall()
        cur = await conn.execute(
            """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY (metadata->>'elapsed_ms')::float8) median
            FROM message_details WHERE session_id=ANY(%s) AND message_type='final_answer' AND user_visible
            AND metadata->>'elapsed_ms' ~ '^[0-9]+$'""",
            (ids,),
        )
        median = (await cur.fetchone())["median"]
    outcomes = {}
    daily = {}
    for row in rows:
        outcomes[row["status"]] = outcomes.get(row["status"], 0) + 1
        # UTC is explicit in the API; the dashboard displays this chart timezone.
        day = row["created_at"].astimezone(UTC).date()
        entry = daily.setdefault(
            day, {"day": day, "conversations": 0, "costs": [], "cost_complete": True}
        )
        entry["conversations"] += 1
        if row["estimated_cost_usd"] is not None:
            entry["costs"].append(row["estimated_cost_usd"])
        entry["cost_complete"] &= bool(row["cost_complete"])

    def known_sum(values):
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    return DashboardOverview(
        conversations=len(rows),
        messages=sum(r["message_count"] for r in rows),
        total_tokens=known_sum([r["total_tokens"] for r in rows]) if rows else 0,
        estimated_cost_usd=known_sum([r["estimated_cost_usd"] for r in rows]) if rows else 0,
        usage_complete=all(r["usage_complete"] for r in rows),
        cost_complete=all(r["cost_complete"] for r in rows),
        median_response_ms=median,
        outcomes=outcomes,
        models=models,
        activity=[
            {
                **{k: v for k, v in entry.items() if k != "costs"},
                "estimated_cost_usd": known_sum(entry["costs"]),
            }
            for _, entry in sorted(daily.items())
        ],
    )
