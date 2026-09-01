"""新建模药水类别：max_hp / orb_slots / orb:TYPE / hand_exhaust / upgrade_hand + 可用性闸。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sim.effects import apply_potion
from sim.search import _usable_potion
from sim.state import BattleState, CardInstance, EnemyState, PlayerState


def _state(hand=None, potions=None, *, hp=50, max_hp=50, orb_capacity=0) -> BattleState:
    player = PlayerState(
        id="p0", hp=hp, max_hp=max_hp, block=0, energy=3, max_energy=3,
        hand=list(hand or []), potions=list(potions or []), orb_capacity=orb_capacity,
    )
    enemies = [EnemyState(id="0", hp=20, max_hp=20, block=0, intent_damage=0,
                          intent_attack=False, move_id="", enemy_id="SLIME")]
    return BattleState(players=[player], enemies=enemies)


def test_apply_potion_max_hp() -> None:
    st = _state()
    pl = st.players[0]
    assert apply_potion(st, pl, "FRUIT_JUICE") is True
    assert pl.max_hp == 55 and pl.hp == 55  # +5 max hp 且回血


def test_apply_potion_orb_slots() -> None:
    st = _state(orb_capacity=2)
    pl = st.players[0]
    assert apply_potion(st, pl, "POTION_OF_CAPACITY") is True
    assert pl.orb_capacity == 4


def test_apply_potion_orb_channel_dark() -> None:
    st = _state(orb_capacity=1)
    pl = st.players[0]
    assert apply_potion(st, pl, "ESSENCE_OF_DARKNESS") is True
    assert pl.orbs and pl.orbs[0].orb_id == "DARK"
    # 被 capacity 限幅
    assert len(pl.orbs) == 1


def test_apply_potion_hand_exhaust() -> None:
    card = CardInstance(card_id="CLOGGED", name="Clogged", cost=3, damage=0, block=0)
    st = _state(hand=[card])
    pl = st.players[0]
    assert apply_potion(st, pl, "ASHWATER") is True
    assert card not in pl.hand
    assert "CLOGGED" in pl.exhausted


def test_apply_potion_upgrade_hand() -> None:
    card = CardInstance(card_id="STRIKE", name="Strike", cost=1, damage=5, block=0)
    st = _state(hand=[card])
    pl = st.players[0]
    assert apply_potion(st, pl, "BLESSING_OF_THE_FORGE") is True
    assert card.damage == 7  # +2


def test_usable_potion_gate() -> None:
    assert _usable_potion("POTION_OF_CAPACITY") is True   # orb_slots, value 2
    assert _usable_potion("ATTACK_POTION") is True        # draw, value 1
    assert _usable_potion("FIRE_POTION") is True          # attack
    assert _usable_potion("STAR_POTION") is False         # utility: 无战斗影响
    assert _usable_potion("ENTROPIC_BREW") is False       # AnyTime
