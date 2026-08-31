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


def _partner(battle: BattleState, player: PlayerState) -> PlayerState | None:
    """co-op：队友（非玩家本人）。单玩家时无。"""
    for p in battle.players:
        if p.id != player.id and p != player:
            return p
    return None


def _ally_target(battle: BattleState, player: PlayerState, target: Any, card: CardInstance) -> Combatant | None:
    """解析对"盟友/玩家"目标的效果落点（AnyAlly/AnyPlayer → 队友）。"""
    t = (card.target or "").lower()
    if t in ("anyally", "anyplayer", "players", "ally"):
        return _partner(battle, player) or player
    return None


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
        if (card.target or "").lower() in ("anyally", "anyplayer", "ally", "players"):
            ally = _partner(battle, player)
            if ally:
                ally.add_power(pid, amount)
        elif target == "self" or target is None:
            player.add_power(pid, amount)
        elif isinstance(target, int):
            en = battle.enemy_by_index(target)
            if en:
                en.add_power(pid, amount)


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


ORB_BASE: dict[str, tuple[int, int]] = {
    "LIGHTNING": (3, 8),
    "FROST": (2, 5),
    "PLASMA": (0, 2),
    "DARK": (0, 6),
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
    for action, orb_id, times in (card.orb_action or []):
        ally = _ally_target(battle, player, target, card)
        if action == "channel":
            base = ORB_BASE.get(orb_id, (0, 0))
            channel_orb(ally or player, orb_id, passive=base[0], evoke=base[1])
        elif action == "evoke":
            evoke_orb(battle, ally or player, times=times)
    # 关键字决定去向：EXHAUST→消耗堆；ETERNAL→回手；否则→弃牌堆
    kws = {k.upper() for k in (card.keywords or [])}
    if "EXHAUST" in kws:
        player.exhausted.append(card.card_id)
    elif "ETERNAL" in kws:
        player.hand.append(card)
    else:
        player.discard.append(card.card_id)


# ---------------------------------------------------------------------------
# Power 回合行为（回合末 tick 等）
# ---------------------------------------------------------------------------

from .state import Orb


def channel_orb(player: PlayerState, orb_id: str, *, passive: int = 0, evoke: int = 0) -> None:
    """充能球：加入队列（不超槽位）。"""
    orb = Orb(orb_id=orb_id, passive=passive, evoke=evoke)
    if len(player.orbs) < player.orb_capacity:
        player.orbs.append(orb)


def evoke_orb(battle: BattleState, player: PlayerState, *, times: int = 1) -> None:
    """激发最前端的球（弹出），并按其效果结算。"""
    for _ in range(max(1, times)):
        if not player.orbs:
            return
        orb = player.orbs.pop(0)
        value = orb.evoke + player.focus
        if orb.orb_id == "LIGHTNING":
            for enemy in battle.enemies:
                if enemy.alive:
                    deal_damage_to(battle, enemy, value, player)
        elif orb.orb_id == "FROST":
            give_block(player, value)
        elif orb.orb_id == "PLASMA":
            player.energy += value
        elif orb.orb_id == "DARK":
            target = next((e for e in battle.enemies if e.alive), None)
            if target:
                deal_damage_to(battle, target, value, player)


def process_orbs_turn_end(battle: BattleState, player: PlayerState) -> None:
    """回合末：每个球触发一次被动，然后激发最前端球。"""
    for orb in list(player.orbs):
        value = orb.passive + player.focus
        if orb.orb_id == "LIGHTNING":
            for enemy in battle.enemies:
                if enemy.alive:
                    deal_damage_to(battle, enemy, value, player)
        elif orb.orb_id == "FROST":
            give_block(player, value)
    if player.orbs:
        evoke_orb(battle, player, times=1)


def apply_potion(battle: BattleState, player: PlayerState, potion_id: str, target: int | None = None) -> bool:
    """按药水效果表结算一瓶药水；成功返回 True 并消耗。kind 未知/数值为0 的当作不可用。"""
    from .potion_data import POTION_TABLE
    entry = POTION_TABLE.get(potion_id)
    if not entry:
        return False
    kind = entry.get("kind", "unknown")
    value = int(entry.get("value") or 0)
    if kind == "block" and value:
        give_block(player, value)
    elif kind == "heal" and value:
        player.hp = min(player.max_hp, player.hp + int(player.max_hp * value / 100))
    elif kind.startswith("buff:") and value:
        player.add_power(kind.split(":", 1)[1], value)
    elif kind == "attack" and value:
        en = battle.enemy_by_index(target) if isinstance(target, int) else \
            next((e for e in battle.enemies if e.alive), None)
        if en is None:
            return False
        deal_damage_to(battle, en, value, player)
    elif kind == "draw" and value:
        draw_cards(battle, player, max(1, value))
    elif kind == "energy" and value:
        player.energy += value
    else:
        return False  # unknown / 0值：先不推荐用
    if potion_id in player.potions:
        player.potions.remove(potion_id)
    return True


def apply_relic_hook(battle: BattleState, player: PlayerState, hook_name: str) -> None:
    """按遗物 hook 类型应用战斗被动（数据驱动，relic_data）。

    支持简单 Var 类型：HealVar->治疗, BlockVar->格挡, CardsVar->抽牌, EnergyVar->能量。
    复杂 hook（召唤/奥术/打牌触发等）留待扩展。
    """
    from .relic_data import RELIC_TABLE
    for rid in player.relics:
        e = RELIC_TABLE.get(rid)
        if not e or hook_name not in e.get("hooks", []):
            continue
        value = int(e.get("value") or 0)
        vt = e.get("var_type", "")
        if not value:
            continue
        if "HealVar" in vt:
            player.hp = min(player.max_hp, player.hp + value)
        elif "BlockVar" in vt:
            give_block(player, value)
        elif "CardsVar" in vt:
            draw_cards(battle, player, value)
        elif "EnergyVar" in vt:
            player.max_energy = max(1, player.max_energy + value)


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
