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


def _normalize_powers(powers_applied: Any) -> list[tuple[str, int]]:
    """powers_applied 可能是 list[str] / list[{id,amount}] / [{power_id,amount}]。"""
    out: list[tuple[str, int]] = []
    if not powers_applied:
        return out
    for item in powers_applied:
        if isinstance(item, str):
            out.append((item, 1))
        elif isinstance(item, dict):
            pid = (item.get("id") or item.get("power_id") or item.get("power") or "").upper()
            amt = int(item.get("amount") or item.get("stacks") or item.get("power") or 1)
            if pid:
                out.append((pid, amt))
        elif isinstance(item, list) and len(item) >= 2:
            out.append((str(item[0]).upper(), int(item[1])))
    return out


def card_from_json(entry: dict[str, Any]) -> CardInstance:
    return CardInstance(
        card_id=str(entry.get("id") or ""),
        name=str(entry.get("name") or ""),
        cost=int(entry.get("cost") or 0),
        card_type=str(entry.get("type") or ""),
        target=str(entry.get("target") or "Self"),
        damage=int(entry.get("damage") or 0),
        block=int(entry.get("block") or 0),
        hit_count=int(entry.get("hit_count") or 0),
        cards_draw=int(entry.get("cards_draw") or 0),
        energy_gain=int(entry.get("energy_gain") or 0),
        hp_loss=int(entry.get("hp_loss") or 0),
        powers_applied=_normalize_powers(entry.get("powers_applied")),
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
