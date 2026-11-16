
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

# Audit historical data without modifying the original archive.
from collections import Counter

audit = {
    "season": SEASON,
    "updated_at_utc": NOW,
    "total_players": len(all_players),
    "total_stored_stat_entries": 0,
    "valid_weekly_scoring_entries": 0,
    "other_stat_entries": 0,
    "duplicate_player_week_records": [],
    "scores_on_scheduled_byes": [],
    "scoring_records_without_final_games": [],
    "unknown_team_ids": [],
    "players_with_multiple_team_ids": [],
    "stat_entry_types": {},
}

entry_types = Counter()
known_team_ids = {
    team["id"]
    for week_data in schedule.values()
    for game in week_data.get("games", [])
    for team in game.get("teams", [])
    if team.get("id")
}

for player in all_players:
    player_id = player.get("player_id")
    name = player.get("name")
    team_id = str(player.get("team_id"))
    entries = player.get("stats", [])

    audit["total_stored_stat_entries"] += len(entries)

    if team_id not in known_team_ids:
        audit["unknown_team_ids"].append({
            "player_id": player_id,
            "name": name,
            "team_id": team_id,
        })

    seen_weeks = Counter()

    for stat in entries:
        source = stat.get("stat_source_id")
        split = stat.get("stat_split_type_id")
        week = stat.get("week")

        entry_types[f"source_{source}_split_{split}"] += 1

        valid = (
            source == 0
            and split == 1
            and isinstance(week, int)
            and 1 <= week <= 18
            and isinstance(
                stat.get("fantasy_points"), (int, float)
            )
        )

        if not valid:
            audit["other_stat_entries"] += 1
            continue

        audit["valid_weekly_scoring_entries"] += 1
        seen_weeks[week] += 1

        game = team_games.get((team_id, str(week)))

        if (
            game is None
            and schedule_complete.get(str(week))
        ):
            audit["scores_on_scheduled_byes"].append({
                "player_id": player_id,
                "name": name,
                "team_id": team_id,
                "week": week,
                "points": stat.get("fantasy_points"),
            })

        if game and not game.get("completed"):
            audit["scoring_records_without_final_games"].append({
                "player_id": player_id,
                "name": name,
                "week": week,
                "game_status": game.get("status"),
            })

    for week, count in seen_weeks.items():
        if count > 1:
            audit["duplicate_player_week_records"].append({
                "player_id": player_id,
                "name": name,
                "week": week,
                "record_count": count,
            })

audit["stat_entry_types"] = dict(entry_types)

audit["summary"] = {
    "duplicate_player_week_count": len(
        audit["duplicate_player_week_records"]
    ),
    "scores_on_scheduled_byes_count": len(
        audit["scores_on_scheduled_byes"]
    ),
    "nonfinal_game_score_count": len(
        audit["scoring_records_without_final_games"]
    ),
    "unknown_team_id_count": len(
        audit["unknown_team_ids"]
    ),
    "other_stat_entry_count": audit["other_stat_entries"],
}

audit["limitations"] = [
    "Current player team IDs may not match historical teams.",
    "A score on a scheduled bye is a flag for review, not proof of bad data.",
    "Historical injury absences and actual games played remain unverified.",
]

save_json("data_quality_audit.json", audit)

print("DATA QUALITY AUDIT")
print(json.dumps(audit["summary"], indent=2))

# Correct bye-week classifications conservatively.
# Preserve all original scoring records and statistics.

bye_corrections = {
    "season": SEASON,
    "updated_at_utc": NOW,
    "method": (
        "Confirmed team byes are assigned only to D/ST "
        "entries with verified NFL team IDs. "
        "Individual player byes remain unverified "
        "without historical team assignments."
    ),
    "unknown_team_weeks": 0,
    "historical_team_unverified_weeks": 0,
    "confirmed_dst_byes": 0,
    "scoring_team_conflicts": 0,
}

for record in availability:
    team_id = str(record.get("team_id"))
    position_id = record.get("position_id")
    known_team = team_id in known_team_ids

    for week_key, status in record[
        "weekly_classification"
    ].items():
        has_score = status.get("scoring_record", False)
        game = team_games.get((team_id, week_key))
        complete = schedule_complete.get(
            week_key, False
        )

        status["bye_week"] = None
        status["historical_team_verified"] = False

        if not known_team:
            status["team_game_status"] = (
                "unknown_team_assignment"
            )
            status["classification_reason"] = (
                "Player has no recognized current NFL "
                "team ID. Historical bye cannot be verified."
            )
            bye_corrections[
                "unknown_team_weeks"
            ] += 1

        elif position_id == 16:
            # D/ST entries represent a specific NFL team.
            status["historical_team_verified"] = True

            if game:
                status["bye_week"] = False
                status["team_game_status"] = (
                    "final" if game.get("completed")
                    else "not_final"
                )
                status["classification_reason"] = (
                    "D/ST team has a scheduled NFL game."
                )

            elif complete:
                status["bye_week"] = True
                status["team_game_status"] = (
                    "confirmed_team_bye"
                )
                status["classification_reason"] = (
                    "D/ST team has no game in the "
                    "complete weekly NFL schedule."
                )
                bye_corrections[
                    "confirmed_dst_byes"
                ] += 1

            else:
                status["team_game_status"] = (
                    "schedule_unverified"
                )
                status["classification_reason"] = (
                    "Weekly NFL schedule is incomplete."
                )

        else:
            # A player's current team does not prove
            # which team they played for in prior weeks.
            status["team_game_status"] = (
                "historical_team_unverified"
            )
            status["classification_reason"] = (
                "Current team ID cannot establish "
                "historical team assignment or bye."
            )
            bye_corrections[
                "historical_team_unverified_weeks"
            ] += 1

            if has_score and game is None and complete:
                status["team_game_status"] = (
                    "scoring_team_conflict"
                )
                status["classification_reason"] = (
                    "Player scored, but their current "
                    "team has no scheduled game. "
                    "Historical team needs verification."
                )
                bye_corrections[
                    "scoring_team_conflicts"
                ] += 1

    record["bye_week_method"] = (
        "Verified NFL team schedule for D/ST; "
        "unverified historical team for individuals."
    )

# Replace only the derived availability report.
# Original scoring and consistency archives are untouched.
save_json("player_availability.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "weeks_examined": weeks_found,
    "player_count": len(availability),
    "methodology": bye_corrections["method"],
    "players": availability,
})

# Preserve original audit flags for comparison.
audit["original_bye_flags_before_correction"] = (
    audit.get("scores_on_scheduled_byes", [])
)
audit["scores_on_scheduled_byes"] = []

audit["summary"][
    "original_bye_flags_before_correction"
] = len(
    audit["original_bye_flags_before_correction"]
)
audit["summary"][
    "scores_on_scheduled_byes_count"
] = 0
audit["summary"][
    "scoring_team_conflicts_needing_review"
] = bye_corrections["scoring_team_conflicts"]

audit["bye_classification_correction"] = (
    bye_corrections
)

save_json("data_quality_audit.json", audit)
save_json(
    "bye_classification_verification.json",
    bye_corrections
)

print("BYE-WEEK CLASSIFICATION CORRECTION")
print(json.dumps(bye_corrections, indent=2))

# ============================================================
# 2027 DRAFT INTELLIGENCE SYSTEM
# Uses verified scoring records and existing draft history.
# Does not treat missing scores as injuries or missed games.
# ============================================================

import math

DRAFT_YEAR = 2027

# Position-specific scoring expectations for preliminary
# comparison. These are configurable model assumptions,
# not ESPN projections or published expert rankings.
POSITION_BASELINES = {
    "QB": 18.0,
    "RB": 12.0,
    "WR": 12.0,
    "TE": 9.0,
    "K": 8.0,
    "D/ST": 8.0,
}

# Position weights are preliminary draft-model assumptions.
# Final rankings should incorporate 2027 ADP, projected
# roles, rookies, depth charts, and injury reports.
POSITION_WEIGHTS = {
    "QB": 0.90,
    "RB": 1.08,
    "WR": 1.08,
    "TE": 1.00,
    "K": 0.55,
    "D/ST": 0.55,
}

draft_path = OUTPUT / "draft.json"
draft_picks = []

if draft_path.exists():
    try:
        draft_payload = json.loads(
            draft_path.read_text(encoding="utf-8")
        )
        draft_picks = (
            draft_payload.get("draftDetail", {})
            .get("picks", [])
        )
    except (ValueError, OSError) as exc:
        print(f"Draft history unavailable: {exc}")

draft_by_player = {}

for pick in draft_picks:
    player_id = pick.get("playerId")
    overall = pick.get("overallPickNumber")

    if player_id is not None and overall is not None:
        draft_by_player[str(player_id)] = {
            "overall_pick": overall,
            "round": pick.get("roundId"),
            "draft_team_id": pick.get("teamId"),
        }

player_lookup = {
    str(player["player_id"]): player
    for player in all_players
}

availability_lookup = {
    str(player["player_id"]): player
    for player in availability
}

rankings = []
participation_review = []

for summary in summaries:
    player_id = str(summary["player_id"])
    player = player_lookup.get(player_id, {})
    availability_record = availability_lookup.get(
        player_id, {}
    )

    position = POSITIONS.get(
        player.get("position_id"), "OTHER"
    )

    if position not in POSITION_BASELINES:
        continue

    weekly_points = {
        int(week): float(points)
        for week, points in summary["weeks"].items()
    }

    if not weekly_points:
        continue

    scores = list(weekly_points.values())
    recorded_weeks = len(scores)
    average = statistics.mean(scores)
    median = statistics.median(scores)
    deviation = (
        statistics.pstdev(scores)
        if recorded_weeks > 1 else 0.0
    )

    baseline = POSITION_BASELINES[position]
    weight = POSITION_WEIGHTS[position]

    floor = sorted(scores)[
        int((recorded_weeks - 1) * 0.25)
    ]

    ceiling = sorted(scores)[
        int((recorded_weeks - 1) * 0.75)
    ]

    threshold = THRESHOLDS.get(position, baseline)
    hit_rate = sum(
        score >= threshold for score in scores
    ) / recorded_weeks

    # Reward productive, repeatable scoring.
    # Do not automatically penalize injury absences.
    production = (
        median * 0.40
        + average * 0.30
        + floor * 0.20
        + ceiling * 0.10
    )

    stability = max(
        0.70,
        1.0 - (
            deviation / max(average, 1.0)
        ) * 0.15
    )

    sample_confidence = min(
        1.0,
        math.sqrt(recorded_weeks / 12.0)
    )

    draft_score = (
        (production - baseline * 0.35)
        * weight
        * stability
    )

    # Confidence is displayed separately, rather than
    # silently treating injuries as poor performance.
    draft_score = round(draft_score, 3)

    previous_draft = draft_by_player.get(
        player_id, {}
    )

    classification = availability_record.get(
        "weekly_classification", {}
    )

    bye_weeks = [
        int(week)
        for week, status in classification.items()
        if status.get("bye_week") is True
    ]

    scoring_conflicts = [
        int(week)
        for week, status in classification.items()
        if status.get("team_game_status")
        == "scoring_team_conflict"
    ]

    missing_weeks = (
        availability_record.get(
            "weeks_without_scoring_records", []
        )
    )

    ranking = {
        "player_id": int(player_id),
        "name": summary.get("name"),
        "position": position,
        "team_id": player.get("team_id"),
        "draft_score": draft_score,
        "sample_confidence": round(
            sample_confidence, 3
        ),
        "recorded_weeks": recorded_weeks,
        "total_points": round(sum(scores), 2),
        "average": round(average, 2),
        "median": round(median, 2),
        "lower_quartile": round(floor, 2),
        "upper_quartile": round(ceiling, 2),
        "standard_deviation": round(
            deviation, 2
        ),
        "threshold_hit_rate": round(
            hit_rate, 3
        ),
        "weekly_points": weekly_points,
        "2026_draft": previous_draft,
        "confirmed_bye_weeks": bye_weeks,
        "missing_scoring_weeks": missing_weeks,
        "scoring_team_conflicts": scoring_conflicts,
        "confirmed_games_played": None,
        "confirmed_injury_absences": None,
        "injury_review_required": bool(
            missing_weeks
        ),
        "projection_status": (
            "2026 historical model only"
        ),
    }

    rankings.append(ranking)

    if missing_weeks or scoring_conflicts:
        participation_review.append({
            "player_id": int(player_id),
            "name": summary.get("name"),
            "position": position,
            "recorded_weeks": recorded_weeks,
            "missing_scoring_weeks": missing_weeks,
            "scoring_team_conflicts": scoring_conflicts,
            "historical_injury_status": "unverified",
            "games_played": None,
            "review_priority": (
                "high" if scoring_conflicts
                else "normal"
            ),
        })

rankings.sort(
    key=lambda p: p["draft_score"],
    reverse=True
)

# Create both overall and positional rankings.
position_counts = {}

for overall_rank, player in enumerate(
    rankings, start=1
):
    position = player["position"]
    position_counts[position] = (
        position_counts.get(position, 0) + 1
    )
    player["overall_rank"] = overall_rank
    player["position_rank"] = (
        position_counts[position]
    )

# Compare historical 2026 draft positions with
# actual production among players with scoring data.
draft_review = []

for player in rankings:
    previous = player.get("2026_draft") or {}

    if previous.get("overall_pick") is None:
        continue

    draft_review.append({
        "player_id": player["player_id"],
        "name": player["name"],
        "position": player["position"],
        "2026_draft_pick": previous["overall_pick"],
        "2026_draft_round": previous.get("round"),
        "historical_model_rank": player[
            "overall_rank"
        ],
        "recorded_points": player["total_points"],
        "median": player["median"],
        "note": (
            "Historical production comparison only. "
            "Not an injury-adjusted draft value grade."
        ),
    })

draft_review.sort(
    key=lambda p: p["2026_draft_pick"]
)

# Draft board includes editable future rookie and
# expert-ranking inputs. No rookie names are invented.
rookie_path = OUTPUT / "2027_rookie_watchlist.json"

if rookie_path.exists():
    try:
        rookie_watchlist = json.loads(
            rookie_path.read_text(encoding="utf-8")
        ).get("prospects", [])
    except (ValueError, OSError):
        rookie_watchlist = []
else:
    rookie_watchlist = []

save_json("2027_draft_rankings.json", {
    "draft_year": DRAFT_YEAR,
    "source_season": SEASON,
    "updated_at_utc": NOW,
    "league_id": LEAGUE_ID,
    "league_size": 14,
    "scoring_format": "full_PPR",
    "status": "preliminary_historical_rankings",
    "player_count": len(rankings),
    "methodology": {
        "uses": [
            "Recorded weekly fantasy points",
            "Average and median scoring",
            "Lower and upper scoring quartiles",
            "Week-to-week standard deviation",
            "Position-specific baseline assumptions",
        ],
        "does_not_yet_use": [
            "Verified 2027 NFL team assignments",
            "2027 offensive depth charts",
            "Verified historical injury absences",
            "2027 expert rankings and ADP",
            "2027 rookie projections",
        ],
        "warning": (
            "These are historical performance rankings, "
            "not finalized 2027 draft recommendations."
        ),
    },
    "players": rankings,
})

save_json("2026_draft_outcomes.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "total_archived_draft_picks": len(draft_picks),
    "drafted_players_with_scoring_data": len(
        draft_review
    ),
    "comparison": draft_review,
})

save_json("player_participation_review.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "status": "requires_independent_verification",
    "note": (
        "Missing fantasy scoring records are not proof "
        "of injury, inactivity, or a missed NFL game."
    ),
    "players": participation_review,
})

save_json("2027_rookie_watchlist.json", {
    "draft_year": DRAFT_YEAR,
    "updated_at_utc": NOW,
    "status": "awaiting_verified_prospect_data",
    "prospects": rookie_watchlist,
})

save_json("2027_draft_assistant_state.json", {
    "draft_year": DRAFT_YEAR,
    "league_size": 14,
    "scoring_format": "full_PPR",
    "draft_status": "not_started",
    "my_draft_position": None,
    "picks_made": [],
    "my_roster": [],
    "rookies": rookie_watchlist,
    "ranking_source": "2027_draft_rankings.json",
    "note": (
        "Draft state template. Live pick tracking "
        "and recommendations require a separate "
        "interactive interface or draft API connection."
    ),
})

save_json("2027_intelligence_verification.json", {
    "updated_at_utc": NOW,
    "season": SEASON,
    "draft_year": DRAFT_YEAR,
    "ranked_players": len(rankings),
    "draft_picks_archived": len(draft_picks),
    "drafted_players_analyzed": len(draft_review),
    "players_needing_participation_review": len(
        participation_review
    ),
    "rookie_prospects_loaded": len(
        rookie_watchlist
    ),
    "source_scoring_records": sum(
        len(p["weeks"]) for p in summaries
    ),
    "data_preserved": True,
    "live_draft_assistant_ready": False,
    "historical_injury_verification_complete": False,
})

print("2027 DRAFT INTELLIGENCE BUILD COMPLETE")
print(
    f"Ranked {len(rankings)} players, "
    f"analyzed {len(draft_review)} draft picks, "
    f"flagged {len(participation_review)} "
    "players for participation review."
)
