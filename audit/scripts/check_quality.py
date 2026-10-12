"""Audit coverage, joins, basketball identities and priority queries.

All transformations are TEMP views in the audit connection. No source or
PostgreSQL data is changed. Query SQL is retained alongside every result.
"""
import json
import time
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'audit/reports'


def rows(con, sql):
    r = con.execute(sql)
    names = [d[0] for d in r.description]
    return [dict(zip(names, x)) for x in r.fetchall()]


def main():
    con = duckdb.connect(str(ROOT / 'audit/work/audit.duckdb'), read_only=True)
    con.execute("SET memory_limit='2GB'")
    con.execute("SET threads=4")
    con.execute("CREATE TEMP VIEW g AS SELECT *, lpad(gameId,10,'0') gid10, left(lpad(gameId,10,'0'),3) prefix, CASE WHEN try_cast(substr(lpad(gameId,10,'0'),4,2) AS INT)>=46 THEN 1900 ELSE 2000 END + try_cast(substr(lpad(gameId,10,'0'),4,2) AS INT) season_start, try_cast(gameDateTimeEst AS TIMESTAMP) played_at, try_cast(homeScore AS INT) hs, try_cast(awayScore AS INT) aws FROM games")
    con.execute("CREATE TEMP VIEW nba_games AS SELECT * FROM g WHERE season_start>=1949 AND prefix IN ('002','004')")
    numeric = ['numMinutes','points','assists','reboundsTotal','reboundsOffensive','reboundsDefensive','steals','blocks','turnovers','fieldGoalsMade','fieldGoalsAttempted','threePointersMade','threePointersAttempted','freeThrowsMade','freeThrowsAttempted','foulsPersonal','plusMinusPoints']
    casts = ','.join(f'try_cast(p.{c} AS DOUBLE) AS n_{c}' for c in numeric)
    con.execute(f"CREATE TEMP VIEW p AS SELECT p.*, {casts}, g.season_start, g.prefix, g.played_at, g.gameLabel canonical_label, g.gameType canonical_type, g.hometeamId, g.awayteamId, CASE WHEN p.home='1' THEN g.hometeamId WHEN p.home='0' THEN g.awayteamId END inferred_team_id FROM player_stats p LEFT JOIN g USING(gameId)")
    con.execute("CREATE TEMP VIEW np AS SELECT * FROM p WHERE season_start>=1949 AND prefix IN ('002','004')")
    # This participation rule is used ONLY for the modern priority examples.
    # Historical zero-minute rows can be genuine appearances, not DNPs.
    con.execute("CREATE TEMP VIEW appearances AS SELECT * FROM np WHERE n_numMinutes>0")
    checks = {}

    def check(name, sql):
        print(name, flush=True)
        started = time.monotonic()
        try:
            checks[name] = {'sql': sql, 'rows': rows(con, sql), 'seconds': round(time.monotonic()-started,3)}
        except Exception as exc:
            checks[name] = {'sql': sql, 'error': str(exc)}
        (OUT / 'quality_checks.json').write_text(json.dumps(checks, indent=2, default=str))

    check('nba_scope', "SELECT prefix, count(*) games, min(played_at) first_game, max(played_at) last_game, min(season_start) first_season, max(season_start) last_season FROM nba_games GROUP BY 1 ORDER BY 1")
    check('excluded_game_categories', "SELECT prefix, CASE WHEN season_start<1949 THEN 'pre-NBA boundary' ELSE 'outside regular/playoff analytics' END reason,count(*) games FROM g WHERE NOT(season_start>=1949 AND prefix IN ('002','004')) GROUP BY 1,2 ORDER BY 1,2")
    check('game_type_by_prefix', "SELECT prefix, gameType, count(*) games FROM g GROUP BY 1,2 ORDER BY 1,2")
    check('season_date_disagreements', "SELECT gameId,gameDateTimeEst,season_start FROM g WHERE year(played_at) NOT BETWEEN season_start AND season_start+1 LIMIT 30")
    check('game_score_integrity', "SELECT count(*) FILTER(WHERE hs<=0 OR aws<=0) nonpositive_score_games, count(*) FILTER(WHERE hs=aws) tied_games, count(*) FILTER(WHERE hometeamId=awayteamId) same_team_games, count(*) FILTER(WHERE winner IS DISTINCT FROM CASE WHEN hs>aws THEN hometeamId WHEN aws>hs THEN awayteamId END) wrong_winner FROM nba_games")
    check('player_orphans', "SELECT count(*) FILTER(WHERE g.gameId IS NULL) rows_without_game, count(*) FILTER(WHERE pl.personId IS NULL) rows_without_player_bio, count(*) FILTER(WHERE p.personId IS NULL) null_player_ids FROM player_stats p LEFT JOIN games g USING(gameId) LEFT JOIN players pl USING(personId)")
    check('orphan_player_examples', "SELECT p.personId,p.firstName,p.lastName,count(*) AS row_count FROM player_stats p LEFT JOIN players pl USING(personId) WHERE pl.personId IS NULL GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 15")
    check('orphan_games_in_team_stats', "SELECT t.gameId,t.teamId,t.gameDateTimeEst FROM team_stats t LEFT JOIN games g USING(gameId) WHERE g.gameId IS NULL")
    check('player_game_metadata', "SELECT count(*) missing_source_team_ids, count(*) FILTER(WHERE inferred_team_id IS NOT NULL) recoverable_from_game_and_home FROM np WHERE playerteamId IS NULL")
    check('player_game_metadata_conflicts', "SELECT count(*) FILTER(WHERE playerteamId IS NOT NULL AND playerteamId IS DISTINCT FROM inferred_team_id) team_conflicts, count(*) FILTER(WHERE opponentteamId IS NOT NULL AND opponentteamId IS DISTINCT FROM CASE WHEN home='1' THEN awayteamId WHEN home='0' THEN hometeamId END) opponent_conflicts, count(*) FILTER(WHERE try_cast(gameDateTimeEst AS TIMESTAMP) IS DISTINCT FROM played_at) date_conflicts_or_missing FROM np")
    check('participation_patterns', "SELECT CASE WHEN n_numMinutes>0 THEN 'positive minutes' WHEN n_numMinutes=0 THEN 'zero minutes' ELSE 'missing minutes' END minutes_status, comment IS NOT NULL has_comment, count(*) AS row_count, count(*) FILTER(WHERE n_points>0) rows_with_points FROM np GROUP BY 1,2 ORDER BY 1,2")
    check('participation_comments', "SELECT comment,count(*) AS row_count FROM np WHERE comment IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 25")
    check('zero_minutes_with_stats', "SELECT personId,firstName,lastName,gameId,gameDateTimeEst,numMinutes,points,assists,reboundsTotal,comment FROM np WHERE n_numMinutes=0 AND (n_points>0 OR n_assists>0 OR n_reboundsTotal>0) LIMIT 30")
    check('played_with_dnp_comment', "SELECT personId,gameId,numMinutes,comment FROM np WHERE n_numMinutes>0 AND (comment ILIKE '%DNP%' OR comment ILIKE '%DND%' OR comment ILIKE '%inactive%') LIMIT 30")
    check('numeric_parse_failures', 'SELECT ' + ','.join(f'count(*) FILTER(WHERE {c} IS NOT NULL AND n_{c} IS NULL) AS {c}' for c in numeric) + ' FROM np')
    check('box_score_identities', "SELECT count(*) FILTER(WHERE n_points IS NOT NULL AND n_fieldGoalsMade IS NOT NULL AND n_freeThrowsMade IS NOT NULL AND n_points<>2*n_fieldGoalsMade+coalesce(n_threePointersMade,0)+n_freeThrowsMade) points_formula_conflicts, count(*) FILTER(WHERE n_fieldGoalsMade>n_fieldGoalsAttempted OR n_threePointersMade>n_threePointersAttempted OR n_freeThrowsMade>n_freeThrowsAttempted) makes_exceed_attempts, count(*) FILTER(WHERE n_threePointersMade>n_fieldGoalsMade OR n_threePointersAttempted>n_fieldGoalsAttempted) threes_exceed_field_goals, count(*) FILTER(WHERE n_reboundsTotal<>n_reboundsOffensive+n_reboundsDefensive) rebound_sum_conflicts, count(*) FILTER(WHERE n_points<0 OR n_assists<0 OR n_reboundsTotal<0 OR n_numMinutes<0) negative_count_or_minutes FROM np")
    check('points_formula_examples', "SELECT gameId,personId,gameDateTimeEst,points,fieldGoalsMade,threePointersMade,freeThrowsMade FROM np WHERE n_points<>2*n_fieldGoalsMade+coalesce(n_threePointersMade,0)+n_freeThrowsMade LIMIT 20")
    check('season_coverage', "WITH pc AS (SELECT gameId,count(*) player_rows,count(*) FILTER(WHERE try_cast(numMinutes AS DOUBLE)>0) positive_minutes_rows FROM player_stats GROUP BY 1), tc AS (SELECT gameId,count(*) team_rows FROM team_stats GROUP BY 1) SELECT g.season_start,g.prefix,count(*) games,count(*) FILTER(WHERE pc.gameId IS NULL) games_without_player_rows,count(*) FILTER(WHERE tc.gameId IS NULL) games_without_team_rows,count(*) FILTER(WHERE tc.team_rows<>2) games_with_wrong_team_row_count,sum(coalesce(pc.player_rows,0)) player_rows,min(played_at) first_game,max(played_at) last_game FROM nba_games g LEFT JOIN pc USING(gameId) LEFT JOIN tc USING(gameId) GROUP BY 1,2 ORDER BY 1,2")
    check('metric_completeness_by_season', 'SELECT season_start,prefix,count(*) AS row_count,' + ','.join(f'count(*) FILTER(WHERE n_{c} IS NULL) missing_{c}' for c in ['numMinutes','points','assists','reboundsTotal','steals','blocks','turnovers','fieldGoalsMade','fieldGoalsAttempted','threePointersMade','threePointersAttempted']) + ' FROM np GROUP BY 1,2 ORDER BY 1,2')
    check('team_game_score_conflicts', "SELECT count(*) AS row_count, count(*) FILTER(WHERE t.teamId NOT IN(g.hometeamId,g.awayteamId)) unexpected_team, count(*) FILTER(WHERE try_cast(t.teamScore AS INT) IS DISTINCT FROM CASE WHEN t.teamId=g.hometeamId THEN g.hs ELSE g.aws END) score_conflicts FROM team_stats t JOIN nba_games g USING(gameId)")
    con.execute("CREATE TEMP VIEW point_reconciliation AS WITH ps AS (SELECT gameId,inferred_team_id team_id,count(*) AS row_count,count(*) FILTER(WHERE n_points IS NULL) missing_points,sum(n_points) player_points FROM np GROUP BY 1,2), sides AS (SELECT gameId,season_start,hometeamId team_id,hs score FROM nba_games UNION ALL SELECT gameId,season_start,awayteamId team_id,aws score FROM nba_games) SELECT sides.*,ps.row_count,ps.missing_points,ps.player_points FROM sides LEFT JOIN ps USING(gameId,team_id)")
    check('player_to_team_points', "SELECT count(*) team_games,count(*) FILTER(WHERE row_count IS NULL) no_player_rows,count(*) FILTER(WHERE missing_points>0) incomplete_points,count(*) FILTER(WHERE missing_points=0 AND player_points<>score) non_null_player_points_score_conflicts FROM point_reconciliation")
    check('player_to_team_points_by_era', "SELECT (season_start//10)*10 AS decade_start,count(*) team_games,count(*) FILTER(WHERE missing_points=0 AND player_points<>score) score_conflicts,count(*) FILTER(WHERE missing_points>0) incomplete_points FROM point_reconciliation GROUP BY 1 ORDER BY 1")
    check('player_to_team_points_examples', "SELECT * FROM point_reconciliation WHERE missing_points=0 AND player_points<>score ORDER BY season_start DESC LIMIT 25")
    check('series_metadata_coverage', "SELECT season_start,count(*) playoff_games,count(*) FILTER(WHERE gameLabel IS NULL) missing_round,count(*) FILTER(WHERE try_cast(regexp_extract(seriesGameNumber,'[0-9]+',0) AS INT) IS NULL) missing_game_number FROM nba_games WHERE prefix='004' GROUP BY 1 ORDER BY 1")
    check('priority_identities', "SELECT personId,firstName,lastName,fromYear,toYear FROM players WHERE lastName IN ('Curry','Jokic','Embiid','Leonard','James') ORDER BY lastName,firstName")
    con.execute("CREATE TEMP VIEW curry_wcf AS SELECT * FROM appearances WHERE personId='201939' AND season_start=2015 AND prefix='004' AND (playerteamId='1610612760' OR opponentteamId='1610612760')")
    check('curry_wcf_games', "SELECT gameId,gameDateTimeEst,canonical_label,seriesGameNumber,home,numMinutes,points,reboundsTotal,assists,fieldGoalsMade,fieldGoalsAttempted,threePointersMade,threePointersAttempted,freeThrowsMade,freeThrowsAttempted FROM curry_wcf ORDER BY played_at")
    aggregates = "count(*) games,sum(n_points) points,sum(n_reboundsTotal) rebounds,sum(n_assists) assists,avg(n_points) ppg,avg(n_reboundsTotal) rpg,avg(n_assists) apg,sum(n_numMinutes) source_minutes,avg(n_numMinutes) source_mpg,sum(n_fieldGoalsMade)/nullif(sum(n_fieldGoalsAttempted),0) fg_pct,sum(n_threePointersMade)/nullif(sum(n_threePointersAttempted),0) three_pct,sum(n_freeThrowsMade)/nullif(sum(n_freeThrowsAttempted),0) ft_pct"
    check('curry_wcf_totals', f"SELECT {aggregates} FROM curry_wcf")
    check('jokic_embiid_playoffs', f"SELECT personId,firstName,lastName,season_start, {aggregates} FROM appearances WHERE personId IN ('203999','203954') AND prefix='004' AND season_start>=2020 GROUP BY 1,2,3,4 ORDER BY personId,season_start")
    check('jokic_embiid_playoffs_pooled', f"SELECT personId,firstName,lastName,{aggregates} FROM appearances WHERE personId IN ('203999','203954') AND prefix='004' AND season_start>=2020 GROUP BY 1,2,3 ORDER BY personId")
    check('priority_participation_exclusions', "SELECT personId,season_start,comment,numMinutes,count(*) AS row_count FROM np WHERE personId IN ('203999','203954') AND prefix='004' AND season_start>=2020 AND (n_numMinutes IS NULL OR n_numMinutes<=0) GROUP BY 1,2,3,4 ORDER BY 1,2")
    con.execute("CREATE TEMP VIEW kawhi_lebron AS SELECT k.* FROM appearances k JOIN appearances l ON k.gameId=l.gameId WHERE k.personId='202695' AND l.personId='2544' AND k.inferred_team_id<>l.inferred_team_id")
    check('kawhi_lebron_shared_games', "SELECT gameId,gameDateTimeEst,prefix,playerteamName,opponentteamName,points,reboundsTotal,assists,numMinutes FROM kawhi_lebron ORDER BY played_at")
    check('kawhi_lebron_totals', f"SELECT prefix,{aggregates} FROM kawhi_lebron GROUP BY 1 ORDER BY 1")
    check('extended_coverage', "SELECT 'player' kind,count(*) AS row_count,count(DISTINCT gameId) games,min(gameDateTimeEst) first_date,max(gameDateTimeEst) last_date,count(*) FILTER(WHERE offensiveRating IS NULL) missing_offensive_rating FROM player_extended UNION ALL SELECT 'team',count(*),count(DISTINCT gameId),min(gameDateTimeEst),max(gameDateTimeEst),count(*) FILTER(WHERE offensiveRating IS NULL) FROM team_extended")
    check('extended_player_base_reconciliation', "SELECT count(*) matched_rows,count(*) FILTER(WHERE try_cast(e.points AS DOUBLE) IS DISTINCT FROM try_cast(p.points AS DOUBLE)) points_conflicts,count(*) FILTER(WHERE try_cast(e.numMinutes AS DOUBLE) IS DISTINCT FROM try_cast(p.numMinutes AS DOUBLE)) minutes_conflicts,count(*) FILTER(WHERE e.playerteamId IS DISTINCT FROM p.playerteamId) team_id_conflicts FROM player_extended e JOIN player_stats p USING(gameId,personId)")
    check('extended_orphans', "SELECT count(*) extended_rows_without_base FROM player_extended e LEFT JOIN player_stats p USING(gameId,personId) WHERE p.gameId IS NULL")
    check('non_nba_team_history_in_scope', "SELECT g.gameId,g.season_start,g.hometeamName,g.awayteamName FROM nba_games g WHERE NOT EXISTS(SELECT 1 FROM team_histories h WHERE h.teamId=g.hometeamId AND h.league='NBA') OR NOT EXISTS(SELECT 1 FROM team_histories h WHERE h.teamId=g.awayteamId AND h.league='NBA') LIMIT 30")
    check('team_history_overlaps', "SELECT a.teamId,a.teamCity,a.teamName,a.league,a.seasonFounded,a.seasonActiveTill,b.teamCity other_city,b.teamName other_name,b.seasonFounded other_start,b.seasonActiveTill other_end FROM team_histories a JOIN team_histories b ON a.teamId=b.teamId AND a.seasonFounded<b.seasonFounded AND try_cast(a.seasonActiveTill AS INT)>=try_cast(b.seasonFounded AS INT) WHERE a.league='NBA' AND b.league='NBA'")
    check('pbp_scope', "SELECT count(*) AS row_count,count(DISTINCT gameId) games,min(gameDateTimeEst) first_date,max(gameDateTimeEst) last_date,count(*) FILTER(WHERE gameId IS NULL) missing_game_id,count(*) FILTER(WHERE actionNumber IS NULL) missing_action_number,count(*) FILTER(WHERE actionId IS NULL) missing_action_id FROM pbp")
    check('pbp_duplicate_event_keys', "SELECT count(*) duplicate_groups,coalesce(sum(n-1),0) excess_rows FROM (SELECT count(*) n FROM pbp WHERE actionNumber IS NOT NULL GROUP BY gameId,actionNumber HAVING count(*)>1)")
    check('pbp_coverage_by_season', "WITH ids AS (SELECT DISTINCT gameId FROM pbp) SELECT g.season_start,g.prefix,count(*) games,count(*) FILTER(WHERE ids.gameId IS NOT NULL) games_with_pbp FROM nba_games g LEFT JOIN ids USING(gameId) WHERE season_start>=1996 GROUP BY 1,2 ORDER BY 1,2")
    check('pbp_date_fields', "SELECT min(coalesce(gameDateTimeEst,gameDateTimeEst_g)) first_combined_date,max(coalesce(gameDateTimeEst,gameDateTimeEst_g)) last_combined_date,count(*) FILTER(WHERE gameDateTimeEst IS NULL) missing_primary_date,count(*) FILTER(WHERE coalesce(gameDateTimeEst,gameDateTimeEst_g) IS NULL) missing_both_dates FROM pbp")
    check('player_orphans_by_phase', "SELECT left(lpad(p.gameId,10,'0'),3) prefix,count(*) row_count FROM player_stats p LEFT JOIN games g USING(gameId) WHERE g.gameId IS NULL GROUP BY 1 ORDER BY 1")
    check('nba_player_bio_orphans', "SELECT count(*) row_count,count(*) FILTER(WHERE np.personId IS NULL) missing_id FROM np LEFT JOIN players pl USING(personId) WHERE pl.personId IS NULL")
    check('bad_game_scores', "SELECT gameId,season_start,gameDateTimeEst,hometeamName,awayteamName,homeScore,awayScore,winner FROM nba_games WHERE hs<=0 OR aws<=0 OR hs=aws ORDER BY season_start")
    check('bad_team_rows', "SELECT t.gameId,t.teamId,t.teamScore,g.hometeamId,g.awayteamId,g.hs,g.aws FROM team_stats t JOIN nba_games g USING(gameId) WHERE t.teamId NOT IN(g.hometeamId,g.awayteamId) OR try_cast(t.teamScore AS INT) IS DISTINCT FROM CASE WHEN t.teamId=g.hometeamId THEN g.hs ELSE g.aws END")
    check('player_metadata_conflict_examples', "SELECT gameId,personId,gameDateTimeEst,played_at,playerteamId,inferred_team_id,home,playerteamName,opponentteamName FROM np WHERE (playerteamId IS NOT NULL AND playerteamId IS DISTINCT FROM inferred_team_id) OR try_cast(gameDateTimeEst AS TIMESTAMP) IS DISTINCT FROM played_at ORDER BY gameId,personId")
    check('early_season_teams', "WITH sides AS (SELECT season_start,hometeamId team_id,hometeamName team_name FROM nba_games WHERE prefix='002' UNION ALL SELECT season_start,awayteamId,awayteamName FROM nba_games WHERE prefix='002') SELECT season_start,count(DISTINCT team_id) teams,string_agg(DISTINCT team_name,', ' ORDER BY team_name) team_names FROM sides WHERE season_start<1955 GROUP BY 1 ORDER BY 1")
    check('metric_issues_by_era', "SELECT (season_start//10)*10 AS decade_start,count(*) row_count,count(*) FILTER(WHERE n_numMinutes=0 AND n_points>0) zero_minutes_with_points,count(*) FILTER(WHERE n_numMinutes>0 AND n_numMinutes<>floor(n_numMinutes)) fractional_minutes,count(*) FILTER(WHERE n_fieldGoalsMade>n_fieldGoalsAttempted OR n_freeThrowsMade>n_freeThrowsAttempted OR n_threePointersMade>n_threePointersAttempted) makes_exceed_attempts,count(*) FILTER(WHERE n_reboundsTotal<>n_reboundsOffensive+n_reboundsDefensive) rebound_sum_conflicts FROM np GROUP BY 1 ORDER BY 1")
    check('minutes_precision_by_season', "SELECT season_start,count(*) FILTER(WHERE n_numMinutes>0) positive_minutes,count(*) FILTER(WHERE n_numMinutes>0 AND n_numMinutes<>floor(n_numMinutes)) fractional_minutes FROM np GROUP BY 1 ORDER BY 1")
    check('schedule_regular_2024', "SELECT count(*) regular_games,count(*) FILTER(WHERE g.gameId IS NULL) missing_from_games FROM schedule_2024 s LEFT JOIN games g USING(gameId) WHERE left(lpad(s.gameId,10,'0'),3)='002'")
    check('schedule_missing_2024', "SELECT s.* FROM schedule_2024 s LEFT JOIN games g USING(gameId) WHERE left(lpad(s.gameId,10,'0'),3)='002' AND g.gameId IS NULL ORDER BY s.gameId")
    check('same_day_matchup_duplicates', "SELECT cast(played_at AS DATE) played_date,hometeamId,awayteamId,count(*) games FROM nba_games GROUP BY 1,2,3 HAVING count(*)>1")
    failures = [k for k,v in checks.items() if 'error' in v]
    print(f'Finished {len(checks)} checks; errors={failures}', flush=True)
    con.close()
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
