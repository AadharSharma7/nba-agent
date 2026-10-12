"""Independent CSV checks of priority queries and preservation of raw files.

Uses Python's standard library, not DuckDB. A successful run verifies audit
reproducibility; it does not certify the source dataset as error-free.
"""
import csv
import hashlib
import json
import math
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / 'audit/reports'
RAW = ROOT / 'data/raw/kaggle/v515'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    quality = json.loads((REPORTS / 'quality_checks.json').read_text())
    manifest = json.loads((REPORTS / 'snapshot_manifest.json').read_text())
    require(all('error' not in item for item in quality.values()), 'Audit SQL error')
    hashes = {}
    for item in manifest['files']:
        with (RAW / item['name']).open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        require(digest == item['sha256'], f"Raw file changed: {item['name']}")
        hashes[item['name']] = True
    with (RAW / 'Games.csv').open(newline='') as source:
        games = {r['gameId']: r for r in csv.DictReader(source)}
    people = defaultdict(dict)
    targets = {'201939', '203999', '203954', '202695', '2544'}
    with (RAW / 'PlayerStatistics.csv').open(newline='') as source:
        for row in csv.DictReader(source):
            if row['personId'] in targets:
                people[row['personId']][row['gameId']] = row

    def phase(gid):
        return gid.zfill(10)[:3]

    def season(gid):
        year = int(gid.zfill(10)[3:5])
        return year + (1900 if year >= 46 else 2000)

    def played(row):
        return float(row['numMinutes'] or 0) > 0

    def aggregate(records):
        return {'games': len(records), **{
            out: sum(float(r[field] or 0) for r in records)
            for out, field in [('points', 'points'), ('rebounds', 'reboundsTotal'),
                               ('assists', 'assists'), ('source_minutes', 'numMinutes')]
        }}

    def compare(actual, expected):
        for key, value in actual.items():
            require(math.isclose(value, expected[key], abs_tol=1e-8), f'{key}: {value} != {expected[key]}')

    curry_rows = [r for gid, r in people['201939'].items()
                  if phase(gid) == '004' and season(gid) == 2015 and played(r)
                  and games[gid]['awayteamId'] in {'1610612744', '1610612760'}
                  and games[gid]['hometeamId'] in {'1610612744', '1610612760'}]
    curry = aggregate(curry_rows)
    compare(curry, quality['curry_wcf_totals']['rows'][0])
    external_core = {'games': 7, 'points': 195, 'rebounds': 44, 'assists': 41}
    compare(external_core, curry)
    require(round(curry['source_minutes'] / curry['games'], 1) != 36.7,
            'Known minutes discrepancy changed; investigate before updating fixture')
    comparisons = {}
    for expected in quality['jokic_embiid_playoffs_pooled']['rows']:
        person = expected['personId']
        subset = [r for gid, r in people[person].items()
                  if gid in games and phase(gid) == '004' and season(gid) >= 2020 and played(r)]
        actual = aggregate(subset)
        compare(actual, expected)
        comparisons[person] = actual
    kawhi = defaultdict(list)
    for gid, row in people['202695'].items():
        other = people['2544'].get(gid)
        if (gid in games and phase(gid) in {'002', '004'} and other
                and played(row) and played(other) and row['home'] != other['home']):
            kawhi[phase(gid)].append(row)
    for expected in quality['kawhi_lebron_totals']['rows']:
        compare(aggregate(kawhi[expected['prefix']]), expected)
    report = {
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'python_executable': sys.executable,
        'inside_venv': sys.prefix != sys.base_prefix,
        'raw_file_hashes_match': hashes,
        'sql_checks_executed_without_errors': len(quality),
        'independent_csv_priority_aggregates_match': True,
        'curry_external_core_matches': True,
        'curry_external_minutes_matches': False,
        'curry_source': curry,
        'curry_reference': {
            **external_core, 'displayed_mpg': 36.7,
            'url': 'https://www.statmuse.com/nba/ask/steph-curry-stats-2016-western-conference-finals-stats',
            'checked_on': '2026-10-10',
            'scope': 'Core totals externally checked; minutes discrepancy retained, not repaired.'},
        'jokic_embiid_source_totals': comparisons,
        'external_validation_limits': 'No exhaustive external certification of all games, career totals, advanced metrics or play-by-play.'
    }
    (REPORTS / 'verification.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
