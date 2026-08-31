"""效果引擎：把"卡牌字段"和"Power 行为"映射成状态变更。

核心思想（遵循你的架构）：
- 每张卡 = 一组"字段"，每个字段对应一个 handler。
- 效果表 EFFECT_HANDLERS：字段名 -> (battle, player, card, value, target) -> None。
- Power 行为 POWER_BEHAVIORS：power_id -> 该 Power 在结算中的语义
  （攻击倍率、受击倍率、回合末 tick 等）。可组合、可扩展。

正确性优先：伤害公式 = Base → (攻击方 Weak ×0.75) → (防御方 Vulnerable ×1.5) → 格挡 → HP。
"""
from __future__ import annotations

from typing import Any

from .state import BattleState, CardInstance, Combatant, PlayerState


# ---------------------------------------------------------------------------
# 数值规则
# ---------------------------------------------------------------------------

# 攻击方身上的 Power -> 攻击伤害倍率
ATTACK_MOD_POWERS: dict[str, float] = {
    "weak_power": 0.75,   # 虚弱：攻击伤害 ×0.75
}
# 防御方身上的 Power -> 承受伤害倍率
DEFENSE_MOD_POWERS: dict[str, float] = {
    "vulnerable_power": 1.5,  # 易伤：承受伤害 ×1.5
}


def attacker_damage_mult(player: Combatant) -> float:
    mult = 1.0
    for pid, m in ATTACK_MOD_POWERS.items():
        if player.power(pid) > 0:
            mult *= m
    return mult


def defender_damage_mult(target: Combatant) -> float:
    mult = 1.0
    for pid, m in DEFENSE_MOD_POWERS.items():
        if target.power(pid) > 0:
            mult *= m
    return mult


def compute_damage(base: int, attacker: Combatant, defender: Combatant) -> int:
    """(Base + 攻击方力量) -> Weak(attacker) -> Vulnerable(defender)，取整。"""
    base_with_strength = base + attacker.power("strength_power")
    dmg = base_with_strength * attacker_damage_mult(attacker) * defender_damage_mult(defender)
    return int(dmg)


# ---------------------------------------------------------------------------
# 结算原语
# ---------------------------------------------------------------------------

def deal_damage_to(
    battle: BattleState,
    target: Combatant,
    base_damage: int,
    attacker: Combatant,
    *,
    hits: int = 1,
) -> int:
    """把 base_damage×(hits) 打到 target（格挡先抵消）。返回实际造成 HP 伤害之和。"""
    total = 0
    for _ in range(max(1, hits)):
        amount = compute_damage(base_damage, attacker, target)
        remaining = amount
        if target.block > 0:
            absorbed = min(target.block, remaining)
            target.block -= absorbed
            remaining -= absorbed
        if remaining > 0:
            target.hp = max(0, target.hp - remaining)
            total += remaining
    return total


def give_block(player: PlayerState, amount: int) -> None:
    player.block += amount + player.power("dexterity_power")


def draw_cards(state: BattleState, player: PlayerState, n: int) -> None:
    for _ in range(n):
        if not player.draw:
            player.draw = list(player.discard)
            player.discard = []
        if player.draw:
            player.hand.append(CardInstance(card_id=player.draw.pop(0)))


# ---------------------------------------------------------------------------
# 效果表：字段名 -> handler
# ---------------------------------------------------------------------------

EffectHandler = Any  # (battle, player, card, value, target) -> None


def _h_damage(battle, player, card, value, target):
    base = int(value or card.damage)
    target_c = battle.enemy_by_index(target) if isinstance(target, int) else None
    if target_c is None:
        target_c = next((e for e in battle.enemies if e.alive), None)
    if target_c is None:
        return
    deal_damage_to(battle, target_c, base, player, hits=card.hit_count or 1)


def _h_block(battle, player, card, value, target):
    give_block(player, int(value or card.block))


def _h_draw(battle, player, card, value, target):
    draw_cards(battle, player, int(value or card.cards_draw))


def _h_energy(battle, player, card, value, target):
    player.energy += int(value or card.energy_gain)


def _h_hp_loss(battle, player, card, value, target):
    player.hp = max(0, player.hp - int(value or card.hp_loss))


def _h_power(battle, player, card, value, target):
    # value = [(power_id, amount)]；target 决定施加给谁
    for pid, amount in (value or card.powers_applied):
        # target: None/Self=player, int=敌方下标, "ally"=另一个玩家
        if target == "self" or target is None:
            player.add_power(pid, amount)
        elif isinstance(target, int):
            en = battle.enemy_by_index(target)
            if en:
                en.add_power(pid, amount)
        # "ally" 略（co-op 扩展）


# 字段 -> handler（新卡只要用这些字段，就零代码）
EFFECT_HANDLERS: dict[str, EffectHandler] = {
    "damage": _h_damage,
    "block": _h_block,
    "cards_draw": _h_draw,
    "energy_gain": _h_energy,
    "hp_loss": _h_hp_loss,
    "powers_applied": _h_power,
    # 其它字段（hit_count 是伤害的循环，由 _h_damage 用 card.hit_count 处理）
}


def apply_card(
    battle: BattleState,
    player: PlayerState,
    card: CardInstance,
    target: int | None,
) -> None:
    """结算一张卡：扣能量 -> 逐字段触发 handler -> 进弃牌堆/消耗。"""
    if card.cost > player.energy:
        return
    player.energy -= card.cost
    for field, handler in EFFECT_HANDLERS.items():
        value = getattr(card, field, None)
        if value:
            handler(battle, player, card, value, target)
    if card.card_type == "Exhaust" or getattr(card, "exhaust", False):
        player.exhausted.append(card.card_id)
    else:
        player.discard.append(card.card_id)


# ---------------------------------------------------------------------------
# Power 回合行为（回合末 tick 等）
# ---------------------------------------------------------------------------

def tick_turn_end(battle: BattleState, combatant: Combatant) -> None:
    """回合末结算所有 game-affecting 的 Power。poison：扣 stacks 血，然后 -1。"""
    poison = combatant.power("poison_power")
    if poison > 0:
        combatant.hp = max(0, combatant.hp - poison)
        combatant.powers["poison_power"] = poison - 1
    # 后续 Power（如 每回合+力量的 RITUAL 等）在此扩展
    combatant.powers.setdefault("_ticked", 0)


__all__ = [
    "EFFECT_HANDLERS", "apply_card", "compute_damage", "deal_damage_to",
    "give_block", "draw_cards", "tick_turn_end",
    "attacker_damage_mult", "defender_damage_mult",
]
