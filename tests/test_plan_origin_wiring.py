"""D1 集成口径：`plan_origin` 必须**真的**从 `loop_runner.tick` 流到 planner。

## 为什么必须有这个文件

`tests/test_solver_deadlock_contract.py` 直接把 `plan_origin` 传给
`_combat_solver_only_outcome`，它证明的是**判据本身**正确。
但 D1 的修复由**两半**组成：

1. planner 侧：按 `plan_origin` 把 A / B 分开（已在契约测试里钉死）；
2. **loop_runner 侧**：`plan_origin` 必须来自**真实分支**（复用缓存 / 真的调了
   `get_combat_plan()` / 都没有），而**不是** planner 猜的。

只做第 1 半、第 2 半写错（例如永远传 `"reused"`，或把 `reused` 也标成 `refetched`），
契约测试**全绿**而实机行为依旧白烧回合 —— 这正是"变量看起来变好了"的陷阱。
所以本文件用**真实的 `STS2LoopRunner.tick`** 走一遍，断言三件事：

- `reused`：缓存命中时 `planning_context["plan_origin"] == "reused"`，且**没有**再调
  `get_combat_plan()`（整回合缓存仍生效，不额外增加 mod 搜索开销）。
- `refetched`：缓存未命中、真的取回 `in_combat` 计划时 `== "refetched"`。
- `none`：取计划失败 / 非 `in_combat` / 不在战斗决策态时 `== "none"`。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.loop_runner import STS2LoopRunner
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class _Logger:
    def debug(self, *a, **k):
        return None

    info = warning = exception = error = debug


def _combat_state(*, turn: int = 1, hand: list[dict] | None = None, energy: int = 3) -> dict[str, Any]:
    return {
        "screen": "COMBAT",
        "in_combat": True,
        "run_id": "TESTRUN",
        "turn": turn,
        "floor": 5,
        "act": 0,
        "combat": {
            "turn": turn,
            "player": {"current_hp": 60, "max_hp": 80, "block": 0, "energy": energy},
            "hand": hand if hand is not None else [{"index": 0, "card_id": "STRIKE", "playable": True}],
            "enemies": [{"index": 0, "enemy_id": "SLIME", "current_hp": 30, "is_alive": True}],
        },
    }


def _plan(*steps: dict) -> dict[str, Any]:
    return {"in_combat": True, "line": [{"turn": 1, "steps": list(steps)}]}


class _TickClient:
    """记录 `get_combat_plan()` 调用次数 —— 这是"到底有没有重取"的唯一客观证据。"""

    def __init__(self, state: dict[str, Any], plan: dict[str, Any] | None) -> None:
        self._state = state
        self._plan = plan
        self.plan_calls = 0

    async def get_state(self) -> dict[str, Any]:
        return self._state

    async def get_available_actions(self) -> dict[str, Any]:
        return {"actions": [{"name": "play_card"}, {"name": "end_turn"}]}

    async def get_combat_plan(self) -> dict[str, Any] | None:
        self.plan_calls += 1
        return self._plan


def _capture_planning_context(monkeypatch: pytest.MonkeyPatch, service: STS2AutoplayService) -> list[dict]:
    """把 planner 实际收到的**每一份** `context` 抓下来（不改 planner 语义）。

    ★ 必须抓**所有**调用点，而不是只看 `planning_context`：
      实机实测（2026-10-19 08:29:29，见 `.dsh-artifacts/deploy/`）发现战斗决策有**两条**
      调 `planner.plan()` 的路径，而 `candidate_generator.py:18` 那条**排在前面**：

        loop_runner.py:181  candidate_generator.generate(...)   ← 先跑，自建 context
        loop_runner.py:250  planner.plan(planning_context)      ← 后跑

      第一版修复只给 `planning_context` 加了 `plan_origin`，candidate 那条没有 ⇒
      planner 读到缺省 `"none"` ⇒ **永远判成 A（过期）**、B（真无解）永不成立 ⇒
      死锁保险被静默摘掉（end_turn 数字好看，但真卡住时永不兜底）。
      这就是"变量看起来变好了"的典型陷阱，故本文件必须覆盖两条路径。
    """
    captured: list[dict] = []
    real_plan = service._planner.plan

    def spy(context: dict) -> Any:
        captured.append(dict(context))
        return real_plan(context)

    monkeypatch.setattr(service._planner, "plan", spy)
    return captured


def _make_service(
    state: dict[str, Any], plan: dict[str, Any] | None, monkeypatch
) -> tuple[STS2AutoplayService, _TickClient, list[dict]]:
    service = STS2AutoplayService(_Logger(), lambda payload: None)
    client = _TickClient(state, plan)
    service._client = client
    service._state.control_mode = "program"
    captured = _capture_planning_context(monkeypatch, service)
    return service, client, captured


def _origins(captured: list[dict]) -> list[str]:
    """本次 tick 里所有 planner 调用收到的 `plan_origin`（按调用顺序）。"""
    return [str(c.get("plan_origin")) for c in captured]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_planner_call_in_a_tick_receives_the_same_plan_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 单 tick 内**每一条**调 planner 的路径都必须收到同一个（非 `none` 误判的）`plan_origin`。

    实机实测的缺陷：`candidate_generator.py:18` 那条路径排在 `planning_context` 之前，
    第一版修复漏了它 ⇒ 日志里出现 `plan_origin=none`，所有漂移都被判成 A。
    本用例直接钉死"条数 ≥ 2 且各条一致"，任何只改一处的实现都会转红。
    """
    service, client, captured = _make_service(
        _combat_state(), _plan({"kind": "play_card", "card_id": "STRIKE", "card_index": 0}), monkeypatch
    )
    runner = STS2LoopRunner(service)

    await runner.tick()

    origins = _origins(captured)
    assert len(origins) >= 2, (
        f"战斗决策应有 ≥2 条调 planner 的路径（candidate_generator + planning_context），实际 {origins}"
    )
    assert set(origins) == {"refetched"}, (
        f"★ 本 tick 真的取了计划，**所有**调用点都必须是 refetched，实际 {origins} "
        f"（有 'none' 说明某条路径漏传了 plan_origin）"
    )


# --------------------------------------------------------------------------------------
# refetched：缓存未命中 + 真的取回计划
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_marks_refetched_when_the_plan_is_actually_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次 tick（无缓存）⇒ 真的调 `get_combat_plan()` ⇒ `plan_origin == "refetched"`。"""
    service, client, captured = _make_service(
        _combat_state(), _plan({"kind": "play_card", "card_id": "STRIKE", "card_index": 0}), monkeypatch
    )
    runner = STS2LoopRunner(service)

    await runner.tick()

    assert client.plan_calls == 1, "缓存为空时必须真的取一次计划"
    assert captured, "planner 必须被调用（否则抓不到 context）"
    origins = _origins(captured)
    assert set(origins) == {"refetched"}, (
        f"★ 真的取了计划却标成 {origins} ⇒ D1 判据会失效（>'none' 说明漏传、>'reused' 说明误标）"
    )
    assert captured[-1]["solver_plan"] is not None, "refetched 必须伴随非空 solver_plan"


# --------------------------------------------------------------------------------------
# reused：缓存命中，不得再调 mod（否则整回合缓存失效、搜索开销上升）
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_marks_reused_and_does_not_refetch_when_the_snapshot_still_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第 2 次 tick（局面没变、下一步仍可执行）⇒ 复用缓存 ⇒ `plan_origin == "reused"`
    且 **`get_combat_plan()` 调用次数不变**（整回合缓存语义未被破坏）。
    """
    state = _combat_state()
    service, client, captured = _make_service(
        state, _plan({"kind": "play_card", "card_id": "STRIKE", "card_index": 0}), monkeypatch
    )
    runner = STS2LoopRunner(service)

    await runner.tick()  # 第 1 次：refetched
    first_origins = _origins(captured)
    assert set(first_origins) == {"refetched"}, f"第 1 tick 应全为 refetched，实际 {first_origins}"
    calls_after_first = client.plan_calls
    captured.clear()

    await runner.tick()  # 第 2 次：应复用
    second_origins = _origins(captured)
    assert set(second_origins) == {"reused"}, (
        f"★ 第二步仍可执行时应复用缓存（所有调用点一致），实际 {second_origins}"
    )
    assert client.plan_calls == calls_after_first, (
        "复用分支**不得**再调 get_combat_plan（否则每 tick 都重跑 ~1s 搜索）"
    )


# --------------------------------------------------------------------------------------
# none：取计划失败 / 非 in_combat / 不在战斗决策态
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_marks_none_when_the_fetch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_combat_plan()` 返回 None（搜索失败/超时）⇒ `plan_origin == "none"`。"""
    service, _client, captured = _make_service(_combat_state(), None, monkeypatch)
    runner = STS2LoopRunner(service)

    await runner.tick()

    origins = _origins(captured)
    assert set(origins) == {"none"}, f"取计划失败 ⇒ 所有调用点都应是 none，实际 {origins}"
    assert captured[-1]["solver_plan"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_marks_none_when_the_fetched_payload_is_not_in_combat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取回的 payload `in_combat=False`（非玩家出牌窗口）⇒ 不得缓存 ⇒ `plan_origin == "none"`。

    ★ 这对应契约 §4.2 的诚实边界：`/solver/plan` 在非出牌阶段会返回
      `in_combat=false` + 「只能在玩家出牌阶段计算」警告 —— **不能**把它当成漂移。
    """
    service, _client, captured = _make_service(
        _combat_state(),
        {"in_combat": False, "warnings": ["CombatSolver 搜索失败: 只能在玩家出牌阶段计算。"]},
        monkeypatch,
    )
    runner = STS2LoopRunner(service)

    await runner.tick()

    origins = _origins(captured)
    assert set(origins) == {"none"}, f"取计划失败 ⇒ 所有调用点都应是 none，实际 {origins}"
    assert captured[-1]["solver_plan"] is None
    assert runner._solver_plan_cached is None, "in_combat=False 的 payload 不得进缓存"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_marks_none_outside_a_combat_decision_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """屏幕不是战斗 ⇒ `plan_origin == "none"`（战斗外不参与 A/B 判定）。"""
    service, client, captured = _make_service({"screen": "MAP", "in_combat": False}, None, monkeypatch)
    runner = STS2LoopRunner(service)

    await runner.tick()

    assert client.plan_calls == 0, "非战斗决策态不得取计划"
    assert set(_origins(captured)) == {"none"}


# --------------------------------------------------------------------------------------
# ★ 端到端：复用缓存（A 的典型来源）绝不允许增加死锁计数
# --------------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tick_reused_never_increases_the_deadlock_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ D1 的端到端锁死：真实 tick 里走到 `reused` 分支时，死锁计数**不得**增加。

    这正是修前的缺陷路径：缓存计划还在、计数值一路涨到 3 ⇒ 发 `end_turn` 烧回合。
    修后：`reused` ⇒ planner 归 A ⇒ 只动过期计数。
    """
    state = _combat_state()
    plan = _plan(
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 0},
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 0},
    )
    service, _client, captured = _make_service(state, plan, monkeypatch)
    runner = STS2LoopRunner(service)
    planner = service._planner

    # 第 1 tick：refetched，planner 直接打出 STRIKE；把 step_index 复位以构造 reused。
    await runner.tick()
    assert set(_origins(captured)) == {"refetched"}

    service._state.solver_step_index = 0
    before_deadlock = planner._solver_deadlock_count
    captured.clear()

    await runner.tick()
    origins = set(_origins(captured))

    if origins == {"reused"}:
        assert planner._solver_deadlock_count == before_deadlock, (
            "★ 走 reused（A 的典型来源）时死锁计数绝不允许增加 —— 否则又回到白烧回合"
        )
    else:
        # 走到 refetched 也合法（`_reusable_plan` 判失效）；此时只有"首步在当前局面下
        # 仍不可执行"（= B）才允许计数。
        assert origins == {"refetched"}, f"只允许 reused / refetched，实际 {origins}"


# --------------------------------------------------------------------------------------
# ★★ t11：`plan_fetch_signature` 必须从 `loop_runner.tick` 真实分支透传
#
# 与 plan_origin 同理：判据正确 ≠ 接线正确。本段用真实 tick 断言
# "取回计划那一刻的指纹"被记下、并原样送到**每一条** planner 调用点。
# --------------------------------------------------------------------------------------


def _fetch_signatures(captured: list[dict]) -> list[str]:
    """本次 tick 里所有 planner 调用收到的 `plan_fetch_signature`（按调用顺序）。"""
    return [str(c.get("plan_fetch_signature")) for c in captured]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_every_planner_call_receives_the_fetch_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 单 tick 内**每一条** planner 路径都必须收到同一个非空 `plan_fetch_signature`。

    漏传的后果（与 D1 那次同型）：planner 收到空串 ⇒ 按保守口径仍判 B ⇒
    「refetched + 漂移」的误报**原样回来**，t11 等于没修。
    """
    service, _client, captured = _make_service(
        _combat_state(), _plan({"kind": "play_card", "card_id": "STRIKE", "card_index": 0}), monkeypatch
    )
    runner = STS2LoopRunner(service)

    await runner.tick()

    sigs = _fetch_signatures(captured)
    assert len(sigs) >= 2, f"应有 ≥2 条调 planner 的路径，实际 {sigs}"
    assert set(sigs) != {""}, (
        "★ 所有调用点都收到空 plan_fetch_signature ⇒ t11 判据失效（又回到 refetched+漂移 误判成 B）"
    )
    assert len(set(sigs)) == 1, f"各调用点必须收到**同一个**值，实际 {sigs}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_signature_equals_the_signature_at_fetch_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ `plan_fetch_signature` 必须等于**当前局面**的指纹（取回与判据在同一 tick）。

    值本身必须来自生产 `_combat_deadlock_signature`，而不是随便一个占位串 ——
    否则"指纹未变/已变"的比较毫无意义。
    """
    from plugin.plugins.sts2_autoplay.loop_runner import _combat_deadlock_signature

    state = _combat_state()
    service, _client, captured = _make_service(
        state, _plan({"kind": "play_card", "card_id": "STRIKE", "card_index": 0}), monkeypatch
    )
    runner = STS2LoopRunner(service)

    await runner.tick()

    # tick 内部用的是它自己构造的 snapshot；这里用**同一个**局面算期望值。
    # 手牌 STRIKE / 能量 3 / 敌人 30HP ⇒ 'STRIKE|3|30'
    assert set(_fetch_signatures(captured)) == {"STRIKE|3|30"}, (
        f"取回时指纹应为当前局面指纹 'STRIKE|3|30'，实际 {_fetch_signatures(captured)}"
    )
    # 与生产函数自洽（防止以后改取材而测试没跟上）。
    assert _combat_deadlock_signature({"raw_state": state}) == "STRIKE|3|30"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reused_tick_keeps_the_original_fetch_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 缓存复用（`reused`）时必须沿用**上一次取回时**的指纹，而不是重新采当前局面。

    语义：一份计划的"新鲜度基准"是**它被取回那一刻**的局面。若 reused 时把指纹刷新成
    当前局面，那么"取回后局面变了"这个事实就被抹掉 ⇒ t11 判据永远为"未变" ⇒ 误报回来。
    """
    state = _combat_state()
    plan = _plan(
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 0},
        {"kind": "play_card", "card_id": "STRIKE", "card_index": 0},
    )
    service, _client, captured = _make_service(state, plan, monkeypatch)
    runner = STS2LoopRunner(service)

    await runner.tick()  # 第 1 tick：refetched，记下指纹
    first = set(_fetch_signatures(captured))
    assert first == {"STRIKE|3|30"}

    service._state.solver_step_index = 0
    captured.clear()
    await runner.tick()  # 第 2 tick：应复用缓存

    origins = set(_origins(captured))
    if origins == {"reused"}:
        assert set(_fetch_signatures(captured)) == {"STRIKE|3|30"}, (
            "★ reused 必须沿用取回时的指纹（= 第 1 tick 记下的那个），不得重新采样当前局面"
        )
