"""t14/D4 回归门：`start_autoplay()` 必须能**重建已死的刷新通路**。

历史缺陷（实测）：`_event_loop` 静默退出后刷新通路中断，而 `start_autoplay()`
只重建 `_autoplay_task` ⇒ pause → start 完全无法恢复（step_count 恒为 33），
只能 reload 插件。

本门断言的行为（不依赖 sleep、不打真网络）：
  T1. `_event_task` 已 done 时，`start_autoplay()` 必须把它重建为**存活**任务
  T2. `_poll_task` 已 done 且处于非 SSE 模式时，同样必须被重建
  T3. 健康态（任务都活着）时**不得**重复起任务 —— 防"每次 start 都多起一个"
  T4. `_autoplay_task` 本身的复活行为**不得**被破坏
  T5. ★ 端到端：`_event_loop` 静默退出（模拟半开流）后调用 `start_autoplay()`
      ⇒ 刷新通路必须恢复存活（这是"无需 reload 即可自愈"的核心断言）
"""
from __future__ import annotations

import asyncio

from plugin.plugins.sts2_autoplay import loop_runner as lr


class _State:
    """`_autoplay_loop` 读的最小状态面。"""

    def __init__(self) -> None:
        self.autoplay_state = "running"
        self.standby = False
        self.step_count = 0
        self.transport_state = "idle"
        self.last_error = ""
        self.consecutive_errors = 0


class _FakeService:
    """最小 service 替身：提供 loop_runner 构造、start_background 与后台循环所需的面。

    ★ 刻意让 `_autoplay_loop` 能真正跑起来（而不是一起就抛）——
      否则"任务存活"的断言会被异常退出污染，测不出真实语义。
    """

    def __init__(self, *, use_event_stream: bool = True) -> None:
        self._use_event_stream = use_event_stream
        self._state = _State()
        self.calls: list[str] = []

    def _cfg_use_event_stream(self) -> bool:
        return self._use_event_stream

    def _cfg_fallback_poll_interval(self) -> float:
        return 7.0

    def _cfg_poll_interval(self, active: bool = False) -> float:
        return 0.5

    async def refresh_state(self, **kwargs):  # noqa: ANN003
        self.calls.append("refresh_state")
        return {}

    async def run_autoplay_step(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls.append("run_autoplay_step")
        self._state.step_count += 1
        return {}

    def _mark_loop_error(self, exc: Exception) -> None:  # noqa: ANN001
        self.calls.append("_mark_loop_error")
        self._state.consecutive_errors += 1

    def _autoplay_retry_backoff(self) -> float:
        return 0.01

    def _cfg_action_interval(self) -> float:
        return 0.01

    def _require_client(self):
        raise RuntimeError("no client")


def _make_runner(*, use_event_stream: bool = True):
    svc = _FakeService(use_event_stream=use_event_stream)
    runner = lr.STS2LoopRunner(svc)
    return runner, svc


def test_start_autoplay_revives_a_dead_event_task():
    """T1：已死的 `_event_task` 必须被 `start_autoplay()` 重建。"""
    runner, _ = _make_runner(use_event_stream=True)

    async def scenario():
        async def _die_immediately():
            return "dead"

        # 先把 event task 造成"已死"状态。
        runner._event_task = asyncio.ensure_future(_die_immediately())
        await asyncio.sleep(0)
        assert runner._task_done(runner._event_task), "前置：该任务应已结束"

        runner.start_autoplay()
        await asyncio.sleep(0)
        assert not runner._task_done(runner._event_task), (
            "★ start_autoplay() 必须重建已死的 _event_task —— 否则刷新通路永久中断，"
            "pause→start 无法恢复（D4 实测缺陷）"
        )
        runner._shutdown = True
        for t in (runner._autoplay_task, runner._event_task, runner._poll_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_start_autoplay_revives_a_dead_poll_task_off_sse_mode():
    """T2：非 SSE 模式下已死的 `_poll_task` 必须被重建。"""
    runner, _ = _make_runner(use_event_stream=False)

    async def scenario():
        async def _die_immediately():
            return "dead"

        runner._poll_task = asyncio.ensure_future(_die_immediately())
        await asyncio.sleep(0)
        assert runner._task_done(runner._poll_task)

        runner.start_autoplay()
        await asyncio.sleep(0)
        assert not runner._task_done(runner._poll_task), (
            "★ 非 SSE 模式下 start_autoplay() 必须重建已死的 _poll_task"
        )
        runner._shutdown = True
        for t in (runner._autoplay_task, runner._event_task, runner._poll_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_start_autoplay_does_not_duplicate_healthy_tasks():
    """T3：健康态不得重复起任务（幂等）。"""
    runner, _ = _make_runner(use_event_stream=True)

    async def scenario():
        runner.start_autoplay()
        await asyncio.sleep(0)
        first_event = runner._event_task
        first_auto = runner._autoplay_task
        assert first_event is not None and not first_event.done()
        assert first_auto is not None and not first_auto.done()

        # 再调一次：两个任务都活着 ⇒ 必须复用，不得换新对象。
        runner.start_autoplay()
        await asyncio.sleep(0)
        assert runner._event_task is first_event, "★ 健康事件任务被重复创建（泄漏）"
        assert runner._autoplay_task is first_auto, "★ 健康 autoplay 任务被重复创建（泄漏）"

        runner._shutdown = True
        for t in (runner._autoplay_task, runner._event_task, runner._poll_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_start_autoplay_still_revives_the_autoplay_task():
    """T4：不得破坏 `_autoplay_task` 的原有复活行为。"""
    runner, _ = _make_runner(use_event_stream=True)

    async def scenario():
        async def _die_immediately():
            return "dead"

        runner._autoplay_task = asyncio.ensure_future(_die_immediately())
        await asyncio.sleep(0)
        assert runner._task_done(runner._autoplay_task)

        runner.start_autoplay()
        await asyncio.sleep(0)
        assert not runner._task_done(runner._autoplay_task), (
            "★ start_autoplay() 必须重建已死的 _autoplay_task"
        )
        runner._shutdown = True
        for t in (runner._autoplay_task, runner._event_task, runner._poll_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_silently_dead_event_loop_is_repaired_by_start_autoplay_without_reload():
    """T5：★ 端到端 —— 刷新通路"无声死掉"后，一次 pause→start 必须能恢复，无需 reload。

    模拟 D4 实测形态：`_event_task` 不再运行（实测中它既不写日志也不抛异常，
    外部只能观察到"任务已结束"），而此时 `last_success_at` 不再前进、`step_count` 冻结。
    历史缺陷：`start_autoplay()` 只重建 `_autoplay_task` ⇒ pause→start **完全无法恢复**，
    只能 reload 插件（实测 step_count 恒为 33）。

    本门断言：刷新通路的死亡可由 `start_autoplay()` 单独修复 —— 这是"自愈能力"的定义。
    """
    runner, svc = _make_runner(use_event_stream=True)

    async def scenario():
        # 先正常起来（健康态）。
        runner.start_background()
        await asyncio.sleep(0)
        assert runner._event_task is not None

        # ★ 模拟"无声死亡"：直接取消并把引用置为已结束的任务，
        #   等价于外部可观测状态（_task_done(_event_task) is True）。
        dead = runner._event_task
        dead.cancel()
        try:
            await dead
        except (asyncio.CancelledError, Exception):
            pass
        assert runner._task_done(runner._event_task), (
            "前置：刷新通路应处于已结束状态（复现 D4 的可观测形态）"
        )

        # ★★ 关键断言：不 reload、不调 start_background，仅 pause→start。
        svc._state.autoplay_state = "paused"
        runner.start_autoplay()
        await asyncio.sleep(0)
        assert not runner._task_done(runner._event_task), (
            "★★ D4 核心：刷新通路死掉后，start_autoplay() 必须把它重建 —— "
            "否则 pause→start 无法恢复（实测 step_count 恒为 33），只能 reload 插件"
        )
        assert runner._event_task is not dead, "★ 必须是一个新任务，而非复用已结束对象"

        runner._shutdown = True
        for t in (runner._autoplay_task, runner._event_task, runner._poll_task):
            if t is not None and not t.done():
                t.cancel()
        await asyncio.sleep(0)

    asyncio.run(scenario())
