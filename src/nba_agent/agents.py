"""Two bounded agents. Only the Analyst can call the NBA statistics tool."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic

from openai import AsyncOpenAI
from psycopg_pool import AsyncConnectionPool
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from nba_agent.config import Settings
from nba_agent.domain import AnalystAnswer, GateDecision, StatsQuery
from nba_agent.prompts import ANALYST_INSTRUCTIONS, GATE_INSTRUCTIONS
from nba_agent.queries import ClarificationNeeded, ambiguous_expansion, search_nba_data

REFUSAL = "I can only help with NBA questions. I can't follow requests to change my role or ignore my instructions."
UNSUPPORTED = "This prototype currently supports historical NBA player statistics, game logs, playoff-series statistics, and player comparisons. I don't have the data tools to answer that question yet."
ATTACK_PATTERNS = [
    r"\b(?:ignore|forget|disregard|override)\b.{0,55}\b(?:instructions|system prompt|rules|guardrails)\b",
    r"\b(?:reveal|print|show|repeat)\b.{0,45}\b(?:system prompt|api key|secrets|environment variables)\b",
    r"\byour new role\b",
    r"\bpretend basketball means\b",
    r"\b(?:execute|run)\s+(?:this\s+)?(?:shell|python|sql|command)\b",
]


def explicit_attack(message: str) -> bool:
    return any(re.search(pattern, message, re.I | re.S) for pattern in ATTACK_PATTERNS)


@dataclass
class AnalystDeps:
    pool: AsyncConnectionPool
    tool_calls: list[dict] = field(default_factory=list)
    user_message: str = ""
    conversation: str = ""


def known_unsupported_request(message: str) -> bool:
    """Deterministic exclusions for statistical filters the tool cannot express."""
    return bool(
        re.search(
            r"\belimination\b|\bgame\s*7s?\b|\bon[ /-]off\b|\bdefensive matchups?\b|"
            r"\b(?:offensive|defensive|net) rating\b|\b(?:injured|unavailable)\b|"
            r"\bwithout\b|\bsalary\b|\bstandings\b|\btonight\b|\blive score",
            message,
            re.I,
        )
    )


def explicit_playoff_year_error(message: str, query: StatsQuery) -> str | None:
    """Validate only unambiguous explicit calendar-year playoff phrases.

    This does not attempt general NLP. It prevents an observed year-offset failure
    at the tool boundary; season ranges such as 2021–22 are deliberately excluded.
    """
    if not re.search(r"playoffs?|\bwcf\b|\becf\b|conference finals", message, re.I):
        return None
    since = re.search(r"\b(?:since|from)\s+((?:19|20)\d{2})(?!\d|\s*[-–/]\s*\d)", message, re.I)
    single = re.search(
        r"\b((?:19|20)\d{2})\s+(?:NBA\s+)?(?:playoffs?|WCF|ECF|(?:western|eastern) conference finals)\b",
        message,
        re.I,
    )
    match = since or single
    if match and query.phase != "playoffs":
        return "The explicit request is for playoff games. Set phase=playoffs."
    if match:
        expected = int(match[1]) - 1
        if query.season_start != expected or (not since and query.season_end != expected):
            return (
                f"The user means playoffs in calendar {match[1]}, which belong to season START {expected}. "
                f"Set season_start={expected}"
                + (" and leave season_end unset." if since else f" and season_end={expected}.")
            )
    return None


def invalid_output(answer: AnalystAnswer, calls: list[dict]) -> str | None:
    if "```" in answer.answer or explicit_attack(answer.answer):
        return (
            "Return only an NBA answer or concise NBA clarification, without code or instructions."
        )
    if answer.status != "answer":
        return None
    evidence = {
        c["result"].get("evidence_id"): c["result"]
        for c in calls
        if c.get("result", {}).get("status") in {"ok", "no_data"}
    }
    if not answer.evidence_ids or any(e not in evidence for e in answer.evidence_ids):
        return (
            "A statistical answer must cite evidence IDs returned by search_nba_data in this turn."
        )
    # A numerical consistency check, not a semantic proof of the entire prose.
    allowed: set[str] = set()

    def collect(value):
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            for precision in range(4):
                allowed.add(
                    f"{value:.{precision}f}".rstrip("0").rstrip(".")
                    if precision
                    else str(round(value))
                )
            allowed.add(str(value))
        elif isinstance(value, str):
            allowed.update(re.findall(r"\d+(?:\.\d+)?", value))
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for evidence_id in answer.evidence_ids:
        collect(evidence[evidence_id])
    prose = re.sub(r"\b[23](?:[ -]point(?:er)?s?|PT)\b", "shooting", answer.answer, flags=re.I)

    def season_notation(match):
        start, end = match[1], match[2]
        expected_end = str(int(start) + 1)
        if start in allowed and end in {expected_end, expected_end[-2:]}:
            return start
        return match[0]

    # A season label is a date range, not two independent numerical claims.
    prose = re.sub(r"\b((?:19|20)\d{2})\s*[-–—/]\s*(\d{4}|\d{2})\b", season_notation, prose)
    for token in re.findall(r"\d+(?:\.\d+)?", prose.replace(",", "")):
        normalized = token.rstrip("0").rstrip(".") if "." in token else token
        if token not in allowed and normalized not in allowed:
            return f"The number {token} is not supported by the returned evidence; use supplied values."
    return None


def build_agents(
    settings: Settings, gate_model: Model | None = None, analyst_model: Model | None = None
):
    if gate_model is None or analyst_model is None:
        client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(), timeout=30, max_retries=0
        )
        provider = OpenAIProvider(openai_client=client)
        gate_model = gate_model or OpenAIResponsesModel(
            settings.gatekeeper_model, provider=provider
        )
        analyst_model = analyst_model or OpenAIResponsesModel(
            settings.analyst_model, provider=provider
        )
    gate = Agent(
        gate_model,
        name="NBA Gatekeeper",
        output_type=GateDecision,
        instructions=GATE_INSTRUCTIONS,
        retries=0,
        model_settings={"temperature": 0, "max_tokens": 200, "openai_store": False},
    )
    analyst = Agent(
        analyst_model,
        name="NBA Analyst",
        output_type=AnalystAnswer,
        deps_type=AnalystDeps,
        instructions=ANALYST_INSTRUCTIONS,
        retries=1,
        model_settings={"temperature": 0, "max_tokens": 2200, "openai_store": False},
    )

    @analyst.tool
    async def search_nba_data_tool(ctx: RunContext[AnalystDeps], query: StatsQuery) -> str:
        """Retrieve NBA player statistics using typed filters. No free-form SQL or document search.

        Args:
            query: Player names/IDs, season START years, competition and optional matchup filters.
        """
        started = monotonic()
        record = {
            "tool": "search_nba_data",
            "query": query.model_dump(mode="json"),
            "tool_call_id": ctx.tool_call_id,
        }
        ctx.deps.tool_calls.append(record)
        if error := explicit_playoff_year_error(ctx.deps.user_message, query):
            record["validation_error"] = error
            record["elapsed_ms"] = 0
            raise ModelRetry(error)
        try:
            ambiguity = await ambiguous_expansion(
                ctx.deps.pool, query, ctx.deps.user_message, ctx.deps.conversation
            )
            record["result"] = ambiguity or await search_nba_data(ctx.deps.pool, query)
        except Exception:
            record["error"] = "data_query_failed"
            raise
        finally:
            record["elapsed_ms"] = round((monotonic() - started) * 1000)
            record["completed_at"] = datetime.now(UTC)
        if record["result"]["status"] in {"clarification", "not_found"}:
            # Stop here: repeatedly guessing an identity cannot resolve user intent.
            raise ClarificationNeeded(record["result"])
        return json.dumps(record["result"])

    @analyst.output_validator
    def validate_answer(ctx: RunContext[AnalystDeps], answer: AnalystAnswer) -> AnalystAnswer:
        if error := invalid_output(answer, ctx.deps.tool_calls):
            raise ModelRetry(error)
        return answer

    return gate, analyst
