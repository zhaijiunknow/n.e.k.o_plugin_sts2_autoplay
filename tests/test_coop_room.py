from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.transport_client import STS2TransportError


class DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


# 真机主菜单：open_multiplayer_menu 与 start_coop_session 都在（外加普通主菜单项）。
MAIN_MENU_ACTIONS: set[str] = {
    "open_character_select",
    "open_timeline",
    "open_multiplayer_menu",
    "start_coop_session",
}
# 旧版 mod 的主菜单：没有 start_coop_session —— 用来验证报错可读。
STALE_MOD_MENU: set[str] = {"open_character_select", "open_timeline", "open_multiplayer_menu"}
# NMultiplayerSubmenu 打开后，第二步的正式动作才出现。
SUBMENU_OPEN: set[str] = {"start_multiplayer_host", "join_multiplayer_direct"}


class ScriptedClient:
    """按顺序吐出 (screen, 可用动作) 帧，并记录真正发出的动作（含 /config 写入）。

    帧只在**执行动作**时前进（/config 写入不动界面）；帧用完后一直重复最后一帧，
    用来模拟「动作发出去了但界面没变」。
    """

    def __init__(self, *frames: tuple[str, set[str]]) -> None:
        self._frames = list(frames)
        self._actions = 0
        self.calls: list[str] = []

    async def get_available_actions(self) -> dict[str, Any]:
        screen, names = self._frames[min(self._actions, len(self._frames) - 1)]
        return {"screen": screen, "actions": [{"type": name} for name in sorted(names)]}

    async def execute_action(self, action: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(action)
        self._actions += 1
        return {}

    async def set_config(self, **fields: Any) -> dict[str, Any]:
        # host 路径不该再自己写 coop_enabled（mod 的 start_coop_session 会落盘），记下来是为了让
        # "偷偷写配置"变成一次断言失败，而不是静默混过去。
        rendered = ", ".join(f"{key}={value}" for key, value in sorted(fields.items()))
        self.calls.append(f"set_config({rendered})")
        return {}


def build_service(*frames: tuple[str, set[str]]) -> tuple[STS2AutoplayService, ScriptedClient]:
    service = STS2AutoplayService(DummyLogger(), lambda payload: None)
    client = ScriptedClient(*frames)
    service._client = client  # type: ignore[assignment]

    async def fake_refresh_state(**kwargs: Any) -> dict[str, Any]:
        # 动作之后的状态：两种路径都不是「主菜单」了，交给调用方看的是这份。
        service._state.snapshot = {
            "screen": "CHARACTER_SELECT",
            "available_actions": [{"type": "embark"}, {"type": "select_character"}],
        }
        return {}

    service.refresh_state = fake_refresh_state  # type: ignore[method-assign]
    return service, client


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_host_delegates_to_mod_session_action() -> None:
    """host 只发一个 start_coop_session，编排（含拉起猫娘进程）归 mod。

    回归：这里曾经自己拼 open_multiplayer_menu + host_multiplayer_lobby（调试测试场景那套，
    跟在 open_multiplayer_menu 后面必然 409），后来又自己拼 open_multiplayer_menu +
    start_multiplayer_host —— 但那样拉不起猫娘进程，因为 LaunchCatgirlProcess 只暴露在
    mod 的 start_coop_session 后面。
    """
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))

    result = await service.open_coop_room(host=True)

    assert client.calls == ["start_coop_session"]
    assert result["status"] == "opened"
    assert result["action"] == "start_coop_session"
    assert result["host"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_host_leaves_coop_enabled_to_the_mod() -> None:
    """插件不再自己写 coop_enabled —— mod 的 start_coop_session 第一步就会落盘。"""
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))

    await service.open_coop_room(host=True)

    assert not [call for call in client.calls if call.startswith("set_config")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_host_reports_clear_error_on_stale_mod() -> None:
    """mod 没重启到含 start_coop_session 的版本时，报错要点名并给出可用动作。"""
    service, client = build_service(("MAIN_MENU", STALE_MOD_MENU))

    with pytest.raises(STS2TransportError) as excinfo:
        await service.open_coop_room(host=True)

    message = str(excinfo.value)
    assert "start_coop_session" in message
    assert "open_multiplayer_menu" in message
    assert client.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_client_uses_direct_connect() -> None:
    """加入方没有猫娘要拉，仍自己走 菜单 -> 直连。"""
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS), ("MAIN_MENU", SUBMENU_OPEN))

    result = await service.open_coop_room(host=False)

    assert client.calls == ["open_multiplayer_menu", "join_multiplayer_direct"]
    assert result["action"] == "join_multiplayer_direct"
    assert result["host"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_client_skips_menu_when_submenu_already_open() -> None:
    """上一轮留下的子菜单还在时，再发 open_multiplayer_menu 只会 409，应直接发第二步。"""
    service, client = build_service(("MAIN_MENU", SUBMENU_OPEN))

    await service.open_coop_room(host=False)

    assert client.calls == ["join_multiplayer_direct"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_client_closes_stale_main_menu_submenu_first() -> None:
    """主菜单上开着别的子菜单时 open_multiplayer_menu 不可用，先按 mod 的做法收干净。"""
    service, client = build_service(
        ("MAIN_MENU", {"close_main_menu_submenu", "open_timeline"}),
        ("MAIN_MENU", MAIN_MENU_ACTIONS),
        ("MAIN_MENU", SUBMENU_OPEN),
    )

    await service.open_coop_room(host=False)

    assert client.calls == [
        "close_main_menu_submenu",
        "open_multiplayer_menu",
        "join_multiplayer_direct",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_client_reports_screen_and_actions_when_menu_unavailable() -> None:
    """不在主菜单时给出带 screen / 可用动作的报错，而不是 mod 那句无法定位的 409。"""
    service, client = build_service(("COMBAT", {"play_card", "end_turn"}))

    with pytest.raises(STS2TransportError) as excinfo:
        await service.open_coop_room(host=False)

    message = str(excinfo.value)
    assert "COMBAT" in message
    assert "play_card" in message
    assert client.calls == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_client_errors_when_menu_does_not_stick() -> None:
    """open_multiplayer_menu 返回 200 不等于子菜单留在栈上：发第二步前必须校验。"""
    service, client = build_service(
        ("MAIN_MENU", MAIN_MENU_ACTIONS),
        ("MAIN_MENU", MAIN_MENU_ACTIONS),
    )

    with pytest.raises(STS2TransportError) as excinfo:
        await service.open_coop_room(host=False)

    assert "join_multiplayer_direct" in str(excinfo.value)
    assert client.calls == ["open_multiplayer_menu"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_never_sends_test_scene_lobby_actions() -> None:
    """两条路径都不许碰调试测试场景那套动作（那条路会崩掉第二个游戏实例）。"""
    service, client = build_service(
        ("MAIN_MENU", MAIN_MENU_ACTIONS),
        ("MAIN_MENU", MAIN_MENU_ACTIONS),
        ("MAIN_MENU", SUBMENU_OPEN),
    )

    await service.open_coop_room(host=True)
    await service.open_coop_room(host=False)

    assert "host_multiplayer_lobby" not in client.calls
    assert "join_multiplayer_lobby" not in client.calls
    assert "ready_multiplayer_lobby" not in client.calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_coop_room_result_reports_post_action_state() -> None:
    """返回值里的 screen / available_actions 取动作之后的状态，和调用方看到的一致。"""
    service, _ = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS), ("CHARACTER_SELECT", {"embark", "select_character"}))

    result = await service.open_coop_room(host=True)

    assert result["screen"] == "CHARACTER_SELECT"
    assert result["available_actions"] == ["embark", "select_character"]


@pytest.mark.asyncio
async def test_resume_coop_uses_only_saved_session_action():
    service, client = build_service(("MAIN_MENU", {"continue_coop_session", "start_coop_session"}))
    result = await service.open_coop_room(resume=True)
    assert client.calls == ["continue_coop_session"]
    assert result["resume"] is True
    assert service._state.autoplay_state != "running"


@pytest.mark.asyncio
async def test_resume_not_supported_never_falls_back_to_new_room():
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))
    with pytest.raises(STS2TransportError, match="continue_coop_session"):
        await service.open_coop_room(resume=True)
    assert client.calls == []
    assert not service._coop_opening


@pytest.mark.asyncio
async def test_coop_failure_does_not_adopt_or_open_new_room():
    service, client = build_service(("MAIN_MENU", {"continue_coop_session"}))
    async def fail(action, **kwargs):
        client.calls.append(action)
        raise STS2TransportError("save is incompatible")
    client.execute_action = fail
    with pytest.raises(STS2TransportError, match="save is incompatible"):
        await service.open_coop_room(resume=True)
    assert client.calls == ["continue_coop_session"]
    assert not service._coop_opening


@pytest.mark.asyncio
async def test_coop_does_not_send_host_action_to_active_catgirl():
    service, host = build_service(("MAIN_MENU", {"continue_coop_session"}))
    catgirl = ScriptedClient(("COMBAT", {"end_turn"}))
    service._host_client = host
    service._client = catgirl
    service._state.active_instance = service.INSTANCE_CATGIRL
    async def adopted(**kwargs):
        return {"adopted": True}
    service.adopt_coop_catgirl = adopted
    result = await service.open_coop_room(resume=True)
    assert host.calls == ["continue_coop_session"]
    assert catgirl.calls == []
    assert result["catgirl_adoption"]["adopted"]


@pytest.mark.asyncio
async def test_coop_adoption_failure_is_visible_not_success_claim():
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))
    result = await service.open_coop_room()
    assert not result["catgirl_adoption"]["adopted"]
    assert "未成功接管" in result["summary"]
    assert service._state.autoplay_state != "running"


@pytest.mark.asyncio
async def test_coop_rejects_running_autoplay_and_invalid_join_resume():
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))
    with pytest.raises(STS2TransportError, match="主机"):
        await service.open_coop_room(host=False, resume=True)
    service._state.autoplay_state = "running"
    with pytest.raises(STS2TransportError, match="暂停"):
        await service.open_coop_room()
    assert client.calls == []


@pytest.mark.asyncio
async def test_coop_duplicate_request_rejected_while_first_in_flight():
    import asyncio
    service, client = build_service(("MAIN_MENU", MAIN_MENU_ACTIONS))
    entered, release = asyncio.Event(), asyncio.Event()
    async def adopt(**kwargs):
        entered.set()
        await release.wait()
        return {"adopted": True}
    service.adopt_coop_catgirl = adopt
    task = asyncio.create_task(service.open_coop_room())
    await entered.wait()
    try:
        with pytest.raises(STS2TransportError, match="不要重复"):
            await service.open_coop_room()
        assert client.calls == ["start_coop_session"]
    finally:
        release.set()
        await task
    assert not service._coop_opening
