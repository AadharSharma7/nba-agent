"""Reproducible, read-only source-data audit. This is not application code.

Requires duckdb==1.5.6. Writes an isolated audit database and JSON reports.
Original Kaggle files are never modified.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/kaggle/v515"
REPORTS = ROOT / "audit/reports"
WORK = ROOT / "audit/work"
TABLES = {
    "games": "Games.csv", "players": "Players.csv",
    "team_histories": "TeamHistories.csv", "player_stats": "PlayerStatistics.csv",
    "team_stats": "TeamStatistics.csv", "player_extended": "PlayerStatisticsExtended.csv",
    "team_extended": "TeamStatisticsExtended.csv",
    "schedule_2024": "LeagueSchedule24_25.csv", "schedule_2025": "LeagueSchedule25_26.csv",
}


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def records(con, sql):
    result = con.execute(sql)
    names = [x[0] for x in result.description]
    return [dict(zip(names, row)) for row in result.fetchall()]


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WORK / "audit.duckdb"))
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")
    con.execute(f"SET temp_directory='{WORK / 'temp'}'")
    report = {"audited_at": datetime.now(timezone.utc).isoformat(),
              "duckdb_version": duckdb.__version__, "source_version": 515, "tables": {}}
    for table, filename in TABLES.items():
        print(f"Loading {filename}", flush=True)
        if "--reuse-tables" not in sys.argv:
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv(?, header=true, all_varchar=true, sample_size=-1)", [str(RAW / filename)])
        cols = [x[0] for x in con.execute(f"DESCRIBE {table}").fetchall()]
        null_sql = ', '.join(f"count(*) FILTER (WHERE {quote(c)} IS NULL) AS {quote(c)}" for c in cols)
        item = {"file": filename, "rows": con.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                "columns": cols, "null_counts": records(con, f"SELECT {null_sql} FROM {table}")[0]}
        if "gameDateTimeEst" in cols:
            item["date_range"] = records(con, f"SELECT min(gameDateTimeEst) first_date, max(gameDateTimeEst) last_date, count(*) FILTER (WHERE try_cast(gameDateTimeEst AS TIMESTAMP) IS NULL) invalid_dates FROM {table}")[0]
        keys = [c for c in ["gameId", "personId" if table.startswith('player') else "teamId"] if c in cols]
        if table == "team_histories":
            keys = ["teamId", "seasonFounded", "seasonActiveTill", "league"]
        if keys:
            group = ','.join(map(quote, keys))
            item["key"] = keys
            item["duplicate_keys"] = records(con, f"SELECT count(*) duplicate_groups, coalesce(sum(n-1),0) excess_rows FROM (SELECT count(*) n FROM {table} GROUP BY {group} HAVING count(*) > 1)")[0]
            item["duplicate_examples"] = records(con, f"SELECT {group}, count(*) n FROM {table} GROUP BY {group} HAVING count(*) > 1 ORDER BY n DESC LIMIT 10")
        report["tables"][table] = item
        print(f"  {item['rows']:,} rows; {len(cols)} columns; duplicates={item.get('duplicate_keys')}", flush=True)
        (REPORTS / "profile.json").write_text(json.dumps(report, indent=2, default=str))
    con.execute(f"CREATE OR REPLACE VIEW pbp AS SELECT * FROM read_parquet('{RAW / 'PlayByPlay.parquet'}')")
    report["tables"]["play_by_play"] = {"schema": records(con, "DESCRIBE pbp"), "rows": con.execute("SELECT count(*) FROM pbp").fetchone()[0], "sample": records(con, "SELECT * FROM pbp LIMIT 2")}
    report["categories"] = {}
    for table in ["games", "player_stats", "team_stats", "player_extended", "team_extended"]:
        for col in ["gameType", "gameLabel", "seriesGameNumber"]:
            report["categories"][f"{table}.{col}"] = records(con, f"SELECT {quote(col)} AS category, count(*) n FROM {table} GROUP BY 1 ORDER BY 2 DESC LIMIT 30")
    report["categories"]["team_histories.league"] = records(con, "SELECT league,count(*) n FROM team_histories GROUP BY 1")
    report["categories"]["game_id_prefix"] = records(con, "SELECT left(lpad(gameId,10,'0'),3) prefix, count(*) n, min(gameDateTimeEst) first_date, max(gameDateTimeEst) last_date FROM games GROUP BY 1 ORDER BY 1")
    (REPORTS / "profile.json").write_text(json.dumps(report, indent=2, default=str))
    con.close()
    print("Profile complete", flush=True)


if __name__ == "__main__":
    main()
