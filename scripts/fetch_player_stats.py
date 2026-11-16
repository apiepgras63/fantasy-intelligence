
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

# Create an easy-to-read weekly fantasy scoring summary.
player_summaries = []

for player in result["players"]:
    weekly_points = {}

    for stat in player.get("stats", []):
        week = stat.get("week")
        points = stat.get("fantasy_points")

        if (
            isinstance(week, int)
            and 1 <= week <= 18
            and isinstance(points, (int, float))
            and stat.get("stat_source_id") == 0
            and stat.get("stat_split_type_id") == 1
        ):
            weekly_points[str(week)] = round(points, 2)

    if not weekly_points:
        continue

    scores = list(weekly_points.values())

    player_summaries.append({
        "player_id": player["player_id"],
        "name": player["name"],
        "position_id": player["position_id"],
        "team_id": player["team_id"],
        "weeks": dict(sorted(
            weekly_points.items(),
            key=lambda item: int(item[0])
        )),
        "weeks_with_records": len(scores),
        "total_fantasy_points": round(sum(scores), 2),
        "average_per_record": round(
            sum(scores) / len(scores), 2
        ),
    })

player_summaries.sort(
    key=lambda player: player["total_fantasy_points"],
    reverse=True
)

summary_path = OUTPUT / "player_weekly_summary.json"

summary_path.write_text(
    json.dumps({
        "season": SEASON,
        "updated_at_utc": now,
        "player_count": len(player_summaries),
        "players": player_summaries,
    }, indent=2),
    encoding="utf-8"
)

print(
    f"Saved weekly summaries for "
    f"{len(player_summaries)} players"
)

# Build player consistency statistics for future draft analysis.
import statistics

POSITION_NAMES = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

POSITION_THRESHOLDS = {
    "QB": 20,
    "RB": 15,
    "WR": 15,
    "TE": 10,
    "K": 10,
    "D/ST": 10,
}

consistency_players = []

for player in player_summaries:
    weeks = player["weeks"]
    scores = [float(value) for value in weeks.values()]

    if not scores:
        continue

    position = POSITION_NAMES.get(
        player["position_id"], "OTHER"
    )
    threshold = POSITION_THRESHOLDS.get(position)

    average = statistics.mean(scores)
    median = statistics.median(scores)
    deviation = (
        statistics.pstdev(scores)
        if len(scores) > 1 else 0.0
    )

    sorted_scores = sorted(scores)
    count = len(scores)

    # Lower-quartile observed performance.
    floor_index = max(0, int((count - 1) * 0.25))
    ceiling_index = min(
        count - 1, int((count - 1) * 0.75)
    )

    consistency_players.append({
        "player_id": player["player_id"],
        "name": player["name"],
        "position": position,
        "position_id": player["position_id"],
        "team_id": player["team_id"],
        "weeks_with_records": count,
        "total_points": round(sum(scores), 2),
        "average": round(average, 2),
        "median": round(median, 2),
        "standard_deviation": round(deviation, 2),
        "lowest_recorded_week": round(min(scores), 2),
        "highest_recorded_week": round(max(scores), 2),
        "lower_quartile_score": round(
            sorted_scores[floor_index], 2
        ),
        "upper_quartile_score": round(
            sorted_scores[ceiling_index], 2
        ),
        "weeks_at_or_above_average": sum(
            score >= average for score in scores
        ),
        "weeks_below_10_points": sum(
            score < 10 for score in scores
        ),
        "position_threshold": threshold,
        "weeks_meeting_threshold": (
            sum(score >= threshold for score in scores)
            if threshold is not None else None
        ),
        "threshold_hit_rate_pct": (
            round(
                100 * sum(
                    score >= threshold for score in scores
                ) / count,
                1
            )
            if threshold is not None else None
        ),
        "weekly_points": weeks,
    })

consistency_players.sort(
    key=lambda p: (
        p["median"],
        p["average"],
        p["total_points"]
    ),
    reverse=True
)

consistency_report = {
    "season": SEASON,
    "updated_at_utc": now,
    "scoring_format": "ESPN league scoring",
    "player_count": len(consistency_players),
    "methodology": {
        "sample": "Weeks with recorded ESPN fantasy points",
        "missing_weeks": (
            "Excluded, not automatically scored as zero"
        ),
        "standard_deviation": "Population standard deviation",
        "quartiles": "Observed scores at 25% and 75% indices",
        "thresholds": POSITION_THRESHOLDS,
        "limitations": (
            "Records do not yet distinguish games played, "
            "bye weeks, injuries, or partial live games. "
            "These are descriptive metrics, not final rankings."
        ),
    },
    "players": consistency_players,
}

consistency_path = OUTPUT / "player_consistency.json"

consistency_path.write_text(
    json.dumps(consistency_report, indent=2),
    encoding="utf-8"
)

print(
    f"Saved consistency metrics for "
    f"{len(consistency_players)} players"
)

# Track weekly scoring availability without guessing injury status.
availability_players = []

completed_weeks = sorted(
    week for week in weeks_found
    if isinstance(week, int) and 1 <= week <= 18
)

for player in result["players"]:
    player_weeks = {
        stat.get("week")
        for stat in player.get("stats", [])
        if stat.get("stat_source_id") == 0
        and stat.get("stat_split_type_id") == 1
        and isinstance(stat.get("fantasy_points"), (int, float))
    }

    recorded_weeks = sorted(
        week for week in completed_weeks
        if week in player_weeks
    )
    missing_weeks = sorted(
        week for week in completed_weeks
        if week not in player_weeks
    )

    availability_players.append({
        "player_id": player["player_id"],
        "name": player["name"],
        "position_id": player["position_id"],
        "team_id": player["team_id"],
        "latest_injury_status": player.get("injury_status"),
        "weeks_with_scoring_records": recorded_weeks,
        "weeks_without_scoring_records": missing_weeks,
        "recorded_week_count": len(recorded_weeks),
        "missing_week_count": len(missing_weeks),
        "confirmed_games_played": None,
        "confirmed_injury_absences": None,
        "confirmed_bye_weeks": None,
    })

availability_path = OUTPUT / "player_availability.json"

availability_path.write_text(
    json.dumps({
        "season": SEASON,
        "updated_at_utc": now,
        "weeks_examined": completed_weeks,
        "player_count": len(availability_players),
        "methodology": (
            "Missing scoring records are not automatically "
            "classified as injuries, bye weeks, or missed games. "
            "Game participation and historical injury data "
            "require additional verification."
        ),
        "players": availability_players,
    }, indent=2),
    encoding="utf-8"
)

print(
    f"Saved availability records for "
    f"{len(availability_players)} players"
)
