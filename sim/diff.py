"""把 mod 的 /state.combat 快照灌成模拟器状态（差分验证用）。"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .cards import card as card_by_id
from .state import BattleState, CardInstance, EnemyState, Orb, PlayerState


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


def _norm_orb(name: str) -> str:
    """live 的 orb_id 带 _ORB 后缀（如 LIGHTNING_ORB），归一到 effects 用的裸 id。"""
    return str(name or "").upper().replace("_ORB", "")


def _delayed_energy(base: CardInstance | None) -> bool:
    """base 是否带"下回合能量"（ENERGY_NEXT_TURN_POWER）。"""
    if base is None:
        return False
    return any(_norm_power(p) == "energy_next_turn_power" for p, _ in base.powers_applied)


def _card_id(card: Any) -> str | None:
    """拿到卡的 id：raw dict 用 card_id，sim 的 CardInstance 用 .card_id。"""
    if isinstance(card, dict):
        return card.get("card_id")
    return getattr(card, "card_id", None)


def _draw_from_deck(hand: list[Any], deck: list[Any]) -> list[str]:
    """用 run.deck 减掉当前手牌推 draw 堆（card_id 队列）。仅做兜底。"""
    in_hand = Counter(_card_id(c) for c in hand if _card_id(c))
    draw: list[str] = []
    for c in deck:
        cid = _card_id(c)
        if not cid:
            continue
        if in_hand[cid] > 0:
            in_hand[cid] -= 1
        else:
            draw.append(str(cid))
    return draw


def apply_live_piles(state: BattleState, raw: dict[str, Any]) -> None:
    """把 agent_view.combat 的精确 piles（draw/discard/exhaust，各带 card_id）灌进本地玩家。

    跨回合预测准确的关键：deck-minus-hand 会把弃牌堆当抽牌堆，产生假胜利。缺 agent_view 才退回 deck 推。
    """
    player = state.players[0] if state.players else None
    if player is None:
        return
    av = raw.get("agent_view") if isinstance(raw.get("agent_view"), dict) else {}
    avc = av.get("combat") if isinstance(av.get("combat"), dict) else {}
    draw = [_card_id(c) for c in avc.get("draw_cards") or [] if _card_id(c)]
    if draw:
        player.draw = draw
        player.discard = [_card_id(c) for c in avc.get("discard_cards") or [] if _card_id(c)]
        player.exhausted = [_card_id(c) for c in avc.get("exhaust_cards") or [] if _card_id(c)]
        return
    run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    deck = run.get("deck") if isinstance(run.get("deck"), list) else []
    if deck:
        player.draw = _draw_from_deck(player.hand, deck)


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
    # 快照里 powers 通过动态值如 VulnerablePower/WeakPower 出现；Damage/Block/Cards/Energy/HpLoss 是数值不是 power。
    for name, val in ((k, v) for dv in (card.get("dynamic_values") or []) if isinstance(dv, dict) for k, v in [(dv.get("name"), dv.get("current_value") or dv.get("base_value") or 0)] if isinstance(k, str) and k and k not in ("Damage", "Block", "Cards", "Repeat", "Energy", "HpLoss")):
        if name and val:
            powers.append((_norm_power(name), int(val)))
    # live 动态值没给 buff/debuff 时，退回静态卡定义的 powers_applied（球/增益/状态牌靠它分类）
    if not powers and base is not None:
        powers = list(base.powers_applied)
    # base 若带"下回合能量"power（如充电），live 动态值里没有则并入（它不是即时能量）。
    if base is not None and not any(_norm_power(p) == "energy_next_turn_power" for p, _ in powers):
        for p, amt in base.powers_applied:
            if _norm_power(p) == "energy_next_turn_power":
                powers.append((_norm_power(p), amt))
    return CardInstance(
        card_id=str(card.get("card_id") or ""),
        name=str(card.get("name") or (base.name if base else "")),
        cost=int(card.get("energy_cost") or (base.cost if base else 0)),
        star_cost=int(card.get("star_cost") or (base.star_cost if base else 0)),
        card_type=str(card.get("card_type") or (base.card_type if base else "")),
        target=str(card.get("target_type") or (base.target if base else "Self")),
        damage=dmg,
        block=blk,
        hit_count=int(card.get("hit_count") or (base.hit_count if base else 0)),
        cards_draw=_dynamic_value(card, "Cards") or (base.cards_draw if base else 0),
        energy_gain=0 if _delayed_energy(base) else (_dynamic_value(card, "Energy") or (base.energy_gain if base else 0)),
        hp_loss=_dynamic_value(card, "HpLoss") or (base.hp_loss if base else 0),
        powers_applied=powers,
        keywords=list(base.keywords) if base else [],
        orb_action=list(base.orb_action) if base else [],
    )


def from_live_state(combat: dict[str, Any], run: dict[str, Any] | None = None) -> BattleState:
    # 本地玩家：读 combat.player（有手牌/能量/球/Power）+ combat.players 里的 HP/格挡。
    players_list = combat.get("players") or []
    local_json = combat.get("player") or {}
    local_id = str(local_json.get("id") or local_json.get("player_id") or "p0")
    # run 在 /state 里是 combat 的兄弟键，从入参读（或退回 combat.run）。
    run = run if isinstance(run, dict) else (combat.get("run") if isinstance(combat.get("run"), dict) else {})
    local = PlayerState(
        id=local_id,
        hp=int(local_json.get("current_hp") or local_json.get("hp") or 0),
        max_hp=int(local_json.get("max_hp") or 1),
        block=int(local_json.get("block") or 0),
        energy=int(local_json.get("energy") or 0),
        stars=int(local_json.get("stars") or 0),
        max_energy=int(local_json.get("max_energy") or run.get("max_energy") or 3),
        hand=[card_instance_from_json(c) for c in (combat.get("hand") or []) if isinstance(c, dict)],
        draw=[str(c.get("card_id")) for c in (combat.get("draw_cards") or []) if isinstance(c, dict)],
        discard=[],
    )
    # Power：live 的 combat.player.powers[]（power_id UPPER_SNAKE + amount），归一到 lower。
    local.powers = {
        _norm_power(p["power_id"]): int(p.get("amount") or 0)
        for p in (local_json.get("powers") or [])
        if isinstance(p, dict) and p.get("power_id")
    }
    # 球：live 的 orbs[]（orb_id 带 _ORB 后缀），被动/激发值归一到 Orb。
    local.orbs = [
        Orb(_norm_orb(o.get("orb_id") or "LIGHTNING"),
            passive=int(o.get("passive_value") or 0),
            evoke=int(o.get("evoke_value") or 0))
        for o in (local_json.get("orbs") or [])
        if isinstance(o, dict) and o.get("orb_id")
    ]
    local.focus = int(local_json.get("focus") or 0)
    local.orb_capacity = int(local_json.get("orb_capacity") or 0)
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
                max_energy=int(run.get("max_energy") or 3),
                focus=int(p.get("focus") or 0),
            )
            partners.append(others)
    players = [local] + partners
    # 药水：从 run.potions[]（occupied 的有 potion_id）读入本地玩家
    pots = run.get("potions") if isinstance(run.get("potions"), list) else []
    local.potions = [str(p.get("potion_id")) for p in pots
                     if isinstance(p, dict) and p.get("potion_id")]
    rels = run.get("relics") if isinstance(run.get("relics"), list) else []
    local.relics = [str(r.get("relic_id")) for r in rels if isinstance(r, dict) and r.get("relic_id")]
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
            block=int(e.get("block") or 0),
            powers={_norm_power(p["power_id"]): int(p.get("amount") or 0)
                    for p in (e.get("powers") or [])
                    if isinstance(p, dict) and p.get("power_id")},
            intent_damage=intent_dmg,
            intent_label=e.get("intent"), intent_attack=is_attack,
            move_id=str(e.get("intent") or e.get("move_id") or ""),
            enemy_id=str(e.get("enemy_id") or ""),
        ))
    return BattleState(players=players, enemies=enemies,
                       ctx={"local_id": local_id, "players": list(players), "enemies": list(enemies)})


__all__ = ["from_live_state", "apply_live_piles"]
