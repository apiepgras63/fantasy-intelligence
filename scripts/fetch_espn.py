#!/usr/bin/env python3
"""Collect compact ESPN fantasy league snapshots."""
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LEAGUE_ID = "924923029"
SEASON = "2026"
BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/"
    f"games/ffl/seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)
VIEWS = {
    "teams": ["mTeam"],
    "rosters": ["mRoster", "mTeam"],
    "settings": ["mSettings"],
    "matchups": ["mMatchupScore"],
    "transactions": ["mTransactions2"],
}
DATA = Path("data")
DATA.mkdir(exist_ok=True)
errors = {}

def compact_rosters(payload):
    teams = []
    for team in payload.get("teams", []):
        players = []
        for entry in team.get("roster", {}).get("entries", []):
            pool = entry.get("playerPoolEntry", {})
            player = pool.get("player", {})
            players.append({
                "player_id": entry.get("playerId") or player.get("id"),
                "name": player.get("fullName"),
                "position_id": player.get("defaultPositionId"),
                "lineup_slot_id": entry.get("lineupSlotId"),
                "injury_status": player.get("injuryStatus"),
                "pro_team_id": player.get("proTeamId"),
                "acquisition_type": entry.get("acquisitionType"),
            })
        teams.append({
            "team_id": team.get("id"),
            "team_name": team.get("name") or (
                str(team.get("location", "")) + " " +
                str(team.get("nickname", ""))
            ).strip(),
            "players": players,
        })
    return {
        "league_id": str(payload.get("id")),
        "season": payload.get("seasonId"),
        "scoring_period": payload.get("scoringPeriodId"),
        "teams": teams,
    }

for label, views in VIEWS.items():
    url = BASE + "?" + urlencode(
        [("view", view) for view in views]
    )
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=35) as response:
            payload = json.load(response)

        if (
            not isinstance(payload, dict)
            or str(payload.get("id")) != LEAGUE_ID
        ):
            raise ValueError("Unexpected ESPN response")

        if label == "rosters":
            payload = compact_rosters(payload)
            if len(payload["teams"]) != 14:
                raise ValueError("Expected 14 teams")
            if any(
                not team["players"]
                for team in payload["teams"]
            ):
                raise ValueError("One or more rosters are empty")

        (DATA / f"{label}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        print(f"{label}: OK")

    except Exception as exc:
        errors[label] = str(exc)
        print(f"{label}: ERROR: {exc}")

(DATA / "status.json").write_text(
    json.dumps({
        "updated_at_utc": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(),
        "league_id": LEAGUE_ID,
        "errors": errors,
    }, indent=2),
    encoding="utf-8",
)

if "teams" in errors or "rosters" in errors:
    raise SystemExit("Critical ESPN data failed to refresh")
