"""One-time, atomic import of the pinned Kaggle snapshot. Never repairs source stats."""

import csv
import hashlib
import json
import re
from datetime import datetime
from importlib.resources import files

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from nba_agent.config import Settings
from nba_agent.domain import normalize_name

SOURCE_ID = "kaggle-v515"


def admin_dsn(settings: Settings) -> str:
    dbname = conninfo_to_dict(settings.database_url.get_secret_value())["dbname"]
    return make_conninfo(settings.admin_database_url.get_secret_value(), dbname=dbname)


def bootstrap(settings: Settings) -> None:
    app = conninfo_to_dict(settings.database_url.get_secret_value())
    if app.get("dbname") != "nba_agent" or app.get("user") != "nba_agent_app":
        raise ValueError("Local bootstrap is restricted to nba_agent and nba_agent_app")
    with psycopg.connect(settings.admin_database_url.get_secret_value(), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (app["user"],)).fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(app["user"]), sql.Literal(app.get("password", "")))
            )
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (app["dbname"],)
        ).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(app["dbname"])))
    with psycopg.connect(admin_dsn(settings)) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(619402)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for migration in sorted(
            files("nba_agent").joinpath("migrations").iterdir(), key=lambda p: p.name
        ):
            if not migration.name.endswith(".sql"):
                continue
            content = migration.read_text()
            digest = hashlib.sha256(content.encode()).hexdigest()
            existing = conn.execute(
                "SELECT sha256 FROM schema_migrations WHERE name=%s", (migration.name,)
            ).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError(f"Applied migration changed: {migration.name}")
                continue
            conn.execute(content)
            conn.execute(
                "INSERT INTO schema_migrations(name,sha256) VALUES(%s,%s)", (migration.name, digest)
            )
        conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA public TO nba_agent_app")
        conn.execute(
            "GRANT SELECT ON source_datasets, players, teams, team_aliases, games, player_game_stats, team_game_stats TO nba_agent_app"
        )
        conn.execute("GRANT SELECT, INSERT ON session_details TO nba_agent_app")
        conn.execute("GRANT SELECT, INSERT ON message_details TO nba_agent_app")
        conn.execute("GRANT SELECT, INSERT ON model_metrics TO nba_agent_app")
        conn.execute(
            "GRANT UPDATE (metrics_started_at,input_tokens,output_tokens,cached_input_tokens,"
            "estimated_cost_usd,model_call_count,user_message_count,usage_complete,cost_complete,"
            "last_activity_at) ON session_details TO nba_agent_app"
        )
    print("Database and migrations ready.", flush=True)


def number(value: str | None, *, integer=True):
    if value is None or not value.strip():
        return None
    parsed = float(value)
    return int(parsed) if integer else parsed


def season_scope(game_id: str) -> tuple[str, int, str] | None:
    gid = game_id.zfill(10)
    if not re.fullmatch(r"\d{10}", gid) or gid[:3] not in {"002", "004"}:
        return None
    year = int(gid[3:5])
    year += 1900 if year >= 46 else 2000
    return (gid, year, "regular" if gid[:3] == "002" else "playoffs") if year >= 1949 else None


def round_label(label: str) -> tuple[str | None, str | None]:
    label = label.lower()
    conference = "west" if "west" in label else "east" if "east" in label else None
    if "conf" in label and "final" in label and "semi" not in label:
        return "conference_finals", conference
    if "semi" in label:
        return "conference_semifinals", conference
    if "first" in label or "1st" in label:
        return "first_round", conference
    if "final" in label:
        return "finals", conference
    return None, conference


def appeared(row: dict) -> bool:
    # Positive activity takes precedence over contradictory DNP comments.
    if any(
        (number(row.get(k), integer=False) or 0) > 0
        for k in (
            "numMinutes",
            "points",
            "assists",
            "reboundsTotal",
            "steals",
            "blocks",
            "fieldGoalsAttempted",
            "freeThrowsAttempted",
            "turnovers",
            "foulsPersonal",
        )
    ):
        return True
    comment = row.get("comment", "").upper()
    if any(token in comment for token in ("DNP", "DND", "INACTIVE", "DID NOT", "NOT WITH TEAM")):
        return False
    # Integer source minutes can round a real short appearance to zero.
    return number(row.get("numMinutes"), integer=False) is not None


def ingest(settings: Settings) -> dict:
    manifest = json.loads(settings.manifest_path.read_text())
    if manifest["metadata"]["currentVersionNumber"] != 515:
        raise ValueError("This importer targets the audited v515 snapshot")
    required = {
        "Games.csv",
        "Players.csv",
        "TeamHistories.csv",
        "PlayerStatistics.csv",
        "TeamStatistics.csv",
    }
    for item in manifest["files"]:
        if item["name"] in required:
            with (settings.data_dir / item["name"]).open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"Source checksum mismatch: {item['name']}")

    def records(filename):
        with (settings.data_dir / filename).open(newline="", encoding="utf-8-sig") as source:
            yield from csv.DictReader(source)

    games = {r["gameId"]: r for r in records("Games.csv") if season_scope(r["gameId"])}
    teams, aliases = {}, set()
    for game in games.values():
        for side in ("home", "away"):
            team = int(game[f"{side}teamId"])
            full_name = f"{game[f'{side}teamCity']} {game[f'{side}teamName']}".strip()
            teams.setdefault(team, full_name)  # Games file is newest first.
            aliases.add((team, normalize_name(full_name)))
            aliases.add((team, normalize_name(game[f"{side}teamName"])))
    for row in records("TeamHistories.csv"):
        team = int(row["teamId"])
        if row["league"] == "NBA" and team in teams:
            for name in (
                row["teamAbbrev"],
                row["teamName"],
                f"{row['teamCity']} {row['teamName']}",
            ):
                aliases.add((team, normalize_name(name)))
    counts = {
        "games": len(games),
        "teams": len(teams),
        "players": 0,
        "player_game_stats": 0,
        "team_game_stats": 0,
        "excluded_team_rows": 0,
    }
    with psycopg.connect(admin_dsn(settings)) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(619403)")
        previous = conn.execute(
            "SELECT archive_sha256,counts FROM source_datasets WHERE id=%s", (SOURCE_ID,)
        ).fetchone()
        if previous:
            if previous[0] != manifest["archive_sha256"]:
                raise ValueError("Snapshot ID already exists with a different checksum")
            print("Snapshot already imported; no changes.")
            return previous[1]
        dates = [datetime.fromisoformat(g["gameDateTimeEst"]).date() for g in games.values()]
        conn.execute(
            "INSERT INTO source_datasets(id,version,source_url,archive_sha256,source_updated_at,min_game_date,max_game_date) VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                SOURCE_ID,
                515,
                manifest["source"],
                manifest["archive_sha256"],
                manifest["metadata"]["lastUpdated"],
                min(dates),
                max(dates),
            ),
        )
        with conn.cursor().copy(
            "COPY players(id,name,normalized_name,normalized_surname) FROM STDIN"
        ) as copy:
            for row in records("Players.csv"):
                name = f"{row['firstName']} {row['lastName']}".strip()
                copy.write_row(
                    (
                        int(row["personId"]),
                        name,
                        normalize_name(name),
                        normalize_name(row["lastName"]),
                    )
                )
                counts["players"] += 1
        with conn.cursor().copy("COPY teams(id,name) FROM STDIN") as copy:
            for item in teams.items():
                copy.write_row(item)
        with conn.cursor().copy("COPY team_aliases(team_id,alias) FROM STDIN") as copy:
            for item in sorted(aliases):
                copy.write_row(item)
        with conn.cursor().copy(
            "COPY games(id,source_id,season_start,phase,played_at,home_team_id,away_team_id,home_team_name,away_team_name,home_score,away_score,playoff_round,conference,series_game_number,source_label) FROM STDIN"
        ) as copy:
            for raw_id, row in games.items():
                gid, year, phase = season_scope(raw_id)
                rnd, conference = round_label(row["gameLabel"])
                game_number = re.search(r"\d+", row["seriesGameNumber"])
                copy.write_row(
                    (
                        gid,
                        SOURCE_ID,
                        year,
                        phase,
                        datetime.fromisoformat(row["gameDateTimeEst"]),
                        int(row["hometeamId"]),
                        int(row["awayteamId"]),
                        f"{row['hometeamCity']} {row['hometeamName']}",
                        f"{row['awayteamCity']} {row['awayteamName']}",
                        number(row["homeScore"]),
                        number(row["awayScore"]),
                        rnd,
                        conference,
                        int(game_number[0]) if game_number else None,
                        row["gameLabel"] or None,
                    )
                )
        print("Dimensions and games loaded; copying player statistics…", flush=True)
        stat_keys = (
            "points",
            "reboundsTotal",
            "assists",
            "steals",
            "blocks",
            "turnovers",
            "fieldGoalsMade",
            "fieldGoalsAttempted",
            "threePointersMade",
            "threePointersAttempted",
            "freeThrowsMade",
            "freeThrowsAttempted",
        )
        with conn.cursor().copy(
            "COPY player_game_stats(player_id,game_id,team_id,opponent_team_id,home,appeared,minutes,points,rebounds,assists,steals,blocks,turnovers,fgm,fga,fg3m,fg3a,ftm,fta,source_comment) FROM STDIN"
        ) as copy:
            for row in records("PlayerStatistics.csv"):
                game = games.get(row["gameId"])
                if not game:
                    continue
                home = row["home"] == "1"
                team = int(game["hometeamId" if home else "awayteamId"])
                opponent = int(game["awayteamId" if home else "hometeamId"])
                copy.write_row(
                    (
                        int(row["personId"]),
                        row["gameId"].zfill(10),
                        team,
                        opponent,
                        home,
                        appeared(row),
                        number(row["numMinutes"], integer=False),
                        *(number(row[k]) for k in stat_keys),
                        row["comment"] or None,
                    )
                )
                counts["player_game_stats"] += 1
        with conn.cursor().copy(
            "COPY team_game_stats(team_id,game_id,points,rebounds,assists) FROM STDIN"
        ) as copy:
            for row in records("TeamStatistics.csv"):
                game = games.get(row["gameId"])
                if not game:
                    continue
                if row["teamId"] not in (game["hometeamId"], game["awayteamId"]):
                    counts["excluded_team_rows"] += 1
                    continue
                copy.write_row(
                    (
                        int(row["teamId"]),
                        row["gameId"].zfill(10),
                        number(row["teamScore"]),
                        number(row["reboundsTotal"]),
                        number(row["assists"]),
                    )
                )
                counts["team_game_stats"] += 1
        conn.execute("UPDATE source_datasets SET counts=%s WHERE id=%s", (Jsonb(counts), SOURCE_ID))
    with psycopg.connect(admin_dsn(settings), autocommit=True) as conn:
        conn.execute("ANALYZE")
    print(json.dumps(counts, indent=2), flush=True)
    return counts
