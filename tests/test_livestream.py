from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.livestream import DELAYS, SCENES, LivestreamController, settings
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from test_service_runtime import DummyLogger


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def controller(profile="casual"):
    clock = Clock()
    live = LivestreamController(clock=clock)
    live.configure(settings(True, profile, 1))
    return live, clock


def snapshot(screen="REST"):
    return {"screen": screen, "run_id": "run-1", "floor": 3, "act": 1, "in_combat": False,
            "classification": {"state_name": screen.lower()},
            "raw_state": {"state_version": 1, "run": {"current_hp": 12, "max_hp": 80, "gold": 100},
                          "rest": {"options": [{"index": 0, "title": "休息"}, {"index": 1, "title": "锻造"}]}},
            "available_actions": [{"type": "choose_rest_option"}],
            "agent_operation": {"action_type": "choose_rest_option", "kwargs": {"option_index": 0},
                                "reason": "rest_heal"}}


@pytest.mark.parametrize("screen,scene", SCENES.items())
def test_windows_cover_all_noncombat_screens(screen, scene):
    live, clock = controller()
    snap, speech = snapshot(screen.upper()), []
    assert live.gate(snap, snap["agent_operation"], speak=speech.append)
    clock.now += DELAYS[scene] - 0.1
    assert live.gate(snap, snap["agent_operation"], speak=speech.append)
    clock.now += 0.1
    assert not live.gate(snap, snap["agent_operation"], speak=speech.append)
    assert len(speech) == 2


def test_polling_never_extends_deadline_or_repeats_speech():
    live, clock = controller()
    snap, speech = snapshot(), []
    assert live.gate(snap, snap["agent_operation"], speak=speech.append)
    for n in range(19):
        snap["raw_state"]["state_version"] += 1
        snap["polled_at"] = str(n)
        # Planning text can vary without changing the available decision.
        snap["agent_operation"]["reason"] = f"reason {n}"
        clock.now += 1
        assert live.gate(snap, snap["agent_operation"], speak=speech.append)
    clock.now += 1
    assert not live.gate(snap, snap["agent_operation"], speak=speech.append)
    assert len(speech) == 2
    assert "get_recent_live_chat" in speech[-1]
    assert "不可信数据" in speech[-1]


@pytest.mark.parametrize("change", ["screen", "run", "choices", "gold"])
def test_new_decision_invalidates_window(change):
    live, clock = controller()
    snap, speech = snapshot(), []
    live.gate(snap, snap["agent_operation"], speak=speech.append)
    clock.now += 40
    if change == "screen":
        snap["screen"] = "CARD_SELECTION"
    elif change == "run":
        snap["run_id"] = "run-2"
    elif change == "gold":
        snap["raw_state"]["run"]["gold"] -= 5
    else:
        snap["raw_state"]["rest"]["options"].pop()
    assert live.gate(snap, snap["agent_operation"], speak=speech.append)
    assert len(speech) == 2


@pytest.mark.parametrize("case", ["disabled", "combat", "raw_combat", "classified_combat", "force", "menu"])
def test_bypass_never_speaks_or_holds(case):
    live, _ = controller()
    snap, speech = snapshot(), []
    if case == "disabled":
        live.configure(settings(False, "casual", 1))
    elif case == "combat":
        snap["in_combat"] = True
    elif case == "raw_combat":
        snap["raw_state"]["in_combat"] = True
    elif case == "classified_combat":
        snap["classification"]["in_combat"] = True
    elif case == "menu":
        snap["screen"] = "MAIN_MENU"
    assert not live.gate(snap, snap["agent_operation"], speak=speech.append, force=case == "force")
    assert not speech and not live.key


def test_rest_voice_depends_on_observed_hp():
    live, _ = controller()
    snap = snapshot()
    assert "血量偏低" in live.prompt(snap, snap["agent_operation"], audience=False)
    snap["raw_state"]["run"]["current_hp"] = 70
    assert "状态还不错" in live.prompt(snap, snap["agent_operation"], audience=False)
    snap["raw_state"]["run"].pop("max_hp")
    assert "血量未知" in live.prompt(snap, snap["agent_operation"], audience=False)


@pytest.mark.parametrize("scale", [0, -1, 3.1, float("nan"), float("inf")])
def test_bad_wait_scale_rejected(scale):
    with pytest.raises(ValueError):
        settings(True, "casual", scale)


def test_profile_sampling_is_stable_bounded_and_distinct():
    choices = {}
    ranked = [(0, 50.0), (1, 44.0), (2, 29.0), (3, -2.0), (4, float("nan"))]
    for profile in ("focused", "casual", "chaotic"):
        live, _ = controller(profile)
        choices[profile] = [live.pick_ranked(ranked, key=i) for i in range(300)]
        assert choices[profile] == [live.pick_ranked(ranked, key=i) for i in range(300)]
    assert set(choices["focused"]) == {0}
    assert set(choices["casual"]) == {0, 1}
    assert set(choices["chaotic"]) == {0, 1, 2}
    assert choices["chaotic"].count(0) < choices["casual"].count(0)


def scored(kind="CARD_REWARD"):
    return {"choice": 0, "kind": kind, "forced": False, "decision_id": "decision",
            "probs": [0.45, 0.4, 0.15], "candidates": [
                {"action": "choose_reward_card", "executable": True},
                {"action": "choose_reward_card", "executable": True},
                {"action": "choose_reward_card", "executable": False}]}


@pytest.mark.parametrize("kind", ["COMBAT", "EVENT", "REST", "MAP", "CARD_SELECT"])
def test_policy_does_not_randomize_other_decisions(kind):
    live, _ = controller("chaotic")
    assert live.policy_choice(scored(kind)) == 0


def test_policy_respects_forced_skip_and_executable_candidates():
    live, _ = controller("chaotic")
    decision = scored()
    for i in range(50):
        decision["decision_id"] = str(i)
        assert live.policy_choice(decision) in {0, 1}
    decision["forced"] = True
    assert live.policy_choice(decision) == 0
    decision["forced"] = False
    decision["candidates"][0]["action"] = "policy_reward_alternative"
    assert live.policy_choice(decision) == 0


def test_heuristic_reward_path_uses_live_profile_but_respects_guidance(monkeypatch):
    from test_reward_card_quality import DEFEND, STRIKE, _ctx

    planner = STS2HeuristicPlanner()
    live, _ = controller("chaotic")
    planner.configure_livestream(live)
    calls = []
    monkeypatch.setattr(live, "pick_ranked", lambda ranked, **kw: calls.append(ranked) or 2)
    ctx = _ctx([DEFEND, STRIKE], [{"type": "choose_reward_card"}])
    monkeypatch.setattr(planner, "_score_reward_card", lambda *args: 50)
    assert planner.plan(ctx).kwargs["option_index"] == 2
    assert calls
    calls.clear()
    ctx["summary_context"]["decision_payload"] = {"instructions": [{"source": "neko_guidance", "content": "先防"}]}
    planner.plan(ctx)
    assert not calls
    # Focused must preserve the original raw-score ordering, not re-sort rounded telemetry.
    ctx["summary_context"].pop("decision_payload")
    live.profile = "focused"
    planner.plan(ctx)
    assert not calls


def service_fixture(monkeypatch):
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    live, clock = controller()
    service._livestream = live
    service._state.autoplay_state = "running"
    service._state.snapshot = snapshot()
    refresh = AsyncMock()
    execute = AsyncMock(return_value={"status": "ok", "result": {"status": "completed"}})
    monkeypatch.setattr(service, "refresh_state", refresh)
    monkeypatch.setattr(service, "execute_operation", execute)
    monkeypatch.setattr(service, "_combat_potion_override", lambda s: None)
    monkeypatch.setattr(service, "_crystal_sphere_override", lambda s: None)
    monkeypatch.setattr(service, "_advance_custom_event_cursor", lambda s, op: op)
    monkeypatch.setattr(service, "_note_step_progress", lambda *a: None)
    speech = []
    monkeypatch.setattr(service, "_push_proactive_to_host", lambda text, **kw: speech.append(text))
    return service, clock, refresh, execute, speech


@pytest.mark.parametrize("action", ["choose_rest_option", "policy_act"])
def test_service_holds_both_policy_and_heuristic_and_uses_fresh_operation(monkeypatch, action):
    service, clock, refresh, execute, speech = service_fixture(monkeypatch)
    service._state.snapshot["agent_operation"] = {
        "action_type": action, "kwargs": {"option_index": 0, "command": "decision"}}
    assert asyncio.run(service.run_autoplay_step())["held"]
    assert not execute.called and service._state.step_count == 0
    clock.now += 10
    assert asyncio.run(service.run_autoplay_step())["held"]
    clock.now += 11
    fresh = {"action_type": action, "kwargs": {"option_index": 1, "command": "decision"}}
    service._state.snapshot["agent_operation"] = fresh
    assert asyncio.run(service.run_autoplay_step())["status"] == "ok"
    execute.assert_awaited_once_with(fresh)
    assert refresh.await_count == 3 and len(speech) == 2
    assert not service._livestream.key


def test_service_force_and_pause_cancel_wait(monkeypatch):
    service, _, _, execute, _ = service_fixture(monkeypatch)
    asyncio.run(service.run_autoplay_step())
    assert asyncio.run(service.run_autoplay_step(force=True))["status"] == "ok"
    assert execute.await_count == 1 and not service._livestream.key
    # Avoid background scheduling in this unit test.
    monkeypatch.setattr(service, "_sync_background_polling", lambda: None)
    monkeypatch.setattr(service, "_apply_mode_danmaku_policy", lambda: None)
    asyncio.run(service.run_autoplay_step())
    service.pause_autoplay()
    assert not service._livestream.key


def test_service_refresh_to_combat_cancels_window(monkeypatch):
    service, _, refresh, execute, _ = service_fixture(monkeypatch)
    asyncio.run(service.run_autoplay_step())

    async def enter_combat(**kw):
        service._state.snapshot = snapshot("COMBAT")
        service._state.snapshot["in_combat"] = True
        service._state.snapshot["agent_operation"] = {"action_type": "end_turn", "kwargs": {}}

    refresh.side_effect = enter_combat
    asyncio.run(service.run_autoplay_step())
    assert execute.await_count == 1 and not service._livestream.key


def test_mod_sync_requires_acknowledgement(monkeypatch):
    service, _, _, _, _ = service_fixture(monkeypatch)
    client = AsyncMock()
    client.set_config.return_value = {"livestream_enabled": True}
    monkeypatch.setattr(service, "_require_client", lambda: client)
    assert asyncio.run(service.sync_livestream_to_mod())["applied"]
    client.set_config.return_value = {"status": "updated"}
    assert not asyncio.run(service.sync_livestream_to_mod())["applied"]


def test_speech_does_not_mutate_game_data():
    live, _ = controller()
    snap = snapshot()
    before = deepcopy(snap)
    live.gate(snap, snap["agent_operation"], speak=lambda text: None)
    assert snap == before


@pytest.mark.parametrize("policy", [False, True])
@pytest.mark.parametrize("action", ["claim_reward", "resolve_rewards", "collect_rewards_and_proceed",
                                   "open_shop_inventory", "proceed", "confirm_selection",
                                   "confirm_bundle", "close_cards_view"])
def test_ui_plumbing_does_not_hold(policy, action):
    live, _ = controller()
    snap, speech = snapshot(), []
    op = {"action_type": "policy_act", "policy": {"action": action}} if policy else {"action_type": action}
    assert not live.gate(snap, op, speak=speech.append)
    assert not speech and not live.key


def test_service_new_policy_decision_restarts_window(monkeypatch):
    service, clock, _, execute, speech = service_fixture(monkeypatch)
    operation = {"action_type": "policy_act", "kwargs": {"option_index": 0, "command": "first"}}
    service._state.snapshot["agent_operation"] = operation
    assert asyncio.run(service.run_autoplay_step())["held"]
    clock.now += 25
    operation["kwargs"]["command"] = "second"
    assert asyncio.run(service.run_autoplay_step())["held"]
    assert not execute.called and len(speech) == 2
    clock.now += 21
    assert asyncio.run(service.run_autoplay_step())["status"] == "ok"
    execute.assert_awaited_once_with(operation)


def shop_fixture(monkeypatch, profile="chaotic"):
    from test_shop_scoring import _context, _shop_card, _starter_deck

    planner = STS2HeuristicPlanner()
    live, _ = controller(profile)
    planner.configure_livestream(live)
    ctx = _context(shop={"cards": [_shop_card(i, f"Card{i}") for i in range(3)]},
                   run={"gold": 300, "deck": _starter_deck()})
    monkeypatch.setattr(planner, "_shop_cost_penalty", lambda *args: 0)
    monkeypatch.setattr(planner, "_score_shop_card", lambda card, *args, **kw: ([70, 65, 10][card["index"]], "test"))
    monkeypatch.setattr(planner, "_entry_threshold", lambda *args, **kw: 40)
    calls = []
    monkeypatch.setattr(live, "pick_ranked", lambda ranked, **kw: calls.append(ranked) or ranked[-1][0])
    return planner, ctx, calls


def test_shop_explores_only_above_threshold(monkeypatch):
    planner, ctx, calls = shop_fixture(monkeypatch)
    operation = planner.plan(ctx)
    assert operation.action_type == "buy_card" and operation.kwargs["option_index"] == 1
    assert calls == [[(0, 70), (1, 65)]]


@pytest.mark.parametrize("case", ["focused", "disabled", "skip", "records", "guidance"])
def test_shop_keeps_original_decision_when_not_allowed_to_explore(monkeypatch, case):
    planner, ctx, calls = shop_fixture(monkeypatch, "focused" if case == "focused" else "chaotic")
    if case == "disabled":
        planner._livestream.enabled = False
    elif case == "skip":
        monkeypatch.setattr(planner, "_entry_threshold", lambda *args, **kw: 80)
    elif case == "records":
        ctx["strategy_context"]["preferences"]["records"] = [{"content": "保血"}]
    elif case == "guidance":
        ctx["summary_context"]["decision_payload"] = {"instructions": [{"source": "neko_guidance", "content": "先防"}]}
    operation = planner.plan(ctx)
    assert not calls
    assert operation.action_type != "buy_card" if case == "skip" else operation.kwargs["option_index"] == 0


def test_shop_does_not_promote_other_candidate_when_original_best_fails_own_threshold(monkeypatch):
    planner, ctx, calls = shop_fixture(monkeypatch)
    monkeypatch.setattr(planner, "_entry_threshold", lambda *args, entry, **kw: 80 if entry["option_index"] == 0 else 40)
    operation = planner.plan(ctx)
    assert not calls and operation.action_type != "buy_card"


def test_shop_excludes_relics_blocked_by_budget(monkeypatch):
    from test_shop_scoring import _shop_relic

    planner, ctx, calls = shop_fixture(monkeypatch)
    shop = ctx["snapshot"]["raw_state"]["shop"]
    shop["cards"] = []
    shop["relics"] = [_shop_relic(i, f"Relic{i}", price=price) for i, price in enumerate([100, 120, 290])]
    ctx["summary_context"]["payload"]["shop_cards"] = []
    ctx["summary_context"]["payload"]["shop_relics"] = shop["relics"]
    monkeypatch.setattr(planner, "_score_shop_relic", lambda relic, *args, **kw: ([70, 65, 99][relic["index"]], "test"))
    operation = planner.plan(ctx)
    assert operation.action_type == "buy_relic" and operation.kwargs["option_index"] == 1
    assert calls == [[(0, 70), (1, 65)]]


@pytest.mark.parametrize("failure", [None, "invalid", "save"])
def test_entry_validates_and_persists_before_runtime_change(failure):
    from types import SimpleNamespace

    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    calls = []

    async def save(patch):
        calls.append(("save", patch))
        if failure == "save":
            raise OSError("disk unavailable")

    async def apply(patch):
        calls.append(("apply", patch))
        return {"summary": "ok", "mod_sync": {"applied": True}}

    async def run_entry(fn):
        return await fn()

    # Exercise the real decorated entry with fake persistence, not its implementation copied here.
    plugin = SimpleNamespace(config=SimpleNamespace(update=save), _cfg={},
                             _service=SimpleNamespace(set_livestream=apply), _run_entry=run_entry)
    if failure:
        with pytest.raises(ValueError if failure == "invalid" else OSError):
            asyncio.run(STS2AutoplayPlugin.sts2_set_livestream(plugin, enabled=True,
                        profile="invalid" if failure == "invalid" else "casual"))
        assert [c[0] for c in calls] == ([] if failure == "invalid" else ["save"])
        assert plugin._cfg == {}
    else:
        result = asyncio.run(STS2AutoplayPlugin.sts2_set_livestream(plugin, enabled=True))
        assert result["saved"]
        assert [c[0] for c in calls] == ["save", "apply"]
        assert calls[0][1] == {"sts2": calls[1][1]}


def test_policy_advisor_emits_selected_live_choice_and_original_telemetry(monkeypatch):
    from plugin.plugins.sts2_autoplay.policy_client import PolicyAdvisor

    live, _ = controller("chaotic")
    advisor = PolicyAdvisor(DummyLogger())
    advisor.livestream = live
    decision = {**scored(), "probability": 0.45, "value": 0.1, "multiplayer": False, "character": "IRONCLAD"}
    monkeypatch.setattr(advisor, "_score", AsyncMock(return_value=decision))
    monkeypatch.setattr(live, "pick_ranked", lambda *args, **kw: 1)
    operation = asyncio.run(advisor.decide(None, character="IRONCLAD"))
    assert operation["kwargs"] == {"option_index": 1, "command": "decision"}
    assert operation["confidence"] == 0.4
    assert advisor.last_decision["network_choice"] == 0
    assert advisor.last_decision["choice"] == 1
    assert advisor.last_decision["livestream_profile"] == "chaotic"
    assert decision["choice"] == 0


@pytest.mark.parametrize("state", ["paused", "idle"])
def test_pause_or_stop_while_refreshing_cannot_reopen_live_window(monkeypatch, state):
    service, _, refresh, execute, speech = service_fixture(monkeypatch)

    async def stop_during_refresh(**kwargs):
        service._state.autoplay_state = state

    refresh.side_effect = stop_during_refresh
    assert asyncio.run(service.run_autoplay_step())["held"]
    assert not execute.called and not speech and not service._livestream.key
