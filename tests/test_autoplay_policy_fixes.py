"""四个缺陷的回归测试。

1. 猫娘端无法关闭弹幕       -> danmaku_enabled 是插件侧唯一真闸门（_push_catgirl / _maybe_emit_catgirl_llm）
2. 和 AI 组队中途退出回不到存档 -> 主菜单决策必须优先 continue_run，不能抢开新局
3. 插件抢路线               -> 有人先投票就跟随；没人投票时只在多人局等
4. 战斗之外选择覆盖不全/选两张卡卡住 -> 选牌屏逐张推进 + 能确认就确认 + 新屏有分支
"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import (
    MAP_POLICY_WAIT,
    STS2HeuristicPlanner,
    parse_map_pick,
)
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService
from plugin.plugins.sts2_autoplay.state_machine import STS2StateMachine


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None


class FakeClient:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.config_writes: list[dict] = []
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
    client = client or FakeClient()
    service = STS2AutoplayService(DummyLogger(), lambda p: None, lambda **k: None)
    service._client = client
    return service, client


# ---- 1. 弹幕总开关 ---------------------------------------------------------------------


@pytest.mark.unit
def test_push_catgirl_is_silent_when_danmaku_disabled() -> None:
    """danmaku_enabled=False 之后 _push_catgirl 直接早退：不推、不拉头像。"""
    service, client = _service()
    service._danmaku_enabled = False
    asyncio.run(service._push_catgirl("这条不该出现"))
    assert client.pushed == []


@pytest.mark.unit
def test_maybe_emit_catgirl_llm_suppressed_when_danmaku_disabled() -> None:
    """关弹幕时连 LLM 生成/启发式兜底都不发生（catgirl_llm_enabled 只管文本来源，不是开关）。"""
    service, client = _service()

    async def boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("danmaku disabled must not reach the LLM")

    service._catgirl_llm.generate = boom  # type: ignore[method-assign]
    service._danmaku_enabled = False

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "启发式兜底文本。"},
            {"message": "启发式兜底文本。"},
        )
        await asyncio.sleep(0.1)

    asyncio.run(go())
    assert client.pushed == []


@pytest.mark.unit
def test_fallback_still_pushes_when_danmaku_is_enabled(monkeypatch) -> None:
    """闸门默认开：catgirl_llm_enabled=False 仍走启发式兜底推弹幕（老行为不能被闸门误伤）。"""
    service, client = _service()
    service._catgirl_llm_enabled = False
    assert service._danmaku_enabled is True

    # Unit test: optional host avatar HTTP must not race a fixed one-second sleep.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(service, "_catgirl_avatar_base64", AsyncMock(return_value=None))

    async def go() -> None:
        service._maybe_emit_catgirl_llm(
            {},
            {"should_comment": True, "primary_message": "建议先防御。"},
            {"message": "建议先防御。"},
        )
        # Wait for the actual push, not a live host/avatar request or an arbitrary delay.
        async def wait_pushed():
            while not client.pushed:
                await asyncio.sleep(0)
        await asyncio.wait_for(wait_pushed(), timeout=1.0)

    asyncio.run(go())
    assert client.pushed == [("建议先防御。", "catgirl")]


@pytest.mark.unit
def test_set_danmaku_enabled_toggles_flag_and_config() -> None:
    service, _client = _service()
    result = service.set_danmaku_enabled(False)
    assert result["enabled"] is False
    assert service._danmaku_enabled is False
    assert service._cfg["danmaku_enabled"] is False

    result = service.set_danmaku_enabled(True)
    assert result["enabled"] is True
    assert service._cfg["danmaku_enabled"] is True


@pytest.mark.unit
def test_set_danmaku_disabled_pushes_gate_to_mod_and_clears_screen() -> None:
    """关闭弹幕要把 mod 的 /danmaku 收口也写掉，并清掉屏幕上还在飘的旧弹幕。"""
    service, client = _service()
    service.set_danmaku_enabled(False)
    gate = asyncio.run(service.sync_danmaku_gate_to_mod())

    assert gate == {"applied": True, "cleared": True}
    assert client.config_writes == [{"danmaku_push_enabled": False}]
    assert client.cleared == 1


@pytest.mark.unit
def test_get_status_exposes_danmaku_switch() -> None:
    service, _client = _service()
    service.set_danmaku_enabled(False)
    status = asyncio.run(service.get_status())
    assert status["danmaku"]["enabled"] is False


@pytest.mark.unit
def test_neko_set_danmaku_goes_through_service_and_mod() -> None:
    """入口层 sts2_set_danmaku -> neko.set_danmaku：插件侧关掉 + 通知 mod + 清屏。"""
    service, client = _service()
    result = asyncio.run(service.neko.set_danmaku(False))
    assert result["enabled"] is False
    assert result["mod_gate"] == {"applied": True, "cleared": True}
    assert service._danmaku_enabled is False
    assert client.config_writes == [{"danmaku_push_enabled": False}]


@pytest.mark.unit
def test_continue_saved_run_reports_when_no_save(monkeypatch) -> None:
    """主菜单没有可继续存档时给可诊断的回报（界面 + 可用动作），而不是静默点错。"""
    service, _client = _service()

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict:
        service._state.snapshot = {
            "screen": "MAIN_MENU",
            "available_actions": [{"type": "open_character_select"}, {"type": "open_timeline"}],
        }
        return {}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)
    result = asyncio.run(service.neko.continue_saved_run())
    assert result["status"] == "idle"
    assert "open_character_select" in result["summary"]


@pytest.mark.unit
def test_continue_saved_run_executes_continue_run(monkeypatch) -> None:
    service, _client = _service()
    executed: list[str] = []

    class Client:
        async def execute_action(self, action: str, **kwargs) -> dict:
            executed.append(action)
            return {"ok": True}

    snapshots = [
        {"screen": "MAIN_MENU", "available_actions": [{"type": "continue_run"}]},
        {"screen": "MAP", "available_actions": [{"type": "choose_map_node"}]},
    ]
    state = {"i": 0}

    async def fake_refresh_state(*, trigger_sync: bool = False) -> dict:
        service._state.snapshot = snapshots[min(state["i"], len(snapshots) - 1)]
        state["i"] += 1
        return {}

    monkeypatch.setattr(service, "refresh_state", fake_refresh_state)
    monkeypatch.setattr(service, "_require_client", lambda: Client())

    result = asyncio.run(service.neko.continue_saved_run())
    assert executed == ["continue_run"]
    assert result["status"] == "ok"
    assert result["screen"] == "MAP"


# ---- 2. 回到存档 -----------------------------------------------------------------------


def _main_menu_context(actions: list[str]) -> dict:
    return {
        "classification": {"state_name": "main_menu"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "MAIN_MENU",
            "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
        },
    }


@pytest.mark.unit
def test_main_menu_prefers_continue_run_over_new_run() -> None:
    """存档存在时不能去点「单人/新建局」——那正是"回不到存档"的成因。"""
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_main_menu_context(["open_character_select", "continue_run", "open_timeline"]))
    assert operation is not None
    assert operation.action_type == "continue_run"
    assert operation.reason == "main_menu_continue_run"


@pytest.mark.unit
def test_main_menu_closes_submenu_so_continue_becomes_reachable() -> None:
    """子菜单开着时 Continue 会被藏起来（CanContinueRun 要求 SubmenusOpen==false），先收菜单。"""
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_main_menu_context(["open_multiplayer_menu", "close_main_menu_submenu"]))
    assert operation is not None
    assert operation.action_type == "close_main_menu_submenu"


@pytest.mark.unit
def test_main_menu_falls_back_to_new_run_when_no_save() -> None:
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_main_menu_context(["open_character_select", "open_timeline"]))
    assert operation is not None
    assert operation.action_type == "open_character_select"


# ---- 3. 跟随玩家路线 -------------------------------------------------------------------


def _map_context(*, nodes: list[dict], other_vote: int | None, is_multiplayer: bool) -> dict:
    return {
        "classification": {"state_name": "map"},
        "summary_context": {
            "payload": {
                "available_nodes": nodes,
                "travelable_nodes": nodes,
                "other_player_vote_index": other_vote,
                "is_multiplayer": is_multiplayer,
                "floor": 3,
                "current_node": {"row": 0, "col": 0},
            }
        },
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "MAP",
            "available_actions": [{"type": "choose_map_node", "raw": {"name": "choose_map_node", "requires_index": True}}],
        },
    }


_MAP_NODES = [
    {"index": 0, "row": 1, "col": 0, "node_type": "Monster"},
    {"index": 1, "row": 1, "col": 1, "node_type": "Elite"},
]


@pytest.mark.unit
def test_map_default_policy_waits_and_never_votes_in_coop() -> None:
    """新默认 wait：多人局里即使队友已经投了也不跟——路线完全留给玩家，直到玩家发话。"""
    planner = STS2HeuristicPlanner()
    assert planner.plan(_map_context(nodes=_MAP_NODES, other_vote=1, is_multiplayer=True)) is None


@pytest.mark.unit
def test_map_default_policy_still_picks_in_single_player() -> None:
    """wait 只对多人局生效：单机没人可等，必须立刻自己选，否则每个节点都白等一轮。"""
    planner = STS2HeuristicPlanner()
    operation = planner.plan(_map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=False))
    assert operation is not None
    assert operation.action_type == "choose_map_node"
    assert operation.kwargs["option_index"] in {0, 1}


@pytest.mark.unit
def test_map_follow_mode_mirrors_the_players_vote() -> None:
    """follow 是可选的老行为：队友先投就镜像跟随。"""
    planner = STS2HeuristicPlanner()
    planner.configure_map_policy(policy="follow", vote_wait_seconds=20)
    operation = planner.plan(_map_context(nodes=_MAP_NODES, other_vote=1, is_multiplayer=True))
    assert operation is not None
    assert operation.action_type == "choose_map_node"
    assert operation.kwargs["option_index"] == 1
    assert operation.reason == "map_follow_player_vote"


@pytest.mark.unit
def test_map_follow_mode_waits_then_falls_back() -> None:
    """follow 下没人投票时先让位一次；等待窗口设为 0 就不再等，立刻自己选。"""
    planner = STS2HeuristicPlanner()
    planner.configure_map_policy(policy="follow", vote_wait_seconds=20)
    assert planner.plan(_map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=True)) is None

    planner.configure_map_policy(policy="follow", vote_wait_seconds=0)
    operation = planner.plan(_map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=True))
    assert operation is not None
    assert operation.action_type == "choose_map_node"


@pytest.mark.unit
def test_map_auto_mode_picks_without_waiting() -> None:
    planner = STS2HeuristicPlanner()
    planner.configure_map_policy(policy="auto", vote_wait_seconds=20)
    operation = planner.plan(_map_context(nodes=_MAP_NODES, other_vote=None, is_multiplayer=True))
    assert operation is not None
    assert operation.action_type == "choose_map_node"


@pytest.mark.unit
def test_map_policy_unknown_value_falls_back_to_wait() -> None:
    """未知取值必须退化为 wait：宁可不投，也不能去抢玩家已经定好的路线。"""
    planner = STS2HeuristicPlanner()
    planner.configure_map_policy(policy="nonsense", vote_wait_seconds=20)
    assert planner._map_policy == MAP_POLICY_WAIT
    assert planner.plan(_map_context(nodes=_MAP_NODES, other_vote=1, is_multiplayer=True)) is None


# ---- 3b. 对话式地图协作（sts2_set_route）-----------------------------------------------


_MAP_NODES_3 = [
    {"index": 0, "row": 1, "col": 0, "node_type": "Monster"},
    {"index": 1, "row": 1, "col": 1, "node_type": "Event"},
    {"index": 2, "row": 1, "col": 2, "node_type": "Shop"},
]


def _map_summary(nodes: list[dict], *, other_vote: int | None = None, hp: tuple[int, int] = (70, 80)) -> dict:
    return {
        "payload": {
            "available_nodes": nodes,
            "travelable_nodes": nodes,
            "other_player_vote_index": other_vote,
            "is_multiplayer": True,
            "current_hp": hp[0],
            "max_hp": hp[1],
            "gold": 50,
        }
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("token", "count", "expected"),
    [
        ("中间", 3, 1),
        ("中间那个", 3, 1),
        ("middle", 4, 1),
        ("最左", 3, 0),
        ("左边", 3, 0),
        ("最右", 3, 2),
        ("最后一个", 3, 2),
        ("第三个", 3, 2),
        ("3", 3, 2),
        ("九", 3, None),
        ("", 3, None),
        ("随便", 3, None),
    ],
)
def test_parse_map_pick_understands_positions(token: object, count: int, expected: int | None) -> None:
    assert parse_map_pick(token, count) == expected


@pytest.mark.unit
def test_resolve_route_mirror_needs_the_player_vote_first() -> None:
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route(_map_summary(_MAP_NODES_3), mode="mirror")
    assert decision["ok"] is False
    assert decision["error"] == "no_player_vote"
    assert "投票" in decision["message"]


@pytest.mark.unit
def test_resolve_route_mirror_copies_the_player_vote() -> None:
    """"我选完了，来选这个" -> 投玩家当前那一票。"""
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route(_map_summary(_MAP_NODES_3, other_vote=2), mode="mirror")
    assert decision["ok"] is True
    assert decision["option_index"] == 2
    assert decision["position"] == 3
    assert decision["node_type"] == "Shop"


@pytest.mark.unit
def test_resolve_route_pick_middle_uses_the_players_words() -> None:
    """"你选中间" -> 三个候选的中间那个。"""
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route(_map_summary(_MAP_NODES_3), mode="pick", pick="你选中间")
    assert decision["ok"] is True
    assert decision["position"] == 2
    assert decision["option_index"] == 1


@pytest.mark.unit
def test_resolve_route_pick_explicit_index_wins_over_words() -> None:
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route(_map_summary(_MAP_NODES_3), mode="pick", pick="中间", index=2)
    assert decision["ok"] is True
    assert decision["option_index"] == 2


@pytest.mark.unit
def test_resolve_route_pick_unparsed_reports_the_candidates() -> None:
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route(_map_summary(_MAP_NODES_3), mode="pick", pick="随便啦")
    assert decision["ok"] is False
    assert decision["error"] == "pick_unparsed"
    assert decision["node_count"] == 3


@pytest.mark.unit
def test_resolve_route_outside_the_map_is_an_error() -> None:
    planner = STS2HeuristicPlanner()
    decision = planner.resolve_route({"payload": {}}, mode="safe")
    assert decision["ok"] is False
    assert decision["error"] == "not_on_map"


@pytest.mark.unit
def test_resolve_route_safe_avoids_elite_when_hp_is_low() -> None:
    """安全优先：血少时宁可走休息点，也不碰精英。"""
    planner = STS2HeuristicPlanner()
    nodes = [
        {"index": 0, "row": 1, "col": 0, "node_type": "Elite"},
        {"index": 1, "row": 1, "col": 1, "node_type": "Rest"},
    ]
    decision = planner.resolve_route(_map_summary(nodes, hp=(30, 80)), mode="safe")
    assert decision["ok"] is True
    assert decision["option_index"] == 1
    assert "先挑稳一点" in decision["message"]


# ---- 4. 选牌屏推进 + 覆盖不全 -----------------------------------------------------------


def _selection_context(*, cards: list[dict], actions: list[str], can_confirm: bool = False) -> dict:
    payload: dict = {"selection_cards": cards, "selection_can_confirm": can_confirm}
    if can_confirm:
        payload["selection_max_select"] = len(cards)
        payload["selection_selected_count"] = len(cards)
    return {
        "classification": {"state_name": "card_selection"},
        "summary_context": {"payload": payload},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "CARD_SELECTION",
            "available_actions": [{"type": name, "raw": {"name": name, "requires_index": True}} for name in actions],
        },
    }


_SELECTION_CARDS = [
    {"index": 0, "card_id": "Strike", "name": "打击", "card_type": "Attack"},
    {"index": 1, "card_id": "Defend", "name": "防御", "card_type": "Skill"},
]


@pytest.mark.unit
def test_two_card_selection_advances_to_a_different_card() -> None:
    """需要选 2 张时不能一直按同一张：按下一张之后必须换成另一张，否则 max_select 永远到不了。

    注意光标由**执行**推动（note_selection_option_pressed），不由规划推动 —— 见下面的回归测试。
    """
    planner = STS2HeuristicPlanner()
    context = _selection_context(cards=_SELECTION_CARDS, actions=["select_deck_card"])

    first = planner.plan(context)
    assert first is not None and first.action_type == "select_deck_card"
    planner.note_selection_option_pressed(first.kwargs["option_index"])

    second = planner.plan(context)

    assert second is not None
    assert second.action_type == "select_deck_card"
    assert second.kwargs["option_index"] != first.kwargs["option_index"]


# 现场（2026-10-07 01:16 那局）战斗内「生存者」弃牌屏的真实候选：一张诅咒 + 三张基本牌。
_LIVE_SELECTION_CARDS = [
    {"index": 0, "card_id": "ASCENDERS_BANE", "name": "进阶之灾", "card_type": "Curse"},
    {"index": 1, "card_id": "STRIKE_SILENT", "name": "打击", "card_type": "Attack"},
    {"index": 2, "card_id": "STRIKE_SILENT", "name": "打击", "card_type": "Attack"},
    {"index": 3, "card_id": "DEFEND_SILENT", "name": "防御", "card_type": "Skill"},
]


@pytest.mark.unit
def test_planning_ticks_alone_cannot_exhaust_the_selection_cursor() -> None:
    """**"猫娘卡在选牌屏"的回归测试。**

    事件流大约每 tick 调一次 plan()，而真正执行动作是另一回事（自动玩步进约 2.5 秒一次）。
    旧代码在**规划时**就把候选记成"已按过"，于是光靠 tick 就能把 4 个候选耗光：plan() 从此永远
    返回 None，这一屏再也出不了动作 —— 线上现场正是如此（selection.selected_count 一直是 0，
    一张都没选上，界面就此永久不动）。
    """
    planner = STS2HeuristicPlanner()
    context = _selection_context(cards=_LIVE_SELECTION_CARDS, actions=["select_deck_card"])

    operations = [planner.plan(context) for _ in range(30)]

    assert all(op is not None for op in operations), "只规划不该把候选耗光"
    assert len({op.kwargs["option_index"] for op in operations}) == 1, "没真的按键之前光标不该前进"


@pytest.mark.unit
def test_selection_presses_each_card_once_then_retries_instead_of_hanging() -> None:
    """整屏按遍仍不推进时：先让一次 None（留确认/离开的出口），随后必须重置重按而不是永久 None。"""
    planner = STS2HeuristicPlanner()
    context = _selection_context(cards=_SELECTION_CARDS, actions=["select_deck_card"])
    # 真实时序：先规划、执行时才算"按过"（签名由第一次 plan 建立，之后按键才留得住）。
    for _ in range(2):
        planned = planner.plan(context)
        assert planned is not None
        planner.note_selection_option_pressed(planned.kwargs["option_index"])

    # 第一次耗尽：返回 None，交给调用方走确认/离开分支。
    assert planner.plan(context) is None
    # 但绝不能永远 None —— 再来一次就必须重置光标、重新给出可选动作。
    retry = planner.plan(context)
    assert retry is not None, "候选按遍后必须重置重按，不能永久停手"
    assert retry.action_type == "select_deck_card"


@pytest.mark.unit
def test_selection_confirms_when_the_mod_says_it_can() -> None:
    """can_confirm 为真时优先确认——老代码把 select_deck_card 排在前面，永远确认不了。"""
    planner = STS2HeuristicPlanner()
    context = _selection_context(
        cards=_SELECTION_CARDS,
        actions=["select_deck_card", "confirm_selection"],
        can_confirm=True,
    )
    operation = planner.plan(context)
    assert operation is not None
    assert operation.action_type == "confirm_selection"


@pytest.mark.unit
def test_selection_screen_reset_on_new_card_set() -> None:
    """换屏/换候选集合后重新从头开始（新的一屏不该继承上一屏的"已按过"状态）。"""
    planner = STS2HeuristicPlanner()
    context = _selection_context(cards=_SELECTION_CARDS, actions=["select_deck_card"])
    for _ in range(2):
        planned = planner.plan(context)
        assert planned is not None
        planner.note_selection_option_pressed(planned.kwargs["option_index"])

    other = _selection_context(
        cards=[{"index": 0, "card_id": "Bash", "name": "重击", "card_type": "Attack"}],
        actions=["select_deck_card"],
    )
    operation = planner.plan(other)
    assert operation is not None
    assert operation.kwargs["option_index"] == 0


@pytest.mark.unit
def test_bundle_selection_confirms_after_choosing() -> None:
    """choose_bundle 选完仍在 available_actions 里，必须先确认再谈重选。"""
    planner = STS2HeuristicPlanner()
    context = {
        "classification": {"state_name": "bundle_selection"},
        "summary_context": {"payload": {}},
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "BUNDLE_SELECTION",
            "available_actions": [
                {"type": "choose_bundle", "raw": {"name": "choose_bundle", "requires_index": True}},
                {"type": "confirm_bundle", "raw": {"name": "confirm_bundle"}},
            ],
        },
    }
    first = planner.plan(context)
    second = planner.plan(context)
    assert first is not None and first.action_type == "choose_bundle"
    assert second is not None and second.action_type == "confirm_bundle"


@pytest.mark.unit
def test_cards_view_and_game_over_are_covered() -> None:
    """曾经没有任何分支的界面（cards_view / game_over / modal）现在能推进，不再静默卡住。"""
    planner = STS2HeuristicPlanner()

    def plan_with(screen: str, state_name: str, actions: list[str]):
        return planner.plan(
            {
                "classification": {"state_name": state_name},
                "summary_context": {"payload": {}},
                "strategy_context": {"preferences": {}},
                "snapshot": {
                    "screen": screen,
                    "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
                },
            }
        )

    cards_view = plan_with("CARDS_VIEW", "cards_view", ["close_cards_view"])
    assert cards_view is not None and cards_view.action_type == "close_cards_view"

    game_over = plan_with("GAME_OVER", "game_over", ["return_to_main_menu"])
    assert game_over is not None and game_over.action_type == "return_to_main_menu"

    modal = plan_with("MODAL", "modal", ["confirm_modal", "dismiss_modal"])
    assert modal is not None and modal.action_type == "dismiss_modal"


def _reward_context(rewards: list[dict], actions: list[str], *, card_options: list[dict] | None = None) -> dict:
    return {
        "classification": {"state_name": "reward"},
        "summary_context": {
            "payload": {
                "reward": {
                    "pending_card_choice": bool(card_options),
                    "can_proceed": not rewards and not card_options,
                    "rewards": rewards,
                    "card_options": card_options or [],
                    "alternatives": [],
                }
            }
        },
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "REWARD",
            "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
        },
    }


_REWARD_ACTIONS = ["claim_reward", "resolve_rewards", "collect_rewards_and_proceed"]


@pytest.mark.unit
def test_reward_prefers_card_over_potion() -> None:
    """药水最容易"领不动"（栏位满），所以领奖励时排到最后。"""
    planner = STS2HeuristicPlanner()
    context = _reward_context(
        [
            {"index": 0, "reward_type": "Potion", "description": "技能药水", "claimable": True},
            {"index": 1, "reward_type": "Card", "description": "将一张牌添加到你的牌组。", "claimable": True},
        ],
        _REWARD_ACTIONS,
    )

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "claim_reward"
    assert operation.kwargs["option_index"] == 1, "先领卡，别先领那个发不下来的药水"


@pytest.mark.unit
def test_reward_stops_reclaiming_an_option_that_does_not_land() -> None:
    """**"奖励屏无限重领"的回归测试。**

    药水栏满时"领药水奖励"发不出去，而 mod 照样回 `completed`：奖励清单不变、`step_count` 照涨，
    旧代码于是每 tick 重发同一个 `claim_reward(0)`（下标 0 正好是药水），实测卡了 5 分钟以上。
    现在"已经真的发过的下标"不再重发，全部领不动就推进界面。
    """
    planner = STS2HeuristicPlanner()
    potion_only = [{"index": 0, "reward_type": "Potion", "description": "技能药水", "claimable": True}]
    context = _reward_context(potion_only, _REWARD_ACTIONS)

    first = planner.plan(context)
    assert first is not None and first.kwargs["option_index"] == 0

    # 假装这一下真的发出去了，但奖励清单没变（药水没发下来）。
    planner.note_reward_claim_dispatched(0)

    second = planner.plan(context)
    assert second is not None
    assert second.action_type != "claim_reward", "领不动的奖励不能无限重发，必须改成推进界面"
    assert second.action_type == "collect_rewards_and_proceed"


@pytest.mark.unit
def test_reward_claim_cursor_resets_when_the_reward_list_changes() -> None:
    """领到一样东西之后清单会变，那时游标必须重置，否则剩下的奖励会被当成"已试过"跳过。"""
    planner = STS2HeuristicPlanner()
    both = [
        {"index": 0, "reward_type": "Potion", "claimable": True},
        {"index": 1, "reward_type": "Card", "claimable": True},
    ]
    first = planner.plan(_reward_context(both, _REWARD_ACTIONS))
    assert first is not None and first.kwargs["option_index"] == 1
    planner.note_reward_claim_dispatched(1)

    # 卡领到了，清单只剩药水（新签名 -> 游标重置）
    second = planner.plan(_reward_context([both[0]], _REWARD_ACTIONS))
    assert second is not None and second.action_type == "claim_reward"
    assert second.kwargs["option_index"] == 0


@pytest.mark.unit
def test_reward_card_choice_picks_from_card_options() -> None:
    """领出卡奖励之后的选卡段：动作换成 choose_reward_card，要从 card_options 里挑。"""
    planner = STS2HeuristicPlanner()
    context = _reward_context(
        [],
        ["choose_reward_card", "skip_reward_cards", "select_deck_card", "collect_rewards_and_proceed"],
        card_options=[
            {"index": 0, "card_id": "SETUP_STRIKE", "name": "预备打击", "card_type": "Attack", "energy_cost": 1},
            {"index": 1, "card_id": "POMMEL_STRIKE", "name": "剑柄打击", "card_type": "Attack", "energy_cost": 1},
        ],
    )

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "choose_reward_card"
    assert isinstance(operation.kwargs.get("option_index"), int)


@pytest.mark.unit
def test_chest_opens_before_claiming() -> None:
    """**"宝箱屏卡住"的回归测试。**

    mod 的宝箱流程是 `open_chest` -> `choose_treasure_relic` -> `proceed`。开箱**前**只有
    `open_chest` 在册（实测 floor 10：available_actions = open_chest / discard_potion /
    save_and_quit，且 chest.is_opened=false、relic_options=[]），旧分支只找后两个 ——
    规划不出任何动作，猫娘永久停在宝箱屏（卡了 6 分钟以上，step_count 一直是 0）。
    """
    planner = STS2HeuristicPlanner()

    def plan_chest(actions: list[str]):
        return planner.plan(
            {
                "classification": {"state_name": "chest"},
                "summary_context": {"payload": {}},
                "strategy_context": {"preferences": {}},
                "snapshot": {
                    "screen": "CHEST",
                    "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
                },
            }
        )

    opened = plan_chest(["open_chest", "discard_potion", "save_and_quit"])
    assert opened is not None and opened.action_type == "open_chest"

    claimed = plan_chest(["choose_treasure_relic", "proceed"])
    assert claimed is not None and claimed.action_type == "choose_treasure_relic"

    left = plan_chest(["proceed"])
    assert left is not None and left.action_type == "proceed"


def _rest_context(options: list[dict], *, hp: int = 80, max_hp: int = 80, preference: int | None = None) -> dict:
    # 偏好用规划器真正认的形状：records[].value.preferred_option_index
    # （见 _preferred_option_index；写成 instruction_type/option_index 是认不出来的）。
    strategy = {"preferences": {}}
    if preference is not None:
        strategy = {"preferences": {"records": [{"value": {"preferred_option_index": preference}}]}}
    return {
        "classification": {"state_name": "rest"},
        "summary_context": {"payload": {"rest": {"options": options}, "current_hp": hp, "max_hp": max_hp}},
        "strategy_context": strategy,
        "snapshot": {
            "screen": "REST",
            "available_actions": [{"type": "choose_rest_option", "raw": {"name": "choose_rest_option", "requires_index": True}}],
        },
    }


# mod 的 RestOptionPayload 发的是 option_id / title（不是 type/name）。
_REST_OPTIONS = [
    {"index": 0, "option_id": "REST", "title": "休息", "description": "回复生命", "is_enabled": True},
    {"index": 1, "option_id": "SMITH", "title": "锻造", "description": "升级一张牌", "is_enabled": True},
]


@pytest.mark.unit
def test_rest_prefers_smithing_over_healing_when_healthy() -> None:
    """**"插件偏爱休息回血"的回归测试。**

    玩家口径：锻造能升级卡牌、提升数值、降低费用，对战力提升很大，需要优先思考。
    旧实现是 `_preferred_route_option(preferences) or 0` —— 用**路由**偏好、否则取下标 0，
    而列表第一项通常就是回血，所以插件事实上永远在回血。
    """
    planner = STS2HeuristicPlanner()
    context = _rest_context(_REST_OPTIONS, hp=80, max_hp=80)  # 满血

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "choose_rest_option"
    assert operation.kwargs["option_index"] == 1, "满血时应该锻造，而不是回血"


@pytest.mark.unit
def test_rest_heals_when_hp_is_low() -> None:
    """血低的时候回血优先（快死了还去锻造就是送）。"""
    planner = STS2HeuristicPlanner()
    context = _rest_context(_REST_OPTIONS, hp=20, max_hp=80)  # 25%

    operation = planner.plan(context)

    assert operation is not None and operation.kwargs["option_index"] == 0


@pytest.mark.unit
def test_rest_option_order_does_not_decide_the_choice() -> None:
    """选项顺序调换也要选对：靠 option_id 判断类型，不是靠下标。"""
    planner = STS2HeuristicPlanner()
    swapped = [_REST_OPTIONS[1], _REST_OPTIONS[0]]
    swapped[0]["index"], swapped[1]["index"] = 1, 0  # 锻造在前

    operation = planner.plan(_rest_context(swapped, hp=80, max_hp=80))

    assert operation is not None and operation.kwargs["option_index"] == 1, "应选锻造（index=1）"


@pytest.mark.unit
def test_rest_respects_an_explicit_player_preference() -> None:
    """玩家显式说了要哪个就听他的（他说话算数）。"""
    planner = STS2HeuristicPlanner()
    context = _rest_context(_REST_OPTIONS, hp=80, max_hp=80, preference=0)

    operation = planner.plan(context)

    assert operation is not None and operation.kwargs["option_index"] == 0


@pytest.mark.unit
def test_rest_skips_disabled_options() -> None:
    planner = STS2HeuristicPlanner()
    options = [
        {"index": 0, "option_id": "SMITH", "title": "锻造", "is_enabled": False},
        {"index": 1, "option_id": "REST", "title": "休息", "is_enabled": True},
    ]

    operation = planner.plan(_rest_context(options, hp=80, max_hp=80))

    assert operation is not None and operation.kwargs["option_index"] == 1, "不可用的选项不能选"


@pytest.mark.unit
def test_rest_falls_back_to_first_option_when_types_are_unknown() -> None:
    """认不出类型时取第一个可用项，不能因为"没识别出来"就不出手。"""
    planner = STS2HeuristicPlanner()
    options = [{"index": 3, "option_id": "SOMETHING_NEW", "title": "未知道具", "is_enabled": True}]

    operation = planner.plan(_rest_context(options))

    assert operation is not None and operation.kwargs["option_index"] == 3


@pytest.mark.unit
def test_state_machine_classifies_new_non_combat_screens() -> None:
    """这些界面以前落在 unknown，planner 完全不出决策（"战斗之外的选择没有全部覆盖"）。"""
    machine = STS2StateMachine()
    for screen in ("BUNDLE_SELECTION", "CAPSTONE_SELECTION", "CARDS_VIEW", "RELIC_SELECTION", "MULTIPLAYER_LOBBY"):
        classification = machine.classify({"screen": screen})
        assert classification["screen_class"] == "selection", screen


# ---- 选人界面：不能一直重按 select_character --------------------------------------------


def _character_select_context(*, selected_id: str | None, actions: list[str], with_random: bool = False) -> dict:
    characters = [
        {"index": 0, "character_id": "IRONCLAD", "name": "绯刃战姬", "is_locked": False, "is_selected": selected_id == "IRONCLAD"},
        {"index": 1, "character_id": "SILENT", "name": "静默猎手", "is_locked": False, "is_selected": selected_id == "SILENT"},
    ]
    if with_random:
        characters.append(
            {"index": 5, "character_id": "RANDOM_CHARACTER", "name": "随机", "is_locked": False, "is_random": True, "is_selected": selected_id == "RANDOM_CHARACTER"}
        )
    return {
        "classification": {"state_name": "character_select"},
        "summary_context": {
            "payload": {"selected_character_id": selected_id, "character_select": {"characters": characters}}
        },
        "strategy_context": {"preferences": {}},
        "snapshot": {
            "screen": "CHARACTER_SELECT",
            "available_actions": [{"type": name, "raw": {"name": name}} for name in actions],
        },
    }


@pytest.mark.unit
def test_character_select_embarks_when_a_character_is_already_selected() -> None:
    """**"自动玩不会选角色"的回归测试。**

    `_selected_character_index` 取的是 `selected_character_id` 的下标，也就是**当前已经选中**的角色，
    所以 `select_character(那个下标)` 是 no-op：屏幕不推进 -> 下一 tick 又发一次 -> 无限重按，
    猫娘永远停在选人界面（线上实测：selected_character_id=IRONCLAD、can_embark=true，人却不动）。
    角色已经选好就该直接 embark 开局。
    """
    planner = STS2HeuristicPlanner()
    context = _character_select_context(selected_id="IRONCLAD", actions=["select_character", "embark"])

    operation = planner.plan(context)

    assert operation is not None
    assert operation.action_type == "embark", "角色已选中时必须开局，不能一直重按 select_character"


@pytest.mark.unit
def test_character_select_picks_random_then_embarks() -> None:
    """玩家口径：**每局角色随机** —— 用游戏自带的「随机」候选，按下一次之后再开局。"""
    planner = STS2HeuristicPlanner()
    context = _character_select_context(
        selected_id="IRONCLAD",
        actions=["select_character", "embark"],
        with_random=True,
    )

    first = planner.plan(context)
    assert first is not None and first.action_type == "select_character"
    assert first.kwargs["option_index"] == 5, "应按下游戏自带的「随机」候选"
    assert first.reason == "character_select_random"

    # 假设这一下真的发出去了（记账由执行推动）
    planner.note_character_select_dispatched(5)
    second = planner.plan(context)

    assert second is not None and second.action_type == "embark", "按过随机之后就该开局，不能反复按"


@pytest.mark.unit
def test_character_select_embarks_when_random_is_already_the_selection() -> None:
    planner = STS2HeuristicPlanner()
    context = _character_select_context(
        selected_id="RANDOM_CHARACTER",
        actions=["select_character", "embark"],
        with_random=True,
    )

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "embark"


@pytest.mark.unit
def test_character_select_embarks_without_a_random_entry() -> None:
    """没有「随机」候选时仍是"已选中就开局"（角色由游戏默认值决定，不能卡在重按）。"""
    planner = STS2HeuristicPlanner()
    context = _character_select_context(selected_id=None, actions=["select_character", "embark"])

    operation = planner.plan(context)

    assert operation is not None and operation.action_type == "embark"
