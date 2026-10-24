"""t46：**等求解器**要等到底，但"等到哪一步了"必须是可查的状态。

## 玩家口径（本文件的存在理由）

> 让插件等待求解器，不需要超时设置，不然超时之后会退级策略会影响求解器的结果，
> 可能出现本来能过的过不了了。那么相对的，就需要补一个状态来检查是否出结果。

拆成两条硬要求：

1. **`/solver/plan` 不设读超时**：求解器算多久就等多久。等不及就退级 ⇒ 同一局面被换成
   另一条策略路径 ⇒ 本来能赢的战斗可能输。这比"卡住"更坏，所以读超时是被**故意**去掉的。
   （同时 `connect` 阶段仍受限：端口不通要秒级失败，那是错误、不是"在算"。）
2. **必须补一个状态**回答"出结果了没有"：`runtime_state.solver_status` 五态机
   （idle / computing / ready / waiting_phase / failed），并写进状态推送
   （`service.solver_status_payload()` → `_emit_status` 的 `solver` 段）。

## 为什么五态而不是"有/没有计划"两态（两起实机故障的教训）

| 现场 | 旧实现看到的 | 真因 |
|---|---|---|
| 装了没有 `/solver/plan` 的 fork | `in_combat=None` + `waiting_solver`，静默 3 分钟 | 0 ms 回 **404** |
| nekospire 预算 120s+120s 审计 vs 客户端 15s 超时 | 同上 | 每次**读超时** |

两者外形完全一样。现在：前者 ⇒ `failed`（等下去永远不好，warning 点名去查 mod/端口）；
后者 ⇒ `computing`（安静地等，每 5 秒一行进度，绝不取消搜索）。

★ 本文件全程 hermetic：没有真实端口、没有真实等待（进度行间隔被压缩到毫秒级）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from plugin.plugins.sts2_autoplay import transport_client as transport_mod
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import STS2LoopRunner
from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.transport_client import (
    STS2TransportClient,
    STS2TransportError,
)

pytestmark = pytest.mark.unit


class _CaptureLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def _record(self, args: Any) -> None:
        if not args:
            self.lines.append("")
            return
        template = str(args[0])
        rest = tuple(args[1:])
        try:
            self.lines.append(template % rest if rest else template)
        except (TypeError, ValueError):
            self.lines.append(template)

    def info(self, *args: Any, **kwargs: Any) -> None:
        self._record(args)

    def warning(self, *args: Any, **kwargs: Any) -> None:
        self._record(args)

    def error(self, *args: Any, **kwargs: Any) -> None:
        self._record(args)

    def debug(self, *args: Any, **kwargs: Any) -> None:
        self._record(args)


class _Svc:
    """`_fetch_combat_plan_single_flight` 需要的最小服务面：logger + 真实 runtime state。"""

    def __init__(self, logger: _CaptureLogger) -> None:
        self.logger = logger
        self._state = STS2RuntimeState()


def _runner(logger: _CaptureLogger) -> STS2LoopRunner:
    return STS2LoopRunner(_Svc(logger))


def _plan(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "in_combat": True,
        "turn": 3,
        "search_status": "complete",
        "budget_ms": 8000,
        "state_fingerprint": "F00D",
        "line": [{"turn": 1, "steps": [{"kind": "play_card", "card_id": "STRIKE", "card_index": 0}]}],
    }
    payload.update(overrides)
    return payload


# ══════════════════════════════════════════════════════════════════════════════
# ① 传输层：/solver/plan 不设读超时；别的端点照旧
# ══════════════════════════════════════════════════════════════════════════════


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingHttpClient:
    """替掉宿主那个共享 client，只记录每次请求真正带上的 httpx.Timeout。"""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.timeouts: list[httpx.Timeout] = []
        self.urls: list[str] = []
        self._payload = payload or {"ok": True, "data": {"in_combat": True, "search_status": "complete"}}

    async def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.timeouts.append(kwargs["timeout"])
        self.urls.append(url)
        return _FakeResponse(self._payload)


@pytest.mark.asyncio
async def test_combat_plan_request_has_no_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 核心断言：`GET /solver/plan` 的 read 阶段**不设超时**（None），connect 仍受限。"""
    fake = _RecordingHttpClient()
    monkeypatch.setattr(transport_mod, "_internal_client", lambda: fake)

    client = STS2TransportClient("http://127.0.0.1:18080", connect_timeout=5.0, request_timeout=15.0)
    await client.get_combat_plan()

    assert len(fake.timeouts) == 1
    timeout = fake.timeouts[0]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read is None, (
        "★ /solver/plan 的读超时必须为 None —— 求解器预算实测可达 120s+120s，"
        "15s 超时会把'还能赢的局面'退级成别的策略"
    )
    assert timeout.connect == pytest.approx(5.0), "连接阶段仍要秒级失败（端口不通是错误，不是'在算'）"
    assert timeout.pool == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_other_endpoints_keep_the_configured_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """别的端点不许跟着变成无超时（那会把"mod 挂了"变成永久挂起）。"""
    fake = _RecordingHttpClient({"ok": True, "data": {"screen": "COMBAT"}})
    monkeypatch.setattr(transport_mod, "_internal_client", lambda: fake)

    client = STS2TransportClient("http://127.0.0.1:18080", connect_timeout=5.0, request_timeout=15.0)
    await client.get_state()

    assert fake.timeouts[0].read == pytest.approx(15.0)


# ══════════════════════════════════════════════════════════════════════════════
# ② 等待态：idle → computing → ready / waiting_phase / failed
# ══════════════════════════════════════════════════════════════════════════════


class _SlowOkClient:
    """搜索耗时可控、期间可以观测"插件此刻是什么状态"。"""

    def __init__(self, plan: dict[str, Any], *, delay: float, observer: dict[str, Any]) -> None:
        self._plan = plan
        self._delay = delay
        self._observer = observer
        self.calls = 0

    async def get_combat_plan(self) -> dict[str, Any]:
        self.calls += 1
        self._observer["status_during_call"] = self._observer["state"].solver_status
        await asyncio.sleep(self._delay)
        return dict(self._plan)


@pytest.mark.asyncio
async def test_wait_state_goes_computing_then_ready() -> None:
    """等的时候必须是 `computing`（可查），拿到可用计划后变 `ready`。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    state = runner._service._state
    assert state.solver_status == "idle", "开局是 idle"

    observer = {"state": state}
    client = _SlowOkClient(_plan(), delay=0.02, observer=observer)
    fetched = await runner._fetch_combat_plan_single_flight(client)

    assert fetched is not None and fetched.get("in_combat") is True
    assert observer["status_during_call"] == "computing", "请求在飞时必须能查到'正在计算'"
    assert state.solver_status == "ready"
    assert state.solver_wait_count == 1
    assert state.solver_last_elapsed_seconds >= 0.02
    assert state.solver_last_search_status == "complete"
    assert state.solver_last_result_at > 0.0
    assert state.solver_last_error == ""
    assert any("[sts2_solver_result] 出结果 status=ready" in line for line in logger.lines), logger.lines


@pytest.mark.asyncio
async def test_not_in_play_phase_is_waiting_phase_not_failure() -> None:
    """mod 回 `in_combat=false` 是"还没到出牌阶段" ⇒ `waiting_phase`，不许记成失败。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    state = runner._service._state

    class _Client:
        async def get_combat_plan(self) -> dict[str, Any]:
            return {"in_combat": False, "search_status": None, "line": [], "warnings": ["只能在对局出牌阶段计算"]}

    await runner._fetch_combat_plan_single_flight(_Client())

    assert state.solver_status == "waiting_phase"
    assert "失败" not in "".join(logger.lines), f"这不是失败，不该报 warning：{logger.lines}"
    assert any("status=waiting_phase" in line for line in logger.lines), logger.lines


@pytest.mark.asyncio
async def test_request_failure_is_recorded_and_shouted_not_swallowed() -> None:
    """★★ 404 / 连接断：状态写 `failed` + warning 点名去查什么 —— **绝不再静默吞成 None**。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    state = runner._service._state

    class _NotFoundClient:
        async def get_combat_plan(self) -> dict[str, Any]:
            raise STS2TransportError("STS2-Agent 请求失败: HTTP 404 (code=not_found)")

    fetched = await runner._fetch_combat_plan_single_flight(_NotFoundClient())

    assert fetched is None
    assert state.solver_status == "failed"
    assert "404" in state.solver_last_error
    assert "STS2TransportError" in state.solver_last_error
    warnings = [line for line in logger.lines if "求解器请求失败" in line]
    assert warnings, f"失败必须留痕（旧实现静默吞成 None，两起实机故障因此查不出）：{logger.lines}"
    assert "/solver/plan" in warnings[0] and "404" in warnings[0]


@pytest.mark.asyncio
async def test_client_without_the_endpoint_is_a_failure_too() -> None:
    """客户端对象根本没有 `get_combat_plan`（构造期异常）同样算失败，不能静默。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    state = runner._service._state

    class _DumbClient:
        pass

    fetched = await runner._fetch_combat_plan_single_flight(_DumbClient())

    assert fetched is None
    assert state.solver_status == "failed"
    assert "AttributeError" in state.solver_last_error


@pytest.mark.asyncio
async def test_wait_progress_lines_are_emitted_while_searching(monkeypatch: pytest.MonkeyPatch) -> None:
    """搜索期间要周期性留痕：`在算` 与 `死了` 必须能在日志里分开。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    monkeypatch.setattr(runner, "_SOLVER_WAIT_LOG_EVERY", 0.01, raising=False)

    class _Client:
        async def get_combat_plan(self) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return _plan()

    await runner._fetch_combat_plan_single_flight(_Client())

    progress = [line for line in logger.lines if "求解器仍在计算" in line]
    assert len(progress) >= 2, f"等待期间应有多行进度，实际 {progress}"
    assert "已等=" in progress[0]
    assert any("开始等待求解器" in line for line in logger.lines), logger.lines


@pytest.mark.asyncio
async def test_waiting_never_cancels_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 等待只观测，**绝不取消搜索**（取消就等于退级，玩家明确禁止）。"""
    logger = _CaptureLogger()
    runner = _runner(logger)
    monkeypatch.setattr(runner, "_SOLVER_WAIT_LOG_EVERY", 0.01, raising=False)
    finished = {"value": False}

    class _Client:
        async def get_combat_plan(self) -> dict[str, Any]:
            try:
                await asyncio.sleep(0.05)
                finished["value"] = True
                return _plan()
            except asyncio.CancelledError:  # pragma: no cover - 出现即为缺陷
                raise

    fetched = await runner._fetch_combat_plan_single_flight(_Client())

    assert finished["value"] is True, "搜索被中途取消了 ⇒ 结果被换掉"
    assert fetched is not None
    assert runner._service._state.solver_status == "ready"


# ══════════════════════════════════════════════════════════════════════════════
# ③ 状态包：`sts2_get_status` / STATUS_UPDATE 里必须读得到
# ══════════════════════════════════════════════════════════════════════════════


def _service() -> tuple[STS2AutoplayService, _CaptureLogger, list[dict[str, Any]]]:
    logger = _CaptureLogger()
    pushed: list[dict[str, Any]] = []
    service = STS2AutoplayService(logger, pushed.append, lambda **kwargs: None)
    return service, logger, pushed


@pytest.mark.unit
def test_solver_status_payload_reports_five_states() -> None:
    """五态机：每个状态的含义可读，且明确标出"读超时被故意去掉"。"""
    service, _, _ = _service()
    state = service._state

    assert service.solver_status_payload()["status"] == "idle"

    state.solver_status = "computing"
    state.solver_wait_started_at = 1.0
    computing = service.solver_status_payload()
    assert computing["status"] == "computing"
    assert computing["wait_seconds"] >= 0.0
    assert "等" in computing["meaning"]

    state.solver_status = "failed"
    state.solver_last_error = "STS2TransportError: HTTP 404"
    failed = service.solver_status_payload()
    assert failed["status"] == "failed"
    assert "404" in failed["last_error"]
    assert "检查" in failed["meaning"]
    assert failed["read_timeout_seconds"] is None, "读超时是刻意去掉的，状态里要写明"


@pytest.mark.unit
def test_status_push_carries_the_solver_block() -> None:
    """STATUS_UPDATE 里带 `solver` 段：不接调试器也能看出"在等谁"。"""
    service, _, pushed = _service()
    service._state.solver_status = "computing"
    service._state.solver_wait_started_at = 1.0

    service._emit_status()

    assert pushed, "状态要推一次"
    solver = pushed[-1].get("solver")
    assert isinstance(solver, dict), pushed[-1]
    assert solver["status"] == "computing"
    assert solver["read_timeout_seconds"] is None


@pytest.mark.unit
def test_get_status_exposes_solver_section() -> None:
    """`sts2_get_status` 也要带同一份状态（LLM/前端走的是这条）。"""
    service, _, _ = _service()
    payload = asyncio.run(service.get_status())
    assert payload["solver"]["status"] == "idle"


# ══════════════════════════════════════════════════════════════════════════════
# ④ 闸门日志：一行里就能看出"在等谁"
# ══════════════════════════════════════════════════════════════════════════════


def _gate() -> dict[str, Any]:
    return {
        "verdict": "waiting_solver",
        "missing": "solver_plan",
        "why": "没有求解器计划（本 tick 未取到 /solver/plan）",
        "search_status": "",
        "in_combat": None,
        "step_kind": "",
        "fingerprint_note": "",
        "plan_origin": "none",
    }


@pytest.mark.unit
def test_gate_log_line_reports_solver_state_and_wait_seconds() -> None:
    """`[sts2_solver_gate]` 行尾必须带「求解器 <状态> 已等=Ns」。"""
    logger = _CaptureLogger()
    planner = STS2HeuristicPlanner(logger=logger)

    planner._log_solver_gate(
        gate=_gate(),
        plan_origin="none",
        force=True,
        solver_status="computing",
        solver_wait_seconds=42.5,
    )

    line = next(text for text in logger.lines if "[sts2_solver_gate]" in text)
    assert "求解器 computing 已等=42.5s" in line, line


@pytest.mark.unit
def test_gate_log_flips_immediately_when_state_changes() -> None:
    """computing → failed 必须立刻打一行（不被 5 次节流吃掉）。"""
    logger = _CaptureLogger()
    planner = STS2HeuristicPlanner(logger=logger)

    planner._log_solver_gate(gate=_gate(), plan_origin="none", force=False, solver_status="computing")
    planner._log_solver_gate(
        gate=_gate(),
        plan_origin="none",
        force=False,
        solver_status="failed",
        solver_last_error="STS2TransportError: HTTP 404",
    )

    lines = [text for text in logger.lines if "[sts2_solver_gate]" in text]
    assert len(lines) == 2, lines
    assert "求解器 failed" in lines[-1]
    assert "HTTP 404" in lines[-1], "失败原因要写在同一行里，别让人去翻别的行"
