"""Audit-first teaching cells inserted before schema exploration and EDA."""


def append_audit_cells(md, code):
    md("""
    ## 1A. Audit schema versions before using any features

    Compare the actual columns with a **pinned, reviewed contract**, not with a contract regenerated from the current input.
    The contract records expected column names, numeric types, row domains, duration units, and notebook dependencies.
    A missing column differs from a present column containing missing values. Extra columns and ambiguous legacy aliases
    require review. Aliases are reported without automatically renaming or combining source columns.

    **Version limits:** identical names do not prove identical units or formulas. The official legacy dictionary used
    different names and duration conventions. Historical header retrieval encountered Google Drive's download quota;
    cross-year equivalence is therefore **unverified**. The comparison below detects changes from the pinned current snapshot.
    Preserve patches as version strings. Never infer a field's introduction/removal solely from its first/last non-null value.
    """)
    code(r'''
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from analysis.completeness import (
        STATES, OBSERVED, schema_audit, missingness_audit, source_coverage,
        temporal_coverage, bounded_missing_runs, entity_audit, analysis_readiness,
        checkpoint_endings, eligible_rows, has_value,
    )
    CONTRACT_PATH = ROOT / "data" / "contracts" / f"oracles_elixir_{manifest['season']}.json"
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    version_audit = schema_audit(raw, contract)
    version_audit.to_csv(REPORT_DIR / "schema_version_audit.csv", index=False)
    display(version_audit.groupby("status").size().rename("fields").to_frame())
    display(version_audit.loc[version_audit["status"].ne("present") | version_audit["numeric_dtype_changed"]])
    historical_status = json.loads((ROOT / "data" / "schema_snapshots" / "historical_comparison_status.json").read_text())
    display(pd.Series(historical_status, name="Historical comparison status").to_frame())
    (REPORT_DIR / "schema_version_notes.json").write_text(json.dumps(historical_status, indent=2)+"\n", encoding="utf-8")
    schema_blockers = version_audit.loc[version_audit["blocks_current_notebook"]]
    if not schema_blockers.empty:
        raise ValueError("Schema review required before this notebook can continue: " + ", ".join(schema_blockers["field"]))
    ''')

    md("""
    ## 1B. Audit source-related coverage without inventing provenance

    The download publisher is known from the manifest. This CSV does **not** provide a verified upstream-provider label
    for every row. A match-reference URL hostname is recorded as evidence, not relabeled as the provider.
    Missing reference links also do not establish that gameplay data are missing.

    `groupby` compares field availability by league, player/team grain, publisher completeness flag, and reference hostname.
    A separate month/patch table exposes changing coverage. These comparisons diagnose source-related patterns;
    they do not establish that an entire league is always unsupported or that a particular provider failed.
    All rates use explicit eligible-cell counts. Observed zeros remain observed values.
    """)
    code(r'''
    audited_source_fields = ["wardsplaced", "wardskilled", "visionscore", "golddiffat15", "goldat20", "goldat25", "atakhans"]
    source_profile = source_coverage(raw, contract, audited_source_fields)
    source_profile.to_csv(REPORT_DIR / "source_coverage_by_cohort.csv", index=False)
    reference_summary = source_profile.loc[source_profile["field"].eq("visionscore"),
        ["league", "row_type", "publisher_flag", "reference_host", "upstream_provider", "eligible_cells", "available_pct"]]
    display(reference_summary.loc[reference_summary["row_type"].eq("team")].head(18).round(2))
    temporal_profile = temporal_coverage(raw, contract, ["golddiffat15", "goldat20", "goldat25", "visionscore", "atakhans"])
    temporal_profile.to_csv(REPORT_DIR / "field_availability_by_month_patch.csv", index=False)
    display(Markdown("**Month/patch cohorts with unexpected checkpoint gaps (team rows):**"))
    display(temporal_profile.loc[temporal_profile["row_type"].eq("team") & temporal_profile["unexpected_missing"].gt(0),
        ["league", "month", "patch", "field", "observed", "observed_zero", "unexpected_missing", "structural_checkpoint_not_reached"]].head(20))
    ''')

    md("""
    ## 1C. Classify missing cells before choosing denominators

    | State | Evidence and treatment |
    |---|---|
    | Observed / observed zero | Keep the source value. Zero is not a missing-value substitute. |
    | Structural: not applicable | An explicit reviewed row-domain rule excludes the field, such as a player ID on a team summary. |
    | Structural: checkpoint not reached | Verified duration in seconds is shorter than the checkpoint. Exclude from that checkpoint's denominator. |
    | Absent from schema | The column is absent in this file, rather than blank in one record. |
    | Eligibility unknown | Duration, row type, patch availability, or objective-event context cannot establish applicability. |
    | Unexpected missing | A field is applicable and eligible, but its value is absent. The collection cause remains unverified. |
    | Supplied value, checkpoint not reached | The game ended before the checkpoint. The supplied value may be an end-capped total or a placeholder; it is not eligible for a true checkpoint analysis. |
    | Invalid or out-of-domain value | Invalid numeric data or a value outside its reviewed row domain requires review. |

    The audit summarizes **cells**, with player-game and team-game denominators kept separate. It does not infer
    structural non-applicability merely because a whole cohort is blank. Patch-dependent objectives and legacy fields
    retain unknown eligibility when no verified context rule is available. An objective not taken is not automatically
    the same as an uncollected objective statistic.

    Some early-ending games still have populated checkpoint fields. Compare those values with final totals and report
    the agreement explicitly. An end-capped value is usable as a final total but cannot represent a state at a timestamp
    the game never reached. Preserve it in the raw data and exclude it only from the affected checkpoint analysis.

    Bounded missing runs are missing observations bracketed by observed eligible games in the same league.
    They are **candidates to investigate**, not confirmed collection interruptions. Adjacency refers to recorded eligible games,
    and multiple fields can describe the same run. Missing schedules/games cannot be discovered from the CSV alone.
    """)
    code(r'''
    missingness = missingness_audit(raw, contract)
    missingness.to_csv(REPORT_DIR / "missingness_by_league_field_grain.csv", index=False)
    state_summary = missingness.groupby(["field", "row_type"])[list(STATES)].sum()
    state_summary.to_csv(REPORT_DIR / "missingness_state_summary.csv")
    display(state_summary.loc[state_summary.index.get_level_values("field").isin(
        ["playerid", "damageshare", "wardsplaced", "goldat20", "goldat25", "golddiffat15", "atakhans"])])
    display(Markdown("**Missing-state totals count cells, not games:**"))
    display(missingness[list(STATES)].sum().rename("cells").to_frame())
    early_checkpoint_values = checkpoint_endings(raw)
    early_checkpoint_values.to_csv(REPORT_DIR / "checkpoint_values_in_early_ending_games.csv", index=False)
    display(Markdown("**Values supplied for games ending before the checkpoint (one Blue row per game):**"))
    display(early_checkpoint_values.groupby("minute")[["games_ending_before_checkpoint", "early_games_with_value", "supplied_value_equals_final_gold"]].sum())
    collection_candidates = bounded_missing_runs(raw, contract, ["golddiffat15", "goldat20", "goldat25", "visionscore"])
    collection_candidates.to_csv(REPORT_DIR / "bounded_missing_run_candidates.csv", index=False)
    display(collection_candidates)
    ''')

    md("""
    ## 1D. Align entities, then define analysis-specific populations

    A local player record is identified by game and participant slot. Attach its team summary through
    **`(gameid, side)`**, after verifying non-null keys and a unique team row per key. `merge(validate='many_to_one')`
    prevents accidental Cartesian multiplication. Rows with null join keys are not joined; pandas can otherwise match
    null keys to one another. Audit roster roles, within-game team IDs, labels, and results independently.

    Persistent player/team IDs answer a different question: whether records can be joined longitudinally.
    Missing IDs limit history joins but need not invalidate a game-duration observation. Shared names, renamed teams,
    and transfers do not justify filling an ID from a name or merging two entities. Name/ID collisions remain review items.

    The final table shows separate populations for duration, vision, early gold, player resources, and history joins.
    There is no global `dropna()` or zero imputation. Structural/key failures stop the notebook before gameplay means
    or figures. Passing the audit enables only the stated analyses; upstream provenance and causal attribution remain unresolved.
    """)
    code(r'''
    entity_checks, player_team_alignment, name_collisions = entity_audit(raw)
    entity_checks.to_csv(REPORT_DIR / "entity_alignment_checks.csv", index=False)
    name_collisions.to_csv(REPORT_DIR / "entity_name_id_collisions.csv", index=False)
    display(entity_checks)
    display(name_collisions)
    alignment_issues = player_team_alignment.loc[
        ~player_team_alignment["team_reference_found"] | player_team_alignment["team_id_conflict"] |
        player_team_alignment["team_name_conflict"] | player_team_alignment["team_id_not_comparable"] |
        ~has_value(player_team_alignment["playerid"])]
    alignment_issues.to_csv(REPORT_DIR / "player_team_alignment_review_rows.csv", index=False)
    readiness = analysis_readiness(raw, contract)
    readiness.to_csv(REPORT_DIR / "analysis_readiness.csv", index=False)
    display(readiness)
    entity_blockers = entity_checks.loc[entity_checks["severity"].eq("blocker") & entity_checks["affected"].gt(0)]
    invalid_required = missingness.loc[missingness["field"].isin(contract["required_for_notebook"]) & missingness["invalid_value"].gt(0)]
    if not entity_blockers.empty or not invalid_required.empty:
        raise ValueError("Resolve blocking entity or numeric-value issues before proceeding to the EDA sections; inspect audit CSVs.")
    AUDIT_COMPLETED = True
    display(Markdown("**Audit completed.** Subsequent analyses use explicit field and grain requirements. "
                     "Review limitations separately from blocking key/value errors."))
    ''')
