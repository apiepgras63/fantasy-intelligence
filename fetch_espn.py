#!/usr/bin/env python3
"""Download ESPN public fantasy league snapshots using the read-only API."""
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LEAGUE_ID = '924923029'
SEASON = '2026'
BASE = f'https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}'
VIEWS = {'teams': ['mTeam'], 'rosters': ['mRoster', 'mTeam'], 'settings': ['mSettings'], 'matchups': ['mMatchupScore'], 'transactions': ['mTransactions2']}
DATA = Path('data')
DATA.mkdir(exist_ok=True)
errors = {}
for label, views in VIEWS.items():
    url = BASE + '?' + urlencode([('view', v) for v in views])
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
        with urlopen(req, timeout=35) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or str(payload.get('id', LEAGUE_ID)) != LEAGUE_ID:
            raise ValueError('Unexpected ESPN response')
        (DATA / f'{label}.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print(f'{label}: OK')
    except Exception as exc:
        errors[label] = str(exc)
        print(f'{label}: ERROR: {exc}')
(DATA / 'status.json').write_text(json.dumps({'updated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'league_id': LEAGUE_ID, 'errors': errors}, indent=2), encoding='utf-8')
if 'teams' in errors or 'rosters' in errors:
    raise SystemExit('Critical ESPN data failed to refresh')
