"""Build a self-contained HTML report from the completed audit evidence."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'audit/reports'


def load(name):
    return json.loads((OUT / name).read_text())


def table(records, columns=None):
    if not records:
        return '<p>No rows found.</p>'
    columns = columns or list(records[0])
    def display(value):
        if value is None:
            return '—'
        if isinstance(value, float):
            return f'{value:,.3f}'.rstrip('0').rstrip('.')
        if isinstance(value, int):
            return f'{value:,}'
        return str(value)
    head = ''.join(f'<th>{html.escape(c)}</th>' for c in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{html.escape(display(r.get(c)))}</td>' for c in columns) + '</tr>' for r in records)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def main():
    manifest = load('snapshot_manifest.json')
    profile = load('profile.json')
    checks = load('quality_checks.json')
    verification = load('verification.json')
    if any('error' in result for result in checks.values()):
        raise ValueError('Resolve failed audit queries before publishing the report')
    q = lambda name: checks[name]['rows']
    inventory = [{'File': value.get('file', 'PlayByPlay.parquet'), 'Rows': value['rows'],
                  'Columns': len(value.get('columns', value.get('schema', [])))}
                 for value in profile['tables'].values()]
    recent = [r for r in q('season_coverage') if r['season_start'] >= 2020]
    early = [r for r in q('season_coverage') if r['season_start'] <= 1954]
    comparisons = [{'Player': r['firstName']+' '+r['lastName'], 'Games': r['games'],
                    'PPG': r['ppg'], 'RPG': r['rpg'], 'APG': r['apg']}
                   for r in q('jokic_embiid_playoffs_pooled')]
    shared = [{'Competition': 'Regular season' if r['prefix']=='002' else 'Playoffs',
               'Games': r['games'], 'Kawhi PPG': r['ppg'], 'RPG': r['rpg'], 'APG': r['apg']}
              for r in q('kawhi_lebron_totals')]
    refs = '''<ul>
      <li><a href="https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores">Publisher dataset</a>; pinned version 515 and original metadata preserved locally.</li>
      <li><a href="https://www.statmuse.com/nba/ask/steph-curry-stats-2016-western-conference-finals-stats">StatMuse: Curry, 2016 Western Conference Finals</a> — external check of seven games, points/rebounds/assists, shooting totals and the minutes discrepancy.</li>
      <li><a href="https://www.nba.com/lakers/history/season-capsule">NBA/Lakers season history</a> — the 1949–50 NBA had 17 teams.</li>
      <li><a href="https://www.nba.com/news/power-rankings-2024-25-week-13">NBA: 2024–25 Week 13</a> — confirms a 1,230-game regular-season schedule.</li>
    </ul>'''
    parts = ['''<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>NBA data audit · Kaggle v515</title>
    <style>
    :root{color-scheme:light}body{font:16px/1.65 system-ui,sans-serif;color:#172b3a;background:#f3f6f8;margin:0}
    main{max-width:1080px;margin:auto;padding:42px 26px 80px}h1{font-size:38px;line-height:1.15;letter-spacing:-1px}
    h2{margin-top:38px;font-size:24px}h3{font-size:19px}a{color:#086888}p,li{max-width:95ch}
    .eyebrow{font-size:13px;letter-spacing:2px;text-transform:uppercase;color:#456574}.lede{font-size:19px}
    .verdict{background:#e5f1ec;border-left:5px solid #278167;padding:18px 24px;border-radius:5px}
    .warning{background:#fff0da;border-left:5px solid #b2700b;padding:16px 24px;border-radius:5px}
    .table-wrap{overflow:auto;margin:16px 0;background:white;border:1px solid #d9e2e8;border-radius:6px}
    table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e6ebef;vertical-align:top}
    th{background:#eaf0f4;white-space:nowrap}td{font-variant-numeric:tabular-nums}tr:last-child td{border-bottom:0}
    code,pre{font-family:ui-monospace,monospace;font-size:13px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eaf0f4;padding:14px;border-radius:5px}
    details{background:white;border:1px solid #d9e2e8;padding:12px 16px;margin:10px 0;border-radius:6px}summary{cursor:pointer;font-weight:600}
    .muted{color:#536976;font-size:14px}@media print{body{background:white}main{padding:0}details{break-inside:avoid}a{color:inherit}}
    </style></head><body><main><div class="eyebrow">Data audit · 10 October 2026 · No application implementation</div>
    <h1>A useful foundation, with material coverage limits</h1>
    <p class="lede">Kaggle v515 can support a focused NBA statistics prototype. It cannot, by itself, support a reliable promise of complete NBA history or unrestricted statistical answers.</p>
    <div class="verdict"><strong>Recommendation: proceed with a curated subset.</strong> Keep the original snapshot, normalize structured records into PostgreSQL later, and expose only metrics and scopes that pass coverage checks. Do not embed box scores or use vector search for statistical calculations.</div>
    <p>Your stated tolerance is small gaps, but not large ones. The known 2024–25 game-level issues affect 7 of 1,230 scheduled regular-season games (about 0.57%); this is compatible with a prototype that discloses incomplete scopes. It is not a bound on every possible player/metric defect. The early-history omissions are substantial and do not meet that tolerance.</p>''']
    parts.append(f'''<h2>Snapshot and scope</h2><p>The download contains <strong>{sum(f['bytes'] for f in manifest['files']):,} bytes</strong> of extracted data across ten files. Version <strong>515</strong> reports an update on <strong>15 June 2026</strong>; the latest game found is <strong>13 June 2026</strong>. “Today” in the dataset title is not a freshness guarantee.</p>
    <p>For this project, NBA scope begins with <strong>1949–50</strong>, as requested. The audit includes regular-season and playoff games and excludes pre-1949 records, preseason, All-Star, play-in and Cup championship games. Cup games that count toward the regular season remain included. Competition is derived from the observed NBA game-ID convention, checked against labels, dates and the NBA team catalog; label text alone is inconsistent.</p>''')
    parts.append(table(q('nba_scope')))
    parts.append('<p>IDs arrive without leading zeros. Preserve canonical IDs as text and normalize deliberately. Season 2015 means 2015–16; playoffs in calendar 2021 belong to season 2020.</p><h2>File inventory</h2>')
    parts.append(table(inventory))
    parts.append('''<p>The extended player file overlaps the basic file: all 838,803 extended rows match base rows on player/game, with identical points and minutes. Join extensions onto the base table; never append them as additional appearances. Advanced player data begins in November 1996.</p>
    <h2>What the priority questions prove</h2>
    <h3>Curry’s 2016 Western Conference Finals</h3>
    <p>Seven games produce <strong>195 points, 44 rebounds and 41 assists</strong>: <strong>27.9 PPG, 6.3 RPG and 5.9 APG</strong>. Shooting totals are 62/140 FG, 32/77 3PT and 39/44 FT. These match the external StatMuse reference. Percentages must be computed from summed makes and attempts, not by averaging game percentages.</p>
    <div class="warning"><strong>Minutes do not pass the same check.</strong> The source totals 254 minutes, implying 36.3 MPG; the reference displays 36.7 MPG. Pre-2024–25 minutes are overwhelmingly integer values. The audit establishes lost precision, not the exact upstream rounding algorithm. Suppress or repair MPG and per-minute metrics before presenting them as accurate.</div>
    <h3>Jokic vs Embiid in the playoffs since 2021</h3>
    <p>The query is straightforward: resolve player IDs, select playoff appearances from the 2020–21 season onward, then aggregate. Below are <strong>snapshot-derived results through 13 June 2026</strong>. They have been independently recomputed from the CSV; complete external career validation has not been performed. A comparison must show unequal sample sizes and the snapshot cutoff.</p>''')
    parts.append(table(comparisons))
    parts.append('''<h3>Kawhi in opposing-team games where LeBron also played</h3>
    <p>This requires a self-join on game ID, two confirmed appearances and opposing team assignments. It does not require play-by-play. These are game-level comparisons; they do not establish who guarded whom or shared court time. The following results are internally verified, not externally certified career totals.</p>''')
    parts.append(table(shared))
    parts.append('''<p>The priority examples use positive recorded minutes to identify appearances. That rule cannot become the universal participation rule: genuine appearances sometimes have zero recorded minutes. The Jokic/Embiid playoff selection has no excluded zero/missing-minute rows; the target Curry series and Kawhi/LeBron shared sample were checked separately.</p>
    <h2>Material defects and their implications</h2>
    <ol>
    <li><strong>Early NBA coverage is substantially incomplete.</strong> The 1949–50 regular-season table contains 122 games involving eight teams, while the NBA historical source identifies 17 teams. A date range reaching 1949 does not mean complete coverage. Do not advertise complete all-time totals or leaderboards.</li>
    <li><strong>Recent seasons also need reconciliation.</strong> The 2024–25 schedule has 1,230 regular games; the game table contains 1,225. Five schedule IDs are absent, and two additional stored games have 0–0 scores and no player rows. Schedule timestamps may be stale after rescheduling, so use them to identify gaps, not as authoritative played dates.</li>
    <li><strong>Missing information is often encoded as zero.</strong> There are 85,156 NBA player rows with zero minutes and positive points. Many historical attempts and rebound splits are also inconsistent with recorded makes or totals. Never globally replace zero with NULL or treat every zero as measured data; define availability by metric, era and record evidence.</li>
    <li><strong>Game and team errors need quarantine.</strong> Four stored games end in tied scores, including the two 0–0 records. Two 2025–26 games have an extra team-stat row with team ID 0. There are 739 team/game point-total mismatches despite non-null player points; missing players and source errors remain possible.</li>
    <li><strong>Identity and date joins need explicit rules.</strong> 48,430 in-scope player rows lack source team IDs but can be mapped through game ID plus home/away. Nine non-null team assignments conflict with that mapping, and eleven player timestamps disagree with game timestamps. Retain provenance and flag conflicts rather than silently trusting either side.</li>
    <li><strong>DNP comments are not a complete availability history.</strong> Five rows have positive minutes alongside DNP/inactive-like comments. Absence of a player row does not establish injury, team membership or unavailability. Historical rosters/transactions and a participation policy are prerequisites for robust “team without player” answers.</li>
    </ol>
    <p>There are no duplicate observed game keys or player/game keys. Every NBA-scope player row joins to a player biography. The 4,906 source player rows without a game occur outside the selected regular/playoff scope, in preseason or All-Star data. These reassuring checks do not prove game or player completeness.</p>
    <details><summary>Problem game records</summary>''')
    parts.append(table(q('bad_game_scores')) + table(q('bad_team_rows')) + '</details>')
    parts.append('<details><summary>Five missing 2024–25 schedule IDs</summary>' + table(q('schedule_missing_2024'), ['gameId','gameDateTimeEst','hometeamId','awayteamId']) + '</details>')
    parts.append('<details><summary>Early-season coverage</summary>' + table(early) + table(q('early_season_teams')) + '</details>')
    parts.append('<details><summary>Recent-season coverage</summary>' + table(recent) + '</details>')
    parts.append('''<h2>Play-by-play and advanced data</h2>
    <p>Play-by-play contains 18,727,295 events across 39,164 games, starting in 1996. The observed (game ID, action number) key has no duplicates. Its 89-column schema combines multiple generations of data. Looking only at the primary date column would incorrectly suggest an April 2026 cutoff: an alternate date field extends through June 2026, and 56,192 events lack both dates. Join canonical games and validate events before use.</p>
    <p>Keep play-by-play as a raw Parquet asset initially. Box scores suffice for the current examples. Possessions, lineup stints, defensive matchups and on/off statistics need separate reconstruction and validation. The presence of an advanced-rating column does not validate its definition or justify averaging ratings across games.</p>
    <h2>Data capabilities and boundaries</h2>''')
    parts.append(table([
        {'Question':'Player or playoff-series counting stats','Assessment':'Feasible for audited scopes; check game and metric coverage before answering.'},
        {'Question':'Player comparisons since a season/date','Assessment':'Feasible; specify season semantics, phase, cutoff and unequal sample sizes.'},
        {'Question':'Both players appeared on opposing teams','Assessment':'Feasible with a player/game self-join and reliable participation.'},
        {'Question':'Last N games','Assessment':'Feasible after canonical date and participation validation; missing games can change the selection.'},
        {'Question':'Game 7 / elimination games','Assessment':'Needs normalized series metadata, historical series formats and complete prior series results.'},
        {'Question':'With/without a teammate or injured player','Assessment':'Needs roster tenure and availability evidence; not justified by missing box-score rows alone.'},
        {'Question':'All-time rankings and complete early NBA totals','Assessment':'Unsupported as a complete claim until historical gaps are backfilled.'},
        {'Question':'NBA rules, history and terminology','Assessment':'Not supplied by these statistical files. Requires separately sourced documents.'},
        {'Question':'Live results / current schedule','Assessment':'Outside current scope; this fixed snapshot cannot provide live information.'},
    ]))
    parts.append('''<h2>Storage and agent implications</h2>
    <p>Use three layers: unchanged raw files with checksums; normalized relational data; and a coverage/quality record attached to every answerable scope. Statistical answers should be computed through typed Python tools over relational data. Vector embeddings add no value for summing box scores or identifying shared games.</p>
    <p>The Analyst’s knowledge tool should return data plus player/game IDs, filters, denominator, metric definitions, source version, cutoff, coverage status and exclusions. The LLM can write the explanation, but cannot upgrade incomplete evidence into a complete claim. Document retrieval can be added separately for rules/history; no vector database is necessary for this audit or first statistical milestone.</p>
    <p>Recommended ingestion behavior: retain source values; classify phases; normalize IDs, dates and series labels; derive missing team IDs only with recorded evidence; quarantine conflicting records; and expose coverage-aware query results. No corrections or PostgreSQL ingestion have been performed in this audit.</p>
    <h2>Licensing and local environment</h2>
    <p>Kaggle metadata labels the upload <strong>CC0: Public Domain</strong>; the publisher identifies NBA data as its upstream source. This records the uploader’s license declaration, not independent verification of upstream redistribution or commercial rights. Personal prototyping can proceed on the chosen source; commercialization requires a separate rights review or appropriately licensed provider.</p>
    <p>A project <code>.venv</code> has been created using Python 3.12.14, with <code>duckdb==1.5.6</code> pinned for audit work. DuckDB is a disposable local analysis engine here, not a change to the recommended PostgreSQL application store. Raw data, scratch database, virtual environment and secrets are excluded by <code>.gitignore</code>. The workspace was not a Git repository at inspection time.</p>
    <p>PostgreSQL 14.15 command-line tools and a Homebrew data directory are present; Postgres.app is also installed. Local connection checks and the Homebrew server-status check found no running server. No server was started and no PostgreSQL database was changed. No OpenAI or Kaggle API key was needed.</p>
    <h2>Audit method, reproducibility and limits</h2>''')
    parts.append(f'''<p>All ten raw files were downloaded from pinned version 515, extracted with ZIP CRC validation and SHA-256 hashing, and inspected in full for row counts and relevant keys. The audit ran <strong>{len(checks)} SQL checks without execution errors</strong>. Source defects above are findings, not passing data-quality tests. A separate standard-library CSV pass reproduced the priority aggregates, and all raw-file hashes still match.</p>
    <p>External verification is a targeted spot-check, not certification of every season, game, metric or play-by-play event. The snapshot’s own schedule is useful internal evidence; historical gap estimates require further independent schedules. The report does not validate the full meaning of all 110 advanced player columns or establish commercial rights.</p>
    <p><strong>Archive SHA-256:</strong></p><pre>{manifest['archive_sha256']}</pre>
    <p>Evidence: <a href="snapshot_manifest.json">snapshot manifest</a> · <a href="profile.json">full file profile</a> · <a href="quality_checks.json">SQL and results</a> · <a href="verification.json">independent verification</a> · <a href="../RUNBOOK.txt">rerun instructions</a>.</p>
    <h2>Recommended next boundary</h2>
    <p>Use Curry’s seven-game series as the first verified counting-stat fixture, then expand to the audited comparison and shared-game queries. Keep minutes-derived metrics disabled until repaired and require explicit coverage status everywhere. Preserve the long-term goal of 1949 onward, while refusing complete-history claims for scopes this snapshot cannot support. Backend implementation remains a separate next step.</p>
    <h2>External references</h2><p class="muted">Checked 10 October 2026. Original data and calculations are preserved in the local evidence files.</p>{refs}
    <details><summary>All {len(checks)} checks, with exact SQL and results</summary>''')
    for name, result in checks.items():
        parts.append(f'<details><summary>{html.escape(name)}</summary><pre>{html.escape(result["sql"])}</pre>{table(result["rows"])}</details>')
    parts.append('</details></main></body></html>')
    (OUT / 'data-audit.html').write_text('\n'.join(parts))
    print(OUT / 'data-audit.html')


if __name__ == '__main__':
    main()
