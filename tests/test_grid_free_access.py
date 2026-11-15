"""Regression checks for analysis grain and missing-value handling; fixtures are synthetic."""

import copy
import unittest

import pandas as pd

from analysis.grid_free_access import availability_audit, duration_seconds, flatten_state, gold_comparison, grain_audit


def snapshot():
    teams = []
    actions = []
    for index, team_id in enumerate(["A", "B"]):
        roster = [{"id": f"{team_id}{i}", "name": f"Player {i}", "character": {"id": f"{team_id}-pick-{i}", "name": f"Champion {i}"},
                   "roles": [], "kills": 0, "deaths": 0, "totalMoneyEarned": 100, "netWorth": 90}
                  for i in range(5)]
        teams.append({"id": team_id, "name": "Team " + team_id, "side": ["red", "blue"][index],
                      "won": index == 0, "kills": 0, "deaths": 0, "totalMoneyEarned": 500,
                      "netWorth": 450, "moneyDifference": 0, "players": roster})
        for kind in ["pick", "ban"]:
            for i in range(5):
                sequence = len(actions) + 1
                actions.append({"id": f"draft-action-{sequence}", "type": kind, "sequenceNumber": str(sequence),
                                "drafter": {"id": team_id, "type": "team"},
                                "draftable": {"id": f"{team_id}-{kind}-{i}", "name": f"Champion {i}", "type": "character"}})
    game = {"id": "game-1", "sequenceNumber": 1, "finished": True, "valid": True, "duration": "PT20M1S", "teams": teams, "draftActions": actions}
    second = copy.deepcopy(game)
    second.update(id="game-2", sequenceNumber=2)
    second["teams"].reverse()
    return {"id": "series", "teams": [{"id": "A"}, {"id": "B"}], "games": [game, second]}


class FreeAccessAnalysisTests(unittest.TestCase):
    def test_duration_is_explicit_and_rejects_unknown_units(self):
        self.assertAlmostEqual(duration_seconds("PT1H2M3.5S"), 3723.5)
        self.assertIsNone(duration_seconds(3723))
        self.assertIsNone(duration_seconds("PT"))

    def test_draft_ids_are_scoped_to_game_and_picks_join_by_champion_id(self):
        state = snapshot()
        tables = flatten_state(state)
        checks, _ = grain_audit(tables, state)
        self.assertEqual(len(tables["players"]), 20)
        self.assertTrue(tables["drafts"].action_id.duplicated().any())
        self.assertTrue(checks[checks.severity.eq("block")].passed.all())
        state["games"][0]["draftActions"][0]["draftable"]["id"] = "incorrect-champion"
        checks, _ = grain_audit(flatten_state(state), state)
        self.assertFalse(checks.set_index("check").loc["Picks match player champions", "passed"])

    def test_duplicate_player_rows_block_the_audit(self):
        state = snapshot()
        state["games"][0]["teams"][0]["players"].append(copy.deepcopy(state["games"][0]["teams"][0]["players"][0]))
        checks, _ = grain_audit(flatten_state(state), state)
        self.assertFalse(checks.set_index("check").loc["players keys", "passed"])

    def test_audit_keeps_zeros_nulls_absent_keys_and_empty_lists_distinct(self):
        state = snapshot()
        first, second = state["games"][0]["teams"][0]["players"][:2]
        first["visionScore"] = None
        second["visionScore"] = 0
        audit = availability_audit(state).set_index(["grain", "field"])
        row = audit.loc[("player-game", "visionScore")]
        self.assertEqual(row["null"], 1)
        self.assertEqual(row["observed_zero"], 1)
        self.assertEqual(row["absent"], 18)
        self.assertEqual(audit.loc[("player-game", "roles"), "empty_collection"], 20)
        self.assertEqual(audit.loc[("player-game", "kills"), "observed_zero"], 20)

    def test_gold_comparison_uses_ids_despite_team_order_and_preserves_missing(self):
        teams = flatten_state(snapshot())["teams"]
        teams.loc[teams.team_id.eq("A"), "totalMoneyEarned"] = 600
        teams.loc[teams.team_id.eq("A"), "moneyDifference"] = 100
        result = gold_comparison(teams, "A")
        self.assertEqual(result.gold_difference.tolist(), [100, 100])
        teams.loc[teams.game_id.eq("game-2") & teams.team_id.eq("B"), "totalMoneyEarned"] = float("nan")
        result = gold_comparison(teams, "A")
        self.assertTrue(pd.isna(result.iloc[1].gold_difference))
        with self.assertRaises(ValueError):
            gold_comparison(teams[teams.team_id.eq("A")], "A")

    def test_kill_residual_is_reviewed_and_raw_totals_are_preserved(self):
        state = snapshot()
        state["games"][0]["teams"][0]["kills"] = 1
        tables = flatten_state(state)
        checks, residuals = grain_audit(tables, state)
        self.assertTrue(checks[checks.severity.eq("block")].passed.all())
        finding = checks.set_index("check").loc["Team minus player sums: kills"]
        self.assertFalse(finding.passed)
        self.assertEqual(finding.severity, "review")
        self.assertEqual(residuals.kills_residual.tolist(), [1, 0, 0, 0])
        self.assertEqual(tables["teams"].iloc[0].kills, 1)


if __name__ == "__main__":
    unittest.main()
