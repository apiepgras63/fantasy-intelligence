
#!/usr/bin/env python3
"""
2027 Fantasy Football Intelligence System

League: 924923029
Format: 14-team, full PPR

Features:
- Historical fantasy scoring and consistency
- NFL box-score participation evidence
- Weekly player usage from box scores
- Injury history and availability review
- Historical draft comparisons
- External rankings and ADP imports
- Rookie prospect imports
- Preliminary 2027 rankings
- Interactive, offline draft room
- Data quality and verification reports

Missing information is marked unverified.
"""

import csv
import datetime as dt
import json
import math
import os
import statistics
import time
import urllib.request
from pathlib import Path

SEASON = str(os.getenv("ESPN_SEASON", "2026"))
DRAFT_YEAR = 2027
ROOT = Path("data") / SEASON
ROOT.mkdir(parents=True, exist_ok=True)

NOW = dt.datetime.now(
    dt.timezone.utc
).isoformat()

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

POSITIONS = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "D/ST",
}

BASELINES = {
    "QB": 18,
    "RB": 12,
    "WR": 12,
    "TE": 9,
    "K": 8,
    "D/ST": 8,
}

WEIGHTS = {
    "QB": 0.90,
    "RB": 1.08,
    "WR": 1.08,
    "TE": 1.00,
    "K": 0.55,
    "D/ST": 0.55,
}


def load(name, default=None):
    path = ROOT / name
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except (ValueError, OSError):
        return default


def save(name, data):
    path = ROOT / name
    temp = path.with_suffix(
        path.suffix + ".tmp"
    )
    temp.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def fetch(url):
    request = urllib.request.Request(
        url,
        headers=HEADERS,
    )
    with urllib.request.urlopen(
        request, timeout=40
    ) as response:
        return json.load(response)


def read_csv(name):
    path = ROOT / name

    if not path.exists():
        return []

    with path.open(
        encoding="utf-8-sig", newline=""
    ) as file:
        return list(csv.DictReader(file))


def number(value, default=None):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def percentile(values, fraction):
    values = sorted(values)
    if not values:
        return None

    index = int(
        (len(values) - 1) * fraction
    )
    return values[index]


def valid_stats(player):
    result = {}

    for stat in player.get("stats", []):
        week = stat.get("week")
        points = stat.get("fantasy_points")

        if (
            stat.get("stat_source_id") == 0
            and stat.get("stat_split_type_id") == 1
            and isinstance(week, int)
            and 1 <= week <= 18
            and isinstance(points, (int, float))
        ):
            result[week] = float(points)

    return result


# ==================================================
# LOAD EXISTING ARCHIVES
# ==================================================

archive = load("player_stats.json", {})
schedule = load("nfl_schedule.json", {})
availability = load(
    "player_availability.json", {}
)
draft = load("draft.json", {})

players = archive.get("players", [])
weeks = schedule.get("weeks", {})

if not players or not weeks:
    raise SystemExit(
        "Run fetch_player_stats.py first."
    )

player_lookup = {
    str(p["player_id"]): p
    for p in players
}

availability_lookup = {
    str(p["player_id"]): p
    for p in availability.get("players", [])
}

draft_picks = (
    draft.get("draftDetail", {})
    .get("picks", [])
)

draft_lookup = {
    str(p["playerId"]): p
    for p in draft_picks
    if p.get("playerId") is not None
}

# ==================================================
# HISTORICAL NFL BOXSCORE COLLECTION
# ==================================================

cache = load(
    "2026_nfl_boxscore_cache.json", {}
)

new_games = 0
boxscore_errors = {}

for week, info in weeks.items():
    for game in info.get("games", []):
        event_id = str(
            game.get("event_id") or ""
        )

        if not event_id:
            continue

        if not game.get("completed"):
            continue

        if event_id in cache:
            continue

        url = (
            "https://site.api.espn.com/"
            "apis/site/v2/sports/football/nfl/"
            f"summary?event={event_id}"
        )

        try:
            payload = fetch(url)

            cache[event_id] = {
                "week": int(week),
                "event_id": event_id,
                "date": game.get("date"),
                "boxscore": payload.get(
                    "boxscore", {}
                ),
                "injuries": payload.get(
                    "injuries", []
                ),
                "collected_at": NOW,
            }

            new_games += 1
            time.sleep(0.12)

        except Exception as exc:
            boxscore_errors[event_id] = str(exc)

# Save incrementally collected evidence.
save(
    "2026_nfl_boxscore_cache.json",
    cache,
)

# ==================================================
# PARSE BOXSCORE PARTICIPATION AND USAGE
# ==================================================

participation = {}
usage = {}
game_injuries = []

for event_id, record in cache.items():
    week = record["week"]
    boxscore = record.get("boxscore", {})

    for team in boxscore.get("players", []):
        team_id = str(
            (team.get("team") or {}).get("id")
        )

        for group in team.get("statistics", []):
            labels = group.get("labels", [])
            category = group.get("name", "")

            for athlete in group.get(
                "athletes", []
            ):
                person = athlete.get(
                    "athlete", {}
                )

                player_id = person.get("id")

                if player_id is None:
                    continue

                player_id = str(player_id)

                values = athlete.get(
                    "stats", []
                )

                stats = dict(
                    zip(labels, values)
                )

                key = (
                    player_id,
                    int(week),
                    event_id,
                )

                participation[key] = {
                    "player_id": player_id,
                    "name": person.get(
                        "displayName"
                    ),
                    "week": int(week),
                    "event_id": event_id,
                    "team_id": team_id,
                    "evidence": (
                        "ESPN NFL boxscore listing"
                    ),
                    "appearance_verified": True,
                }

                usage.setdefault(
                    key, {}
                )[category] = stats

    # Injury information associated with a game
    # is evidence only if ESPN provides it.
    for injury_group in record.get(
        "injuries", []
    ):
        team_id = str(
            (injury_group.get("team") or {})
            .get("id")
        )

        for injury in injury_group.get(
            "injuries", []
        ):
            athlete = injury.get(
                "athlete", {}
            )

            if not athlete.get("id"):
                continue

            game_injuries.append({
                "player_id": str(
                    athlete["id"]
                ),
                "name": athlete.get(
                    "displayName"
                ),
                "week": int(week),
                "team_id": team_id,
                "status": injury.get(
                    "status"
                ),
                "type": (
                    injury.get("type") or {}
                ).get("description"),
                "details": injury.get(
                    "details"
                ),
                "source": (
                    "ESPN game injury data"
                ),
                "event_id": event_id,
            })

# ==================================================
# IMPORT INDEPENDENT INJURY HISTORY
# ==================================================

# Optional file:
# data/2026/injury_history.csv
#
# Columns:
# player_id,week,status,injury,source,notes
#
# Source should identify where the record came from.
# Unverified entries remain labeled as such.

injury_imports = read_csv(
    "injury_history.csv"
)

injury_by_player = {}

for injury in game_injuries:
    injury_by_player.setdefault(
        injury["player_id"], []
    ).append(injury)

for row in injury_imports:
    player_id = str(
        row.get("player_id", "")
    ).strip()

    if not player_id:
        continue

    injury_by_player.setdefault(
        player_id, []
    ).append({
        "player_id": player_id,
        "week": row.get("week"),
        "status": row.get("status"),
        "type": row.get("injury"),
        "source": row.get("source"),
        "notes": row.get("notes"),
        "origin": "manual_csv_import",
    })

# ==================================================
# IMPORT OUTSIDE RANKINGS AND ADP
# ==================================================

# Optional file:
# data/2026/external_rankings.csv
#
# Columns:
# player_id,source,rank,adp
#
# Examples of source labels:
# ESPN, FantasyPros, Fantasy Life
#
# Only import rankings you actually have.
# Do not fabricate unpublished 2027 ADP.

external_rows = read_csv(
    "external_rankings.csv"
)

external_by_player = {}

for row in external_rows:
    player_id = str(
        row.get("player_id", "")
    ).strip()

    if not player_id:
        continue

    external_by_player.setdefault(
        player_id, []
    ).append({
        "source": row.get("source"),
        "rank": number(row.get("rank")),
        "adp": number(row.get("adp")),
    })

# ==================================================
# IMPORT 2027 ROOKIE PROSPECTS
# ==================================================

# Optional file:
# data/2026/rookie_prospects.csv
#
# Columns:
# name,position,college,projected_round,
# source,notes
#
# Prospects are not assigned invented grades.

rookies = read_csv(
    "rookie_prospects.csv"
)

rookie_watchlist = []

for index, row in enumerate(rookies):
    if not row.get("name"):
        continue

    rookie_watchlist.append({
        "id": f"rookie-{index}",
        "name": row.get("name"),
        "position": row.get("position"),
        "college": row.get("college"),
        "projected_round": row.get(
            "projected_round"
        ),
        "source": row.get("source"),
        "notes": row.get("notes"),
        "nfl_team": None,
        "draft_capital": None,
        "rookie_grade": None,
        "status": (
            "prospect awaiting verification"
        ),
    })

# ==================================================
# BUILD PLAYER RANKINGS
# ==================================================

rankings = []
participation_review = []

for player in players:
    player_id = str(player["player_id"])
    weekly = valid_stats(player)

    if not weekly:
        continue

    position = POSITIONS.get(
        player.get("position_id"),
        "OTHER",
    )

    if position not in BASELINES:
        continue

    scores = list(weekly.values())

    average = statistics.mean(scores)
    median = statistics.median(scores)

    deviation = (
        statistics.pstdev(scores)
        if len(scores) > 1 else 0
    )

    floor = percentile(scores, 0.25)
    ceiling = percentile(scores, 0.75)

    listed_games = {
        week
        for pid, week, event in participation
        if pid == player_id
    }

    player_usage = []

    for key, categories in usage.items():
        pid, week, event = key

        if pid == player_id:
            player_usage.append({
                "week": week,
                "event_id": event,
                "categories": categories,
            })

    player_injuries = injury_by_player.get(
        player_id, []
    )

    availability_record = (
        availability_lookup.get(
            player_id, {}
        )
    )

    missing_weeks = (
        availability_record.get(
            "weeks_without_scoring_records",
            [],
        )
    )

    external = external_by_player.get(
        player_id, []
    )

    adps = [
        x["adp"]
        for x in external
        if x.get("adp") is not None
    ]

    expert_ranks = [
        x["rank"]
        for x in external
        if x.get("rank") is not None
    ]

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
        ) * 0.15,
    )

    draft_score = (
        production
        - BASELINES[position] * 0.35
    ) * WEIGHTS[position] * stability

    # Injury information is reported separately.
    # Do not automatically penalize injured players.
    confidence = min(
        1.0,
        math.sqrt(len(scores) / 12)
    )

    old_pick = draft_lookup.get(
        player_id, {}
    )

    record = {
        "player_id": player_id,
        "name": player.get("name"),
        "position": position,
        "team_id": player.get("team_id"),
        "draft_score": round(
            draft_score, 3
        ),
        "sample_confidence": round(
            confidence, 3
        ),
        "recorded_weeks": len(scores),
        "total_points": round(
            sum(scores), 2
        ),
        "average": round(average, 2),
        "median": round(median, 2),
        "floor": round(floor, 2),
        "ceiling": round(ceiling, 2),
        "standard_deviation": round(
            deviation, 2
        ),
        "weekly_points": weekly,
        "boxscore_listed_weeks": sorted(
            listed_games
        ),
        "boxscore_listed_games": len(
            listed_games
        ),
        "weekly_usage": player_usage,
        "injury_records": player_injuries,
        "missing_scoring_weeks": (
            missing_weeks
        ),
        "injury_absences_confirmed": None,
        "games_missed_confirmed": None,
        "2026_draft_pick": old_pick.get(
            "overallPickNumber"
        ),
        "expert_rankings": external,
        "average_expert_rank": (
            round(statistics.mean(
                expert_ranks
            ), 2)
            if expert_ranks else None
        ),
        "average_adp": (
            round(statistics.mean(
                adps
            ), 2)
            if adps else None
        ),
        "2027_projection": None,
    }

    rankings.append(record)

    if missing_weeks or player_injuries:
        participation_review.append({
            "player_id": player_id,
            "name": player.get("name"),
            "missing_scoring_weeks": (
                missing_weeks
            ),
            "injury_evidence": player_injuries,
            "boxscore_listed_weeks": sorted(
                listed_games
            ),
            "needs_review": True,
        })

rankings.sort(
    key=lambda p: p["draft_score"],
    reverse=True,
)

position_counts = {}

for index, player in enumerate(
    rankings, 1
):
    position = player["position"]

    position_counts[position] = (
        position_counts.get(position, 0) + 1
    )

    player["overall_rank"] = index
    player["position_rank"] = (
        position_counts[position]
    )

    adp = player.get("average_adp")

    player["adp_value_gap"] = (
        round(adp - index, 2)
        if adp is not None else None
    )

# ==================================================
# HISTORICAL DRAFT ANALYSIS
# ==================================================

draft_analysis = []

for player in rankings:
    pick = player.get("2026_draft_pick")

    if pick is None:
        continue

    draft_analysis.append({
        "player_id": player["player_id"],
        "name": player["name"],
        "position": player["position"],
        "2026_pick": pick,
        "historical_model_rank": (
            player["overall_rank"]
        ),
        "points": player["total_points"],
        "median": player["median"],
    })

draft_analysis.sort(
    key=lambda p: p["2026_pick"]
)

# ==================================================
# SAVE ALL REPORTS
# ==================================================

save("2027_draft_rankings.json", {
    "draft_year": DRAFT_YEAR,
    "source_season": SEASON,
    "updated_at_utc": NOW,
    "status": (
        "preliminary historical rankings"
    ),
    "methodology": (
        "Historical fantasy scoring and consistency. "
        "Injuries are reported separately. "
        "Future performance is not guaranteed."
    ),
    "players": rankings,
})

save("2027_injury_intelligence.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "game_injury_records": game_injuries,
    "manual_injury_records": injury_imports,
    "review_queue": participation_review,
    "limitations": [
        "Game injury feeds may be incomplete.",
        "A missing fantasy score is not proof of injury.",
        "Boxscore absence is not proof of inactivity.",
        "Historical injury recovery is not projected.",
    ],
})

save("2027_player_usage.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "source": "ESPN NFL game boxscores",
    "records": [
        {
            "player_id": pid,
            "week": week,
            "event_id": event,
            "statistics": categories,
        }
        for (pid, week, event), categories
        in usage.items()
    ],
    "note": (
        "Boxscore categories are not equivalent "
        "to independently verified snap counts, "
        "routes run, or target shares."
    ),
})

save("2027_external_rankings.json", {
    "draft_year": DRAFT_YEAR,
    "updated_at_utc": NOW,
    "records": external_rows,
    "status": (
        "Imported sources only; no fabricated ADP"
    ),
})

save("2027_rookie_watchlist.json", {
    "draft_year": DRAFT_YEAR,
    "updated_at_utc": NOW,
    "prospects": rookie_watchlist,
})

save("2026_draft_outcomes.json", {
    "season": SEASON,
    "draft_picks_archived": len(
        draft_picks
    ),
    "comparison": draft_analysis,
})

save("2027_participation_evidence.json", {
    "season": SEASON,
    "updated_at_utc": NOW,
    "cached_games": len(cache),
    "new_games_this_run": new_games,
    "errors": boxscore_errors,
    "verified_boxscore_listings": len(
        participation
    ),
    "note": (
        "A boxscore listing is evidence of "
        "participation, not independent proof "
        "of every snap or injury absence."
    ),
})

# ==================================================
# INTERACTIVE OFFLINE DRAFT ROOM
# ==================================================

board = []

for player in rankings:
    board.append({
        "id": player["player_id"],
        "name": player["name"],
        "pos": player["position"],
        "rank": player["overall_rank"],
        "score": player["draft_score"],
        "adp": player["average_adp"],
        "median": player["median"],
    })

for rookie in rookie_watchlist:
    board.append({
        "id": rookie["id"],
        "name": rookie["name"],
        "pos": rookie["position"],
        "rank": None,
        "score": None,
        "adp": None,
        "median": None,
    })

html = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>2027 Fantasy Draft Intelligence</title>
<style>
body{
 font-family:system-ui,sans-serif;
 background:#101827;
 color:#f1f5f9;
 margin:auto;
 max-width:1100px;
 padding:18px;
}
h1{font-size:26px}
p,small{color:#b7c4d7}
input,select,button{
 padding:10px;
 margin:4px;
 background:#24344c;
 color:white;
 border:1px solid #64748b;
 border-radius:7px;
}
button{cursor:pointer}
table{
 width:100%;
 border-collapse:collapse;
}
th,td{
 padding:9px;
 border-bottom:1px solid #334155;
 text-align:left;
}
th{color:#cbd5e1}
.controls{
 display:flex;
 flex-wrap:wrap;
 gap:6px;
}
.scroll{overflow-x:auto}
</style>
</head>
<body>
<h1>2027 Fantasy Draft Room</h1>
<p>
14 teams | Full PPR | Historical scouting board
</p>
<p>
Rankings are preliminary. Injury verification,
rookie scouting, and 2027 projections may
still be incomplete.
</p>

<div class="controls">
<input id="search" placeholder="Search player">
<select id="position">
<option value="">All positions</option>
<option>QB</option>
<option>RB</option>
<option>WR</option>
<option>TE</option>
<option>K</option>
<option>D/ST</option>
</select>
<select id="team">
<option value="other">Other team</option>
<option value="mine">My team</option>
</select>
<button onclick="undo()">Undo last pick</button>
<button onclick="resetDraft()">Reset draft</button>
</div>

<p id="status"></p>
<p id="myRoster"></p>

<div class="scroll">
<table>
<thead>
<tr>
<th>Rank</th>
<th>Player</th>
<th>Pos</th>
<th>Score</th>
<th>ADP</th>
<th>Median</th>
<th>Action</th>
</tr>
</thead>
<tbody id="rows"></tbody>
</table>
</div>

<script>
const players = __PLAYER_DATA__;
const key = "fantasy-intelligence-2027-picks";

let picks = [];

try {
 picks = JSON.parse(
  localStorage.getItem(key) || "[]"
 );
 if (!Array.isArray(picks)) picks = [];
} catch {
 picks = [];
}

function save() {
 localStorage.setItem(
  key, JSON.stringify(picks)
 );
 render();
}

function draft(id) {
 id = String(id);

 if (picks.some(p => p.id === id)) return;

 picks.push({
  id:id,
  mine:document.getElementById(
   "team"
  ).value === "mine"
 });

 save();
}

function undo() {
 picks.pop();
 save();
}

function resetDraft() {
 if (confirm("Clear all recorded picks?")) {
  picks = [];
  save();
 }
}

function escapeText(value) {
 return String(value)
  .replaceAll("&","&amp;")
  .replaceAll("<","&lt;")
  .replaceAll(">","&gt;")
  .replaceAll('"',"&quot;");
}

function render() {
 const query = document.getElementById(
  "search"
 ).value.toLowerCase();

 const position = document.getElementById(
  "position"
 ).value;

 const taken = new Set(
  picks.map(p => p.id)
 );

 const available = players.filter(p =>
  !taken.has(String(p.id))
 );

 const mine = picks
  .filter(p => p.mine)
  .map(p => players.find(
   x => String(x.id) === p.id
  ))
  .filter(Boolean);

 document.getElementById(
  "status"
 ).textContent =
  picks.length + " picks recorded | " +
  available.length + " players available";

 document.getElementById(
  "myRoster"
 ).textContent =
  "My roster: " +
  (mine.map(p => p.name).join(", ") || "Empty");

 const filtered = available.filter(p =>
  p.name.toLowerCase().includes(query) &&
  (!position || p.pos === position)
 );

 document.getElementById(
  "rows"
 ).innerHTML = filtered.slice(0,150)
  .map(p => `
   <tr>
    <td>${p.rank ?? "R"}</td>
    <td>${escapeText(p.name)}</td>
    <td>${escapeText(p.pos)}</td>
    <td>${p.score ?? "-"}</td>
    <td>${p.adp ?? "-"}</td>
    <td>${p.median ?? "-"}</td>
    <td>
     <button onclick='draft(${JSON.stringify(
      String(p.id)
     )})'>Drafted</button>
    </td>
   </tr>
  `).join("");
}

document.getElementById(
 "search"
).oninput = render;

document.getElementById(
 "position"
).onchange = render;

render();
</script>
</body>
</html>
"""

html = html.replace(
    "__PLAYER_DATA__",
    json.dumps(board).replace(
        "</", "<\\/"
    ),
)

(ROOT / "2027_draft_room.html").write_text(
    html,
    encoding="utf-8",
)

# ==================================================
# VERIFICATION REPORT
# ==================================================

verification = {
    "season": SEASON,
    "draft_year": DRAFT_YEAR,
    "updated_at_utc": NOW,
    "players_ranked": len(rankings),
    "source_players": len(players),
    "draft_picks_archived": len(draft_picks),
    "scoring_records": sum(
        len(valid_stats(p))
        for p in players
    ),
    "nfl_games_cached": len(cache),
    "new_games_collected": new_games,
    "boxscore_player_listings": len(
        participation
    ),
    "injury_records_found": len(
        game_injuries
    ),
    "manual_injury_records": len(
        injury_imports
    ),
    "players_flagged_for_review": len(
        participation_review
    ),
    "external_rankings_loaded": len(
        external_rows
    ),
    "rookies_loaded": len(
        rookie_watchlist
    ),
    "draft_room_created": True,
    "injury_history_complete": False,
    "2027_projections_complete": False,
    "automatic_espn_draft_sync": False,
    "boxscore_errors": boxscore_errors,
}

save(
    "2027_intelligence_verification.json",
    verification,
)

print("2027 INTELLIGENCE BUILD COMPLETE")
print(json.dumps(
    verification, indent=2
))
