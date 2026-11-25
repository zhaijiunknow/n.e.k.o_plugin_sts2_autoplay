# -*- coding: utf-8 -*-
"""自动游玩历史页的数据层：把猫娘记忆里的"每局"整理成前端能直接渲染的 JSON。

为什么要落一个静态快照：插件 UI 是通过插件服务器按静态文件提供的
（`GET /plugin/{plugin_id}/ui/<path>`），前端没有"直接读插件内存"的通道。
所以每次局末把 `catgirl_memory.json` 里的记录投影成一份紧凑 JSON 放进插件自带的
`static/` 目录，页面 `fetch` 它即可 —— 权威数据仍在 `catgirl_memory.json`，这份只是视图缓存，
升级插件被覆盖也无所谓（下次局末会重新生成）。
"""

from __future__ import annotations

import json
import os
from typing import Any

HISTORY_FILENAME = "autoplay_history.json"
# 页面上最多展示多少局（越靠后越新）
_MAX_RUNS = 50
# 每局最多列多少条决策
_MAX_DECISIONS = 60
# 每局最多列多少个房间（路线图）
_MAX_ROUTE_ROOMS = 80
# 每局最多列多少步对照复盘
_MAX_REVIEW_STEPS = 10


def build_history_payload(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """把 catgirl_memory 的 runs 投影成前端 JSON（新的一局在前）。"""
    out: list[dict[str, Any]] = []
    for run in reversed([r for r in (runs or []) if isinstance(r, dict)][-_MAX_RUNS:]):
        hp = run.get("hp") if isinstance(run.get("hp"), dict) else {}
        heaviest = hp.get("heaviest") if isinstance(hp.get("heaviest"), list) else []
        out.append(
            {
                "run_id": str(run.get("run_id") or ""),
                "character": str(run.get("character") or ""),
                "outcome": str(run.get("outcome") or ""),
                "floor": run.get("floor"),
                "act": run.get("act"),
                "ended_at": run.get("ended_at"),
                "hp": {
                    "total_lost": hp.get("total_lost"),
                    "loss_events": hp.get("loss_events"),
                    "min_hp": hp.get("min_hp"),
                    "max_hp": hp.get("max_hp"),
                    "final_hp": hp.get("final_hp"),
                },
                "heaviest": [
                    {
                        # 房间标识（敌人组合 / 事件 id）优先：尖塔地图随机，"第 N 层"跨局不可复用。
                        "room": str(item.get("room") or ""),
                        "screen": str(item.get("screen") or ""),
                        "floor": item.get("floor"),
                        "lost": item.get("lost"),
                        "hp_before": item.get("hp_before"),
                        "hp_after": item.get("hp_after"),
                    }
                    for item in heaviest
                    if isinstance(item, dict)
                ],
                "lessons": [str(item) for item in (run.get("lessons") or []) if str(item).strip()],
                "preferences": [str(item) for item in (run.get("preferences") or []) if str(item).strip()],
                # 旧版本的 LLM 反思（v0.8.1 及以前的记录里才有）。
                "reflection": str(run.get("reflection") or ""),
                # 对照复盘：玩家亲手打的步里，和当时的推荐差得最多的几步，以及模型的讲解。
                "review": _review_view(run.get("review")),
                # 逐房间的路线图：每个房间的损血与收获（顺序即本局的走法）。
                "route": [
                    {
                        "room": str(item.get("room") or ""),
                        "screen": str(item.get("screen") or ""),
                        "floor": item.get("floor"),
                        "hp_start": item.get("hp_start"),
                        "hp_end": item.get("hp_end"),
                        "lowest_hp": item.get("lowest_hp"),
                        "hp_lost": item.get("hp_lost"),
                        "gold_gained": item.get("gold_gained"),
                        "cards_gained": [str(x) for x in (item.get("cards_gained") or [])],
                        "relics_gained": [str(x) for x in (item.get("relics_gained") or [])],
                        "potions_gained": [str(x) for x in (item.get("potions_gained") or [])],
                    }
                    for item in (run.get("route") or [])[:_MAX_ROUTE_ROOMS]
                    if isinstance(item, dict)
                ],
                "decisions": [
                    {
                        "screen": str(item.get("screen") or ""),
                        "action_type": str(item.get("action_type") or ""),
                        "reason": str(item.get("reason") or ""),
                        "step_count": item.get("step_count"),
                    }
                    for item in (run.get("decisions") or [])[-_MAX_DECISIONS:]
                    if isinstance(item, dict)
                ],
            }
        )
    summary = {
        "runs": len(out),
        "victories": sum(1 for run in out if run["outcome"] == "victory"),
        "defeats": sum(1 for run in out if run["outcome"] == "defeat"),
        "best_floor": max([int(run["floor"] or 0) for run in out], default=0),
        "total_hp_lost": sum(int((run["hp"].get("total_lost") or 0)) for run in out),
    }
    return {"summary": summary, "runs": out}


def _review_view(review: Any) -> dict[str, Any] | None:
    if not isinstance(review, dict) or not review.get("compared"):
        return None
    return {
        "compared": review.get("compared"),
        "agreed": review.get("agreed"),
        "network_steps": review.get("network_steps"),
        "planner_steps": review.get("planner_steps"),
        "explanation": str(review.get("explanation") or ""),
        "steps": [
            {
                "source": str(step.get("source") or ""),
                "floor": step.get("floor"),
                "round": step.get("round"),
                "room": str(step.get("room") or ""),
                "situation": str(step.get("situation") or ""),
                "chosen": str(step.get("chosen") or ""),
                "recommended": str(step.get("recommended") or ""),
                "p_chosen": step.get("p_chosen"),
                "p_recommended": step.get("p_recommended"),
            }
            for step in (review.get("steps") or [])[:_MAX_REVIEW_STEPS]
            if isinstance(step, dict)
        ],
    }


def static_dir() -> str:
    """插件自带的 static 目录（本文件所在包的旁边）。"""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def write_history_view(runs: list[dict[str, Any]], *, directory: str | None = None) -> str | None:
    """把历史快照写进插件 static 目录；返回写入路径（失败返回 None，绝不抛）。"""
    target_dir = directory or static_dir()
    path = os.path.join(target_dir, HISTORY_FILENAME)
    try:
        os.makedirs(target_dir, exist_ok=True)
        payload = build_history_payload(runs)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


__all__ = ["HISTORY_FILENAME", "build_history_payload", "static_dir", "write_history_view"]
