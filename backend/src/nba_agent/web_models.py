"""Typed boundaries for browsing, visitor chat, and owner analytics."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, model_validator

from nba_agent.domain import StrictModel

Phase = Literal["regular", "playoffs"]


class OwnerLogin(StrictModel):
    password: SecretStr = Field(min_length=1, max_length=1024)
    code: str = Field(pattern=r"^\d{6}$")


class Player(BaseModel):
    id: int
    name: str
    first_season: int | None = None
    last_season: int | None = None


class PlayerPage(BaseModel):
    players: list[Player]
    next_offset: int | None = None


class SeasonStats(BaseModel):
    season_start: int
    games: int
    teams: str
    points: int | None
    rebounds: int | None
    assists: int | None
    ppg: float | None
    rpg: float | None
    apg: float | None
    fg_pct: float | None
    three_pct: float | None
    ft_pct: float | None


class PlayerProfile(BaseModel):
    player: Player
    phase: Phase
    seasons: list[SeasonStats]


class GameStats(BaseModel):
    game_id: str
    game_date: date
    team: str
    opponent: str
    result: str | None
    points: int | None
    rebounds: int | None
    assists: int | None
    fgm: int | None
    fga: int | None
    fg3m: int | None
    fg3a: int | None


class GamePage(BaseModel):
    games: list[GameStats]
    next_offset: int | None


class Leader(BaseModel):
    id: int
    name: str
    games: int
    ppg: float | None
    rpg: float | None
    apg: float | None
    fg_pct: float | None


class LeadersPage(BaseModel):
    season_start: int
    phase: Phase
    minimum_games: int
    leaders: list[Leader]


class BrowseMeta(BaseModel):
    seasons: list[int]
    first_game: date | None
    last_game: date | None
    source: str = "Kaggle v515"


class PublicReply(BaseModel):
    session_id: UUID
    message_id: UUID
    status: str
    answer: str
    sources: list[dict] = Field(default_factory=list)
    data: list[dict] = Field(default_factory=list)


class PublicMessage(BaseModel):
    message_id: UUID
    sequence_number: int
    role: Literal["user", "assistant"]
    text: str
    created_at: datetime
    sources: list[dict] = Field(default_factory=list)
    data: list[dict] = Field(default_factory=list)


class PublicSession(BaseModel):
    session_id: UUID
    messages: list[PublicMessage]
    next_after_sequence: int | None


class DashboardFilter(StrictModel):
    date_from: datetime | None = None
    date_to: datetime | None = None
    q: str = Field(default="", max_length=200)
    status: Literal[
        "all", "answer", "clarification", "unsupported", "refused", "error", "pending"
    ] = "all"
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_dates(self):
        for value in (self.date_from, self.date_to):
            if value and value.utcoffset() is None:
                raise ValueError("Dates must include a timezone")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not follow date_to")
        return self


class DashboardSession(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    last_activity_at: datetime
    status: str
    message_count: int
    total_tokens: int | None
    estimated_cost_usd: Decimal | None
    usage_complete: bool | None
    cost_complete: bool | None
    model_call_count: int | None
    elapsed_ms: int | None
    snippet: str | None = None


class DashboardPage(BaseModel):
    sessions: list[DashboardSession]
    total: int
    next_offset: int | None


class OwnerMessage(BaseModel):
    message_id: UUID
    sequence_number: int
    role: str
    message_type: str
    content: dict
    created_at: datetime
    user_visible: bool
    status: str | None
    tool_call_id: str | None


class OwnerSession(BaseModel):
    session: DashboardSession
    messages: list[OwnerMessage]
    next_after_sequence: int | None


class DailyActivity(BaseModel):
    day: date
    conversations: int
    estimated_cost_usd: Decimal | None
    cost_complete: bool


class ModelUsage(BaseModel):
    model: str
    calls: int
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    estimated_cost_usd: Decimal | None
    usage_complete: bool
    cost_complete: bool


class DashboardOverview(BaseModel):
    conversations: int
    messages: int
    total_tokens: int | None
    estimated_cost_usd: Decimal | None
    usage_complete: bool
    cost_complete: bool
    median_response_ms: float | None
    outcomes: dict[str, int]
    activity: list[DailyActivity]
    models: list[ModelUsage]
