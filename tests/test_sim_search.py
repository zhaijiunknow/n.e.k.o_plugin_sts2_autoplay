"""sim/search：放宽可选牌（buff/球/抽/能量）+ 吐 use_potion 药水。"""
from __future__ import annotations

import os

from sim.search import first_recommendation, search
from sim.state import BattleState, CardInstance, EnemyState, PlayerState

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "combat_step_result.json")


def _single_enemy_state(hand, *, potions=None, energy=3, enemy_hp=20) -> BattleState:
    player = PlayerState(
        id="p0", hp=50, max_hp=50, block=0, energy=energy, max_energy=3,
        hand=list(hand), potions=list(potions or []), relics=[],
    )
    enemies = [EnemyState(id="0", hp=enemy_hp, max_hp=enemy_hp, block=0,
                          intent_damage=0, intent_attack=False, move_id="", enemy_id="SLIME")]
    return BattleState(players=[player], enemies=enemies)


def test_search_makes_buff_and_orb_cards_playable() -> None:
    # 一张球卡（无即时伤害/格挡）+ 一张 buff 卡（无即时数值）也应进入可选面
    zap = CardInstance(card_id="ZAP", name="Zap", cost=1, card_type="Skill", target="Self",
                       damage=0, block=0, orb_action=[("channel", "LIGHTNING", 1)])
    buff = CardInstance(card_id="BUFF_TEST", name="Buff", cost=1, card_type="Skill", target="Self",
                        damage=0, block=0, powers_applied=[("strength_power", 2)])
    st = _single_enemy_state([zap, buff])
    best = search(st, horizon=1, beam_width=8)
    assert any(step[0] == "PLAY" for step in best.line)


def test_first_recommendation_surfaces_potion() -> None:
    # 空手牌 + 一瓶可用的攻击药水（CombatOnly / AnyEnemy）=> 推荐 use_potion
    st = _single_enemy_state([], potions=["FIRE_POTION"], enemy_hp=20)
    best = search(st, horizon=1, beam_width=8)
    rec = first_recommendation(best, st)
    assert rec is not None
    assert rec["action"] == "use_potion"
    assert rec["potion_id"] == "FIRE_POTION"
    # FIRE_POTION 目标是 AnyEnemy → 必须带 target_index
    assert rec.get("target_index") == 0


def test_search_still_prefers_attack_card_line() -> None:
    # 有即时伤害的牌仍是首选（回归：不破坏原 play_card 行为）
    strike = CardInstance(card_id="STRIKE", name="Strike", cost=1, card_type="Attack",
                          target="AnyEnemy", damage=6, block=0)
    st = _single_enemy_state([strike], enemy_hp=20)
    best = search(st, horizon=1, beam_width=8)
    rec = first_recommendation(best, st)
    assert rec is not None
    assert rec["action"] == "play_card"
    assert isinstance(rec.get("card_index"), int)
    assert rec.get("card_id") == "STRIKE"


def test_star_cost_card_requires_stars() -> None:
    # 0 能量 + 2 星费的卡：无星不可打，有星可打（星星是第二资源）
    star_card = CardInstance(card_id="ASTRAL_PULSE", name="Astral", cost=0, star_cost=2,
                             card_type="Attack", target="AllEnemies", damage=14)
    st = _single_enemy_state([star_card], enemy_hp=20)
    st.players[0].stars = 0
    best = search(st, horizon=1, beam_width=8)
    assert not any(s[0] == "PLAY" for s in best.line)  # 没星 → 打不出这张
    st.players[0].stars = 2
    best2 = search(st, horizon=1, beam_width=8)
    assert any(s[0] == "PLAY" for s in best2.line)      # 有 2 星 → 可打


def test_star_next_turn_resolves() -> None:
    # STAR_NEXT_TURN power 在回合开始 +星星并清零
    from sim.simulator import new_turn
    p = PlayerState(id="p0", hp=50, max_hp=50, energy=3, max_energy=3,
                    hand=[], draw=["STRIKE"] * 5, potions=[], relics=[],
                    powers={"star_next_turn_power": 2}, stars=0)
    st = BattleState(players=[p], enemies=[])
    new_turn(st, p)
    assert p.stars == 2
    assert p.powers["star_next_turn_power"] == 0
