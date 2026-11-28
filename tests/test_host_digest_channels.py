"""Host digests stay independent of overlay state; stale overlay keys cannot mute them."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.tests.test_danmaku_timeliness import _combat_payload, _combat_snapshot
from plugin.plugins.sts2_autoplay.tests.test_digest_quality import _service
from plugin.plugins.sts2_autoplay.tests.test_digest_replay import _with_plan


@pytest.mark.parametrize("overlay_enabled", [True, False])
@pytest.mark.parametrize("digest_enabled", [True, False])
def test_digest_uses_own_switch_not_overlay_switch(overlay_enabled, digest_enabled):
    service, plugin = _service()
    service.set_danmaku_enabled(overlay_enabled)
    service._state.host_digest_enabled = digest_enabled
    service._push_host_situation_digest(_combat_snapshot(), _combat_payload())
    assert len(plugin.messages) == int(digest_enabled)


@pytest.mark.parametrize("first", ["digest", "commentary"])
def test_overlay_off_channels_share_cooldown(monkeypatch, first):
    service, plugin = _service()
    service.set_danmaku_enabled(False)
    clock = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: clock[0])
    snapshot, payload = _combat_snapshot(), _combat_payload()
    service._state.snapshot = snapshot

    def digest():
        service._push_host_situation_digest(snapshot, payload)

    def commentary():
        return service._push_host_commentary("Current advice.", scene_key="current")

    send_first, send_second = (digest, commentary) if first == "digest" else (commentary, digest)
    send_first()
    assert len(plugin.messages) == 1
    send_second()
    assert len(plugin.messages) == 1
    clock[0] += 80
    send_second()
    assert len(plugin.messages) == 2


def test_overlay_off_digest_still_deduplicates_unchanged_facts(monkeypatch):
    service, plugin = _service()
    service.set_danmaku_enabled(False)
    clock = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: clock[0])
    snapshot, payload = _combat_snapshot(), _combat_payload()
    service._push_host_situation_digest(snapshot, payload)
    assert len(plugin.messages) == 1
    clock[0] += 80
    service._push_host_situation_digest(snapshot, payload)
    service._push_host_situation_digest({**snapshot, "turn": snapshot["turn"] + 1}, payload)
    assert len(plugin.messages) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["Fresh advice.", None])
@pytest.mark.parametrize("disable_during_generation", [False, True])
async def test_old_overlay_key_does_not_discard_fresh_host_commentary(response, disable_during_generation):
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(True)
    service._state.last_instant_line_key = "old-run|0|EVENT|1|None"
    service._client = SimpleNamespace(push_danmaku=AsyncMock())
    if not disable_during_generation:
        service.set_danmaku_enabled(False)

    async def generate(**kwargs):
        if disable_during_generation:
            service.set_danmaku_enabled(False)
        return response

    service._catgirl_llm = SimpleNamespace(generate=generate)
    await service._catgirl_llm_async(
        snapshot, _combat_payload(), {"primary_message": "Fallback advice."},
        scene_key="current-run|0|COMBAT|5|2",
    )
    await asyncio.sleep(0)
    assert len(plugin.messages) == 1
    assert (response or "Fallback advice.") in plugin.messages[0]["parts"][0]["text"]
    service._client.push_danmaku.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["Old advice.", None])
@pytest.mark.parametrize("change", ["hand", "turn", "run", "step", "step_count", "hp"])
async def test_overlay_off_still_discards_real_observation_or_execution_changes(change, response):
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(False)
    service._state.last_instant_line_key = "old-run|0|EVENT|1|None"
    service._client = SimpleNamespace(push_danmaku=AsyncMock())

    async def generate(**kwargs):
        live = deepcopy(snapshot)
        if change == "hand":
            live["raw_state"]["combat"]["hand"].pop(0)
        elif change == "turn":
            live["turn"] += 1
        elif change == "run":
            live["run_id"] = "other-run"
        elif change == "step":
            service._state.solver_step_index += 1
        elif change == "step_count":
            service._state.step_count += 1
        else:
            live["raw_state"]["combat"]["player"] = {"current_hp": 1}
        service._state.snapshot = live
        return response

    service._catgirl_llm = SimpleNamespace(generate=generate)
    await service._catgirl_llm_async(
        snapshot, _combat_payload(), {"primary_message": "Old fallback."},
        scene_key="current-run|0|COMBAT|5|2",
    )
    await asyncio.sleep(0)
    assert plugin.messages == []
    service._client.push_danmaku.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("response", ["Old advice.", None])
async def test_overlay_on_preserves_instant_scene_guard(response):
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(True)
    service._state.last_instant_line_key = "newer-scene"
    service._catgirl_llm = SimpleNamespace(generate=AsyncMock(return_value=response))
    service._push_change_catgirl = AsyncMock()
    await service._catgirl_llm_async(
        snapshot, _combat_payload(), {"primary_message": "Old fallback."}, scene_key="older-scene",
    )
    await asyncio.sleep(0)
    service._push_change_catgirl.assert_not_awaited()
    assert plugin.messages == []
