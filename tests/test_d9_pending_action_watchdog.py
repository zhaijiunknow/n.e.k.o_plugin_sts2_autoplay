"""t20/D9 回归门：mod 回 `status="pending"` 且局面未变时，不再当成「已执行」。

## 缺陷（实测）
`action_engine.execute` 在 `client.execute_action(...)` 之后**无条件**返回
`{"status": "ok", ...}`，而 mod 的真实状态被埋在 `result["result"]["status"]` 里，
`service.execute_operation` 只检查外层 `result.get("status") == "ok"`（必真）⇒
mod 回 `pending, stable=false`（药水被游戏侧 `Cancel()`、槽位纹丝不动）也被视为已执行
⇒ `solver_step_index` 空耗 ⇒ 步骤用尽 ⇒ 重取 ⇒ 求解器又给同一建议 ⇒ **无限循环**。
实测 run22 X9XP0L8HZWF7：循环 **7347** 次、卡死 **5.88 小时**（16:51:13 → 22:44:06）。

## 本文件覆盖的判据
1. 派发后**确实读取** mod 的 status（`_mod_action_status` 能穿透到 `result["result"]`）；
2. `pending` + 同一指纹连续 N 次 ⇒ 判无效、转入**既有** end_turn 破局路径；
3. **不误伤**：`completed` 不算无效；`pending` 但**局面在变**不算无效；
4. 同一 turn_key 连续 3 次 `use_potion` 而药水未减少 ⇒ 明确告警（不静默）。
"""
from __future__ import annotations

import logging
from typing import Any

from plugin.plugins.sts2_autoplay import loop_runner as lr
from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


def _snapshot(*, enemy_hp: int = 30, potions: int = 2, turn: int = 5) -> dict[str, Any]:
    """构造一份最小但**字段形状真实**的 snapshot（指纹取自 raw_state）。"""
    return {
        "run_id": "TESTRUN",
        "floor": 14,
        "turn": turn,
        "raw_state": {
            "run_id": "TESTRUN",
            "turn": turn,
            "combat": {
                "turn": turn,
                "player": {"energy": 0, "current_hp": 48},
                "hand": [{"card_id": "SETUP_STRIKE", "playable": False}],
                "enemies": [{"enemy_id": "MAWLER", "current_hp": enemy_hp, "is_alive": True}],
                "potions": (
                    [{"potion_id": "EXPLOSIVE_AMPOULE"}, {"potion_id": "CLARITY"}] if potions == 2
                    else ([{"potion_id": "EXPLOSIVE_AMPOULE"}] if potions == 1 else [])
                ),
            },
        },
    }


class _Planner:
    """最小 planner 替身：只需要 `_SOLVER_DEADLOCK_RETRIES` 与两个计数槽（本修复要写它们）。"""

    _SOLVER_DEADLOCK_RETRIES = 3

    def __init__(self) -> None:
        self._solver_deadlock_signature = ""
        self._solver_deadlock_count = 0


def _mk_service() -> STS2AutoplayService:
    """不走 `__init__`（那会建一堆真实依赖），只装配本判据需要的字段。"""
    svc = STS2AutoplayService.__new__(STS2AutoplayService)
    svc._state = STS2RuntimeState()
    svc._planner = _Planner()
    svc.logger = logging.getLogger("test.t20")
    return svc


# ── 1. status 确实被读取 ────────────────────────────────────────────────────

def test_mod_action_status_reads_the_inner_data_status():
    """`_mod_action_status` 必须穿透到 mod 的真实 status（而不是外层恒 ok）。"""
    svc = _mk_service()
    # 真实结构：_request 已拆出 data ⇒ action_engine 放进 result["result"]
    assert svc._mod_action_status({"status": "ok", "result": {"status": "pending"}}) == "pending"
    assert svc._mod_action_status({"status": "ok", "result": {"status": "completed"}}) == "completed"
    # 取不到 ⇒ 空串（未知，不据此判失败）
    assert svc._mod_action_status({"status": "ok", "result": {}}) == ""
    assert svc._mod_action_status({"status": "ok"}) == ""
    assert svc._mod_action_status({"status": "ok", "result": "not-a-dict"}) == ""


# ── 2. 核心：pending + 指纹未变 ⇒ 计数 → 转 end_turn 破局路径 ───────────────

def test_pending_with_unchanged_state_counts_up_and_is_not_treated_as_executed():
    """同一指纹下连续 pending ⇒ 每次都不算「已执行」，计数递增。"""
    svc = _mk_service()
    snap = _snapshot(enemy_hp=30)
    for i in range(1, svc._planner._SOLVER_DEADLOCK_RETRIES):
        ok = svc._note_action_status(action_type="play_card", mod_status="pending", snapshot=snap)
        assert ok is False, f"第 {i} 次「pending 且未变」必须判为无效"
        assert svc._state.invalid_action_count == i


def test_repeated_invalid_actions_hand_over_to_the_existing_end_turn_breaker():
    """★ 核心：连续 N(=3) 次 ⇒ 把 planner 顶到阈值，由**既有**破局路径发 end_turn。"""
    svc = _mk_service()
    snap = _snapshot(enemy_hp=30)
    retries = svc._planner._SOLVER_DEADLOCK_RETRIES

    for _ in range(retries):
        svc._note_action_status(action_type="use_potion", mod_status="pending", snapshot=snap)

    # 必须复用既有机制：计数被顶到阈值、指纹对齐 ⇒ planner 下一 tick 会发破局 end_turn
    assert svc._planner._solver_deadlock_count == retries, (
        "★ 必须把既有 `_solver_deadlock_count` 顶到 `_SOLVER_DEADLOCK_RETRIES`，"
        "否则 planner 的 end_turn 破局永远不会触发（复用既有机制，不新造动作）"
    )
    assert svc._planner._solver_deadlock_signature != "", "指纹必须对齐，否则会被判成「局面变了」而清零"

    from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

    assert STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES == retries


# ── 3. 不误伤正常路径 ──────────────────────────────────────────────────────

def test_completed_actions_are_never_counted_as_invalid():
    """正常 completed 的动作绝不计入（契约明确要求）。"""
    svc = _mk_service()
    snap = _snapshot(enemy_hp=30)
    for _ in range(10):
        assert svc._note_action_status(
            action_type="play_card", mod_status="completed", snapshot=snap
        ) is True
    assert svc._state.invalid_action_count == 0, "★ completed 不得被计成无效动作"
    # ★ 别名与大小写归一：`_mod_action_status` 已 lower()，这里直接传规范化值也应被认。
    for alias in ("ok", "success", "completed"):
        svc._note_action_status(action_type="play_card", mod_status=alias, snapshot=snap)
    assert svc._state.invalid_action_count == 0, "★ 别名必须也认"
    # 真实路径下 `_mod_action_status` 会做 lower/strip，验证它确实如此：
    assert svc._mod_action_status({"status": "ok", "result": {"status": " COMPLETED "}}) == "completed"


def test_pending_with_changing_state_is_a_legitimate_transition():
    """★ 不误伤：pending 但**局面在变**（长动画等合法过渡）⇒ 有效、计数清零。

    ★ 判据的精确语义：第一次 pending 没有"上一次指纹"可比 ⇒ 归入无效计数（保守：它确实
      可能是 D9）；**一旦后续观察到局面变化**，就必须立刻清零并算作合法过渡。
      这样既不会漏掉"一上来就卡死"，也不会误伤"长动画中反复 pending"。
    """
    svc = _mk_service()
    # 首次 pending：无基线可比 ⇒ 计 1
    assert svc._note_action_status(
        action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=30)
    ) is False
    assert svc._state.invalid_action_count == 1
    # 局面变了（敌人掉血）⇒ 合法过渡，清零
    assert svc._note_action_status(
        action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=20)
    ) is True, "★ pending 但局面在变必须算有效，否则会误伤长动画过渡"
    assert svc._state.invalid_action_count == 0
    # 再变一次也必须继续算有效（连续变化 = 长动画全程都不该计入）
    assert svc._note_action_status(
        action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=15)
    ) is True
    assert svc._state.invalid_action_count == 0


def test_unknown_status_is_not_punished():
    """取不到 status ⇒ 不惩罚（保守，避免误伤旧版 mod / 非战斗动作）。"""
    svc = _mk_service()
    snap = _snapshot()
    for _ in range(10):
        assert svc._note_action_status(action_type="play_card", mod_status="", snapshot=snap) is True
    assert svc._state.invalid_action_count == 0


def test_a_progressing_board_clears_a_running_invalid_streak():
    """局面一旦真的动了，之前的连续无效计数必须清零（否则会攒到误触发）。"""
    svc = _mk_service()
    svc._note_action_status(action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=30))
    svc._note_action_status(action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=30))
    assert svc._state.invalid_action_count == 2
    svc._note_action_status(action_type="play_card", mod_status="pending", snapshot=_snapshot(enemy_hp=25))
    assert svc._state.invalid_action_count == 0, "局面变了 ⇒ 计数必须清零"


# ── 4. 药水「派发了但没消耗」可观测性 ────────────────────────────────────────

def test_three_ineffective_potion_uses_emit_an_explicit_warning(caplog):
    """★ 契约第 4 条：同一 turn_key 连续 3 次 use_potion 而药水未减少 ⇒ 明确告警。"""
    svc = _mk_service()
    snap = _snapshot(potions=1)
    with caplog.at_level(logging.WARNING):
        svc._note_potion_maybe_no_effect(snap, 1)  # 1
        svc._note_potion_maybe_no_effect(snap, 1)  # 2
        assert not [r for r in caplog.records if "sts2_d9" in r.getMessage()], (
            "第 1、2 次不应告警（避免噪声）"
        )
        svc._note_potion_maybe_no_effect(snap, 1)  # 3 ⇒ 告警
    hits = [r for r in caplog.records if "sts2_d9" in r.getMessage()]
    assert hits, "★ 连续 3 次 use_potion 无效必须留下明确告警（而非静默）"
    assert "未减少" in hits[0].getMessage()


def test_a_consumed_potion_resets_the_warning_counter(caplog):
    """药水真的减少了 ⇒ 计数与告警标志清零。"""
    svc = _mk_service()
    before = _snapshot(potions=2)
    after = _snapshot(potions=1)
    with caplog.at_level(logging.WARNING):
        svc._note_potion_maybe_no_effect(before, 2)
        svc._note_potion_maybe_no_effect(before, 2)
        svc._note_potion_maybe_no_effect(after, 2)  # 药水消耗 ⇒ 清零
        assert svc._state.potion_no_effect_count == 0
        svc._note_potion_maybe_no_effect(before, 2)
    assert not [r for r in caplog.records if "sts2_d9" in r.getMessage()], (
        "消耗过就必须重新计数，不得立刻告警"
    )


def test_count_potions_reads_the_real_state_shape():
    """`_count_potions` 必须能读真实 `raw_state.combat.potions` 形状。"""
    assert STS2AutoplayService._count_potions(_snapshot(potions=2)) == 2
    assert STS2AutoplayService._count_potions(_snapshot(potions=1)) == 1
    assert STS2AutoplayService._count_potions(_snapshot(potions=0)) == 0
    assert STS2AutoplayService._count_potions(None) == 0
    assert STS2AutoplayService._count_potions({"raw_state": {}}) == 0
    # 实测探针里的槽位形状 [[0,None,false],[1,"EXPLOSIVE_AMPOULE",true]] 等价物：
    assert STS2AutoplayService._count_potions(
        {"raw_state": {"combat": {"potions": [{"potion_id": None}, {"potion_id": "EXPLOSIVE_AMPOULE"}]}}}
    ) == 1


# ── 5. 与既有死锁指纹的一致性（防止两侧各写一份而漂移）────────────────────

def test_signature_matches_the_shared_loop_runner_function():
    """D9 判据用的指纹必须与 t11 的共享实现**同一个函数**，不得另写一份。"""
    snap = _snapshot(enemy_hp=30)
    assert lr._combat_deadlock_signature(snap) == lr._combat_deadlock_signature(_snapshot(enemy_hp=30))
    assert lr._combat_deadlock_signature(snap) != lr._combat_deadlock_signature(_snapshot(enemy_hp=20))
