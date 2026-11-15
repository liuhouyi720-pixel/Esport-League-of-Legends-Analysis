"""Inspect a LoL final-state snapshot without requiring event-file access."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from analysis.grid_client import GridError


def state_query(series_id):
    """Request draft actions and LoL-specific summary fields, not event history."""
    return '''query FreeAccessLolState {seriesState(id: %s) {
      id version format started finished valid updatedAt startedAt duration
      teams {id name won score}
      draftActions {id type sequenceNumber drafter {id type} draftable {id type name}}
      games {
        id sequenceNumber started finished valid startedAt duration
        titleVersion {name}
        clock {currentSeconds ticking}
        draftActions {id type sequenceNumber drafter {id type} draftable {id type name}}
        teams {
          __typename id name side won kills deaths killAssistsGiven
          money loadoutValue netWorth structuresDestroyed
          objectives {id type completionCount completedFirst}
          ... on GameTeamStateLol {
            totalMoneyEarned moneyDifference damageDealt damageTaken visionScore
            moneyPerMinute majorMoneyLead majorMoneyDeficit
            baronPowerPlays {id value}
          }
          players {
            __typename id name character {id name} roles {id}
            kills deaths killAssistsGiven money loadoutValue netWorth
            position {x y}
            ... on GamePlayerStateLol {
              totalMoneyEarned damageDealt damageTaken visionScore
              experiencePoints moneyPerMinute damagePerMinute
            }
          }
        }
      }
    }}''' % json.dumps(str(series_id))


def collect_state(client, series_id, output_dir):
    """Save the exact query, response and provenance; never persist credentials."""
    query = state_query(series_id)
    response = client.graphql(query, state=True).get("seriesState")
    if not response or str(response.get("id")) != str(series_id):
        raise GridError("The requested series state was not returned; no cache was replaced.")
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(response, indent=2) + "\n").encode("utf-8")
    manifest = {
        "series_id": str(series_id),
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": client.state_url,
        "source": "GRID Series State API",
        "snapshot_kind": "final" if response.get("finished") else "current",
        "state_updated_at": response.get("updatedAt"),
        "state_sha256": hashlib.sha256(payload).hexdigest(),
        "query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "event_file_api_used": False,
    }
    (folder / "series_state.json").write_bytes(payload)
    (folder / "query.graphql").write_text(query, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return response, manifest


TEAM_FIELDS = [
    "side", "won", "kills", "deaths", "killAssistsGiven", "money", "loadoutValue",
    "netWorth", "structuresDestroyed", "totalMoneyEarned", "moneyDifference",
    "damageDealt", "damageTaken", "visionScore", "moneyPerMinute",
    "majorMoneyLead", "majorMoneyDeficit",
]
PLAYER_FIELDS = [
    "kills", "deaths", "killAssistsGiven", "money", "loadoutValue", "netWorth",
    "totalMoneyEarned", "damageDealt", "damageTaken", "visionScore",
    "experiencePoints", "moneyPerMinute", "damagePerMinute",
]


def duration_seconds(value):
    """Parse the ISO 8601 time durations returned here; reject unknown formats."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", value)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = (float(x or 0) for x in match.groups())
    return 3600 * hours + 60 * minutes + seconds


def flatten_state(state):
    """Keep game, team-game, player-game, draft-action and objective grains separate."""
    games, teams, players, drafts, objectives = [], [], [], [], []
    for game in state.get("games") or []:
        base = {"series_id": state["id"], "game_id": game.get("id"),
                "game_number": game.get("sequenceNumber")}
        games.append({**base, "started": game.get("started"), "finished": game.get("finished"),
                      "valid": game.get("valid"), "duration_seconds": duration_seconds(game.get("duration")),
                      "clock_seconds": (game.get("clock") or {}).get("currentSeconds"),
                      "patch": (game.get("titleVersion") or {}).get("name")})
        for team in game.get("teams") or []:
            team_base = {**base, "team_id": team.get("id"), "team_name": team.get("name")}
            teams.append({**team_base, **{k: team.get(k) for k in TEAM_FIELDS}})
            for player in team.get("players") or []:
                position = player.get("position") or {}
                players.append({**team_base, "player_id": player.get("id"),
                                "player_name": player.get("name"),
                                "champion_id": (player.get("character") or {}).get("id"),
                                "champion": (player.get("character") or {}).get("name"),
                                "role_ids": [r.get("id") for r in player.get("roles") or []],
                                "x": position.get("x"), "y": position.get("y"),
                                **{k: player.get(k) for k in PLAYER_FIELDS}})
            for objective in team.get("objectives") or []:
                objectives.append({**team_base, "objective_id": objective.get("id"),
                                   "objective": objective.get("type"),
                                   "count": objective.get("completionCount"),
                                   "completed_first": objective.get("completedFirst")})
        for action in game.get("draftActions") or []:
            drafter, draftable = action.get("drafter") or {}, action.get("draftable") or {}
            drafts.append({**base, "action_id": action.get("id"), "action": action.get("type"),
                           "sequence": pd.to_numeric(action.get("sequenceNumber"), errors="coerce"),
                           "drafter_type": drafter.get("type"), "team_id": drafter.get("id"),
                           "draftable_type": draftable.get("type"),
                           "champion_id": draftable.get("id"), "champion": draftable.get("name")})
    return {name: pd.DataFrame(rows) for name, rows in [
        ("games", games), ("teams", teams), ("players", players),
        ("drafts", drafts), ("objectives", objectives)]}


def availability_audit(state):
    """Count absent keys, nulls, empty lists and observed zeros separately."""
    groups = {"game": [], "team-game": [], "player-game": [], "draft-action": []}
    for game in state.get("games") or []:
        groups["game"].append(game)
        groups["draft-action"].extend(game.get("draftActions") or [])
        for team in game.get("teams") or []:
            groups["team-game"].append(team)
            groups["player-game"].extend(team.get("players") or [])
    fields = {
        "game": ["id", "sequenceNumber", "finished", "valid", "duration", "clock.currentSeconds", "draftActions"],
        "team-game": ["id", *TEAM_FIELDS, "objectives", "baronPowerPlays"],
        "player-game": ["id", "character.name", "roles", *PLAYER_FIELDS, "position.x", "position.y"],
        "draft-action": ["id", "type", "sequenceNumber", "drafter.id", "draftable.id", "draftable.name"],
    }
    absent = object()
    rows = []
    for grain, keys in fields.items():
        for key in keys:
            counts = {"absent": 0, "null": 0, "empty_collection": 0, "observed": 0, "observed_zero": 0}
            for item in groups[grain]:
                value = item
                for part in key.split("."):
                    value = value.get(part, absent) if isinstance(value, dict) else absent
                if value is absent:
                    counts["absent"] += 1
                elif value is None:
                    counts["null"] += 1
                elif isinstance(value, (list, dict)) and not value:
                    counts["empty_collection"] += 1
                else:
                    counts["observed"] += 1
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
                        counts["observed_zero"] += 1
            rows.append({"grain": grain, "field": key, "rows": len(groups[grain]), **counts})
    return pd.DataFrame(rows)


def schema_inventory(schema):
    """Expose official types and descriptions from a cached introspection result."""
    def typename(value):
        if value["kind"] == "NON_NULL":
            return typename(value["ofType"]) + "!"
        if value["kind"] == "LIST":
            return "[" + typename(value["ofType"]) + "]"
        return value["name"]

    rows = []
    for item in schema["__schema"]["types"]:
        if item["name"] in {"SeriesState", "GameState", "GameTeamStateLol", "GamePlayerStateLol", "DraftAction", "Draftable", "Drafter", "Objective"}:
            for field in item.get("fields") or []:
                rows.append({"type": item["name"], "field": field["name"],
                             "graphql_type": typename(field["type"]), "description": field.get("description")})
    return pd.DataFrame(rows)


def grain_audit(tables, state):
    """Return blocking key/roster checks and separate review-level reconciliation."""
    games, teams, players, drafts = (tables[k] for k in ["games", "teams", "players", "drafts"])
    rows = []

    def check(name, passed, detail, severity="block"):
        rows.append({"check": name, "passed": bool(passed), "severity": severity, "detail": detail})

    for name, frame, key in [
        ("games", games, ["series_id", "game_id"]),
        ("teams", teams, ["game_id", "team_id"]),
        ("players", players, ["game_id", "player_id"]),
        ("drafts", drafts, ["game_id", "action_id"]),
    ]:
        check(name + " keys", not frame.empty and not frame[key].isna().any().any() and not frame.duplicated(key).any(),
              f"{len(frame)} rows; key = {', '.join(key)}")
    check("Game numbers", not games["game_number"].isna().any() and games["game_number"].is_unique,
          "Use the returned sequenceNumber; do not infer game number from array order.")
    check("Completed valid games", games["finished"].eq(True).all() and games["valid"].eq(True).all(),
          "This notebook analyzes completed, valid games only.")
    check("Two teams per game", teams.groupby("game_id").size().eq(2).all(), "Expected for the selected LoL series.")
    check("Five players per team-game", players.groupby(["game_id", "team_id"]).size().eq(5).all(),
          "Expected for the selected LoL series.")
    known = {t["id"] for t in state.get("teams") or []}
    check("Persistent team IDs", set(teams["team_id"]).issubset(known), "Match team IDs to the series roster, not team-array order.")
    joined = players.merge(teams[["game_id", "team_id"]], on=["game_id", "team_id"], how="left", indicator=True, validate="many_to_one")
    check("Player-to-team join", joined["_merge"].eq("both").all(), "Many players to one team-game; no unmatched players.")
    check("One winner per game", teams.groupby("game_id")["won"].sum(min_count=2).eq(1).all(), "A finished game should have exactly one winning team.")
    check("Draft team IDs", drafts["drafter_type"].eq("team").all() and set(zip(drafts.game_id, drafts.team_id)).issubset(set(zip(teams.game_id, teams.team_id))),
          "Every action must resolve to a team in that same game.")
    characters = drafts[drafts.draftable_type.eq("character") & drafts.action.isin(["pick", "ban"])]
    counts = characters.groupby(["game_id", "team_id", "action"]).size()
    check("Draft completeness", len(counts) == len(teams) * 2 and counts.eq(5).all() and characters["champion_id"].notna().all() and characters["sequence"].notna().all(),
          "Selected format: five picks and five bans per team-game.")
    check("Draft sequence uniqueness", not characters.duplicated(["game_id", "sequence"]).any(), "Draft action IDs and sequence numbers are scoped to each game.")
    picked = characters[characters.action.eq("pick")]
    a = set(zip(picked.game_id, picked.team_id, picked.champion_id))
    b = set(zip(players.game_id, players.team_id, players.champion_id))
    check("Picks match player champions", a == b, "Compare champion IDs at game/team grain.")
    reconciliation = teams[["game_id", "game_number", "team_id", "team_name"]].copy()
    for field in ["kills", "deaths", "totalMoneyEarned", "netWorth"]:
        totals = players.groupby(["game_id", "team_id"])[field].agg(lambda x: x.sum(min_count=len(x))).rename("player_sum")
        compared = teams.merge(totals, on=["game_id", "team_id"], validate="one_to_one")
        residual = compared[field] - compared["player_sum"]
        reconciliation[field + "_residual"] = residual.to_numpy()
        check("Team minus player sums: " + field, residual.notna().all() and residual.eq(0).all(),
              f"{residual.ne(0).sum()} nonzero/missing residuals; preserve source values and investigate.", "review")
    return pd.DataFrame(rows), reconciliation


def gold_comparison(teams, focus_team_id):
    """Compare two aligned team totals; never turn a missing value into zero."""
    rows = []
    for game_id, group in teams.groupby("game_id", sort=False):
        focus = group[group.team_id.eq(str(focus_team_id))]
        opponent = group[~group.team_id.eq(str(focus_team_id))]
        if len(focus) != 1 or len(opponent) != 1:
            raise ValueError("Each game must contain the focus team and exactly one opponent.")
        own, other = focus.iloc[0], opponent.iloc[0]
        diff = own.totalMoneyEarned - other.totalMoneyEarned
        rows.append({"game_id": game_id, "game_number": own.game_number, "focus_side": own.side,
                     "focus_gold": own.totalMoneyEarned, "opponent_gold": other.totalMoneyEarned,
                     "gold_difference": diff, "api_money_difference": own.moneyDifference,
                     "difference_residual": diff - own.moneyDifference})
    return pd.DataFrame(rows).sort_values("game_number").reset_index(drop=True)
