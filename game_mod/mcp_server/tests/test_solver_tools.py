from __future__ import annotations

import asyncio
import unittest

from sts2_mcp.server import create_server


class SolverClient:
    """Minimal Sts2Client stub exposing the solver / co-op read endpoints only."""

    def __init__(self, combat_plan: dict | None = None, coop_state: dict | None = None) -> None:
        self._combat_plan = combat_plan or {"in_combat": False, "reason": "not_in_combat"}
        self._coop_state = coop_state or {"players": [], "enemies": []}

    def get_health(self) -> dict:
        return {"ok": True}

    def get_state(self) -> dict:
        return {"screen": "COMBAT", "available_actions": []}

    def get_available_actions(self) -> list[dict]:
        return []

    def wait_for_event(self, *, event_names=None, timeout=0.0):
        return None

    def get_game_data_collection(self, collection: str):
        raise RuntimeError("Game data loader is not available on this client.")

    def get_combat_plan(self) -> dict:
        return self._combat_plan

    def get_coop_state(self) -> dict:
        return self._coop_state


class SolverToolsTests(unittest.TestCase):
    def tool_fn(self, server, name) -> object:
        return asyncio.run(server.get_tool(name)).fn

    def test_get_combat_plan_returns_recommendation_when_in_combat(self) -> None:
        plan = {
            "in_combat": True,
            "turn": 3,
            "score": 42.0,
            "action": "play_card",
            "card_index": 1,
            "card_id": "STRIKE",
            "target_index": 0,
            "line": [{"kind": "play_card", "card_index": 1, "card_id": "STRIKE"}],
            "search_status": "complete",
        }
        server = create_server(client=SolverClient(combat_plan=plan))
        result = self.tool_fn(server, "get_combat_plan")()
        self.assertEqual(result, plan)

    def test_get_combat_plan_returns_not_in_combat_payload(self) -> None:
        server = create_server(
            client=SolverClient(combat_plan={"in_combat": False, "reason": "not_in_combat"}),
        )
        result = self.tool_fn(server, "get_combat_plan")()
        self.assertFalse(result["in_combat"])
        self.assertEqual(result["reason"], "not_in_combat")

    def test_get_combat_plan_registered_in_guided_profile(self) -> None:
        server = create_server(client=SolverClient(), tool_profile="guided")
        tool = asyncio.run(server.get_tool("get_combat_plan"))
        self.assertIsNotNone(tool)

    def test_get_coop_state_returns_every_player_in_layered_profile(self) -> None:
        coop = {
            "players": [
                {"player_index": 0, "name": "A", "action_phase": True, "hand": ["STRIKE"]},
                {"player_index": 1, "name": "B", "action_phase": False, "hand": ["DEFEND"]},
            ],
            "enemies": [{"id": "CULTIST"}],
        }
        server = create_server(client=SolverClient(coop_state=coop), tool_profile="layered")
        result = self.tool_fn(server, "get_coop_state")()
        self.assertEqual(result, coop)

    def test_get_coop_state_is_absent_in_guided_profile(self) -> None:
        server = create_server(client=SolverClient(), tool_profile="guided")
        tool = asyncio.run(server.get_tool("get_coop_state"))
        self.assertIsNone(tool)


if __name__ == "__main__":
    unittest.main()
