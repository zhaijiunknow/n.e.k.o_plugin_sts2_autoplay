"""弹幕节奏：回合不能丢、短句独立门控、慢请求不积压、出口统一限流。"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.snapshot_normalizer import normalize_snapshot
from plugin.plugins.sts2_autoplay.tests.test_danmaku_timeliness import _combat_payload, _combat_snapshot, _service


@pytest.mark.parametrize(("raw", "expected"), [
    ({"turn": 3}, 3), ({"combat": {"turn": 4}}, 4),
    ({"turn": 0, "combat": {"turn": 4}}, 0), ({}, None),
])
def test_real_snapshot_keeps_turn(raw, expected):
    snapshot = normalize_snapshot({"screen": "COMBAT", **raw}, {})
    assert snapshot["turn"] == expected


def test_normalized_combat_turns_no_longer_share_instant_key():
    service = _service()
    a, b = [normalize_snapshot({"screen": "COMBAT", "turn": turn}, {}) for turn in (1, 2)]
    assert service._instant_line_key(a, {}) != service._instant_line_key(b, {})


@pytest.mark.asyncio
async def test_burst_from_all_producers_is_dropped_not_queued(monkeypatch):
    service = _service()
    clock = [0.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.monotonic", lambda: clock[0])
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    await asyncio.gather(*(service._push_catgirl(f"短句{i}", fetch_avatar=False) for i in range(20)))
    assert service._client.push_danmaku.await_count == 1
    clock[0] = 6.0
    await service._push_catgirl("新的局势", fetch_avatar=False)
    assert service._client.push_danmaku.await_count == 2
    # No backlog is released when time advances.
    clock[0] = 60.0
    await asyncio.sleep(0)
    assert service._client.push_danmaku.await_count == 2


@pytest.mark.asyncio
async def test_text_dedup_does_not_consume_cooldown(monkeypatch):
    service = _service()
    clock = [0.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.monotonic", lambda: clock[0])
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    await service._push_catgirl("小心攻击！", fetch_avatar=False)
    clock[0] = 6.0
    await service._push_catgirl("小心攻击。", fetch_avatar=False)
    await service._push_catgirl("血量变低了", fetch_avatar=False)
    assert service._client.push_danmaku.await_count == 2
    clock[0] = 61.0
    await service._push_catgirl("小心攻击！", fetch_avatar=False)
    assert service._client.push_danmaku.await_count == 3


@pytest.mark.asyncio
async def test_output_is_one_short_sentence():
    service = _service()
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    await service._push_catgirl("第一句！\n还有一大段解释。", fetch_avatar=False)
    assert service._client.push_danmaku.await_args.args[0] == "第一句！"
    assert len(service._compact_danmaku("长" * 200)) == 32


@pytest.mark.asyncio
async def test_slow_avatar_cannot_create_parallel_push_backlog():
    service = _service()
    started, release = asyncio.Event(), asyncio.Event()
    async def avatar():
        started.set()
        await release.wait()
        return None
    service._catgirl_avatar_base64 = avatar
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    first = asyncio.create_task(service._push_catgirl("第一条"))
    await started.wait()
    await asyncio.gather(*(service._push_catgirl("跟着挤进来") for _ in range(10)))
    release.set()
    await first
    assert service._client.push_danmaku.await_count == 1
    assert not service._danmaku_sending


@pytest.mark.asyncio
async def test_cancellation_releases_sending_guard():
    service = _service()
    entered = asyncio.Event()
    async def push(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    service._client = SimpleNamespace(push_danmaku=push)
    task = asyncio.create_task(service._push_catgirl("正在发送", fetch_avatar=False))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not service._danmaku_sending


@pytest.mark.parametrize("gate", ["should_sync", "probability", "should_comment"])
def test_instant_line_independent_of_host_chat_gates(monkeypatch, gate):
    service = _service()
    lines = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: lines.append(text))
    monkeypatch.setattr(service, "_should_allow_push_by_probability", lambda **kw: False)
    payload = _combat_payload()
    if gate == "should_comment":
        payload["companion_evaluation"]["should_comment"] = False
    snapshot = _combat_snapshot()
    snapshot["catgirl_sync"] = {"should_sync": gate != "should_sync", "payload": payload}
    service._deliver_catgirl_sync(snapshot)
    assert len(lines) == 1
    service._danmaku_enabled = False
    snapshot["turn"] += 1
    service._deliver_catgirl_sync(snapshot)
    assert len(lines) == 1


def test_same_turn_changed_facts_can_speak_but_unchanged_scene_does_not(monkeypatch):
    service = _service()
    lines = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: lines.append(text))
    snapshot, payload = _combat_snapshot(), _combat_payload()
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    service._state.step_count += 1
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    assert len(lines) == 1
    payload["player"]["current_hp"] = 12
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_slow_generation_times_out_and_falls_back(monkeypatch):
    service = _service()
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.DANMAKU_GENERATION_TIMEOUT", 0.01)
    async def generate(**kwargs):
        await asyncio.Event().wait()
    service._catgirl_llm = SimpleNamespace(generate=generate)
    fallback = []
    monkeypatch.setattr(service, "_push_catgirl_fallback", lambda *args: fallback.append(args))
    await service._catgirl_llm_async({}, {"summary_kind": "map", "message": "地图"}, {"primary_message": "选路线喵"})
    assert len(fallback) == 1


def test_instant_line_selects_one_threat_and_does_not_invent_max_hp():
    service = _service()
    snapshot, payload = _combat_snapshot(), _combat_payload()
    payload["enemies"] = [{"name": "小怪"}, {"name": "重击怪"}]
    snapshot["raw_state"]["combat"]["enemies"] = [
        {"intents": [{"intent_type": "Attack", "total_damage": 2}]},
        {"intents": [{"intent_type": "Attack", "total_damage": 30}]},
    ]
    payload["player"] = {"current_hp": 7}
    text = service._instant_line_text(snapshot, {}, payload)
    assert "重击怪" in text and "小怪" not in text
    assert "我7" in text and "/0" not in text


def test_cooldown_does_not_consume_next_turn(monkeypatch):
    service = _service()
    clock = [1.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_danmaku.monotonic", lambda: clock[0])
    lines = []
    monkeypatch.setattr(service, "_schedule_catgirl_push", lambda text, **kw: lines.append(text))
    service._last_danmaku_at = 0.0
    service._emit_instant_catgirl_line(_combat_snapshot(), {}, _combat_payload())
    assert not lines
    clock[0] = 6.0
    service._emit_instant_catgirl_line(_combat_snapshot(), {}, _combat_payload())
    assert len(lines) == 1
