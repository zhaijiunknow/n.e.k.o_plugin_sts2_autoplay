from __future__ import annotations

import unittest

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.planner_interface import PlannedOperation


def _event_context(
    *,
    event_llm_scores: dict[int, float] | None,
    event_llm_weight: float = 0.5,
    guidance: str | None = None,
):
    event_options = [
        {"index": 0, "text": "reveal the map, next path is clearer"},
        {"index": 1, "text": "lose 5 hp and gain a relic"},
        {"index": 2, "text": "gain 40 gold, take 3 damage"},
    ]
    payload = {
        "event_options": event_options,
        "current_hp": 50,
        "max_hp": 80,
        "gold": 100,
    }
    summary_context = {"payload": payload}
    if guidance:
        summary_context["decision_payload"] = {
            "instructions": [{"content": guidance, "source": "neko_guidance"}],
        }
    return {
        "classification": {"state_name": "event", "screen_class": "event"},
        "summary_context": summary_context,
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "available_actions": [
                {"type": "choose_event_option", "label": "choose", "raw": {"name": "choose_event_option", "index": 0}},
            ],
        },
        "mode": {"allows_planner": True},
        "event_llm_scores": event_llm_scores,
        "event_llm_weight": event_llm_weight,
    }


class EventLlmFusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._planner = STS2HeuristicPlanner(logger=None)

    def test_event_branch_uses_llm_scores_and_labels_source(self) -> None:
        context = _event_context(event_llm_scores={0: 90, 1: 10, 2: 20}, event_llm_weight=1.0)
        result = self._planner.plan(context)

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.action_type, "choose_event_option")
        self.assertEqual(result.kwargs, {"option_index": 0})
        self.assertEqual(result.source, "heuristic+llm")

    def test_event_branch_without_llm_falls_back_to_heuristic(self) -> None:
        context = _event_context(event_llm_scores=None)
        result = self._planner.plan(context)

        self.assertIsInstance(result, PlannedOperation)
        self.assertEqual(result.source, "heuristic")

    def test_guidance_is_hard_constraint_even_against_llm(self) -> None:
        # prefer_defense + an option that loses hp (index 1): the LLM favours it (45) but the guidance
        # penalty (-15) must make a safer option (index 0, LLM 40) win after fusion at weight=1.
        context = _event_context(
            event_llm_scores={0: 40, 1: 45, 2: 20},
            event_llm_weight=1.0,
            guidance="先防",
        )
        result = self._planner.plan(context)
        self.assertEqual(result.kwargs["option_index"], 0)

    def test_guidance_absent_allows_llm_preference_to_win(self) -> None:
        # Without guidance, the LLM preference (index 1, 45) wins at weight=1.
        context = _event_context(event_llm_scores={0: 40, 1: 45, 2: 20}, event_llm_weight=1.0)
        result = self._planner.plan(context)
        self.assertEqual(result.kwargs["option_index"], 1)

    def test_missing_llm_scores_still_comparable(self) -> None:
        # Index 1 has no LLM score; it must still be a candidate and not crash.
        context = _event_context(event_llm_scores={0: 60, 2: 10}, event_llm_weight=0.5)
        result = self._planner.plan(context)
        self.assertIsInstance(result, PlannedOperation)

    def test_normalize_percentile_flat_to_50(self) -> None:
        self.assertEqual(STS2HeuristicPlanner._normalize_percentile({0: 5, 1: 5}), {0: 50.0, 1: 50.0})

    def test_normalize_percentile_maps_min_max(self) -> None:
        norm = STS2HeuristicPlanner._normalize_percentile({0: 0, 1: 100, 2: 50})
        self.assertAlmostEqual(norm[0], 0.0)
        self.assertAlmostEqual(norm[1], 100.0)
        self.assertAlmostEqual(norm[2], 50.0)


if __name__ == "__main__":
    unittest.main()
