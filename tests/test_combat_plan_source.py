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
            "state_fingerprint": "abc123",
            "line": [{"turn": 1, "steps": [{"kind": "play_card", "card_index": 1, "card_id": "STRIKE", "target_index": 0}]}],
        }
        # 手牌里给一张可打出的 STRIKE，使 _resolve_solver_card_index 按 card_id 映射回手牌枚举位置。
        # 注意：_resolve_solver_card_index 返回的是手牌枚举位置（从0数），不是卡上自带的 index 字段。
        snapshot = _combat_snapshot(
            raw_state={
                "combat": {"player": {"energy": 3, "block": 0}, "enemies": [], "hand": [
                    {"card_id": "STRIKE", "index": 1, "playable": True},
                ]},
                "run": {"potions": []},
            }
        )
        context = _combat_context(solver_plan, snapshot)
        result = self._planner.plan(context)

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "play_card")
        # STRIKE 在手牌枚举位置 0（尽管卡上 index 字段标 1），故 card_index 应为 0。
        self.assertEqual(result.kwargs, {"card_index": 0, "target_index": 0})
        self.assertEqual(result.source, "mod_solver")

    def test_maps_use_potion_to_option_index(self) -> None:
        snapshot = _combat_snapshot(
            raw_state={
                "combat": {"player": {"energy": 3, "block": 0}, "hand": [], "enemies": []},
                "run": {"potions": [{"potion_id": "BLOOD_POTION", "index": 1, "can_use": True}]},
            }
        )
        solver_plan = {"in_combat": True, "line": [{"turn": 1, "steps": [{"kind": "use_potion", "card_id": "BLOOD_POTION", "target_index": 0}]}]}
        result = self._planner.plan(_combat_context(solver_plan, snapshot))

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "use_potion")
        self.assertEqual(result.kwargs, {"option_index": 1, "target_index": 0})
        self.assertEqual(result.source, "mod_solver")

    def test_end_turn_from_mod_solver_plan(self) -> None:
        solver_plan = {"in_combat": True, "line": [{"turn": 1, "steps": [{"kind": "end_turn"}]}]}
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
        solver_plan = {"in_combat": False}
        context = _combat_context(solver_plan=solver_plan, snapshot=_combat_snapshot())
        result = self._planner.plan(context)

        if result is not None:
            self.assertNotEqual(result.source, "mod_solver")

    def test_ignores_planless_card_index(self) -> None:
        # in_combat true but line[0].steps[0] has no card_index -> not a usable play_card; fall through.
        solver_plan = {"in_combat": True, "line": [{"turn": 1, "steps": [{"kind": "play_card"}]}]}
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

    def test_signature_changes_when_turn_changes(self) -> None:
        # 整回合缓存：签名只按 run_id + turn。换 turn 应变化。
        a = _combat_snapshot(raw_state={"run_id": "r1", "turn": 1})
        b = _combat_snapshot(raw_state={"run_id": "r1", "turn": 2})
        self.assertNotEqual(_combat_plan_signature(a), _combat_plan_signature(b))

    def test_signature_stable_within_same_turn(self) -> None:
        # 同一 run_id + turn 内，即使手牌/药水等局面细节变化，签名也应保持稳定（整回合缓存、不重查）。
        a = _combat_snapshot(raw_state={"run_id": "r1", "turn": 1, "combat": {"player": {"energy": 3}, "hand": [], "enemies": []}, "run": {"potions": []}})
        b = _combat_snapshot(raw_state={"run_id": "r1", "turn": 1, "combat": {"player": {"energy": 0}, "hand": [], "enemies": []}, "run": {"potions": [{"potion_id": "X", "index": 1, "can_use": True}]}})
        self.assertEqual(_combat_plan_signature(a), _combat_plan_signature(b))


if __name__ == "__main__":
    unittest.main()
