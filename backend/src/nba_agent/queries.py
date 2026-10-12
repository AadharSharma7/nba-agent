"""Allowlisted, parameterized statistics queries; no model-generated SQL."""

import hashlib
import json
import re

from psycopg_pool import AsyncConnectionPool

from nba_agent.database import read_connection
from nba_agent.domain import StatsQuery, normalize_name

# Deliberate common names, not fuzzy guesses. Other surname collisions clarify.
PLAYER_ALIASES = {
    "curry": 201939,
    "steph curry": 201939,
    "steph": 201939,
    "lebron": 2544,
    "king james": 2544,
    "kawhi": 202695,
    "joker": 203999,
}


class ClarificationNeeded(Exception):
    def __init__(self, result: dict):
        self.result = result
        super().__init__("Entity resolution needs clarification")


async def resolve_player(conn, name: str) -> list[dict]:
    normalized = normalize_name(name)
    player_id = int(name) if name.isascii() and name.isdigit() else PLAYER_ALIASES.get(normalized)
    if player_id:
        cur = await conn.execute("SELECT id,name FROM players WHERE id=%s", (player_id,))
    else:
        cur = await conn.execute(
            """SELECT id,name,EXISTS(
                SELECT 1 FROM player_game_stats s WHERE s.player_id=players.id
            ) AS has_nba_games FROM players WHERE normalized_name=%s
            ORDER BY has_nba_games DESC,id LIMIT 10""",
            (normalized,),
        )
        exact = await cur.fetchall()
        if exact:
            return prefer_nba_identities(exact)
        cur = await conn.execute(
            """SELECT id,name,EXISTS(
                SELECT 1 FROM player_game_stats s WHERE s.player_id=players.id
            ) AS has_nba_games FROM players WHERE normalized_surname=%s
            ORDER BY has_nba_games DESC,id LIMIT 10""",
            (normalized,),
        )
        return prefer_nba_identities(await cur.fetchall())
    return await cur.fetchall()


def prefer_nba_identities(candidates: list[dict]) -> list[dict]:
    # The biography file includes extra identities without NBA box scores. Scope
    # name resolution to observed NBA identities, independent of requested dates.
    # Never choose the most famous player or the one with the largest game count.
    nba = [player for player in candidates if player["has_nba_games"]]
    return [{"id": player["id"], "name": player["name"]} for player in nba or candidates]


async def ambiguous_expansion(
    pool, query: StatsQuery, message: str, conversation: str = ""
) -> dict | None:
    """Stop an LLM expanding an ambiguous surname into an unsupported identity.

    Actual explicit names/aliases and prior conversation can disambiguate. The
    database, not the model's familiarity with a famous player, decides collisions.
    """
    current = normalize_name(re.sub(r"['’]s\b", "", message, flags=re.I))
    context = normalize_name(re.sub(r"['’]s\b", "", conversation, flags=re.I))

    def mentioned(name, text):
        return bool(re.search(r"\b" + re.escape(name) + r"\b", text))

    async with read_connection(pool) as conn:
        for name in query.players + ([query.opposing_player] if query.opposing_player else []):
            resolved = await resolve_player(conn, name)
            if len(resolved) != 1:
                continue
            player = resolved[0]
            full = normalize_name(player["name"])
            aliases = [alias for alias, pid in PLAYER_ALIASES.items() if pid == player["id"]]
            if any(
                mentioned(n, current) or mentioned(n, context)
                for n in [full, str(player["id"]), *aliases]
            ):
                continue
            cur = await conn.execute(
                "SELECT normalized_surname FROM players WHERE id=%s", (player["id"],)
            )
            surname = (await cur.fetchone())["normalized_surname"]
            if surname and mentioned(surname, current):
                candidates = await resolve_player(conn, surname)
                if len(candidates) > 1:
                    return {
                        "status": "clarification",
                        "entity": surname,
                        "candidates": candidates,
                        "message": "The user supplied an ambiguous surname. Ask which player; do not infer a full name.",
                    }
    return None


async def search_nba_data(pool: AsyncConnectionPool, query: StatsQuery) -> dict:
    async with read_connection(pool) as conn:
        resolved = []
        names = query.players + ([query.opposing_player] if query.opposing_player else [])
        for name in names:
            candidates = await resolve_player(conn, name)
            if len(candidates) != 1:
                return {
                    "status": "clarification" if candidates else "not_found",
                    "entity": name,
                    "candidates": candidates,
                    "message": "Specify the full player name or an offered player ID.",
                }
            resolved.append(candidates[0])
        player_ids = list(dict.fromkeys(p["id"] for p in resolved[: len(query.players)]))
        params: list = [player_ids]
        filters = ["p.player_id = ANY(%s)", "p.appeared"]
        if query.phase != "both":
            filters.append("g.phase=%s")
            params.append(query.phase)
        if query.season_start is not None:
            filters.append("g.season_start >= %s")
            params.append(query.season_start)
        if query.season_end is not None:
            filters.append("g.season_start <= %s")
            params.append(query.season_end)
        for field, value in [
            ("playoff_round", query.playoff_round),
            ("conference", query.conference),
        ]:
            if value is not None:
                filters.append(f"g.{field}=%s")
                params.append(value)
        if query.home is not None:
            filters.append("p.home=%s")
            params.append(query.home)
        if query.date_from:
            filters.append("g.played_at::date >= %s")
            params.append(query.date_from)
        if query.date_to:
            filters.append("g.played_at::date <= %s")
            params.append(query.date_to)
        opponent = None
        if query.opponent_team:
            cur = await conn.execute(
                "SELECT t.id,t.name FROM teams t JOIN team_aliases a ON a.team_id=t.id WHERE a.alias=%s ORDER BY t.id",
                (normalize_name(query.opponent_team),),
            )
            candidates = await cur.fetchall()
            if len(candidates) != 1:
                return {
                    "status": "clarification" if candidates else "not_found",
                    "entity": query.opponent_team,
                    "candidates": candidates,
                    "message": "Specify an unambiguous NBA team name or abbreviation.",
                }
            opponent = candidates[0]
            filters.append("p.opponent_team_id=%s")
            params.append(opponent["id"])
        if query.opposing_player:
            filters.append(
                "EXISTS (SELECT 1 FROM player_game_stats other WHERE other.game_id=p.game_id AND other.player_id=%s AND other.appeared AND other.team_id<>p.team_id)"
            )
            params.append(resolved[-1]["id"])
        # Only fixed, developer-owned SQL fragments are interpolated. All user/model values are parameters.
        cte = f"""WITH ranked AS (
            SELECT p.*, pl.name player_name, g.season_start, g.phase, g.played_at,
                   g.home_team_name, g.away_team_name,
                   row_number() OVER(PARTITION BY p.player_id ORDER BY g.played_at DESC,g.id DESC) rn
            FROM player_game_stats p JOIN games g ON g.id=p.game_id
            JOIN players pl ON pl.id=p.player_id WHERE {" AND ".join(filters)}
        ), selected AS (SELECT * FROM ranked WHERE rn<=%s) """
        params.append(query.last_n or 1000000)
        if query.group_by == "game":
            body = """SELECT player_id,player_name,game_id,season_start,phase,played_at::date game_date,
                CASE WHEN home THEN home_team_name ELSE away_team_name END team,
                CASE WHEN home THEN away_team_name ELSE home_team_name END opponent,
                home,minutes,points,rebounds,assists,steals,blocks,turnovers,fgm,fga,fg3m,fg3a,ftm,fta
                FROM selected ORDER BY played_at DESC,game_id DESC,player_id LIMIT %s"""
        else:
            group = "player_id,player_name" + (
                ",season_start" if query.group_by == "season" else ""
            )
            body = f"""SELECT {group}, count(*) games,
                min(played_at)::date first_game, max(played_at)::date last_game,
                sum(points) points, sum(rebounds) rebounds, sum(assists) assists,
                avg(points)::float8 ppg, avg(rebounds)::float8 rpg, avg(assists)::float8 apg,
                avg(minutes)::float8 mpg, avg(steals)::float8 spg, avg(blocks)::float8 bpg,
                avg(turnovers)::float8 tpg,
                sum(fgm) fgm,sum(fga) fga,sum(fg3m) fg3m,sum(fg3a) fg3a,sum(ftm) ftm,sum(fta) fta,
                100.0*sum(fgm)::float8/nullif(sum(fga),0) fg_pct,
                100.0*sum(fg3m)::float8/nullif(sum(fg3a),0) three_pct,
                100.0*sum(ftm)::float8/nullif(sum(fta),0) ft_pct,
                count(points) points_games,count(rebounds) rebounds_games,count(assists) assists_games,
                count(minutes) minutes_games
                FROM selected GROUP BY {group} ORDER BY {group} LIMIT %s"""
        cur = await conn.execute(cte + body, (*params, query.limit + 1))
        result_rows = await cur.fetchall()
        truncated = len(result_rows) > query.limit
        result_rows = result_rows[: query.limit]
        cur = await conn.execute(
            "SELECT id,version,source_url,max_game_date,source_updated_at FROM source_datasets ORDER BY imported_at DESC LIMIT 1"
        )
        source = await cur.fetchone()
    normalized_query = query.model_dump(mode="json")
    evidence_id = hashlib.sha256(
        json.dumps([normalized_query, source], sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    found = {r["player_id"] for r in result_rows}
    return json.loads(
        json.dumps(
            {
                "status": "ok" if result_rows else "no_data",
                "evidence_id": evidence_id,
                "query": normalized_query,
                "players": resolved[: len(query.players)],
                "opponent": opponent,
                "opposing_player": resolved[-1] if query.opposing_player else None,
                "rows": result_rows,
                "truncated": truncated,
                "players_without_returned_rows": [
                    p for p in resolved[: len(query.players)] if p["id"] not in found
                ],
                "source": source,
                "definitions": {
                    "season_start": "2020 denotes 2020–21; playoffs held in 2021.",
                    "percentages": "100 times total makes divided by total attempts; not mean game percentages.",
                    "opposing_player": "Both recorded appearances on opposing teams; not guarding or shared court time.",
                    "minutes": "Values supplied by the dataset.",
                    "last_n": "Last N matching appearances per player after other filters.",
                },
            },
            default=str,
        )
    )
