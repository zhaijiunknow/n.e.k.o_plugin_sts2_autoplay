"""把 sim（跨回合beam搜索 + 已验证模拟器）接进插件战斗决策。

从插件快照（raw_state.combat + run）构造模拟器战场，跑 search，
给出"这一步打哪张/打谁/是否用牌"的推荐。只推荐，不出牌（执行仍由插件 action_engine 走）。
若模拟器认为无推荐（无可用战斗卡），返回 None，由外层 heuristic 兜底。
"""
from __future__ import annotations

from typing import Any

from sim.diff import apply_live_piles, from_live_state
from sim.search import first_recommendation, search


def _resolve_potion_option_index(run: dict[str, Any], potion_id: str | None) -> int | None:
    """用 potion_id 回查 raw.run.potions[] 的真实下标（含空槽），且要求 can_use。

    sim 的 player.potions 是压缩过的（只留占用槽），而 mod 的 use_potion 用完整下标，
    所以必须回查，不能用压缩后的列表下标。
    """
    if not potion_id:
        return None
    for p in run.get("potions") or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("potion_id") or "") != potion_id:
            continue
        if not p.get("can_use"):
            return None
        if isinstance(p.get("index"), int):
            return p["index"]
    return None


def sim_recommend(snapshot: dict[str, Any], *, horizon: int = 2, beam_width: int = 10) -> dict[str, Any] | None:
    raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else None
    if not combat:
        return None

    run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    state = from_live_state(combat, run)

    player = state.players[0] if state.players else None
    if player is None or not player.hand:
        return None

    # 灌抽/弃/消耗堆（agent_view 精确 piles 优先）；from_live_state 已吞 orbs/powers/药水/遗物。
    apply_live_piles(state, raw)

    best = search(state, horizon=horizon, beam_width=beam_width)
    rec = first_recommendation(best, state)
    if not rec:
        return None

    if rec.get("action") == "play_card" and rec.get("card_index") is not None:
        return rec

    if rec.get("action") == "use_potion":
        option_index = _resolve_potion_option_index(run, rec.get("potion_id"))
        if option_index is None:
            return None
        rec.pop("potion_id", None)
        rec["option_index"] = option_index
        return rec

    return None


__all__ = ["sim_recommend"]
