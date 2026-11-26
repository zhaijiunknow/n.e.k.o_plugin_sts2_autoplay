"""联机地图等待：配置、计时、面板入口保存与实例同步。"""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest
from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin
from plugin.plugins.sts2_autoplay import heuristic_planner as planner_module
from plugin.plugins.sts2_autoplay.heuristic_planner import validate_map_vote_wait_seconds
from plugin.plugins.sts2_autoplay.tests.test_autoplay_policy_fixes import _MAP_NODES, _map_context, _service


@pytest.mark.parametrize("value", [0, 0.5, 20, 60, 86400, "0"])
def test_config_wait_seconds_is_used_by_planner(value):
    service, _ = _service()
    service._cfg = {"map_policy": "follow", "map_vote_wait_seconds": value}
    service._configure_map_policy_from_config()
    assert service._map_vote_wait_seconds == float(value)
    assert service._planner._map_vote_wait_seconds == float(value)


@pytest.mark.parametrize("value", [None, True, -1, "bad", float("nan"), float("inf"), 86401])
def test_bad_seconds_rejected_and_old_config_falls_back(value):
    with pytest.raises(ValueError, match="0～86400"):
        validate_map_vote_wait_seconds(value)
    service, _ = _service()
    service._cfg = {"map_vote_wait_seconds": value}
    service._configure_map_policy_from_config()
    assert service._map_vote_wait_seconds == 20
    assert service._map_policy == "wait"


def test_default_and_legacy_map_config_still_work():
    service, _ = _service()
    service._cfg = {}
    service._configure_map_policy_from_config()
    assert (service._map_policy, service._map_vote_wait_seconds) == ("wait", 20)
    service._cfg = {"map_follow_player": True, "map_vote_wait_seconds": 0}
    service._configure_map_policy_from_config()
    assert (service._map_policy, service._map_vote_wait_seconds) == ("follow", 0)


def test_follow_wait_deadline_and_new_map_reset(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(planner_module, "monotonic", lambda: now[0])
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=7.5)
    context = _map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=True)
    assert service._planner.plan(context) is None
    now[0] = 107.49
    assert service._planner.plan(context) is None
    now[0] = 107.5
    assert service._planner.plan(context).action_type == "choose_map_node"
    next_map = deepcopy(context)
    next_map["summary_context"]["payload"]["floor"] += 1
    assert service._planner.plan(next_map) is None
    service.set_map_policy("follow", vote_wait_seconds=0)
    assert service._planner.plan(next_map).action_type == "choose_map_node"


def test_changing_wait_restarts_current_timer(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(planner_module, "monotonic", lambda: now[0])
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=20)
    context = _map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=True)
    assert service._planner.plan(context) is None
    now[0] = 115
    service.set_map_policy("follow", vote_wait_seconds=5)
    assert service._planner.plan(context) is None
    now[0] = 120
    assert service._planner.plan(context) is not None


@pytest.mark.parametrize("policy,multiplayer,other_vote,wait,expected", [
    ("follow", True, 1, 60, 1),
    ("follow", False, None, 60, 0),
    ("auto", True, None, 60, 0),
    ("wait", True, 1, 0, None),
])
def test_wait_does_not_delay_vote_singleplayer_or_auto(policy, multiplayer, other_vote, wait, expected):
    service, _ = _service()
    service.set_map_policy(policy, vote_wait_seconds=wait)
    result = service._planner.plan(_map_context(nodes=_MAP_NODES, other_vote=other_vote, is_multiplayer=multiplayer))
    if expected is None:
        assert result is None
    else:
        assert result is not None
        if other_vote is not None:
            assert result.kwargs["option_index"] == other_vote


@pytest.mark.parametrize("seconds,mod_seconds", [(0, 0), (7.5, 8), (60, 60)])
def test_seconds_synced_to_mod(seconds, mod_seconds):
    service, client = _service()
    service.set_map_policy("follow", vote_wait_seconds=seconds)
    result = asyncio.run(service.sync_map_policy_to_mod())
    assert result["applied"]
    assert client.config_writes == [{"coop_map_policy": "follow", "coop_map_vote_wait_seconds": mod_seconds}]


class MemoryConfig:
    def __init__(self):
        self.patches = []
        self.fail = False

    async def update(self, patch):
        if self.fail:
            raise RuntimeError("保存失败")
        self.patches.append(deepcopy(patch))
        return patch


class SettingsPlugin(STS2AutoplayPlugin):
    def __init__(self):
        self._service, self.client = _service()
        self._cfg = {"other_setting": True}
        self.memory_config = MemoryConfig()

    @property
    def config(self):
        return self.memory_config

    async def _run_entry(self, factory, **kwargs):
        return await factory()


def test_entry_saves_applies_and_reloads_zero():
    plugin = SettingsPlugin()
    result = asyncio.run(plugin.sts2_set_map_policy(policy="follow", vote_wait_seconds=0))
    patch = {"map_policy": "follow", "map_vote_wait_seconds": 0}
    assert plugin.memory_config.patches == [{"sts2": patch}]
    assert plugin._cfg == {"other_setting": True, **patch}
    assert result["saved"] and result["mod_sync"]["applied"]
    assert plugin._service._planner._map_vote_wait_seconds == 0
    restarted, _ = _service()
    restarted._cfg = plugin.memory_config.patches[0]["sts2"]
    restarted._configure_map_policy_from_config()
    assert restarted._planner._map_vote_wait_seconds == 0


def test_save_failure_does_not_change_live_behavior():
    plugin = SettingsPlugin()
    plugin.memory_config.fail = True
    with pytest.raises(RuntimeError, match="保存失败"):
        asyncio.run(plugin.sts2_set_map_policy(policy="follow", vote_wait_seconds=0))
    assert plugin._service._map_policy == "wait"
    assert plugin._service._map_vote_wait_seconds == 20
    assert plugin.client.config_writes == []


@pytest.mark.parametrize("policy,seconds", [("oops", 20), ("follow", -1), ("follow", float("nan"))])
def test_invalid_entry_does_not_save(policy, seconds):
    plugin = SettingsPlugin()
    with pytest.raises(ValueError):
        asyncio.run(plugin.sts2_set_map_policy(policy=policy, vote_wait_seconds=seconds))
    assert plugin.memory_config.patches == []
    assert plugin.client.config_writes == []


def test_offline_mod_does_not_prevent_saving():
    plugin = SettingsPlugin()
    plugin._service._client = None
    result = asyncio.run(plugin.sts2_set_map_policy(policy="follow", vote_wait_seconds=45))
    assert result["saved"] and not result["mod_sync"]["applied"]
    assert "尚未同步" in result["summary"]
    assert plugin._service._planner._map_vote_wait_seconds == 45


def test_switching_instance_syncs_wait_to_target():
    from plugin.plugins.sts2_autoplay.tests.test_instance_switch import FakeClient
    from plugin.plugins.sts2_autoplay.tests.test_instance_switch import _service as instance_service

    service = instance_service()
    service._catgirl_client = FakeClient()
    service.set_map_policy("follow", vote_wait_seconds=45)
    result = asyncio.run(service.switch_active_instance("catgirl"))
    assert result["switched"]
    assert {"coop_map_policy": "follow", "coop_map_vote_wait_seconds": 45} in service._catgirl_client.config_writes
    assert service._catgirl_client.config_writes[0] == {"coop_control_mode": "hold"}
