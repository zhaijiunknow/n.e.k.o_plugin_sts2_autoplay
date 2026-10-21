"""t14/D4 批 1 回归测试：SSE 模式下的**周期兜底刷新**（no-progress 看门狗）。

被防的缺陷（实机 2026-10-19 复现）：
    SSE 模式下 `start_background()` 不创建 `_poll_task`（loop_runner.py:367-376，
    实测 `poll_task_alive=False`）⇒ **没有任何周期刷新者**。
    `_event_loop` 阻塞在 `await ... subscribe_events()`（实测 ENTER=4 / EXIT=0 /
    WAKE=0 / DROPPED=0，从未返回、从未重连），而 SSE 的 `: heartbeat` 注释行
    （transport_client.py:180-182 `continue`）持续刷新 HTTP 读超时，
    使"流已死"**不可检测**。

    ⇒ 某次 refresh 恰好拿到退化的 available_actions（出牌动画瞬间只剩 save_and_quit）
      后，快照被**永久固化**：陈旧 registry 无 end_turn ⇒ `action_engine.validate()`
      返回 None（实测 `executable_operation = null`）⇒ 动作发不出 ⇒ 局面不变
      ⇒ 更无事件 ⇒ 自洽死锁。
    实机表现：`step_count` 卡在 13（TICK 980+）、turn 恒 6、
    插件内部 names=['save_and_quit'] 而 mod 侧=['end_turn','save_and_quit']（60s+ 不变）、
    `external_control.seconds_since_contact` 单调增长（无人向 mod 发请求）。

修法：`step_count` 连续 `_FALLBACK_NO_PROGRESS_SECONDS` 秒未增 ⇒ 强制 `refresh_state()`。
★ 刻意不看 state_version / 心跳 / 连接状态（这些都被证明会掩盖真实停滞）。
"""
from __future__ import annotations

import asyncio
from typing import Any

from plugin.plugins.sts2_autoplay import loop_runner as lr


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...
    def debug(self, *a: Any, **k: Any) -> None: ...


class _State:
    def __init__(self, step_count: int = 0) -> None:
        self.step_count = step_count


class _Svc:
    """最小 service 替身：只关心 refresh_state 被调用几次。"""

    def __init__(self, step_count: int = 0, boom: bool = False) -> None:
        self._state = _State(step_count)
        self.refresh_calls = 0
        self._boom = boom
        self.logger = _Log()

    async def refresh_state(self, *, trigger_sync: bool = False) -> dict[str, Any]:
        self.refresh_calls += 1
        if self._boom:
            raise RuntimeError("boom")
        return {"status": "ok"}


def _mk(svc: _Svc, clock: dict[str, float]) -> Any:
    runner = lr.STS2LoopRunner.__new__(lr.STS2LoopRunner)
    runner._service = svc
    runner._fallback_last_step_count = -1
    runner._fallback_last_progress_at = 0.0
    runner._fallback_refresh_count = 0
    monkey = lambda: clock["t"]  # noqa: E731
    lr.time = monkey  # type: ignore[assignment]
    return runner


def _drive(seq: list[tuple[float, int]], monkeypatch, boom: bool = False) -> list[int]:
    """seq = [(经过秒数, 该刻的 step_count)]；返回"发生刷新"的 tick 下标。"""
    clock = {"t": 0.0}
    monkeypatch.setattr(lr, "time", lambda: clock["t"])
    svc = _Svc(boom=boom)
    runner = _mk(svc, clock)
    fired: list[int] = []
    for i, (dt, sc) in enumerate(seq):
        clock["t"] += dt
        svc._state.step_count = sc
        before = svc.refresh_calls
        asyncio.run(runner._maybe_fallback_refresh())
        if svc.refresh_calls > before:
            fired.append(i)
    return fired


def test_without_progress_it_forces_a_refresh_at_the_threshold(monkeypatch) -> None:
    """★ 核心：停滞达阈值必须刷新（这正是打破自洽死锁的那一下）。"""
    # tick0 视为"首次见到该 step_count"⇒重置计时基线；故 20s 在第 3 跳到达。
    assert _drive([(10.0, 5)] * 4, monkeypatch) == [2]


def test_progress_resets_the_timer_so_healthy_runs_never_refresh(monkeypatch) -> None:
    """★ 防误伤：正常推进时绝不能刷新（否则会白烧求解器预算）。"""
    assert _drive([(0.3, i + 1) for i in range(100)], monkeypatch) == []


def test_it_does_not_refresh_before_the_threshold(monkeypatch) -> None:
    """阈值必须生效：19 秒仍未到，不得刷新。"""
    assert _drive([(1.0, 5)] * 19, monkeypatch) == []


def test_it_refreshes_at_most_once_per_threshold_window(monkeypatch) -> None:
    """★ 防刷屏：长期停滞时按阈值节流（每 20s 一次），不是每 tick 都刷。"""
    assert _drive([(1.0, 5)] * 100, monkeypatch) == [20, 40, 60, 80]


def test_a_failing_refresh_never_breaks_the_autoplay_loop(monkeypatch) -> None:
    """★ 健壮性：刷新失败必须被吞掉（看门狗绝不拖垮主循环）。

    注意：`refresh_state` 抛异常时调用**已经发生**（计数器已加），
    所以这里断言的不是"没调用"，而是"**没有任何异常逃逸出去**"。
    """
    # 不应抛异常（若看门狗不吞异常，这里会直接 RuntimeError）。
    _drive([(10.0, 5)] * 4, monkeypatch, boom=True)
