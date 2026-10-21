"""D1 契约：把「计划过期（漂移）」与「求解器真无解」拆成两种计数。

契约来源：`.dsh-artifacts/req/R1-D1-CONTRACT.md`（t3，sha16=8485b8db8ad637f6）。
本文件落 §2 的 T-A…T-G 七条用例，并在末段落 §1.3 的 INV-1…INV-4 不变量。

## 为什么必须拆

修 D1 前，两种**局面逐位不可区分**的情况共用一个 `_solver_deadlock_count`：

- **(A) 计划过期（漂移）**：求解器建议的牌**已不在手**。搜索要 ~1s，期间上一步动作
  已让手牌变了 ⇒ 计划天然过期。**运维常态**。上游等价动作是
  `SolverController.cs:3190-3202` 的 `RequestSearch(DeploymentDrift)`：同回合重搜、**不烧回合**。
- **(B) 求解器真无解**：**看着最新局面重取回来**的计划，首步**仍然**做不了。

修前 A 也计数 ⇒ 3 次正常漂移就白烧一整个回合。实测代价：74 条不一致里 10 条直接 end_turn，
**全部**由漂移触发（上游语义下本应只触发重搜）。

## 判据（唯一新增的可观测差异）

`plan_origin ∈ {"reused","refetched","none"}`，由 `loop_runner.tick` 的**真实分支**透传
（`loop_runner.py` :107/:115/:123），planner **绝不猜**：

- `refetched` 且首步**现在仍不可执行** ⇒ **B**（看着最新局面还是不行）
- 计划为空 / 首步缺失 ⇒ **B**（重取后连计划都没有）
- 其余（`reused` 或本 tick 未重取）⇒ **A**（只是过期）

## 上界（防无限重取）

`_SOLVER_STALE_RECLAIM_LIMIT = 5`：同一指纹下连续 5 次 A 不计数，
第 6 次起转入 B 计数 ⇒ 最迟第 `5+3 = 8` 次发 `end_turn`。
⇒ 任一固定指纹下调用次数 ≤ 8，**不可能无限重取卡死整局**。

## ★ 陷阱提醒

`_SOLVER_DEADLOCK_RETRIES` 让 planner **有状态**：第 N 次相同 `plan()` 调用会返回 `end_turn`。
**每个断言都要新建 `STS2HeuristicPlanner()`**，否则用例之间会互相污染。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

# --------------------------------------------------------------------------------------
# 夹具：固定局面（手牌 DEFEND / 能量 3 / 敌人 30HP）⇒ 指纹恒为 'DEFEND|3|30'
# --------------------------------------------------------------------------------------


def _snapshot(*, hand: list[dict] | None = None, energy: int = 3, enemy_hp: int = 30,
              actions: list[str] | None = None) -> dict:
    """固定局面。默认手牌只有 DEFEND ⇒ 任何「打 STRIKE」的计划都**不可执行**。"""
    return {
        "screen": "COMBAT",
        "available_actions": [{"type": n, "raw": {"name": n}} for n in (actions or ["play_card", "end_turn"])],
        "raw_state": {
            "combat": {
                "hand": hand if hand is not None else [_card("DEFEND", index=0)],
                "player": {"energy": energy},
                "enemies": [{"index": 0, "current_hp": enemy_hp, "is_alive": True}],
            },
        },
    }


def _card(card_id: str, *, index: int = 0, playable: bool = True) -> dict:
    return {"index": index, "card_id": card_id, "playable": playable, "requires_target": False}


def _plan(*steps: dict) -> dict:
    return {"in_combat": True, "line": [{"turn": 1, "steps": list(steps)}]}


def _outcome(planner: STS2HeuristicPlanner, *, snapshot: dict, plan_origin: str,
             first_step: dict | None, solver_plan: dict | None = None,
             actions: list[str] | None = None, plan_fetch_signature: str | None = None):
    """走**生产同一条**出口 `_combat_solver_only_outcome`（不经 plan()，避免其它分支干扰）。

    ``plan_fetch_signature``（t11）：**取回计划那一刻**的局面指纹。
      - 省略（``None``）⇒ 沿用空串，即"调用方没提供" ⇒ 按保守口径视为**未变**（仍是 B）。
        ★ 这样现存的 T-A…T-G 用例语义不变（它们测的是"没提供指纹"的老调用方）。
      - 显式传 ``"__CURRENT__"`` ⇒ 用**当前**快照的指纹（模拟"取回后局面一点没动" ⇒ 真无解 B）。
      - 显式传任意别的字符串 ⇒ 模拟"取回后局面已变"（⇒ 漂移 A）。
    """
    if plan_fetch_signature == "__CURRENT__":
        plan_fetch_signature = planner._combat_deadlock_signature(snapshot)
    elif plan_fetch_signature is None:
        plan_fetch_signature = ""
    return planner._combat_solver_only_outcome(
        snapshot=snapshot,
        available_actions=snapshot["available_actions"] if actions is None else
        [{"type": n, "raw": {"name": n}} for n in actions],
        solver_plan=solver_plan if solver_plan is not None else _plan(first_step or {"kind": "play_card", "card_id": "STRIKE"}),
        first_step=first_step,
        solver_skip_reason="求解器建议 STRIKE 但实机手牌里没有这张卡（手牌漂移）",
        hand=list(snapshot["raw_state"]["combat"]["hand"]),
        plan_origin=plan_origin,
        plan_fetch_signature=plan_fetch_signature,
    )


def _stale_step() -> dict:
    """一张**已不在手**的牌：手牌只有 DEFEND，这里建议打 STRIKE。"""
    return {"kind": "play_card", "card_id": "STRIKE", "card_index": 1}


# --------------------------------------------------------------------------------------
# T-A ★ ①第 1 步就漂移（solver_step_index==0，旧计划首步的牌已不在手）
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_a_first_step_stale_is_not_counted_as_deadlock() -> None:
    """T-A：`plan_origin="reused"` + 首步失效 ⇒ 返回 None、死锁计数为 0、过期计数为 1。

    这是**运维常态**（搜索 ~1s 内手牌已变），上游语义是重搜而非烧回合。
    """
    planner = STS2HeuristicPlanner()  # ★ 每个断言新建：planner 有状态
    snapshot = _snapshot()  # 手牌 [DEFEND]，能量 3，敌人 30

    result = _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())

    assert result is None, "计划过期不得在本 tick 发动作（应交给 loop_runner 立即重取）"
    assert planner._solver_deadlock_count == 0, "★ 计划过期**不得**计入死锁（否则 3 次漂移就白烧回合）"
    assert planner._solver_stale_reclaim_count == 1, "计划过期应计入**独立的**过期计数"
    assert planner._solver_deadlock_signature == "DEFEND|3|30"


@pytest.mark.unit
def test_t_a_stale_leaves_a_trace_mentioning_plan_expired() -> None:
    """T-A 留痕：日志必须写明「计划过期…不计死锁」，否则线上无法核对修复是否生效。"""
    planner = STS2HeuristicPlanner()
    reasons: list[str] = []
    planner._combat_log_solver_vs_executed = (  # type: ignore[method-assign]
        lambda **kw: reasons.append(str(kw.get("fallback_reason") or ""))
    )

    _outcome(planner, snapshot=_snapshot(), plan_origin="reused", first_step=_stale_step())

    assert reasons, "计划过期必须留痕（否则'插件没动'与'插件卡死'无法区分）"
    assert "计划过期" in reasons[0]
    assert "不计死锁" in reasons[0]
    assert "1/5" in reasons[0], f"应带上界进度 1/5，实际 {reasons[0]!r}"


# --------------------------------------------------------------------------------------
# T-B ★ ②第 2 步漂移（前面步已打出，solver_step_index==1，缓存计划第 1 步失效）
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_b_second_step_stale_is_also_not_counted() -> None:
    """T-B：与 T-A 同因——前一步真的打出过牌（`service.py:901` 推进 step_index）。

    ★ T-A 与 T-B 的区别只在「第几步失效」，**判据完全相同**（都属于 A）：
      两者都由 `_reusable_plan` → `_plan_step_is_executable` 判失效，
      因此都必须**不计数**。这里显式把 `solver_step_index=1` 的局面走一遍，
      防止修复只覆盖了首步。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    plan = _plan({"kind": "play_card", "card_id": "ZAP"}, {"kind": "play_card", "card_id": "ZAP"})

    # step_index=1 ⇒ 取第二步（前面步已打出，这张牌真的不在手了）
    second = plan["line"][0]["steps"][1]
    result = _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=second, solver_plan=plan)

    assert result is None
    assert planner._solver_deadlock_count == 0, "★ 第 2 步漂移同样不得计入死锁"
    assert planner._solver_stale_reclaim_count == 1


@pytest.mark.unit
def test_t_b_consecutive_stale_never_burns_a_turn_before_the_limit() -> None:
    """T-B 加强：连续 5 次漂移（含第 2 步漂移）**一次 end_turn 都不发**（这正是修前的缺陷）。

    修前行为：第 3 次就给 `end_turn`（`_SOLVER_DEADLOCK_RETRIES=3`）⇒ 白烧一整个回合。
    修后行为：前 5 次全是 A ⇒ 全部 None、死锁计数恒为 0。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    limit = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT

    for i in range(limit):
        result = _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
        assert result is None, f"第 {i + 1} 次漂移不得发动作（修前第 3 次会发 end_turn）"
        assert planner._solver_deadlock_count == 0, f"第 {i + 1} 次漂移后死锁计数必须仍为 0"

    assert planner._solver_stale_reclaim_count == limit, (
        f"前 {limit} 次应累计到 {limit}（尚未转入死锁计数）"
    )


# --------------------------------------------------------------------------------------
# T-C ★ ③同一指纹下连续 3 次真无解 ⇒ 第 3 次仍 end_turn
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_c_real_deadlock_still_fires_end_turn_on_the_third_call() -> None:
    """T-C：`plan_origin="refetched"` 且首步**仍**不可执行 ⇒ 第 3 次发 end_turn。

    「看着最新局面重取回来还是一样」= 真僵局 —— 这正是 `_SOLVER_DEADLOCK_RETRIES=3` 想抓的，
    修后它抓的**只有**这种情况。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    retries = STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES

    results = [
        _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
        for _ in range(retries)
    ]

    assert results[0] is None and results[1] is None, "前 2 次真无解应仍不发动作"
    assert planner._solver_deadlock_count == 0, "发过 end_turn 后计数应清零（下一回合是全新局面）"

    third = results[2]
    assert third is not None, "★ 第 3 次真无解必须给动作（否则永久卡住）"
    assert third.action_type == "end_turn"
    assert third.source == "solver_deadlock_breaker"
    assert third.reason == "solver_deadlock_end_turn"


@pytest.mark.unit
def test_t_c_real_deadlock_counts_up_before_the_threshold() -> None:
    """T-C 计数轨迹：第 1、2 次 B ⇒ 死锁计数 1、2；且过期计数始终为 0。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    first = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
    assert first is None and planner._solver_deadlock_count == 1
    assert planner._solver_stale_reclaim_count == 0, "B 不得增加过期计数"

    second = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
    assert second is None and planner._solver_deadlock_count == 2
    assert planner._solver_stale_reclaim_count == 0


# --------------------------------------------------------------------------------------
# T-D 上界：连续过期不会无限重取，最迟第 LIMIT+RETRIES 次 end_turn
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_d_stale_reclaim_is_bounded_and_breaks_out_by_the_limit_plus_retries() -> None:
    """T-D：同一指纹下连续 8 次 A ⇒ 前 5 次 None、第 6 次起转入 B、**第 8 次 end_turn**。

    ★ 为什么必须有上界：反复过期本身异常（上游 `UnexpectedReplanCount`
      `SolverController.cs:175-177` 把 `DeploymentDrift` 当**缺陷信号**）。
      只重取不设上界 ⇒ 某局面每 tick 都过期就**永久卡死整局**。
    算术：LIMIT=5、RETRIES=3 ⇒ 第 1–5 次 A 不计数；第 6/7/8 次计 1/2/3 ⇒ 第 8 次发 end_turn。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    limit = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT
    retries = STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES
    total = limit + retries

    outcomes = []
    for i in range(total):
        outcomes.append(_outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step()))

    assert all(o is None for o in outcomes[: limit]), f"前 {limit} 次 A 应全部不发动作"
    assert all(o is None for o in outcomes[limit : total - 1]), "第 6、7 次仍不发动作（计数 1、2）"
    last = outcomes[-1]
    assert last is not None and last.action_type == "end_turn", (
        f"★ 第 {total} 次必须 end_turn 脱离（有界性），实际 {last!r}"
    )
    assert last.source == "solver_deadlock_breaker"


@pytest.mark.unit
def test_t_d_upper_bound_is_exactly_limit_plus_retries() -> None:
    """T-D 的形式化：任一固定指纹下「调用次数 ≤ LIMIT+RETRIES」= 8。"""
    assert STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT == 5
    assert STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES == 3
    assert STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT + STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES == 8


# --------------------------------------------------------------------------------------
# T-E：计划为空 / 首步缺失归 B
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_e_absent_plan_is_classified_as_real_deadlock() -> None:
    """T-E：`plan_origin="refetched"` 但**连计划都没有** ⇒ 归 B（求解器确实没给东西）。

    ★ 这是 R4「误把 B 判成 A ⇒ 真死锁永不兜底」的专门防护，最危险的一类误判。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    result = _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                      first_step=None, solver_plan=None)

    assert result is None, "第 1 次 B 不发动作"
    assert planner._solver_deadlock_count == 1, "★ 计划缺失必须归 B、必须计数"
    assert planner._solver_stale_reclaim_count == 0


@pytest.mark.unit
def test_t_e_absent_first_step_with_plan_present_is_real_deadlock() -> None:
    """T-E 变体：有计划但**没有首步** ⇒ 同样归 B。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    _outcome(planner, snapshot=snapshot, plan_origin="refetched",
             first_step=None, solver_plan=_plan())

    assert planner._solver_deadlock_count == 1
    assert planner._solver_stale_reclaim_count == 0


@pytest.mark.unit
def test_t_e_absent_plan_reaches_end_turn_on_the_third_call() -> None:
    """T-E 兜底：连续 3 次「计划缺失」⇒ 第 3 次 end_turn（与 T-C 同阈值语义）。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    for _ in range(2):
        assert _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                        first_step=None, solver_plan=None) is None
    third = _outcome(planner, snapshot=snapshot, plan_origin="refetched",
                     first_step=None, solver_plan=None)

    assert third is not None and third.action_type == "end_turn"


# --------------------------------------------------------------------------------------
# T-F：★ t23 已修正语义 —— 清零的触发条件由「指纹变化」改为「求解器给出可执行首步」
# --------------------------------------------------------------------------------------
#
# ★★ 为什么必须改（t23 修复 D6/(乙)，经队长 amend inScope 授权）：
#
#   原 T-F/INV-4 断言「指纹变化 ⇒ 两计数清零」。但指纹含**手牌/能量/敌血的即时值**
#   （`loop_runner._combat_deadlock_signature`），在**卡死**局面下这些值仍会逐 tick 微动
#   ⇒ 每 tick 都被判成"新局面"⇒ 计数被反复清回 0
#   ⇒ (A) 分支的上界判定（`:1589`）**永远达不到** ⇒ 每 tick 在 (A) 分支 `return None`
#   ⇒ 破局 gate（`:1686`）**从未被求值** ⇒ 整局冻结。
#
#   实机证据（`run X9XP0L8HZWF7`，`.dsh-artifacts/_t23_gate_probe.log`，313 条 plan-call）：
#       stale    = {'0': 313}            ← 313/313 恒 0，**从未累加过**
#       deadlock = {'0':170, '3':143}    ← 143 次已达阈值，却一次都没发过 end_turn
#       plan-return action_type=end_turn = 0 / 292
#   A/B 对照（`.dsh-artifacts/_t23_conflict.log`，同一 planner 实例逐 tick 递进）：
#       保留原 T-F/INV-4（指纹变即清零） ⇒ 20 tick 内**永不脱困**
#       改为"真恢复才清零"               ⇒ 第 8 tick 脱困（= 上界 LIMIT+RETRIES）
#
#   ★ 原意的**保留**：T-F/INV-4 想表达的是「局面真换了就别背旧计数」——
#     这个意图是**对的**，只有判据选错了。正确的判据是**语义性的**：
#     「求解器基于最新局面给出了**可执行**的首步」= 真的能推进了
#     （且要求 `plan_origin == "refetched"`，因为 reused 的旧计划不可信）。
#
#   上游同构（铁律：先查上游是否有意为之）：
#     `SolverController.cs:661`/`:1189` 的 `ReplanCounts[...] = GetValueOrDefault(...) + 1`
#     **单调累计、从不清零**（全仓库 grep `ReplanCounts = new`/`.Clear` 零命中），
#     并把 `DeploymentDriftReplans`/`PlanExhaustedReplans` 当**跨重试累计**的缺陷证据上报
#     （`CombatBugReportDescription.cs:113-115`、`:194`、`:203-204`）。
#
# ★ 授权口径（队长 amaml，逐字遵守）：换触发条件、**保留原始意图**、**不删测试**。
#   故下面三条用例**仍然存在**，仍钉「清零」这件事，只把驱动方式从"改指纹"
#   换成"让求解器给出可执行首步"。


@pytest.mark.unit
def test_t_f_recovery_clears_both_counters() -> None:
    """T-F（★ t23 修正触发条件，保留原意）：求解器**真恢复** ⇒ 两计数清零。

    原判据「指纹变化」已被证伪为有害（卡死时指纹仍微动 ⇒ 清零 ⇒ 上界不可达 ⇒ 冻结）。
    新判据：「`refetched` + 首步可执行」= 求解器基于最新局面给出了能照做的动作。

    ★ 保留的原意：**局面真的能推进时，不该背旧计数**（防"上回合的过期累计到下回合"）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(energy=3)

    # 先攒出非零计数（A 分支：reused ⇒ 计划过期）
    _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
    _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
    assert planner._solver_stale_reclaim_count == 2
    assert planner._solver_deadlock_count == 0

    # ★ 变化的是"武器"而不是"指纹"：求解器给出**可执行**首步（refetched）
    good = {"kind": "play_card", "card_index": 0, "card_id": "DEFEND",
            "card_name": "防御", "target_index": None}
    result = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=good)

    assert result is None, "求解器已给出可执行首步 ⇒ 兜底函数不该发动作"
    assert planner._solver_deadlock_count == 0
    assert planner._solver_stale_reclaim_count == 0, (
        "★ 真恢复后两计数必须清零（T-F 的原意：局面能推进了就别背旧计数）"
    )
    # ★ 保留原用例对指纹字段的核对（指纹仍应被记录，只是不再驱动清零）
    assert planner._solver_deadlock_signature == "DEFEND|3|30"


@pytest.mark.unit
def test_t_f_recovery_clears_deadlock_count_too() -> None:
    """T-F 的另一半（★ t23 修正触发条件，保留原意）：B 计数同样因**真恢复**而清零。

    原用例靠"敌人掉血 ⇒ 指纹变"来清零 —— 已被证伪为有害（卡死时敌血仍可能微动）。
    新触发条件：求解器给出**可执行**首步（`refetched`）。
    ★ 保留的原意：`deadlock_count` 也不该背旧局面 —— 真恢复时同样归零。
    """
    planner = STS2HeuristicPlanner()

    # 攒 B 计数：refetched + 首步不可执行 + 指纹未变 ⇒ 真无解
    _outcome(planner, snapshot=_snapshot(enemy_hp=30), plan_origin="refetched", first_step=_stale_step())
    _outcome(planner, snapshot=_snapshot(enemy_hp=30), plan_origin="refetched", first_step=_stale_step())
    assert planner._solver_deadlock_count == 2

    # ★ 真恢复：求解器给出可执行首步（而不是靠"敌人掉血"这种指纹微动）
    good = {"kind": "play_card", "card_index": 0, "card_id": "DEFEND",
            "card_name": "防御", "target_index": None}
    _outcome(planner, snapshot=_snapshot(enemy_hp=25), plan_origin="refetched", first_step=good)

    assert planner._solver_deadlock_count == 0, "★ 真恢复 ⇒ 死锁计数清零（保留 T-F 原意）"
    assert planner._solver_stale_reclaim_count == 0


# --------------------------------------------------------------------------------------
# T-G：end_turn 不可用时不得误发
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_g_no_end_turn_action_means_no_action_at_all() -> None:
    """T-G（★ t13 已修正语义）：`available_actions` 无 `end_turn` ⇒ 不发动作，且**不计死锁**。

    ★ 本条**原期望**是「计数仍应累计（一旦窗口期到了就能立即兜底）」——**那是错的**，
      它正是 D3 缺陷：实机日志 `N.E.K.O_Plugin_sts2_autoplay_20261019.log`
      ```
      09:14:42  死锁 5/3  origin=none  手牌=[]
      09:14:43  死锁 7/3  origin=none  手牌=[]
      09:14:54  5/3  ｜ 09:14:55  7/3  ｜ 09:15:20  5/3  ｜ 09:15:31  5/3
      ```
      窗口没开时计数一路涨过阈值（5/3、7/3）⇒ `N/3` 语义自相矛盾；
      且 `end_turn` 不可用时兜底**永远不可能触发** ⇒ 若窗口再也不开，插件**永久静默**。

    ★ 修正后的判据：`end_turn` 不可用 ⇒ 这不是"求解器无解"而是"轮不到我们"，
      ⇒ **不累加 `N/3`**（清零），改用一个**独立**的窗口外计数（可观测的出口）。
      `end_turn` 是唯一永远合法的动作，它不可用时我们**没有任何合法动作可发**。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])  # ★ 没有 end_turn

    for i in range(3):
        result = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
        assert result is None, f"第 {i + 1} 次：end_turn 不可用时不得发任何动作"

    assert planner._solver_deadlock_count == 0, (
        "★ t13：非玩家行动窗口**不得**累加 N/3 —— 否则 N 会越过阈值（实机 5/3、7/3）"
    )
    assert planner._solver_out_of_window_count == 3, "应计入**独立的**窗口外计数（可观测出口）"

    # ★ 独立计数达上限后回绕，保证日志不出现越界数字、且每 LIMIT 次会再喊一次。
    for _ in range(planner._SOLVER_OUT_OF_WINDOW_LIMIT - 3):
        _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
    assert planner._solver_out_of_window_count == 0, "达上限应回绕（不越界）"


@pytest.mark.unit
def test_t13_deadlock_count_never_exceeds_its_threshold_in_a_closed_window() -> None:
    """★ t13 核心不变量：窗口关闭期间 `_solver_deadlock_count` **恒 ≤ 阈值**（实机 7/3 必不重现）。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])  # 无 end_turn = 窗口关闭

    for i in range(30):  # 远超 3 与 5
        _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
        assert planner._solver_deadlock_count <= planner._SOLVER_DEADLOCK_RETRIES, (
            f"第 {i+1} 次后死锁计数 {planner._solver_deadlock_count} 越过了阈值 "
            f"{planner._SOLVER_DEADLOCK_RETRIES} ⇒ D3 重现"
        )
        assert planner._solver_out_of_window_count <= planner._SOLVER_OUT_OF_WINDOW_LIMIT, (
            f"第 {i+1} 次后窗口外计数 {planner._solver_out_of_window_count} 越界"
        )


@pytest.mark.unit
def test_t13_window_reopening_resets_the_out_of_window_counter() -> None:
    """★ t13：窗口重新打开 ⇒ 窗口外计数清零（它只度量"连续窗口外"的时长）。"""
    planner = STS2HeuristicPlanner()
    closed = _snapshot(actions=["play_card"])           # 无 end_turn
    opened = _snapshot(actions=["play_card", "end_turn"])  # 有 end_turn

    for _ in range(3):
        _outcome(planner, snapshot=closed, plan_origin="refetched", first_step=_stale_step())
    assert planner._solver_out_of_window_count == 3

    # 窗口重开 + 真无解 ⇒ 回到玩家窗口内的 3 次兜底语义。
    _outcome(planner, snapshot=opened, plan_origin="refetched", first_step=_stale_step())
    assert planner._solver_out_of_window_count == 0, "窗口重开应清零窗口外计数"
    assert planner._solver_deadlock_count == 1, "回到玩家窗口后，真无解应照常累加 N/3"


@pytest.mark.unit
def test_t_g_stale_path_is_unaffected_by_missing_end_turn() -> None:
    """T-G 对照：A 路径本来就 return None，有没有 end_turn 都不得发动作。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["end_turn"])

    for i in range(4):
        assert _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step()) is None
        assert planner._solver_deadlock_count == 0, f"第 {i + 1} 次漂移不得计数"


@pytest.mark.unit
def test_d1_regression_three_consecutive_drifts_must_not_burn_a_turn() -> None:
    """★ D1 反向锁死（修前行为重放）：连续 3 次漂移**绝不允许**发 end_turn。

    这是本条修复的**核心可观测量**：修前 `_SOLVER_DEADLOCK_RETRIES=3` 会把
    3 次正常漂移当成真死锁 ⇒ 发 `end_turn` ⇒ **白烧一整个回合**。
    队长实测：74 条不一致里 10 条直接 end_turn，**全部**由漂移触发。

    本用例与 T-A/T-B 的区别：它**直接复刻修前会触发的那条路径**
    （同一局面、同一失效步、连续 3 次），因此任何把 A 重新混进死锁计数的
    改法都会让它转红（变异测试 M2 专门验证这一点）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    assert STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES == 3, "本用例针对 RETRIES=3 的修前行为"

    for i in range(3):
        result = _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
        assert result is None, (
            f"★ D1 回归：第 {i + 1} 次漂移发了 {result.action_type}（source={result.source}）"
            f" ⇒ 白烧一个回合，正是本条修复要消除的缺陷"
        )
        assert planner._solver_deadlock_count == 0, (
            f"★ D1 回归：第 {i + 1} 次漂移后死锁计数={planner._solver_deadlock_count}（必须为 0）"
        )


@pytest.mark.unit
def test_d1_regression_deadlock_still_works_after_being_split_out() -> None:
    """★ D1 反向锁死（防过度修复）：拆开后**真死锁**仍必须能兜底。

    只有把 A 与 B 都修坏（A 计数 / B 不计数）才能同时骗过上面那两条 —— 本用例堵住后者。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    deadlock = [
        _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
        for _ in range(3)
    ]

    assert deadlock[0] is None and deadlock[1] is None
    assert deadlock[2] is not None and deadlock[2].action_type == "end_turn", (
        "★ 真死锁必须仍能兜底，否则'修好 D1'实际是把死锁保险一起删掉了"
    )


@pytest.mark.unit
def test_d1_stale_and_deadlock_are_counted_independently() -> None:
    """A 与 B **各有独立计数器**，互不污染（这是"拆成两种计数"的字面要求）。

    交替 A/B 时：A 只动 `_solver_stale_reclaim_count`，B 只动 `_solver_deadlock_count`。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
    assert (planner._solver_stale_reclaim_count, planner._solver_deadlock_count) == (1, 0)

    _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())
    assert (planner._solver_stale_reclaim_count, planner._solver_deadlock_count) == (1, 1)

    _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())
    assert (planner._solver_stale_reclaim_count, planner._solver_deadlock_count) == (2, 1), (
        "A 不得改动死锁计数，B 不得改动过期计数"
    )


# --------------------------------------------------------------------------------------
# INV-1…INV-4：契约 §1.3 的可断言不变量
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_inv1_plan_expiry_never_increases_deadlock_count() -> None:
    """INV-1：连续 `_SOLVER_STALE_RECLAIM_LIMIT` 次带 `plan_origin="reused"` 的漂移
    ⇒ 死锁计数**恒为 0**，返回值恒为 None。

    ★ 上界的第一段（契约 §1.2 的 `if count >= LIMIT: 转入 B`）：第 1…LIMIT 次
      **全部**不计数、不发动作。第 LIMIT+1 次起才转入 B 计数（见 T-D）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    limit = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT

    for i in range(limit):
        assert _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step()) is None
        assert planner._solver_deadlock_count == 0, f"INV-1 违反于第 {i + 1} 次"
    assert planner._solver_stale_reclaim_count == limit, "前 LIMIT 次应累计到 LIMIT"


@pytest.mark.unit
def test_inv2_real_deadlock_triggers_end_turn_on_the_third_call() -> None:
    """INV-2：连续 3 次 `plan_origin="refetched"` 且首步仍不可执行 ⇒ 第 3 次 end_turn。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    for _ in range(2):
        assert _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step()) is None
    third = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=_stale_step())

    assert third is not None
    assert third.action_type == "end_turn"
    assert third.source == "solver_deadlock_breaker"


@pytest.mark.unit
def test_inv3_stale_reclaim_has_an_upper_bound() -> None:
    """INV-3：连续 A 不会无限重取 —— 第 LIMIT 次后转入 B 计数，最迟第 LIMIT+RETRIES 次 end_turn。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    limit = STS2HeuristicPlanner._SOLVER_STALE_RECLAIM_LIMIT
    retries = STS2HeuristicPlanner._SOLVER_DEADLOCK_RETRIES

    for _ in range(limit + retries - 1):
        assert _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step()) is None
    final = _outcome(planner, snapshot=snapshot, plan_origin="reused", first_step=_stale_step())

    assert final is not None and final.action_type == "end_turn", "INV-3：必须有界脱离"


@pytest.mark.unit
def test_inv4_recovery_resets_both_counters() -> None:
    """INV-4（★ t23 修正触发条件，保留原意）：**真恢复**后两计数都归零。

    ★ 原判据「改变手牌/能量/敌人血量 ⇒ 清零」已被实机证伪为**有害**：
      指纹含手牌/能量/敌血的**即时值**，卡死局面下仍会逐 tick 微动 ⇒ 每 tick 清零
      ⇒ (A) 分支上界（`:1589`）永不可达 ⇒ 破局 gate（`:1686`）从未求值 ⇒ **整局冻结**
      （实机：stale 313/313 恒 0、deadlock 143 次达阈值却 0 次发 end_turn）。

    ★ 新判据：「求解器基于最新局面给出**可执行**首步（`refetched`）」
      = 真的能推进了 ⇒ 两个计数都必须归零。
    ★ 保留的原意：**不要背旧局面的计数** —— 真恢复时两计数都清零。

    多局面形态（换手牌 / 换能量 / 换敌血）**仍然遍历**，只是每种形态都通过
    "给出可执行首步"来触发清零 —— 这样"不同局面都能正确清零"这一覆盖**没有被削弱**。
    """
    good_card = {"kind": "play_card", "card_index": 0, "card_id": "DEFEND",
                 "card_name": "防御", "target_index": None}
    for mutate in (
        lambda: _snapshot(hand=[_card("DEFEND", index=0), _card("BASH", index=1)]),
        lambda: _snapshot(energy=1),
        lambda: _snapshot(enemy_hp=7),
    ):
        planner = STS2HeuristicPlanner()

        # 旧局面上：1 次 A + 1 次 B ⇒ 两计数都非零
        _outcome(planner, snapshot=_snapshot(), plan_origin="reused", first_step=_stale_step())
        _outcome(planner, snapshot=_snapshot(), plan_origin="refetched", first_step=_stale_step())
        assert planner._solver_deadlock_count == 1
        assert planner._solver_stale_reclaim_count == 1

        # ★ 新局面上求解器给出了可执行首步 ⇒ 两计数清零
        result = _outcome(planner, snapshot=mutate(), plan_origin="refetched",
                          first_step=good_card)

        assert result is None, "求解器已给出可执行首步 ⇒ 不发动作"
        assert planner._solver_stale_reclaim_count == 0, (
            f"INV-4：真恢复 ⇒ 过期计数清零，实际 {planner._solver_stale_reclaim_count}"
        )
        assert planner._solver_deadlock_count == 0, "INV-4：真恢复 ⇒ 死锁计数清零"


# --------------------------------------------------------------------------------------
# 判据必要性：`plan_origin` 必须是**真实分支**透传，不能由 planner 猜
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_plan_origin_none_behaves_like_stale_not_like_deadlock() -> None:
    """`plan_origin="none"`（非战斗决策态/取计划失败）⇒ 归 A：不计数。

    ★ 保守方向的刻意选择：把"未知来源"当 A（不计数）比当 B（计数、可能白烧回合）安全，
      因为 A 有上界保护（T-D），最终仍会脱离；而误判成 B 会立刻烧回合。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    result = _outcome(planner, snapshot=snapshot, plan_origin="none", first_step=_stale_step())

    assert result is None
    assert planner._solver_deadlock_count == 0
    assert planner._solver_stale_reclaim_count == 1


@pytest.mark.unit
def test_refetched_plan_with_executable_first_step_is_not_stale() -> None:
    """对照：`refetched` + 首步**可执行** ⇒ 既不是 A 也不是 B（不应触碰任何计数）。

    现实中这条路径不会走到 `_combat_solver_only_outcome`（会在更早处直接出牌），
    但判据本身必须对此保守：**只有"不可执行"才算过期**，可执行绝不计数。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(hand=[_card("STRIKE", index=0)])
    step = {"kind": "play_card", "card_id": "STRIKE", "card_index": 0}

    result = _outcome(planner, snapshot=snapshot, plan_origin="refetched", first_step=step)

    assert result is None
    assert planner._solver_deadlock_count == 0
    assert planner._solver_stale_reclaim_count == 0


# --------------------------------------------------------------------------------------
# ★★ T-C2（t11 修复）：`refetched` + **取回后指纹已变** ⇒ 归 A、不计数
#
# 背景（t5 复核发现、队长独立复核证实、我亦实机复现）：
#   原判据 `plan_origin == "refetched" and 首步不可执行 ⇒ B` **不充分**。
#   求解器搜索耗时约 1s，期间手牌可能已变 ⇒ **重取回来的计划首步就已漂移**。
#   实机分类：refetched+手牌漂移 **13 条** vs refetched+真无解（药水缺失）**2 条**
#   ⇒ 该组合**主要就是漂移**，判成 B 是误报；实机死锁计数一度达 **3/3**（08:45:01），
#     只因那一 tick `end_turn_action is None` 才侥幸没烧回合。
#
# 修复：判 B 再加一条 —— **取回时的指纹 == 当前指纹**。
# --------------------------------------------------------------------------------------


@pytest.mark.unit
def test_t_c2_refetched_with_changed_signature_is_stale_not_deadlock() -> None:
    """★ T-C2 核心：`refetched` 但取回后指纹**已变** ⇒ 归 A：不发动作、**不计数**。

    这正是"重取回来的计划已经漂移"——货真价实的 A。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()  # 当前指纹 'DEFEND|3|30'

    result = _outcome(
        planner,
        snapshot=snapshot,
        plan_origin="refetched",
        first_step=_stale_step(),
        # 取回时的指纹与现在**不同** ⇒ 取回后局面变了（漂移）
        plan_fetch_signature="STRIKE,DEFEND,STRIKE|3|30",
    )

    assert result is None, "漂移不得在本 tick 发动作（应交给 loop_runner 立即重取）"
    assert planner._solver_deadlock_count == 0, (
        "★ refetched+漂移**不得**计入死锁 —— 这就是 t11 要修的误报"
        "（原判据会让 3 次漂移就白烧一整个回合）"
    )
    assert planner._solver_stale_reclaim_count == 1, "应计入独立的过期计数"


@pytest.mark.unit
def test_t_c2_refetched_with_unchanged_signature_is_still_deadlock() -> None:
    """★ T-C2 对照：取回后指纹**未变**且首步仍不可执行 ⇒ 仍是 B（真无解）。

    ★ 这是"不得为了消除误报而摘掉死锁保险"的核心断言：
      看着最新局面取回的，局面也没动，首步还是做不了 ⇒ 求解器真的给不出东西。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    result = _outcome(
        planner,
        snapshot=snapshot,
        plan_origin="refetched",
        first_step=_stale_step(),
        plan_fetch_signature="__CURRENT__",  # 取回时指纹 == 当前指纹
    )

    assert result is None, "第 1 次真 B 尚不到阈值，应返回 None 等待重算"
    assert planner._solver_deadlock_count == 1, "★ 真无解必须照常计数（否则死锁保险被摘掉）"
    assert planner._solver_stale_reclaim_count == 0, "真无解不应计入过期计数"


@pytest.mark.unit
def test_t_c2_unchanged_signature_deadlock_still_fires_end_turn_on_third_call() -> None:
    """★ T-C2 兜底保真：连续 3 次「指纹未变 + 首步不可执行」⇒ 第 3 次仍发 end_turn。

    证明了 t11 的改动**没有**削弱真无解路径的兜底能力。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    for i in (1, 2):
        assert _outcome(
            planner, snapshot=snapshot, plan_origin="refetched",
            first_step=_stale_step(), plan_fetch_signature="__CURRENT__",
        ) is None, f"第 {i} 次真 B 不应发动作（阈值是 3）"

    third = _outcome(
        planner, snapshot=snapshot, plan_origin="refetched",
        first_step=_stale_step(), plan_fetch_signature="__CURRENT__",
    )

    assert third is not None, "★ 第 3 次真无解必须兜底（不得因 t11 改动而失效）"
    assert third.action_type == "end_turn"
    assert third.source == "solver_deadlock_breaker"


@pytest.mark.unit
def test_t_c2_repeated_refetched_drifters_never_burn_a_turn_before_the_bound() -> None:
    """★ T-C2 反误报：连续 5 次（= `_SOLVER_STALE_RECLAIM_LIMIT`）「refetched + 漂移」
    ⇒ 死锁计数**始终为 0**、**从不发动作**。

    修复前：同样 5 次会把计数推到 3/3 ⇒ 第 3 次就发 end_turn（实测 4 次漂移即达 3/3）。
    修复后：5 次全走 A 路径（不计数）。

    ★ 为什么不测 10 次全为 0：第 `LIMIT+1` 次起会**按设计**转入 B 计数（上界保护，
      见 T-D/INV-3）。那是**安全属性**、不是误报 —— 若要求 10 次全不计数，
      就等于取消上界、允许无限重取卡死整局。故本条只断言"界内不烧回合"。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    for i in range(planner._SOLVER_STALE_RECLAIM_LIMIT):
        result = _outcome(
            planner, snapshot=snapshot, plan_origin="refetched",
            first_step=_stale_step(),
            plan_fetch_signature="SOME_OTHER_HAND|3|30",  # 与当前指纹不同 ⇒ 每次都是漂移
        )
        assert result is None, f"第 {i+1} 次漂移不得发动作（尤其不得 end_turn）"
        assert planner._solver_deadlock_count == 0, (
            f"第 {i+1} 次漂移后死锁计数仍须为 0，实际 {planner._solver_deadlock_count}"
        )
    assert planner._solver_stale_reclaim_count == planner._SOLVER_STALE_RECLAIM_LIMIT, (
        "5 次漂移应全部记入独立的过期计数"
    )


@pytest.mark.unit
def test_t_c2_empty_fetch_signature_falls_back_to_deadlock_for_safety() -> None:
    """★ 保守方向：调用方**没提供** `plan_fetch_signature`（空串）⇒ 按"未变"处理 ⇒ 仍是 B。

    理由（安全属性）：把真无解误判成 A 会导致**无限重取、卡死整局**，
    比误报一次兜底更危险。故"未知"必须偏向 B。
    这也保证非 loop_runner 的既有调用点语义不变。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    result = _outcome(
        planner, snapshot=snapshot, plan_origin="refetched",
        first_step=_stale_step(), plan_fetch_signature="",
    )

    assert result is None
    assert planner._solver_deadlock_count == 1, "空指纹按保守口径仍归 B"


@pytest.mark.unit
def test_t_c2_stale_reclaim_limit_still_transfers_to_deadlock() -> None:
    """★ 上界仍成立：连续 A（漂移）超 `_SOLVER_STALE_RECLAIM_LIMIT` 后仍会转入 B 计数。

    证明 t11 没有破坏「S ≤ LIMIT+RETRIES = 8」这条安全属性
    —— 即便每次都是"refetched+漂移"，也不可能无限重取。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()

    # 前 LIMIT 次（5 次）：不计数、不发动作。
    for i in range(planner._SOLVER_STALE_RECLAIM_LIMIT):
        assert _outcome(
            planner, snapshot=snapshot, plan_origin="refetched",
            first_step=_stale_step(), plan_fetch_signature="DRIFTED|3|30",
        ) is None, f"第 {i+1} 次漂移应在不计数区内"
    assert planner._solver_deadlock_count == 0

    # 第 LIMIT+1 次起转入 B 计数 ⇒ 最迟第 LIMIT+RETRIES 次发 end_turn。
    outcome = None
    for _ in range(planner._SOLVER_DEADLOCK_RETRIES):
        outcome = _outcome(
            planner, snapshot=snapshot, plan_origin="refetched",
            first_step=_stale_step(), plan_fetch_signature="DRIFTED|3|30",
        )
    assert outcome is not None, "★ 上界必须生效：不得无限重取卡死整局"
    assert outcome.action_type == "end_turn"
