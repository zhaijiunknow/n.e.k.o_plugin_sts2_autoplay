# -*- coding: utf-8 -*-
"""AlphaDefect 策略网络的插件侧接线（policy_client + loop_runner.tick 的接入点）。

钉住的不变量
------------
1. 什么都没下载时不碰 mod、不碰旁路，行为与接入前相同；这里不触发任何下载。
2. 网络给得出时整步归它：不取 `/solver/plan`、不跑启发式规划器，操作是 `policy_act` + 候选下标 + decision_id。
3. 给不出时**回落而不是猜**：运行环境没装、本局角色没有模型、旁路没起、这一屏没有适配、
   选中的候选没有动作 → None，日志写明原因。
4. 「还没轮到」（mod 回 wait）≠「没有适配」：这一 tick 谁都不出动作，后备不得接手。
5. 多人局的地图投票仍听 map_policy。
6. 模组的词表与本机的不是同一份时，先把本机这份交给模组，这一步回落。
7. 选人只在已下载模型的角色里选；一个都没有时照旧用游戏的「随机」。
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.action_engine import STS2ActionEngine
from plugin.plugins.sts2_autoplay.action_registry import STS2ActionRegistry
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.policy_client import (
    POLICY_ACTION_TYPE,
    POLICY_SOURCE,
    PolicyAdvisor,
    is_policy_hold,
    is_policy_operation,
)
from test_d10_main_menu_recovery import _FakeClient, _make_runner

DATA_ID = "b9ba21326c3d90dc"


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str, *args: Any) -> None:
        self.lines.append(msg % args if args else msg)


class _Assets:
    """本机资产的替身：哪些角色有模型、运行环境在不在。任何下载方法都不该被调用。"""

    def __init__(self, *, runtime: bool = True, models: tuple[str, ...] = ("IRONCLAD",)) -> None:
        self.runtime, self.models = runtime, list(models)
        self.root, self.python_exe = "root", "python.exe"

    def runtime_ready(self) -> bool:
        return self.runtime

    def model_ready(self, character: str) -> bool:
        return character in self.models

    def ready_characters(self) -> list[str]:
        return list(self.models)

    def mod_data_id(self) -> str:
        return DATA_ID

    def mod_data(self) -> dict[str, str]:
        return {"vocab.txt": "a\nb\n"}

    def ensure_runtime(self) -> None:  # pragma: no cover
        raise AssertionError("决策路径不得触发下载")

    def download_model(self, character: str) -> None:  # pragma: no cover
        raise AssertionError("决策路径不得触发下载")


def _decision(**over: Any) -> dict[str, Any]:
    base = {
        "available": True,
        "data_id": DATA_ID,
        "decision_id": "abc123",
        "decision_kind": "COMBAT",
        "character": "IRONCLAD",
        "multiplayer": False,
        "obs_format": 28,
        "frame": "AAAA",
        "forced_index": None,
        "candidates": [
            {"index": 0, "kind": "PlayCard", "label": "play BASH -> NIBBIT", "executable": True, "action": "play_card"},
            {"index": 1, "kind": "PlayCard", "label": "play DEFEND_IRONCLAD", "executable": True, "action": "play_card"},
            {"index": 2, "kind": "EndTurn", "label": "end turn", "executable": True, "action": "end_turn"},
        ],
    }
    base.update(over)
    return base


class _ModClient:
    def __init__(self, decision: dict[str, Any] | Exception) -> None:
        self.decision = decision
        self.formats: list[int] = []
        self.pushed: list[dict[str, str]] = []

    async def get_policy_decision(self, obs_format: int) -> dict[str, Any]:
        self.formats.append(obs_format)
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision

    async def push_policy_data(self, files: dict[str, str]) -> dict[str, Any]:
        self.pushed.append(files)
        return {"available": True, "data_id": DATA_ID}


class _Advisor(PolicyAdvisor):
    """旁路换成脚本：`health` 为 None 表示连不上；`scored` 是 /infer 的回复。"""

    def __init__(self, *, health: dict[str, Any] | None, scored: dict[str, Any] | None = None, enabled: bool = True,
                 assets: _Assets | None = None) -> None:
        self.log = _Logger()
        super().__init__(self.log, assets if assets is not None else _Assets())
        self.configure({"policy_enabled": enabled})
        self._health, self._scored = health, scored
        self.health_calls = 0
        self.infer_calls: list[str] = []
        self.sidecar_starts = 0

    async def _sidecar_health(self, character: str) -> dict[str, Any]:
        self.health_calls += 1
        if self._health is None:
            raise ConnectionError("refused")
        return self._health

    async def _sidecar_infer(self, character: str, frame: str) -> dict[str, Any]:
        self.infer_calls.append(character)
        assert self._scored is not None
        return self._scored

    def _start_sidecar(self) -> bool:
        self.sidecar_starts += 1
        return True


HEALTH = {"ok": True, "obs_format": 28, "characters": {"IRONCLAD": {}}}


def _scored(choice: int, n: int = 3) -> dict[str, Any]:
    probs = [0.1] * n
    probs[choice] = 0.8
    return {"ok": True, "choice": choice, "num_candidates": n, "probs": probs, "value": 1.5}


def _decide(advisor: PolicyAdvisor, client: Any, *, character: str = "IRONCLAD", **kwargs: Any) -> Any:
    return asyncio.run(advisor.decide(client, character=character, **kwargs))


@pytest.mark.unit
@pytest.mark.parametrize(
    "advisor, character",
    [
        (lambda: _Advisor(health=HEALTH, enabled=False), "IRONCLAD"),
        (lambda: _Advisor(health=HEALTH), ""),      # 不在对局里（主菜单、选人）
    ],
)
def test_off_or_outside_a_run_touches_nothing(advisor: Any, character: str) -> None:
    advisor = advisor()
    client = _ModClient(_decision())
    assert _decide(advisor, client, character=character) is None
    assert advisor.health_calls == 0 and client.formats == [] and advisor.fallbacks == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    "assets, expect",
    [
        (_Assets(runtime=False), "运行环境还没下载"),
        (_Assets(models=("SILENT",)), "没有下载 IRONCLAD 的模型"),
        (_Assets(models=()), "没有下载 IRONCLAD 的模型"),
    ],
)
def test_nothing_downloaded_falls_back_without_touching_mod_or_sidecar(assets: _Assets, expect: str) -> None:
    advisor = _Advisor(health=HEALTH, assets=assets)
    client = _ModClient(_decision())
    assert _decide(advisor, client) is None
    assert advisor.health_calls == 0 and advisor.sidecar_starts == 0 and client.formats == []
    assert any(expect in line for line in advisor.log.lines), advisor.log.lines


@pytest.mark.unit
def test_network_choice_becomes_a_policy_act_operation() -> None:
    advisor = _Advisor(health=HEALTH, scored=_scored(1))
    client = _ModClient(_decision())
    op = _decide(advisor, client)
    assert client.formats == [28], "帧格式必须按该角色网络的 obs_format 向 mod 要"
    assert advisor.infer_calls == ["IRONCLAD"], "推理必须点名角色（旁路里可能同时有几个角色的模型）"
    assert is_policy_operation(op) and not is_policy_hold(op)
    assert op["action_type"] == POLICY_ACTION_TYPE and op["source"] == POLICY_SOURCE
    assert op["kwargs"] == {"option_index": 1, "command": "abc123"}
    assert op["reason"] == "COMBAT: play DEFEND_IRONCLAD"
    assert advisor.decisions == 1 and advisor.fallbacks == 0
    _decide(advisor, client)
    assert advisor.health_calls == 1, "观测格式问过一次就记住，不是每步都问旁路"


@pytest.mark.unit
def test_forced_step_skips_the_network() -> None:
    advisor = _Advisor(health=HEALTH, scored=None)
    decision = _decision(decision_kind="REWARDS", frame=None, forced_index=0,
                         candidates=[{"index": 0, "kind": "Forced", "label": "take GoldReward", "executable": True}])
    op = _decide(advisor, _ModClient(decision))
    assert op["kwargs"]["option_index"] == 0 and advisor.infer_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "decision, scored, expect",
    [
        (_decision(available=False, reason="no policy adapter for screen NCapstoneSubmenuStack"), None, "不给这一屏出决策"),
        (_decision(character="SILENT"), None, "mod 的决策是 SILENT 的"),
        (_decision(candidates=[{"index": 0, "kind": "SkipRelic", "label": "skip", "executable": False},
                               {"index": 1, "kind": "PickRelic", "label": "take X", "executable": True}]),
         _scored(0, 2), "mod 没有对应的动作"),
        (_decision(), {"ok": False, "error": "frame format 19 does not match"}, "没有给出可用结果"),
        (_decision(), _scored(0, 5), "没有给出可用结果"),
        (_decision(frame=None), None, "没有观测帧"),
        (RuntimeError("HTTP 500"), None, "/policy/decision 失败"),
    ],
)
def test_falls_back_with_a_stated_reason(decision: Any, scored: Any, expect: str) -> None:
    advisor = _Advisor(health=HEALTH, scored=scored)
    assert _decide(advisor, _ModClient(decision)) is None
    assert advisor.fallbacks == 1 and advisor.decisions == 0
    assert any(expect in line for line in advisor.log.lines), advisor.log.lines


@pytest.mark.unit
def test_sidecar_down_starts_it_falls_back_and_is_not_probed_every_tick() -> None:
    advisor = _Advisor(health=None)
    client = _ModClient(_decision())
    assert _decide(advisor, client) is None
    assert _decide(advisor, client) is None
    assert advisor.health_calls == 1, "旁路连不上时不该每个 tick 都去等一次连接超时"
    assert advisor.sidecar_starts == 1 and client.formats == []
    assert any("连不上" in line and "已启动旁路进程" in line for line in advisor.log.lines)


@pytest.mark.unit
def test_sidecar_that_cannot_load_the_model_falls_back() -> None:
    advisor = _Advisor(health={"ok": False, "error": "FileNotFoundError: the IRONCLAD model is not downloaded"})
    assert _decide(advisor, _ModClient(_decision())) is None
    assert any("加载 IRONCLAD 的模型失败" in line for line in advisor.log.lines)


@pytest.mark.unit
def test_not_my_turn_yet_is_a_hold_not_a_fallback() -> None:
    advisor = _Advisor(health=HEALTH)
    op = _decide(advisor, _ModClient({"available": False, "wait": True, "data_id": DATA_ID, "reason": "not the local player's play phase"}))
    assert is_policy_hold(op) and not is_policy_operation(op)
    assert advisor.fallbacks == 0


@pytest.mark.unit
@pytest.mark.parametrize("mod_data_id", [None, "0000000000000000"])
def test_mod_without_our_vocabulary_gets_it_and_this_step_falls_back(mod_data_id: Any) -> None:
    advisor = _Advisor(health=HEALTH, scored=_scored(0))
    client = _ModClient(_decision(data_id=mod_data_id))
    assert _decide(advisor, client) is None
    assert client.pushed == [{"vocab.txt": "a\nb\n"}] and advisor.infer_calls == []
    assert _decide(advisor, client) is None
    assert len(client.pushed) == 1, "交过一次仍对不上时，不要每个 tick 都再交一次"
    client.decision = _decision()
    assert is_policy_operation(_decide(advisor, client))


@pytest.mark.unit
def test_multiplayer_map_still_follows_map_policy() -> None:
    decision = _decision(decision_kind="MAP", multiplayer=True)
    waiting = _Advisor(health=HEALTH, scored=_scored(0))
    assert _decide(waiting, _ModClient(decision), map_policy="wait") is None
    assert waiting.infer_calls == []
    auto = _Advisor(health=HEALTH, scored=_scored(0))
    assert is_policy_operation(_decide(auto, _ModClient(decision), map_policy="auto"))
    solo = _Advisor(health=HEALTH, scored=_scored(0))
    assert is_policy_operation(_decide(solo, _ModClient(_decision(decision_kind="MAP")), map_policy="wait"))


# ---- 选人 -------------------------------------------------------------------------------


def _select_context(selected: str) -> dict[str, Any]:
    return {"payload": {"selected_character_id": selected, "character_select": {"characters": [
        {"index": 0, "character_id": "RANDOM", "is_random": True},
        {"index": 1, "character_id": "IRONCLAD"},
        {"index": 2, "character_id": "SILENT"},
        {"index": 3, "character_id": "DEFECT", "is_locked": True},
    ]}}}


@pytest.mark.unit
def test_playable_characters_need_runtime_and_model() -> None:
    assert _Advisor(health=HEALTH, assets=_Assets(models=("IRONCLAD", "SILENT"))).playable_characters() == ["IRONCLAD", "SILENT"]
    assert _Advisor(health=HEALTH, assets=_Assets(runtime=False)).playable_characters() == []
    assert _Advisor(health=HEALTH, enabled=False).playable_characters() == []


@pytest.mark.unit
def test_character_select_picks_among_downloaded_models_only() -> None:
    planner = STS2HeuristicPlanner(None)
    context = _select_context("SILENT")
    assert planner._preferred_character_index(context) is None, "没配置时照旧用游戏的随机"
    downloaded = ["IRONCLAD"]
    planner.configure_preferred_characters(lambda: downloaded)
    assert planner._preferred_character_index(context) == 1
    assert not planner._preferred_character_selected(context)
    assert planner._preferred_character_selected(_select_context("IRONCLAD"))

    downloaded[:] = ["DEFECT"]      # 有模型但角色还锁着：等于没有
    planner._character_pick_target = ""
    assert planner._preferred_character_index(context) is None

    downloaded[:] = []              # 模型被删了：回到游戏的随机
    assert planner._preferred_character_index(context) is None


@pytest.mark.unit
def test_character_pick_is_random_per_visit_and_stable_within_one() -> None:
    planner = STS2HeuristicPlanner(None)
    planner.configure_preferred_characters(lambda: ["IRONCLAD", "SILENT"])
    planner._character_rng = random.Random(7)
    context = _select_context("RANDOM")
    seen = set()
    for _ in range(40):
        first = planner._preferred_character_index(context)
        assert all(planner._preferred_character_index(context) == first for _ in range(3)), "同一次选人里不能变来变去"
        seen.add(first)
        planner.plan({"classification": {"state_name": "main_menu"}})      # 离开选人界面：下次重新定
        assert planner._character_pick_target == "" and planner._character_pick_done is False
    assert seen == {1, 2}


# ---- tick 接入点 -------------------------------------------------------------------------


class _TickClient(_FakeClient):
    def __init__(self, decision: dict[str, Any]) -> None:
        super().__init__(state_raises=False)
        self.decision = decision
        self.plan_calls = 0

    async def get_state(self) -> dict[str, Any]:
        return {"screen": "COMBAT", "in_combat": True, "run": {"character_id": "IRONCLAD"}}

    async def get_combat_plan(self) -> dict[str, Any]:
        self.plan_calls += 1
        return {"in_combat": False}

    async def get_policy_decision(self, obs_format: int) -> dict[str, Any]:
        return self.decision


class _Planner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, context: Any) -> None:
        self.calls += 1
        return None


def _tick(decision: dict[str, Any], advisor: PolicyAdvisor, *, autoplay: str = "running") -> tuple[dict[str, Any], _TickClient, _Planner]:
    client = _TickClient(decision)
    runner, state, _logger, _sm = _make_runner(client)
    planner = _Planner()
    service = runner._service
    service._policy_advisor = advisor
    service._planner = planner
    service._action_engine = STS2ActionEngine()
    service._action_registry = STS2ActionRegistry()
    service._map_policy = "wait"
    service._mode_controller = type("MC", (), {"describe": lambda self, mode: {"allows_planner": True}})()
    state.autoplay_state = autoplay
    return asyncio.run(runner.tick())["snapshot"], client, planner


@pytest.mark.unit
def test_tick_hands_the_whole_step_to_the_policy() -> None:
    snapshot, client, planner = _tick(_decision(), _Advisor(health=HEALTH, scored=_scored(2)))
    op = snapshot["agent_operation"]
    assert op["action_type"] == POLICY_ACTION_TYPE and op["kwargs"]["option_index"] == 2
    assert planner.calls == 0, "网络出了这一步，启发式规划器不该再跑"
    assert client.plan_calls == 0, "网络出了这一步，不该再让 mod 跑一遍 CombatSolver 搜索"
    executable = snapshot["executable_operation"]
    assert executable is not None and executable["action_type"] == POLICY_ACTION_TYPE, "policy_act 必须过得了动作校验"
    assert executable["kwargs"] == {"option_index": 2, "command": "abc123"}


@pytest.mark.unit
def test_tick_falls_back_to_the_planner_when_the_policy_declines() -> None:
    advisor = _Advisor(health=HEALTH)
    snapshot, _client, planner = _tick(_decision(available=False, reason="no adapter"), advisor)
    assert planner.calls == 1 and snapshot["agent_operation"] is None
    assert all(a.get("type") != POLICY_ACTION_TYPE for a in snapshot["action_registry"])


@pytest.mark.unit
def test_tick_uses_the_old_path_when_the_character_has_no_model() -> None:
    advisor = _Advisor(health=HEALTH, scored=_scored(0), assets=_Assets(models=("SILENT",)))
    _snapshot, client, planner = _tick(_decision(), advisor)
    assert planner.calls == 1 and client.plan_calls == 1, "没有模型的角色：求解器 + 启发式照旧"
    assert advisor.health_calls == 0


@pytest.mark.unit
def test_tick_holds_when_the_policy_says_wait() -> None:
    advisor = _Advisor(health=HEALTH)
    snapshot, client, planner = _tick({"available": False, "wait": True, "data_id": DATA_ID, "reason": "enemy turn"}, advisor)
    assert planner.calls == 0 and client.plan_calls == 0 and snapshot["agent_operation"] is None


@pytest.mark.unit
def test_tick_leaves_companion_polling_alone() -> None:
    """自动玩没在跑（陪玩 / 轮询）时不接管：出牌建议弹幕读的是 /solver/plan。"""
    advisor = _Advisor(health=HEALTH, scored=_scored(0))
    _snapshot, _client, planner = _tick(_decision(), advisor, autoplay="idle")
    assert advisor.health_calls == 0 and planner.calls == 1
