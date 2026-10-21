"""t14/D4 批 2 回归测试：**业务事件静默 ⇒ 主动重连**（心跳不得参与存活判定）。

被防的缺陷（实机 2026-10-19 复现）：
    mod 每 ~1s 发一行 `: heartbeat`（SSE 注释）⇒ 它刷新了 `read=60.0` 的 HTTP 读超时
    ⇒ 超时**永不触发**；而注释行**不产出业务事件**。
    于是当业务事件流真的停了，连接看起来**完全健康**：
    实机 `event-loop ENTER=4 / EXIT=0 / WAKE=0 / DROPPED=0`（从未返回、从未重连），
    而队长直连 mod `/events/stream` 仍是 HTTP 200、15 秒 16 行。
    ⇒ ★ **保活信号掩盖了业务事件流的死亡** ⇒ `_event_loop` 永久阻塞
      ⇒ 无事件 ⇒ 不 refresh ⇒ 快照冻结 ⇒ 动作发不出 ⇒ 死锁。

修法（批 2）：
  · `transport_client` 记录 `last_business_event_at`（★ **只有业务事件**才刷新，心跳不算）
  · `loop_runner` 把订阅包进 `asyncio.ensure_future` + `asyncio.wait(timeout=...)`，
    每 10s 检查一次"业务事件是否已静默 >= 60s"，是则 `sub.cancel()` 主动重连。
    ★ 刻意**不看**心跳/连接状态 —— 它们正是缺陷来源。

★ 有效成分的回归锁：**"心跳不刷新 last_business_event_at"** 这一条。
   若它被改回"心跳也算进度"，本文件的用例 1 与用例 5 必须转红。
"""
from __future__ import annotations

import asyncio
from typing import Any

from plugin.plugins.sts2_autoplay import loop_runner as lr
from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient


class _Log:
    def info(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...
    def debug(self, *a: Any, **k: Any) -> None: ...


class _State:
    def __init__(self) -> None:
        self.step_count = 0
        self.autoplay_state = "running"
        self.standby = False
        self.sse_reconnect_count = 0


class _Client:
    """最小 client：只暴露 last_business_event_at（被测判据唯一依赖）。"""

    def __init__(self, last: float = 0.0) -> None:
        self.last_business_event_at = last


class _Svc:
    def __init__(self, client: _Client) -> None:
        self._client = client
        self._state = _State()
        self.logger = _Log()
        self.refresh_calls = 0

    def _require_client(self) -> _Client:
        return self._client

    async def refresh_state(self, *, trigger_sync: bool = False) -> dict[str, Any]:
        self.refresh_calls += 1
        return {"status": "ok"}


def _mk(monkeypatch, client: _Client, now: float) -> tuple[Any, _Svc]:
    clock = {"t": now}
    monkeypatch.setattr(lr, "time", lambda: clock["t"])
    svc = _Svc(client)
    runner = lr.STS2LoopRunner.__new__(lr.STS2LoopRunner)
    runner._service = svc
    runner._business_silence_since = 0.0
    runner._hb_log = lambda *a, **k: None
    runner._clock = clock  # 测试用
    return runner, svc


# ── 用例 1：★ 有效成分的回归锁 —— 心跳绝不能刷新 last_business_event_at ──────────
def test_heartbeat_lines_never_refresh_the_business_event_clock(monkeypatch) -> None:
    """★ 这是本次修复的**有效成分**：心跳（`:` 行）不得被当作业务进度。

    ★ 行为测试（不是源码文本断言 —— 文本断言会被注释误伤）：
      直接驱动真实的 SSE 解析，喂入"仅心跳"的流，断言时钟**不被刷新**；
      再喂入"含一个业务事件"的流，断言时钟**被刷新**。
    """

    frames = [
        b": heartbeat\n\n",
        b": heartbeat\n\n",
        b": heartbeat\n\n",
    ]
    event_frames = [
        b": heartbeat\n\n",
        b"data: {\"type\": \"available_actions_changed\"}\n\n",
    ]

    def _drive(payload_frames: list[bytes]) -> float:
        client = STS2TransportClient(base_url="http://127.0.0.1:1")
        client.last_business_event_at = 0.0

        class _Resp:
            status_code = 200

            async def aread(self) -> bytes:  # pragma: no cover - 仅错误路径
                return b""

            async def aiter_lines(self):
                for f in payload_frames:
                    for line in f.decode().splitlines():
                        yield line

        class _StreamCtx:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(
            "plugin.plugins.sts2_autoplay.transport_client._internal_client",
            lambda: type("C", (), {"stream": lambda *a, **k: _StreamCtx()})(),
        )

        async def _run() -> None:
            async for _ in client.subscribe_events():
                pass

        asyncio.run(_run())
        return client.last_business_event_at

    # 仅心跳 ⇒ 时钟保持 0.0（★ 若有人改成心跳也刷新，这里会变成非 0 ⇒ 转红）
    assert _drive(frames) == 0.0, (
        "★ 心跳刷新了业务事件时钟 —— 保活信号会再次掩盖业务事件流死亡"
    )
    # 含一个业务事件 ⇒ 时钟必须被刷新
    assert _drive(event_frames) > 0.0, "业务事件未刷新 last_business_event_at"


# ── 用例 2/3/4：静默判定阈值 ────────────────────────────────────────────────
def test_silence_under_the_threshold_does_not_trigger_reconnect(monkeypatch) -> None:
    client = _Client(last=1000.0)
    runner, _ = _mk(monkeypatch, client, now=1030.0)  # 静默 30s < 60s
    assert asyncio.run(runner._business_silence_expired()) is False


def test_silence_at_or_over_the_threshold_triggers_reconnect(monkeypatch) -> None:
    client = _Client(last=1000.0)
    runner, _ = _mk(monkeypatch, client, now=1060.0)  # 静默 60s >= 60s
    assert asyncio.run(runner._business_silence_expired()) is True


def test_a_brand_new_connection_uses_the_connect_time_not_zero(monkeypatch) -> None:
    """★ 防误判：刚连上（还没产出业务事件，last<=0）时，从"建立时刻"起算。

    否则 last=0.0 会被算成"静默了自纪元以来的秒数" ⇒ 一连上就重连 ⇒ 抖动。
    """
    client = _Client(last=0.0)
    runner, _ = _mk(monkeypatch, client, now=1000.0)

    # 第一次调用：记下起算时刻，且静默应为 0 ⇒ 不触发
    assert asyncio.run(runner._business_silence_expired()) is False
    assert runner._business_silence_since == 1000.0

    # 30s 后仍不触发
    runner._service._client.last_business_event_at = 0.0
    monkeypatch.setattr(lr, "time", lambda: 1030.0)
    assert asyncio.run(runner._business_silence_expired()) is False

    # 60s 后触发
    monkeypatch.setattr(lr, "time", lambda: 1060.0)
    assert asyncio.run(runner._business_silence_expired()) is True


# ── 用例 5：★ 端到端 —— 永久阻塞的 async for + 心跳持续 ⇒ 必须被强制重连 ──────
def test_a_permanently_blocked_stream_is_cancelled_after_business_silence(
    monkeypatch,
) -> None:
    """★ 本缺陷的核心场景：`async for` 永不返回（心跳维持连接），必须被超时强制 cancel。

    构造：`_event_loop_body` 挂死（模拟永久阻塞）；
          业务事件时钟停在过去（静默已超阈值）。
    断言：外层通过 asyncio.wait 超时发现静默 ⇒ cancel ⇒ 循环重试（而非永久卡住）。
    """
    client = _Client(last=1000.0)
    runner, svc = _mk(monkeypatch, client, now=2000.0)  # 静默 1000s ⇒ 已超阈值

    body_started = asyncio.Event()
    body_cancelled = asyncio.Event()

    async def _hanging_body() -> None:
        body_started.set()
        try:
            await asyncio.sleep(3600)  # 模拟永久阻塞的 async for
        except asyncio.CancelledError:
            body_cancelled.set()
            raise

    runner._event_loop_body = _hanging_body
    # 压缩等待：检查周期改小，避免测试等 10s
    monkeypatch.setattr(runner, "_BUSINESS_SILENCE_CHECK_EVERY", 0.05)
    runner._shutdown = False
    runner._event_backoff = 0.01
    runner._mark_sse_connected = lambda *a, **k: None
    runner._sse_connected = False

    async def _run_briefly() -> None:
        task = asyncio.ensure_future(runner._event_loop())
        # 等 body 真正启动
        await asyncio.wait_for(body_started.wait(), timeout=2.0)
        # 等它被静默检测 cancel（检查周期 0.05s ⇒ 很快）
        await asyncio.wait_for(body_cancelled.wait(), timeout=2.0)
        runner._shutdown = True
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    asyncio.run(_run_briefly())
    assert body_cancelled.is_set(), "★ 永久阻塞的订阅未被强制取消 ⇒ 又回到了永久死锁"
