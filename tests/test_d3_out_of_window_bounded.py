# -*- coding: utf-8 -*-
"""t24 / D3 —— ★「窗口外计数有界」的回归门 ＋ 「有界 ≠ 脱困」的边界固化。

D3 = 非玩家行动窗口期死锁计数越过阈值却不兜底。
     （窗口没开时 `end_turn` 不在 `available_actions`，`is_player_window_open=False`
       的 guard 静默挡住兜底，但 `_solver_deadlock_count` 已经先 +1 ⇒ 计数越界继续涨。）

修复前实机形态（heuristic_planner.py:1706-1707 源码注释逐字引用 t13 当时日志，
窗口 2026-10-19 09:14–09:15）：
    「09:14:42 死锁 5/3 origin=none 手牌=[] ｜ 09:14:43 死锁 7/3 手牌=[]」
    「09:14:54 5/3 ｜ 09:14:55 7/3 ｜ 09:15:20 5/3 ｜ 09:15:31 5/3（post-reload 6 条）」
★ 分母 3 = `_SOLVER_DEADLOCK_RETRIES`；N = 5、7 > 3 ⇒ **越过阈值继续涨**（语义自相矛盾）。
★ 那些行的**原始文件已被 rotation 覆盖**（20261019 家族最早 11:25:02），
  故本门**不引用**已消失的原始行，而是钉住**结构性**性质（见下）。

本文件钉两件事
--------------
**A. 有界（结构性质，可单测）**
   窗口外计数达 `_SOLVER_OUT_OF_WINDOW_LIMIT` 即**回绕归零**（`:1766`），
   故任一时刻 `N <= LIMIT`；且窗口没开时**不得**累加死锁计数。

**B. ★★ 有界 ≠ 脱困（边界固化，防下游误读）**
   本修复只做到「**不再产出矛盾计数**」+「把『插件没动』与『窗口没开』在日志里区分开」，
   **未**做到「让窗口外局面自己能走出去」。
   ★ 窗口外时我们**没有任何合法动作可发**（`end_turn` 是唯一永远合法的动作；
     它不可用 ⇒ 窗口没开），所以"走出去"必须由**窗口外部的力量**提供：
     mod 侧状态推进 / 连接恢复 / 看门狗重连 —— 即 **D8/t14 的范围**。
   ★ `test_bounded_does_not_imply_escape` 用**行为断言**把这条边界写死：
     即使计数回绕无数次、日志喊了无数次，`_combat_solver_only_outcome` **始终返回 None**
     （= 永远不发动作）。任何"把有界当成已脱困"的下游结论都会与本门冲突。

上游同构（铁律：先查上游是否有意为之）
--------------------------------------
`SolverController.cs:2802-2816` / `:3070-3083`：无当前回合计划时一律
`RequestSearch(...)` 重搜并 `return`，**不静默空转**、也不伪造动作。
⇒ 我方「不计数、不发动作、但**必须说出来**」与上游同向（上游用重搜，我方用可观测告警）。
"""

from __future__ import annotations

import pytest

# 路径引导统一在 tests/conftest.py（此处不再插 sys.path）
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

_OUT_OF_WINDOW_LIMIT = STS2HeuristicPlanner._SOLVER_OUT_OF_WINDOW_LIMIT
_DEADLOCK_RETRIES = STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES
_STALE_LIMIT = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT


# --------------------------------------------------------------------------------------
# 工装
# --------------------------------------------------------------------------------------
def _A(name: str) -> dict:
    return {"type": name, "raw": {"name": name}, "id": name}


def _snapshot(*, with_end_turn: bool, hand_empty: bool = True, turn: int = 5,
              enemy_hp: int = 27) -> dict:
    """窗口**没开**的典型局面：`available_actions` 无 `end_turn`、手牌为空。

    ★ 与实机行一致：「求解器=None/None(target=None) ... 手牌=[]」+`origin=none`。
    """
    actions = [_A("play_card"), _A("save_and_quit")]
    if with_end_turn:
        actions.insert(0, _A("end_turn"))
    return {
        "screen": "COMBAT",
        "raw_state": {
            "run_id": "T24-PROBE",
            "floor": 14,
            "combat": {
                "turn": turn,
                "energy": 0,
                "max_energy": 3,
                "player_hp": 46,
                "player_max_hp": 85,
                "hand": [] if hand_empty else [
                    {"index": 0, "card_id": "STRIKE_IRONCLAD", "name": "打击",
                     "playable": False, "cost": 1, "requires_target": True,
                     "target_type": "AnyEnemy", "valid_target_indices": [0],
                     "upgraded": False},
                ],
                "enemies": [{"index": 0, "enemy_id": "MAWLER", "current_hp": enemy_hp,
                             "max_hp": 76, "is_alive": True, "block": 0}],
                "potions": [],
            },
        },
        "available_actions": actions,
        "classification": {"state_name": "combat", "screen_class": "CombatRoom"},
    }


def _call(planner, snap, *, plan_origin: str = "none"):
    """按实机形态调用兜底函数：无计划（`plan_origin=none`）、无首步。"""
    return planner._combat_solver_only_outcome(
        snapshot=snap,
        available_actions=snap["available_actions"],
        solver_plan=None,
        first_step=None,
        solver_skip_reason="probe: 窗口未开 + 无计划",
        hand=snap["raw_state"]["combat"]["hand"],
        plan_origin=plan_origin,
        plan_fetch_signature=None,
    )


# ======================================================================================
# A. 有界：计数结构上不可能 ≤ LIMIT 被突破
# ======================================================================================
@pytest.mark.unit
def test_out_of_window_count_never_exceeds_limit() -> None:
    """★ D3 核心：连续窗口外 N 次，计数**始终** <= `_SOLVER_OUT_OF_WINDOW_LIMIT`。

    修复前形态是 `死锁 5/3`、`死锁 7/3`（N 可无限涨）。本门钉住"不再越界"。
    """
    planner = STS2HeuristicPlanner()
    observed: list[int] = []
    for _ in range(_OUT_OF_WINDOW_LIMIT * 4 + 3):
        snap = _snapshot(with_end_turn=False)
        _call(planner, snap)
        observed.append(planner._solver_out_of_window_count)
    worst = max(observed)
    assert worst <= _OUT_OF_WINDOW_LIMIT, (
        f"★ 窗口外计数越界：观察到的最大值 = {worst} > LIMIT = {_OUT_OF_WINDOW_LIMIT}\n"
        f"   这正是 D3 的修复前形态（实机 `死锁 5/3`、`死锁 7/3`）"
        f"；序列 = {observed}"
    )


@pytest.mark.unit
def test_out_of_window_counter_wraps_at_limit_rather_than_growing() -> None:
    """★ 达上限即**回绕归零**（`:1766`），故会出现「5/5 → 2/5」而非「5/5 → 6/5」。

    ★ 实机对照（本盘日志 `N.E.K.O_Plugin_sts2_autoplay_20261020.log`）：
        00:05:26 N=1/5 ｜ 00:05:27 N=5/5 ｜ 00:05:28 N=2/5   ← 回绕，非继续涨
    """
    planner = STS2HeuristicPlanner()
    seen_wrap = False
    prev = 0
    for _ in range(_OUT_OF_WINDOW_LIMIT * 3 + 2):
        snap = _snapshot(with_end_turn=False)
        _call(planner, snap)
        cur = planner._solver_out_of_window_count
        if prev == _OUT_OF_WINDOW_LIMIT - 1 and cur == 0:
            seen_wrap = True
        prev = cur
    # 回绕发生在「+1 到 LIMIT 后立刻归零」，故观测点可能直接看到 0 或 1
    assert planner._solver_out_of_window_count <= _OUT_OF_WINDOW_LIMIT
    assert seen_wrap or planner._solver_out_of_window_count < _OUT_OF_WINDOW_LIMIT, (
        "★ 计数未回绕 ⇒ 会一直涨（D3 的越界形态）"
    )


@pytest.mark.unit
def test_out_of_window_does_not_accumulate_deadlock_count() -> None:
    """★ 窗口没开时**不得**累加死锁计数（那不是"求解器无解"，是"轮不到我们"）。"""
    planner = STS2HeuristicPlanner()
    for _ in range(_OUT_OF_WINDOW_LIMIT * 3):
        snap = _snapshot(with_end_turn=False)
        res = _call(planner, snap)
        assert res is None, "窗口外不得发任何动作"
    assert planner._solver_deadlock_count == 0, (
        f"★ 窗口外把死锁计数推到了 {planner._solver_deadlock_count}"
        f"（应恒为 0：窗口未开时 end_turn 不可用，计数无意义）"
    )


@pytest.mark.unit
def test_deadlock_count_is_also_bounded_by_its_own_retries() -> None:
    """★ 另一路计数也须有界：`_solver_deadlock_count` 不得越过 `_SOLVER_DEADLOCK_RETRIES` 而无出口。

    允许它短暂达到 RETRIES（那正是发 end_turn 的条件），但**一旦达到就必须兑现出口**
    （发 end_turn 并归零），不得像修复前那样涨到 5/3、7/3 还不兜底。
    """
    planner = STS2HeuristicPlanner()
    bad_step = {"kind": "play_card", "card_index": 0, "card_id": "NOT_IN_HAND",
                "card_name": "x", "target_index": 0}
    bad_plan = {"line": [{"steps": [bad_step]}]}
    fired = 0
    for _ in range(40):
        snap = _snapshot(with_end_turn=True, hand_empty=False)
        res = planner._combat_solver_only_outcome(
            snapshot=snap, available_actions=snap["available_actions"],
            solver_plan=bad_plan, first_step=bad_step,
            solver_skip_reason="probe", hand=snap["raw_state"]["combat"]["hand"],
            plan_origin="none", plan_fetch_signature=None)
        if res is not None and res.action_type == "end_turn":
            fired += 1
        assert planner._solver_deadlock_count <= _DEADLOCK_RETRIES, (
            f"★ 死锁计数越界 = {planner._solver_deadlock_count} > "
            f"RETRIES = {_DEADLOCK_RETRIES}（修复前形态 `死锁 5/3`/`死锁 7/3`）"
        )
    assert fired > 0, "计数达到 RETRIES 必须兑现出口（发 end_turn），不得只涨不发"


# ======================================================================================
# B. ★★ 有界 ≠ 脱困（边界固化）
# ======================================================================================
@pytest.mark.unit
def test_bounded_does_not_imply_escape() -> None:
    """★★ D3 边界：即使计数回绕无数次，窗口外局面**自己走不出去**。

    修复只做到「不再产出矛盾计数」；**未**做到「让窗口外局面自己能走出去」。
    窗口外时我们**没有任何合法动作可发**（`end_turn` 唯一永远合法；它不可用 ⇒ 窗口没开）
    ⇒ 必须由**窗口外部的力量**推进（mod 侧状态推进 / 连接恢复 / 看门狗重连），
      即 **D8/t14 的范围**。

    ★ 本门的价值：任何把「有界」误读为「已脱困」的下游结论都会在这里冲突。
    """
    planner = STS2HeuristicPlanner()
    actions = []
    for _ in range(_OUT_OF_WINDOW_LIMIT * 5):
        snap = _snapshot(with_end_turn=False)
        res = _call(planner, snap)
        actions.append(None if res is None else res.action_type)
    assert set(actions) == {None}, (
        f"★ 窗口外产出了动作 {set(actions) - {None}} —— 这与「窗口外无合法动作可发」矛盾；\n"
        f"   若本断言失败，说明 D3 的边界描述需要更新（或有新的合法出口被引入）"
    )
    # 计数确实回绕过（有界生效），但**局面没有因此前进**
    assert planner._solver_out_of_window_count <= _OUT_OF_WINDOW_LIMIT
    assert planner._solver_deadlock_count == 0, (
        "窗口外不得借道死锁计数 —— 那是把「轮不到我们」误当「求解器无解」"
    )


@pytest.mark.unit
def test_out_of_window_is_observable_not_silent() -> None:
    """★ 修复的核心价值：把「插件没动」与「窗口没开」在日志里**区分开**（本缺陷最贵的部分 = 它静默）。

    判据：达上限时**必须**留下可观测的告警（`fallback_reason` 含「非玩家行动窗口持续」）。
    """
    planner = STS2HeuristicPlanner()
    recorded: list[str] = []
    orig = planner._combat_log_solver_vs_executed

    def spy(*, fallback_reason="", **kw):
        recorded.append(str(fallback_reason))
        return orig(fallback_reason=fallback_reason, **kw)

    planner._combat_log_solver_vs_executed = spy  # type: ignore[assignment]
    for _ in range(_OUT_OF_WINDOW_LIMIT + 2):
        _call(planner, _snapshot(with_end_turn=False))
    planner._combat_log_solver_vs_executed = orig  # type: ignore[assignment]

    assert recorded, "★ 窗口外全程**没有任何留痕** ⇒ 又回到「静默」形态（D3 最贵的问题）"
    assert any("非玩家行动窗口" in r for r in recorded), (
        f"★ 留痕未点明「窗口没开」；实际留下的是：{recorded[:3]}"
    )


@pytest.mark.unit
def test_return_to_window_clears_out_of_window_count() -> None:
    """★ 回到玩家窗口 ⇒ 窗口外计数清零（它只度量「连续窗口外」的时长）。"""
    planner = STS2HeuristicPlanner()
    for _ in range(3):
        _call(planner, _snapshot(with_end_turn=False))
    assert planner._solver_out_of_window_count > 0

    # 窗口开了（end_turn 回到 available_actions）⇒ 走正常路径、计数清零
    snap = _snapshot(with_end_turn=True, hand_empty=False)
    planner._combat_solver_only_outcome(
        snapshot=snap, available_actions=snap["available_actions"],
        solver_plan=None, first_step=None, solver_skip_reason="probe",
        hand=snap["raw_state"]["combat"]["hand"],
        plan_origin="none", plan_fetch_signature=None)
    assert planner._solver_out_of_window_count == 0, (
        f"回到窗口后计数未清零 = {planner._solver_out_of_window_count}"
    )
