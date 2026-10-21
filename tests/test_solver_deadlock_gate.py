"""t37：D1 实机复现形态的破局门可达性与三计数互不掩盖。

## 背景（实机证据）

`N.E.K.O_Plugin_sts2_autoplay_20261020.log`，run=C6CM8CZRR9KQ，07:44:36–07:53:52：
  · 17 条「求解器=play_card/XXX -> 执行=(过期/交接转死锁判定) 一致=否」
  · `stale` 7,8,…,23（看似单调；LIMIT=5）
  · `deadlock` 在 1/2 徘徊，永远到不了阈值 3
  · `source=solver_deadlock_breaker` 0 次、「打破死锁」0 次

关键更正（t37 实测，已由队长独立复核）：
  「stale 7..23 单调」是只打印 mismatch 行造成的视觉假单调 ——
  被清零的成功 tick 不打印，多个小周期被拼成一条看似单调的数列。
  写入位置插桩（`.dsh-artifacts/_t37_trace.log`，仅测试进程）显示实机序列下
  只命中 `:1729`（累加）与 `:1868`（破局后清零）；`:1807` 一次都没出现
  （那 17 条发生时窗口是开的，见 `.dsh-artifacts/_t37_probe3.log`）。
  ⇒ 任务书原先推测的「`:1807` 清零」不是这条形态的成因。

真因：两条 planner 调用路径的 `first_step` 不同（缺 `solver_step_index`）
  ⇒ 一条判可执行（走 :1769 else 清零 stale/deadlock）、另一条判漂移（+1）
  ⇒ 计数被反复清零 ⇒ 门不可达。
  修在 `loop_runner._planner_context`（单一工厂，见 test_plan_reuse_step_consistency.py）。

## 本文件测什么

1. 破局门确实可达（纯 reused 序列下第 LIMIT+RETRIES 次发 end_turn）。
2. 三个计数互不掩盖：任一 tick 都能读出「求解器给了什么 / 为什么没执行 / 三计数各是多少」。
   特别是：成功 tick 也必须留一行（这正是视觉假单调的根因）。
3. 变异测试：至少 3 个变异体必须转红。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

pytestmark = pytest.mark.unit


# ── 夹具（与 tests/test_solver_deadlock_contract.py 同源口径）──────────────────


def _snapshot(*, hand=None, energy: int = 3, enemy_hp: int = 30, actions=None) -> dict:
    """固定局面。默认手牌只有 DEFEND ⇒ 任何建议打 STRIKE 的计划都不可执行。"""
    return {
        "screen": "COMBAT",
        "available_actions": [{"type": n, "raw": {"name": n}}
                              for n in (actions or ["play_card", "end_turn"])],
        "raw_state": {
            "combat": {
                "hand": hand if hand is not None else [_card("DEFEND", index=0)],
                "player": {"energy": energy},
                "enemies": [{"index": 0, "current_hp": enemy_hp, "is_alive": True}],
            },
        },
    }


def _card(card_id: str, *, index: int = 0, playable: bool = True) -> dict:
    return {"index": index, "card_id": card_id, "playable": playable,
            "requires_target": False}


def _stale_step() -> dict:
    """一张已不在手的牌：手牌只有 DEFEND，这里建议打 STRIKE。"""
    return {"kind": "play_card", "card_id": "STRIKE", "card_index": 1}


def _good_step() -> dict:
    return {"kind": "play_card", "card_id": "DEFEND", "card_index": 0}


def _outcome(planner, *, snapshot, plan_origin, first_step, fetch=None, actions=None):
    return planner._combat_solver_only_outcome(
        snapshot=snapshot,
        available_actions=(snapshot["available_actions"] if actions is None else
                           [{"type": n, "raw": {"name": n}} for n in actions]),
        solver_plan={"in_combat": True,
                     "line": [{"turn": 1, "steps": [first_step]}]},
        first_step=first_step,
        solver_skip_reason="求解器建议 STRIKE 但实机手牌里没有这张卡（手牌漂移）",
        hand=list(snapshot["raw_state"]["combat"]["hand"]),
        plan_origin=plan_origin,
        plan_fetch_signature=fetch if fetch is not None else "",
    )


# ── ① 破局门可达 ────────────────────────────────────────────────────────────


def test_breaker_gate_is_reachable_under_pure_reused() -> None:
    """上界必须真正可达：第 LIMIT+RETRIES 次发 end_turn。

    实机 `source=solver_deadlock_breaker` = 0 次；本用例证明在一致的输入下门是可达的
    （修前不可达是因为两条路径给的 first_step 不同，不是这段算术坏了）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    actions = []
    for _ in range(13):
        result = _outcome(planner, snapshot=snapshot, plan_origin="reused",
                          first_step=_stale_step())
        actions.append(result.action_type if result is not None else None)
        if result is not None and result.source == "solver_deadlock_breaker":
            break

    assert "end_turn" in actions, (
        "13 个 tick 内破局从未落地 —— 上界不可达。实际序列 = %r。"
        "这正是实机形态：stale 无限涨而 deadlock 到不了阈值。" % (actions,)
    )
    assert actions.count("end_turn") == 1, "破局应恰好 1 次，实际 %r" % (actions,)


def test_breaker_gate_uses_the_declared_bound() -> None:
    """触发点必须是声明的上界 LIMIT+RETRIES，不得提前也不得无限期。"""
    planner = STS2HeuristicPlanner()
    limit = planner._SOLVER_STALE_RECLAIM_LIMIT
    retries = planner._SOLVER_DEADLOCK_RETRIES
    snapshot = _snapshot()

    fired_at = None
    for tick in range(1, limit + retries + 3):
        r = _outcome(planner, snapshot=snapshot, plan_origin="reused",
                     first_step=_stale_step())
        if r is not None and r.source == "solver_deadlock_breaker":
            fired_at = tick
            break
    assert fired_at is not None, "破局门从未触发"
    assert fired_at == limit + retries, (
        "破局应在第 %d 次触发，实际第 %s 次" % (limit + retries, fired_at)
    )


def test_breaker_clears_all_counters_after_firing() -> None:
    """破局后三计数归零（下一回合是全新局面，不该背旧计数）。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    for _ in range(planner._SOLVER_STALE_RECLAIM_LIMIT + planner._SOLVER_DEADLOCK_RETRIES):
        r = _outcome(planner, snapshot=snapshot, plan_origin="reused",
                     first_step=_stale_step())
        if r is not None and r.source == "solver_deadlock_breaker":
            break
    assert planner._solver_deadlock_count == 0
    assert planner._solver_stale_reclaim_count == 0


# ── ② 三计数互不掩盖 ────────────────────────────────────────────────────────


def test_out_of_window_does_not_inflate_deadlock() -> None:
    """窗口关（无 end_turn）时：不累加 deadlock，改用独立窗口外计数。

    ★ 路径口径（t37 实测确认，别再猜）：
      只有走到「A/B 判定之后」的窗口检查时才会累加 `_solver_out_of_window_count`。
      而 `plan_origin="reused"` + 首步**不可执行**会在 (A) 分支就 `return None`（更早），
      根本走不到窗口检查 —— 那种形态累加的是 `stale`（见场景 X）。
      ⇒ 要测窗口外语义，必须用**能通过 A/B 判定**的输入：
        `refetched` + 指纹相同（判 B）或 `refetched` + 首步可执行。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])          # 无 end_turn ⇒ 窗口关
    fetch = planner._combat_deadlock_signature(snapshot)  # 取回后指纹未变 ⇒ 归 B

    for _ in range(4):
        _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                 first_step=_stale_step(), fetch=fetch)

    assert planner._solver_out_of_window_count == 4, (
        "窗口外必须有独立计数（否则插件没动与窗口没开在日志里不可区分）。"
        "实际 out_of_window=%d" % planner._solver_out_of_window_count
    )
    assert planner._solver_deadlock_count == 0, (
        "窗口外不得累加 deadlock（那不是求解器无解，是轮不到我们）。"
        "★ 实机曾见到 N/3 的 N 越界到 5/3、7/3，正是此处累加所致。"
    )


def test_stale_path_returns_before_window_check() -> None:
    """★ 路径口径固化：`reused` + 不可执行 ⇒ 在 (A) 分支就 return，不碰窗口外计数。

    这条用例把「哪个计数在哪种路径上累加」钉死，防止将来改动把两者混在一起
    （那正是 D1 最贵的部分：语义互相掩盖 ⇒ 日志里读不出真因）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])
    for _ in range(3):
        _outcome(planner, snapshot=snapshot, plan_origin="reused",
                 first_step=_stale_step())
    assert planner._solver_stale_reclaim_count == 3, "reused+不可执行 ⇒ 累加 stale"
    assert planner._solver_out_of_window_count == 0, (
        "该路径在 (A) 分支就 return，不该累加窗口外计数"
    )


def test_three_counters_are_independent() -> None:
    """三计数语义互不掩盖：同一 planner 上三者各走各的。"""
    planner = STS2HeuristicPlanner()
    # (A) 漂移 ⇒ stale +1，deadlock / out_of_window 都不动
    _outcome(planner, snapshot=_snapshot(), plan_origin="reused",
             first_step=_stale_step())
    assert planner._solver_stale_reclaim_count == 1
    assert planner._solver_deadlock_count == 0
    assert planner._solver_out_of_window_count == 0

    # 窗口关（走得到窗口检查的形态）⇒ out_of_window +1，deadlock 仍不动
    win_closed = _snapshot(actions=["play_card"])
    fetch = planner._combat_deadlock_signature(win_closed)
    _outcome(planner, snapshot=win_closed, plan_origin="refetched",
             first_step=_stale_step(), fetch=fetch)
    assert planner._solver_out_of_window_count == 1
    assert planner._solver_deadlock_count == 0


def test_successful_recovery_clears_both_counts() -> None:
    """真恢复（refetched + 首步可执行）= 唯一清零点（t23 语义，保留）。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    _outcome(planner, snapshot=snapshot, plan_origin="reused",
             first_step=_stale_step())
    _outcome(planner, snapshot=snapshot, plan_origin="reused",
             first_step=_stale_step())
    assert planner._solver_stale_reclaim_count == 2

    r = _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                 first_step=_good_step())
    assert r is None, "求解器已给出可执行首步 ⇒ 本函数不该发动作（更早处会出牌）"
    assert planner._solver_stale_reclaim_count == 0
    assert planner._solver_deadlock_count == 0


# ── ③ 成功 tick 也必须留一行（视觉假单调的根因）────────────────────────────


def test_success_tick_emits_log_line(monkeypatch) -> None:
    """每个 tick 都要留痕，包括成功（清零）的那些。

    实机 stale 7..23 看似单调正是因为成功 tick 不打印 ⇒
    多个小周期被拼成一条数列。本用例要求：恢复 tick 必须产生一条**可区分的**日志，
    且该行必须写明「清零前各计数」与「为什么清零」。

    ★ 用内容断言而非"行数增加"：否则漂移行也会让它通过（变异体 M-E 曾因此漏网）。
    """
    planner = STS2HeuristicPlanner()
    lines: list[str] = []

    def _capture(*, solver_kind=None, solver_card_id=None, solver_target=None,
                 executed_action=None, executed_kwargs=None, matched=None,
                 fallback_reason=None, hand=None, **kw):
        lines.append(str(executed_action or "") + "|" + str(fallback_reason or ""))

    monkeypatch.setattr(planner, "_combat_log_solver_vs_executed", _capture)

    snapshot = _snapshot()
    _outcome(planner, snapshot=snapshot, plan_origin="reused",
             first_step=_stale_step())
    assert any("过期" in ln or "漂移" in ln for ln in lines), "漂移 tick 必须留痕"

    # ★ 恢复 tick：必须新增一行，且**内容**能识别出这是"清零"
    joined_before = "\n".join(lines)
    _outcome(planner, snapshot=snapshot, plan_origin="refetched",
             first_step=_good_step())
    joined_after = "\n".join(lines)

    assert joined_after != joined_before, (
        "恢复（清零）tick 没有新增任何日志行 —— 这正是实机 stale 看似单调的根因。"
    )
    # 恢复行必须自带可区分标记 + 清零前计数 + 原因
    recovery = [ln for ln in lines if "恢复" in ln]
    assert recovery, (
        "恢复 tick 的日志行里没有可区分标记（应含『恢复』字样）。"
        "现有行：%r" % (lines,)
    )
    line = recovery[-1]
    assert "清零前" in line, (
        "恢复行必须写出**清零前**的计数，否则日志里读不出『三个计数各是多少』。"
        "实际行：%r" % line
    )
    assert "stale=" in line and "deadlock=" in line and "plan_origin=" in line, (
        "恢复行必须同时含 stale / deadlock / plan_origin，"
        "否则三计数在日志里互相掩盖。实际行：%r" % line
    )


# ── ④ 变异测试：至少 3 个变异体必须转红 ─────────────────────────────────────


def test_mutation_counters_kept_resetting_turns_red() -> None:
    """变异体 M1：让 stale 每 tick 被清零（模拟被反复抹掉）⇒ 门必须不可达。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    fired = []
    for _ in range(planner._SOLVER_STALE_RECLAIM_LIMIT
                   + planner._SOLVER_DEADLOCK_RETRIES + 4):
        r = _outcome(planner, snapshot=snapshot, plan_origin="reused",
                     first_step=_stale_step())
        planner._solver_stale_reclaim_count = 0      # 变异：模拟被清零
        if r is not None:
            fired.append(r.source)

    assert "solver_deadlock_breaker" not in fired, (
        "变异体应转红：若 stale 被反复清零，破局门不该触发"
        "（本断言失败说明门对被清零不敏感 ⇒ 门无效）。实际 fired=%r" % (fired,)
    )


def test_mutation_bound_raised_turns_red(monkeypatch) -> None:
    """变异体 M2：把上界抬到天文数字 ⇒ 门必须不可达。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    monkeypatch.setattr(planner, "_SOLVER_STALE_RECLAIM_LIMIT", 10 ** 9)

    fired = []
    for _ in range(12):
        r = _outcome(planner, snapshot=snapshot, plan_origin="reused",
                     first_step=_stale_step())
        if r is not None:
            fired.append(r.source)
    assert not fired, (
        "变异体应转红：上界被抬到天文数字后，破局门不该在 12 tick 内触发。"
        "实际 fired=%r" % (fired,)
    )


def test_mutation_window_closed_inflates_deadlock_turns_red() -> None:
    """变异体 M3：窗口关时若仍累加 deadlock ⇒ 计数语义自相矛盾。

    ★ 用能走到窗口检查的形态（`refetched` + 指纹相同 ⇒ 判 B）：
      否则会像 (A) 分支那样提前 return，测不到窗口语义。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])   # 窗口关
    fetch = planner._combat_deadlock_signature(snapshot)
    for _ in range(6):
        _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                 first_step=_stale_step(), fetch=fetch)
    assert planner._solver_deadlock_count == 0, (
        "变异体应转红：窗口外累加 deadlock 会产生 N/3 的 N 越界"
        "（实机曾见到 5/3、7/3）。实际 deadlock=%d"
        % planner._solver_deadlock_count
    )
    assert planner._solver_out_of_window_count < planner._SOLVER_OUT_OF_WINDOW_LIMIT, (
        "窗口外计数应回绕，不应越界累加"
    )


def test_mutation_step_index_ignored_turns_red() -> None:
    """变异体 M4：若 solver_step_index 被忽略（永远读第 0 步）⇒ 必然分叉。

    这是 t37 原缺陷的精确形态：两条路径一个读第 0 步、一个读第 N 步。
    """
    from plugin.plugins.sts2_autoplay.heuristic_planner import _line_step_at

    plan = {"in_combat": True, "line": [{"turn": 1, "steps": [
        {"kind": "play_card", "card_id": "FLAME_BARRIER", "card_index": 0},
        {"kind": "play_card", "card_id": "IRON_WAVE", "card_index": 1},
    ]}]}
    ignored = _line_step_at(plan, None)        # 变异：忽略 step_index
    correct = _line_step_at(plan, 1)
    assert ignored != correct, (
        "变异体应转红：忽略 step_index 时取到的步骤与正确值不同 ⇒ "
        "两条路径必然分叉（正是 t37 缺陷）"
    )
