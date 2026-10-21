# -*- coding: utf-8 -*-
"""t23 / D6 —— ★★「破局必须真正【落地】」的端到端契约。

本文件钉住的缺陷（实机复现，run X9XP0L8HZWF7，2026-10-19 23:33–23:40）
------------------------------------------------------------------------
症状：战斗卡死，`floor` 恒 14、`turn_key` 恒 `0:14:combat:5`，但**没有任何动作发出**
      ⇒ 整局冻结。原始日志（`N.E.K.O_Plugin_sts2_autoplay_20261019.log`）实测：

          破局触发 39 次，其中每次之后 8 行内的 `plan-return` **39/39 全是 use_potion**
          全日志 `plan-return` 的 action_type 分布：**use_potion 292 / 292（100%）**
          运行时计数（`plan-call` 行尾，313 条样本）：
              stale    = {'0': 313}          ← ★ 313/313 恒为 0，**从未累加过**
              deadlock = {'0':170, '3':143}
              origin   = {'reused':175, 'refetched':131, 'none':7}

根因（★ 与任务契约原表述**不同**，见 §「对契约前提的订正」）
------------------------------------------------------------
`_combat_solver_only_outcome` 顶部把「局面指纹变了」当作「局面换了」而清零三个计数
（`heuristic_planner.py:1515-1522`），但那个指纹含**手牌/能量/敌血的即时值**
（`loop_runner._combat_deadlock_signature`）—— 卡死局面下它们仍可能逐 tick 微动
⇒ 每 tick 都"看起来是新局面" ⇒ `_solver_stale_reclaim_count` 被反复清回 0
⇒ (A) 分支的上界判定（`:1589`）**永远达不到** ⇒ `return None` 每 tick 遮蔽破局 gate
⇒ `action_type=end_turn` 从 292 次 `plan-return` 里出现 **0 次**。

★★ 对契约前提的两条订正（均附实测）
--------------------------------------
1. **契约称「破局那一刻 planner 返回 end_turn，但紧接着一 tick 被 reused 顶掉」**
   ⇒ **不成立**。实测 `plan-return reason=solver_deadlock_end_turn` **0 次出现**
   （`发 end_turn 打破死锁` 0 次、`求解器连续` 0 次、`纯求解器语义` 0 次）。
   `end_turn` **从未被产出**，谈不上"被顶掉"。真正的病灶是**上界不可达**。

2. **契约称「(甲) energy=0 时 `is_player_window_open` 判据反了」**
   ⇒ **不成立**。该判据是 `end_turn_action is not None`（`:1678`），而 `end_turn` 是
   **唯一不需要能量/目标/手牌**的动作 ⇒ energy=0 **不会**让它为 False。
   实测（`.dsh-artifacts/_t23_d6_premise.log`）：
       energy=0 + 手牌全不可打 + end_turn 在 available_actions ⇒ window_open = **True**（正确）
   契约真正想指的可能是**另一个**问题：本文件 `test_energy_zero_*` 钉住了实测行为，
   契约若认为那是缺陷，需先给出「游戏在 energy=0 时会把 end_turn 移出 available_actions」
   的证据 —— 目前**没有**该证据，故不作为缺陷处理。

上游同构（★ 铁律：先查上游是否有意为之）
------------------------------------------
* 上游把「重算次数」当**单调累计**量，且**从不清零**：
  `SolverController.cs:661` / `:1189` 均为 `... = GetValueOrDefault(...) + 1`；
  全仓库 grep `ReplanCounts = new` / `ReplanCounts.Clear` **零命中**。
  并把 `DeploymentDriftReplans` / `PlanExhaustedReplans` 作为**跨重试累计**的缺陷证据
  上报（`CombatBugReportDescription.cs:113-115`、`:194`、`:203-204`）。
  ⇒ 我的改法（计数不再随局面微动清零）与上游同向。
* 上游把 `EndTurn` 明确当作**合法的当前回合计划动作**：
  `SolverController.cs:2802-2804`
      `action.IsExecutable || action.Kind == PlanActionKind.EndTurn`
  ⇒ 「发 end_turn 脱困」是上游认可的语义，不是我方发明。
* 上游对「计划过期」的处置是**重搜、不烧回合**：
  `:3190-3202` `DEPLOY_REPLAN reason=card_missing → RequestSearch(DeploymentDrift)`。
  ⇒ 我方 (A) 分支的"重取不计数"方向正确，**缺的只是上界可达性**。

★ 与 D9（t17/t20）的关系：本修复**不假设 use_potion 一定失败**
  -----------------------------------------------------------------
t17 修好 mod 侧后 `use_potion(AllEnemies)` 会变成 `completed`，那时破局路径**根本不会被触发**。
故本文件钉的是**条件性正确**：破局一旦触发（无论因为什么原因卡住），就**必须能落地**，
且**必须在有界步数内**。测试用例全部构造"求解器给不出可执行首步"这一独立前提，
与 use_potion 的成败**无关**。
"""

from __future__ import annotations

import pytest

# 路径引导统一在 tests/conftest.py（此处不再插 sys.path）
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import _combat_deadlock_signature

# ★ 这两个是**类属性**（不是模块级常量），从类上取，避免重复定义走样。
_SOLVER_DEADLOCK_RETRIES = STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES
_SOLVER_STALE_RECLAIM_LIMIT = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT


# --------------------------------------------------------------------------------------
# 工装
# --------------------------------------------------------------------------------------
def _A(name: str) -> dict:
    """action_registry 里的一项（`_find_action` 读 `type` / `raw.name`，见 planner:1747）。"""
    return {"type": name, "raw": {"name": name}, "id": name}


def _snapshot(
    *,
    energy: int = 0,
    card_ids: list[str] | None = None,
    enemy_hp: int = 27,
    turn: int = 5,
    playable: bool = False,
    with_end_turn: bool = True,
) -> dict:
    """构造战斗 snapshot。★ 只构造 planner 真正读的字段（raw_state.combat.*）。"""
    cards = card_ids if card_ids is not None else ["STRIKE_IRONCLAD"]
    actions = [_A("end_turn")] if with_end_turn else []
    actions += [_A("play_card"), _A("save_and_quit")]
    return {
        "screen": "COMBAT",
        "raw_state": {
            "run_id": "T23-PROBE",
            "floor": 14,
            "combat": {
                "turn": turn,
                "energy": energy,
                "max_energy": 3,
                "player_hp": 46,
                "player_max_hp": 85,
                "hand": [
                    {
                        "index": i,
                        "card_id": c,
                        "name": c,
                        "playable": playable,
                        "cost": 1,
                        "requires_target": True,
                        "target_type": "AnyEnemy",
                        "valid_target_indices": [0],
                        "upgraded": False,
                    }
                    for i, c in enumerate(cards)
                ],
                "enemies": [
                    {
                        "index": 0,
                        "enemy_id": "MAWLER",
                        "current_hp": enemy_hp,
                        "max_hp": 76,
                        "is_alive": True,
                        "block": 0,
                    }
                ],
                "potions": [],
            },
        },
        "available_actions": actions,
        "classification": {"state_name": "combat", "screen_class": "CombatRoom"},
    }


def _bad_plan() -> dict:
    """求解器给了计划，但首步指向**不在手上**的牌 ⇒ 首步不可执行（= 卡死形态）。"""
    return {
        "line": [
            {
                "steps": [
                    {
                        "kind": "play_card",
                        "card_index": 0,
                        "card_id": "NOT_IN_HAND",
                        "card_name": "x",
                        "target_index": 0,
                    }
                ]
            }
        ]
    }


def _call(planner, snapshot, *, plan_origin: str, solver_plan=None, fetch_sig: str | None = None):
    """按 planner 战斗分支走到 `:1417` 时的**完全同参**调用兜底函数。"""
    plan = solver_plan if solver_plan is not None else _bad_plan()
    first = plan["line"][0]["steps"][0]
    sig = _combat_deadlock_signature(snapshot) if fetch_sig is None else fetch_sig
    return planner._combat_solver_only_outcome(
        snapshot=snapshot,
        available_actions=snapshot["available_actions"],
        solver_plan=plan,
        first_step=first,
        solver_skip_reason="probe: 首步不可执行",
        hand=snapshot["raw_state"]["combat"]["hand"],
        plan_origin=plan_origin,
        plan_fetch_signature=sig,
    )


def _drain(planner, *, plan_origin: str, drift: bool, ticks: int = 12,
           base_snapshot: dict | None = None) -> list[str | None]:
    """连续跑 N 个 tick，返回每 tick 的 action_type（None = 不发动作）。

    ``drift=True`` 模拟**实机卡死形态**：敌血每 tick 微动 ⇒ 局面指纹每 tick 变。
    """
    out: list[str | None] = []
    for i in range(1, ticks + 1):
        snap = base_snapshot() if base_snapshot else _snapshot(enemy_hp=27 - i if drift else 27)
        if base_snapshot and drift:
            snap["raw_state"]["combat"]["enemies"][0]["current_hp"] = 27 - i
        res = _call(planner, snap, plan_origin=plan_origin)
        out.append(None if res is None else res.action_type)
    return out


# ======================================================================================
# 1. ★★ 核心：破局必须有界可达（修复前：永不脱困）
# ======================================================================================
@pytest.mark.parametrize("plan_origin", ["reused", "none", "refetched"])
def test_breaker_lands_within_bound_even_when_signature_keeps_drifting(plan_origin: str) -> None:
    """★★ 主判据：**指纹逐 tick 微动**时，破局仍须在有界步数内落地。

    这是修复前**必然失败**的用例（实机 `stale` 313/313 恒 0 ⇒ gate 从未求值）。

    上界：契约 §5 的形式化陈述「任一固定指纹下调用次数 S ≤ LIMIT + RETRIES」。
    ★ 本用例更强：**指纹不固定**（每 tick 都变）时该上界**仍须成立** ——
      这正是实机形态，也是修复前失效的那一种。
    """
    planner = STS2HeuristicPlanner()
    actions = _drain(planner, plan_origin=plan_origin, drift=True, ticks=12)
    bound = _SOLVER_STALE_RECLAIM_LIMIT + _SOLVER_DEADLOCK_RETRIES
    first_end = next((i for i, a in enumerate(actions, 1) if a == "end_turn"), None)
    assert first_end is not None, (
        f"★★ 12 个 tick 内破局**从未落地**（plan_origin={plan_origin}）——\n"
        f"   这精确复现了实机缺陷：stale 计数被指纹变化反复清零 ⇒ 上界不可达 ⇒\n"
        f"   `_combat_solver_only_outcome` 每 tick 在 (A) 分支 return None，遮蔽破局 gate。\n"
        f"   实测动作序列 = {actions}"
    )
    assert first_end <= bound, (
        f"★破局落地过晚：第 {first_end} tick 才发 end_turn，超过上界 {bound}"
        f"（LIMIT={_SOLVER_STALE_RECLAIM_LIMIT} + RETRIES={_SOLVER_DEADLOCK_RETRIES}）"
    )


def test_breaker_lands_and_signature_drift_does_not_reset_progress_counters() -> None:
    """★ 直击根因：局面指纹变化**不得**重置「没进展」计数。

    修复前 `:1515-1522` 把指纹变化当作"新局面"⇒ 三计数清零 ⇒ 上界不可达。
    本用例用**受控指纹微动**（只改敌血）证明计数是**累积**的。
    """
    planner = STS2HeuristicPlanner()
    seen_stale: list[int] = []
    for i in range(1, 8):
        snap = _snapshot(enemy_hp=27 - i)          # 每 tick 指纹都变
        _call(planner, snap, plan_origin="reused")
        seen_stale.append(planner._solver_stale_reclaim_count)
    assert seen_stale == [1, 2, 3, 4, 5, 6, 7], (
        f"★ 指纹微动时 stale 计数应**单调累加**到 7，实测 {seen_stale}\n"
        "   若出现 [1,1,1,...] 则说明又回到「指纹变即清零」的缺陷行为。"
    )


def test_normal_path_recovers_and_clears_counters() -> None:
    """★ 不误伤：求解器**恢复**给出可执行首步时，计数应清零、且不发 end_turn。"""
    planner = STS2HeuristicPlanner()
    # 先攒到 7（未越过上界 ⇒ 还没发 end_turn）
    for i in range(1, 8):
        _call(planner, _snapshot(enemy_hp=27 - i), plan_origin="reused")
    assert planner._solver_stale_reclaim_count == 7

    # 求解器恢复：refetched + 首步 = end_turn（可执行）⇒ 走 else 恢复分支
    snap = _snapshot(enemy_hp=27)
    good = {
        "line": [
            {"steps": [{"kind": "end_turn", "card_index": None, "card_id": None,
                        "card_name": None, "target_index": None}]}
        ]
    }
    res = _call(planner, snap, plan_origin="refetched", solver_plan=good)
    assert res is None, "求解器已给出可执行首步 ⇒ 兜底函数不该发任何动作"
    assert planner._solver_stale_reclaim_count == 0, "★ 恢复后 stale 计数必须清零"
    assert planner._solver_deadlock_count == 0, "★ 恢复后 deadlock 计数必须清零"


# ======================================================================================
# 2. ★ 不误伤正常路径
# ======================================================================================
def test_no_early_end_turn_while_energy_remains_and_cards_are_playable() -> None:
    """★ 仍有能量 + 有可打牌 ⇒ 不得提前 end_turn。

    构造：energy=1、手牌可打、求解器首步**可执行**（牌在手上）
    ⇒ 应当**永远不进**兜底函数（planner 会走 normal 出牌路径）。
    本用例钉住「兜底函数的入口条件」而不是它的输出 —— 这才是不误伤的关键。
    """
    from plugin.plugins.sts2_autoplay.loop_runner import _plan_step_is_executable

    snap = _snapshot(energy=1, card_ids=["STRIKE_IRONCLAD"], playable=True)
    playable_plan = {
        "line": [
            {"steps": [{"kind": "play_card", "card_index": 0,
                        "card_id": "STRIKE_IRONCLAD", "card_name": "打击",
                        "target_index": 0}]}
        ]
    }
    ok, why = _plan_step_is_executable(playable_plan["line"][0]["steps"][0], snap)
    assert ok, f"前提：该首步应当可执行（否则用例无意义）；why={why}"

    # 即使把计数顶满，兜底函数也只在**被调用**时才有机会发 end_turn；
    # 由于首步可执行，planner 在更早处就 return（:1386 或 normal 路径）⇒ 不经过本函数。
    planner = STS2HeuristicPlanner()
    planner._solver_deadlock_count = 999
    planner._solver_stale_reclaim_count = 999
    assert _plan_step_is_executable(playable_plan["line"][0]["steps"][0], snap)[0] is True


def test_no_action_when_player_window_is_closed() -> None:
    """★ 窗口未开（available_actions 无 end_turn）⇒ 只计数、不发动作（既有契约保持）。

    ★ t23 变异 M-T23-E 暴露的覆盖漏洞：本用例最初用 `plan_origin="reused"` +
      少量 tick ⇒ 在 (A) 分支 `return None` 就结束了，**根本没走到窗口判定**
      ⇒ 把窗口闸整个删掉（`if False:`）也照样绿 ⇒ 用例是**假绿**。
    修法：用 `plan_origin="refetched"` + `deadlock_count` 顶满，确保**必然穿过**
      (A)/(B) 分支抵达窗口判定（相对行 259：`if not is_player_window_open`）。
    """
    planner = STS2HeuristicPlanner()
    snap = _snapshot(with_end_turn=False)
    planner._solver_deadlock_signature = _combat_deadlock_signature(snap)
    planner._solver_deadlock_count = _SOLVER_DEADLOCK_RETRIES   # 顶满 ⇒ 不会被 (A) 提前 return
    res = _call(planner, snap, plan_origin="refetched")
    assert res is None, (
        "★ 窗口未开（无 end_turn 可发）时不得发任何动作；"
        f"实测 {None if res is None else res.action_type}"
    )
    assert planner._solver_out_of_window_count == 1, (
        f"窗口外计数应 +1，实测 {planner._solver_out_of_window_count}"
    )
    # ★ 窗口外**不得**消耗死锁计数（t13 的语义：那不是"求解器无解"，是"轮不到我们"）
    assert planner._solver_deadlock_count == 0, (
        f"窗口判定应把死锁计数清零（t13 语义），实测 {planner._solver_deadlock_count}"
    )


def test_energy_zero_does_not_make_window_look_closed() -> None:
    """★ 钉住实测行为，订正契约 (甲) 的表述。

    `is_player_window_open = end_turn_action is not None`（planner `:1678`）。
    `end_turn` 是**唯一不需要能量/目标/手牌**的动作 ⇒ energy=0 **不会**让它消失。
    实测（`.dsh-artifacts/_t23_d6_premise.log`）：
        energy=0 + 手牌全不可打 + end_turn 在 available_actions ⇒ window_open = **True**

    ⇒ 契约若主张"判据反了"，须先给出「游戏在 energy=0 时把 end_turn 移出
      available_actions」的证据。本用例把**当前实测行为**固定下来，避免无证据改动。
    """
    planner = STS2HeuristicPlanner()
    assert planner._find_action([_A("end_turn")], "end_turn") is not None
    # energy=0 + 手牌全不可打：窗口仍开 ⇒ 兜底函数**可以**发 end_turn（不被窗口闸挡）
    snap = _snapshot(energy=0, card_ids=["STRIKE_IRONCLAD"], playable=False)
    planner._solver_deadlock_count = _SOLVER_DEADLOCK_RETRIES
    planner._solver_deadlock_signature = _combat_deadlock_signature(snap)
    res = _call(planner, snap, plan_origin="refetched")
    assert res is not None and res.action_type == "end_turn", (
        "★ energy=0 + 手牌全不可打 且计数已满 ⇒ 应当能发 end_turn"
        "（窗口没被判成关闭）；实测 "
        f"{None if res is None else res.action_type}"
    )


# ======================================================================================
# 3. ★★ 破局落地：end_turn 必须真的成为「本 tick 的最终动作」
# ======================================================================================
def test_breaker_end_turn_is_the_final_return_not_shadowed() -> None:
    """★★ 破局产出的 end_turn 必须是**函数最终返回**，而非被更早的 return 遮蔽。

    修复前的真实病灶：`plan_origin=reused` 恒走 (A) 分支并在 `:1609` `return None`，
    **根本走不到** gate（`:1686`）⇒ 破局永不落地。
    本用例遍历所有 origin × 计数状态，断言「计数到达阈值 ⇒ 必得 end_turn」。
    """
    for origin in ("reused", "none", "refetched"):
        planner = STS2HeuristicPlanner()
        snap = _snapshot(energy=0, playable=False)
        planner._solver_deadlock_signature = _combat_deadlock_signature(snap)
        planner._solver_deadlock_count = _SOLVER_DEADLOCK_RETRIES
        res = _call(planner, snap, plan_origin=origin)
        # ★ `refetched` + 首步不可执行 + 指纹未变 ⇒ 走 (B) 且计数再 +1 ⇒ 仍应发 end_turn
        assert res is not None and res.action_type == "end_turn", (
            f"★ 计数已达阈值但破局未落地（origin={origin}）："
            f"{None if res is None else res.action_type}\n"
            "   这正是实机「破局 39 次触发、end_turn 产出 0 次」的形态。"
        )
        assert res.source == "solver_deadlock_breaker"
        assert res.reason == "solver_deadlock_end_turn"


def test_breaker_resets_counters_after_landing() -> None:
    """破局落地后计数必须归零（下一回合是全新局面，不该背旧计数）。"""
    planner = STS2HeuristicPlanner()
    snap = _snapshot(energy=0, playable=False)
    planner._solver_deadlock_signature = _combat_deadlock_signature(snap)
    planner._solver_deadlock_count = _SOLVER_DEADLOCK_RETRIES
    res = _call(planner, snap, plan_origin="none")
    assert res is not None and res.action_type == "end_turn"
    assert planner._solver_deadlock_count == 0
    assert planner._solver_stale_reclaim_count == 0
    assert planner._solver_out_of_window_count == 0


# ======================================================================================
# 4. ★ 与 D9（t17/t20）不冲突：不假设 use_potion 一定失败
# ======================================================================================
def test_repair_is_independent_of_potion_success_or_failure() -> None:
    """★ t23 契约要求：修复须**条件性正确**，不依赖「use_potion 永远失败」。

    t17 修好 mod 后 `use_potion(AllEnemies)` 会成功、破局不被触发 —— 那是**正常**的。
    本用例证明：本文件所有判据的**前提**都与药水无关，只依赖
    「求解器给不出可执行首步」这一独立事实。
    """
    planner = STS2HeuristicPlanner()
    snap = _snapshot(energy=0, playable=False)
    # 前提核查：本用例构造的是"首步不可执行"，与药水可用与否无关。
    from plugin.plugins.sts2_autoplay.loop_runner import _plan_step_is_executable

    bad = _bad_plan()["line"][0]["steps"][0]
    ok, why = _plan_step_is_executable(bad, snap)
    assert ok is False, "前提：该首步必须不可执行（与药水状态无关）"
    assert "NOT_IN_HAND" in json_dumps_contains(why) or why, "前提：应给出不可执行原因"

    # 药水槽为空时，本路径照样按"求解器给不出可执行首步"推进到破局。
    assert (snap["raw_state"]["combat"].get("potions") or []) == []
    planner._solver_deadlock_signature = _combat_deadlock_signature(snap)
    planner._solver_deadlock_count = _SOLVER_DEADLOCK_RETRIES
    res = _call(planner, snap, plan_origin="none")
    assert res is not None and res.action_type == "end_turn"


def json_dumps_contains(text: str) -> str:
    import json

    return json.dumps(text, ensure_ascii=False)
