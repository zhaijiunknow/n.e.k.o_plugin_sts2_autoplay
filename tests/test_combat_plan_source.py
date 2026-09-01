from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import _combat_plan_signature
from plugin.plugins.sts2_autoplay.planner_interface import PlannedOperation
from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient


def _combat_snapshot(**overrides):
    snapshot = {
        "raw_state": {
            "combat": {
                "player": {"energy": 3, "block": 0},
                "hand": [],
                "enemies": [],
            },
            "run": {"potions": []},
        },
        "available_actions": [],
        "screen": "combat",
    }
    snapshot.update(overrides)
    return snapshot


def _combat_context(solver_plan, snapshot):
    return {
        "classification": {"state_name": "combat", "screen_class": "combat"},
        "summary_context": {},
        "strategy_context": {},
        "snapshot": snapshot,
        "mode": {"allows_planner": True},
        "solver_plan": solver_plan,
    }


class CombatPlanSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._planner = STS2HeuristicPlanner(logger=None)

    def test_plays_card_from_mod_solver_plan(self) -> None:
        solver_plan = {
            "in_combat": True,
            "turn": 3,
            "score": 42.0,
            "action": "play_card",
            "card_index": 1,
            "card_id": "STRIKE",
            "target_index": 0,
            "reason": "search_top",
        }
        context = _combat_context(solver_plan, _combat_snapshot())
        result = self._planner.plan(context)

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "play_card")
        self.assertEqual(result.kwargs, {"card_index": 1, "target_index": 0})
        self.assertEqual(result.source, "mod_solver")

    def test_maps_use_potion_to_option_index(self) -> None:
        snapshot = _combat_snapshot(
            raw_state={
                "combat": {"player": {"energy": 3, "block": 0}, "hand": [], "enemies": []},
                "run": {"potions": [{"potion_id": "BLOOD_POTION", "index": 1, "can_use": True}]},
            }
        )
        solver_plan = {"in_combat": True, "action": "use_potion", "card_id": "BLOOD_POTION", "target_index": 0}
        result = self._planner.plan(_combat_context(solver_plan, snapshot))

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "use_potion")
        self.assertEqual(result.kwargs, {"option_index": 1, "target_index": 0})
        self.assertEqual(result.source, "mod_solver")

    def test_end_turn_from_mod_solver_plan(self) -> None:
        solver_plan = {"in_combat": True, "action": "end_turn"}
        result = self._planner.plan(_combat_context(solver_plan, _combat_snapshot()))

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "end_turn")
        self.assertEqual(result.source, "mod_solver")

    def test_does_not_use_mod_solver_without_plan(self) -> None:
        # No solver_plan in combat -> falls to heuristic; must NOT emit a mod_solver op and must not raise.
        context = _combat_context(solver_plan=None, snapshot=_combat_snapshot())
        result = self._planner.plan(context)

        if result is not None:
            self.assertNotEqual(result.source, "mod_solver")

    def test_ignores_not_in_combat_plan(self) -> None:
        solver_plan = {"in_combat": False, "reason": "not_in_combat"}
        context = _combat_context(solver_plan=solver_plan, snapshot=_combat_snapshot())
        result = self._planner.plan(context)

        if result is not None:
            self.assertNotEqual(result.source, "mod_solver")

    def test_ignores_planless_card_index(self) -> None:
        # in_combat true but no card_index -> not a usable play_card; fall through to heuristic.
        solver_plan = {"in_combat": True, "action": "play_card"}
        context = _combat_context(solver_plan=solver_plan, snapshot=_combat_snapshot())
        result = self._planner.plan(context)

        if result is not None:
            self.assertNotEqual(result.source, "mod_solver")


class TransportGetCombatPlanTests(unittest.TestCase):
    def test_get_combat_plan_hits_solver_plan_endpoint(self) -> None:
        client = STS2TransportClient(base_url="http://127.0.0.1:8080")
        with patch.object(client, "_request", new=AsyncMock(return_value={"in_combat": True, "action": "play_card"})) as m:
            result = asyncio.run(client.get_combat_plan())

        m.assert_awaited_once_with("GET", "/solver/plan")
        self.assertEqual(result, {"in_combat": True, "action": "play_card"})


class CombatPlanSignatureTests(unittest.TestCase):
    def test_signature_stable_for_unchanged_combat_state(self) -> None:
        snapshot = _combat_snapshot()
        self.assertEqual(_combat_plan_signature(snapshot), _combat_plan_signature(snapshot))

    def test_signature_changes_when_combat_state_changes(self) -> None:
        a = _combat_snapshot()
        b = _combat_snapshot(raw_state={"combat": {"player": {"energy": 0}, "hand": [], "enemies": []}, "run": {"potions": []}})
        self.assertNotEqual(_combat_plan_signature(a), _combat_plan_signature(b))

    def test_signature_changes_when_potions_change(self) -> None:
        a = _combat_snapshot(raw_state={"combat": {"player": {"energy": 3}, "hand": [], "enemies": []}, "run": {"potions": [{"potion_id": "X", "index": 1, "can_use": True}]}})
        b = _combat_snapshot(raw_state={"combat": {"player": {"energy": 3}, "hand": [], "enemies": []}, "run": {"potions": []}})
        self.assertNotEqual(_combat_plan_signature(a), _combat_plan_signature(b))


if __name__ == "__main__":
    unittest.main()
