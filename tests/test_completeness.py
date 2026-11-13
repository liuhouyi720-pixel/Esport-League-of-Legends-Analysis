"""Regression cases for missingness semantics and safe entity joins."""

import unittest

import numpy as np
import pandas as pd

from analysis.completeness import (
    STATES, analysis_readiness, bounded_missing_runs, classify_field,
    checkpoint_endings, eligible_rows, entity_audit, missingness_audit, schema_audit,
)


def game(gameid="g1", date="2026-01-01 12:00:00"):
    records = []
    for side, start in [("Blue", 1), ("Red", 6)]:
        for offset, role in enumerate(["top", "jng", "mid", "bot", "sup", "team"]):
            team = role == "team"
            records.append({"gameid": gameid, "year": 2026, "date": date, "league": "Test", "patch": "16.10",
                "side": side, "position": role,
                "participantid": str((100 if side == "Blue" else 200) if team else start+offset),
                "teamid": f"id-{side}", "teamname": f"Team {side}",
                "playerid": None if team else f"player-{side}-{role}",
                "playername": None if team else f"Name {side} {role}",
                "result": int(side == "Blue"), "gamelength": 1800,
                "datacompleteness": "complete", "url": None,
                "goldat15": 20000 if team else 4000,
                "golddiffat15": 100 if side == "Blue" else -100,
                "goldat20": 25000 if team else 5000,
                "wardsplaced": 50 if team else 10, "wardskilled": 10 if team else 2, "visionscore": 100 if team else 20,
                "damageshare": None if team else .2, "earnedgoldshare": None if team else .2,
                "dpm": 500, "cspm": 5, "barons": 0})
    return pd.DataFrame(records)


def contract(frame):
    return {"columns": list(frame), "duration_unit": "seconds", "required_for_notebook": ["gameid", "gamelength"],
            "numeric_columns": ["gamelength", "goldat15", "goldat20", "golddiffat15", "barons"],
            "domains": {"playerid": "player", "damageshare": "player", "earnedgoldshare": "player"},
            "context_dependent_fields": ["atakhans"], "legacy_aliases": {"golddiffat15": ["gdat15"]}}


class CompletenessTests(unittest.TestCase):
    def test_structural_checkpoint_gap_unexpected_gap_and_unknown_duration(self):
        data = game().iloc[:4].copy()
        data["gamelength"] = [1100, 1500, np.nan, 1200]
        data["goldat20"] = [np.nan, np.nan, np.nan, 0]
        self.assertEqual(classify_field(data, "goldat20", contract(data)).tolist(), [
            "structural_checkpoint_not_reached", "unexpected_missing", "eligibility_unknown", "observed_zero"])

    def test_absent_column_differs_from_present_but_blank(self):
        data = game()
        spec = contract(data)
        absent = data.drop(columns="goldat20")
        self.assertTrue(classify_field(absent, "goldat20", spec).eq("absent_from_schema").all())
        data["goldat20"] = np.nan
        self.assertTrue(classify_field(data, "goldat20", spec).eq("unexpected_missing").all())

    def test_row_domain_is_explicit_and_unknown_context_is_not_an_outage(self):
        data = game()
        data["atakhans"] = np.nan
        states = classify_field(data, "playerid", contract(data))
        self.assertEqual(states[data.position.eq("team")].tolist(), ["structural_not_applicable"]*2)
        self.assertTrue(classify_field(data, "atakhans", contract(data)).eq("eligibility_unknown").all())
        unknown_contract = contract(data) | {"domains": {}}
        self.assertTrue(classify_field(data, "playerid", unknown_contract)[data.position.eq("team")].eq("unexpected_missing").all())

    def test_zero_and_negative_difference_are_preserved_and_nonfinite_is_invalid(self):
        data = game()
        original = data.copy(deep=True)
        self.assertTrue(classify_field(data, "barons", contract(data)).eq("observed_zero").all())
        self.assertTrue(classify_field(data, "golddiffat15", contract(data)).eq("observed").all())
        pd.testing.assert_frame_equal(data, original)
        data["goldat20"] = data["goldat20"].astype(float)
        data.loc[0, "goldat20"] = float("inf")
        self.assertEqual(classify_field(data, "goldat20", contract(data)).iloc[0], "invalid_value")

    def test_supplied_value_in_an_early_ending_game_is_not_checkpoint_eligible(self):
        data = game()
        data.loc[0, "gamelength"] = 600
        self.assertEqual(classify_field(data, "goldat20", contract(data)).iloc[0], "observed_checkpoint_not_reached")
        self.assertFalse(eligible_rows(data, ["goldat20"], "player", contract(data)).iloc[0])
        data["totalgold"] = data["goldat20"]
        data["gamelength"] = 1100
        report = checkpoint_endings(data, (20,))
        self.assertEqual(report["early_games_with_value"].sum(), 1)
        self.assertEqual(report["supplied_value_equals_final_gold"].sum(), 1)

    def test_aliases_are_reviewed_without_overwriting_source_values(self):
        data = game()
        spec = contract(data)
        data["gdat15"] = 999
        audit = schema_audit(data, spec).set_index("field")
        self.assertEqual(audit.loc["golddiffat15", "status"], "ambiguous_aliases_review")
        self.assertTrue(audit.loc["golddiffat15", "blocks_current_notebook"])
        self.assertEqual(data.loc[0, "golddiffat15"], 100)
        legacy = data.drop(columns="golddiffat15")
        self.assertEqual(schema_audit(legacy, spec).set_index("field").loc["golddiffat15", "status"], "legacy_alias_requires_review")

    def test_null_join_keys_are_not_matched(self):
        data = game()
        data.loc[data.side.eq("Blue"), "gameid"] = None
        checks, aligned, _ = entity_audit(data)
        self.assertEqual(len(aligned), 5)
        self.assertTrue(aligned.gameid.notna().all())
        self.assertEqual(checks.set_index("check").loc["Rows with missing local join keys", "affected"], 6)

    def test_duplicate_team_rows_do_not_multiply_players(self):
        data = game()
        data = pd.concat([data, data.loc[data.position.eq("team") & data.side.eq("Blue")]], ignore_index=True)
        checks, aligned, _ = entity_audit(data)
        self.assertEqual(len(aligned), 10)
        self.assertEqual((~aligned.team_reference_found).sum(), 5)
        self.assertGreater(checks.set_index("check").loc["Ambiguous team-summary join keys", "affected"], 0)

    def test_transfers_and_shared_names_use_game_side_and_id(self):
        first = game()
        second = game("g2", "2026-02-01 12:00:00")
        second.loc[second.side.eq("Blue"), "teamid"] = "new-blue-id"
        data = pd.concat([first, second], ignore_index=True)
        checks, aligned, collisions = entity_audit(data)
        self.assertEqual(len(aligned), 20)
        self.assertEqual(aligned.team_id_conflict.sum(), 0)
        self.assertTrue((checks.loc[checks.severity.eq("blocker"), "affected"] == 0).all())
        self.assertTrue(((collisions.entity == "team") & (collisions.issue == "same_name_multiple_ids")).any())
        self.assertEqual(data.teamid.nunique(), 3)

    def test_only_bounded_missing_runs_are_candidates(self):
        frames = []
        for i, value in enumerate([100, np.nan, np.nan, 0, np.nan]):
            data = game(f"g{i}", f"2026-01-0{i+1} 12:00:00")
            data["goldat20"] = value
            frames.append(data)
        data = pd.concat(frames, ignore_index=True)
        found = bounded_missing_runs(data, contract(data), ["goldat20"])
        self.assertEqual(len(found), 1)
        self.assertEqual(found.iloc[0].missing_games, 2)
        self.assertEqual(found.iloc[0].preceding_game, "g0")
        self.assertEqual(found.iloc[0].following_game, "g3")

    def test_missingness_counts_reconcile_without_imputation(self):
        data = game()
        spec = contract(data)
        before = data.copy(deep=True)
        audited = missingness_audit(data, spec)
        self.assertEqual(audited[list(STATES)].to_numpy().sum(), len(data)*len(spec["columns"]))
        pd.testing.assert_frame_equal(data, before)
        missing = data.drop(columns="goldat15")
        self.assertFalse(eligible_rows(missing, ["goldat15"], "team", spec).any())
        ready = analysis_readiness(missing, spec).set_index("analysis")
        self.assertEqual(ready.loc["gold_at_15", "usable_rows"], 0)


if __name__ == "__main__":
    unittest.main()
