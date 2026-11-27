"""Current engine must follow live tick routing, not policy availability/history."""
import asyncio

import pytest
from plugin.plugins.sts2_autoplay.action_engine import STS2ActionEngine
from plugin.plugins.sts2_autoplay.action_registry import STS2ActionRegistry
from plugin.plugins.sts2_autoplay.planner_interface import PlannedOperation
from test_d10_main_menu_recovery import _make_runner
from test_policy_client import HEALTH, _Advisor, _Assets, _decision, _ModClient, _Planner, _scored, _TickClient


def runner_for(advisor=None, client=None):
    runner, state, *_ = _make_runner(client or _TickClient(_decision()))
    service = runner._service
    service._policy_advisor = advisor or _Advisor(health=HEALTH, scored=_scored(0))
    service._planner = _Planner()
    service._action_engine = STS2ActionEngine()
    service._action_registry = STS2ActionRegistry()
    service._map_policy = "auto"
    service._mode_controller = type("MC", (), {"describe": lambda self, mode: {"allows_planner": True}})()
    state.autoplay_state = "running"
    return runner


def test_network_result_and_status_are_consistent():
    runner = runner_for()
    snapshot = asyncio.run(runner.tick())["snapshot"]
    status = runner.decision_engine_status()
    assert status == snapshot["decision_engine"]
    assert status["engine"] == "policy"
    assert status["character"] == "IRONCLAD"
    assert status["updated_at"] > 0
    status["engine"] = "wrong"
    assert runner.decision_engine_status()["engine"] == "policy"


@pytest.mark.parametrize("field,value", [("autoplay_state", "paused"), ("autoplay_state", "idle"),
    ("autoplay_state", "stopped"), ("control_mode", "standby"), ("transport_state", "error")])
def test_inactive_or_disconnected_cannot_claim_old_network(field, value):
    runner = runner_for()
    asyncio.run(runner.tick())
    setattr(runner._service._state, field, value)
    assert runner.decision_engine_status()["engine"] == "unknown"


def test_config_change_and_restart_require_a_new_decision():
    runner = runner_for()
    assert runner.decision_engine_status()["engine"] == "unknown"
    asyncio.run(runner.tick())
    runner._service._policy_advisor.enabled = False
    assert runner.decision_engine_status()["engine"] == "unknown"
    runner._service._policy_advisor.enabled = True
    runner._decision_engine_generation += 1
    assert runner.decision_engine_status()["engine"] == "unknown"
    asyncio.run(runner.tick())
    assert runner.decision_engine_status()["engine"] == "policy"


def test_forced_option_is_network_route_without_claiming_inference():
    runner = runner_for(client=_TickClient(_decision(forced_index=0)))
    asyncio.run(runner.tick())
    status = runner.decision_engine_status()
    assert status["engine"] == "policy" and status["phase"] == "forced"
    assert "无需模型推理" in status["detail"]
    assert not runner._service._policy_advisor.infer_calls


@pytest.mark.parametrize("advisor,reason", [
    (_Advisor(health=HEALTH, enabled=False), "策略网络未启用"),
    (_Advisor(health=HEALTH, assets=_Assets(runtime=False)), "运行环境"),
    (_Advisor(health=HEALTH, assets=_Assets(models=("SILENT",))), "没有下载 IRONCLAD 的模型"),
    (_Advisor(health=None), "推理"),
])
def test_actual_fallback_beats_old_success(advisor, reason):
    advisor.last_decision = {"character": "IRONCLAD", "label": "old network decision"}
    advisor.decisions = 999
    runner = runner_for(advisor)
    asyncio.run(runner.tick())
    status = runner.decision_engine_status()
    assert status["engine"] == "solver"
    assert reason in status["detail"]


def test_pending_solver_is_visible_before_tick_finishes():
    class Client(_TickClient):
        async def get_combat_plan(self):
            status = runner.decision_engine_status()
            assert status["engine"] == "solver" and status["phase"] == "planning"
            return await super().get_combat_plan()
    runner = runner_for(_Advisor(health=HEALTH, enabled=False), Client(_decision()))
    asyncio.run(runner.tick())


def test_explicit_heuristic_action_is_not_called_solver_search():
    runner = runner_for(_Advisor(health=HEALTH, enabled=False))
    runner._service._planner.plan = lambda context: PlannedOperation(
        action_type="end_turn", kwargs={}, confidence=1, source="heuristic", reason="fallback")
    asyncio.run(runner.tick())
    assert runner.decision_engine_status()["engine"] == "heuristic"


def test_review_scoring_does_not_change_current_brain_or_fallback_reason():
    advisor = _Advisor(health=HEALTH, scored=_scored(0))
    runner = runner_for(advisor, _TickClient(_decision(available=False, reason="no adapter")))
    asyncio.run(runner.tick())
    before = runner.decision_engine_status()
    asyncio.run(advisor.recommend(_ModClient(_decision()), character="IRONCLAD"))
    assert runner.decision_engine_status() == before
    assert "no adapter" in advisor.last_fallback_reason
    asyncio.run(advisor.decide(_ModClient(_decision()), character="IRONCLAD"))
    assert advisor.last_fallback_reason == ""


def test_failed_state_read_does_not_keep_previous_network():
    runner = runner_for()
    asyncio.run(runner.tick())
    async def broken():
        raise RuntimeError("offline")
    runner._service._require_client().get_available_actions = broken
    with pytest.raises(RuntimeError):
        asyncio.run(runner.tick())
    assert runner.decision_engine_status()["engine"] == "unknown"


def test_overlapping_older_tick_cannot_overwrite_newer_routing():
    runner = runner_for()

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        operation = await runner._service._policy_advisor.decide(_ModClient(_decision()), character="IRONCLAD")
        calls = 0

        async def policy_operation(client, mode, snapshot):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                return operation
            return None

        runner._policy_operation = policy_operation
        older = asyncio.create_task(runner.tick())
        await entered.wait()
        newer = await runner.tick()
        assert newer["snapshot"]["decision_engine"]["engine"] == "solver"
        release.set()
        old_snapshot = (await older)["snapshot"]
        assert old_snapshot["decision_engine"]["engine"] == "policy"
        assert runner.decision_engine_status()["engine"] == "solver"

    asyncio.run(scenario())


def test_public_status_exposes_current_engine(monkeypatch):
    from plugin.plugins.sts2_autoplay import service as service_module
    from test_autoplay_policy_fixes import DummyLogger

    # Never read the player's potentially large real history while constructing a unit-test service.
    monkeypatch.setattr(service_module, "_default_memory_path", lambda: None)
    monkeypatch.setattr(service_module, "_default_encounter_db_path", lambda: None)
    service = service_module.STS2AutoplayService(DummyLogger(), lambda packet: None, lambda **kwargs: None)
    service._state.transport_state = "connected"
    service._state.autoplay_state = "running"
    service._loop_runner._set_decision_engine("policy", "ready", "模型已完成推理", {"character": "IRONCLAD"}, tick_id=0)
    status = asyncio.run(service.get_status())
    assert status["decision_engine"]["engine"] == "policy"
    assert status["decision_engine"]["character"] == "IRONCLAD"
    service._state.autoplay_state = "paused"
    assert asyncio.run(service.get_status())["decision_engine"]["engine"] == "unknown"
