"""局势点评按事实共享去重，不让换文案/多路触发重播；静止闲聊独立。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.tests.test_danmaku_idle import setup_idle


def setup_change(monkeypatch):
    service, clock, snapshot, payload = setup_idle(monkeypatch)
    snapshot["run_id"] = "run-a"
    service._catgirl_avatar_base64 = AsyncMock(return_value=None)
    return service, clock, snapshot, payload


async def drain():
    # fire-and-forget pushes include wait_for; let their HTTP mocks and callbacks finish.
    for _ in range(8):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["combat", "event", "map", "reward", "shop", "selection", "rest"])
async def test_same_facts_not_replayed_after_text_cache_expires_or_wording_changes(monkeypatch, kind):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    payload["summary_kind"] = kind
    snapshot["screen"] = payload["screen"] = kind.upper()
    for at, wording in ((0, "这次可要仔细选喵。"), (61, "让我再看看怎么选。"), (180, "这个选择值得考虑！")):
        clock[0] = at
        service._emit_instant_catgirl_line(snapshot, {"primary_message": wording}, payload)
        await drain()
        service._push_catgirl_fallback({"primary_message": wording}, payload)
        await drain()
    assert service._client.push_danmaku.await_count == 1
    assert len(service._reported_danmaku_changes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("generated", ["敌人来势汹汹，注意防守喵！", None])
async def test_inflight_llm_or_its_fallback_cannot_repeat_delivered_instant_fact(monkeypatch, generated):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        entered.set()
        await release.wait()
        return generated

    service._catgirl_llm = SimpleNamespace(available=True, generate=generate)
    task = asyncio.create_task(service._catgirl_llm_async(snapshot, payload, {"primary_message": "先防御这下攻击！"}))
    try:
        await entered.wait()
        service._emit_instant_catgirl_line(snapshot, {}, payload)
        await drain()
        clock[0] = 7  # past output cooldown: wording differs, but the fact is the same
        release.set()
        await task
        await drain()
        assert service._client.push_danmaku.await_count == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_already_reported_fact_does_not_start_another_llm_generation(monkeypatch):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    await drain()
    clock[0] = 120
    service._catgirl_llm_enabled = True
    service._catgirl_llm = SimpleNamespace(available=True, generate=AsyncMock(return_value="换个说法再报一遍"))
    service._maybe_emit_catgirl_llm(snapshot, {}, payload)
    await service._catgirl_llm_async(snapshot, payload)
    service._catgirl_llm.generate.assert_not_awaited()


@pytest.mark.parametrize("noise", ["wording", "trigger", "timestamp", "actions", "step"])
def test_trigger_or_poll_metadata_is_not_a_new_fact(monkeypatch, noise):
    service, _, snapshot, payload = setup_change(monkeypatch)
    snapshot["situation_summary"] = {"payload": {"player": {"current_hp": 60}}}
    before = service._danmaku_change_key(snapshot, payload)
    if noise == "wording":
        payload["message"] = "换一个说法"
        snapshot["situation_summary"]["text"] = "另一个摘要"
    elif noise == "trigger":
        payload.update(trigger="combat_turn", event_type="combat_state_changed", companion_evaluation={"primary_message": "说过了"})
    elif noise == "timestamp":
        snapshot["polled_at"] = 999
        snapshot["situation_summary"]["payload"].update(captured_at=999, state_version=2)
    elif noise == "actions":
        snapshot["situation_summary"]["payload"]["available_actions"] = ["end_turn"]
    else:
        service._state.step_count += 1
    assert service._danmaku_change_key(snapshot, payload) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hp", "intent", "hand", "selection", "turn", "run", "floor"])
async def test_real_fact_changes_are_not_silenced(monkeypatch, change):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    before = service._danmaku_change_key(snapshot, payload)
    await service._push_change_catgirl("原来的局势", service._commentary_observation_key(snapshot), before, fetch_avatar=False)
    if change == "hp":
        payload["player"]["current_hp"] = 12
    elif change == "intent":
        snapshot["raw_state"]["combat"]["enemies"][0]["intents"][0]["total_damage"] = 50
    elif change == "hand":
        snapshot["raw_state"]["combat"]["hand"] = [{"card_id": "DEFEND"}]
    elif change == "selection":
        snapshot["raw_state"]["selection"] = {"selected_count": 1, "max_select": 2}
    elif change == "turn":
        snapshot["turn"] += 1
    elif change == "run":
        snapshot["run_id"] = "run-b"
    else:
        snapshot["floor"] += 1
    after = service._danmaku_change_key(snapshot, payload)
    assert after != before
    clock[0] = 7
    await service._push_change_catgirl("新发生的变化", service._commentary_observation_key(snapshot), after, fetch_avatar=False)
    assert service._client.push_danmaku.await_count == 2


@pytest.mark.asyncio
async def test_fact_ledger_does_not_disable_stationary_companionship(monkeypatch):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    await drain()
    service._maybe_emit_idle_catgirl(snapshot, payload)
    clock[0] = 25
    service._maybe_emit_idle_catgirl(snapshot, payload)
    await service._catgirl_llm_task
    assert service._client.push_danmaku.await_count == 2
    assert len(service._reported_danmaku_changes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["failure", "cooldown", "stale"])
async def test_dropped_or_failed_send_does_not_consume_fact(monkeypatch, blocked):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    key, observation = service._danmaku_change_key(snapshot, payload), service._commentary_observation_key(snapshot)
    if blocked == "failure":
        service._client.push_danmaku.side_effect = OSError("network")
    elif blocked == "cooldown":
        service._last_danmaku_at = 0
    else:
        original = deepcopy(snapshot)
        snapshot["turn"] += 1
    await service._push_change_catgirl("该防御了", observation, key, fetch_avatar=False)
    assert key not in service._reported_danmaku_changes
    service._client.push_danmaku.side_effect = None
    if blocked == "stale":
        service._state.snapshot = original
    clock[0] = 7
    await service._push_change_catgirl("该防御了", observation, key, fetch_avatar=False)
    assert key in service._reported_danmaku_changes


@pytest.mark.asyncio
async def test_fact_history_is_bounded(monkeypatch):
    service, clock, snapshot, _ = setup_change(monkeypatch)
    for index in range(300):
        clock[0] = index * 7
        await service._push_change_catgirl(f"局势{index}", service._commentary_observation_key(snapshot), str(index), fetch_avatar=False)
    assert len(service._reported_danmaku_changes) == 256
    assert "299" in service._reported_danmaku_changes
    assert "0" not in service._reported_danmaku_changes


@pytest.mark.asyncio
async def test_full_delivery_path_dedups_forced_and_different_trigger_sources(monkeypatch):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    monkeypatch.setattr(service, "_push_host_situation_digest", lambda *args: None)
    monkeypatch.setattr(service, "_should_emit_long_commentary", lambda *args: True)
    snapshot["catgirl_sync"] = {"payload": payload, "should_sync": True, "force": True}
    for at, trigger in ((0, "scene_entry"), (7, "combat_turn"), (75, "player_operation"), (180, "poll")):
        clock[0] = at
        payload["trigger"] = trigger
        payload["companion_evaluation"] = {"should_comment": True, "primary_message": f"这次点评来源{trigger}"}
        service._deliver_catgirl_sync(snapshot)
        await drain()
    lines = [call.args[0] for call in service._client.push_danmaku.await_args_list]
    # 75/180 秒允许静止闲聊，但不能把旧局势换成“来源xxx”又报一遍。
    assert len(lines) == 3
    assert lines[1:] == ["这回合我陪你慢慢看喵。", "我陪着呢，慢慢来就好喵。"]
    assert all("点评来源" not in line for line in lines)
    assert len(service._reported_danmaku_changes) == 1


@pytest.mark.asyncio
async def test_failed_instant_send_can_retry_same_fact(monkeypatch):
    service, clock, snapshot, payload = setup_change(monkeypatch)
    service._client.push_danmaku.side_effect = OSError("network")
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    await drain()
    assert not service._reported_danmaku_changes
    assert not service._state.last_instant_line_key
    service._client.push_danmaku.side_effect = None
    clock[0] = 7
    service._emit_instant_catgirl_line(snapshot, {}, payload)
    await drain()
    assert service._client.push_danmaku.await_count == 2
    assert len(service._reported_danmaku_changes) == 1
