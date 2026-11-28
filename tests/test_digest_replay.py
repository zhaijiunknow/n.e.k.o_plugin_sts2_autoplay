"""回归：硬编码连招、旧缓存、执行过的步骤、同回合迟到与重复提交。"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.catgirl_llm import CatgirlCommentGenerator
from plugin.plugins.sts2_autoplay.loop_runner import _combat_plan_signature
from plugin.plugins.sts2_autoplay.tests.test_danmaku_timeliness import DummyLogger, _combat_payload, _combat_snapshot
from plugin.plugins.sts2_autoplay.tests.test_digest_quality import _service


def _with_plan():
    service, plugin = _service()
    snapshot = _combat_snapshot()
    snapshot["run_id"] = "run-a"
    snapshot["raw_state"]["run_id"] = "run-a"
    snapshot["raw_state"]["turn"] = snapshot["turn"]
    snapshot["raw_state"]["combat"]["hand"] = [
        {"card_id": "BASH", "name": "痛击", "playable": True},
        {"card_id": "COLD_SNAP", "name": "冷光", "playable": True},
    ]
    snapshot["available_actions"] = [{"type": "end_turn"}]
    service._state.snapshot = snapshot
    service._loop_runner._solver_plan_sig = _combat_plan_signature(snapshot)
    service._loop_runner._solver_plan_cached = {"in_combat": True, "line": [{"steps": [
        {"kind": "play_card", "card_id": "BASH", "card_name": "痛击"},
        {"kind": "play_card", "card_id": "COLD_SNAP", "card_name": "冷光"},
        {"kind": "end_turn"},
    ]}]}
    return service, plugin, snapshot


def test_combat_system_prompt_contains_no_hardcoded_combo(monkeypatch):
    gen = CatgirlCommentGenerator(DummyLogger())
    monkeypatch.setattr(gen, "_persona_system", lambda: "")
    messages = gen._build_messages(summary_text="生命17/80，无可核验计划", summary_kind="combat", payload={})
    system = messages[0]["content"]
    assert "痛击" not in system and "冷光" not in system
    assert "不能编造或沿用历史" in system
    assert "没有出牌建议时" in system


@pytest.mark.asyncio
async def test_both_commentary_paths_skip_executed_plan_steps():
    service, _, snapshot = _with_plan()
    service._state.solver_step_index = 1
    snapshot["raw_state"]["combat"]["hand"].pop(0)
    payload = _combat_payload()
    texts = [service._instant_line_text(snapshot, {}, payload), await service._build_combat_prompt(snapshot, payload)]
    for text in texts:
        assert "痛击" not in text
        assert "冷光" in text
    service._state.solver_step_index = 3
    assert service._current_commentary_plan_line(snapshot) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["turn", "run", "hand", "policy"])
async def test_stale_or_wrong_brain_plan_is_not_reported(changed):
    service, _, snapshot = _with_plan()
    if changed == "turn":
        snapshot["turn"] += 1
        snapshot["raw_state"]["turn"] = snapshot["turn"]
    elif changed == "run":
        snapshot["run_id"] = "run-b"
        snapshot["raw_state"]["run_id"] = "run-b"
    elif changed == "hand":
        snapshot["raw_state"]["combat"]["hand"] = []
    else:
        service._loop_runner._decision_engine = {"engine": "policy"}
    payload = {**_combat_payload(), "event_type": "combat_turn_changed"}
    service._client = SimpleNamespace(get_combat_plan=AsyncMock())
    texts = [service._instant_line_text(snapshot, {}, payload), await service._build_combat_prompt(snapshot, payload)]
    for text in texts:
        assert "痛击" not in text and "冷光" not in text
    service._client.get_combat_plan.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["hand", "turn", "run", "step", "hp"])
@pytest.mark.parametrize("response", ["先打痛击。", None])
async def test_late_generation_is_dropped_even_without_instant_danmaku_key(change, response):
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(False)
    service._state.last_instant_line_key = ""
    service._client = object()

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
        else:
            live["raw_state"]["combat"]["player"] = {"current_hp": 1}
        service._state.snapshot = live
        return response

    service._catgirl_llm = SimpleNamespace(generate=generate)
    await service._catgirl_llm_async(snapshot, _combat_payload(), {"primary_message": "旧建议。"})
    await asyncio.sleep(0)
    assert plugin.messages == []


@pytest.mark.asyncio
async def test_fresh_generated_commentary_still_reaches_host():
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(False)
    service._catgirl_llm = SimpleNamespace(generate=AsyncMock(return_value="敌人这回合要攻击。"))
    await service._catgirl_llm_async(snapshot, _combat_payload())
    assert len(plugin.messages) == 1
    assert "敌人这回合要攻击" in plugin.messages[0]["parts"][0]["text"]


@pytest.mark.asyncio
async def test_only_one_generation_is_in_flight():
    service, _, snapshot = _with_plan()
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        started.set()
        await release.wait()
        return None

    generate_mock = AsyncMock(side_effect=generate)
    service._catgirl_llm = SimpleNamespace(available=True, generate=generate_mock)
    service._maybe_emit_catgirl_llm(snapshot, {}, _combat_payload())
    await started.wait()
    for _ in range(10):
        service._maybe_emit_catgirl_llm(snapshot, {}, _combat_payload())
    assert generate_mock.await_count == 1
    release.set()
    await service._catgirl_llm_task


def test_same_commentary_is_not_resubmitted_after_scene_or_punctuation_change(monkeypatch):
    service, plugin, _ = _with_plan()
    now = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: now[0])
    assert service._push_host_commentary("先打痛击，再补冷光！", scene_key="turn-a")
    now[0] += 80
    assert not service._push_host_commentary("先打痛击再补冷光。", scene_key="turn-b")
    assert len(plugin.messages) == 1
    assert service._state.last_host_commentary_at == 1000
    assert service._push_host_commentary("血量已降到危险线。", scene_key="turn-b")
    assert len(plugin.messages) == 2
    now[0] += 301
    assert service._push_host_commentary("先打痛击，再补冷光！", scene_key="turn-c")


def test_content_dedup_is_run_scoped_and_bounded(monkeypatch):
    service, _, snapshot = _with_plan()
    now = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: now[0])
    assert service._push_host_commentary("先防御。", scene_key="run-a-turn-1")
    now[0] += 80
    snapshot["run_id"] = "run-b"
    assert service._push_host_commentary("先防御。", scene_key="run-b-turn-1")
    for i in range(100):
        service._remember_host_report(str(i), kind="commentary")
    assert len(service._recent_host_reports) <= 32


@pytest.mark.asyncio
async def test_queued_fallback_cannot_be_relabelled_as_a_new_scene():
    service, plugin, snapshot = _with_plan()
    service.set_danmaku_enabled(False)
    service._client = object()
    service._push_catgirl_fallback({"primary_message": "先打痛击。"}, _combat_payload())
    service._state.snapshot = {**snapshot, "turn": 99}
    service._state.last_push_scene_key = "new-turn"
    await asyncio.sleep(0)
    assert plugin.messages == []


@pytest.mark.asyncio
async def test_danmaku_waiting_for_avatar_is_dropped_after_scene_change():
    service, _, snapshot = _with_plan()
    service._client = SimpleNamespace(push_danmaku=AsyncMock())

    async def avatar():
        service._state.snapshot = {**snapshot, "turn": 99}
        return None

    service._catgirl_avatar_base64 = avatar
    await service._push_catgirl("先打痛击。")
    service._client.push_danmaku.assert_not_awaited()


@pytest.mark.parametrize("missing", ["run_id", "turn"])
def test_incomplete_cache_identity_is_not_enough_for_a_card_recommendation(missing):
    service, _, snapshot = _with_plan()
    snapshot["raw_state"].pop(missing)
    service._loop_runner._solver_plan_sig = _combat_plan_signature(snapshot)
    assert service._current_commentary_plan_line(snapshot) == ""


@pytest.mark.asyncio
async def test_shutdown_cancels_inflight_commentary_before_it_can_speak():
    service, plugin, snapshot = _with_plan()
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(**kwargs):
        started.set()
        await release.wait()
        return "旧回合建议。"

    service._catgirl_llm = SimpleNamespace(available=True, generate=generate, shutdown=AsyncMock())
    service._maybe_emit_catgirl_llm(snapshot, {}, _combat_payload())
    await started.wait()
    task = service._catgirl_llm_task
    await service.shutdown()
    assert task.cancelled()
    assert service._catgirl_llm_task is None
    assert plugin.messages == []
