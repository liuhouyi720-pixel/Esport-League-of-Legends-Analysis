"""Create and execute the explanatory Oracle's Elixir data-audit notebook."""

from __future__ import annotations

import json
import os
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
for variable, relative in {
    "TEMP": ".cache/tmp", "TMP": ".cache/tmp",
    "JUPYTER_RUNTIME_DIR": ".cache/jupyter/runtime",
    "JUPYTER_DATA_DIR": ".cache/jupyter/data",
    "IPYTHONDIR": ".cache/ipython", "MPLCONFIGDIR": ".cache/matplotlib",
}.items():
    location = ROOT / relative
    location.mkdir(parents=True, exist_ok=True)
    os.environ[variable] = str(location)

import nbformat
from nbclient import NotebookClient
from nbconvert import HTMLExporter
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager
from audit_notebook_cells import append_audit_cells


def main():
    cells = []

    def md(source):
        cells.append(nbformat.v4.new_markdown_cell(textwrap.dedent(source).strip()))

    def code(source):
        cells.append(nbformat.v4.new_code_cell(textwrap.dedent(source).strip()))

    md("""
    # Oracle's Elixir: schema, grain, completeness and basic EDA

    This executed notebook audits the newest yearly CSV available in the publisher's public folder at download time, then explores recorded professional League of Legends games. It does not train a model.

    **Audit first:** verify the schema contract, source-related coverage, missing-value types and entity alignment before gameplay summaries or figures. pandas reductions often skip nulls silently. Their main risk here is an unexplained change in denominator, not an inevitable execution error. No source values are replaced with zero.

    Read the sections in order: snapshot, four-part completeness audit, column inventory, row grain, arithmetic checks, recorded coverage, eligible checkpoints, and basic EDA.

    - Publisher: [Oracle's Elixir / Tim Sevenhuysen](https://oracleselixir.com/tools/downloads).
    - Files: [publisher's public Drive folder](https://drive.google.com/drive/folders/1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH).
    - Historical reference: [official legacy dictionary](https://lol.timsevenhuysen.com/matchdata/match-data-dictionary/). Its field names and units require version-specific review.
    - pandas references: [mean and null handling](https://pandas.pydata.org/docs/reference/api/pandas.Series.mean.html), [merge cardinality and null join keys](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.merge.html).

    **Run:** select the project `.venv` kernel and Restart & Run All. Both the project root and `notebooks/` are supported working directories. Change `FOCUS_LEAGUE` in the next cell. Downloading is separate: running EDA never refreshes the raw file. Raw values remain unchanged; tables and figures are written under `reports/`.
    """)

    md("""
    ## 1. Load the source snapshot

    Load pandas, NumPy and matplotlib, locate the project, and read the latest local CSV with its manifest. Explicit string dtypes preserve identifiers and patch versions such as `16.10`.

    Verify the file SHA-256 before analysis. The hash establishes snapshot identity, not complete competition coverage. Preserve the source datetime without assigning an unverified timezone. The reviewed contract explicitly records seconds as this export's duration unit; arithmetic checks below verify that convention against reported DPM.
    """)
    code(r'''
    from pathlib import Path
    import hashlib
    import importlib.metadata
    import json
    import re
    import sys
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    from IPython.display import display, Markdown, HTML
    %matplotlib inline

    FOCUS_LEAGUE = "LCK"
    root_candidates = [Path.cwd(), *Path.cwd().parents]
    ROOT = next((p for p in root_candidates if (p / "data" / "raw").is_dir()), None)
    if ROOT is None:
        raise FileNotFoundError("Run from the project root or notebooks/ after downloading the CSV.")
    candidates = sorted((ROOT / "data" / "raw").glob("20??_LoL_esports_match_data_from_OraclesElixir.csv"))
    if not candidates:
        raise FileNotFoundError("Run scripts/download_oracles_elixir.py first.")
    CSV_PATH = candidates[-1]
    manifest = json.loads(CSV_PATH.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256()
    with CSV_PATH.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    assert digest.hexdigest() == manifest["sha256"], "CSV differs from its source manifest."
    raw = pd.read_csv(
        CSV_PATH, low_memory=False,
        dtype={c: "string" for c in ["gameid", "patch", "participantid", "playerid", "teamid"]},
    )
    assert raw.shape == (manifest["rows"], manifest["columns"])
    df = raw.copy()
    df["parsed_date"] = pd.to_datetime(df["date"], errors="coerce")
    df["duration_minutes"] = df["gamelength"] / 60
    REPORT_DIR = ROOT / "reports" / f"oracles_elixir_{manifest['season']}"
    FIGURE_DIR = REPORT_DIR / "figures"
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.max_columns", 12)
    pd.set_option("display.max_rows", 20)
    pd.set_option("display.max_colwidth", 70)
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.dpi": 110, "savefig.dpi": 160})
    COLORS = {"Blue": "#3563B4", "Red": "#C96155", "accent": "#407C68"}

    def finish_figure(fig, filename):
        fig.savefig(FIGURE_DIR / filename, bbox_inches="tight", facecolor="white")
        plt.show()
        plt.close(fig)

    source_summary = pd.Series({
        "File": CSV_PATH.name,
        "Retrieved (UTC)": manifest["retrieved_at_utc"],
        "Source Last-Modified (HTTP)": manifest["source_last_modified_http"],
        "File size (MB, decimal)": round(manifest["bytes"] / 1_000_000, 2),
        "Rows": len(raw), "Columns": len(raw.columns),
        "First recorded game date": df["parsed_date"].min(),
        "Last recorded game date": df["parsed_date"].max(),
        "SHA-256": manifest["sha256"],
    }, name="Snapshot")
    display(source_summary.to_frame())
    display(Markdown(f"**Season {manifest['season']}** was the newest season in the publisher's folder at retrieval. "
                     "It is a changing season snapshot, not proof of complete calendar coverage."))
    ''')

    append_audit_cells(md, code)

    md("""
    ## 2. Inspect all columns and their observed availability

    Group fields into metadata, identity, draft, combat, objectives, economy, vision and checkpoints. For each field, record its dtype, example, unique-value count and availability separately on player and team rows. Export the full inventory to `schema_profile.csv`; expand the table below to inspect every column.

    A schema includes meaning, units, row applicability and observation time. These descriptions are analysis notes rather than an independently verified new official dictionary. Unverified derived formulas are explicitly marked. **`position` is a role label, not a map coordinate.** Use the preceding eligibility-aware audit for valid missingness denominators.
    """)
    code(r'''
    metadata_cols = {"gameid", "datacompleteness", "url", "league", "year", "split", "playoffs", "date", "game", "patch", "gamelength", "result"}
    identity_cols = {"participantid", "side", "position", "playername", "playerid", "teamname", "teamid"}
    economy_cols = {"totalgold", "earnedgold", "earned gpm", "earnedgoldshare", "goldspent", "gspd", "gpr",
                    "total cs", "minionkills", "monsterkills", "monsterkillsownjungle", "monsterkillsenemyjungle", "cspm"}
    vision_cols = {"wardsplaced", "wpm", "wardskilled", "wcpm", "controlwardsbought", "visionscore", "vspm"}
    combat_cols = {"kills", "deaths", "assists", "teamkills", "teamdeaths", "doublekills", "triplekills", "quadrakills", "pentakills",
                   "firstblood", "firstbloodkill", "firstbloodassist", "firstbloodvictim", "team kpm", "ckpm",
                   "damagetochampions", "dpm", "damageshare", "damagetakenperminute", "damagemitigatedperminute", "damagetotowers"}

    def family(column):
        if re.search(r"at(10|15|20|25)$", column): return "Checkpoint statistics"
        if column in metadata_cols: return "Match metadata / outcome"
        if column in identity_cols: return "Identity / role / side"
        if column in {"firstPick", "champion"} or re.fullmatch(r"(ban|pick)[1-5]", column): return "Draft"
        if column in economy_cols: return "Economy / farming"
        if column in vision_cols: return "Vision"
        if column in combat_cols: return "Combat"
        return "Objectives / structures"

    key_descriptions = {
        "gameid": "Game identifier; validate uniqueness in this export before joining other sources.",
        "participantid": "Participant slot; the observed player/team slots are checked below.",
        "position": "Role label, including team summary; NOT a spatial coordinate.",
        "patch": "Game patch version, preserved as text.",
        "date": "Source game datetime; source timezone is not established here.",
        "gamelength": "Game duration in seconds in this export; checked against DPM below.",
        "result": "Recorded result: 1 = win, 0 = loss.",
        "datacompleteness": "Publisher completeness flag; not a guarantee that every column is populated.",
        "playerid": "Player identity, when available; blank on team rows.",
        "teamid": "Team identity, when available; distinct from team display name.",
        "dpm": "Damage to champions per minute.",
        "damageshare": "Player share of team damage to champions.",
        "earnedgoldshare": "Player share of team earned gold; verify publisher definition of earned gold.",
        "earnedgold": "Publisher earned-gold metric; verify exclusions before interpreting.",
        "earned gpm": "Publisher earned gold per minute; verify exclusions before interpreting.",
        "gspd": "Publisher gold-spent percentage-difference metric; verify precise formula.",
        "gpr": "Publisher gold metric; precise formula not verified in this audit.",
        "total cs": "Aggregate creep score; confirm included unit types when comparing sources.",
        "firstPick": "Publisher first-pick indicator; coding and coverage need validation for draft analysis.",
    }

    def describe(column):
        if column in key_descriptions: return key_descriptions[column]
        checkpoint = re.fullmatch(r"(opp_)?(gold|xp|cs|kills|assists|deaths|golddiff|xpdiff|csdiff)at(10|15|20|25)", column)
        if checkpoint:
            opponent, metric, minute = checkpoint.groups()
            who = "Opponent " if opponent else ""
            return f"{who}{metric} at {minute}:00; difference metrics compare the corresponding opponent."
        if re.fullmatch(r"ban[1-5]", column): return "Team champion ban slot; validate patch/draft format before use."
        if re.fullmatch(r"pick[1-5]", column): return "Team champion pick slot; validate patch/draft format before use."
        if column.startswith("opp_"): return f"Opponent counterpart of {column[4:]}; use the matching row grain."
        if column.startswith("first"): return "Publisher first-event indicator; do not interpret as an event timestamp."
        if column in {"playername", "teamname", "champion", "league", "split", "side", "url"}: return "Source label or reference; see observed example and missingness."
        return f"Source {family(column).lower()} field; validate detailed semantics before advanced analysis."

    def unit(column):
        if column == "gamelength": return "seconds"
        if column in {"result", "playoffs"} or column.startswith("first"): return "indicator; inspect coding"
        if "share" in column: return "fraction"
        if re.search(r"gold(?:diff)?at\d+$", column) or column in {"totalgold", "earnedgold", "goldspent"}: return "gold"
        if re.search(r"xp(?:diff)?at\d+$", column): return "XP"
        if column in {"dpm", "damagetakenperminute", "damagemitigatedperminute", "earned gpm", "cspm", "wpm", "wcpm", "vspm", "team kpm", "ckpm"}: return "per minute"
        return "see description / source definition"

    player_mask = raw["position"].ne("team")
    team_mask = raw["position"].eq("team")
    schema = pd.DataFrame({
        "column": raw.columns,
        "family": [family(c) for c in raw.columns],
        "dtype": [str(raw[c].dtype) for c in raw.columns],
        "non_null_pct_all": raw.notna().mean().mul(100).values,
        "non_null_pct_players": raw.loc[player_mask].notna().mean().mul(100).values,
        "non_null_pct_teams": raw.loc[team_mask].notna().mean().mul(100).values,
        "unique_non_null": raw.nunique(dropna=True).values,
        "example": [str(raw[c].dropna().iloc[0])[:90] if raw[c].notna().any() else "<all missing>" for c in raw.columns],
        "unit": [unit(c) for c in raw.columns],
        "description": [describe(c) for c in raw.columns],
    })
    schema.to_csv(REPORT_DIR / "schema_profile.csv", index=False)
    display(schema.groupby("family", sort=False).agg(columns=("column", "size")))
    display(schema.loc[schema["column"].isin(["gameid", "participantid", "position", "patch", "gamelength", "result", "goldat15", "golddiffat15", "damageshare", "dragons"]),
                       ["column", "dtype", "unit", "description"]])
    display(HTML("<details><summary>Expand the full schema and availability table</summary>" +
                 schema.round(1).to_html(index=False, escape=True) + "</details>"))
    ''')

    md("""
    ## 3. Prove the row grain and inspect one complete game

    Use `groupby('gameid')` to verify ten player rows and two team summaries per game. Check participant-slot uniqueness and one player in each role on each side. The example displays all twelve rows belonging to one recorded game.

    Raw rows are not independent games. Summing player and team rows together double-counts totals. The `game` column is a series game number, not a unique match key.
    """)
    code(r'''
    players = df.loc[player_mask].copy()
    teams = df.loc[team_mask].copy()
    structure = df.groupby("gameid").agg(rows=("participantid", "size"), unique_slots=("participantid", "nunique"))
    structure["player_rows"] = players.groupby("gameid").size().reindex(structure.index, fill_value=0)
    structure["team_rows"] = teams.groupby("gameid").size().reindex(structure.index, fill_value=0)
    side_roles = players.groupby(["gameid", "side", "position"]).size().unstack(["side", "position"], fill_value=0)
    expected_roles = pd.MultiIndex.from_product([["Blue", "Red"], ["top", "jng", "mid", "bot", "sup"]])
    correct_roles = side_roles.reindex(columns=expected_roles, fill_value=0).eq(1).all(axis=1)
    side_summaries = teams.groupby(["gameid", "side"]).size().unstack(fill_value=0).reindex(columns=["Blue", "Red"], fill_value=0)
    structure["valid_grain"] = (
        structure["rows"].eq(12) & structure["unique_slots"].eq(12) &
        structure["player_rows"].eq(10) & structure["team_rows"].eq(2) &
        correct_roles.reindex(structure.index, fill_value=False) &
        side_summaries.eq(1).all(axis=1).reindex(structure.index, fill_value=False)
    )
    grain_summary = pd.DataFrame({"rows": [len(df), len(players), len(teams), len(structure)],
                                  "unit": ["mixed source rows", "player-game", "team-game", "unique game"]},
                                 index=["Raw export", "Player subset", "Team subset", "Game count"])
    display(grain_summary)
    display(structure.groupby(["rows", "player_rows", "team_rows", "valid_grain"]).size().rename("games").to_frame())
    display(teams.groupby(["side", "participantid"]).size().rename("team rows").to_frame())
    example_candidates = teams.loc[teams["goldat15"].notna() & teams["gameid"].isin(structure.index[structure["valid_grain"]]), "gameid"]
    example_id = example_candidates.iloc[0]
    display(Markdown(f"**Example game:** `{example_id}`. The final two rows summarize the same players; they are not extra competitors."))
    display(df.loc[df["gameid"].eq(example_id), ["participantid", "side", "position", "playername", "teamname", "champion", "result", "goldat15", "golddiffat15"]])
    structure.to_csv(REPORT_DIR / "game_structure.csv")
    ''')

    md("""
    ## 4. Check identifiers, outcomes and arithmetic consistency

    Compare metadata, player/team results, and player sums against team summaries using validated join keys. Keep diagnostics separate from raw data.

    Check seconds versus minutes by comparing `damage / (gamelength / 60)` with the publisher's DPM. Missing persistent IDs remain identity limitations; they do not automatically invalidate a game-duration observation. Joins and numeric prerequisites have already passed the audit above.
    """)
    code(r'''
    metadata_fields = ["league", "date", "gamelength", "patch"]
    metadata_conflicts = df.groupby("gameid")[metadata_fields].nunique(dropna=False).gt(1)
    result_sums = teams.groupby("gameid")["result"].sum(min_count=2)
    team_result_valid = teams.groupby("gameid")["result"].agg(lambda s: s.isin([0, 1]).all()) & result_sums.eq(1)
    player_results = players.merge(teams[["gameid", "side", "result"]].rename(columns={"result": "team_result"}),
                                    on=["gameid", "side"], how="left", validate="many_to_one")
    rollup = players.groupby(["gameid", "side"])[["kills", "deaths", "totalgold"]].sum(min_count=1)
    reconciliation = teams.set_index(["gameid", "side"])[["kills", "deaths", "totalgold"]].join(rollup, rsuffix="_players")
    unit_error = (teams["dpm"] - teams["damagetochampions"] / (teams["gamelength"] / 60)).abs()
    checks = pd.DataFrame([
        ("Rows with missing game/participant key", int(df[["gameid", "participantid"]].isna().any(axis=1).sum()), "rows"),
        ("Duplicate (gameid, participantid) records beyond first", int(df.duplicated(["gameid", "participantid"]).sum()), "rows"),
        ("Games with unexpected row grain", int((~structure["valid_grain"]).sum()), "games"),
        ("Games with conflicting metadata", int(metadata_conflicts.any(axis=1).sum()), "games"),
        ("Games without one win and one loss", int((~team_result_valid).sum()), "games"),
        ("Player results disagreeing with team result", int(player_results["result"].ne(player_results["team_result"]).sum()), "player rows"),
        ("Nonpositive or missing game duration", int((teams["gamelength"].isna() | teams["gamelength"].le(0)).sum()), "team rows"),
        ("Unparseable source dates", int(df["parsed_date"].isna().sum()), "rows"),
        ("Team kills differ from player sum", int((reconciliation["kills"] != reconciliation["kills_players"]).sum()), "team rows"),
        ("Team deaths differ from player sum", int((reconciliation["deaths"] != reconciliation["deaths_players"]).sum()), "team rows"),
        ("Team gold differs from player sum (tolerance 1 gold)", int((~np.isclose(reconciliation["totalgold"], reconciliation["totalgold_players"], atol=1, rtol=0)).sum()), "team rows"),
        ("DPM differs from damage / minutes (tolerance 0.001)", int((unit_error > 0.001).sum()), "team rows"),
    ], columns=["check", "affected", "unit"])
    display(checks)
    display(pd.Series({"DPM comparison rows": unit_error.notna().sum(), "maximum absolute DPM difference": unit_error.max(),
                       "duration min (seconds)": teams["gamelength"].min(), "duration max (seconds)": teams["gamelength"].max()}).to_frame("value"))
    checks.to_csv(REPORT_DIR / "quality_checks.csv", index=False)
    valid_ids = structure.index[structure["valid_grain"] & ~metadata_conflicts.any(axis=1) & team_result_valid]
    blue = teams.loc[teams["gameid"].isin(valid_ids) & teams["side"].eq("Blue")].copy()
    red = teams.loc[teams["gameid"].isin(valid_ids) & teams["side"].eq("Red")].copy()
    games = blue.merge(red[["gameid", "kills", "golddiffat15", "goldat15", "result"]].rename(columns={
        "kills": "red_kills", "golddiffat15": "red_golddiffat15", "goldat15": "red_goldat15", "result": "red_result"}),
        on="gameid", validate="one_to_one")
    games = games.loc[games["gamelength"].gt(0)].copy()
    games["month"] = games["parsed_date"].dt.to_period("M").astype(str)
    display(Markdown(f"**Game-level EDA:** {len(games):,} retained / {len(structure):,} recorded games. "
                     "No blanket completeness-flag filter or all-column dropna is applied."))
    ''')

    md("""
    ## 5. Describe recorded coverage

    Use the one-row-per-game table to count leagues and source-calendar months. Display all leagues in the table and the fifteen largest in the figure.

    Without an external schedule, a date gap may be a scheduled break or an omission. This measures recorded coverage, not the fraction of all real competition games captured. League comparisons also reflect tournament formats, participating teams and the snapshot's date range.
    """)
    code(r'''
    if not globals().get("AUDIT_COMPLETED", False):
        raise RuntimeError("Run the four-part completeness audit before gameplay summaries and figures.")
    coverage = games.groupby("league").agg(
        games=("gameid", "size"), teams=("teamname", "nunique"),
        first_recorded_date=("parsed_date", "min"), last_recorded_date=("parsed_date", "max"),
        blue_win_rate=("result", "mean"), source_complete_flag_pct=("datacompleteness", lambda s: s.eq("complete").mean()*100),
        gold15_present_pct=("golddiffat15", lambda s: s.notna().mean()*100),
    ).sort_values("games", ascending=False)
    coverage["teams"] = teams.groupby("league")["teamname"].nunique().reindex(coverage.index)
    coverage_display = coverage.copy()
    for column in coverage_display.select_dtypes(include="number").columns:
        coverage_display[column] = coverage_display[column].round(2)
    display(coverage_display)
    flag_counts = pd.crosstab(games["league"], games["datacompleteness"], dropna=False).reindex(coverage.index)
    display(flag_counts.loc[flag_counts.get("partial", pd.Series(0, index=flag_counts.index)).gt(0)])
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), layout="constrained")
    top = coverage.head(15).sort_values("games")
    axes[0].barh(top.index, top["games"], color=COLORS["Blue"])
    axes[0].set(title="Recorded games: 15 largest leagues", xlabel="Unique games")
    for y, value in enumerate(top["games"]): axes[0].text(value+4, y, str(value), va="center", fontsize=8)
    axes[0].set_xlim(0, top["games"].max()*1.13)
    monthly = games.groupby("month").size()
    axes[1].bar(monthly.index, monthly.values, color=COLORS["accent"])
    axes[1].tick_params(axis="x", rotation=45)
    axes[1].set(title="Recorded games by source calendar month", ylabel="Unique games", xlabel="Month (source datetime)")
    finish_figure(fig, "01_recorded_coverage.png")
    coverage.to_csv(REPORT_DIR / "league_coverage.csv")
    flag_counts.to_csv(REPORT_DIR / "completeness_flags_by_league.csv")
    ''')

    md("""
    ## 6. Separate row-type missingness from checkpoint availability

    Compare global availability with player-row and team-row availability. A missing team-row damage share is structurally different from a missing eligible checkpoint. The four-part audit above covers all contract fields; this focused view examines gold-difference checkpoints at 10, 15, 20 and 25 minutes.

    For checkpoint t, use games lasting at least `60*t` seconds as the denominator. Report games not reaching t, eligible games with missing data, and contradictory values recorded before t. Each heatmap cell has its own eligible denominator; the league label gives total recorded games. Late checkpoints select games that lasted long enough and therefore have survivor selection.
    """)
    code(r'''
    selected_missingness = schema.set_index("column").loc[
        ["playerid", "teamid", "champion", "damageshare", "earnedgoldshare", "dragons", "golddiffat15", "url"],
        ["non_null_pct_all", "non_null_pct_players", "non_null_pct_teams"]]
    display((100-selected_missingness).rename(columns=lambda c:c.replace("non_null", "missing")).round(2))
    minutes = [10, 15, 20, 25]
    checkpoint_rows = []
    for league, group in games.groupby("league"):
        for minute in minutes:
            field = f"golddiffat{minute}"
            eligible = group["gamelength"].ge(minute*60)
            available = group[field].notna()
            count_eligible = int(eligible.sum())
            checkpoint_rows.append({"league": league, "minute": minute, "recorded_games": len(group),
                "eligible_games": count_eligible, "not_reached": int((~eligible).sum()),
                "available_when_eligible": int((eligible & available).sum()),
                "missing_when_eligible": int((eligible & ~available).sum()),
                "present_before_reached": int((~eligible & available).sum()),
                "availability_pct": 100*(eligible & available).sum()/count_eligible if count_eligible else np.nan})
    checkpoint_audit = pd.DataFrame(checkpoint_rows)
    totals = checkpoint_audit.groupby("minute")[["recorded_games", "eligible_games", "not_reached", "available_when_eligible", "missing_when_eligible", "present_before_reached"]].sum()
    totals["availability_pct"] = totals["available_when_eligible"].div(totals["eligible_games"]).mul(100)
    display(totals.round(2))
    heat = checkpoint_audit.pivot(index="league", columns="minute", values="availability_pct").reindex(coverage.index)
    fig, ax = plt.subplots(figsize=(9, max(7, len(heat)*0.27)), layout="constrained")
    image = ax.imshow(heat.to_numpy(), vmin=0, vmax=100, cmap="YlGnBu", aspect="auto")
    ax.set_yticks(range(len(heat)), [f"{league} (n={coverage.loc[league, 'games']:,})" for league in heat.index], fontsize=8)
    ax.set_xticks(range(len(minutes)), [f"{minute}:00" for minute in minutes])
    ax.set(title="Gold-difference checkpoint availability among games reaching each minute", xlabel="Checkpoint")
    ax.grid(False)
    for row in range(len(heat)):
        for column in range(len(minutes)):
            value = heat.iloc[row, column]
            ax.text(column, row, f"{value:.0f}%" if pd.notna(value) else "n/a", ha="center", va="center", fontsize=7,
                    color="white" if pd.notna(value) and value>70 else "#202A35")
    fig.colorbar(image, ax=ax, label="Available / eligible games (%)", shrink=0.7)
    finish_figure(fig, "02_checkpoint_availability.png")
    checkpoint_audit.to_csv(REPORT_DIR / "checkpoint_availability.csv", index=False)
    display(Markdown("**Observed gaps at 15 minutes:**"))
    display(checkpoint_audit.loc[checkpoint_audit["minute"].eq(15) & checkpoint_audit["missing_when_eligible"].gt(0),
                                 ["league", "eligible_games", "missing_when_eligible", "availability_pct"]].round(2))
    flag_vs_checkpoint = games.loc[games["gamelength"].ge(15*60)].groupby("datacompleteness").agg(
        eligible_games=("gameid", "size"), missing_gold_diff_at15=("golddiffat15", lambda s: s.isna().sum()))
    flag_vs_checkpoint["available_pct"] = (1-flag_vs_checkpoint["missing_gold_diff_at15"]/flag_vs_checkpoint["eligible_games"])*100
    display(Markdown("**Publisher flag versus observed 15-minute checkpoint availability:**"))
    display(flag_vs_checkpoint.round(2))
    flag_vs_checkpoint.to_csv(REPORT_DIR / "source_flag_vs_checkpoint_availability.csv")
    ''')

    md("""
    ## 7. Define transparent EDA populations

    Keep broad league descriptions separate from detailed early-game EDA in `FOCUS_LEAGUE`, initially LCK. For gold-at-15, require both sides' checkpoint fields, duration at least fifteen minutes, and consistent difference arithmetic.

    Report the sample counts and exclusions. A partial publisher flag does not cause automatic exclusion: select records using the fields and grain that each analysis actually needs. A flag of complete does not guarantee every field is present.
    """)
    code(r'''
    if FOCUS_LEAGUE not in set(games["league"]):
        raise ValueError(f"Unknown FOCUS_LEAGUE={FOCUS_LEAGUE!r}; inspect the coverage table.")
    focus = games.loc[games["league"].eq(FOCUS_LEAGUE)].copy()
    fields15 = ["golddiffat15", "red_golddiffat15", "goldat15", "red_goldat15"]
    reached15 = focus["gamelength"].ge(15*60)
    fields_present15 = focus[fields15].notna().all(axis=1)
    symmetric15 = np.isclose(focus["golddiffat15"], -focus["red_golddiffat15"], atol=1, rtol=0)
    algebra15 = np.isclose(focus["golddiffat15"], focus["goldat15"]-focus["red_goldat15"], atol=1, rtol=0)
    usable15 = reached15 & fields_present15 & symmetric15 & algebra15
    focus15 = focus.loc[usable15].copy()
    if focus15.empty:
        raise ValueError(f"No usable paired 15-minute checkpoints for {FOCUS_LEAGUE}; choose another cohort for this analysis.")
    selection = pd.DataFrame([
        ("Recorded unique games", len(structure)), ("Valid games for basic EDA", len(games)),
        (f"{FOCUS_LEAGUE}: basic EDA", len(focus)),
        (f"{FOCUS_LEAGUE}: reached 15 minutes", int(reached15.sum())),
        (f"{FOCUS_LEAGUE}: reached 15, missing required checkpoint fields", int((reached15 & ~fields_present15).sum())),
        (f"{FOCUS_LEAGUE}: reached 15, present fields but inconsistent arithmetic", int((reached15 & fields_present15 & ~(symmetric15 & algebra15)).sum())),
        (f"{FOCUS_LEAGUE}: usable gold-at-15 sample", len(focus15)),
    ], columns=["population", "games"])
    display(selection)
    selection.to_csv(REPORT_DIR / "eda_sample_selection.csv", index=False)
    compare_leagues = coverage.loc[coverage["games"].ge(100)].head(8).index.tolist()
    ''')

    md("""
    ## 8. Game duration and combat pace

    The histogram describes focus-league duration. The boxplot compares the eight largest leagues with at least one hundred games. The table also reports combined kills per minute, rather than comparing only total kills from games of different lengths.

    Box edges are the 25th and 75th percentiles; the center line is the median, and outliers remain visible. Differences are descriptive and may also reflect patch, opposition, format and recorded coverage.
    """)
    code(r'''
    games["combined_kills_per_minute"] = (games["kills"]+games["red_kills"])/games["duration_minutes"]
    pace = games.groupby("league").agg(games=("gameid", "size"), median_minutes=("duration_minutes", "median"),
        p25_minutes=("duration_minutes", lambda s:s.quantile(.25)), p75_minutes=("duration_minutes", lambda s:s.quantile(.75)),
        median_combined_kpm=("combined_kills_per_minute", "median"))
    display(pace.reindex(compare_leagues).round(2))
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), layout="constrained")
    axes[0].hist(focus["duration_minutes"], bins=22, color=COLORS["Blue"], edgecolor="white")
    axes[0].axvline(focus["duration_minutes"].median(), color=COLORS["Red"], linestyle="--", label=f"Median {focus['duration_minutes'].median():.1f} min")
    axes[0].set(title=f"{FOCUS_LEAGUE}: game duration (n={len(focus):,})", xlabel="Minutes", ylabel="Games")
    axes[0].legend(frameon=False)
    values = [games.loc[games["league"].eq(league), "duration_minutes"].to_numpy() for league in compare_leagues]
    boxes = axes[1].boxplot(values, tick_labels=compare_leagues, patch_artist=True,
                            flierprops={"markersize":2, "alpha":.5})
    for box in boxes["boxes"]: box.set_facecolor("#D6E0EF")
    axes[1].set(title="Duration in eight largest eligible leagues", ylabel="Minutes", xlabel="League (at least 100 games)")
    finish_figure(fig, "03_game_duration.png")
    pace.to_csv(REPORT_DIR / "duration_and_pace_by_league.csv")
    ''')

    md("""
    ## 9. Blue-side win rates, with counts

    Keep one Blue row per game. The mean of its binary result is the Blue-side win rate. Show sample counts and 95% Wilson binomial reference intervals.

    The intervals do not model correlation within a series or team. Side choice can depend on strength and format, so these associations do not establish a causal side advantage.
    """)
    code(r'''
    def add_wilson(table, wins="wins", n="games"):
        table = table.copy()
        count = table[n].astype(float)
        p = table[wins]/count
        z = 1.959963984540054
        denominator = 1+z*z/count
        center = (p+z*z/(2*count))/denominator
        radius = z*np.sqrt(p*(1-p)/count+z*z/(4*count**2))/denominator
        table["win_rate"] = p
        table["lower95"] = center-radius
        table["upper95"] = center+radius
        return table

    side_rates = add_wilson(games.groupby("league").agg(games=("gameid", "size"), wins=("result", "sum")))
    shown = side_rates.reindex(compare_leagues).sort_values("win_rate")
    fig, ax = plt.subplots(figsize=(9, 4.8), layout="constrained")
    y = np.arange(len(shown))
    ax.errorbar(shown["win_rate"]*100, y, xerr=np.vstack([
        (shown["win_rate"]-shown["lower95"])*100, (shown["upper95"]-shown["win_rate"])*100]),
        fmt="o", capsize=4, color=COLORS["Blue"])
    ax.set_yticks(y, [f"{league} (n={int(row.games):,})" for league, row in shown.iterrows()])
    ax.axvline(50, color="#6B7280", linestyle="--", linewidth=1)
    ax.set(xlim=(30,75), xlabel="Blue-side wins (%)", title="Observed side win rates: one observation per game")
    finish_figure(fig, "04_blue_side_win_rates.png")
    display((shown[["games", "wins", "win_rate", "lower95", "upper95"]]).round(3))
    side_rates.to_csv(REPORT_DIR / "blue_side_win_rates.csv")
    ''')

    md("""
    ## 10. Gold advantage at 15 minutes and eventual wins

    Bin Blue-minus-Red gold difference at fifteen minutes and calculate the final Blue win rate in each bin. Bins are left-closed and right-open. Show n and reference intervals; an unobserved bin is not assigned a zero win rate.

    These are descriptive conditional win rates, not a calibrated prediction model or a causal gold effect. A future fifteen-minute model must exclude end-of-game totals from its input features.
    """)
    code(r'''
    gold_edges = [-np.inf, -3000, -1500, -500, 500, 1500, 3000, np.inf]
    gold_labels = ["< -3,000", "[-3,000, -1,500)", "[-1,500, -500)", "[-500, 500)", "[500, 1,500)", "[1,500, 3,000)", ">= 3,000"]
    focus15["gold_bin"] = pd.cut(focus15["golddiffat15"], gold_edges, labels=gold_labels, right=False)
    gold_curve = focus15.groupby("gold_bin", observed=True).agg(games=("gameid", "size"), wins=("result", "sum"),
                                                               median_gold_diff=("golddiffat15", "median"))
    gold_curve = add_wilson(gold_curve).reindex(gold_labels)
    display(gold_curve.round(3))
    fig, ax = plt.subplots(figsize=(12, 5.3), layout="constrained")
    available_bins = gold_curve["games"].notna()
    x = np.flatnonzero(available_bins)
    curve = gold_curve.loc[available_bins]
    ax.errorbar(x, curve["win_rate"]*100, yerr=np.vstack([
        (curve["win_rate"]-curve["lower95"])*100, (curve["upper95"]-curve["win_rate"])*100]),
        fmt="o-", capsize=4, color=COLORS["Blue"], markersize=6)
    for position, (_, row) in zip(x, curve.iterrows()):
        ax.annotate(f"n={int(row.games)}", (position, row.upper95*100), xytext=(0,6), textcoords="offset points", ha="center", fontsize=8)
    ax.set_xticks(range(len(gold_labels)), gold_labels, rotation=18, ha="right")
    ax.axhline(50, color="#6B7280", linestyle="--", linewidth=1)
    ax.set(ylim=(-3,108), ylabel="Blue-side eventual wins (%)", xlabel="Blue minus Red gold at 15:00",
           title=f"{FOCUS_LEAGUE}: early gold advantage and final result (n={len(focus15):,})")
    finish_figure(fig, "05_gold_at_15_and_wins.png")
    gold_curve.to_csv(REPORT_DIR / "gold_at_15_win_rates.csv")
    ''')

    md("""
    ## 11. Describe wins after an early deficit

    Select the trailing side at fifteen minutes from games that passed the paired-checkpoint checks. There is at most one trailing-team observation per game. Define an early-deficit recovery as a team trailing in gold at fifteen minutes but winning the game, then group by deficit size.

    Exclude exact ties at fifteen minutes. The available checkpoints cannot locate the turnaround fight or support a tactical-throw diagnosis.
    """)
    code(r'''
    trailing = teams.loc[teams["gameid"].isin(focus15["gameid"]) & teams["golddiffat15"].lt(0),
                         ["gameid", "side", "teamname", "golddiffat15", "result"]].copy()
    assert trailing["gameid"].is_unique
    trailing["deficit"] = -trailing["golddiffat15"]
    deficit_labels = ["(0, 500]", "(500, 1,500]", "(1,500, 3,000]", "> 3,000"]
    trailing["deficit_bin"] = pd.cut(trailing["deficit"], [0,500,1500,3000,np.inf], labels=deficit_labels)
    comebacks = trailing.groupby("deficit_bin", observed=True).agg(games=("gameid", "size"), wins=("result", "sum"))
    comebacks = add_wilson(comebacks)
    display(Markdown(f"**{len(trailing):,} games with one trailing team; {focus15['golddiffat15'].eq(0).sum():,} games tied at 15:00.**"))
    display(comebacks.round(3))
    comebacks.to_csv(REPORT_DIR / "wins_from_15_minute_deficits.csv")
    ''')

    md("""
    ## 12. Player resources and damage, separated by role

    Each point is one player-game. Compare earned-gold share with champion-damage share and color by role. Larger markers show the role medians; the legend reports player-game sample sizes.

    Roles and champions have different responsibilities. A damage-share-to-gold-share ratio is not an objective player-efficiency or responsibility ranking. The five teammates are not treated as independent observations for an inferential test.
    """)
    code(r'''
    resource_fields = ["earnedgoldshare", "damageshare"]
    resource_source = players.loc[players["gameid"].isin(focus["gameid"])].copy()
    resources = resource_source.dropna(subset=resource_fields).copy()
    role_order = ["top", "jng", "mid", "bot", "sup"]
    role_summary = resources.groupby("position").agg(player_games=("gameid", "size"),
        median_earnedgoldshare=("earnedgoldshare", "median"), median_damageshare=("damageshare", "median"),
        median_dpm=("dpm", "median"), median_cspm=("cspm", "median")).reindex(role_order)
    display(role_summary.round(3))
    fig, ax = plt.subplots(figsize=(10,6), layout="constrained")
    role_colors = dict(zip(role_order, ["#3466AD", "#5A8C67", "#AE7950", "#9B67A8", "#6E8595"]))
    for role in role_order:
        subset = resources.loc[resources["position"].eq(role)]
        ax.scatter(subset["earnedgoldshare"]*100, subset["damageshare"]*100, s=11, alpha=.13, color=role_colors[role], rasterized=True)
        if len(subset):
            ax.scatter(subset["earnedgoldshare"].median()*100, subset["damageshare"].median()*100,
                       s=100, marker="D", color=role_colors[role], edgecolor="white", linewidth=1,
                       label=f"{role}: median (n={len(subset):,})", zorder=4)
    ax.set(xlabel="Player share of team earned gold (%)", ylabel="Player share of team champion damage (%)",
           title=f"{FOCUS_LEAGUE}: resources and damage by role ({len(resources):,} player-games)")
    ax.legend(frameon=True, fontsize=9, loc="upper left")
    finish_figure(fig, "06_player_resources_and_damage.png")
    role_summary.to_csv(REPORT_DIR / "role_resource_profiles.csv")
    ''')

    md("""
    ## 13. Findings and dataset suitability

    Calculate the findings from the current snapshot so a refreshed file updates the reported results after re-execution. The audit distinguishes join blockers, missingness, conditional applicability and identity limitations rather than granting a single overall completeness label.

    This export supports game/player-game summaries and selected checkpoints. Continuous positions, action sequences and tactical responsibility require additional evidence. Internal consistency checks cannot reveal games absent from the entire file or verify an unrecorded upstream provider.
    """)
    code(r'''
    checkpoint15_total = totals.loc[15]
    missing15 = checkpoint_audit.loc[checkpoint_audit["minute"].eq(15) & checkpoint_audit["missing_when_eligible"].gt(0)].sort_values("missing_when_eligible", ascending=False)
    gap_text = ", ".join(f"{row.league}: {int(row.missing_when_eligible)}" for row in missing15.itertuples()) or "none"
    summary = {
        "season": manifest["season"], "source_filename": CSV_PATH.name, "sha256": manifest["sha256"],
        "source_retrieved_at_utc": manifest["retrieved_at_utc"],
        "raw_rows": len(raw), "columns": len(raw.columns), "recorded_games": len(structure),
        "valid_games_for_eda": len(games), "leagues": int(games["league"].nunique()),
        "first_recorded_date": str(games["parsed_date"].min()), "last_recorded_date": str(games["parsed_date"].max()),
        "all_games_have_expected_grain": bool(structure["valid_grain"].all()),
        "nonzero_quality_diagnostics": checks.loc[checks["affected"].gt(0)].to_dict(orient="records"),
        "eligible_games_at15": int(checkpoint15_total["eligible_games"]),
        "available_gold_diff_at15": int(checkpoint15_total["available_when_eligible"]),
        "missing_gold_diff_at15_when_eligible": int(checkpoint15_total["missing_when_eligible"]),
        "player_rows_missing_playerid": int(players["playerid"].isna().sum()),
        "team_rows_missing_teamid": int(teams["teamid"].isna().sum()),
        "focus_league": FOCUS_LEAGUE, "focus_games": len(focus), "focus_games_at15": len(focus15),
        "focus_blue_win_rate": float(focus["result"].mean()),
        "focus_median_duration_minutes": float(focus["duration_minutes"].median()),
        "focus_games_won_from_deficit_at15": int(trailing["result"].sum()),
        "focus_games_with_deficit_at15": len(trailing),
        "schema_contract_fields": len(contract["columns"]),
        "historical_schema_comparison": historical_status["status"],
        "entity_alignment_blockers": int(entity_blockers["affected"].sum()),
        "entity_name_id_collisions": len(name_collisions),
        "bounded_missing_run_candidates": len(collection_candidates),
        "early_ending_games_with_gold_at25": int(early_checkpoint_values.loc[early_checkpoint_values["minute"].eq(25), "early_games_with_value"].sum()),
        "early_gold_at25_equals_final_gold": int(early_checkpoint_values.loc[early_checkpoint_values["minute"].eq(25), "supplied_value_equals_final_gold"].sum()),
        "upstream_provenance": "unverified per row; publisher and download snapshot are known",
    }
    findings = f"""# Oracle's Elixir {manifest['season']}: observed findings

    - Snapshot: **{len(structure):,} recorded games**, **{len(raw):,} rows**, **{len(raw.columns)} columns**, **{games['league'].nunique()} league labels**.
    - Recorded date range: **{games['parsed_date'].min()} to {games['parsed_date'].max()}** (source timezone unverified).
    - Row grain: **{int(structure['valid_grain'].sum()):,}/{len(structure):,} games** have ten player rows and two team summaries with the expected roles and sides.
    - Time resolution: gold/XP/CS and combat checkpoints at **10, 15, 20, 25 minutes**; this file does not supply continuous positions or a fight event sequence.
    - Gold difference at 15: **{int(checkpoint15_total['available_when_eligible']):,}/{int(checkpoint15_total['eligible_games']):,} eligible games** have a value (**{checkpoint15_total['availability_pct']:.2f}%**). Missing eligible games by league: **{gap_text}**.
    - Identity gaps: **{players['playerid'].isna().sum():,} player rows** lack player IDs; **{teams['teamid'].isna().sum():,} team rows** lack team IDs. These matter for history joins even when game totals are usable.
    - {FOCUS_LEAGUE} EDA: **{len(focus):,} games**, median duration **{focus['duration_minutes'].median():.2f} minutes**, observed Blue win rate **{focus['result'].mean():.1%}**.
    - Under the explicit early-deficit definition, **{int(trailing['result'].sum()):,}/{len(trailing):,}** {FOCUS_LEAGUE} games with a trailing team at 15:00 were eventually won by that team.

    ## What this snapshot supports

    Recorded-coverage auditing, row-type-aware missingness, role and resource profiles, game-duration and pace comparisons, and descriptive early-lead conversion.

    ## What remains unresolved

    External schedule coverage has not been checked. A publisher `complete` flag does not promise all columns are populated.
    Late checkpoints select games that lasted that long. Side, gold and resource associations do not establish causal effects.
    Individual tactical responsibility needs richer action and spatial evidence.

    ## Four-part completeness audit

    The pinned contract covers **{len(contract['columns'])} fields**. Schema checks distinguish absent columns, numeric type changes and legacy aliases requiring review.
    Historical header comparison was unavailable because of the public Google Drive download quota. No cross-year equivalence is asserted.
    Source coverage is reported by league, row grain, publisher flag and reference-link hostname. The CSV does not verify a provider for every row.
    Missing cells are separated into explicit structural exclusions, unknown eligibility, absent columns and unexpected gaps. Observed zeros remain unchanged.
    **{summary['early_ending_games_with_gold_at25']} games** ended before 25:00 but have supplied gold-at-25 values;
    **{summary['early_gold_at25_equals_final_gold']} of those values equal final gold totals**. They are excluded from true 25-minute checkpoint analyses.
    The entity audit found **{int(entity_blockers['affected'].sum())} blocking alignment issues**, **{len(name_collisions)} name/ID collision review items**,
    and **{len(collection_candidates)} field-specific bounded missing-run candidates**. These runs are not confirmed outages.
    Full details and analysis-specific sample sizes are in the audit tables exported alongside this report.

    ## Source and reproducibility

    Publisher: [Oracle's Elixir / Tim Sevenhuysen]({manifest['downloads_page']}).
    [Exact source file]({manifest['file_url']}). Retrieved UTC: {manifest['retrieved_at_utc']}.
    SHA-256: `{manifest['sha256']}`.
    """
    findings = "\n".join(line[4:] if line.startswith("    ") else line for line in findings.splitlines())
    display(Markdown(findings))
    (REPORT_DIR / "findings.md").write_text(findings+"\n", encoding="utf-8")
    (REPORT_DIR / "summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    versions = {package:importlib.metadata.version(package) for package in ["pandas", "numpy", "matplotlib", "nbformat", "nbclient", "ipykernel"]}
    environment = {"python": sys.version, "packages": versions}
    (REPORT_DIR / "environment.json").write_text(json.dumps(environment, indent=2)+"\n", encoding="utf-8")
    display(pd.Series(versions, name="Executed with").to_frame())
    ''')

    previous_path = ROOT / "notebooks" / "01_oracles_elixir_schema_and_eda.ipynb"
    if previous_path.exists():
        previous = nbformat.read(previous_path, as_version=4)
        identifiers = {(cell.cell_type, cell.source.splitlines()[0]): cell.id for cell in previous.cells if cell.source}
        for cell in cells:
            key = (cell.cell_type, cell.source.splitlines()[0])
            if cell.cell_type == "code" and "coverage = games.groupby(\"league\").agg(" in cell.source:
                key = ("code", "coverage = games.groupby(\"league\").agg(")
            if key in identifiers:
                cell.id = identifiers[key]
    notebook = nbformat.v4.new_notebook(cells=cells)
    notebook.metadata.update({
        "kernelspec": {"display_name": "Python (Oracle's Elixir EDA)", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"},
    })
    output = ROOT / "notebooks" / "01_oracles_elixir_schema_and_eda.ipynb"
    output.parent.mkdir(parents=True, exist_ok=True)
    nbformat.validate(notebook)
    nbformat.write(notebook, output)
    print(f"Created {len(cells)} cells; executing {sum(c.cell_type == 'code' for c in cells)} code cells...", flush=True)

    kernel_root = ROOT / ".cache" / "kernels"
    kernel_dir = kernel_root / "oe-eda"
    kernel_dir.mkdir(parents=True, exist_ok=True)
    import sys
    (kernel_dir / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "Oracle's Elixir project environment", "language": "python",
    }), encoding="utf-8")
    manager = KernelManager(kernel_name="oe-eda", kernel_spec_manager=KernelSpecManager(kernel_dirs=[str(kernel_root)]))
    client = NotebookClient(notebook, km=manager, timeout=180, startup_timeout=45,
                            resources={"metadata": {"path": str(ROOT)}}, allow_errors=False,
                            on_cell_executed=lambda cell_index, **kwargs: print(f"Executed cell {cell_index+1}/{len(cells)}", flush=True))
    try:
        client.execute()
    finally:
        nbformat.write(notebook, output)
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        if client.kc is not None:
            client.kc.stop_channels()
    nbformat.validate(notebook)
    errors = [o for c in notebook.cells if c.cell_type == "code" for o in c.get("outputs", []) if o.output_type == "error"]
    if errors:
        raise RuntimeError(f"Notebook contains {len(errors)} errors.")
    executed = [c for c in notebook.cells if c.cell_type == "code"]
    assert all(c.execution_count is not None for c in executed)
    season = json.loads(sorted((ROOT / "data" / "raw").glob("*.manifest.json"))[-1].read_text())["season"]
    report_dir = ROOT / "reports" / f"oracles_elixir_{season}"
    html, _ = HTMLExporter(template_name="lab", embed_images=True).from_notebook_node(notebook)
    html_path = report_dir / "notebook.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"Executed {len(executed)} code cells with no errors. Notebook: {output}", flush=True)
    print(f"HTML report: {html_path}", flush=True)


if __name__ == "__main__":
    main()
