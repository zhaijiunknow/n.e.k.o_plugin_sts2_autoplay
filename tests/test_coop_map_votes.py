"""Co-op map votes are persistent; waiting for teammates is not a failed action."""
from copy import deepcopy

import pytest
from plugin.plugins.sts2_autoplay import planner_map
from plugin.plugins.sts2_autoplay import service as service_module
from plugin.plugins.sts2_autoplay.tests.test_autoplay_policy_fixes import _MAP_NODES, _map_context, _service


def context_with_vote(local=None, other=None):
    context = deepcopy(_map_context(nodes=_MAP_NODES, other_vote=other, is_multiplayer=True))
    payload = context["summary_context"]["payload"]
    payload["local_vote"] = local
    context["snapshot"]["summary_context"] = context["summary_context"]
    return context


@pytest.mark.parametrize("policy,other", [("follow", 1), ("follow", None), ("auto", None), ("auto", 0)])
@pytest.mark.parametrize("vote_source", ["coord", "node_flag"])
def test_does_not_resubmit_existing_vote(policy, other, vote_source):
    service, _ = _service()
    service.set_map_policy(policy, vote_wait_seconds=0)
    context = context_with_vote(local={"row": 1, "col": 1}, other=other)
    if vote_source == "node_flag":
        context["summary_context"]["payload"]["local_vote"] = None
        context["summary_context"]["payload"]["available_nodes"][1]["has_local_vote"] = True
    for _ in range(12):
        assert service._planner.plan(context) is None
    assert "已投票" in service._map_wait_reason(context["snapshot"])


def test_follows_changed_human_vote_once_and_uses_option_index():
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=20)
    context = context_with_vote(local={"row": 1, "col": 1}, other=0)
    context["summary_context"]["payload"]["available_nodes"][0]["index"] = 7
    operation = service._planner.plan(context)
    assert operation.kwargs == {"option_index": 7}
    assert operation.reason == "map_follow_player_vote"
    assert service._map_wait_reason(context["snapshot"]) == ""
    context["summary_context"]["payload"]["local_vote"] = {"row": 1, "col": 0}
    assert service._planner.plan(context) is None


@pytest.mark.parametrize("local", [{"row": 0, "col": 0}, {}, None])
def test_stale_or_missing_vote_does_not_block_new_map(local):
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=0)
    assert service._planner.plan(context_with_vote(local=local)) is not None


def test_solo_is_not_held_by_vote_data():
    service, _ = _service()
    service.set_map_policy("auto")
    context = context_with_vote(local={"row": 1, "col": 1})
    context["summary_context"]["payload"]["is_multiplayer"] = False
    assert service._planner.plan(context) is not None
    assert service._map_wait_reason(context["snapshot"]) == ""


@pytest.mark.parametrize("policy,local,other", [
    ("wait", None, 1), ("follow", {"row": 1, "col": 1}, 1),
    ("follow", {"row": 1, "col": 1}, None), ("follow", None, None),
])
def test_expected_wait_does_not_raise_stuck_alert(monkeypatch, policy, local, other):
    monkeypatch.setattr(planner_map, "monotonic", lambda: 100.0)
    now = [1000.0]
    monkeypatch.setattr(service_module, "time", lambda: now[0])
    service, _ = _service()
    service.set_map_policy(policy, vote_wait_seconds=120)
    service._state.transport_state = "connected"
    context = context_with_vote(local, other)
    assert service._planner.plan(context) is None
    snapshot = context["snapshot"]
    service._state.snapshot = snapshot
    service._state.stuck_reason = "old misleading report"
    service._state.stuck_reported = True
    service._emit_stuck_report = lambda **kwargs: pytest.fail("Intentional waiting reported as stuck")
    service._note_no_operation(snapshot)
    now[0] += 300
    service._note_no_operation(snapshot)
    assert service._state.stuck_reason == ""
    assert not service._state.stuck_reported
    assert service._status_payload()["autoplay"]["waiting_reason"]


def test_reading_wait_status_does_not_start_timer_and_expired_wait_is_not_hidden(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(planner_map, "monotonic", lambda: now[0])
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=20)
    context = context_with_vote()
    snapshot = context["snapshot"]
    assert service._map_wait_reason(snapshot) == ""
    assert service._planner._map_wait_started_at == 0
    assert service._planner.plan(context) is None
    assert "等待队友先投票" in service._map_wait_reason(snapshot)
    now[0] += 20
    assert service._map_wait_reason(snapshot) == ""
    assert service._planner.plan(context) is not None


def test_map_without_available_nodes_still_reports_real_stall(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(service_module, "time", lambda: now[0])
    service, _ = _service()
    service.set_map_policy("wait")
    service._state.transport_state = "connected"
    context = context_with_vote()
    context["summary_context"]["payload"].update(available_nodes=[], travelable_nodes=[])
    snapshot = context["snapshot"]
    reports = []
    service._emit_stuck_report = lambda **kwargs: reports.append(kwargs)
    service._note_no_operation(snapshot)
    now[0] += service.NO_OPERATION_STUCK_SECONDS + 1
    service._note_no_operation(snapshot)
    assert len(reports) == 1
    assert "没有可执行动作" in reports[0]["reason"]


def test_wait_reason_reaches_static_control_snapshot(tmp_path):
    import json

    service, _ = _service()
    service.set_map_policy("follow")
    service._state.snapshot = context_with_vote(local={"row": 1, "col": 1}, other=1)["snapshot"]
    service._control_status_path = tmp_path / "control_status.json"
    service._publish_control_status()
    data = json.loads(service._control_status_path.read_text(encoding="utf-8"))
    assert "已投票" in data["autoplay"]["waiting_reason"]


def test_timeout_fallback_votes_only_once_then_can_follow_later_human_vote(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(planner_map, "monotonic", lambda: now[0])
    service, _ = _service()
    service.set_map_policy("follow", vote_wait_seconds=20)
    context = context_with_vote()
    assert service._planner.plan(context) is None
    now[0] += 20
    operation = service._planner.plan(context)
    own = operation.kwargs["option_index"]
    payload = context["summary_context"]["payload"]
    payload["local_vote"] = {"row": 1, "col": own}
    now[0] += 200
    assert service._planner.plan(context) is None
    payload["other_player_vote_index"] = 1 - own
    assert service._planner.plan(context).kwargs["option_index"] == 1 - own
