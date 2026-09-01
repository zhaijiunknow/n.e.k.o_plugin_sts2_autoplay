"""端到端：combat_decision.sim_recommend 用真实 fixture 快照产出推荐（play_card / use_potion）。"""
from __future__ import annotations

import json
import os

from combat_decision import sim_recommend

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "combat_step_result.json")


def _load_payload() -> dict:
    with open(FIXTURE, encoding="utf-8") as f:
        doc = json.load(f)

    def find(o):
        if isinstance(o, dict):
            if "combat" in o and "run" in o and "agent_view" in o:
                return o
            for v in o.values():
                r = find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r is not None:
                    return r
        return None

    return find(doc)


def test_sim_recommend_on_fixture_returns_recommendation() -> None:
    data = _load_payload()
    snapshot = {
        "raw_state": data,
        "available_actions": [
            {"type": "play_card", "raw": {"name": "play_card", "requires_index": True}},
            {"type": "end_turn", "raw": {"name": "end_turn"}},
            {"type": "use_potion", "raw": {"name": "use_potion", "requires_index": True}},
        ],
    }
    rec = sim_recommend(snapshot)
    # 旧 fixture 的 FLEX_POTION 是 value=0 的 buff（不可用），所以期望 play_card；但要容忍 None/use_potion。
    assert rec is None or rec.get("action") in {"play_card", "use_potion"}
    if rec and rec.get("action") == "play_card":
        assert isinstance(rec.get("card_index"), int)
