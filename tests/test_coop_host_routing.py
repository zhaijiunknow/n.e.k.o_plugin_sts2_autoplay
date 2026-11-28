"""Room buttons always target the host even after adoption or a previous run."""
from unittest.mock import AsyncMock, Mock

import pytest
from plugin.plugins.sts2_autoplay.tests.test_coop_room import ScriptedClient, build_service
from plugin.plugins.sts2_autoplay.transport_client import STS2TransportError


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("rebuild_host", [False, True])
async def test_room_probe_action_and_result_stay_on_host(resume, rebuild_host):
    action = "continue_coop_session" if resume else "start_coop_session"
    service, host = build_service(
        ("MAIN_MENU", {action}), ("CHARACTER_SELECT", {"embark", "select_character"}),
    )
    catgirl = ScriptedClient(("GAME_OVER", {"return_to_main_menu"}))
    catgirl.get_available_actions = AsyncMock(side_effect=AssertionError("room button probed catgirl"))
    service._client = service._catgirl_client = catgirl
    service._host_base_url = "http://127.0.0.1:18080"
    service._host_client = None if rebuild_host else host
    service._build_client = Mock(return_value=host)
    service._state.active_instance = service.INSTANCE_CATGIRL
    snapshot = {"screen": "GAME_OVER", "run_id": "previous-catgirl-run"}
    service._state.snapshot = snapshot
    service.refresh_state = AsyncMock(side_effect=AssertionError("room result refreshed active gameplay"))
    service.adopt_coop_catgirl = AsyncMock(return_value={"adopted": True})

    result = await service.open_coop_room(resume=resume)

    assert host.calls == [action]
    assert catgirl.calls == []
    catgirl.get_available_actions.assert_not_awaited()
    service.refresh_state.assert_not_awaited()
    assert service._client is catgirl
    assert service._state.snapshot is snapshot
    assert service._state.active_instance == service.INSTANCE_CATGIRL
    assert result["room_instance"] == "host"
    assert result["screen"] == "CHARACTER_SELECT"
    assert result["available_actions"] == ["embark", "select_character"]
    if rebuild_host:
        service._build_client.assert_called_once_with("http://127.0.0.1:18080")
    else:
        service._build_client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_second_room_works_without_closing_catgirl(resume):
    action = "continue_coop_session" if resume else "start_coop_session"
    service, host = build_service(("MAIN_MENU", {action}))
    service._host_client = host
    catgirl = ScriptedClient(("GAME_OVER", {"return_to_main_menu"}))

    async def adopt(**kwargs):
        service._client = service._catgirl_client = catgirl
        service._state.active_instance = service.INSTANCE_CATGIRL
        return {"adopted": True}

    service.adopt_coop_catgirl = adopt
    for _ in range(2):
        result = await service.open_coop_room(resume=resume)
        assert result["catgirl_adoption"]["adopted"]
    assert host.calls == [action, action]
    assert catgirl.calls == []
    assert service._client is catgirl


@pytest.mark.asyncio
async def test_host_failure_does_not_fall_back_to_catgirl_or_adopt():
    service, host = build_service(("MAIN_MENU", {"start_coop_session"}))
    service._host_client = host
    host.get_available_actions = AsyncMock(side_effect=STS2TransportError("host offline"))
    catgirl = ScriptedClient(("MAIN_MENU", {"start_coop_session"}))
    service._client = catgirl
    service._state.active_instance = service.INSTANCE_CATGIRL
    service.adopt_coop_catgirl = AsyncMock()
    with pytest.raises(STS2TransportError, match="host offline"):
        await service.open_coop_room()
    assert catgirl.calls == []
    service.adopt_coop_catgirl.assert_not_awaited()
    assert not service._coop_opening


@pytest.mark.asyncio
async def test_host_button_does_not_require_an_active_gameplay_client():
    service, host = build_service(("MAIN_MENU", {"start_coop_session"}))
    service._host_client = host
    service._client = None
    service.adopt_coop_catgirl = AsyncMock(return_value={"adopted": True})
    assert (await service.open_coop_room())["room_instance"] == "host"
    assert host.calls == ["start_coop_session"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start_coop_session", "continue_coop_session", "end_turn"])
async def test_only_room_lifecycle_gets_longer_read_timeout(action):
    from plugin.plugins.sts2_autoplay.transport_client import STS2TransportClient

    client = STS2TransportClient("http://127.0.0.1:18080", request_timeout=15)
    client._request = AsyncMock(return_value={})
    await client.execute_action(action)
    kwargs = client._request.await_args.kwargs
    assert kwargs.get("read_timeout") == (None if action == "end_turn" else 60.0)
    assert kwargs["json"]["action"] == action
    assert "read_timeout" not in kwargs["json"]
