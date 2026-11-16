
#!/usr/bin/env python3
"""Collect ESPN fantasy player statistics for historical analysis."""

import datetime as dt
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

LEAGUE_ID = os.environ.get("ESPN_LEAGUE_ID", "924923029")
SEASON = os.environ.get("ESPN_SEASON", "2026")

URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/"
    f"games/ffl/seasons/{SEASON}/segments/0/"
    f"leagues/{LEAGUE_ID}?view=kona_player_info"
)

player_filter = {
    "players": {
        "limit": 5000,
        "sortPercOwned": {
            "sortPriority": 1,
            "sortAsc": False
        }
    }
}

request = Request(
    URL,
    headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "X-Fantasy-Filter": json.dumps(player_filter)
    }
)

with urlopen(request, timeout=90) as response:
    payload = json.load(response)

players = payload.get("players", [])

if not players:
    raise RuntimeError("ESPN returned no player data")

results = []

for entry in players:
    player = entry.get("player", {})
    stats = player.get("stats", [])

    results.append({
        "player_id": player.get("id"),
        "name": player.get("fullName"),
        "position_id": player.get("defaultPositionId"),
        "team_id": player.get("proTeamId"),
        "injury_status": player.get("injuryStatus"),
        "stats": [
            {
                "season_id": stat.get("seasonId"),
                "week": stat.get("scoringPeriodId"),
                "stat_source_id": stat.get("statSourceId"),
                "stat_split_type_id": stat.get("statSplitTypeId"),
                "fantasy_points": stat.get("appliedTotal"),
                "stats": stat.get("stats", {})
            }
            for stat in stats
        ]
    })

output = Path("data") / SEASON
output.mkdir(parents=True, exist_ok=True)

destination = output / "player_stats.json"

destination.write_text(
    json.dumps({
        "season": SEASON,
        "updated_at_utc": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(),
        "player_count": len(results),
        "players": results
    }, indent=2),
    encoding="utf-8"
)

print(f"Saved statistics for {len(results)} players")
