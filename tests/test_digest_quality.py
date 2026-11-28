"""只读轮询不能开口；速报须带当前事实，不能在 fallback 中丢失约束。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.tests.test_danmaku_timeliness import (
    DummyLogger,
    _combat_payload,
    _combat_snapshot,
)
from plugin.plugins.sts2_autoplay.tests.test_push_delivery import NotificationPlugin


@pytest.mark.asyncio
async def test_status_polling_never_sends_proactive_feedback():
    plugin = NotificationPlugin()
    plugin.logger = DummyLogger()
    plugin._service = SimpleNamespace(get_status=AsyncMock(return_value={"summary": "连上了", "floor": 5}))
    for _ in range(12):
        result = await STS2AutoplayPlugin.sts2_get_status(plugin)
        assert result.is_ok()
        assert result.value == {"summary": "连上了", "floor": 5}
    assert plugin._service.get_status.await_count == 12
    assert plugin.messages == []


def _service():
    plugin = NotificationPlugin()
    return STS2AutoplayService(DummyLogger(), lambda payload: None, plugin._push_frontend_notification), plugin


def test_fallback_preserves_full_facts_and_prompt_contract():
    service, plugin = _service()
    snapshot = _combat_snapshot()
    snapshot["raw_state"]["run"] = {"character_id": "SILENT"}
    payload = _combat_payload()
    payload["player"]["block"] = 4
    service._push_host_situation_digest(snapshot, payload)
    assert len(plugin.messages) == 1
    message = plugin.messages[0]
    text = message["parts"][0]["text"]
    for fact in ("静默猎手", "第5层", "第2回合", "60/80", "格挡4", "攻击 9"):
        assert fact in text
    for constraint in ("不是动作执行回执", "不要沿用", "不要保证", "不要反问"):
        assert constraint in text
    assert message["metadata"]["delivery_semantics"] == "proactive"
    assert message["metadata"]["prompt_ephemeral"] is True
    assert message["visibility"] == []
    assert message["ai_behavior"] == "respond"


def test_commentary_also_carries_current_observation_not_just_llm_advice():
    service, plugin = _service()
    snapshot = _combat_snapshot()
    snapshot["raw_state"]["run"] = {"character_name": "储君"}
    payload = _combat_payload(hp=17)
    payload["player"]["block"] = 0
    snapshot["situation_summary"] = {"kind": "combat", "payload": payload}
    service._state.snapshot = snapshot
    assert service._push_host_commentary("先垫格挡。", scene_key="test")
    text = plugin.messages[0]["parts"][0]["text"]
    for phrase in ("储君", "17/80", "格挡0", "先垫格挡", "仅供参考", "当前未由插件自动操作"):
        assert phrase in text


@pytest.mark.parametrize("driving", [True, False])
def test_digest_does_not_claim_autoplay_when_not_driving(driving):
    service, _ = _service()
    service._state.autoplay_state = "running" if driving else "paused"
    text = service._host_digest_prompt(_combat_snapshot(), facts="战斗中")
    assert ("自动游玩正在运行" in text) is driving
    assert ("当前未由插件自动操作" in text) is not driving



@pytest.mark.parametrize("commentary", ["", "先垫格挡。"])
def test_digest_prompt_stays_compact_and_keeps_factual_boundaries(commentary):
    service, _ = _service()
    facts = "静默猎手，第5层，生命17/80，格挡0"
    text = service._host_digest_prompt(_combat_snapshot(), facts=facts, commentary=commentary)
    control = "当前未由插件自动操作"
    assert len(text) - len(facts) - len(commentary) - len(control) <= 220
    for constraint in (
        "按人设用1～2句短话", "仅据最近观测（可能已变化）", "不要沿用旧局信息",
        "重复已报内容", "建议不等于已执行", "不编造牌或战果", "无结算依据不要保证",
        "不要反问或索要指令", "未自动操作勿自称代打",
    ):
        assert constraint in text
    assert facts in text
    assert ("点评（仅供参考，观测优先）：" in text) is bool(commentary)


def test_missing_stats_are_not_invented():
    service, _ = _service()
    payload = _combat_payload()
    payload["player"] = {"current_hp": 0}
    text = service._situation_digest_text(_combat_snapshot(), payload)
    assert "我方生命0" in text
    assert "/0" not in text
    assert "格挡" not in text


@pytest.mark.parametrize(("screen", "expected"), [
    ("MAP", "地图选路"), ("EVENT", "事件选择"), ("CARD_SELECTION_TRANSFORM", "卡牌变化选择"),
    ("REST", "休息处"), ("SHOP", "商店"),
])
def test_noncombat_digest_uses_readable_screen_names(screen, expected):
    service, _ = _service()
    text = service._situation_digest_text({"screen": screen, "floor": 4}, {})
    assert expected in text and "第4层" in text
    assert screen not in text


@pytest.mark.parametrize("screen", ["UNKNOWN", "MAIN_MENU", ""])
def test_unknown_or_menu_screen_does_not_trigger_empty_chatter(screen):
    service, plugin = _service()
    service._push_host_situation_digest({"screen": screen}, {})
    assert plugin.messages == []
    assert service._state.last_host_digest_at == 0


@pytest.mark.parametrize("first", ["digest", "commentary"])
def test_switching_danmaku_channels_cannot_bypass_speech_interval(monkeypatch, first):
    service, plugin = _service()
    clock = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: clock[0])
    snapshot, payload = _combat_snapshot(), _combat_payload()
    if first == "digest":
        service._push_host_situation_digest(snapshot, payload)
        service.set_danmaku_enabled(False)
        assert not service._push_host_commentary("先防御。", scene_key="other")
        clock[0] += 80
        assert service._push_host_commentary("先防御。", scene_key="other")
    else:
        service.set_danmaku_enabled(False)
        assert service._push_host_commentary("先防御。", scene_key="other")
        service.set_danmaku_enabled(True)
        service._push_host_situation_digest(snapshot, payload)
        assert len(plugin.messages) == 1
        clock[0] += 80
        service._push_host_situation_digest(snapshot, payload)
    assert len(plugin.messages) == 2


def test_same_floor_and_turn_in_new_run_can_be_reported_after_cooldown(monkeypatch):
    service, plugin = _service()
    clock = [1000.0]
    monkeypatch.setattr("plugin.plugins.sts2_autoplay.service_host.time", lambda: clock[0])
    snapshot, payload = _combat_snapshot(), _combat_payload()
    service._push_host_situation_digest({**snapshot, "run_id": "a"}, payload)
    clock[0] += 80
    service._push_host_situation_digest({**snapshot, "run_id": "b"}, payload)
    assert len(plugin.messages) == 2
