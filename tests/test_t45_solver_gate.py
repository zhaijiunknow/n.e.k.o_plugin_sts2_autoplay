"""t45：求解器**准入门槛**（hard gate）—— 「没算出结果就一张牌都不打」。

## 本文件要钉死的东西（对应任务书的验收 1 与 3）

1. **gate 拦截**：`in_combat` / `search_status` / `line` / 首步可执行 / 指纹一致
   五条里任一不满足 ⇒ 本 tick **不发任何动作**（含 end_turn），也不落回启发式。
2. **`waiting_solver` 不计死锁**：反复等待既不涨 `_solver_deadlock_count`，也不涨
   `_solver_stale_reclaim_count` ⇒ **上界不会被"等待"消耗掉**（任务书第 3 条）。
3. **`solver_cannot_act` 走上界**：求解器已给出完整结果、首步仍不可执行、且取回后指纹未变
   ⇒ 这才是真无解 ⇒ 计数到 `_SOLVER_DEADLOCK_RETRIES` 后发 `end_turn` 破局（不死锁）。
4. **单飞（single-flight）**：一次 `/solver/plan` 尚未返回时，并发 tick **不得**再发第二次；
   并发者复用同一次结果（`_fetch_combat_plan_single_flight`）。
5. **`search_status` 值域与 C# 侧同源**：直接用 `game_mod` 里的 `MapStatus` /
   `SearchBoundaryReason` 源文件核对 —— 判据不许"猜"。

★ 本文件全程**不碰**真实 18080 端口（hermetic）：所有客户端都是 in-process stub。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import (
    GATE_ACT,
    GATE_CANNOT_ACT,
    GATE_WAITING,
    SOLVER_SEARCH_STATUSES,
    STS2LoopRunner,
    _combat_deadlock_signature,
    adaptive_tick_interval,
    solver_play_gate,
)

pytestmark = pytest.mark.unit


# ══════════════════════════════════════════════════════════════════════════════
# 夹具：一个"手牌可打、窗口开着"的战斗局面（旧实现会在这种局面上出牌）
# ══════════════════════════════════════════════════════════════════════════════


def _card(card_id: str = "STRIKE", *, index: int = 0, playable: bool = True) -> dict:
    return {
        "index": index,
        "card_id": card_id,
        "name": card_id,
        "energy_cost": 1,
        "playable": playable,
        "requires_target": False,
    }


def _snapshot(*, hand: list[dict] | None = None, energy: int = 3, enemy_hp: int = 30,
              actions: list[str] | None = None) -> dict:
    return {
        "screen": "COMBAT",
        "in_combat": True,
        "floor": 5,
        "available_actions": [
            {"type": name, "raw": {"name": name}}
            for name in (actions if actions is not None else ["play_card", "end_turn"])
        ],
        "raw_state": {
            "run_id": "T45RUN",
            "turn": 1,
            "combat": {
                "turn": 1,
                "player": {"energy": energy, "current_hp": 60, "max_hp": 80},
                "hand": hand if hand is not None else [_card()],
                "enemies": [{"index": 0, "enemy_id": "SLIME", "current_hp": enemy_hp, "is_alive": True}],
            },
            "run": {"potions": []},
        },
    }


def _plan(*steps: dict, search_status: Any = "complete", in_combat: Any = True) -> dict:
    plan: dict[str, Any] = {
        "in_combat": in_combat,
        "search_status": search_status,
        "state_fingerprint": "DEADBEEFCAFE0000",
        "budget_ms": 1234,
        "nodes_expanded": 4321,
        "line": [{"turn": 1, "steps": list(steps)}],
    }
    return plan


def _strike_step() -> dict:
    return {"kind": "play_card", "card_id": "STRIKE", "card_index": 0}


def _missing_step() -> dict:
    """一张不在手牌里的牌 ⇒ 首步不可执行（手牌默认只有 STRIKE）。"""
    return {"kind": "play_card", "card_id": "BASH", "card_index": 7}


def _ctx(*, plan: dict | None, snapshot: dict, origin: str = "refetched",
         fetch_signature: str | None = None, step_index: int = 0) -> dict:
    return {
        "classification": {"state_name": "combat", "screen_class": "combat"},
        "summary_context": {},
        "strategy_context": {},
        "snapshot": snapshot,
        "mode": {"allows_planner": True},
        "solver_plan": plan,
        # ★ 生产里由 `loop_runner._planner_context` 透传（t37 的单一工厂）。
        "plan_origin": origin,
        "plan_fetch_signature": (
            _combat_deadlock_signature(snapshot) if fetch_signature is None else fetch_signature
        ),
        "solver_step_index": step_index,
    }


class _CaptureLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _emit(self, message: Any, *args: Any, **kwargs: Any) -> None:
        try:
            self.lines.append(str(message) % args if args else str(message))
        except Exception:
            self.lines.append(str(message))

    debug = info = warning = error = exception = _emit

    def joined(self) -> str:
        return "\n".join(self.lines)


# ══════════════════════════════════════════════════════════════════════════════
# ① search_status 值域必须与 C# 侧同源（判据不许猜）
# ══════════════════════════════════════════════════════════════════════════════


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_search_status_domain_matches_the_csharp_mapper() -> None:
    """`SOLVER_SEARCH_STATUSES` 必须**逐项**等于 `MapStatus` 的值域。

    依据（两处源文件都在本仓库里，故本用例 hermetic）：
      · `game_mod/nekospire/Solver/CombatSolverFacade.cs` 的 `MapStatus`
        （None→"complete"、TimeLimit→"budget_exceeded"、其它→"boundary_"+小写枚举名）
      · `game_mod/nekospire/Solver/Search/CombatPlan.cs` 的 `SearchBoundaryReason` 枚举

    ★ 为什么必须在测试里核对、而不只是写在注释里：注释会腐坏；而这条值域一旦悄悄变窄，
      闸门就会把"求解器其实算完了"判成"没算完" ⇒ 永久等待 ⇒ 卡死（反之亦然）。
    """
    facade = _repo_root() / "game_mod" / "nekospire" / "Solver" / "CombatSolverFacade.cs"
    plan_cs = _repo_root() / "game_mod" / "nekospire" / "Solver" / "Search" / "CombatPlan.cs"
    if not facade.is_file() or not plan_cs.is_file():
        pytest.skip("game_mod 源文件不在（只装了插件产物）⇒ 无法核对值域")

    enum_text = plan_cs.read_text(encoding="utf-8")
    match = re.search(
        r"enum SearchBoundaryReason\s*\{(?P<body>.*?)\}", enum_text, re.S
    )
    assert match is not None, "CombatPlan.cs 里找不到 SearchBoundaryReason 枚举"
    body = re.sub(r"//[^\n]*", "", match.group("body"))
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    members = [m for m in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body)]
    # 去掉 XML 文档注释残留词与数字
    members = [m for m in members if m not in {"summary", "para", "remarks"} and not m.isdigit()]
    assert "TimeLimit" in members and "None" in members, f"枚举解析异常：{members}"

    expected = {"complete", "budget_exceeded"} | {
        "boundary_" + m.lower() for m in members if m not in {"None", "TimeLimit"}
    }
    assert SOLVER_SEARCH_STATUSES == expected, (
        "闸门的 search_status 值域与 C# 侧 MapStatus 不一致：\n"
        "  多出 = %s\n  缺少 = %s"
        % (sorted(SOLVER_SEARCH_STATUSES - expected), sorted(expected - SOLVER_SEARCH_STATUSES))
    )
    assert "complete" in SOLVER_SEARCH_STATUSES


def test_gate_rejects_an_unknown_search_status() -> None:
    """不认识的 search_status（例如未来新增的 boundary）⇒ 等待，不是放行。

    ★ 方向性论证：宁可在"求解器给了未知状态"时多等一 tick（下一 tick 重取），
      也不能把未知状态当成"算完了"→ 那就是把旧的"有 line 就出牌"原样放过。
      真正"没算完"的形态（`Failed(...)`）实测就是 `search_status: null`（见下一条用例）。
    """
    snapshot = _snapshot()
    gate = solver_play_gate(
        solver_plan=_plan(_strike_step(), search_status="boundary_brand_new"),
        snapshot=snapshot,
        first_step=_strike_step(),
        plan_origin="refetched",
        plan_fetch_signature=_combat_deadlock_signature(snapshot),
    )
    assert gate["verdict"] == GATE_WAITING
    assert gate["missing"] == "search_status"


# ══════════════════════════════════════════════════════════════════════════════
# ② gate 拦截：五条判据逐个不满足
# ══════════════════════════════════════════════════════════════════════════════


def test_gate_passes_when_all_four_conditions_hold() -> None:
    """四条件齐备 ⇒ 放行（这是"能出牌"的唯一路径，先把基线立住）。"""
    snapshot = _snapshot()
    gate = solver_play_gate(
        solver_plan=_plan(_strike_step()),
        snapshot=snapshot,
        first_step=_strike_step(),
        plan_origin="refetched",
        plan_fetch_signature=_combat_deadlock_signature(snapshot),
    )
    assert gate["verdict"] == GATE_ACT, gate
    assert gate["missing"] == ""


@pytest.mark.parametrize(
    "case,plan,in_combat_step,origin,fetch_sig,expect_missing",
    [
        ("no_plan", None, _strike_step(), "refetched", "auto", "solver_plan"),
        ("not_in_combat", _plan(_strike_step(), in_combat=False), _strike_step(), "none", "auto", "in_combat"),
        ("status_missing", {"in_combat": True, "line": [{"turn": 1, "steps": [_strike_step()]}]},
         _strike_step(), "refetched", "auto", "search_status"),
        ("status_null", _plan(_strike_step(), search_status=None), _strike_step(), "refetched", "auto", "search_status"),
        ("line_empty", _plan(), None, "refetched", "auto", "line"),
        ("step_not_executable", _plan(_missing_step()), _missing_step(), "refetched", "auto", "executable"),
        ("fingerprint_drift", _plan(_strike_step()), _strike_step(), "refetched", "SOME_OTHER_STATE", "fingerprint"),
        ("origin_unknown", _plan(_strike_step()), _strike_step(), "none", "auto", "fingerprint"),
    ],
)
def test_gate_blocks_each_missing_condition(case, plan, in_combat_step, origin, fetch_sig, expect_missing) -> None:
    """任一条件不满足都必须被拦下，并**说清楚缺的是哪一条**。"""
    snapshot = _snapshot()
    signature = _combat_deadlock_signature(snapshot) if fetch_sig == "auto" else fetch_sig
    gate = solver_play_gate(
        solver_plan=plan,
        snapshot=snapshot,
        first_step=in_combat_step,
        plan_origin=origin,
        plan_fetch_signature=signature,
    )
    assert gate["verdict"] != GATE_ACT, f"{case} 不该放行"
    assert gate["missing"] == expect_missing, f"{case}: 应在等 {expect_missing}，实际 {gate['missing']}"
    assert gate["why"], "拦下时必须给出人类可读原因（日志要能回答'在等什么'）"


def test_planner_emits_nothing_at_all_when_the_gate_blocks() -> None:
    """★ 核心语义：闸门拦下时，`plan()` 返回 None —— 不出牌、不 end_turn、不落启发式。

    这个局面是**旧实现一定会出牌**的局面：手牌有可打的 STRIKE、能量够、窗口开着，
    计划里也有 `line[0].steps[0]`。唯一的差别是 `search_status` 缺失（= 求解器没算出结果）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    plan = {"in_combat": True, "line": [{"turn": 1, "steps": [_strike_step()]}]}  # 没有 search_status

    for _ in range(6):
        result = planner.plan(_ctx(plan=plan, snapshot=snapshot))
        assert result is None, f"闸门没拦住：{result!r}"

    assert planner._solver_gate_allowed_count == 0
    assert planner._solver_gate_wait_count >= 6


def test_planner_never_falls_back_to_heuristic_in_combat() -> None:
    """★ 战斗内**没有**启发式兜底出牌（任务书验收 3 的机制性保证）。

    构造"启发式一定会出手"的局面（手牌可打、能量充足），但求解器结果不合格
    ⇒ 结果必须是 None，且**任何**返回值都不允许 `source == "heuristic"`。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(hand=[_card("STRIKE"), _card("DEFEND", index=1)], energy=3)
    for plan in (
        None,
        {"in_combat": False, "warnings": ["CombatSolver 搜索失败: 只能在玩家出牌阶段计算。"]},
        {"in_combat": True, "search_status": None, "line": []},
        _plan(_missing_step()),
    ):
        result = planner.plan(_ctx(plan=plan, snapshot=snapshot))
        if result is not None:
            assert result.source != "heuristic", f"战斗内出现启发式出牌：{result!r}"


# ══════════════════════════════════════════════════════════════════════════════
# ③ waiting_solver 不计死锁：上界不得被"等待"消耗掉
# ══════════════════════════════════════════════════════════════════════════════


def test_waiting_ticks_do_not_consume_the_deadlock_bound() -> None:
    """反复"等待"（求解器没算出结果）⇒ 两个计数都**不动**，且永不发 end_turn。

    ★ 这是本次任务第 3 条的字面要求：「waiting_solver ⇒ 不计入死锁，继续等；
      上界不得被'等待'消耗掉」。
      tick 次数刻意取 `LIMIT + RETRIES + 5`（超过既有上界 8），若实现把等待也计进上界，
      第 8 个 tick 就会冒出 `end_turn` ⇒ 本用例转红。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    plan = {"in_combat": True, "search_status": None, "line": [{"turn": 1, "steps": [_strike_step()]}]}
    ticks = planner._SOLVER_STALE_RECLAIM_LIMIT + planner._SOLVER_DEADLOCK_RETRIES + 5

    for _ in range(ticks):
        result = planner.plan(_ctx(plan=plan, snapshot=snapshot))
        assert result is None, f"等待期不该发任何动作，实际 {result!r}"

    assert planner._solver_deadlock_count == 0, "等待不得计入死锁"
    assert planner._solver_stale_reclaim_count == 0, "等待不得计入过期重取上界"
    assert planner._solver_gate_wait_count == ticks


def test_waiting_on_drift_also_does_not_consume_the_bound() -> None:
    """「局面正漂移」（refetched 但取回后指纹已变）同样归等待 ⇒ 不计数。

    构造：计划合法、首步可执行，但 `plan_fetch_signature` 与当前局面不符
    （= 搜索期间手牌/能量/敌血变了）。旧实现会把它算进 (A) 的过期上界。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    ticks = planner._SOLVER_STALE_RECLAIM_LIMIT + planner._SOLVER_DEADLOCK_RETRIES + 3
    for _ in range(ticks):
        result = planner.plan(
            _ctx(plan=_plan(_strike_step()), snapshot=snapshot, fetch_signature="STALE|0|999")
        )
        assert result is None
    assert planner._solver_stale_reclaim_count == 0
    assert planner._solver_deadlock_count == 0
    assert planner._solver_gate_wait_count == ticks


def test_waiting_is_observable_in_the_log() -> None:
    """等待必须留痕，且**行内自带累计计数**（否则"插件没动"与"插件卡死"无法区分）。

    两段：
      · 等待期（节流）：第一次等待立刻一行，之后每 `_SOLVER_GATE_LOG_EVERY` 次一行；
      · 恢复放行时一行**收口行**，把累计的 allowed/waiting/cannot_act 精确写出来
        ⇒「被 gate 拦下的 tick 数」可以逐字读，不靠数日志行。
    """
    logger = _CaptureLogger()
    planner = STS2HeuristicPlanner(logger=logger)
    snapshot = _snapshot()
    for _ in range(7):
        planner.plan(_ctx(plan={"in_combat": False}, snapshot=snapshot))

    text = logger.joined()
    assert "[sts2_solver_gate]" in text, f"等待没有留痕：{text!r}"
    assert "waiting_solver" in text
    assert "in_combat" in text, "日志要说明在等哪一条（in_combat）"
    assert "waiting=1" in text, f"第一次等待就要带累计计数，实际：{text!r}"

    # 恢复放行 ⇒ 收口行给出精确累计值
    result = planner.plan(_ctx(plan=_plan(_strike_step()), snapshot=snapshot))
    assert result is not None and result.source == "mod_solver"
    resume = [ln for ln in logger.lines if "恢复放行" in ln]
    assert resume, f"放行时应有收口行：{logger.joined()!r}"
    assert "waiting=7" in resume[-1], f"收口行要写出精确累计等待次数，实际：{resume[-1]!r}"
    assert "allowed=1" in resume[-1]


# ══════════════════════════════════════════════════════════════════════════════
# ④ solver_cannot_act 走上界：真无解才兜底，且必须真的能脱困（不死锁）
# ══════════════════════════════════════════════════════════════════════════════


def test_end_turn_step_with_a_closed_window_waits_instead_of_counting() -> None:
    """首步是 `end_turn` 但窗口没开 ⇒ 归**等待**（不计死锁），不是"求解器无解"。

    ★ 实机形态（2026-10-21 08:06:38）：上一张牌结算动画没走完时，mod 的
      `available_actions` 会短暂退化成 `['save_and_quit']`，而求解器计划以 `end_turn` 结尾
      ⇒ 旧分类会把它算成 `solver_cannot_act`，语义上却是"轮不到我们动手"。
      t13/D3 已裁定窗口没开不该累加 N/3 ⇒ 本条把它归等待，上界不被"等窗口"消耗。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])  # 窗口关：没有 end_turn
    end_turn_plan = _plan({"kind": "end_turn"})
    ticks = planner._SOLVER_DEADLOCK_RETRIES + 4
    for _ in range(ticks):
        result = planner.plan(_ctx(plan=end_turn_plan, snapshot=snapshot))
        assert result is None
    assert planner._solver_gate_cannot_act_count == 0, "窗口没开不是『求解器无解』"
    assert planner._solver_deadlock_count == 0
    assert planner._solver_gate_wait_count == ticks
    assert planner._solver_gate_last.get("missing") == "window"


def test_cannot_act_counts_and_fires_end_turn_at_the_bound() -> None:
    """「已算出结果 + 首步不可执行 + 指纹未变」⇒ 计数，并在第 RETRIES 次发 end_turn。

    ★ 这是"不许死锁"的那一半：求解器对某局面持续给出打不出的牌时，纯等待会永久静默
      ⇒ 必须有一个**有界**的出口。上界语义沿用既有 `_SOLVER_DEADLOCK_RETRIES`（不另造）。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()  # end_turn 在 available_actions 里 ⇒ 窗口开着
    step = _missing_step()

    fired_at = None
    for tick in range(1, planner._SOLVER_DEADLOCK_RETRIES + 2):
        result = planner.plan(_ctx(plan=_plan(step), snapshot=snapshot))
        if result is not None:
            fired_at = tick
            assert result.action_type == "end_turn"
            assert result.source == "solver_deadlock_breaker"
            break

    assert fired_at is not None, "真无解时上界没有落地 ⇒ 永久等待 = 卡死"
    assert fired_at == planner._SOLVER_DEADLOCK_RETRIES, (
        "破局应在第 %d 次触发，实际第 %s 次" % (planner._SOLVER_DEADLOCK_RETRIES, fired_at)
    )
    assert planner._solver_gate_cannot_act_count >= planner._SOLVER_DEADLOCK_RETRIES


def test_cannot_act_requires_an_unchanged_fingerprint() -> None:
    """取回后指纹**已变**时不能判"真无解"—— 那只是漂移（归等待）。

    ★ 判据来源：t11 的实机统计（refetched + 手牌漂移 13 条 vs 真无解 2 条）。
      把漂移误判成 B，就会白烧一整回合 —— 这正是本次改造要避免的"抢跑的另一面"。
    """
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot()
    for _ in range(planner._SOLVER_DEADLOCK_RETRIES + 2):
        result = planner.plan(
            _ctx(plan=_plan(_missing_step()), snapshot=snapshot, fetch_signature="DRIFTED|1|7")
        )
        assert result is None, "漂移不是真无解 ⇒ 不许烧回合"
    assert planner._solver_deadlock_count == 0
    assert planner._solver_gate_cannot_act_count == 0


def test_cannot_act_does_not_fire_when_the_window_is_closed() -> None:
    """非玩家窗口（无 end_turn）时不许发 end_turn，也不许把 N/3 顶出阈值。"""
    planner = STS2HeuristicPlanner()
    snapshot = _snapshot(actions=["play_card"])  # 窗口关
    for _ in range(planner._SOLVER_DEADLOCK_RETRIES + 4):
        result = planner.plan(_ctx(plan=_plan(_missing_step()), snapshot=snapshot))
        assert result is None
    assert planner._solver_deadlock_count < planner._SOLVER_DEADLOCK_RETRIES, (
        "窗口关时不得把死锁计数顶到阈值（否则下一 tick 一开窗就白烧回合）"
    )


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 单飞（single-flight）：一次搜索尚未返回时不得并发重复请求
# ══════════════════════════════════════════════════════════════════════════════


class _BlockingPlanClient:
    """`get_combat_plan()` 会"卡住"直到测试放行，用来暴露并发重复请求。"""

    def __init__(self, plan: dict[str, Any]) -> None:
        self._plan = plan
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.gate = asyncio.Event()

    async def get_combat_plan(self) -> dict[str, Any]:
        self.calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await self.gate.wait()
            return dict(self._plan)
        finally:
            self.concurrent -= 1


class _Harness:
    """只实现 `_fetch_combat_plan_single_flight` 需要的两个属性（logger / fetch 计数）。"""

    def __init__(self, runner: STS2LoopRunner) -> None:
        self._runner = runner

    def __getattr__(self, item: str) -> Any:
        return getattr(self._runner, item)


def _runner_with_client(client: Any) -> STS2LoopRunner:
    class _Svc:
        logger = _CaptureLogger()

    runner = STS2LoopRunner(_Svc())
    return runner


@pytest.mark.asyncio
async def test_single_flight_coalesces_concurrent_plan_requests() -> None:
    """★ 两个并发 tick 取计划 ⇒ 打到 mod 的请求**只有 1 次**，另一个复用同一结果。"""
    plan = _plan(_strike_step())
    client = _BlockingPlanClient(plan)
    runner = _runner_with_client(client)

    first = asyncio.ensure_future(runner._fetch_combat_plan_single_flight(client))
    second = asyncio.ensure_future(runner._fetch_combat_plan_single_flight(client))
    # 让两个协程都跑到"正在等搜索"的位置
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert client.max_concurrent == 1, (
        f"★ 出现了并发请求（max_concurrent={client.max_concurrent}）—— 那正是本次要修的抢跑形态"
    )
    client.gate.set()
    r1, r2 = await asyncio.gather(first, second)

    assert client.calls == 1, f"只应真的打一次 /solver/plan，实际 {client.calls}"
    assert r1 == plan and r2 == plan
    assert runner._solver_fetch_count == 1
    assert runner._solver_fetch_coalesced == 1


@pytest.mark.asyncio
async def test_sequential_requests_are_not_coalesced() -> None:
    """串行两次取计划必须真的取两次（合并只发生在"在飞"期间，不能变成永久缓存）。"""
    plan = _plan(_strike_step())

    class _Client:
        def __init__(self) -> None:
            self.calls = 0

        async def get_combat_plan(self) -> dict[str, Any]:
            self.calls += 1
            return dict(plan)

    client = _Client()
    runner = _runner_with_client(client)
    await runner._fetch_combat_plan_single_flight(client)
    await runner._fetch_combat_plan_single_flight(client)
    assert client.calls == 2
    assert runner._solver_fetch_coalesced == 0


@pytest.mark.asyncio
async def test_single_flight_records_latency_for_pacing() -> None:
    """取计划时必须记下**实测耗时**与 mod 自报 `budget_ms`（自适应节拍的输入）。"""

    class _Client:
        async def get_combat_plan(self) -> dict[str, Any]:
            await asyncio.sleep(0.001)
            return dict(_plan(_strike_step()))

    runner = _runner_with_client(_Client())
    await runner._fetch_combat_plan_single_flight(_Client())
    assert runner._solver_last_fetch_seconds > 0.0
    assert runner._solver_last_budget_ms == 1234
    assert runner._solver_last_search_status == "complete"
    assert runner._solver_fetch_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# ⑥ 自适应节拍（第 5 条）：间隔由实测耗时决定，不再是固定的短 sleep
# ══════════════════════════════════════════════════════════════════════════════


def test_adaptive_interval_falls_back_to_the_configured_base() -> None:
    """还没量到求解器耗时 ⇒ 用配置值（1.5s）—— 行为与旧实现一致。"""
    assert adaptive_tick_interval(1.5) == pytest.approx(1.5)


def test_adaptive_interval_never_ticks_faster_than_the_last_search() -> None:
    """上次搜索 3s ⇒ 间隔不许是 1.5s（否则搜索刚落就又发一次，back-to-back 重算）。"""
    assert adaptive_tick_interval(1.5, measured_seconds=3.0) == pytest.approx(3.0)


def test_adaptive_interval_keeps_the_configured_value_when_the_search_is_fast() -> None:
    """搜索比配置间隔还快 ⇒ 仍按配置走（玩家的 `action_interval_seconds` 不被覆盖）。"""
    assert adaptive_tick_interval(1.5, measured_seconds=0.2) == pytest.approx(1.5)


def test_adaptive_interval_is_capped() -> None:
    """8s 的搜索不许把间隔拖到 8s（上限 3s），否则一回合要等十几秒。"""
    capped = adaptive_tick_interval(1.5, measured_seconds=8.0)
    assert capped == pytest.approx(3.0)
    assert capped < 8.0


def test_adaptive_interval_tolerates_garbage_input() -> None:
    """配置/耗时缺失或非数值 ⇒ 回落到配置值，绝不抛异常（这条路径每 tick 都跑）。"""
    assert adaptive_tick_interval(1.5, measured_seconds="oops") == pytest.approx(1.5)
    assert adaptive_tick_interval(None, measured_seconds=2.0) == pytest.approx(2.0)
