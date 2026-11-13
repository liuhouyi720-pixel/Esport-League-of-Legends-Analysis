"""Evidence-based audits for Oracle's Elixir player-game and team-game exports.

Missingness reasons describe what the file supports. An unexpected blank or a
bounded run of blanks does not establish its upstream provider or an outage.
Functions preserve input values, including zeros, names, identifiers, and nulls.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

ROLES = ("top", "jng", "mid", "bot", "sup")
STATES = (
    "observed", "observed_zero", "structural_not_applicable",
    "structural_checkpoint_not_reached", "absent_from_schema",
    "eligibility_unknown", "unexpected_missing",
    "observed_outside_domain", "observed_checkpoint_not_reached", "invalid_value",
)
OBSERVED = ("observed", "observed_zero")


def has_value(series):
    """Recognize null/blank labels without substituting a fabricated identity."""
    return series.notna() & series.astype("string").str.strip().ne("").fillna(False)


def row_types(frame):
    position = frame["position"] if "position" in frame else pd.Series(pd.NA, index=frame.index)
    return pd.Series(np.select([position.eq("team").fillna(False), position.isin(ROLES)],
                              ["team", "player"], default="unknown"), index=frame.index, name="row_type")


def classify_field(frame, field, contract):
    """Classify cells using explicit row-domain rules and checkpoint eligibility.

    Missing-column, unknown-eligibility and unexpected-missing states remain
    separate. A zero is observed data, never a replacement for a missing value.
    """
    kind = row_types(frame)
    domain = contract.get("domains", {}).get(field, "both")
    applicable = kind.isin([domain]) if domain in {"player", "team"} else kind.isin(["player", "team"])
    absent = field not in frame
    values = frame[field] if not absent else pd.Series(pd.NA, index=frame.index)
    present = has_value(values)
    states = pd.Series("unexpected_missing", index=frame.index, dtype="object")
    states.loc[present] = "observed"
    if field in contract.get("numeric_columns", []) or pd.api.types.is_numeric_dtype(values.dtype):
        numeric = pd.to_numeric(values, errors="coerce")
        invalid = present & (~np.isfinite(numeric.to_numpy(dtype=float, na_value=np.nan)))
        if field == "gamelength":
            invalid |= present & numeric.le(0)
        if field in {"damageshare", "earnedgoldshare"}:
            invalid |= present & ~numeric.between(-1e-6, 1+1e-6)
        if field in {"wardsplaced", "wardskilled", "visionscore", "dpm", "kills", "deaths", "assists", "totalgold", "damagetochampions", "cspm"}:
            invalid |= present & numeric.lt(0)
        if field == "result":
            invalid |= present & ~numeric.isin([0, 1])
        states.loc[present & numeric.eq(0)] = "observed_zero"
        states.loc[invalid] = "invalid_value"
    states.loc[~applicable & ~present] = "structural_not_applicable"
    states.loc[~applicable & present] = "observed_outside_domain"
    states.loc[kind.eq("unknown")] = "eligibility_unknown"
    checkpoint = re.search(r"at(\d+)$", field)
    if checkpoint:
        if contract.get("duration_unit") != "seconds":
            raise ValueError("Checkpoint eligibility requires an explicitly verified duration unit.")
        duration = pd.to_numeric(frame.get("gamelength", pd.Series(pd.NA, index=frame.index)), errors="coerce")
        known = duration.notna() & duration.gt(0)
        not_reached = known & duration.lt(int(checkpoint.group(1))*60) & applicable
        states.loc[not_reached & ~present] = "structural_checkpoint_not_reached"
        states.loc[not_reached & present] = "observed_checkpoint_not_reached"
        states.loc[~known & applicable] = "eligibility_unknown"
    if field in contract.get("context_dependent_fields", []):
        states.loc[applicable & ~present] = "eligibility_unknown"
    if absent:
        states.loc[applicable] = "absent_from_schema"
    return pd.Series(pd.Categorical(states, categories=STATES), index=frame.index, name="state")


def schema_audit(frame, contract):
    """Compare a pinned schema to current columns; flag aliases without renaming."""
    expected = contract["columns"]
    required = set(contract.get("required_for_notebook", []))
    numeric = set(contract.get("numeric_columns", []))
    rows = []
    for field in dict.fromkeys([*expected, *frame.columns]):
        aliases = [a for a in contract.get("legacy_aliases", {}).get(field, []) if a in frame]
        present = field in frame
        if present and aliases:
            status = "ambiguous_aliases_review"
        elif present and field not in expected:
            status = "new_column_review"
        elif present:
            status = "present"
        elif aliases:
            status = "legacy_alias_requires_review"
        else:
            status = "absent_from_schema"
        numeric_change = present and field in numeric and not pd.api.types.is_numeric_dtype(frame[field].dtype)
        rows.append({"field": field, "status": status, "current_dtype": str(frame[field].dtype) if present else "absent",
                     "legacy_aliases_found": ", ".join(aliases), "required_for_notebook": field in required,
                     "numeric_dtype_changed": numeric_change,
                     "blocks_current_notebook": (field in required and (not present or numeric_change)) or status == "ambiguous_aliases_review"})
    return pd.DataFrame(rows)


def missingness_audit(frame, contract):
    """Summarize every contract/current field by league and row grain."""
    league = frame["league"].astype("string").fillna("<missing league>")
    kind = row_types(frame)
    output = []
    for field in dict.fromkeys([*contract["columns"], *frame.columns]):
        states = classify_field(frame, field, contract)
        counts = pd.crosstab([league, kind], states, dropna=False).reindex(columns=STATES, fill_value=0)
        counts.index.names = ["league", "row_type"]
        counts["field"] = field
        counts["source_rows"] = counts[list(STATES)].sum(axis=1)
        counts["eligible_cells"] = counts[[*OBSERVED, "unexpected_missing", "absent_from_schema"]].sum(axis=1)
        denominator = counts["eligible_cells"].replace(0, np.nan)
        counts["available_pct"] = counts[list(OBSERVED)].sum(axis=1).div(denominator).mul(100)
        output.append(counts.reset_index())
    return pd.concat(output, ignore_index=True)


def reference_hosts(frame):
    """A reference-link hostname is evidence, not a verified feed-provider label."""
    if "url" not in frame:
        return pd.Series("<reference unavailable>", index=frame.index)
    def host(value):
        if pd.isna(value) or not str(value).strip():
            return "<reference unavailable>"
        try:
            return urlsplit(str(value)).hostname or "<unparseable reference>"
        except ValueError:
            return "<unparseable reference>"
    return frame["url"].map(host)


def source_coverage(frame, contract, fields):
    context = pd.DataFrame({"league": frame["league"], "row_type": row_types(frame),
                            "publisher_flag": frame["datacompleteness"].fillna("<missing flag>"),
                            "reference_host": reference_hosts(frame)})
    outputs = []
    keys = list(context.columns)
    for field in fields:
        states = classify_field(frame, field, contract)
        group = context.assign(state=states)
        table = group.groupby([*keys, "state"], observed=True, dropna=False).size().unstack("state", fill_value=0)
        table = table.reindex(columns=STATES, fill_value=0)
        table["field"] = field
        table["eligible_cells"] = table[[*OBSERVED, "unexpected_missing", "absent_from_schema"]].sum(axis=1)
        table["available_pct"] = table[list(OBSERVED)].sum(axis=1).div(table["eligible_cells"].replace(0, np.nan)).mul(100)
        table["upstream_provider"] = "unverified from this CSV"
        outputs.append(table.reset_index())
    return pd.concat(outputs, ignore_index=True)


def temporal_coverage(frame, contract, fields):
    """Expose month/patch coverage changes without inferring introduction dates."""
    date = pd.to_datetime(frame["date"], errors="coerce")
    context = pd.DataFrame({"league": frame["league"], "row_type": row_types(frame),
                            "month": date.dt.to_period("M").astype("string").fillna("<unknown month>"),
                            "patch": frame["patch"].astype("string").fillna("<unknown patch>")})
    outputs = []
    for field in fields:
        table = context.assign(state=classify_field(frame, field, contract))
        table = table.groupby([*context.columns, "state"], observed=True, dropna=False).size().unstack("state", fill_value=0)
        table = table.reindex(columns=STATES, fill_value=0)
        table["field"] = field
        outputs.append(table.reset_index())
    return pd.concat(outputs, ignore_index=True)


def bounded_missing_runs(frame, contract, fields, minimum_games=2):
    """Find missing runs bracketed by observed eligible games in a league.

    Adjacency means adjacent *recorded eligible games*, not uninterrupted wall
    time. The returned runs are investigation candidates, not confirmed outages.
    """
    columns = ["league", "field", "missing_games", "first_missing_date", "last_missing_date",
               "preceding_game", "following_game", "preceding_reference_host", "following_reference_host", "interpretation"]
    selected = frame.loc[frame["position"].eq("team") & frame["side"].eq("Blue")].copy()
    selected["audit_date"] = pd.to_datetime(selected["date"], errors="coerce")
    selected["reference_host"] = reference_hosts(selected)
    found = []
    for field in fields:
        selected["audit_state"] = classify_field(selected, field, contract)
        eligible = selected.loc[selected["audit_state"].isin([*OBSERVED, "unexpected_missing"]) & selected["audit_date"].notna()]
        for league, group in eligible.groupby("league"):
            group = group.sort_values(["audit_date", "gameid"]).reset_index(drop=True)
            missing = group["audit_state"].eq("unexpected_missing")
            runs = missing.ne(missing.shift()).cumsum()
            for _, positions in group.loc[missing].groupby(runs[missing]).groups.items():
                first, last = min(positions), max(positions)
                if len(positions) < minimum_games or first == 0 or last == len(group)-1:
                    continue
                before, after = group.iloc[first-1], group.iloc[last+1]
                found.append({"league": league, "field": field, "missing_games": len(positions),
                              "first_missing_date": group.iloc[first]["date"], "last_missing_date": group.iloc[last]["date"],
                              "preceding_game": before["gameid"], "following_game": after["gameid"],
                              "preceding_reference_host": before["reference_host"], "following_reference_host": after["reference_host"],
                              "interpretation": "bounded missing run; cause and upstream provider unverified"})
    return pd.DataFrame(found, columns=columns)


def checkpoint_endings(frame, minutes=(10, 15, 20, 25)):
    """Describe supplied checkpoint values for games ending before that time.

    Exact agreement with final totals is evidence of an end-capped convention,
    not proof of a timestamped observation at the requested checkpoint.
    """
    rows = []
    teams = frame.loc[frame["position"].eq("team") & frame["side"].eq("Blue")]
    for minute in minutes:
        field = f"goldat{minute}"
        if field not in teams:
            continue
        early = teams.loc[teams["gamelength"].gt(0) & teams["gamelength"].lt(minute*60)]
        for league, group in early.groupby("league"):
            supplied = group.loc[group[field].notna()]
            rows.append({"league": league, "minute": minute, "games_ending_before_checkpoint": len(group),
                         "early_games_with_value": len(supplied),
                         "supplied_value_equals_final_gold": int(supplied[field].eq(supplied["totalgold"]).sum())})
    return pd.DataFrame(rows, columns=["league", "minute", "games_ending_before_checkpoint", "early_games_with_value", "supplied_value_equals_final_gold"])


def entity_audit(frame):
    """Audit local roster joins separately from longitudinal identity coverage."""
    checks = []
    def record(check, affected, unit, severity="blocker"):
        checks.append({"check": check, "affected": int(affected), "unit": unit, "severity": severity})
    needed = {"gameid", "side", "participantid", "position", "teamid", "teamname", "playerid", "playername", "result"}
    record("Required entity columns absent", len(needed-set(frame)), "columns")
    if needed-set(frame):
        return pd.DataFrame(checks), pd.DataFrame(), pd.DataFrame()
    kind = row_types(frame)
    key_good = has_value(frame["gameid"]) & has_value(frame["side"])
    record("Rows with missing local join keys", (~key_good).sum(), "rows")
    record("Rows with missing participant slot", (~has_value(frame["participantid"])).sum(), "rows")
    record("Duplicate game/participant key records", frame.duplicated(["gameid", "participantid"], keep=False).sum(), "rows")
    record("Unknown role labels", kind.eq("unknown").sum(), "rows")
    record("Unknown side labels", (~frame["side"].isin(["Blue", "Red"])).sum(), "rows")
    for column in [c for c in ["year", "league", "date", "patch", "gamelength"] if c in frame]:
        record(f"Games with conflicting {column}", frame.groupby("gameid")[column].nunique(dropna=False).gt(1).sum(), "games")
    players = frame.loc[kind.eq("player")].copy()
    teams = frame.loc[kind.eq("team")].copy()
    duplicate_teams = teams.duplicated(["gameid", "side"], keep=False)
    record("Ambiguous team-summary join keys", duplicate_teams.sum(), "team rows")
    sizes = frame.groupby("gameid").size()
    record("Games without twelve source rows", sizes.ne(12).sum(), "games")
    team_counts = teams.groupby("gameid").size().reindex(sizes.index, fill_value=0)
    record("Games without two team summaries", team_counts.ne(2).sum(), "games")
    record("Team rows with invalid outcomes", (~teams["result"].isin([0, 1])).sum(), "team rows")
    winners = teams.groupby("gameid")["result"].sum(min_count=2).reindex(sizes.index)
    record("Games without exactly one recorded winner", (~winners.eq(1)).sum(), "games")
    if "gamelength" in frame:
        duration = pd.to_numeric(frame["gamelength"], errors="coerce")
        record("Rows without a positive finite duration", (~(duration.gt(0) & np.isfinite(duration))).sum(), "rows")
    roster = players.groupby(["gameid", "side", "position"]).size().unstack("position", fill_value=0).reindex(columns=ROLES, fill_value=0)
    expected_sides = pd.MultiIndex.from_product([sizes.index, ["Blue", "Red"]], names=["gameid", "side"])
    roster = roster.reindex(expected_sides, fill_value=0)
    record("Game sides without one player per role", (~roster.eq(1).all(axis=1)).sum(), "game sides")
    good_reference = teams.loc[key_good.loc[teams.index] & ~duplicate_teams,
                               ["gameid", "side", "teamid", "teamname", "result"]]
    good_reference = good_reference.rename(columns={c:f"summary_{c}" for c in ["teamid", "teamname", "result"]})
    align = players.loc[key_good.loc[players.index], ["gameid", "side", "participantid", "playerid", "playername", "teamid", "teamname", "result"]].merge(
        good_reference, on=["gameid", "side"], how="left", validate="many_to_one", indicator=True)
    align["team_reference_found"] = align["_merge"].eq("both")
    both_ids = has_value(align["teamid"]) & has_value(align["summary_teamid"])
    align["team_id_conflict"] = both_ids & align["teamid"].ne(align["summary_teamid"]).fillna(False)
    align["team_id_not_comparable"] = ~both_ids
    normalize = lambda s:s.astype("string").str.strip().str.casefold()
    both_names = has_value(align["teamname"]) & has_value(align["summary_teamname"])
    align["team_name_conflict"] = both_names & normalize(align["teamname"]).ne(normalize(align["summary_teamname"])).fillna(False)
    record("Players without an unambiguous team summary", (~align["team_reference_found"]).sum(), "player rows")
    record("Player/team persistent ID conflicts within game", align["team_id_conflict"].sum(), "player rows")
    record("Player/team name conflicts within game", align["team_name_conflict"].sum(), "player rows", "review")
    record("Player/team result conflicts within game", (align["team_reference_found"] & align["result"].ne(align["summary_result"]).fillna(False)).sum(), "player rows")
    record("Players missing persistent player ID", (~has_value(players["playerid"])).sum(), "player rows", "limitation")
    record("Team rows missing persistent team ID", (~has_value(teams["teamid"])).sum(), "team rows", "limitation")
    collisions = []
    for entity, part, id_col, name_col in [("player", players, "playerid", "playername"), ("team", teams, "teamid", "teamname")]:
        identities = part.loc[has_value(part[id_col]) & has_value(part[name_col]), [id_col, name_col]].drop_duplicates()
        for name, ids in identities.groupby(name_col)[id_col]:
            if ids.nunique() > 1:
                collisions.append({"entity": entity, "issue": "same_name_multiple_ids", "name_or_id": name,
                                   "distinct_values": ids.nunique(), "values": "; ".join(sorted(set(ids.astype(str))))})
        for identity, names in identities.groupby(id_col)[name_col]:
            if names.nunique() > 1:
                collisions.append({"entity": entity, "issue": "same_id_multiple_names", "name_or_id": identity,
                                   "distinct_values": names.nunique(), "values": "; ".join(sorted(set(names.astype(str))))})
    collisions = pd.DataFrame(collisions, columns=["entity", "issue", "name_or_id", "distinct_values", "values"])
    return pd.DataFrame(checks), align.drop(columns="_merge"), collisions


def eligible_rows(frame, fields, grain, contract):
    mask = row_types(frame).eq(grain)
    for field in fields:
        mask &= classify_field(frame, field, contract).isin(OBSERVED)
    return mask


def analysis_readiness(frame, contract):
    """Explicit complete-case populations for distinct analyses, not one score."""
    recipes = {
        "duration": ("team", ["gamelength", "result"]),
        "vision": ("team", ["wardsplaced", "wardskilled", "visionscore"]),
        "gold_at_15": ("team", ["goldat15", "golddiffat15"]),
        "player_resources": ("player", ["damageshare", "earnedgoldshare", "dpm", "cspm"]),
        "player_history_joins": ("player", ["playerid", "teamid"]),
    }
    outputs = []
    for analysis, (grain, fields) in recipes.items():
        population = row_types(frame).eq(grain)
        usable = eligible_rows(frame, fields, grain, contract)
        paired = pd.NA
        if grain == "team":
            counts = frame.loc[usable].groupby("gameid")["side"].nunique()
            paired = int(counts.eq(2).sum())
        outputs.append({"analysis": analysis, "grain": grain, "required_fields": ", ".join(fields),
                        "source_rows": int(population.sum()), "usable_rows": int(usable.sum()),
                        "excluded_rows": int((population & ~usable).sum()), "games_with_both_sides_usable": paired})
    return pd.DataFrame(outputs)
