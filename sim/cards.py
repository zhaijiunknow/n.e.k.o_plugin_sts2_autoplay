"""从游戏数据（cards.json / powers.json）装载卡牌与 Power 元数据。

关键：卡牌是"数据"，不是代码。这里把 cards.json 的每个字段映射成 CardInstance，
数值原样从数据读；powers.json 提供 Power 的元数据（id/type/stack_type）。
加了新卡（用已有字段）= 纯数据，零代码。
"""
from __future__ import annotations

import json
import os
from typing import Any

from .state import CardInstance

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "game_mod", "mcp_server", "data", "eng")
_CARDS = os.path.join(_DATA_DIR, "cards.json")
_POWERS = os.path.join(_DATA_DIR, "powers.json")


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _first(effects: list[Any] | None) -> Any:
    return effects[0] if effects else None


# 卡面 powers_applied 用 uppercase 短名（如 VULNERABLE），但 sim 伤害/结算查的是
# lower + _power 后缀（如 vulnerable_power）。这里统一映射，否则卡施加的易伤/虚弱/力量不生效。
_POWER_KEYS: dict[str, str] = {
    "VULNERABLE": "vulnerable_power",
    "WEAK": "weak_power",
    "STRENGTH": "strength_power",
    "DEXTERITY": "dexterity_power",
    "POISON": "poison_power",
    "INTANGIBLE": "intangible_power",
    "BUFFER": "buffer_power",
    "RITUAL": "ritual_power",
    "REGEN": "regen_power",
    "PLATED_ARMOR": "plated_armor_power",
    "ENERGY_NEXT_TURN": "energy_next_turn_power",
    "BLOCK_NEXT_TURN": "block_next_turn_power",
    "DRAW_CARDS_NEXT_TURN": "draw_cards_next_turn_power",
    "STAR_NEXT_TURN": "star_next_turn_power",
}


def _norm_power_key(pid: str) -> str:
    return _POWER_KEYS.get(str(pid).upper(), str(pid).lower())


def _normalize_powers(powers_applied: Any) -> list[tuple[str, int]]:
    """powers_applied 可能是 list[str] / list[{id,amount}] / [{power_id,amount}]。"""
    out: list[tuple[str, int]] = []
    if not powers_applied:
        return out
    for item in powers_applied:
        if isinstance(item, str):
            out.append((_norm_power_key(item), 1))
        elif isinstance(item, dict):
            pid = (item.get("id") or item.get("power_id") or item.get("power") or "").upper()
            amt = int(item.get("amount") or item.get("stacks") or item.get("power") or 1)
            if pid:
                out.append((_norm_power_key(pid), amt))
        elif isinstance(item, list) and len(item) >= 2:
            out.append((_norm_power_key(str(item[0])), int(item[1])))
    return out


# 球动作卡（缺陷）：cards.json 没有显式球字段，按卡 id 特判。action: channel|evoke
_ORB_CARDS: dict[str, list[tuple[str, str, int]]] = {
    "ZAP": [("channel", "LIGHTNING", 1)],
    "DUALCAST": [("evoke", "", 2)],
    "COLD_SNAP": [("channel", "FROST", 1)],
    "DISCHARGE": [("evoke", "", 0)],
}


def _next_turn_energy(entry: dict[str, Any]) -> int:
    """若卡的"能量"是下回合才给（如 CHARGE_BATTERY），返回能量数；否则 0。

    游戏本身把这种延迟能量建模范成 ENERGY_NEXT_TURN_POWER，所以这里检测到就转成 power。
    """
    desc = str(entry.get("description") or "").lower()
    if "next" in desc and "energy" in desc and entry.get("energy_gain"):
        return int(entry["energy_gain"])
    return 0


def card_from_json(entry: dict[str, Any]) -> CardInstance:
    cid = str(entry.get("id") or "")
    energy = int(entry.get("energy_gain") or 0)
    powers = _normalize_powers(entry.get("powers_applied"))
    delayed = _next_turn_energy(entry)
    if delayed:
        # 下回合能量：不当作即时 energy_gain，而是挂到 ENERGY_NEXT_TURN_POWER 上
        powers = powers + [("ENERGY_NEXT_TURN_POWER", delayed)]
        energy = 0
    return CardInstance(
        card_id=cid,
        name=str(entry.get("name") or ""),
        cost=int(entry.get("cost") or 0),
        star_cost=int(entry.get("star_cost") or 0),
        card_type=str(entry.get("type") or ""),
        target=str(entry.get("target") or "Self"),
        damage=int(entry.get("damage") or 0),
        block=int(entry.get("block") or 0),
        hit_count=int(entry.get("hit_count") or 0),
        cards_draw=int(entry.get("cards_draw") or 0),
        energy_gain=energy,
        hp_loss=int(entry.get("hp_loss") or 0),
        powers_applied=powers,
        keywords=list(entry.get("keywords") or []),
        orb_action=list(_ORB_CARDS.get(cid) or []),
    )


def load_cards() -> dict[str, CardInstance]:
    """id -> CardInstance（读游戏数据）。"""
    raw = _read_json(_CARDS)
    entries = raw if isinstance(raw, list) else list(raw.values())
    return {c.card_id: c for c in (card_from_json(e) for e in entries if isinstance(e, dict)) if c.card_id}


def load_powers() -> dict[str, dict[str, Any]]:
    """id(upper) -> power 元数据。"""
    raw = _read_json(_POWERS)
    entries = raw if isinstance(raw, list) else list(raw.values())
    return {str(e["id"]).upper(): e for e in entries if isinstance(e, dict) and e.get("id")}


# 缓存
_CARDS_CACHE: dict[str, CardInstance] | None = None
_POWERS_CACHE: dict[str, dict[str, Any]] | None = None


def get_cards() -> dict[str, CardInstance]:
    global _CARDS_CACHE
    if _CARDS_CACHE is None:
        _CARDS_CACHE = load_cards()
    return _CARDS_CACHE


def get_powers() -> dict[str, dict[str, Any]]:
    global _POWERS_CACHE
    if _POWERS_CACHE is None:
        _POWERS_CACHE = load_powers()
    return _POWERS_CACHE


def card(card_id: str) -> CardInstance | None:
    return get_cards().get(card_id.upper())


__all__ = ["card_from_json", "load_cards", "load_powers", "get_cards", "get_powers", "card"]
