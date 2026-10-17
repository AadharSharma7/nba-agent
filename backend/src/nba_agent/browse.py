"""Read-only player browsing, independent of model calls."""

from fastapi import HTTPException
from psycopg_pool import AsyncConnectionPool

from nba_agent.database import read_connection
from nba_agent.domain import normalize_name
from nba_agent.web_models import (
    BrowseMeta,
    GamePage,
    LeadersPage,
    Phase,
    PlayerPage,
    PlayerProfile,
)

# Missing source values do not become zero, or understated complete-season totals.
TOTALS = ",".join(
    f"CASE WHEN count(s.{column})=count(*) THEN sum(s.{column}) END AS {column}"
    for column in ("points", "rebounds", "assists")
)
AVERAGES = ",".join(
    f"CASE WHEN count(s.{column})=count(*) THEN avg(s.{column})::float8 END AS {alias}"
    for column, alias in (("points", "ppg"), ("rebounds", "rpg"), ("assists", "apg"))
)
PERCENTAGES = ",".join(
    f"CASE WHEN count(s.{made})=count(*) AND count(s.{attempts})=count(*) "
    f"THEN 100.0*sum(s.{made})::float8/nullif(sum(s.{attempts}),0) END AS {alias}"
    for made, attempts, alias in (
        ("fgm", "fga", "fg_pct"),
        ("fg3m", "fg3a", "three_pct"),
        ("ftm", "fta", "ft_pct"),
    )
)


async def metadata(pool: AsyncConnectionPool) -> BrowseMeta:
    async with read_connection(pool) as conn:
        cur = await conn.execute(
            "SELECT DISTINCT season_start FROM games ORDER BY season_start DESC"
        )
        seasons = [r["season_start"] for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT min(min_game_date) first_game,max(max_game_date) last_game FROM source_datasets"
        )
        source = await cur.fetchone()
    return BrowseMeta(seasons=seasons, **source)


async def players(pool: AsyncConnectionPool, query: str, limit: int, offset: int) -> PlayerPage:
    normalized = normalize_name(query)
    async with read_connection(pool) as conn:
        cur = await conn.execute(
            """SELECT p.id,p.name, min(g.season_start) first_season,max(g.season_start) last_season
            FROM (SELECT p.id,p.name,p.normalized_name FROM players p
                WHERE strpos(p.normalized_name,%s)>0 AND EXISTS(
                    SELECT 1 FROM player_game_stats s WHERE s.player_id=p.id AND s.appeared)
                ORDER BY p.normalized_name,p.id LIMIT %s OFFSET %s) p
            JOIN player_game_stats s ON s.player_id=p.id AND s.appeared
            JOIN games g ON g.id=s.game_id
            GROUP BY p.id,p.name,p.normalized_name ORDER BY p.normalized_name,p.id""",
            (normalized, limit + 1, offset),
        )
        rows = await cur.fetchall()
    return PlayerPage(
        players=rows[:limit], next_offset=offset + limit if len(rows) > limit else None
    )


async def profile(pool: AsyncConnectionPool, player_id: int, phase: Phase) -> PlayerProfile:
    async with read_connection(pool) as conn:
        cur = await conn.execute("SELECT id,name FROM players WHERE id=%s", (player_id,))
        player = await cur.fetchone()
        if player is None:
            raise HTTPException(404, "Player not found.")
        cur = await conn.execute(
            f"""SELECT g.season_start,count(*) games,
            string_agg(DISTINCT CASE WHEN s.home THEN g.home_team_name ELSE g.away_team_name END, ', ') teams,
            {TOTALS},{AVERAGES},{PERCENTAGES}
            FROM player_game_stats s JOIN games g ON g.id=s.game_id
            WHERE s.player_id=%s AND s.appeared AND g.phase=%s
            GROUP BY g.season_start ORDER BY g.season_start DESC""",
            (player_id, phase),
        )
        seasons = await cur.fetchall()
    return PlayerProfile(player=player, phase=phase, seasons=seasons)


async def games(
    pool: AsyncConnectionPool, player_id: int, season: int, phase: Phase, limit: int, offset: int
) -> GamePage:
    async with read_connection(pool) as conn:
        cur = await conn.execute(
            """SELECT g.id game_id,g.played_at::date game_date,
            CASE WHEN s.home THEN g.home_team_name ELSE g.away_team_name END team,
            CASE WHEN s.home THEN g.away_team_name ELSE g.home_team_name END opponent,
            CASE WHEN g.home_score IS NULL OR g.away_score IS NULL THEN NULL
                 WHEN g.home_score=g.away_score THEN 'T'
                 WHEN (g.home_score>g.away_score)=s.home THEN 'W' ELSE 'L' END result,
            s.points,s.rebounds,s.assists,s.fgm,s.fga,s.fg3m,s.fg3a
            FROM player_game_stats s JOIN games g ON g.id=s.game_id
            WHERE s.player_id=%s AND s.appeared AND g.season_start=%s AND g.phase=%s
            ORDER BY g.played_at DESC,g.id DESC LIMIT %s OFFSET %s""",
            (player_id, season, phase, limit + 1, offset),
        )
        rows = await cur.fetchall()
    return GamePage(games=rows[:limit], next_offset=offset + limit if len(rows) > limit else None)


async def leaders(
    pool: AsyncConnectionPool, season: int, phase: Phase, category: str, minimum_games: int
) -> LeadersPage:
    sort = {"points": "ppg", "rebounds": "rpg", "assists": "apg"}[category]
    async with read_connection(pool) as conn:
        cur = await conn.execute(
            f"""WITH totals AS (
                SELECT p.id,p.name,count(*) games,{AVERAGES},{PERCENTAGES}
                FROM player_game_stats s JOIN games g ON g.id=s.game_id
                JOIN players p ON p.id=s.player_id
                WHERE g.season_start=%s AND g.phase=%s AND s.appeared
                GROUP BY p.id,p.name HAVING count(*)>=%s
            ) SELECT * FROM totals WHERE {sort} IS NOT NULL
            ORDER BY {sort} DESC,id LIMIT 10""",
            (season, phase, minimum_games),
        )
        rows = await cur.fetchall()
    return LeadersPage(season_start=season, phase=phase, minimum_games=minimum_games, leaders=rows)
