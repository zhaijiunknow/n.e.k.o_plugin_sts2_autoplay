"""sim/diff 吞入断言：orbs/powers/focus/max_energy 从真实快照读入。"""
from __future__ import annotations

import json
import os

from sim.diff import from_live_state

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


def test_from_live_state_ingests_orbs_powers_focus_energy() -> None:
    data = _load_payload()
    combat, run = data["combat"], data["run"]
    st = from_live_state(combat, run)
    local = st.players[0]

    # orbs：live 的 orb_id 带 _ORB 后缀，归一到 effects 用的裸 id
    assert local.orbs
    assert local.orbs[0].orb_id == "LIGHTNING"
    # 被动/激发值原样读入
    assert local.orbs[0].passive == int(combat["player"]["orbs"][0].get("passive_value") or 0)
    assert local.orbs[0].evoke == int(combat["player"]["orbs"][0].get("evoke_value") or 0)

    assert local.focus == int(combat["player"].get("focus") or 0)
    assert local.orb_capacity == int(combat["player"].get("orb_capacity") or 0)
    assert local.max_energy == int(run.get("max_energy") or 3)
    assert isinstance(local.powers, dict)
    assert len(st.enemies) == len(combat.get("enemies") or [])

    # 手牌第一张带动态值：STRIKE_DEFECT，6 伤害
    assert local.hand
    assert local.hand[0].card_id == "STRIKE_DEFECT"
    assert local.hand[0].damage == 6


def test_from_live_state_ingests_powers_and_orbs_inline() -> None:
    combat = {
        "player": {
            "id": "p0", "current_hp": 50, "max_hp": 70, "block": 3, "energy": 3,
            "focus": 2,
            "powers": [
                {"index": 0, "power_id": "WEAK_POWER", "name": "Weak", "amount": 2, "is_debuff": True},
                {"index": 1, "power_id": "STRENGTH_POWER", "name": "Strength", "amount": 1, "is_debuff": False},
            ],
            "orbs": [
                {"slot_index": 0, "orb_id": "LIGHTNING_ORB", "name": "Lightning",
                 "passive_value": 3, "evoke_value": 8, "is_front": True},
            ],
            "orb_capacity": 3,
        },
        "hand": [],
        "enemies": [
            {"enemy_id": "SLIME", "current_hp": 10, "max_hp": 10, "block": 0,
             "intents": [{"intent_type": "Attack", "total_damage": 4, "damage": 4, "hits": 1}]},
        ],
    }
    run = {"max_energy": 3}
    st = from_live_state(combat, run)
    local = st.players[0]

    assert local.power("weak_power") == 2
    assert local.power("strength_power") == 1
    assert local.orbs[0].orb_id == "LIGHTNING"
    assert local.orbs[0].passive == 3 and local.orbs[0].evoke == 8
    assert local.focus == 2
    assert local.orb_capacity == 3
    assert local.max_energy == 3
