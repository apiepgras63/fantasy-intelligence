
#!/usr/bin/env python3
"""Collect ESPN fantasy scores, NFL schedules, and player analysis."""

import datetime as dt
import json
import os
import statistics
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SEASON = str(os.getenv("ESPN_SEASON", "2026"))
LEAGUE_ID = str(os.getenv("ESPN_LEAGUE_ID", "924923029"))
OUTPUT = Path("data") / SEASON
OUTPUT.mkdir(parents=True, exist_ok=True)

BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/"
    f"games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)
SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/"
    "sports/football/nfl/scoreboard"
)
FILTER = {
    "players": {
        "limit": 5000,
        "sortPercOwned": {
            "sortPriority": 1,
            "sortAsc": False
        }
    }
}
POSITIONS = {
    1: "QB", 2: "RB", 3: "WR",
    4: "TE", 5: "K", 16: "D/ST"
}
THRESHOLDS = {
    "QB": 20, "RB": 15, "WR": 15,
    "TE": 10, "K": 10, "D/ST": 10
}
NOW = dt.datetime.now(dt.timezone.utc).isoformat()


def request_json(url, headers=None):
    last_error = None
    for attempt in range(3):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    **(headers or {})
                }
            )
            with urlopen(req, timeout=90) as response:
                return json.load(response)
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last_error))


def save_json(filename, data):
    destination = OUTPUT / filename
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8"
    )
    temporary.replace(destination)


def fetch_fantasy(week):
    url = BASE + "?" + urlencode({
        "view": "kona_player_info",
        "scoringPeriodId": week
    })
    return request_json(
        url,
        {"X-Fantasy-Filter": json.dumps(FILTER)}
    )


def fetch_schedule(week):
    url = SCOREBOARD + "?" + urlencode({
        "dates": SEASON,
        "seasontype": 2,
        "week": week,
        "limit": 100
    })
    return request_json(url)


# Load and preserve existing player history.
archive_path = OUTPUT / "player_stats.json"
players = {}

if archive_path.exists():
    old = json.loads(
        archive_path.read_text(encoding="utf-8")
    )
    if str(old.get("season")) != SEASON:
        raise RuntimeError("Archive season mismatch")
    players = {
        str(p["player_id"]): p
        for p in old.get("players", [])
        if p.get("player_id") is not None
    }

# Preserve previously collected NFL schedules.
schedule_path = OUTPUT / "nfl_schedule.json"
schedule = {}

if schedule_path.exists():
    old_schedule = json.loads(
        schedule_path.read_text(encoding="utf-8")
    )
    if str(old_schedule.get("season")) == SEASON:
        schedule = old_schedule.get("weeks", {})

refreshed = []
scoring_errors = {}
schedule_errors = {}

# Collect every regular-season week automatically.
for week in range(1, 19):
    try:
        payload = fetch_fantasy(week)
        found = {}

        for entry in payload.get("players", []):
            player = entry.get("player") or {}
            player_id = player.get("id")
            if player_id is None:
                continue

            records = []
            for stat in player.get("stats", []):
                if (
                    stat.get("statSourceId") == 0
                    and stat.get("statSplitTypeId") == 1
                    and stat.get("scoringPeriodId") == week
                    and isinstance(
                        stat.get("appliedTotal"), (int, float)
                    )
                ):
                    records.append({
                        "season_id": stat.get("seasonId"),
                        "week": week,
                        "stat_source_id": 0,
                        "stat_split_type_id": 1,
                        "fantasy_points": stat["appliedTotal"],
                        "stats": stat.get("stats") or {}
                    })

            if records:
                found[str(player_id)] = {
                    "player_id": player_id,
                    "name": player.get("fullName"),
                    "position_id": player.get(
                        "defaultPositionId"
                    ),
                    "team_id": player.get("proTeamId"),
                    "injury_status": player.get("injuryStatus"),
                    "stats": records
                }

        if not found:
            raise ValueError("No weekly scores available")

        for key, fresh in found.items():
            if key not in players:
                players[key] = fresh
            else:
                existing = players[key]
                existing.update({
                    k: v for k, v in fresh.items()
                    if k != "stats"
                })
                existing["stats"] = [
                    s for s in existing.get("stats", [])
                    if s.get("week") != week
                ] + fresh["stats"]

        refreshed.append(week)
        print(f"Scoring Week {week}: {len(found)} players")

    except Exception as exc:
        scoring_errors[str(week)] = str(exc)
        print(f"Scoring Week {week}: {exc}")

    try:
        board = fetch_schedule(week)
        events = board.get("events", [])
        if not events:
            raise ValueError("No schedule events")

        games = []
        for event in events:
            competition = (
                event.get("competitions") or [{}]
            )[0]
            teams = []

            for competitor in competition.get(
                "competitors", []
            ):
                team = competitor.get("team") or {}
                teams.append({
                    "id": str(team.get("id")),
                    "name": team.get("displayName"),
                    "abbreviation": team.get("abbreviation"),
                    "home_away": competitor.get("homeAway")
                })

            status = (
                event.get("status") or {}
            ).get("type") or {}

            games.append({
                "event_id": event.get("id"),
                "date": event.get("date"),
                "teams": teams,
                "status": status.get("name"),
                "completed": status.get("completed") is True
            })

        schedule[str(week)] = {
            "games": games,
            "source": "ESPN NFL scoreboard",
            "retrieved_at_utc": NOW
        }
        print(f"NFL Week {week}: {len(games)} games")

    except Exception as exc:
        schedule_errors[str(week)] = str(exc)
        print(f"NFL Week {week}: {exc}")

    time.sleep(0.15)

if not players:
    raise RuntimeError("No player data collected")

# Save the full scoring archive.
for player in players.values():
    player["stats"] = sorted(
        player.get("stats", []),
        key=lambda s: s.get("week") or 0
    )

all_players = sorted(
    players.values(),
    key=lambda p: p["player_id"]
)

if refreshed:
    save_json("player_stats.json", {
        "season": SEASON,
        "updated_at_utc": NOW,
        "player_count": len(all_players),
        "players": all_players
    })

if schedule:
    save_json("nfl_schedule.json", {
        "season": SEASON,
        "updated_at_utc": NOW,
        "weeks": schedule
    })

# Build team schedule lookup.
team_games = {}
schedule_complete = {}

for week_key, week_data in schedule.items():
    games = week_data.get("games", [])
    teams_seen = set()

    for game in games:
        for team in game.get("teams", []):
            team_id = team.get("id")
            if team_id and team_id != "None":
                teams_seen.add(team_id)
                team_games[(team_id, week_key)] = game

    # Avoid declaring byes from a partial schedule.
    schedule_complete[week_key] = (
        len(games) >= 12 and len(teams_seen) >= 24
    )

weeks_found = sorted({
    stat["week"]
    for player in all_players
    for stat in player.get("stats", [])
    if stat.get("stat_source_id") == 0
    and stat.get("stat_split_type_id") == 1
    and isinstance(stat.get("week"), int)
    and isinstance(
        stat.get("fantasy_points"), (int, float)
    )
})

summaries = []
consistency = []
availability = []

for player in all_players:
    weekly = {}

    for stat in player.get("stats", []):
        week = stat.get("week")
        points = stat.get("fantasy_points")

        if (
            stat.get("stat_source_id") == 0
            and stat.get("stat_split_type_id") == 1
            and isinstance(week, int)
            and isinstance(points, (int, float))
        ):
            weekly[str(week)] = round(points, 2)

    weekly = dict(sorted(
        weekly.items(),
        key=lambda item: int(item[0])
    ))

    scores = list(weekly.values())
    position = POSITIONS.get(
        player.get("position_id"), "OTHER"
    )
    threshold = THRESHOLDS.get(position)

    if scores:
        average = statistics.mean(scores)
        ordered = sorted(scores)

        summaries.append({
            "player_id": player["player_id"],
            "name": player.get("name"),
            "position_id": player.get("position_id"),
            "team_id": player.get("team_id"),
            "weeks": weekly,
            "weeks_with_records": len(scores),
            "total_fantasy_points": round(sum(scores), 2),
            "average_per_record": round(average, 2)
        })

        consistency.append({
            "player_id": player["player_id"],
            "name": player.get("name"),
            "position": position,
            "position_id": player.get("position_id"),
            "team_id": player.get("team_id"),
            "weeks_with_records": len(scores),
            "total_points": round(sum(scores), 2),
            "average": round(average, 2),
            "median": round(
                statistics.median(scores), 2
            ),
            "standard_deviation": round(
                statistics.pstdev(scores), 2
            ) if len(scores) > 1 else 0,
            "lowest_recorded_week": min(scores),
            "highest_recorded_week": max(scores),
            "lower_quartile_score": ordered[
                int((len(ordered) - 1) * 0.25)
            ],
            "upper_quartile_score": ordered[
                int((len(ordered) - 1) * 0.75)
            ],
            "position_threshold": threshold,
            "weeks_meeting_threshold": (
                sum(s >= threshold for s in scores)
                if threshold is not None else None
            ),
            "threshold_hit_rate_pct": (
                round(
                    100 * sum(
                        s >= threshold for s in scores
                    ) / len(scores), 1
                )
                if threshold is not None else None
            ),
            "weekly_points": weekly
        })

    # Classify team schedules without guessing
    # individual participation or injury history.
    weekly_status = {}
    team_id = str(player.get("team_id"))

    for week in weeks_found:
        key = str(week)
        game = team_games.get((team_id, key))

        if game:
            game_status = (
                "final" if game.get("completed")
                else "not_final"
            )
            bye = False
        elif schedule_complete.get(key):
            game_status = "no_team_game_scheduled"
            bye = True
        else:
            game_status = "schedule_unverified"
            bye = None

        weekly_status[key] = {
            "scoring_record": key in weekly,
            "team_game_status": game_status,
            "bye_week": bye,
            "participation": "unverified",
            "injury_absence": "unverified"
        }

    availability.append({
        "player_id": player["player_id"],
        "name": player.get("name"),
        "position_id": player.get("position_id"),
        "team_id": player.get("team_id"),
        "latest_injury_status": player.get(
            "injury_status"
        ),
        "weeks_with_scoring_records": [
            int(w) for w in weekly
        ],
        "weeks_without_scoring_records": [
            w for w in weeks_found
            if str(w) not in weekly
        ],
        "confirmed_games_played": None,
        "confirmed_injury_absences": None,
        "weekly_classification": weekly_status
    })

summaries.sort(
    key=lambda p: p["total_fantasy_points"],
    reverse=True
)
consistency.sort(
    key=lambda p: (p["median"], p["average"]),
    reverse=True
)

save_json("player_weekly_summary.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "player_count": len(summaries),
    "players": summaries
})

save_json("player_consistency.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "player_count": len(consistency),
    "methodology": {
        "sample": "Weeks with recorded fantasy points",
        "missing_weeks": "Excluded, not counted as zero",
        "participation": "Not independently verified",
        "injury_absences": "Not independently verified"
    },
    "players": consistency
})

save_json("player_availability.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "weeks_examined": weeks_found,
    "player_count": len(availability),
    "methodology": (
        "Team schedules and bye weeks are checked. "
        "Individual participation and historical injury "
        "absences remain unverified."
    ),
    "players": availability
})

save_json("stats_verification.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "total_players": len(all_players),
    "players_with_weekly_stats": len(summaries),
    "weekly_stat_records": sum(
        len(p.get("stats", [])) for p in all_players
    ),
    "weeks_found": weeks_found,
    "weeks_refreshed_this_run": refreshed,
    "weekly_records_with_points": sum(
        len(p["weeks"]) for p in summaries
    ),
    "week_errors": scoring_errors,
    "schedule_errors": schedule_errors,
    "schedule_weeks_found": sorted(
        int(w) for w in schedule
    )
})

print(
    f"Saved {len(all_players)} players, "
    f"{len(summaries)} scoring summaries, "
    f"and {len(schedule)} NFL schedule weeks"
)
