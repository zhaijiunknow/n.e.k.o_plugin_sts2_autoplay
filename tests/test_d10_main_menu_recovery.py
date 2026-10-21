# -*- coding: utf-8 -*-
"""t26 / D10 —— MAIN_MENU（非对局状态）下 `/state` 500 不得导致永久卡死的回归门。

缺陷（实机，2026-10-19 23:55:51–23:59:36，35 条连续错误行）
----------------------------------------------------------
游戏停在主菜单时 mod 的 `/state` 返回 HTTP 500
（`last_error='Unhandled server error. (code=internal_error)'`），
而 `/actions/available` 同刻 HTTP 200 且列出 `continue_run` 等动作。

修复前 `loop_runner.tick()` 里 `await client.get_state()` 先抛 ⇒ **整个 tick 中止**
⇒ `/actions/available` 根本没被调用 ⇒ `available_action_count` 恒 0（实测 {'0':31,'4':4}）
⇒ 插件把"状态查询失败"误判成"无事可做" ⇒ 从不尝试 `continue_run`
⇒ 每次重启永久卡死，8 秒一轮（= `_autoplay_retry_backoff` 封顶值）。

★ 这是 D1（计划过期误判为无解）、D9（pending 误判为已执行）的**同族第三例**：
  **状态获取失败被当成语义结论**。

本门钉住的四条不变量
--------------------
1. **NA-1 降级不中止**：`/state` 抛异常时，`tick()` 仍然调用 `/actions/available`，
   并把动作集带给 snapshot（修复前恒 0）。
2. **NA-2 可观测**：降级必须留下 `[sts2_d10]` 告警日志 —— 不得静默。
3. **NA-3 不误伤**：`/state` 正常时行为逐位不变（`state_unavailable=False`，
   screen/floor/available_action_count 与旧实现一致）。
4. **NA-4 区分两类零**：「状态取不到」与「真的没有动作」必须可区分
   （前者 `available_action_count > 0` 且 `state_unavailable=True`；
     后者 `available_action_count == 0` 且 `state_unavailable=False`）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# 路径引导统一在 tests/conftest.py（此处不再插 sys.path）
from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState as _RealState  # noqa: E402
from plugin.plugins.sts2_autoplay.snapshot_normalizer import (  # noqa: E402
    STATE_UNAVAILABLE_SCREEN,
    normalize_actions,
    normalize_snapshot,
)

# ======================================================================================
# D10 实机现场：MAIN_MENU 主菜单的 `/actions/available`（5 个动作，含 continue_run）
# ======================================================================================
MAIN_MENU_ACTIONS: dict[str, Any] = {
    "ok": True,
    "request_id": "req_probe",
    "data": {
        "screen": "MAIN_MENU",
        "actions": [
            {"name": "continue_run", "requires_target": False, "requires_index": False},
            {"name": "abandon_run", "requires_target": False, "requires_index": False},
            {"name": "open_timeline", "requires_target": False, "requires_index": False},
            {"name": "open_multiplayer_menu", "requires_target": False, "requires_index": False},
            {"name": "start_coop_session", "requires_target": False, "requires_index": False},
        ],
    },
}

# 对局中（COMBAT）的正常 `/state`
COMBAT_STATE: dict[str, Any] = {
    "screen": "COMBAT",
    "in_combat": True,
    "run_id": "T26PROBE",
    "run": {"floor": 12, "act_id": "1", "character_id": "IRONCLAD"},
    "available_actions": ["play_card", "end_turn"],
}
COMBAT_ACTIONS: dict[str, Any] = {
    "actions": [
        {"name": "play_card", "requires_target": True, "requires_index": True},
        {"name": "end_turn", "requires_target": False, "requires_index": False},
    ]
}


# ======================================================================================
# 1. 数据层：normalize_snapshot 的降级语义
# ======================================================================================
@pytest.mark.unit
def test_state_failure_keeps_actions_from_available_endpoint() -> None:
    """★ NA-1（数据层）：`/state` 空 ⇒ 动作集**仍**取自 `/actions/available`。

    修复前 `normalize_snapshot({}, ACTIONS)` 也能出 5 个动作，但 `screen` 落到 'unknown'，
    且调用方根本不会走到这里（tick 已抛）。本门先钉数据层契约。
    """
    snap = normalize_snapshot({}, MAIN_MENU_ACTIONS)
    assert snap["available_action_count"] == 5, (
        f"★ 动作集被清零 = {snap['available_action_count']}；"
        f"这正是 D10：'/state 失败'被当成'无事可做'"
    )
    names = [a["type"] for a in snap["available_actions"]]
    assert "continue_run" in names, f"continue_run 必须可见；实际={names}"


@pytest.mark.unit
def test_state_failure_is_flagged_and_distinct_from_no_action() -> None:
    """★ NA-4：区分「状态取不到」与「真的没有动作」——两者不得混淆。"""
    degraded = normalize_snapshot({}, MAIN_MENU_ACTIONS)
    genuinely_empty = normalize_snapshot(COMBAT_STATE, {"actions": []})

    assert degraded["state_unavailable"] is True
    assert genuinely_empty["state_unavailable"] is False

    # 两类"零"可区分：前者有动作（只是状态读不到），后者是真的没动作
    assert degraded["available_action_count"] > 0
    assert genuinely_empty["available_action_count"] == 0
    assert degraded["screen"] == STATE_UNAVAILABLE_SCREEN


@pytest.mark.unit
def test_healthy_state_is_untouched() -> None:
    """★ NA-3（数据层）：`/state` 正常时逐位不变 —— 不误伤对局。"""
    snap = normalize_snapshot(COMBAT_STATE, COMBAT_ACTIONS)
    assert snap["state_unavailable"] is False
    assert snap["screen"] == "COMBAT"
    assert snap["floor"] == 12
    assert snap["act"] == 1
    assert snap["in_combat"] is True
    assert snap["run_id"] == "T26PROBE"
    assert snap["character"] == "IRONCLAD"
    assert snap["available_action_count"] == 2
    assert [a["type"] for a in snap["available_actions"]] == ["play_card", "end_turn"]


@pytest.mark.unit
def test_screen_present_but_empty_state_is_still_not_flagged() -> None:
    """边界：`/state` 返回了内容（哪怕只有 screen）⇒ 不算降级，不误报。"""
    snap = normalize_snapshot({"screen": "MAIN_MENU"}, MAIN_MENU_ACTIONS)
    assert snap["state_unavailable"] is False
    assert snap["screen"] == "MAIN_MENU"
    assert snap["available_action_count"] == 5


@pytest.mark.unit
def test_actions_payload_envelope_is_parsed() -> None:
    """★★ D10 后半段根因：`/actions/available` 的动作在 `data.actions`（mod 统一信封）。

    实测（`_t26_actions_shape.log`）：
        真实 payload 顶层键 = ['ok','request_id','data']，**顶层无 'actions'**
        normalize_actions(真实 payload) = **0 个**（修复前）
        normalize_actions(payload['data']) = 3 个

    ⇒ 修复前即使 tick 不再中止，动作集**仍是 0** ⇒ 等于没降级。
    """
    got = normalize_actions(MAIN_MENU_ACTIONS)
    assert len(got) == 5, (
        f"★ `data.actions` 未解析出来（得 {len(got)} 个）—— 这就是"
        f"『降级取回动作却解析不出』的形态，D10 会表现为仍然卡死"
    )
    assert [a["type"] for a in got][0] == "continue_run"


@pytest.mark.unit
def test_actions_payload_plain_form_still_parsed() -> None:
    """★ 回归保护：顶层 `actions` 这一形态（旧调用方/测试）不得被改坏。"""
    got = normalize_actions({"actions": [{"name": "play_card"}, {"name": "end_turn"}]})
    assert [a["type"] for a in got] == ["play_card", "end_turn"]


@pytest.mark.unit
def test_actions_payload_string_list_supported() -> None:
    """★ `/state` 的 `available_actions` 是**字符串列表** ⇒ 也必须能归一化。"""
    got = normalize_actions({"available_actions": ["end_turn", "play_card"]})
    assert isinstance(got, list), "available_actions 归一化必须返回 list"
    assert isinstance(got, list), "available_actions 归一化必须返回 list"
    # 注意：本函数读的是 'actions' 系列，'available_actions' 由 normalize_snapshot 的
    #       state 侧负责；这里只钉字符串列表这种 item 形态本身可用。
    got2 = normalize_actions({"actions": ["end_turn", "play_card"]})
    assert [a["type"] for a in got2] == ["end_turn", "play_card"]


@pytest.mark.unit
def test_actions_payload_malformed_is_empty_not_crash() -> None:
    """★ 健壮性：畸形响应不得抛异常（否则又变成"状态获取失败被当语义结论"）。"""
    for bad in (None, {}, {"data": None}, {"data": {"actions": "nope"}},
                {"actions": [None, 5, {"name": "ok"}]}):
        got = normalize_actions(bad)  # type: ignore[arg-type]
        assert isinstance(got, list)


# ======================================================================================
# 2. 运行时层：tick() 真的降级（NA-1 + NA-2）—— 端到端
# ======================================================================================
class _AnyCall:
    """★ 万能协作者替身：任何方法调用都返回一个"中性"值。

    `tick()` 是长流水线（快照→归档→分类→摘要→策略→计划→候选→决策→校验→执行），
    穿插十几个协作者。本门只关心最前面两行（`/state` 降级），
    逐个手写每个协作者的每个方法属于**测装配而非测行为**。
    ⇒ 用中性替身让流水线**跑到底**，再断言"降级确实发生了"。
    """

    def __getattr__(self, name: str):
        def _call(*args, **kwargs):  # noqa: ANN001
            # 少数几个必须有"形状"的方法（返回 None 会让下游解引用失败）
            if name == "as_dict":
                return {}
            return _AnyCall()

        return _call

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False

    def get(self, *args, **kwargs):  # noqa: ANN001
        return None

    def __getitem__(self, key):  # noqa: ANN001
        # 下游会做 `x["payload"]["event_type"] = ...` 这类嵌套写入 ⇒ 每次读给**真 dict**，
        # 这样嵌套赋值能落地（返回 None 会 TypeError）。
        return {}

    def __setitem__(self, key, value):  # noqa: ANN001
        return None

    def __contains__(self, item):  # noqa: ANN001
        return False


class _FakeClient:
    """按脚本决定 `/state` 成功或抛错，并记录 `/actions/available` 是否被调用。"""

    def __init__(self, *, state_raises: bool):
        self.state_raises = state_raises
        self.state_calls = 0
        self.actions_calls = 0
        self.snapshots_seen: list[dict[str, Any]] = []

    async def get_state(self) -> dict[str, Any]:
        self.state_calls += 1
        if self.state_raises:
            raise RuntimeError("Unhandled server error. (code=internal_error)")
        return {"screen": "MAIN_MENU"}

    async def get_available_actions(self) -> dict[str, Any]:
        self.actions_calls += 1
        return MAIN_MENU_ACTIONS

    async def get_combat_plan(self) -> dict[str, Any]:
        return {"in_combat": False}

    async def execute_action(self, *a, **k):  # noqa: ANN001
        return {"status": "ok"}


class _FakeLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, msg: str, *args: Any) -> None:
        try:
            self.warnings.append(msg % args if args else str(msg))
        except Exception:  # noqa: BLE001
            self.warnings.append(str(msg))

    def info(self, msg: str, *args: Any) -> None:  # pragma: no cover
        pass


def _make_runner(client: _FakeClient):
    """构造一个只够跑 `tick()` 的 LoopRunner，其余协作者用最小替身。

    ★ 刻意**不** mock 被测的两行逻辑本身（`get_state` 降级 + `normalize_snapshot`），
      只替身它们的下游消费者，确保本门测的是真实行为。
    """
    from plugin.plugins.sts2_autoplay import loop_runner as LR

    logger = _FakeLogger()
    _logger_obj = logger  # 类体内 `logger = logger` 会读类命名空间 ⇒ NameError，故另起名

    class _SM:
        """分类器替身：只回 state_name，且**记录**它实际收到了什么 screen。

        ★ 这是本门最关键的观测点之一：`/state` 挂掉后 screen 必须**不是**
          'unknown'（否则下游会合法地"什么都不做"），而是显式占位值。
        """

        def __init__(self) -> None:
            self.seen: list[dict[str, Any]] = []

        def classify(self, snap):  # noqa: ANN001
            self.seen.append(dict(snap))
            return {"state_name": str(snap.get("screen") or "unknown").lower()}

    sm = _SM()

    class _Reg:
        def build(self, snap):  # noqa: ANN001
            return {"actions": snap.get("available_actions") or []}

    class _MC:
        def describe(self, mode):  # noqa: ANN001
            return {"allows_planner": False}

    class _CG:
        def generate(self, ctx, **kwargs):  # noqa: ANN001
            return []

    class _State(_RealState):
        """★ 用**真实** `STS2RuntimeState`（字段齐全），只覆盖本门关心的健康字段。

        为什么不用手写替身：`tick()` 下游会读十几个状态字段
        （`pending_guidance` / `recent_decision_memory` / `run_intent` …），
        手写替身会一路 AttributeError/TypeError ⇒ 本门被无关装配细节拖红，
        且测不到真实语义。用真类 ⇒ 测的是真行为。
        """

        def __init__(self) -> None:
            super().__init__()
            self.autoplay_state = "running"
            self.transport_state = "connected"
            self.last_error = ""
            self.consecutive_errors = 0

    state = _State()

    class _Service:
        _require_client = staticmethod(lambda: client)
        _action_registry = _Reg()
        _state_machine = sm
        _summary_engine = _AnyCall()
        _strategy_repository = _AnyCall()
        _mode_controller = _MC()
        _candidate_generator = _CG()
        _state = state
        logger = _logger_obj
        _cfg = {}
        _planner = None

        # ★ 其余协作者（摘要/决策/动作引擎/陪玩评估/…）用中性替身 —— 本门不测它们，
        #   只为让这条长流水线**跑到降级点之后**，从而能断言真实行为。
        _summary_builder = _AnyCall()
        _action_engine = _AnyCall()

        def __getattr__(self, name: str):
            """未知的私有协作者 ⇒ 自动给中性替身（只有 `_xxx` 形态才兜底）。

            ★ 目的：`tick()` 是十几个协作者的长流水线，本门只关心最前面两行
              （`/state` 降级）。逐个装配属于**测装配而非测行为** ——
              用真 `STS2RuntimeState` + 中性协作者让流水线跑到底。
            ★ 仅对下划线开头的名字兜底，避免掩盖真实属性拼写错误。
            """
            if name.startswith("_"):
                return _AnyCall()
            raise AttributeError(name)

        def _mark_loop_error(self, exc):  # noqa: ANN001
            state.transport_state = "error"
            state.last_error = str(exc)
            state.consecutive_errors += 1

    class _Runner(LR.STS2LoopRunner):
        def __init__(self) -> None:
            # ★ 跑**真实** `__init__`（它只赋值实例字段，不产生副作用），
            #   这样 `_current_event_type` / `_solver_plan_cached` 等全部就位，
            #   不会因"替身少装配一个字段"而把本门拖红。
            super().__init__(_Service())

        def _hb_log(self, message: str) -> None:  # 心跳仅日志
            pass

        def _log_plan_reuse(self, *a, **k):  # noqa: ANN001
            pass

        def _log_plan_refresh(self, *a, **k):  # noqa: ANN001
            pass

    return _Runner(), state, logger, sm


@pytest.mark.unit
def test_tick_degrades_when_state_raises_and_still_fetches_actions() -> None:
    """★★ NA-1（端到端）：`/state` 抛异常时 tick **不中止**，且**仍然**取动作集。

    修复前的行为 = `get_available_actions` 从未被调用（本门会在此转红）。
    """
    client = _FakeClient(state_raises=True)
    runner, state, logger, sm = _make_runner(client)

    # 修复前这里会直接抛出 RuntimeError（整个 tick 中止）
    asyncio.run(runner.tick())

    assert client.actions_calls == 1, (
        "★ `/state` 失败后 `/actions/available` 未被调用 ⇒ 正是 D10 的卡死形态"
    )
    # ★ 动作集必须真的抵达 snapshot（不只是"被调用了一次"）
    assert sm.seen, "分类器没收到 snapshot ⇒ tick 在降级点之前就结束了"
    snap = sm.seen[0]
    assert snap.get("available_action_count") == 5, (
        f"★ 动作集未抵达 snapshot：available_action_count={snap.get('available_action_count')}"
    )
    assert "continue_run" in [a.get("type") for a in (snap.get("available_actions") or [])], (
        "★ continue_run 必须对插件可见（否则它永远试不了恢复）"
    )
    assert snap.get("screen") == STATE_UNAVAILABLE_SCREEN, (
        f"★ 降级时 screen 应为显式占位值，实际={snap.get('screen')!r}"
        "（留 'unknown' 会让下游合法地什么都不做）"
    )


@pytest.mark.unit
def test_degradation_emits_observable_log() -> None:
    """★★ NA-2：降级必须留下可观测日志，**不得静默**。"""
    client = _FakeClient(state_raises=True)
    runner, state, logger, sm = _make_runner(client)
    asyncio.run(runner.tick())

    hit = [w for w in logger.warnings if "sts2_d10" in w]
    assert hit, f"★ 降级没有任何日志（静默）⇒ 与缺陷的贵处相同；warnings={logger.warnings}"
    assert "continue_run" in " ".join(hit) or "actions=5" in " ".join(hit), (
        "★ 降级日志未暴露动作集（看不到『有动作可发』这一决定性事实）："
        + repr(hit[:2])
    )


@pytest.mark.unit
def test_healthy_tick_does_not_log_degradation() -> None:
    """★ NA-3（端到端）：`/state` 正常时**不得**打降级日志（不误报、不刷屏）。"""
    client = _FakeClient(state_raises=False)
    runner, state, logger, sm = _make_runner(client)
    asyncio.run(runner.tick())

    assert client.actions_calls == 1
    assert not [w for w in logger.warnings if "sts2_d10" in w], (
        f"★ 正常路径误报降级：{logger.warnings}"
    )
    # 正常路径 screen 必须如实为真实界面，不得被占位值污染
    assert sm.seen and sm.seen[0].get("screen") == "MAIN_MENU"
    assert sm.seen[0].get("state_unavailable") is False


@pytest.mark.unit
def test_healthy_tick_leaves_health_fields_clean() -> None:
    """★ NA-3（端到端）：正常 tick 不得把健康字段写成 error。"""
    client = _FakeClient(state_raises=False)
    runner, state, logger, sm = _make_runner(client)
    asyncio.run(runner.tick())
    assert state.consecutive_errors == 0
    assert state.transport_state == "connected"
