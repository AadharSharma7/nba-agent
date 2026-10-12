import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9 ]", "", value.lower()).split())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatsQuery(StrictModel):
    """Typed player statistics query. Season years are START years: 2020 = 2020–21."""

    players: list[str] = Field(min_length=1, max_length=4)
    season_start: int | None = Field(
        default=None,
        ge=1949,
        le=2100,
        description="Inclusive season START year. The 2021 playoffs are season 2020. Since 2021 playoffs => 2020.",
    )
    season_end: int | None = Field(
        default=None,
        ge=1949,
        le=2100,
        description="Inclusive season START year. For only the 2016 playoffs set both season_start and season_end to 2015.",
    )
    phase: Literal["regular", "playoffs", "both"] = "regular"
    playoff_round: (
        Literal["first_round", "conference_semifinals", "conference_finals", "finals"] | None
    ) = None
    conference: Literal["west", "east"] | None = None
    opponent_team: str | None = Field(default=None, max_length=100)
    opposing_player: str | None = Field(default=None, max_length=100)
    home: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    last_n: int | None = Field(default=None, ge=1, le=100)
    group_by: Literal["player", "season", "game"] = "player"
    limit: int = Field(default=100, ge=1, le=200)

    @model_validator(mode="after")
    def validate_scope(self):
        if any(not p.strip() or len(p) > 100 for p in self.players):
            raise ValueError("Player names must contain 1–100 characters")
        if (
            self.season_start is not None
            and self.season_end is not None
            and self.season_end < self.season_start
        ):
            raise ValueError("season_end must be at least season_start")
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise ValueError("date_to must be at least date_from")
        if (self.playoff_round or self.conference) and self.phase != "playoffs":
            raise ValueError("Round/conference filters require phase=playoffs")
        return self


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: UUID | None = None


class GateDecision(StrictModel):
    decision: Literal["allow", "refuse"]
    reason: Literal["nba", "off_topic", "instruction_attack", "mixed_request"]


class AnalystAnswer(StrictModel):
    status: Literal["answer", "clarification", "unsupported"]
    answer: str = Field(min_length=1, max_length=6000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class ChatResponse(StrictModel):
    session_id: UUID
    message_id: UUID
    status: Literal["answer", "clarification", "unsupported", "refused", "error"]
    answer: str
    sources: list[dict] = Field(default_factory=list)
    data: list[dict] = Field(default_factory=list)
    model_calls: int = 0


class ModelMetric(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_message_id: UUID
    stage: Literal["gatekeeper", "analyst"]
    call_sequence: int = Field(ge=1)
    provider: str
    model: str
    started_at: datetime
    duration_ms: int = Field(default=0, ge=0)
    status: Literal["completed", "error", "cancelled"] = "error"
    error_type: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)
    pricing_status: Literal["priced", "unavailable", "usage_unavailable"] = "usage_unavailable"
    pricing_metadata: dict = Field(default_factory=dict)


class ModelMetricRecord(ModelMetric):
    session_id: UUID
    total_tokens: int | None


class ModelMetricsPage(BaseModel):
    session_id: UUID
    metrics: list[ModelMetricRecord]
    next_offset: int | None


class SessionSummary(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    last_activity_at: datetime


class SessionsPage(BaseModel):
    sessions: list[SessionSummary]
    next_offset: int | None
