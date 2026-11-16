
#!/usr/bin/env python3
"""Archive weekly ESPN fantasy statistics for every available regular-season week."""
import datetime as dt
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LEAGUE_ID = os.environ.get("ESPN_LEAGUE_ID", "924923029")
SEASON = os.environ.get("ESPN_SEASON", "2026")
BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/"
    f"games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)
OUTPUT = Path("data") / SEASON
OUTPUT.mkdir(parents=True, exist_ok=True)
DESTINATION = OUTPUT / "player_stats.json"
FILTER = {"players": {"limit": 5000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}


def fetch(week=None):
    query = [("view", "kona_player_info")]
    if week is not None:
        query.append(("scoringPeriodId", str(week)))
    req = Request(
        BASE + "?" + urlencode(query),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "X-Fantasy-Filter": json.dumps(FILTER),
        },
    )
    with urlopen(req, timeout=90) as response:
        payload = json.load(response)
    return payload


def weekly_stats(payload, week):
    records = {}
    for entry in payload.get("players", []):
        player = entry.get("player", {})
        player_id = player.get("id")
        if player_id is None:
            continue
        stats = [
            {
                "season_id": stat.get("seasonId"),
                "week": week,
                "stat_source_id": stat.get("statSourceId"),
                "stat_split_type_id": stat.get("statSplitTypeId"),
                "fantasy_points": stat.get("appliedTotal"),
                "stats": stat.get("stats", {}),
            }
            for stat in player.get("stats", [])
            if stat.get("statSourceId") == 0
            and stat.get("statSplitTypeId") == 1
            and stat.get("scoringPeriodId") == week
            and isinstance(stat.get("appliedTotal"), (int, float))
        ]
        if stats:
            records[str(player_id)] = {
                "player_id": player_id,
                "name": player.get("fullName"),
                "position_id": player.get("defaultPositionId"),
                "team_id": player.get("proTeamId"),
                "injury_status": player.get("injuryStatus"),
                "stats": stats,
            }
    return records


# Retain previously saved weeks if a later request fails or returns no scores.
players = {}
if DESTINATION.exists():
    try:
        old = json.loads(DESTINATION.read_text(encoding="utf-8"))
        if str(old.get("season")) == SEASON:
            players = {
                str(p["player_id"]): p
                for p in old.get("players", [])
                if p.get("player_id") is not None
            }
    except (ValueError, OSError, KeyError) as exc:
        raise RuntimeError(
            f"Cannot safely read existing archive: {exc}"
        ) from exc

current_week = 18

updated_weeks = []
errors = {}

for week in range(1, current_week + 1):
    try:
        records = weekly_stats(fetch(week), week)
        if not records:
            raise ValueError("No actual weekly scoring records returned")

        for key, record in records.items():
            existing = players.get(key)
            if existing is None:
                players[key] = record
            else:
                existing.update({
                    field: value
                    for field, value in record.items()
                    if field != "stats"
                })
                existing["stats"] = [
                    s for s in existing.get("stats", [])
                    if s.get("week") != week
                ] + record["stats"]

        updated_weeks.append(week)
        print(f"Week {week}: {len(records)} players with actual points")

    except Exception as exc:
        errors[str(week)] = str(exc)
        print(f"Week {week}: ERROR: {exc}")

    time.sleep(0.2)

if not updated_weeks:
    raise RuntimeError(
        "No weeks refreshed; existing archive left unchanged"
    )

now = dt.datetime.now(dt.timezone.utc).isoformat()

for player in players.values():
    player["stats"] = sorted(
        player.get("stats", []),
        key=lambda s: (
            s.get("week") or 0,
            s.get("stat_source_id") or 0
        )
    )

result = {
    "season": SEASON,
    "updated_at_utc": now,
    "player_count": len(players),
    "players": sorted(
        players.values(),
        key=lambda p: p["player_id"]
    ),
}

# Atomic write: don't leave a truncated archive if interrupted.
temporary = DESTINATION.with_suffix(".json.tmp")
temporary.write_text(
    json.dumps(result, indent=2),
    encoding="utf-8"
)
temporary.replace(DESTINATION)

actual = [
    s
    for p in players.values()
    for s in p.get("stats", [])
    if s.get("stat_source_id") == 0
    and s.get("stat_split_type_id") == 1
]

weeks_found = sorted({
    s["week"]
    for s in actual
    if isinstance(s.get("week"), int)
})

report = {
    "season": SEASON,
    "updated_at_utc": now,
    "current_scoring_week": current_week,
    "total_players": len(players),
    "players_with_weekly_stats": sum(
        bool(p.get("stats")) for p in players.values()
    ),
    "weekly_stat_records": len(actual),
    "weeks_found": weeks_found,
    "weeks_refreshed_this_run": updated_weeks,
    "weekly_records_with_points": sum(
        isinstance(s.get("fantasy_points"), (int, float))
        for s in actual
    ),
    "week_errors": errors,
    "note": (
        "Weeks are scoring periods; a scoring record does "
        "not prove a player appeared in an NFL game."
    ),
}

(OUTPUT / "stats_verification.json").write_text(
    json.dumps(report, indent=2),
    encoding="utf-8"
)

print(json.dumps(report, indent=2))
