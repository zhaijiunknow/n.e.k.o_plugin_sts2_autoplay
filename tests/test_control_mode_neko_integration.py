# -*- coding: utf-8 -*-
"""控制档位（program / advise / standby）与猫娘联动、以及弹幕两个开关的边界。

1. **入口接线**：``__init__.py`` 的 plugin_entry / llm_tool 真的把 mode 落到 service；
2. 与猫娘的两条边界：``danmaku_enabled`` vs ``catgirl_llm_enabled``、
   ``program`` / ``advise`` / ``standby`` 与 ``coop_control_mode`` 的映射。

运行方式见 AGENTS.md 的「Plugin tests」。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.mode_controller import STS2ModeController
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None

    def exception(self, *a, **k):
        return None


class FakeClient:
    """够用的传输桩：只记录弹幕推送与 /config 写入，不发任何真实 HTTP。"""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.config_writes: list[dict[str, Any]] = []
        self.cleared = 0

    async def push_danmaku(self, text, *, style="catgirl", placement="scrolling", avatar=None) -> dict:
        self.pushed.append((text, style))
        return {"status": "ok"}

    async def set_config(self, **fields) -> dict:
        self.config_writes.append(dict(fields))
        return {"ok": True}

    async def clear_danmaku(self) -> dict:
        self.cleared += 1
        return {"ok": True}


def _service(client: FakeClient | None = None) -> tuple[STS2AutoplayService, FakeClient]:
    fake = client or FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: None)
    service._client = fake
    return service, fake


# =====================================================================================
# 5. 入口接线：__init__.py 的 plugin_entry / llm_tool 真的落到 service
# =====================================================================================


class _RecordingService:
    """只记录"入口层到底把什么参数交给 service"的最小替身。"""

    def __init__(self) -> None:
        self.mode_calls: list[str] = []
        self.neko = self

    async def set_control_mode(self, mode: str) -> dict[str, Any]:
        self.mode_calls.append(mode)
        return {"status": "ok", "mode": STS2ModeController().normalize(mode), "summary": "ok"}


class _RecordingDispatcher:
    """替掉真 dispatcher：它需要完整的 SDK ctx，而这里只关心推送参数。"""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    def push_status_feedback(self, text, *, entry_id, ai_behavior="respond", **kw):
        self._sink.append({"entry_id": entry_id, "text": text, "ai_behavior": ai_behavior})

    def push_frontend_notification(self, **kwargs):
        self._sink.append(dict(kwargs))


def _make_plugin():
    """用**真实的** STS2AutoplayPlugin 入口方法，只把 service 换成替身。

    直接继承真类（而不是重写入口）才能保证测的是真的入口函数，包括装饰器注册的形状。
    """
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    class Plugin(STS2AutoplayPlugin):
        def __init__(self) -> None:
            self._service = _RecordingService()
            self.pushed: list[dict[str, Any]] = []
            self.logger = DummyLogger()
            self._dispatcher = _RecordingDispatcher(self.pushed)

        def push_message(self, **kwargs: Any):  # type: ignore[override]
            self.pushed.append(kwargs)
            return {"submitted": True}

        async def finish(self, **kwargs: Any):  # type: ignore[override]
            return kwargs

    return Plugin()


def _make_plugin_with_real_service():
    """入口 + **真实** service（client 换成桩），用来验证"入口 -> service -> 状态"整条链。"""
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    real = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None, i18n=None)
    real._client = FakeClient()

    class Plugin(STS2AutoplayPlugin):
        def __init__(self) -> None:
            self._service = real
            self.pushed: list[dict[str, Any]] = []
            self.logger = DummyLogger()
            self._dispatcher = _RecordingDispatcher(self.pushed)

        def push_message(self, **kwargs: Any):  # type: ignore[override]
            self.pushed.append(kwargs)
            return {"submitted": True}

        async def finish(self, **kwargs: Any):  # type: ignore[override]
            return kwargs

    return Plugin()


def _unwrap(result: Any) -> dict[str, Any]:
    """`_run_entry` 返回 SDK 的 `Ok(...)`/`Err(...)`，把里面的 payload 取出来。"""
    value = getattr(result, "value", None)
    if isinstance(value, dict):
        return value
    if isinstance(result, dict):
        return result
    raise AssertionError(f"拿不到 payload，返回值是 {result!r}")


@pytest.mark.unit
def test_plugin_entry_sts2_set_control_mode_and_llm_tool_are_wired() -> None:
    plugin = _make_plugin()
    asyncio.run(plugin.sts2_set_control_mode(mode="听我安排"))
    asyncio.run(plugin.llm_set_control_mode(mode="停手"))

    assert plugin._service.mode_calls == ["听我安排", "停手"]
    assert plugin._service.mode_calls[:1] != plugin._service.mode_calls[1:]


def _entry_metas() -> dict[str, Any]:
    """读注册期真正挂在函数上的入口元数据（SDK 的 ``__neko_event_meta__``）。

    这就是 SDK 派发时用的那一份；不解析源码 —— 后者会在导入期/运行期差异上给出假结论。
    """
    from plugin.plugins.sts2_autoplay import STS2AutoplayPlugin

    metas: dict[str, Any] = {}
    for name, value in vars(STS2AutoplayPlugin).items():
        meta = getattr(value, "__neko_event_meta__", None)
        if meta is not None:
            metas[name] = meta
    return metas


@pytest.mark.unit
def test_the_declared_entry_schemas_match_the_implementation() -> None:
    """入口的 enum 必须与实现的三个控制档位一一对应；已删除的游玩模式入口不得再出现。"""
    metas = _entry_metas()
    assert "sts2_set_control_mode" in metas, sorted(metas)
    assert "sts2_set_play_style" not in metas

    mode_schema = metas["sts2_set_control_mode"].input_schema
    assert tuple(mode_schema["properties"]["mode"]["enum"]) == ("program", "advise", "standby")


# =====================================================================================
# 6. 与猫娘的两条边界：danmaku_enabled vs catgirl_llm_enabled
# =====================================================================================


@pytest.mark.unit
def test_catgirl_llm_disabled_still_pushes_danmaku_with_heuristic_text(monkeypatch) -> None:
    """`catgirl_llm_enabled=False` 只把点评**降级为启发式文本**，弹幕照旧推。

    为了把"是否真的推了"变成确定性的断言（而不是靠 sleep 赌异步任务跑到），这里把
    `_push_catgirl` 直接换成记录器 —— 已经由既有测试证明了 `_push_catgirl` 会 POST /danmaku。
    头像也打桩成瞬时返回，所以这条测试不再依赖任何真实 HTTP。
    """
    service, _client = _service()
    service._catgirl_llm_enabled = False
    assert service._danmaku_enabled is True

    llm_calls: list[dict[str, Any]] = []
    avatar_calls: list[str] = []
    pushed: list[str] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            llm_calls.append(kw)
            raise AssertionError("catgirl_llm_enabled=False 时不该调用 LLM")

    async def fake_avatar_full():
        avatar_calls.append("full")
        return None

    service._catgirl_llm = Gen()
    service._catgirl_avatar_base64 = fake_avatar_full  # type: ignore[method-assign]
    service._cached_avatar_base64 = lambda: avatar_calls.append("cached") or None  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_push_catgirl", lambda text, **kw: pushed.append(text))

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "建议先防御。"},
            {"message": "建议先防御。", "screen": "COMBAT", "summary_kind": "combat"},
        )
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(go())

    assert llm_calls == [], "关了 LLM 不该还去调模型"
    assert pushed == ["建议先防御。"], "弹幕必须照推（这是老行为，不能被闸门误伤）"
    assert avatar_calls == [], "既然打桩了 _push_catgirl，头像 HTTP 就不该发生"


@pytest.mark.unit
def test_catgirl_llm_enabled_but_unavailable_falls_back_without_going_silent(monkeypatch) -> None:
    """LLM 开关开着但模型不可用（或这次没产出）时，也必须退回启发式文本，不能噤声。"""
    service, _client = _service()
    service._catgirl_llm_enabled = True

    pushed: list[str] = []

    class Unavailable:
        available = False

        async def generate(self, **kw):  # pragma: no cover - available=False 就不该被调用
            raise AssertionError("available=False 时不该调用 generate")

    async def fake_avatar_full():
        return None

    service._catgirl_llm = Unavailable()
    service._catgirl_avatar_base64 = fake_avatar_full  # type: ignore[method-assign]
    service._cached_avatar_base64 = lambda: None  # type: ignore[method-assign]
    monkeypatch.setattr(service, "_push_catgirl", lambda text, **kw: pushed.append(text))

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "启发式兜底。"},
            {"message": "启发式兜底。", "screen": "MAP", "summary_kind": "map"},
        )
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(go())

    assert pushed == ["启发式兜底。"]


@pytest.mark.unit
def test_danmaku_disabled_blocks_llm_avatar_and_post_entirely() -> None:
    """`danmaku_enabled=False` 是**真闸门**：连 LLM 生成与头像 HTTP 都不发生。

    ★ t51 补充：0.7.9 起还有一条**替代通道** —— 弹幕关着时若开着
      `host_commentary_when_danmaku_off`（默认 true），同一段点评会改走宿主 `push_message`
      让猫娘在聊天里说（见 `tests/test_t51_host_commentary.py`）。
      本用例钉的是**"两路都关"的老契约**，所以显式把那条通道关掉；
      "弹幕关 + 宿主开 ⇒ 生成但不 POST /danmaku"由 t51 的用例覆盖。
    """
    service, client = _service()
    service._danmaku_enabled = False
    service._cfg["host_commentary_when_danmaku_off"] = False

    llm_calls: list[dict[str, Any]] = []

    class Gen:
        available = True

        async def generate(self, **kw):
            llm_calls.append(kw)
            return "这条不该出现"

        async def _unused(self):  # pragma: no cover
            return None

    service._catgirl_llm = Gen()

    async def explode_async():
        raise AssertionError("弹幕关掉后不该拉头像")

    def explode_sync():
        raise AssertionError("弹幕关掉后不该拉头像")

    service._catgirl_avatar_base64 = explode_async  # type: ignore[method-assign]
    service._cached_avatar_base64 = explode_sync  # type: ignore[method-assign]

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "启发式文本。"},
            {"message": "启发式文本。", "screen": "COMBAT", "summary_kind": "combat"},
        )
        service._emit_instant_catgirl_line(
            {"screen": "COMBAT", "floor": 5, "turn": 2}, {}, {"summary_kind": "combat"}
        )
        service._push_catgirl_fallback({"primary_message": "兜底文本。"}, {"message": "兜底文本。"})
        service._schedule_catgirl_push("调度文本。")
        await asyncio.sleep(0.05)
        # 真正的出口也要挡住
        await service._push_catgirl("直接推送。")

    asyncio.run(go())

    assert llm_calls == [], "弹幕关掉后不该调 LLM"
    assert client.pushed == [], "弹幕关掉后不该有 POST /danmaku"


@pytest.mark.unit
def test_danmaku_disabled_also_blocks_the_host_digest_push() -> None:
    """弹幕总闸门还顺带挡住"给本体的局势速报"——速报与弹幕同生共死。"""
    calls: list[dict[str, Any]] = []
    service = STS2AutoplayService(DummyLogger(), lambda payload: None, lambda **k: calls.append(k))
    service._danmaku_enabled = False

    service._push_host_situation_digest(
        {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}},
        {"screen": "COMBAT", "summary_kind": "combat", "player": {"current_hp": 60, "max_hp": 80}},
    )
    assert calls == []

    service._danmaku_enabled = True
    service._push_host_situation_digest(
        {"screen": "COMBAT", "floor": 5, "turn": 2, "raw_state": {"combat": {"enemies": []}}},
        {"screen": "COMBAT", "summary_kind": "combat", "player": {"current_hp": 60, "max_hp": 80}},
    )
    assert len(calls) == 1, "闸门打开后速报应当恢复"


@pytest.mark.unit
def test_instant_line_uses_the_avatar_cache_only() -> None:
    """即时短句绝不能为头像等 HTTP：它只用缓存（`fetch_avatar=False`），否则"即时"就没了。"""
    service, _client = _service()
    seen: list[bool] = []

    def fake_schedule(text: str, *, fetch_avatar: bool = True) -> None:
        seen.append(fetch_avatar)

    service._schedule_catgirl_push = fake_schedule  # type: ignore[method-assign]
    service._emit_instant_catgirl_line(
        {"screen": "MAP", "floor": 3, "turn": None},
        {"primary_message": "往右边走喵"},
        {"screen": "MAP", "summary_kind": "map", "message": "三条路"},
    )

    assert seen == [False], "即时短句必须走缓存头像（fetch_avatar=False）"


# =====================================================================================
# 7. 控制档位 program / advise / standby 与猫娘的协同
# =====================================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("program", "auto"), ("advise", "advise"), ("standby", "hold")],
)
def test_control_mode_is_synced_to_the_mod(mode: str, expected: str) -> None:
    """三个档位必须映射成 mod 的 coop_control_mode：只停插件等于没停。"""
    service, client = _service()
    result = asyncio.run(service.set_control_mode(mode))

    assert result["mode"] == mode
    assert result["mod_sync"] == {"applied": True, "mode": expected}
    assert client.config_writes[-1] == {"coop_control_mode": expected}


@pytest.mark.unit
def test_control_mode_sync_reports_failure_without_crashing() -> None:
    """mod 不通时如实回报 applied=False，而不是假装成功（失败是良性的，但不能撒谎）。"""
    service, client = _service()

    async def boom(**fields):
        raise RuntimeError("mod 不在")

    client.set_config = boom  # type: ignore[method-assign]
    result = asyncio.run(service.set_control_mode("standby"))

    assert result["mod_sync"] == {"applied": False, "mode": "hold"}
    assert result["mode"] == "standby"


@pytest.mark.unit
def test_no_client_yet_still_reports_the_intended_mapping() -> None:
    """游戏没开时设档位：插件侧照样生效，映射只回报意图（mode）而不谎报已应用。"""
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    assert service._client is None
    result = asyncio.run(service.set_control_mode("advise"))

    assert result["mod_sync"] == {"applied": False, "mode": "advise"}


@pytest.mark.unit
def test_standby_does_not_execute_even_in_combat() -> None:
    """standby 连战斗也不动：`run_autoplay_step` 在算计划之前就早退。"""
    service, _client = _service()
    service._apply_control_mode("standby")
    service._state.snapshot = {
        "classification": {"state_name": "combat"},
        "agent_operation": {"action_type": "play_card", "kwargs": {}},
    }

    executed: list[Any] = []

    async def fake_execute(operation):
        executed.append(operation)
        return {"status": "ok"}

    service.execute_operation = fake_execute  # type: ignore[method-assign]
    result = asyncio.run(service.run_autoplay_step())

    assert result["status"] == "idle"
    assert "standby" in str(result["message"])
    assert executed == [], "standby 下不该执行任何动作"


@pytest.mark.unit
def test_advise_holds_off_combat_but_fights_in_combat() -> None:
    """advise：非战斗界面只算不做，战斗照常自动打（否则回合永久卡住）。"""
    service, _client = _service()
    service._apply_control_mode("advise")
    service._state.interrupt_requested = False
    service._state.force_replan_requested = False

    # 非战斗决策屏 -> 停手
    service._state.snapshot = {
        "classification": {"state_name": "reward"},
        "agent_operation": {"action_type": "claim_reward", "kwargs": {}},
    }
    held = asyncio.run(service.run_autoplay_step())
    assert held.get("held") is True and held["status"] == "idle"

    # 战斗 -> 不停手
    service._state.snapshot = {"classification": {"state_name": "combat"}}
    assert service._should_hold_for_orders(service._state.snapshot) is False


@pytest.mark.unit
def test_advise_plans_but_program_executes_the_hold_never_screens() -> None:
    """`allows_planner` / `allows_execute` 的三档对照，以及"进局流程永远不停手"。"""
    controller = STS2ModeController()
    program = controller.describe("program")
    advise = controller.describe("advise")
    standby = controller.describe("standby")

    assert (program["allows_planner"], program["allows_execute"]) == (True, True)
    assert (advise["allows_planner"], advise["allows_execute"]) == (True, False)
    assert (standby["allows_planner"], standby["allows_execute"]) == (False, False)


@pytest.mark.unit
def test_standby_true_and_false_round_trip_and_sync_to_the_mod() -> None:
    """`sts2_set_standby(true/false)` 走的是同一条 coop_control_mode 同步路径。"""
    service, client = _service()

    info_on = service.set_standby(True)
    assert info_on["mode"] == "standby"
    assert info_on["allows_planner"] is False
    sync_on = asyncio.run(service.sync_control_mode_to_mod())
    assert sync_on == {"applied": True, "mode": "hold"}

    info_off = service.set_standby(False)
    assert info_off["mode"] == "program"
    sync_off = asyncio.run(service.sync_control_mode_to_mod())
    assert sync_off == {"applied": True, "mode": "auto"}

    assert client.config_writes[-2:] == [
        {"coop_control_mode": "hold"},
        {"coop_control_mode": "auto"},
    ]


@pytest.mark.unit
def test_switching_to_the_catgirl_instance_pushes_hold() -> None:
    """切到猫娘实例时顺手推 hold，避免插件与 mod 两个驱动抢同一个动作。"""
    service, client = _service()
    service._host_base_url = "http://127.0.0.1:18080"
    service._host_client = client
    service._client = client
    service._catgirl_base_url = "http://127.0.0.1:18081"
    service._catgirl_client = client

    result = asyncio.run(service.switch_active_instance("catgirl", reason="test"))

    assert result["switched"] is True
    assert result["instance"] == "catgirl"
    assert client.config_writes[-1] == {"coop_control_mode": "hold"}


@pytest.mark.unit
def test_switching_control_mode_interrupts_the_pending_decision() -> None:
    """切档必须打断已算好的那一步（否则切到"听安排"却仍走了一步）。"""
    service, _client = _service()
    service._state.interrupt_requested = False
    service._apply_control_mode("advise")

    assert service._state.interrupt_requested is True
    assert service._state.interrupt_reason == "mode_change"


@pytest.mark.unit
def test_get_status_exposes_the_danmaku_gate() -> None:
    """状态查询里能看到弹幕闸门；已删除的游玩模式不再出现在状态里。"""
    service, _client = _service()
    service.set_danmaku_enabled(False)

    status = asyncio.run(service.get_status())

    assert "play_style" not in status
    assert status["danmaku"]["enabled"] is False
    assert status["danmaku"]["catgirl_llm_enabled"] is True, "两个开关必须分别可见"
