import asyncio
import json
from dataclasses import asdict
from time import monotonic
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from pydantic_ai import capture_run_messages
from pydantic_ai.usage import RunUsage, UsageLimits

from nba_agent.agents import (
    REFUSAL,
    UNSUPPORTED,
    AnalystDeps,
    build_agents,
    explicit_attack,
    known_unsupported_request,
)
from nba_agent.config import Settings
from nba_agent.domain import ChatRequest, ChatResponse, ModelMetric
from nba_agent.messages import (
    Message,
    agent_messages,
    append_messages,
    conversation_context,
    json_value,
)
from nba_agent.metrics import MeteredModel
from nba_agent.queries import ClarificationNeeded


class MissingAPIKey(Exception):
    pass


class UnknownSession(Exception):
    pass


class ChatService:
    def __init__(self, pool, settings: Settings, agents=None):
        self.pool = pool
        self.settings = settings
        self.agents = agents or (build_agents(settings) if settings.llm_configured else None)
        self.semaphore = asyncio.Semaphore(3)
        self.session_locks = WeakValueDictionary()

    async def close(self):
        if not self.agents:
            return
        closed = set()
        for agent in self.agents:
            client = getattr(agent.model, "client", None)
            if client is not None and id(client) not in closed:
                await client.close()
                closed.add(id(client))

    async def chat(self, request: ChatRequest, visitor_id: UUID | None = None) -> ChatResponse:
        if not self.agents and not explicit_attack(request.message):
            raise MissingAPIKey
        session_id = request.session_id or uuid4()
        lock = self.session_locks.setdefault(session_id, asyncio.Lock())
        # One local worker: keep a conversation sequential, including simultaneous HTTP submissions.
        async with lock, self.semaphore:
            return await self._chat(request, session_id, visitor_id)

    async def _chat(
        self, request: ChatRequest, session_id, visitor_id: UUID | None = None
    ) -> ChatResponse:
        started = monotonic()
        async with self.pool.connection() as conn:
            if request.session_id:
                cur = await conn.execute(
                    "SELECT id FROM session_details WHERE id=%s AND (%s::uuid IS NULL OR visitor_id=%s)",
                    (session_id, visitor_id, visitor_id),
                )
                if not await cur.fetchone():
                    raise UnknownSession
            else:
                await conn.execute(
                    "INSERT INTO session_details(id,visitor_id) VALUES(%s,%s)",
                    (session_id, visitor_id),
                )
        context = await conversation_context(self.pool, session_id)
        user_message = Message(
            "user", "user_message", {"message": request.message}, user_visible=True
        )
        await append_messages(self.pool, session_id, [user_message])
        events = []
        metrics: list[ModelMetric] = []

        async def run_agent(agent, role, **kwargs):
            with capture_run_messages() as messages:
                try:
                    model = MeteredModel(
                        agent.model,
                        metrics,
                        user_message.message_id,
                        "gatekeeper" if role == "gate" else "analyst",
                    )
                    return await agent.run(prompt, model=model, **kwargs)
                finally:
                    events.extend(
                        agent_messages(messages, role, deps.tool_calls if role == "analyst" else [])
                    )

        prompt = json.dumps(
            {"untrusted_conversation": context, "latest_user_message": request.message}
        )
        deps, usage = (
            AnalystDeps(self.pool, user_message=request.message, conversation=json.dumps(context)),
            RunUsage(),
        )
        error_code, analyst_output = None, None
        cancelled = None
        response = ChatResponse(
            session_id=session_id, message_id=uuid4(), status="refused", answer=REFUSAL
        )
        try:
            async with asyncio.timeout(90):
                if explicit_attack(request.message):
                    events.append(
                        Message(
                            "application",
                            "gate_decision",
                            {
                                "decision": "refuse",
                                "reason": "instruction_attack",
                                "method": "pattern",
                            },
                        )
                    )
                else:
                    gate, analyst = self.agents
                    decision = await run_agent(
                        gate, "gate", usage=usage, usage_limits=UsageLimits(request_limit=1)
                    )
                    allowed = (
                        decision.output.decision == "allow" and decision.output.reason == "nba"
                    )
                    if allowed and known_unsupported_request(request.message):
                        response.status, response.answer = "unsupported", UNSUPPORTED
                    elif allowed:
                        result = await run_agent(
                            analyst,
                            "analyst",
                            deps=deps,
                            usage=usage,
                            usage_limits=UsageLimits(
                                request_limit=5, tool_calls_limit=4, total_tokens_limit=25000
                            ),
                        )
                        output = result.output
                        if output.status == "unsupported":
                            response.status, response.answer = "unsupported", UNSUPPORTED
                        else:
                            analyst_output = output.model_dump(mode="json")
                            response.status, response.answer = output.status, output.answer
                            evidence = {
                                c["result"].get("evidence_id"): c["result"]
                                for c in deps.tool_calls
                                if "result" in c
                            }
                            selected = [
                                evidence[e]
                                for e in dict.fromkeys(output.evidence_ids)
                                if e in evidence
                            ]
                            response.data = selected
                            response.sources = [
                                {"evidence_id": e["evidence_id"], **e["source"]}
                                for e in selected
                                if e.get("source")
                            ]
        except ClarificationNeeded as exc:
            response.status = "clarification"
            candidates = exc.result.get("candidates", [])
            if candidates:
                names = ", ".join(f"{p['name']} (ID {p['id']})" for p in candidates)
                response.answer = f"Which NBA player or team did you mean: {names}? Please give the full name or ID."
            else:
                response.answer = (
                    "I couldn't find that NBA player or team. Please provide the full name."
                )
        except asyncio.CancelledError as exc:
            cancelled = exc
            error_code = "CancelledError"
            response.status = "error"
            response.answer = "The NBA query was cancelled."
        except Exception as exc:
            # No provider error strings, credentials or raw traces in user-facing output.
            error_code = type(exc).__name__
            response.status = "error"
            response.answer = "I couldn't complete that NBA query. Please try again."
        response.model_calls = usage.requests
        final = None
        if analyst_output is not None:
            final = next(
                (
                    event
                    for event in reversed(events)
                    if event.role == "analyst"
                    and event.message_type == "agent_output"
                    and event.content == analyst_output
                ),
                None,
            )
        if final is None:
            final = Message("application", "final_answer", {})
            events.append(final)
        else:
            final.metadata["agent_output"] = final.content
        final.message_id = response.message_id
        final.message_type = "final_answer"
        final.user_visible = True
        final.status = response.status
        final.content = response.model_dump(mode="json")
        final.metadata.update(
            {
                "total_usage": json_value(asdict(usage)),
                "elapsed_ms": round((monotonic() - started) * 1000),
                "error_code": error_code,
            }
        )
        await append_messages(self.pool, session_id, events, metrics=metrics)
        if cancelled is not None:
            raise cancelled
        return response
