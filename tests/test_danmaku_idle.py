"""静止画面的陪伴闲聊不复播战报、不抢即时点评、不积压。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.catgirl_llm import CatgirlCommentGenerator
from plugin.plugins.sts2_autoplay.tests.test_danmaku_timeliness import (
    DummyLogger,
    _combat_payload,
    _combat_snapshot,
    _service,
)


def setup_idle(monkeypatch):
    service = _service()
    clock = [0.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.monotonic", lambda: clock[0])
    snapshot, payload = _combat_snapshot(), _combat_payload()
    service._state.snapshot = snapshot
    service._state.transport_state = "connected"
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    service._catgirl_llm_enabled = False
    return service, clock, snapshot, payload


@pytest.mark.asyncio
async def test_stationary_scene_gets_idle_line_every_25_seconds_without_burst(monkeypatch):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 24.9
    service._maybe_emit_idle_catgirl(snapshot, payload)
    assert service._catgirl_llm_task is None
    for at in (25.0, 50.0, 75.0, 100.0, 125.0):
        clock[0] = at
        for _ in range(20):
            service._maybe_emit_idle_catgirl(snapshot, payload)
        await service._catgirl_llm_task
    lines = [call.args[0] for call in service._client.push_danmaku.await_args_list]
    assert len(lines) == 5
    assert len(set(lines[:4])) == 4
    assert all(len(line) <= 32 and "60/80" not in line and "攻击" not in line for line in lines)


@pytest.mark.asyncio
async def test_scene_change_and_other_speech_restart_idle_wait(monkeypatch):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 24
    snapshot["turn"] += 1
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 30
    service._last_danmaku_at = 30
    clock[0] = 54
    service._maybe_emit_idle_catgirl(snapshot, payload)
    assert service._catgirl_llm_task is None
    clock[0] = 55
    service._maybe_emit_idle_catgirl(snapshot, payload)
    await service._catgirl_llm_task
    service._client.push_danmaku.assert_awaited_once()


@pytest.mark.asyncio
async def test_idle_bypasses_host_should_sync_gate(monkeypatch):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    snapshot["catgirl_sync"] = {"payload": payload, "should_sync": False}
    monkeypatch.setattr(service, "_emit_instant_catgirl_line", lambda *args: None)
    service._deliver_catgirl_sync(snapshot)
    clock[0] = 25
    service._deliver_catgirl_sync(snapshot)
    await service._catgirl_llm_task
    service._client.push_danmaku.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["disabled", "disconnected", "menu", "livestream"])
async def test_idle_only_runs_on_connected_game_danmaku_channel(monkeypatch, blocked):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    if blocked == "disabled":
        service._danmaku_enabled = False
    elif blocked == "disconnected":
        service._state.transport_state = "disconnected"
    elif blocked == "menu":
        payload["summary_kind"] = "menu"
    else:
        service._state.autoplay_state = "running"
        monkeypatch.setattr(service, "_should_hold_for_orders", lambda snapshot: False)
        service._livestream = SimpleNamespace(enabled=True, scene_for=lambda snapshot: "map")
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 100
    service._maybe_emit_idle_catgirl(snapshot, payload)
    assert service._catgirl_llm_task is None
    service._client.push_danmaku.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["observation", "progress", "disabled", "disconnected", "another_line"])
async def test_idle_generation_dropped_when_context_changes(monkeypatch, changed):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    origin = deepcopy(snapshot)
    async def generate(**kwargs):
        if changed == "observation":
            service._state.snapshot["turn"] += 1
        elif changed == "progress":
            service._state.step_count += 1
        elif changed == "disabled":
            service._danmaku_enabled = False
        elif changed == "disconnected":
            service._state.transport_state = "disconnected"
        else:
            service._last_danmaku_at = 1
        return "我也想歇一歇。"
    service._catgirl_llm_enabled = True
    service._catgirl_llm = SimpleNamespace(available=True, generate=generate)
    host = []
    monkeypatch.setattr(service, "_push_host_commentary", lambda *args, **kwargs: host.append(args))
    await service._idle_catgirl_async(origin, payload, service._commentary_observation_key(origin))
    service._client.push_danmaku.assert_not_awaited()
    assert not host


@pytest.mark.asyncio
async def test_idle_shares_generation_slot_with_event_commentary(monkeypatch):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 25
    pending = asyncio.create_task(asyncio.sleep(60))
    service._catgirl_llm_task = pending
    try:
        service._maybe_emit_idle_catgirl(snapshot, payload)
        assert service._catgirl_llm_task is pending
        assert service._danmaku_idle_attempt_at is None
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ["repeat", "empty", "error", "timeout"])
async def test_failed_or_repeated_idle_generation_uses_safe_fallback(monkeypatch, result):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.DANMAKU_GENERATION_TIMEOUT", 0.01)
    service._recent_idle_lines = ["我也想歇一歇。"]
    async def generate(**kwargs):
        assert kwargs["summary_kind"] == "idle"
        assert kwargs["payload"]["recent_idle_lines"] == ["我也想歇一歇。"]
        if result == "error":
            raise RuntimeError("offline")
        if result == "timeout":
            await asyncio.Event().wait()
        return "我也想歇一歇！" if result == "repeat" else None
    service._catgirl_llm_enabled = True
    service._catgirl_llm = SimpleNamespace(available=True, generate=generate)
    await service._idle_catgirl_async(snapshot, payload, service._commentary_observation_key(snapshot))
    service._client.push_danmaku.assert_awaited_once()
    assert service._client.push_danmaku.await_args.args[0] == "这回合我陪你慢慢看喵。"


def test_idle_prompt_is_companion_speech_not_another_battle_report(monkeypatch):
    generator = CatgirlCommentGenerator(DummyLogger())
    monkeypatch.setattr(generator, "_persona_system", lambda: "我的人设")
    messages = generator._build_messages(summary_text="战斗中", summary_kind="idle", payload={"recent_idle_lines": ["我陪着呢。"]})
    assert "我的人设" in messages[0]["content"]
    for phrase in ("画面暂时没变化", "不要重报", "没有新事件", "不编造", "不催操作", "不复读"):
        assert phrase in messages[0]["content"]
    assert "我陪着呢。" in messages[1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fresh", ["same", "changed", "failure"])
async def test_sse_idle_timer_refreshes_before_speaking_even_when_autoplay_paused(monkeypatch, fresh):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    snapshot["catgirl_sync"] = {"payload": payload, "should_sync": False}
    service._state.autoplay_state = "paused"
    monkeypatch.setattr(service, "_emit_instant_catgirl_line", lambda *args: None)

    async def refresh(**kwargs):
        assert kwargs == {"trigger_sync": True}
        if fresh == "failure":
            raise OSError("mod unavailable")
        if fresh == "changed":
            snapshot["turn"] += 1
        service._deliver_catgirl_sync(snapshot)

    service.refresh_state = AsyncMock(side_effect=refresh)
    await service._idle_danmaku_tick()
    clock[0] = 24
    await service._idle_danmaku_tick()
    service.refresh_state.assert_not_awaited()
    clock[0] = 30
    if fresh == "failure":
        with pytest.raises(OSError):
            await service._idle_danmaku_tick()
    else:
        await service._idle_danmaku_tick()
    service.refresh_state.assert_awaited_once()
    if fresh == "same":
        await service._catgirl_llm_task
        service._client.push_danmaku.assert_awaited_once()
        clock[0] = 40
        await service._idle_danmaku_tick()
        service.refresh_state.assert_awaited_once()
    else:
        assert service._catgirl_llm_task is None
        service._client.push_danmaku.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails_once", [False, True])
async def test_silent_sse_subscription_drives_idle_tick_without_change_events(monkeypatch, fails_once):
    service, _, _, _ = setup_idle(monkeypatch)
    runner = service._loop_runner
    woke = asyncio.Event()
    calls = 0

    async def idle_tick():
        nonlocal calls
        calls += 1
        if fails_once and calls == 1:
            raise OSError("transient refresh failure")
        woke.set()

    monkeypatch.setattr(service, "_idle_danmaku_tick", idle_tick)
    monkeypatch.setattr(runner, "_BUSINESS_SILENCE_CHECK_EVERY", 0.001)
    monkeypatch.setattr(runner, "_business_silence_expired", AsyncMock(return_value=False))
    monkeypatch.setattr(runner, "_event_loop_body", asyncio.Event().wait)
    task = asyncio.create_task(runner._event_loop())
    try:
        await asyncio.wait_for(woke.wait(), timeout=1)
        assert calls >= (2 if fails_once else 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
