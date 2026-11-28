"""Configuration page API: allowlist, persistence, effective runtime values."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.settings_config import FIELDS, validate_patch
from plugin.plugins.sts2_autoplay.tests.test_map_wait_settings import SettingsPlugin


def test_every_default_and_numeric_boundary_is_valid():
    defaults = {key: spec["default"] for key, spec in FIELDS.items()}
    assert validate_patch(defaults) == defaults
    for key, spec in FIELDS.items():
        if spec["type"] == "number":
            for value in (spec["minimum"], spec["maximum"]):
                assert validate_patch({key: value}) == {key: value}


@pytest.mark.parametrize("patch", [None, [], {}, {"base_url": "http://example.test"},
    {"api_key": "secret"}, {"policy_enabled": "false"}, {"policy_enabled": 0},
    {"policy_timeout_seconds": True}, {"policy_timeout_seconds": "20"},
    {"policy_timeout_seconds": float("nan")}, {"policy_timeout_seconds": float("inf")},
    {"policy_timeout_seconds": 0}, {"policy_timeout_seconds": 10**1000},
    {"action_interval_seconds": -1}, {"autoplay_push_probability": 1.01},
    {"livestream_profile": "bad"}, {"livestream_wait_scale": 0},
    {"policy_enabled": False, "map_policy": "auto"}])
def test_bad_patch_cannot_write_or_apply(patch):
    plugin = SettingsPlugin()
    before = deepcopy(plugin._service._cfg)
    with pytest.raises(ValueError):
        asyncio.run(plugin.sts2_update_settings(patch=patch))
    assert plugin.memory_config.patches == []
    assert plugin._service._cfg == before


def test_get_is_read_only_and_does_not_disclose_secrets():
    plugin = SettingsPlugin()
    plugin._service._cfg.update(api_key="secret", base_url="http://127.0.0.1:18080")
    plugin._service._policy_advisor.assets.publish = lambda: pytest.fail("must not publish/download")
    result = asyncio.run(plugin.sts2_get_settings())
    assert set(result["settings"]) == set(FIELDS)
    assert "secret" not in str(result)
    assert plugin.memory_config.patches == []
    assert plugin.client.config_writes == []


def test_save_only_patch_applies_runtime_and_preserves_other_settings():
    plugin = SettingsPlugin()
    patch = {"policy_enabled": False, "policy_timeout_seconds": 7,
             "catgirl_llm_enabled": False, "host_digest_enabled": False,
             "run_reflection_enabled": False, "encounter_memory_enabled": False,
             "action_interval_seconds": 2.5, "combat_replay_interval_seconds": 0.1,
             "companion_push_probability": 0, "autoplay_push_probability": 0,
             "memory_report_interval_seconds": 0, "autoplay_on_start": True}
    result = asyncio.run(plugin.sts2_update_settings(patch=patch))
    assert result["saved"]
    assert plugin.memory_config.patches == [{"sts2": patch}]
    assert plugin._cfg["other_setting"] is True
    for key, value in patch.items():
        assert result["settings"][key] == value
    service = plugin._service
    assert not service._policy_advisor.enabled and service._policy_advisor._timeout == 7
    assert service._state.force_replan_requested
    assert service._cfg_action_interval() == 2.5
    assert service._cfg_combat_replay_interval() == 0.1
    assert service._cfg_companion_push_probability() == 0
    assert service._cfg_autoplay_push_probability() == 0
    assert service._cfg_memory_report_interval() == 0
    assert service._state.autoplay_state != "running"
    assert not service._cfg.get("autoplay_on_start", False)
    assert service._pending_restart_settings == {"autoplay_on_start": True}
    assert result["restart_required"] == ["autoplay_on_start"]


def test_failed_persistence_leaves_runtime_unchanged():
    plugin = SettingsPlugin()
    plugin.memory_config.fail = True
    before = plugin._service.get_settings()
    with pytest.raises(RuntimeError, match="保存失败"):
        asyncio.run(plugin.sts2_update_settings(patch={"policy_enabled": False, "danmaku_enabled": False}))
    assert plugin._service.get_settings() == before
    assert plugin._cfg == {"other_setting": True}
    assert plugin.client.config_writes == []


def test_offline_mod_reports_saved_but_not_synced():
    plugin = SettingsPlugin()
    plugin._service._client = None
    result = asyncio.run(plugin.sts2_update_settings(patch={"danmaku_enabled": False,
        "livestream_enabled": True, "livestream_profile": "focused", "livestream_wait_scale": 2}))
    assert result["saved"]
    assert "尚未同步" in result["summary"]
    assert not result["mod_sync"]["danmaku"]["applied"]
    assert not result["mod_sync"]["livestream"]["applied"]
    assert not plugin._service._danmaku_enabled
    assert plugin._service._livestream.profile == "focused"
    assert plugin._service._livestream.wait_scale == 2


def test_partial_livestream_edit_preserves_profile_and_enabled():
    plugin = SettingsPlugin()
    plugin._service._cfg.update(livestream_enabled=True, livestream_profile="chaotic")
    plugin._service._livestream.configure(plugin._service._cfg)
    plugin._service.sync_livestream_to_mod = AsyncMock(return_value={"applied": True})
    result = asyncio.run(plugin.sts2_update_settings(patch={"livestream_wait_scale": 1.5}))
    assert result["settings"]["livestream_profile"] == "chaotic"
    assert result["settings"]["livestream_enabled"] is True
    assert plugin.memory_config.patches == [{"sts2": {"livestream_wait_scale": 1.5}}]


def test_unrelated_save_does_not_reset_livestream_wait():
    plugin = SettingsPlugin()
    plugin._service._livestream.key = "current-choice"
    plugin._service._livestream.deadline = 123
    asyncio.run(plugin.sts2_update_settings(patch={"action_interval_seconds": 2}))
    assert plugin._service._livestream.key == "current-choice"
    assert plugin._service._livestream.deadline == 123


def test_extra_top_level_keys_rejected():
    plugin = SettingsPlugin()
    with pytest.raises(ValueError):
        asyncio.run(plugin.sts2_update_settings(patch={"policy_enabled": True}, base_url="unsafe"))
    assert plugin.memory_config.patches == []


def test_effective_settings_follow_control_page_changes():
    plugin = SettingsPlugin()
    service = plugin._service
    service.set_danmaku_enabled(False, sync_mod=False)
    service.set_host_digest_enabled(False)
    service.set_run_reflection_enabled(False)
    service._cfg["host_commentary_min_interval_seconds"] = 0  # legacy value is clamped to 5
    result = asyncio.run(plugin.sts2_get_settings())["settings"]
    assert result["danmaku_enabled"] is False
    assert result["host_digest_enabled"] is False
    assert result["run_reflection_enabled"] is False
    assert result["host_commentary_min_interval_seconds"] == 0
    assert "host_commentary_min_interval_seconds" in service.get_settings()["field_errors"]
    assert service._host_commentary_min_interval() == 5
    assert plugin.memory_config.patches == []


def test_poll_and_commentary_intervals_reach_runtime_accessors():
    plugin = SettingsPlugin()
    patch = {"poll_interval_active_seconds": 2, "poll_interval_idle_seconds": 7,
             "poll_fallback_seconds": 10, "host_commentary_min_interval_seconds": 5,
             "host_commentary_when_danmaku_off": False}
    asyncio.run(plugin.sts2_update_settings(patch=patch))
    service = plugin._service
    assert service._cfg_poll_interval(active=True) == 2
    assert service._cfg_poll_interval(active=False) == 7
    assert service._cfg_fallback_poll_interval() == 10
    assert service._host_commentary_min_interval() == 5
    assert not service._is_host_commentary_enabled()


@pytest.mark.parametrize("entry,key", [
    ("sts2_set_host_digest", "host_digest_enabled"),
    ("sts2_set_run_reflection", "run_reflection_enabled"),
    ("llm_set_host_digest", "host_digest_enabled"),
    ("llm_set_run_reflection", "run_reflection_enabled"),
])
def test_talk_switches_persist_and_report_effective_state(entry, key):
    plugin = SettingsPlugin()
    plugin._push_silent_mode_change = lambda *args: None
    for enabled in (False, True):
        result = asyncio.run(getattr(plugin, entry)(enabled=enabled))
        assert result["enabled"] is enabled
        assert plugin.memory_config.patches[-1] == {"sts2": {key: enabled}}
        assert plugin._cfg[key] is enabled
        assert plugin._service.get_settings()["settings"][key] is enabled
        status = asyncio.run(plugin._service.get_status())
        assert status[key.removesuffix("_enabled")]["enabled"] is enabled
    assert plugin._cfg["other_setting"] is True


@pytest.mark.parametrize("entry,key", [
    ("sts2_set_host_digest", "host_digest_enabled"),
    ("sts2_set_run_reflection", "run_reflection_enabled"),
    ("llm_set_host_digest", "host_digest_enabled"),
    ("llm_set_run_reflection", "run_reflection_enabled"),
])
def test_talk_switch_save_failure_and_invalid_input_do_not_apply(entry, key):
    plugin = SettingsPlugin()
    plugin._push_silent_mode_change = lambda *args: pytest.fail("must not report success")
    plugin.memory_config.fail = True
    with pytest.raises(RuntimeError, match="保存失败"):
        asyncio.run(getattr(plugin, entry)(enabled=False))
    assert getattr(plugin._service._state, key) is True
    assert key not in plugin._cfg
    plugin.memory_config.fail = False
    with pytest.raises(ValueError):
        asyncio.run(getattr(plugin, entry)(enabled="false"))
    assert plugin.memory_config.patches == []


def test_control_page_talk_switches_survive_service_restart():
    plugin = SettingsPlugin()
    plugin._push_silent_mode_change = lambda *args: None

    async def check():
        await plugin.sts2_set_host_digest(enabled=False)
        await plugin.sts2_set_run_reflection(enabled=False)
        saved = {}
        for patch in plugin.memory_config.patches:
            saved.update(patch["sts2"])
        restarted = SettingsPlugin()._service
        await restarted.startup(saved)
        try:
            status = await restarted.get_status()
            assert status["host_digest"]["enabled"] is False
            assert status["run_reflection"]["enabled"] is False
        finally:
            await restarted.shutdown()

    asyncio.run(check())


@pytest.mark.parametrize("key,value", [
    ("poll_fallback_seconds", 3), ("memory_report_interval_seconds", -1),
    ("autoplay_push_probability", 1.5), ("companion_push_probability", "bad"),
    ("policy_timeout_seconds", 0), ("livestream_profile", "old-profile"),
    ("danmaku_enabled", "yes"),
])
def test_legacy_invalid_settings_are_readable_and_do_not_break_other_saves(key, value):
    plugin = SettingsPlugin()
    plugin._service._cfg[key] = value
    result = asyncio.run(plugin.sts2_get_settings())
    assert result["settings"][key] == value
    assert key in result["field_errors"]
    assert plugin.memory_config.patches == []
    saved = asyncio.run(plugin.sts2_update_settings(patch={"action_interval_seconds": 2.5}))
    assert saved["saved"] is True
    assert key in saved["field_errors"]
    assert plugin._service._cfg[key] == value
    assert plugin.memory_config.patches == [{"sts2": {"action_interval_seconds": 2.5}}]
    fixed = asyncio.run(plugin.sts2_update_settings(patch={key: FIELDS[key]["default"]}))
    assert key not in fixed["field_errors"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_legacy_value_is_json_safe_without_silently_fixing_it(value):
    import json

    plugin = SettingsPlugin()
    plugin._service._cfg["autoplay_push_probability"] = value
    result = plugin._service.get_settings()
    assert result["settings"]["autoplay_push_probability"] == str(value)
    assert "autoplay_push_probability" in result["field_errors"]
    json.dumps(result, allow_nan=False)


def test_repaired_restart_preference_overrides_invalid_legacy_readback():
    plugin = SettingsPlugin()
    plugin._service._cfg["autoplay_on_start"] = "old-invalid-value"
    assert "autoplay_on_start" in plugin._service.get_settings()["field_errors"]
    saved = asyncio.run(plugin.sts2_update_settings(patch={"autoplay_on_start": False}))
    assert saved["saved"] is True
    assert saved["settings"]["autoplay_on_start"] is False
    assert "autoplay_on_start" not in saved["field_errors"]
    # The current session keeps its original startup preference until restart.
    assert plugin._service._cfg["autoplay_on_start"] == "old-invalid-value"
    assert plugin._service._pending_restart_settings["autoplay_on_start"] is False


@pytest.mark.parametrize("context", [None, {}, {"run_id": "settings-regression", "lanlan_name": "test", "entry_timeout": 30}])
def test_save_accepts_host_injected_context_without_persisting_it(context):
    from plugin.sdk.shared.core.decorators import EVENT_META_ATTR
    from plugin.sdk.shared.core.entry_runtime import prepare_entry_kwargs

    plugin = SettingsPlugin()
    handler = plugin.sts2_update_settings
    patch = {"action_interval_seconds": 2.5}
    args = prepare_entry_kwargs(plugin_id="sts2_autoplay", entry_id="sts2_update_settings",
                                handler=handler, meta=getattr(handler, EVENT_META_ATTR),
                                args={"patch": patch, "_ctx": context})
    result = asyncio.run(handler(**args))
    assert result["saved"]
    assert plugin.memory_config.patches == [{"sts2": patch}]
    assert "_ctx" not in plugin._cfg
    assert "_ctx" not in result["settings"]


@pytest.mark.parametrize("extra", [{"base_url": "unsafe"}, {"run_id": "unexpected"},
                                   {"_unexpected": True}])
def test_host_context_does_not_allow_arbitrary_top_level_arguments(extra):
    plugin = SettingsPlugin()
    before = deepcopy(plugin._service._cfg)
    with pytest.raises(ValueError, match="包含不支持的请求参数"):
        asyncio.run(plugin.sts2_update_settings(patch={"policy_enabled": False},
                                               _ctx={"run_id": "test"}, **extra))
    assert plugin.memory_config.patches == []
    assert plugin._service._cfg == before


def test_host_context_cannot_bypass_patch_validation():
    plugin = SettingsPlugin()
    with pytest.raises(ValueError):
        asyncio.run(plugin.sts2_update_settings(patch={"_ctx": {"run_id": "test"}},
                                               _ctx={"run_id": "test"}))
    assert plugin.memory_config.patches == []
