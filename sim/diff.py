"""把 mod 的 /state.combat 快照灌成模拟器状态（差分验证用）。"""
from __future__ import annotations

from typing import Any

from .cards import card as card_by_id
from .state import BattleState, CardInstance, EnemyState, PlayerState


def _dynamic_value(card: dict[str, Any], key: str) -> int:
    for dv in card.get("dynamic_values") or []:
        if isinstance(dv, dict) and dv.get("name") == key:
            return int(dv.get("current_value") or dv.get("base_value") or 0)
    return 0


# 快照里动态值名（如 VulnerablePower）和 canonical power id（VULNERABLE_POWER）不一致，做归一。
_POWER_ALIASES: dict[str, str] = {
    "vulnerablepower": "vulnerable_power",
    "weakpower": "weak_power",
    "poisonpower": "poison_power",
    "strengthpower": "strength_power",
    "dexteritypower": "dexterity_power",
}


def _norm_power(name: str) -> str:
    key = str(name or "").lower()
    return _POWER_ALIASES.get(key, key)


def card_instance_from_json(card: dict[str, Any]) -> CardInstance:
    """优先读快照里的动态值（含修饰），退回 cards.json 基础值。"""
    base = card_by_id(card.get("card_id") or "")
    dmg = _dynamic_value(card, "Damage")
    blk = _dynamic_value(card, "Block")
    if dmg or blk:
        pass
    elif base is not None:
        dmg, blk = base.damage, base.block
    powers: list[tuple[str, int]] = []
    # 快照里 powers 通过动态值如 VulnerablePower/WeakPower 出现
    for name, val in ((k, v) for dv in (card.get("dynamic_values") or []) if isinstance(dv, dict) for k, v in [(dv.get("name"), dv.get("current_value") or dv.get("base_value") or 0)] if isinstance(k, str) and k and k not in ("Damage", "Block", "Cards", "Repeat")):
        if name and val:
            powers.append((_norm_power(name), int(val)))
    return CardInstance(
        card_id=str(card.get("card_id") or ""),
        name=str(card.get("name") or ""),
        cost=int(card.get("energy_cost") or base.cost if base else card.get("energy_cost") or 0),
        card_type=str(card.get("card_type") or (base.card_type if base else "")),
        target=str(card.get("target_type") or (base.target if base else "Self")),
        damage=dmg,
        block=blk,
        hit_count=int(card.get("hit_count") or 0),
        powers_applied=powers,
    )


def from_live_state(combat: dict[str, Any]) -> BattleState:
    # 本地玩家：读 combat.player（有手牌/能量/球）+ combat.players 里的 HP/格挡。
    players_list = combat.get("players") or []
    local_json = combat.get("player") or {}
    local_id = str(local_json.get("id") or local_json.get("player_id") or "p0")
    local = PlayerState(
        id=local_id,
        hp=int(local_json.get("current_hp") or local_json.get("hp") or 0),
        max_hp=int(local_json.get("max_hp") or 1),
        block=int(local_json.get("block") or 0),
        energy=int(local_json.get("energy") or 0),
        max_energy=3,
        hand=[card_instance_from_json(c) for c in (combat.get("hand") or []) if isinstance(c, dict)],
        draw=[str(c.get("card_id")) for c in (combat.get("draw_cards") or []) if isinstance(c, dict)],
        discard=[],
    )
    # 其它玩家（co-op 伙伴）：只有 HP/格挡/能量，无手牌（/state 不暴露对方手牌）。
    partners: list[PlayerState] = []
    for i, p in enumerate(players_list):
        if isinstance(p, dict) and str(p.get("player_id") or p.get("id")) != local_id:
            others = PlayerState(
                id=str(p.get("player_id") or p.get("id") or f"p{i+1}"),
                hp=int(p.get("current_hp") or p.get("hp") or 0),
                max_hp=int(p.get("max_hp") or 1),
                block=int(p.get("block") or 0),
                energy=int(p.get("energy") or 0),
                max_energy=3,
            )
            partners.append(others)
    players = [local] + partners
    # 药水：从 run.potions[]（occupied 的有 potion_id）读入本地玩家
    run = combat.get("run") if isinstance(combat.get("run"), dict) else {}
    pots = run.get("potions") if isinstance(run.get("potions"), list) else []
    local.potions = [str(p.get("potion_id")) for p in pots
                     if isinstance(p, dict) and p.get("potion_id")]
    enemies: list[EnemyState] = []
    for i, e in enumerate(combat.get("enemies") or []):
        if not isinstance(e, dict):
            continue
        intent_dmg = 0
        is_attack = False
        for int_ in e.get("intents") or []:
            if isinstance(int_, dict) and int_.get("intent_type") == "Attack":
                intent_dmg += int(int_.get("total_damage") or 0)
                is_attack = True
        enemies.append(EnemyState(
            id=str(i), hp=int(e.get("current_hp") or 0), max_hp=int(e.get("max_hp") or 0),
            block=int(e.get("block") or 0), intent_damage=intent_dmg,
            intent_label=e.get("intent"), intent_attack=is_attack,
            move_id=str(e.get("intent") or e.get("move_id") or ""),
            enemy_id=str(e.get("enemy_id") or ""),
        ))
    return BattleState(players=players, enemies=enemies,
                       ctx={"local_id": local_id, "players": list(players), "enemies": list(enemies)})


__all__ = ["from_live_state"]
