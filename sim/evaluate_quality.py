"""决策效果评测：在不同真实/构造快照上对比 beam=16 vs beam=1（贪心）。

只统计"动作窗口已开"（available_actions 含 play_card / end_turn / use_potion）的局面；
对每局跑跨回合搜索，输出：
  - beam 增益率：beam=16 比 beam=1 更能找到更好线的比例（有真实分歧时才体现）
  - 斩杀命中率：beam=16 能不能找到获胜线
  - 平均玩家 HP / 平均敌人剩余 HP

快照可以是完整 /state envelope（含 agent_view），也可以是只含 combat+run 的 data 对象。
用法： python -m sim.evaluate_quality  [glob...]
"""
from __future__ import annotations

import glob
import json
import os
import sys
from typing import Any

from sim.diff import apply_live_piles, from_live_state
from sim.search import SearchNode, search

# 判定"找到获胜线"：敌人全灭（score 到 VICTORY_BONUS 量级）
_LEETHAL_SCORE = 1e8


def load_snapshot(path: str) -> dict[str, Any] | None:
    """读一个 /state 快照：返回含 combat/run 的 data dict；找不到返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # 非单文档或不可读（如 live-step 产物），跳过

    def find(o: Any) -> dict[str, Any] | None:
        if isinstance(o, dict):
            if "combat" in o and "run" in o:
                return o
            for v in o.values():
                r = find(v)
                if r is not None:
                    return r
        elif isinstance(o, list):
            for v in o:
                r = find(v)
                if r is not None:
                    return r
        return None

    return find(doc)


def is_window_open(data: dict[str, Any]) -> bool:
    avail = {str(a) for a in (data.get("available_actions") or [])}
    return bool(avail & {"play_card", "end_turn", "use_potion"})


def run_one(data: dict[str, Any], *, horizon: int = 2) -> dict[str, Any] | None:
    combat = data.get("combat") if isinstance(data.get("combat"), dict) else None
    run = data.get("run") if isinstance(data.get("run"), dict) else {}
    if not combat:
        return None
    state = from_live_state(combat, run)
    apply_live_piles(state, data)
    if not state.players or not state.players[0].hand:
        return None
    greedy = search(state, horizon=horizon, beam_width=1)
    beamed = search(state, horizon=horizon, beam_width=16)

    def terminal(node: SearchNode) -> dict[str, Any]:
        enemies = node.state.enemies
        alive = [e for e in enemies if e.alive]
        return {
            "score": node.score,
            "lethal": len(alive) == 0,
            "player_hp": node.state.players[0].hp if node.state.players else -1,
            "enemy_hp": sum(e.hp for e in alive),
        }

    t_g, t_b = terminal(greedy), terminal(beamed)
    return {
        "run_id": data.get("run_id"),
        "turn": data.get("turn"),
        "beam_gain": t_b["score"] > t_g["score"] + 1e-6,
        "greedy": t_g,
        "beamed": t_b,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        args = [os.path.join(os.path.dirname(__file__), "..", "tests", "*.json")]
    paths: list[str] = []
    for a in args:
        paths.extend(sorted(glob.glob(a)))
    paths = [p for p in paths if os.path.isfile(p)]

    rows: list[dict[str, Any]] = []
    for p in paths:
        data = load_snapshot(p)
        if not data:
            continue
        if not is_window_open(data):
            print(f"[跳过] {os.path.basename(p)}: 窗口未开 (available={data.get('available_actions')})")
            continue
        r = run_one(data)
        if r is None:
            print(f"[跳过] {os.path.basename(p)}: 无手牌/无法构造")
            continue
        rows.append(r)
        print(f"[评测] {os.path.basename(p):24} 首步win={r['beamed']['lethal']} "
              f"beam增益={r['beam_gain']} 玩家HP={r['beamed']['player_hp']} 敌剩余HP={r['beamed']['enemy_hp']}")

    n = len(rows)
    if n == 0:
        print("无有效局面可评测（都是未开窗口/无法构造）")
        return 0
    beam_gain = sum(1 for r in rows if r["beam_gain"]) / n * 100
    lethal = sum(1 for r in rows if r["beamed"]["lethal"]) / n * 100
    avg_hp = sum(r["beamed"]["player_hp"] for r in rows) / n
    avg_enemy = sum(r["beamed"]["enemy_hp"] for r in rows) / n
    print("\n=== 汇总（窗口已开且可构造的 %d 局） ===" % n)
    print(f"  beam 增益率 (beam16 > beam1): {beam_gain:.0f}%")
    print(f"  beam16 斩杀命中率:           {lethal:.0f}%")
    print(f"  beam16 平均玩家HP:           {avg_hp:.1f}")
    print(f"  beam16 平均敌人剩余HP:       {avg_enemy:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
