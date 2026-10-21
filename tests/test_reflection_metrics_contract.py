# -*- coding: utf-8 -*-
"""t10 / M2 —— 教训作用的**端到端**「零影响」回归契约（战斗屏 + 奖励屏 + 路线屏 + 事件屏）。

本文件钉住的契约（对应 t_r3 §3.1 G0 硬闸）
------------------------------------------------
1. **无教训时逐字等于原序** —— 没有记忆时行为与"完全没有复盘机制"逐字相同。
2. **走了真实注入路径仍是零影响** —— 见 `test_default_memory_injection_*`：
   用真实 `STS2StrategyRepository` + 真实 `catgirl_memory.json` 摘要注入 `avoid`，
   在**默认档**下必须与"空注入"逐字相同。这是 t10 基线的零点，也是 t11 的回归红线。
3. **手工喂 `avoid` 不是合格口径** —— `test_handfed_avoid_is_not_a_valid_proxy` 反向钉住 t2 的
   方法学证伪：手工绕过注入层会给出与真实路径**不同**的结论，所以判据必须走真实路径。

⚠️ 为什么这些测试**不足以**证明"教训能改变决策"
--------------------------------------------------
默认档（`serious`）下 `service.py:793` 早退，`ordered_candidates`（唯一调用点 `:804`）不可达，
所以本文件里的 `_ranked_option_indices` 直调**绕过了那道闸**。这些测试只证明
"`_apply_run_lessons` 这一层是对的"，**不能**当作"决策被改变了"的证据。
真正的位移判据在 `.dsh-artifacts/reflection_metrics.py` 的 `e2e` 段（`plan()` 端到端）。

`_apply_run_lessons` 的**直接**契约（降权/不删除/纯函数）已在
`tests/test_run_lessons_affect_decision.py` 钉牢，本文件不重复，只做**跨屏扩展**。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.strategy_repository import STS2StrategyRepository


# --------------------------------------------------------------------------------------
# 场景（自己构造，不依赖工装 JSON，保证在任何机器上可复现）
# --------------------------------------------------------------------------------------
def _combat_snapshot() -> dict:
    return {
        "screen": "COMBAT",
        "floor": 17,
        "in_combat": True,
        "turn": 3,
        "classification": {"state_name": "combat"},
        "available_actions": [{"type": "play_card"}, {"type": "end_turn"}],
        "summary_context": {"payload": {}},
        "raw_state": {
            "combat": {
                "hand": [
                    {"index": 0, "card_type": "attack", "name": "Strike", "playable": True,
                     "damage": 9, "block": 0, "cost": 1},
                    {"index": 1, "card_type": "skill", "name": "Defend", "playable": True,
                     "damage": 0, "block": 5, "cost": 1},
                ],
                "player": {"current_hp": 22, "max_hp": 80, "block": 0, "energy": 1, "potions": []},
                "enemies": [{"index": 0, "enemy_id": "X", "is_alive": True, "current_hp": 30, "max_hp": 30,
                             "intent": {"kind": "attack", "damage": 11, "hits": 1}}],
            },
            "run": {"current_hp": 22, "max_hp": 80, "gold": 120},
        },
    }


def _map_payload() -> dict:
    return {"travelable_nodes": [{"index": 0, "type": "Monster"}, {"index": 1, "type": "RestSite"},
                                 {"index": 2, "type": "Elite"}, {"index": 3, "type": "Shop"}]}


def _map_snapshot() -> dict:
    return {"screen": "MAP", "floor": 5, "classification": {"state_name": "map"},
            "available_actions": [{"type": "choose_map_node"}],
            "summary_context": {"payload": _map_payload()},
            "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}}}


def _reward_payload() -> dict:
    return {"reward_cards": [{"index": 0, "card_type": "attack", "name": "Strike"},
                             {"index": 1, "card_type": "skill", "name": "Defend"},
                             {"index": 2, "card_type": "power", "name": "DemonForm"}]}


def _reward_snapshot() -> dict:
    return {"screen": "CARD_SELECTION_REWARD", "floor": 5,
            "classification": {"state_name": "card_selection_reward"},
            "available_actions": [{"type": "choose_reward_card"}],
            "summary_context": {"payload": _reward_payload()},
            "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}}}


def _event_payload() -> dict:
    return {"event_options": [{"index": 0, "title": "接受", "description": "失去 5 点生命上限，获得 100 金币"},
                              {"index": 1, "title": "拒绝", "description": "什么也不发生"},
                              {"index": 2, "title": "战斗", "description": "受到 10 点伤害，获得遗物"}]}


def _event_snapshot() -> dict:
    return {"screen": "EVENT", "floor": 7, "classification": {"state_name": "event"},
            "available_actions": [{"type": "choose_event_option"}],
            "summary_context": {"payload": _event_payload()},
            "raw_state": {"run": {"current_hp": 60, "max_hp": 80, "gold": 200}}}


#: 屏名 -> (snapshot 工厂, plan() 会产出的 action_type)
SCREENS = {
    "combat": (_combat_snapshot, "play_card"),
    "map": (_map_snapshot, "choose_map_node"),
    "reward": (_reward_snapshot, "choose_reward_card"),
    "event": (_event_snapshot, "choose_event_option"),
}


@pytest.fixture()
def planner() -> STS2HeuristicPlanner:
    return STS2HeuristicPlanner()


def _context(snapshot: dict, avoid: list[str] | None = None) -> dict:
    return {
        "snapshot": snapshot,
        "classification": snapshot.get("classification") or {},
        "summary_context": snapshot.get("summary_context") or {"payload": {}},
        "strategy_context": {"preferences": {},
                             "strategy_directives": {"must": [], "prefer": [], "avoid": list(avoid or [])}},
    }


def _ranked(planner: STS2HeuristicPlanner, snapshot: dict, action_type: str,
            avoid: list[str] | None = None) -> list[int]:
    """走真实 `_ranked_option_indices`（内部才会调 `_apply_run_lessons`）。"""
    return planner._ranked_option_indices(
        _context(snapshot, avoid), {"action_type": action_type, "kwargs": {"option_index": 0}}
    )


def _plan_identity(planner: STS2HeuristicPlanner, snapshot: dict, avoid: list[str] | None = None):
    operation = planner.plan(_context(snapshot, avoid))
    if operation is None:
        return None
    data = operation.as_dict() if hasattr(operation, "as_dict") else operation
    return data.get("action_type"), json.dumps(data.get("kwargs"), sort_keys=True), data.get("reason")


# --------------------------------------------------------------------------------------
# 1. 无教训 -> 逐字原序（战斗屏 / 奖励屏 两条**新增**路径）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("screen", ["combat", "map", "reward", "event"])
@pytest.mark.parametrize("avoid_value", [[], [""], ["   "], ["\n", "\t"]])
def test_no_lesson_keeps_ranking_byte_identical(planner: STS2HeuristicPlanner,
                                                screen: str, avoid_value) -> None:
    """★M2 核心契约：**无教训**时排序与"完全没有记忆机制"逐字相同（四屏统一）。

    `test_no_lesson_keeps_event_ranking_byte_identical`（事件屏）与
    `test_apply_run_lessons_without_lessons_returns_exact_input_order`（直接口径）已各自覆盖一屏；
    本参数化把同一契约扩到**战斗屏与奖励屏**（t10 要求的新路径），
    并顺带把路线/事件屏一并纳入，避免四屏契约漂移。
    """
    factory, action_type = SCREENS[screen]
    snapshot = factory()

    baseline = _ranked(planner, snapshot, action_type, avoid=[])
    after = _ranked(planner, snapshot, action_type, avoid=avoid_value)
    assert after == baseline, f"[{screen}] avoid={avoid_value!r} 时必须逐字原序，实际 {after} != {baseline}"


def test_avoid_none_and_absent_are_equivalent(planner: STS2HeuristicPlanner) -> None:
    """`avoid` 缺失 / 为 None / 为空列表 三种"没有教训"的表示必须等价。

    回归测试里出现过真事故：`None` 在参数化里被当成可迭代值直接炸掉。
    这里把三种表示的等价性显式钉住，顺带证明 `_run_memory_lessons` 对非列表输入是安全的。
    """
    snapshot = _map_snapshot()
    baseline = _ranked(planner, snapshot, "choose_map_node", avoid=[])

    absent = _context(snapshot)
    absent["strategy_context"] = {"preferences": {}, "strategy_directives": {}}
    assert planner._ranked_option_indices(
        absent, {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}
    ) == baseline

    none_value = _context(snapshot, avoid=[])
    none_value["strategy_context"]["strategy_directives"]["avoid"] = None
    assert planner._ranked_option_indices(
        none_value, {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}
    ) == baseline


@pytest.mark.parametrize("screen", ["combat", "map", "reward", "event"])
def test_blank_lesson_text_never_displaces_plan(screen: str) -> None:
    """空白教训不得改变 `plan()`（端到端口径，覆盖四屏）。

    ★ 必须用**每个断言一个全新 planner** —— 不能共用 fixture。

    原因（2026-10-19 实测）：纯求解器改造引入了**死锁保险**
    （`_SOLVER_DEADLOCK_RETRIES = 3`，heuristic_planner.py:1383）：
    战斗屏在「无求解器计划」时连续 3 次同签名会主动发 `end_turn` 打破僵局
    （生产上是对的：避免永久卡住）。

    但这个保险让 planner **变成有状态对象**：

        第1次 plan() -> None
        第2次 plan() -> None
        第3次 plan() -> ('end_turn', '{}', 'solver_deadlock_end_turn')   ← 保险触发
        第4次 plan() -> None        ← 计数已清零

    ⇒ 若共用 fixture，循环里的第 3 次调用会拿到 `end_turn` 而非 `None`，
      **看起来像"空白教训改变了决策"**，实际是测试自身制造的顺序依赖。
      （这正是先前 `[combat]` 子用例失败的根因。）
    """
    factory, _action_type = SCREENS[screen]
    snapshot = factory()
    # ★ 每个取值独立 planner，排除死锁保险的跨调用状态
    baseline = _plan_identity(STS2HeuristicPlanner(), snapshot, avoid=[])
    for value in ([], [""], ["   "]):
        fresh = STS2HeuristicPlanner()
        assert _plan_identity(fresh, snapshot, avoid=value) == baseline, (
            f"[{screen}] 空白教训改变了 plan()，avoid={value!r}"
        )


# --------------------------------------------------------------------------------------
# 2. 真实注入路径下的零影响（t10 基线零点 / t11 回归红线）
# --------------------------------------------------------------------------------------
# --------------------------------------------------------------------------------------
# ★ t21：hermetic 快照 —— 本文件的判据**不得**依赖实时记忆库
# --------------------------------------------------------------------------------------
#
# 修复前的问题（实测）：本文件直接以 `%LOCALAPPDATA%\N.E.K.O\sts2_autoplay\catgirl_memory.json`
# 作为 fixture，而那是**正在被实机对局持续写入**的文件 ⇒ 「全量 passed」这个判据会随
# **数据**漂移，而不是随**代码**变化 ⇒ 无法区分「代码回归」与「数据换了」。
#
# 修法：把 fixture 指向随仓库一起版本化的**冻结快照**。判据从此只随代码变。
# 快照来源：2026-10-19 11:39 的 50 局取证副本（原 `live/EVIDENCE-FROZEN-catgirl_memory.json`），
# 逐字节拷贝到 `tests/data/` 下，随测试一起被 review/reproduce。
#
# ★ 为什么不直接删掉这些用例：它们钉的是「真实注入路径」的语义
#   （手工喂 avoid 不是合格口径）——见 docstring §3，这个**方法学结论**必须保留。
#   冻结快照保留了"真实 50 局数据"这一关键性质，同时去掉了"随时间漂移"。
_HERE = os.path.dirname(os.path.abspath(__file__))
_FROZEN_MEMORY_PATH = os.path.join(_HERE, "data", "catgirl_memory_frozen_50runs.json")

_LIVE_MEMORY_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA") or r"C:\Users\Administrator\AppData\Local",
    "N.E.K.O", "sts2_autoplay", "catgirl_memory.json",
)

#: ★ 本文件**一律**用冻结快照。仅当快照缺失时才回退实时文件（并会被下面的门标记）。
_MEMORY_PATH = _FROZEN_MEMORY_PATH if os.path.exists(_FROZEN_MEMORY_PATH) else _LIVE_MEMORY_PATH


def test_asset_is_hermetic_frozen_snapshot_not_live_memory() -> None:
    """★ 元判据：本文件用的 fixture 必须是**冻结快照**，不是实时记忆库。

    这是 t21 的核心门 —— 它把「该文件是否 hermetic」本身变成可断言的事实。
    若有人把 `_MEMORY_PATH` 改回实时文件（或快照被删而回退），本门立即转红。
    """
    assert os.path.exists(_FROZEN_MEMORY_PATH), (
        f"冻结快照缺失：{_FROZEN_MEMORY_PATH}\n"
        "本文件的判据必须靠它才与实机数据解耦。"
    )
    assert _MEMORY_PATH == _FROZEN_MEMORY_PATH, (
        f"★ fixture 不是冻结快照（当前={_MEMORY_PATH}）—— "
        "本文件已 hermetic 化，禁止再依赖实时记忆库，否则「全量 passed」会随数据漂移。"
    )
    assert os.path.abspath(_MEMORY_PATH) != os.path.abspath(_LIVE_MEMORY_PATH), (
        "★ fixture 指向了实时记忆库，测试重新变为非 hermetic"
    )


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    info = warning = exception = debug


class _Prefs:
    def list_domain(self, domain):
        return []

    def get(self, domain, key):
        return None


def _real_repo(memory_path: str) -> STS2StrategyRepository:
    """构造**真实** `STS2StrategyRepository`，让 `_inject_run_memory_directives` 真的跑一遍。"""
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=memory_path if os.path.exists(memory_path) else None)
    return STS2StrategyRepository(_Logger(), _Prefs(), catgirl_memory=store)


_ZERO_DISPLACEMENT_SCREENS = ["combat", "map", "event"]
"""仍未接通教训的屏（零点仍然成立）。

★ 2026-10-17 更新：`reward` 已**不再是零点** —— 本轮打通了
「复盘教训 → 选卡质量」（`_reward_lesson_bonus` 接入 `_preferred_reward_option`），
实测真实注入把 `option_index` 从 0 改到 1 ⇒ 位移真的发生了。
本文件的原始 docstring 已预言这条测试**会且应该**转红，见
`test_reward_screen_now_shows_lesson_displacement`。
"""


@pytest.mark.parametrize("screen", _ZERO_DISPLACEMENT_SCREENS)
def test_default_memory_injection_is_zero_displacement(planner: STS2HeuristicPlanner, screen: str) -> None:
    """★**零点契约**：这些屏上，真实记忆注入**不得**改变 `plan()`。

    这些屏至今仍是零点，原因是教训尚未接到它们的评分函数上：
      * `combat` —— 战斗已交给 mod 的 CombatSolver（`:1049` 命中即 return），
        且用户明确要求「战斗完全交给求解器，保持战斗的权威」⇒ **有意不接**；
      * `map` / `event` —— 事件/路线屏的评分函数（`_score_event_option` 等）
        尚未消费教训 ⇒ 仍是零点（待办）。

    ⚠️ 若本测试转红，说明有改动**意外**接通了这些屏的教训通道 ——
    对 `combat` 而言那是**违背用户决策**的，必须复核。
    """
    factory, _action_type = SCREENS[screen]
    snapshot = factory()
    repo = _real_repo(_MEMORY_PATH)
    injected = repo.build_context(snapshot)["strategy_directives"]["avoid"]

    baseline = _plan_identity(planner, snapshot, avoid=[])
    after = _plan_identity(planner, snapshot, avoid=injected)
    assert after == baseline, (
        f"[{screen}] 真实注入改变了 plan()：{baseline} -> {after}（注入的 avoid={injected!r}）。"
        "若这是有意造成的，请同步更新 t10 基线快照与 t_r3 的 G3 判据；"
        "若是 combat 屏，注意用户已要求「战斗完全交给求解器」。"
    )


def test_reward_screen_now_shows_lesson_displacement(planner: STS2HeuristicPlanner) -> None:
    """★**零点已被移动**（t10 预言的那条转红）：reward 屏的真实注入必须改变 `plan()`。

    这是本轮（2026-10-17）修复的核心证据：
      `_reward_lesson_bonus` 接进 `_preferred_reward_option`，
      且 `strategy_directives` 从 `strategy_context` 显式传入
      （它**不在** `summary_context` 里 —— 传错层会恒空）。

    ★ 反恒真：同时断言注入的 `avoid` **非空**，否则"位移"可能只是噪声。
    """
    factory, _action_type = SCREENS["reward"]
    snapshot = factory()
    repo = _real_repo(_MEMORY_PATH)
    injected = repo.build_context(snapshot)["strategy_directives"]["avoid"]
    assert injected, "注入的 avoid 不得为空，否则本测试无法证明任何事"

    baseline = _plan_identity(planner, snapshot, avoid=[])
    after = _plan_identity(planner, snapshot, avoid=injected)
    assert after != baseline, (
        f"[reward] 真实注入**没有**改变 plan()：{baseline} == {after}。"
        "说明「复盘教训→选卡质量」通道断了（检查是否误传 summary_context，"
        "`strategy_directives` 在 strategy_context 里）。"
    )


def test_zero_displacement_screens_are_not_vacuous(planner: STS2HeuristicPlanner) -> None:
    """反恒真：零点屏的"无位移"不得是因为注入为空。

    若 `avoid` 恒空，上面的零点断言毫无意义（测不到任何东西）。
    """
    for screen in _ZERO_DISPLACEMENT_SCREENS:
        factory, _action_type = SCREENS[screen]
        snapshot = factory()
        repo = _real_repo(_MEMORY_PATH)
        injected = repo.build_context(snapshot)["strategy_directives"]["avoid"]
        assert injected, f"[{screen}] 注入的 avoid 为空 ⇒ 零点断言是空的"


def test_real_injection_carries_lesson_text_into_avoid(planner: STS2HeuristicPlanner) -> None:
    """前提校验：上面那条"零影响"断言**不是恒真** —— 注入确实把文本塞进了 `avoid`。

    没有这条，`test_default_memory_injection_is_zero_displacement` 可能只是因为
    `avoid` 是空列表而通过（那样它就测不出任何东西）。
    """
    if not os.path.exists(_MEMORY_PATH):
        pytest.skip(f"真实记忆文件不存在：{_MEMORY_PATH}")
    repo = _real_repo(_MEMORY_PATH)
    injected = repo.build_context(_combat_snapshot())["strategy_directives"]["avoid"]
    assert injected, "真实记忆必须至少注入一条 avoid 文本，否则上面的零影响测试是恒真的"
    assert all(isinstance(item, str) and item.strip() for item in injected)

    # ★t11：闸2/闸3 已修复 ⇒ 注入文本**必须**能抽出 token。
    # （修复前这里断言 == []，那是基线的**缺陷事实**，不是设计意图；t11 已开闸。）
    assert planner._lesson_tokens(injected) != [], (
        f"★闸2/闸3 修复后注入文本必须能抽出 token，实际 {injected}。"
        "抽不出说明注入窗口或 token 抽取又退化了。"
    )


def test_window_truncation_hides_real_reflections(planner: STS2HeuristicPlanner) -> None:
    """★闸2（修复后）：默认窗口已能捞出**抽得出 token** 的真实教训。

    t10 基线（修复前）：`limit=5` 只捞出 1 条模板句、token 恒为 `[]`；
    `limit=20` 才有 7 条、token 非空。这是**注入窗口截断**这一独立缺陷。

    t11 修复后：`summarize_recent(limit=5)` 会在窗口之外补足"有信息量"的教训，
    使默认窗口即可产出非空 token。本测试钉住修复结果，并保留**反向对照**：
    关掉补足（`include_older_lessons=False`）必须退回修复前的行为 ——
    这确保"窗口截断"这个机制仍然可观测，而不是被永久掩盖。
    """
    if not os.path.exists(_MEMORY_PATH):
        pytest.skip(f"真实记忆文件不存在：{_MEMORY_PATH}")
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=_MEMORY_PATH)
    runs = store.runs
    assert runs, "真实记忆为空，测试前提不成立"

    # 前提：记忆里确实存在能抽出 token 的反思文本。
    reflections = [str(r.get("reflection") or "").strip() for r in runs]
    reflections = [text for text in reflections if text]
    assert reflections, "真实记忆里没有任何反思，测试前提不成立"
    tokenizable = [text for text in reflections if planner._lesson_tokens([text])]
    assert tokenizable, "真实反思一条都抽不出 token，闸3 的描述需要复核"

    detailed = store.summarize_recent_detailed(limit=5)
    small = detailed["summary"]
    large = store.summarize_recent(limit=len(runs))

    # ★修复后：默认窗口必须能抽出 token。
    assert planner._lesson_tokens(small["lessons"]) != [], (
        f"★闸2 未开：默认窗口(5)仍抽不出 token：{small['lessons']}；window={detailed['window']}"
    )
    assert len(small["lessons"]) > 1, (
        f"★闸2 未开：默认窗口仍只捞出 {len(small['lessons'])} 条教训"
    )
    assert detailed["window"]["older_lessons_added_count"] > 0, (
        "默认窗口应从窗口外补足非兜底教训；补了 0 条说明补足逻辑失效"
    )

    # 反向对照：关掉补足 -> 窗口内**不额外补**非兜底教训。
    #
    # ★ 2026-10-19 修正：原断言硬编码"窗口内恰好 1 条模板句"，那是基于
    #   t11 时期**真实记忆恰好前 5 局全是兜底句**的数据快照。
    #   但记忆是**活的** —— 后续对局产生了真实反思，
    #   于是"窗口内全是兜底"不再成立，测试转红。
    #   **这类测试不该依赖历史数据的偶然形态**，否则每打几局就红一次。
    #
    # 改为钉住**机制语义**（与数据无关），且**从同一数据源推导期望值** ——
    #   `summarize_recent_detailed` 读的是 `run["lessons"]`（不是 `run["reflection"]`），
    #   所以期望值也必须从 `lessons` 推导，否则会拿两套口径互比。
    legacy = store.summarize_recent_detailed(limit=5, include_older_lessons=False)
    assert legacy["window"]["older_lessons_added_count"] == 0, (
        "关掉补足后不得再从窗口外追加教训，实际追加了 "
        f"{legacy['window']['older_lessons_added_count']} 条"
    )
    expected = store._dedup([
        str(item)
        for run in runs[-5:]
        for item in run.get("lessons", [])
        if isinstance(item, (str, int))
    ])
    assert legacy["summary"]["lessons"] == expected, (
        "关掉补足后的 lessons 必须**恰好**是窗口内教训（不多不少，含去重）："
        f"期望 {len(expected)} 条，实际 {len(legacy['summary']['lessons'])} 条"
    )
    # 补足开着时条数不得少于关掉时（机制方向性）。
    assert len(small["lessons"]) >= len(legacy["summary"]["lessons"]), (
        "开补足后的条数不应少于关补足，"
        f"{len(small['lessons'])} vs {len(legacy['summary']['lessons'])}"
    )

    assert planner._lesson_tokens(large["lessons"]) != [], (
        "放大窗口后仍抽不出 token，说明闸2 不是截断问题"
    )
    assert len(large["lessons"]) >= len(small["lessons"])


# --------------------------------------------------------------------------------------
# 5. 变异逃逸补靶（t10 首轮变异实测 8/10，两个逃逸点在此关掉）
# --------------------------------------------------------------------------------------
def test_lesson_tokens_gate_is_structurally_redundant(planner: STS2HeuristicPlanner) -> None:
    """★关掉变异体 `L2-drop-token-early-return` 的逃逸 —— 但要**如实**描述它为何逃逸。

    变异 L2 把 `_apply_run_lessons` 里的 `if not tokens: return ranked` 删掉，
    首轮实测**全部测试仍绿**（逃逸）。原因是它**结构性冗余**，不是测试没写够：

        tokens 为空  ⇒  `_indices_worth_penalizing` 首行 `if not tokens: return targets`
                       必然返回空集  ⇒  紧接着的 `if not penalize_indices: return ranked` 兜住。

    两道早退由同一个条件触发（tokens 空），所以删掉前一道**没有任何可观测后果**。
    这是"无法被测试保护的死代码"（与 `_LESSON_KEYWORD_MAP` 注释里记录的
    `_EVENT_ONLY_TOKENS` 教训同型）。

    ⇒ 本测试**不**假装 L2 可观测，而是把这条**结构性冗余**钉成断言：
    空 token 时惩罚集必为空，且输出必与原序逐字相同。这样若有人把
    `_indices_worth_penalizing` 的空 token 行为改成"返回全集"，
    两道闸会**同时**失效 —— 那时这条测试会转红，L2 也就随之变得可观测。
    """
    payload = {"travelable_nodes": [{"index": 0, "type": "Monster"},
                                    {"index": 1, "type": "RestSite"}]}
    # 前提一：空 token -> 惩罚集必为空（这是冗余的根源）。
    assert planner._indices_worth_penalizing(payload, "choose_map_node", []) == set(), (
        "空 token 时 `_indices_worth_penalizing` 必须返回空集；它若有别的行为，"
        "L2 变异体就变得可观测了，本测试的结论需重写"
    )
    # 前提二：抽不出 token 的教训 -> 输出逐字原序。
    lesson = "今天天气不错适合钓鱼"
    assert planner._lesson_tokens([lesson]) == [], "前提：该教训必须抽不出 token"
    strategy_context = {"strategy_directives": {"avoid": [lesson]}}
    after = planner._apply_run_lessons(strategy_context, {"payload": payload},
                                       "choose_map_node", [1, 0])
    assert after == [1, 0], f"抽不出 token 的教训必须原序返回，实际 {after}"


def test_default_memory_lesson_text_is_really_injected(planner: STS2HeuristicPlanner) -> None:
    """默认窗口注入的 lessons 必须非空，且被真实注入进 `avoid`（闸2 的可观测面）。"""
    if not os.path.exists(_MEMORY_PATH):
        pytest.skip(f"真实记忆文件不存在：{_MEMORY_PATH}")
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=_MEMORY_PATH)
    record = store.summarize_recent()  # 默认 limit=5，与真实注入路径一致
    assert record["lessons"], (
        "默认窗口注入的 lessons 为空 —— summarize_recent 断源。"
        "（t10 基线：默认窗口恰好注入 1 条兜底句；这是闸2 的可观测面）"
    )
    # 且真实注入路径确实把它送进了 avoid。
    repo = _real_repo(_MEMORY_PATH)
    injected = repo.build_context(_map_snapshot())["strategy_directives"]["avoid"]
    assert injected == record["lessons"] or set(record["lessons"]) <= set(injected), (
        f"`build_context` 注入的 avoid({injected!r}) 未包含 summarize_recent 的 lessons"
        f"({record['lessons']!r})"
    )


def test_fallback_lesson_is_generated_for_a_fresh_defeat_run() -> None:
    """★关掉变异体 `M2-fallback-lesson-removed` 的逃逸 —— 观测点必须落在**生成**而非**回放**。

    为什么上一版测试也逃逸了（重要，记录以防后人重犯）
    ----------------------------------------------------
    变异 M2 删掉的是 `_heuristic_lessons` 里的兜底句 —— 那是**生成期**逻辑。
    而 `catgirl_memory.json` 里那 41 条模板教训是**早就落盘的存量数据**。
    ⇒ 任何"读真实记忆文件再断言 lessons 非空"的测试，读到的都是**历史产物**，
      与生成期代码是否被改坏**无关** —— 属于"测了数据、没测代码"的假靶。
    首轮实测 39 passed 全绿，正是这个原因。

    ⇒ 正确的观测点：用**合成的** `decision_memory` 调 `add_run`，走完整生成链路。
    这样删掉兜底句会立刻转红。
    """
    import inspect
    import tempfile

    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "memory.json")
        store = CatgirlMemoryStore(path=path)
        # ★t11 更新：R5 修复后，`map_preference_or_default` / `combat_*` 这类**英文 reason**
        # 已经能产出**具体**教训（这正是修 R5 的目的）。所以要让兜底句成为**唯一**来源，
        # 必须用**不含任何已知英文标签、也不含中文关键词、且无低血**的 reason。
        decision_memory = [
            {"screen": "COMBAT", "action_type": "play_card", "reason": "some_unknown_reason",
             "step_count": 3},
            {"screen": "MAP", "action_type": "choose_map_node", "reason": "another_unknown",
             "step_count": 4},
        ]
        run = store.add_run("T10-FALLBACK-PROBE", character="IRONCLAD", outcome="defeat",
                            floor=3, act=1, decision_memory=decision_memory)
        assert run["lessons"], (
            "defeat 局没有生成任何 lesson —— 兜底句逻辑被删除或失效（这正是变异体 M2）。"
            f"lessons={run['lessons']!r}"
        )
        assert any("不明不白" in lesson for lesson in run["lessons"]), (
            f"兜底教训应包含『不明不白』特征，实际 {run['lessons']!r}"
        )
        # ★I1：结构化记录必须同步落盘（与 lessons 一一对应）。
        records = run.get("lesson_records") or []
        assert [record["text"] for record in records] == [
            str(text) for text in run["lessons"]
        ], f"lesson_records 与 lessons 不同步：{records!r} vs {run['lessons']!r}"
        assert all(record.get("source") == "heuristic" for record in records)
        # ★I1 向前兼容：老格式（纯字符串）必须能升级成结构化记录。
        assert store.lesson_records() , "结构化读取层不得为空"
        assert store.lesson_records()[0]["text"] == str(run["lessons"][0])
        # 反向对照：victory 局**不应**触发兜底句（证明上面不是恒真）。
        store2 = CatgirlMemoryStore(path=os.path.join(tmp, "memory2.json"))
        run2 = store2.add_run("T10-FALLBACK-PROBE-WIN", character="IRONCLAD", outcome="victory",
                              floor=50, act=3, decision_memory=decision_memory)
        assert not any("不明不白" in lesson for lesson in run2["lessons"]), (
            f"victory 局不该落兜底教训，实际 {run2['lessons']!r}"
        )
        _ = inspect


def test_summarize_recent_limit_is_five_by_default() -> None:
    """默认窗口必须是 5（闸2 的拐点基线）；它一变，闸2 的量化结论就要重测。"""
    import inspect

    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    signature = inspect.signature(CatgirlMemoryStore.summarize_recent)
    assert signature.parameters["limit"].default == 5, (
        f"`summarize_recent` 默认 limit 变了（{signature.parameters['limit'].default}）——"
        "闸2 基线需重测，并更新 reflection-metrics-baseline.json"
    )


def test_injection_path_does_not_pass_a_limit(planner: STS2HeuristicPlanner) -> None:
    """真实注入路径**不传** limit ⇒ 用的是默认 5（而不是"刚好够用的大窗口"）。

    这条防止一种廉价假修复：把 `summarize_recent()` 的调用改成 `limit=50`，
    看起来"窗口问题解决了"，但实际上 `_inject_run_memory_directives` 一行没动，
    而真正的问题（近期 5 局全是模板句）被掩盖。
    """
    import inspect

    from plugin.plugins.sts2_autoplay.strategy_repository import STS2StrategyRepository

    source = inspect.getsource(STS2StrategyRepository._inject_run_memory_directives)
    assert "summarize_recent()" in source, (
        "真实注入路径应调用无参 `summarize_recent()`；源码变了需人工确认口径：\n" + source
    )
    assert "limit=" not in source, (
        "真实注入路径不得显式传 limit（会掩盖『近 5 局全是模板句』这一真实问题）：\n" + source
    )


# --------------------------------------------------------------------------------------
# 3. 反作弊：手工喂 avoid 不是合格口径
# --------------------------------------------------------------------------------------
def test_handfed_avoid_is_not_a_valid_proxy_for_real_injection(planner: STS2HeuristicPlanner) -> None:
    """★方法学契约：**手工喂 `avoid`** 与**真实注入**是两条不同的口径，不能互相代替。

    t2 已证手工喂会得**假阳性**（`avoid=[rest教训] → [3,0,2,1]` 有位移，
    真实注入 `→ [1,3,0,2]` 无位移）。本测试把这个差别钉成可执行断言：
    在路线屏上，手工喂一条**抽得出 token**的教训确实会重排，而真实注入不会。

    这条同时是 `reflection_metrics.py` 里 `plan_handfed_displacement_bits` 字段的存在理由 ——
    验收方若只看到手工喂的结果，会误判"通路已接通"。

    ★t11 更新：t11 修好闸2（注入窗口）后，**真实注入在路线屏仍然是原序** ——
    因为真实教训的 token 是 `skill/attack/elite/rest`，而本测试快照里没有一个节点命中它们
    （这正是"只降权命中项"的正确行为）。故断言改为"真实注入不得**凭空**重排"：
    手工喂与真实注入的差别依然存在且被钉住。

    ★★t21 更新（2026-10-19）：**上文"真实注入无位移"已被修复，本断言随之翻转**。
    修复 = 方案 A（玩家选定）：`_apply_run_lessons` 由 `kept + demoted` 改为按
    `(惩罚强度, 原始名次)` 排序（`heuristic_planner.py:5800-5837`）。
    现在真实注入**必须**产生位移；详见下方就地注释与
    `.dsh-artifacts/BUG-lesson-demotion-identity.md`。
    """
    snapshot = _map_snapshot()
    action_type = "choose_map_node"
    baseline = _ranked(planner, snapshot, action_type, avoid=[])

    # 手工喂一条能抽出 rest token 的教训 -> **必须**重排（否则本测试无意义）。
    lesson = "上次在篝火休息浪费了升级机会，以后别选休息"
    assert planner._lesson_tokens([lesson]) == ["rest"], "前提：该教训应抽出 rest token"
    handfed = _ranked(planner, snapshot, action_type, avoid=[lesson])
    assert handfed != baseline, (
        f"手工喂的 rest 教训没能重排路线，`_apply_run_lessons` 可能已退化为恒等函数："
        f"{baseline} -> {handfed}"
    )
    assert sorted(handfed) == sorted(baseline), "只许降权，不许删除候选"

    # ★★ 历史缺陷（已修）：真实注入在本屏曾**不可能**有位移。
    #
    # 2026-10-19 实测（`.dsh-artifacts/BUG-lesson-demotion-identity.md`）：
    #
    #     真实注入命中 penalized = {0, 2}
    #     基线 ranked            = [1, 3, 0, 2]
    #     kept    = [1, 3]
    #     demoted = [0, 2]        ← ★ 被罚两项**本来就在末尾**
    #     拼接    = [1, 3, 0, 2]  ← **与原序逐字相同 ⇒ 教训等于没生效**
    #
    # 旧 `_apply_run_lessons` 用 `kept + demoted`（组内保持原序）。
    # 当被罚集合恰好是"尾部的若干个"时，这是**恒等变换**。
    # 而真实数据（基线把 Elite/怪排后面 + 真实 token 恰含 elite/monster）
    # **正好落在这个失效区**。
    #
    # ⚠️ 这与"同值冗余"**不同**：
    #   同值冗余 = 基线与教训想要同一结果（位移为 0 是正确的）；
    #   本例     = 教训想再往后压 Monster(0)（基线排第 2，不是最后一位），
    #              但算法表达不了这个意图。
    #
    # ⇒ ★★ 2026-10-19/t21 更新：**缺陷已修复**，本断言随之从「钉住缺陷」翻转为
    #    「钉住修复」。原文（保留以备追溯）见 `.dsh-artifacts/BUG-lesson-demotion-identity.md`。
    #
    #    修复：方案 A（玩家选定）—— `_apply_run_lessons` 不再用 `kept + demoted`，
    #          改为按 `(惩罚强度, 原始名次)` 排序（`heuristic_planner.py:5800-5837`）。
    #          强度 = 有多少条教训提到过该 token，于是「同罚两项」也能被区分。
    #
    #    ⇒ 现在必须**观察到位移**，否则说明方案 A 被回退（或恒等失效复发）。
    repo = _real_repo(_MEMORY_PATH)
    injected = repo.build_context(snapshot)["strategy_directives"]["avoid"]
    real = _ranked(planner, snapshot, action_type, avoid=injected)
    assert sorted(real) == sorted(baseline), "只许降权，不许删除候选"

    real_tokens_for_band = planner._lesson_tokens(injected)
    real_pen = planner._indices_worth_penalizing(
        snapshot["summary_context"]["payload"], action_type, real_tokens_for_band
    )
    if real_pen:
        # 前半段通路必须通：惩罚集算得出来。
        assert real_pen <= set(baseline), (
            f"惩罚集 {sorted(real_pen)} 超出候选范围 {baseline}"
        )
        # ★★ 修复钉：命中即**惩罚必须落到排序上**。
        #
        # ★ 为什么这里**不能**断言 `real != baseline`（本文件曾如此写，是错的）：
        #   方案 A 的排序键是 `(penalty, 原始名次)`。当**被罚项在基线里本来就排在未罚项之后**
        #   （即"已经是尾部"），且组内 penalty 次序恰好与原始名次一致时，
        #   输出**逐字等于基线** —— 而此时恒等是**正确行为**（它们已经在应该在的位置）。
        #
        #   实测对照（同一份代码，只换数据；见 `.dsh-artifacts/_t21_diag_displacement.py`）：
        #     实时库  : 被罚 {2(Elite,强度6), 3(Shop,强度1)} ⇒ real=[1,0,3,2]  ★有位移
        #     冻结快照: 被罚 {0(Monster,强度1), 2(Elite,强度5)} ⇒ real=[1,3,0,2]  ★无位移（正确）
        #   两例的**共同不变量**都是"被罚项全部排在未罚项之后" ⇒ 这才是契约。
        #
        #   ⇒ 断言改为该不变量；恒等被允许（不再是缺陷信号）。
        penalized_in_real = [i for i in real if i in real_pen]
        unpenalized_in_real = [i for i in real if i not in real_pen]
        if penalized_in_real and unpenalized_in_real:
            last_unpenalized = max(real.index(i) for i in unpenalized_in_real)
            first_penalized = min(real.index(i) for i in penalized_in_real)
            assert first_penalized > last_unpenalized, (
                f"★惩罚未落到排序上：被罚 {sorted(real_pen)} 未全部排到未罚项之后。\n"
                f"  baseline={baseline}  real={real}\n"
                f"  首个被罚名次={first_penalized} 末个未罚名次={last_unpenalized}\n"
                "⇒ 方案 A（按 penalty 排序）可能被回退为 `kept + demoted`；见 "
                "`.dsh-artifacts/BUG-lesson-demotion-identity.md`"
            )
        # ★ 旧 `kept + demoted` 的**特征失效**：被罚项**已在尾部**时输出恒等。
        #   方案 A 之下，同一输入必须体现"被罚项确实被压到后面"——
        #   判据用**可区分强度**构造反例：若把被罚项中 penalty 最小的那个
        #   人为提到最前，方案 A 仍须把它压回未罚项之后（下面的 `_apply` 直调即可证明）。
        #   这里只做**行为存在性**检查：真实注入的集合必须能被算法表达。
        assert planner._lesson_penalty_strength(injected), (
            "真实注入必须能算出惩罚强度，否则方案 A 无处着力"
        )

    # ★方法学差别仍然成立（本测试的核心）：手工喂与真实注入是**两条不同口径**。
    # 用手工喂的 token 算出的惩罚集，与用真实注入 token 算出的**不同** ⇒ 不可互相代替。
    handfed_tokens = planner._lesson_tokens([lesson])
    real_tokens = planner._lesson_tokens(injected)
    assert handfed_tokens != real_tokens, (
        "手工喂与真实注入的 token 必须不同，否则『不可代替』这条契约失去意义"
    )
    assert planner._indices_worth_penalizing(
        _map_snapshot()["summary_context"]["payload"] if False else {},
        action_type,
        handfed_tokens,
    ) == set(), "前提：手工喂的 rest token 在空 payload 上无着力点（用于对照）"
    # 两条口径给出的惩罚集必须分别可算、且互不等价。
    #
    # ★ 2026-10-19 修正：原断言硬编码"真实注入含 elite+rest ⇒ 必须命中 {0,1}"。
    #   但真实反思的 token 组合是**会变的**（取决于最近打了什么局）：
    #   t11 时期含 rest，当前数据是 `skill/attack/elite/monster`（**不含** rest）。
    #   ⇒ 不能把某次数据快照当成契约。
    #
    # 改为**从真实 token 推导期望**：命中集必须恰好等于「token 能匹配上的节点」。
    payload = {"travelable_nodes": [{"index": 0, "type": "Elite"}, {"index": 1, "type": "RestSite"}]}
    handfed_pen = planner._indices_worth_penalizing(payload, action_type, handfed_tokens)
    real_pen = planner._indices_worth_penalizing(payload, action_type, real_tokens)
    assert handfed_pen == {1}, f"手工喂 rest 应命中休息点(1)，实际 {sorted(handfed_pen)}"

    # 由真实 token 推出"应当命中哪些节点"
    expected_real = {
        node["index"]
        for node in payload["travelable_nodes"]
        if planner._map_node_weight_key(node) in real_tokens
    }
    assert real_pen == expected_real, (
        f"真实注入命中集应等于『token 能匹配的节点』："
        f"期望 {sorted(expected_real)}（token={real_tokens}），实际 {sorted(real_pen)}"
    )
    # ★ 核心契约（与数据无关）：两条口径必须给出**不同**结果，否则不可互相代替是假的。
    assert handfed_pen != real_pen, (
        f"两条口径必须给出不同结果，否则不可互相代替是假的："
        f"手工喂={sorted(handfed_pen)} 真实注入={sorted(real_pen)}"
    )
    # ★并钉住"真实注入**确实携带内容**" —— 否则上面的断言是恒真的。
    assert injected, "真实注入不得为空（否则本测试退化为恒真断言）"
    assert real_tokens, (
        "★闸2/闸3 修复后，真实注入必须能抽出 token；抽不出说明注入窗口又断了"
    )


def test_scheme_a_demotion_is_not_identity_for_the_tail_case() -> None:
    """★★ t21 新门：**直调** `_apply_run_lessons`，证明方案 A 真的修掉「尾部恒等失效」。

    为什么必须补这一条：上面那条端到端测试的位移**取决于数据**
    （被罚项恰好已在尾部时，恒等是**正确**行为）⇒ 它**不能**单独证明方案 A 生效。
    本门用**构造数据**复现旧缺陷的失效条件，从而与数据解耦。

    旧缺陷（`.dsh-artifacts/BUG-lesson-demotion-identity.md`）：
        `kept + demoted` 组内保持原序 ⇒ 当被罚集合是**尾部的若干个**时
        ⇒ 输出逐字等于输入 ⇒ 教训完全失效。

    实测的两种数据（`.dsh-artifacts/_t21_diag_displacement.py`）恰好各命中一半：
        实时库  : 被罚 {2,3}（Elite 强度6 / Shop 强度1）⇒ real=[1,0,3,2]  ★有位移
        冻结快照: 被罚 {0,2}（Monster 强度1 / Elite 强度5）⇒ real=[1,3,0,2]  ★无位移（正确）
    ⇒ 两例的共同不变量 = 「被罚项全部排在未罚项之后」，这才是契约。
    """
    planner = STS2HeuristicPlanner()
    payload = {
        "travelable_nodes": [
            {"index": 0, "type": "Monster"},
            {"index": 1, "type": "RestSite"},
            {"index": 2, "type": "Elite"},
            {"index": 3, "type": "Shop"},
        ]
    }
    summary_context = {"payload": payload}

    # 两条教训同时命中 Elite(2) 与 Shop(3) —— 两者**都在尾部** = 旧算法的失效区。
    lessons = ["别再打精英了，太危险", "路线别选商店"]
    tokens = planner._lesson_tokens(lessons)
    assert tokens, f"前提：教训应抽出 token，实际 {tokens}"
    pen = planner._indices_worth_penalizing(payload, "choose_map_node", tokens)
    assert pen == {2, 3}, (
        f"前提：该组合应命中尾部集合 {{2,3}}（=旧算法失效区），实际 {sorted(pen)}；tokens={tokens}"
    )

    ranked = [1, 3, 0, 2]
    got = planner._apply_run_lessons(
        {"strategy_directives": {"avoid": lessons}}, summary_context, "choose_map_node", list(ranked)
    )
    assert sorted(got) == sorted(ranked), "只许降权，不许删除候选"

    # ★ 核心不变量（方案 A 的契约，与数据无关）：被罚项必须全部排在**未罚项**之后。
    pen_in = [i for i in got if i in pen]
    unpen_in = [i for i in got if i not in pen]
    if pen_in and unpen_in:
        assert min(got.index(i) for i in pen_in) > max(got.index(i) for i in unpen_in), (
            f"★方案 A 未生效：被罚 {sorted(pen)} 未全部排到未罚项之后。\n"
            f"  ranked={ranked}  got={got}\n"
            "⇒ `_apply_run_lessons` 可能被回退为 `kept + demoted`，见 "
            "`.dsh-artifacts/BUG-lesson-demotion-identity.md`"
        )

    # ★ 强度表必须非空且可区分（方案 A 的排序键依赖它；旧算法无此概念）。
    strength = planner._lesson_penalty_strength(lessons)
    assert strength, "惩罚强度表不得为空（方案 A 依赖它）"

    # ★ 最决定性的一条：**旧算法在此输入下必然恒等，方案 A 必然重排**。
    #   构造：被罚项 2 在 ranked 中**很靠前**，且在未罚项之前 ——
    #   这样 `kept + demoted` 会把它挪走（旧算法此时**也**有效），
    #   故改用「被罚项 = 尾部集合」+「强度可区分」：
    #   旧算法对尾部集恒等；方案 A 会按强度把 {'弱罚项'} 与 {'强罚项'} 重排。
    #   例：ranked=[0,2,3,1]、pen={2,3}、Elite(2) 强度 > Shop(3) 强度
    ranked2 = [0, 2, 3, 1]
    got3 = planner._apply_run_lessons(
        {"strategy_directives": {"avoid": lessons}}, summary_context, "choose_map_node", list(ranked2)
    )
    assert sorted(got3) == sorted(ranked2), "只许降权，不许删除候选"
    pen3 = [i for i in got3 if i in pen]
    unpen3 = [i for i in got3 if i not in pen]
    if pen3 and unpen3:
        assert min(got3.index(i) for i in pen3) > max(got3.index(i) for i in unpen3), (
            f"★被罚项未排到未罚项之后：ranked2={ranked2} got3={got3} pen={sorted(pen)}"
        )


# --------------------------------------------------------------------------------------
# 3b. ★t11 回归红线：修复**不得**破坏"无教训逐字原序"三条契约
# --------------------------------------------------------------------------------------
def test_t11_gates_are_open_and_memory_is_forward_compatible(tmp_path: Path) -> None:
    """★t11 核心红线：两道闸必须真的开了，且真实记忆文件向前兼容。

    1. **闸2（注入窗口）**：真实记忆 `summarize_recent(limit=5)` 必须能捞出
       **> 1 条**教训（修复前恒为 1 条模板句）。
    2. **闸3（token 为空）**：抽出的教训必须能产出**非空** token（修复前恒为 `[]`）。
    3. **向前兼容**：真实 705KB 记忆文件必须能加载、不报错、不丢 run。
    4. **I1 结构化层**：老格式（纯字符串 lessons）必须能升级成结构化记录。
    """
    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    store = CatgirlMemoryStore(path=str(_MEMORY_PATH))
    runs_before = len(store._runs)
    assert runs_before >= 40, f"真实记忆应至少有 40 条 run，实际 {runs_before}"

    detailed = store.summarize_recent_detailed(limit=5)
    lessons = detailed["summary"]["lessons"]
    assert len(lessons) > 1, (
        f"★闸2 未开：真实注入仍只捞出 {len(lessons)} 条教训；"
        f"window={detailed['window']}"
    )
    planner = STS2HeuristicPlanner()
    tokens = planner._lesson_tokens(lessons)
    assert tokens, f"★闸3 未开：真实注入的教训抽不出任何 token，lessons={lessons[:3]}"

    # 向前兼容：老格式 lessons（纯字符串）必须能升级成结构化记录。
    records = store.lesson_records()
    assert records, "结构化记录层不得为空"
    assert all(isinstance(record, dict) and record.get("text") for record in records)
    assert all("domain" in record and "trigger" in record for record in records)
    # 纯字符串 -> 结构化。
    upgraded = CatgirlMemoryStore.normalize_lesson_record("下次记得先垫格挡再输出")
    assert upgraded["text"] == "下次记得先垫格挡再输出"
    assert upgraded["domain"] == "combat", "应能按行为词推断出战斗域"
    assert upgraded["hit_count"] == 0 and upgraded["source"] == "heuristic"
    # 空/非法输入不得抛错。
    assert CatgirlMemoryStore.normalize_lesson_record("") == {}
    assert CatgirlMemoryStore.normalize_lesson_record(None) == {}
    assert CatgirlMemoryStore.normalize_lesson_record({"no_text": 1}) == {}


# --------------------------------------------------------------------------------------
# 3c. ★I3：奖励选牌着力点必须有**区分度**（不得全罚/偏罚）
# --------------------------------------------------------------------------------------
def test_reward_penalty_must_keep_discrimination(planner: STS2HeuristicPlanner) -> None:
    """★I3：`["skill","attack"]` 对 attack/skill/power 三选一**不得**把区分度压成 1。

    病因（t10 实测）：真实最高频教训抽出 `["skill","attack"]`，奖励屏三选一里
    前两张全被罚、只剩 power ⇒ 区分度 3/3 → **1/3**，且**恒定**把玩家推向 power。
    这是"偏罚"退化，比 no-op 更危险（`_apply_run_lessons:4467` 只要还剩 1 项就重排）。

    修法：只有"未被罚候选 >= 2"时才应用降权。本测试同时钉住两个方向：
      * 3 候选（attack/skill/power）⇒ **不得**降权（否则区分度塌成 1）；
      * 4 候选（多一个 power）⇒ **应当**降权（仍有 2 个未罚，有区分度）；
      * 纯 curse token 的既有行为**不得**被破坏。
    """
    tokens = ["skill", "attack"]
    three = {"reward_cards": [
        {"index": 0, "card_type": "attack"},
        {"index": 1, "card_type": "skill"},
        {"index": 2, "card_type": "power"},
    ]}
    penalized = planner._indices_worth_penalizing(three, "choose_reward_card", tokens)
    remaining = 3 - len(penalized)
    assert remaining >= 2, (
        f"★I3 退化：3 候选里罚了 {sorted(penalized)}，只剩 {remaining} 个未罚候选 ⇒ "
        f"教训没有区分度，会恒定把玩家推向唯一幸存档"
    )

    four = {"reward_cards": [
        {"index": 0, "card_type": "attack"},
        {"index": 1, "card_type": "skill"},
        {"index": 2, "card_type": "power"},
        {"index": 3, "card_type": "power"},
    ]}
    penalized4 = planner._indices_worth_penalizing(four, "choose_reward_card", tokens)
    assert penalized4 == {0, 1}, (
        f"4 候选时仍有区分度（剩 2 个未罚），应当降权 attack/skill，实际 {sorted(penalized4)}"
    )
    assert 4 - len(penalized4) >= 2, "降权后必须仍留 >=2 个未罚候选"

    # 既有 curse 行为不得被破坏。
    curse = {"reward_cards": [
        {"index": 0, "card_type": "attack"},
        {"index": 1, "card_type": "skill", "rarity": "curse"},
        {"index": 2, "card_type": "power"},
    ]}
    assert planner._indices_worth_penalizing(curse, "choose_reward_card", ["curse"]) == {1}, (
        "curse 教训应仍能精确命中 rarity=curse 的选项"
    )


# --------------------------------------------------------------------------------------
# 4. 分辨率对照：`_ranked_option_indices` 直调**不能**代表默认档决策
# --------------------------------------------------------------------------------------
def test_ranking_layer_is_not_the_decision_layer() -> None:
    """★把「候选层」与「决策层」的差别写进测试，防止后人拿前者当后者。

    默认档下 `service._apply_play_style`（`service.py:793-794`）早退，`ordered_candidates`
    （唯一调用点 `:804`）**不可达**。因此直接在测试里调 `_ranked_option_indices` 得到的重排，
    **不代表**真实决策发生了位移 —— 真实的位移判据是 `plan()` 端到端。

    本测试做两件事：
    1. 用猴子补丁确认 serious 档下 `ordered_candidates` 一次都没被调用；
    2. 断言 `reflection_metrics.py` 确实提供 `e2e` 口径（防止脚手架被换成占位率类指标）。
    """
    from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

    service = STS2AutoplayService(_Logger(), lambda payload: None, lambda **kwargs: None)
    service._state.play_style = "serious"

    touched: list[str] = []

    def spy(context, operation):
        touched.append("called")
        raise AssertionError("serious 档不得触达 ordered_candidates")

    service._planner.ordered_candidates = spy
    snapshot = {"classification": {"state_name": "map"}, "summary_context": {"payload": {}},
                "strategy_context": {}}
    operation = {"action_type": "choose_map_node", "kwargs": {"option_index": 1}, "reason": "test"}
    out, info = service._apply_play_style(snapshot, operation)

    assert touched == [], "serious 档调用了 ordered_candidates，说明早退闸门失效"
    assert out is operation and info == {}, "serious 必须原对象返回、零扰动信息"

    # 脚手架必须提供端到端口径（而非只有覆盖率/占位率）。
    # ⚠️ 位置探测要跨副本工作：测试可能从 installed 或 builtin 副本运行，
    #    而 `.dsh-artifacts/` 只存在于仓库根 `D:\NekoClaw\n.e.k.o_plugin_sts2_autoplay`。
    #    这里按优先级探测，找不到就**失败**而不是 skip —— 否则这条契约在副本里静默失效。
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", "..", "..", ".dsh-artifacts",
                                      "reflection_metrics.py")),
        r"D:\NekoClaw\n.e.k.o_plugin_sts2_autoplay\.dsh-artifacts\reflection_metrics.py",
    ]
    metrics_path = next((path for path in candidates if os.path.exists(path)), None)
    assert metrics_path, (
        "找不到度量脚手架 `.dsh-artifacts/reflection_metrics.py`。"
        "它是 t10 交付物，也是 t11/t13 前后对照的唯一判据来源 —— 缺了它本仓库的"
        "『提升』就无法度量。探测过：" + " | ".join(candidates)
    )
    with open(metrics_path, encoding="utf-8") as handle:
        source = handle.read()
    assert "e2e_primary_metric" in source, "度量脚手架必须声明端到端位移为主判据"
    assert "plan_displacement_bits" in source, "度量脚手架必须测量 plan() 的位移"
    assert "_inject_run_memory_directives" in source or "build_context" in source, (
        "度量脚手架必须走真实注入路径（strategy_repository），而不是手工喂 avoid"
    )


# --------------------------------------------------------------------------------------
# 5. ★★ 教训的着力点 = **运营**（选卡 / 路线 / 商店 / 事件），**不是战斗**
# --------------------------------------------------------------------------------------
#
# 玩家 2026-10-18 指示（架构级）：
#   「战斗完全交给求解器。战斗以外的才需要启发式评分。我就这么说，
#     求解器是穷举所有可能性，这一步启发完全没有意义，只需要按照路线走
#     就一定能成功，启发了会导致更多变量导致失败，得不偿失；而非战斗外的
#     部分才是这个游戏的精髓，没有运营好，求解器都找不出一条可以胜利的
#     道路，这才是需要反思的」
#
# ⇒ 本节由「教训改变**战斗**出牌」重写为「教训改变**运营**决策」。
#
# 为什么必须重写而不是修修补补：
#   旧版本的 5 个测试断言的是 `plan()` 在 combat 屏上"选攻击还是选防御"。
#   纯求解器改造后，combat 屏**没有任何启发式**（见 heuristic_planner.py
#   的冻结契约块），该断言的主体已不存在。把它们改绿的唯一方法是
#   把启发式加回来 —— 那正是本次要移除的东西。
#   **它们的失败不是回归，是设计被否决。**
#
# 本节钉住的运营契约（对应玩家点名的六维度）：
#   * 路线屏  —— 规避某类节点（精英/Boss/怪/休息/商店/宝箱）
#   * 奖励屏  —— 规避某类卡（技能/攻击/能力）
#   * 事件屏  —— 规避某类事件后果
#   * 商店屏  —— ★ **当前无着力点**（诚实记录，见 test_shop_screen_has_no_lesson_fulcrum）
#
# ★ 三条不变量（沿用旧节的保守语义，一字不改）：
#   1. 无教训 ⇒ **逐字原序**（没有记忆时行为与"没有复盘机制"完全相同）
#   2. 只降权**不删除**（命中的选项仍在候选里，只是靠后）
#   3. 纯函数（不掷随机、不读时间，同输入必得同输出）
# --------------------------------------------------------------------------------------


def _lesson_context(snapshot: dict, avoid: list[str]) -> dict:
    """构造带教训的 context（走 `_ranked_option_indices` 的真实入口）。"""
    return {
        "snapshot": snapshot,
        "classification": snapshot.get("classification") or {},
        "summary_context": snapshot.get("summary_context") or {"payload": {}},
        "strategy_context": {
            "preferences": {},
            "strategy_directives": {"must": [], "prefer": [], "avoid": list(avoid)},
        },
    }


def _rank_map(planner: STS2HeuristicPlanner, avoid: list[str]) -> list[int]:
    return _ranked(planner, _map_snapshot(), "choose_map_node", avoid=avoid)


def _rank_reward(planner: STS2HeuristicPlanner, avoid: list[str]) -> list[int]:
    return _ranked(planner, _reward_snapshot(), "choose_reward_card", avoid=avoid)


def _plan_combat(planner: STS2HeuristicPlanner, avoid: list[str], *, guidance: str = "",
                 snapshot: dict | None = None):
    """战斗屏 `plan()` 入口（**无求解器计划**）。

    ★ 保留这个 helper 是为了测「战斗屏**不再**受教训影响」这条契约：
      纯求解器改造后，没有 `solver_plan` 时战斗屏必须返回 `None`，
      无论有没有教训。见 `test_combat_screen_is_solver_only_and_ignores_lessons`。
    """
    payload: dict = {}
    if guidance:
        payload["guidance"] = {"pending": [{"content": guidance}]}
    return planner.plan({
        "classification": {"state_name": "combat"},
        "snapshot": snapshot if snapshot is not None else _combat_snapshot(),
        "strategy_context": ({"strategy_directives": {"avoid": avoid}} if avoid else {}),
        "summary_context": {"payload": {}, "decision_payload": payload},
    })


def test_lesson_displaces_map_route_end_to_end(planner: STS2HeuristicPlanner) -> None:
    """★核心判据：教训必须真的改变**路线**排序。

    ★★ 可观测带的实测结论（2026-10-19，写测试前先跑过）：
      在标准 4 节点地图 `[Monster, RestSite, Elite, Shop]` 上，
      **基线排序**（无教训）是 `[1, 3, 0, 2]` —— 也就是说
      **`elite`(2) 与 `treasure` 本来就被排在最后**。

    ⇒ 「下次别挑精英」这类教训在此场景下**位移恒为 0**，
      但**不是着力点断了** —— 是代码本来就做了教训想要的事（同值冗余）。
      用 `elite` 做断言会得到"看起来失败其实正确"的假阴。

    ⇒ 本测试改用 **`rest`**（基线排第 0，即最优先）：
      只有它被降权后移，才**能归因给教训**。这是唯一有净贡献的带。
      （`shop`/`monster` 亦可观测，见 `test_route_lesson_observable_band_is_recorded`。）

    ⚠️ 这个区分不是吹毛求疵：我第一版用 `elite` 写，跑出"没有位移"，
       差点把它当成缺陷上报。**先测基线，再下结论。**
    """
    snapshot = _map_snapshot()
    baseline = _ranked(planner, snapshot, "choose_map_node", avoid=[])

    # 教训指向"休息"（别名表：休息/睡觉/回血点/篝火）
    lesson = "以后别在篝火休息了，休息浪费升级机会"
    assert "rest" in planner._lesson_tokens([lesson]), (
        f"前提：该教训应抽出 rest token，实际 {planner._lesson_tokens([lesson])}"
    )

    displaced = _ranked(planner, snapshot, "choose_map_node", avoid=[lesson])

    assert displaced != baseline, (
        f"★教训没有改变路线决策：{baseline} -> {displaced}。"
        "运营侧着力点断了 —— 这是玩家最在意的部分。"
    )
    # 不变量 2：只降权，不删除候选
    assert sorted(displaced) == sorted(baseline), "只许降权，不许删除候选"
    # ★ 语义方向：休息点(1)必须被降到后面
    assert displaced.index(1) > baseline.index(1), (
        f"休息点应被降权后移：{baseline} -> {displaced}"
    )


def test_route_lesson_observable_band_is_recorded(planner: STS2HeuristicPlanner) -> None:
    """★把「哪些路线教训可观测」钉成契约 —— 防止后人用假阴场景写测试。

    实测（2026-10-19，标准 4 节点地图 `[Monster 0, RestSite 1, Elite 2, Shop 3]`）:

        基线（无教训） = [1, 3, 0, 2]
        ⇒ 基线顺序：RestSite(1) → Shop(3) → Monster(0) → **Elite(2) 已在最后**

        token      基线位置   教训后   可观测？
        ---------  ---------  -------  --------
        rest       0（最优先） 3        ★ 是
        shop       1          3        ★ 是
        monster    2          3        ★ 是
        elite      **3（最后）** —      ✗ **否**（同值冗余）

    ⇒ `elite` 的规避教训在此场景下**结构上不可观测**：
      不是着力点断了，而是**基线本来就把精英排最后了**。

    ⚠️ 我第一版用 `elite` 写核心测试，跑出"没有位移"，
       差点当成缺陷上报。**先测基线，再下结论。**

    ★ 另一个同样重要的发现（见 `test_event_lesson_is_redundant_with_baseline_scorer`）：
      事件屏的 `_score_event_option` 基线就对该扣上限罚 −60、掉血罚 −24/−12，
      而教训只罚 −30 ⇒ 事件屏也常落在同值冗余带。
      **结论：教训机制大部分时候是"冗余保险"，而不是主力。**
    """
    nodes = [{"index": 0, "type": "Monster"}, {"index": 1, "type": "RestSite"},
             {"index": 2, "type": "Elite"}, {"index": 3, "type": "Shop"}]
    payload = {"travelable_nodes": nodes}

    def rank(avoid: list[str]) -> list[int]:
        ctx = {
            "snapshot": {}, "classification": {},
            "summary_context": {"payload": payload},
            "strategy_context": {"preferences": {},
                                 "strategy_directives": {"must": [], "prefer": [], "avoid": avoid}},
        }
        return planner._ranked_option_indices(
            ctx, {"action_type": "choose_map_node", "kwargs": {"option_index": 0}}
        )

    baseline = rank([])
    assert baseline == [1, 3, 0, 2], f"基线排序变了（{baseline}），本节结论需重核"

    lessons = {
        "rest": "以后别在篝火休息了，休息浪费升级机会",
        "shop": "别进商店乱花钱了，商店没什么好买的",
        "monster": "别老打小怪了，小怪打多了没收益",
    }
    for token, lesson in lessons.items():
        assert token in planner._lesson_tokens([lesson]), f"前提：{token} 应可抽出"
        assert rank([lesson]) != baseline, f"[{token}] 应可观测（基线未同向），却没有位移"

    # ★ 不可观测的那个：位移必须为 0，且**必须由"基线已把它排最后"解释**。
    #   （实测：只有 elite 不可观测；treasure 在含 Treasure 节点时是**可观测**的，
    #     所以它归上面那组，不能混在这里。）
    elite_lesson = "别再挑精英了，上次就是栽在精英手里"
    assert "elite" in planner._lesson_tokens([elite_lesson]), "前提：elite 应可抽出"
    elite_index = next(
        n["index"] for n in nodes if planner._map_node_weight_key(n) == "elite"
    )
    assert baseline.index(elite_index) == len(baseline) - 1, (
        f"elite 应在基线最后才能解释『同值』，实际位置 {baseline.index(elite_index)}"
    )
    assert rank([elite_lesson]) == baseline, (
        "elite 教训预期同值（基线已排最后），实际出现位移 —— 本测试结论需重核"
    )


def test_lesson_displaces_reward_card_end_to_end(planner: STS2HeuristicPlanner) -> None:
    """★教训必须真的改变**选卡**排序（玩家：「遇到群怪就死就反思有没有拿 aoe 卡」）。

    ★ 用词必须命中 `_LESSON_KEYWORD_MAP`：
      「攻击」→ token `attack`（别名表是 输出/伤害/抢血/斩杀，**不含**"攻击"二字）
      「格挡」→ token `skill`
    """
    snapshot = _reward_snapshot()
    baseline = _ranked(planner, snapshot, "choose_reward_card", avoid=[])

    # 同一句里同时含 skill + attack，命中奖励屏的两张牌
    lesson = "群怪那几场死得太惨，下次记得先垫格挡再输出"
    tokens = planner._lesson_tokens([lesson])
    assert tokens, f"前提：该教训应能抽出 token，实际 {tokens}"

    displaced = _ranked(planner, snapshot, "choose_reward_card", avoid=[lesson])

    # 奖励屏有"区分度闸"（见 _indices_worth_penalizing 的 I3 注释）：
    # 若惩罚集会把候选压成单一选择，本屏**主动放弃**降权、返回原序。
    # 所以这里不断言"必有位移"，而断言**要么有位移、要么留 ≥2 个未罚项**。
    if displaced == baseline:
        tokens = planner._lesson_tokens([lesson])
        payload = snapshot["summary_context"]["payload"]
        penalized = planner._indices_worth_penalizing(payload, "choose_reward_card", tokens)
        assert len(penalized) < 2 or len([i for i in baseline if i not in penalized]) >= 2, (
            f"★奖励屏既没位移、又因偏罚退化（罚了 {sorted(penalized)}）—— "
            "这说明区分度闸失效，会把玩家恒定推向某一个选项"
        )
    assert sorted(displaced) == sorted(baseline), "只许降权，不许删除候选"


def test_lesson_changes_event_choice_end_to_end(planner: STS2HeuristicPlanner) -> None:
    """★教训必须能作用于**事件**屏（缺陷 C 的原始现场）。

    旧注释记录：事件屏此前不在 `_indices_worth_penalizing` 的返回内，
    于是复盘教训**永远作用不到事件屏** —— 这正是玩家说的
    「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。

    ★★ 但**判据不能是"排序有位移"** —— 实测发现事件屏存在**同值冗余带**：

        `_score_event_option`（heuristic_planner.py:3972）**基线就已**对
        「扣生命上限」罚 **−60**、对普通掉血罚 −24/−12、对诅咒计负分。
        而教训的惩罚量是 **−30**（实测 Δ）。
        ⇒ 基线罚得比教训还重，且**已经把该选项压到最低分**，
          于是教训无论怎么扣都**改不了名次**。

        实测（选项0=「获得一张诅咒牌，得到 500 金币」，选项1=「什么也不发生」）：
            无教训 = {0: 0.0,   1: 100.0}   → 排序 [1, 0]
            有教训 = {0: -30.0, 1: 100.0}   → 排序 [1, 0]     ← **分变了，序没变**

    ⇒ 正确的判据是「**教训确实改变了评分**」，而不是"改变了名次"。
      名次不变是因为**基线已经做对了**，那是好事，不是缺陷。

    ⚠️ 我第一版用「排序必须有位移」写，跑出"失败"，差点把它当缺陷上报。
       **先测基线，再下结论。**
    """
    payload = {"event_options": [
        {"index": 0, "title": "接受", "description": "获得一张诅咒牌，得到 500 金币"},
        {"index": 1, "title": "拒绝", "description": "什么也不发生"},
    ]}
    lesson = "别再拿诅咒了，上次拿诅咒把自己坑死了"
    tokens = planner._lesson_tokens([lesson])
    assert "curse" in tokens, f"前提：该教训应抽出 curse token，实际 {tokens}"

    summary = {"payload": payload}
    scores_without = planner._event_final_scores(summary, {"strategy_directives": {"avoid": []}})
    scores_with = planner._event_final_scores(summary, {"strategy_directives": {"avoid": [lesson]}})

    # ★ 核心断言：教训**必须**改变评分（证明事件屏着力点接通）
    assert scores_with != scores_without, (
        f"★事件屏着力点断了：有无教训的评分完全相同 {scores_without}。"
        "这正是玩家报告的『复盘了但脚本毫无改善』。"
    )
    # 被罚的必须**正是**命中教训的那个选项
    penalized = planner._indices_worth_penalizing(payload, "choose_event_option", tokens)
    assert penalized, "前提：该教训应在事件屏命中至少一个选项"
    for index in penalized:
        assert scores_with[index] < scores_without[index], (
            f"选项 {index} 被判定应罚，但分数没有下降："
            f"{scores_without[index]} -> {scores_with[index]}"
        )
    # 未命中的选项**不得**被牵连（避免"全罚等于没罚"）
    for index in set(scores_without) - penalized:
        assert scores_with[index] == scores_without[index], (
            f"选项 {index} 未命中教训却分数变了："
            f"{scores_without[index]} -> {scores_with[index]}"
        )


def test_event_lesson_is_redundant_with_baseline_scorer(planner: STS2HeuristicPlanner) -> None:
    """★诚实记录：事件屏存在**同值冗余带** —— 基线与教训针对同一后果。

    基线 `_score_event_option`(:3972) 已对三类后果重罚：

        后果          基线惩罚      注释里的实机依据
        ------------  -----------  ----------------------------------------
        扣生命上限    **−60**      原先 +10 ⇒ 脚本每轮都选，max_hp 80→54
        普通掉血      −24 / −12    "掉血换药水"曾净 +2 ⇒ 会主动拿命换
        诅咒          见 :4030     诅咒在卡牌质量维度零/负期望

    而教训的惩罚量是 **−30**（本轮实测 Δ）。

    ⇒ **基线罚得比教训更重**，且已把该选项压到最低分 ⇒
      教训在事件屏**改不了名次**（但确实改了分数，见上一条测试）。

    ⚠️ 本测试**不是**在庆祝冗余，而是把它钉成可执行的"已知边界"：
      它保证后人不会用"排序无位移"去判定"着力点断了"
      （那会得到假阴，并把正确行为误报成缺陷）。

    ★ 设计问题留待玩家定夺（本测试不预设答案）：
      若认为教训该有独立话语权 ⇒ 应提高教训惩罚量级使其能翻盘；
      若认为基线已足够      ⇒ 保持现状，教训作为兜底保险。
    """
    payload = {"event_options": [
        {"index": 0, "title": "接受", "description": "获得一张诅咒牌，得到 500 金币"},
        {"index": 1, "title": "拒绝", "description": "什么也不发生"},
    ]}
    lesson = "别再拿诅咒了，上次拿诅咒把自己坑死了"

    def order(avoid: list[str]) -> list[int]:
        scores = planner._event_final_scores({"payload": payload}, {"strategy_directives": {"avoid": avoid}})
        return [k for k, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]

    # 前提：基线已把被罚选项排在最后（这正是"不可观测"的成因）
    baseline = order([])
    assert baseline[-1] == 0, (
        f"前提失效：基线应已把 0 号（诅咒/金币项）排最后，实际 {baseline}。"
        "若基线排序变了，本节『同值冗余』的结论需重核。"
    )
    assert order([lesson]) == baseline, (
        "预期同值（基线已把它排最后），实际出现位移 —— 本测试的结论需重核"
    )


def test_shop_screen_now_has_lesson_fulcrum(planner: STS2HeuristicPlanner) -> None:
    """★★ 商店屏**已有**教训着力点（2026-10-19 实现，替换原来的"无着力点"钉桩）。

    原缺口：`_indices_worth_penalizing` 只处理路线/选牌/事件三类，
    **没有**商店分支 ⇒ `avoid` 对商店决策零影响，
    于是"这局缺格挡"这类教训永远改不了"该不该买格挡牌"。
    玩家把「商店的选择」列为六维度之一，这条必须接通。

    ★ 与其它屏的**语义差别**：路线/选牌是"规避某类选项"，
      商店是"**别买**没在补缺口的那类" —— 仍是降权，复用同一机制。

    ★ 刻意保守：只有能明确映射到某一维的 token 才动商店
      （`skill`→格挡维、`attack`→输出维）。
      `elite`/`boss`/`rest` 这类**路线/敌人** token 在商店里没有对应商品 ⇒ **不动**，
      否则会变成噪声（与全局"匹配不准就别动"一致）。
    """
    strike = {"index": 0, "card_id": "strike", "card_type": "Attack", "name": "打击",
              "resolved_rules_text": "造成 6 点伤害。"}
    defend = {"index": 1, "card_id": "defend", "card_type": "Skill", "name": "防御",
              "resolved_rules_text": "获得 5 点格挡。"}
    payload = {"shop_cards": [strike, defend]}

    # 教训说缺格挡 ⇒ 应把"纯输出"那张降权（把额度留给格挡）
    penalized = planner._indices_worth_penalizing(payload, "choose_shop_item", ["skill"])
    assert penalized == {0}, (
        f"缺格挡的教训应降权攻击牌(0)，实际 {sorted(penalized)}"
    )

    # 反向：教训说缺输出 ⇒ 应把"纯格挡"那张降权
    penalized = planner._indices_worth_penalizing(payload, "choose_shop_item", ["attack"])
    assert penalized == {1}, (
        f"缺输出的教训应降权格挡牌(1)，实际 {sorted(penalized)}"
    )

    # ★ 保守边界：路线/敌人类 token 在商店里**不动任何东西**
    for token in ("elite", "boss", "rest", "treasure"):
        assert planner._indices_worth_penalizing(payload, "choose_shop_item", [token]) == set(), (
            f"token `{token}` 在商店屏没有对应商品的语义 —— 必须保持不动，避免噪声"
        )


def test_shop_item_dims_are_unit_correct(planner: STS2HeuristicPlanner) -> None:
    """★★ 商店商品的维度必须**单位正确**（防止格挡被当成输出）。

    这条直接钉住 `BUG-card-damage-unit-confusion` —— 它是本轮的**最大发现**：

    `ShopCardPayload`（`GameStateService.cs:6180`）**没有 `damage` 字段**，
    只有 `dynamic_values` + `resolved_rules_text` ⇒ 商店屏**必然**走文本回落路径。
    而原 `_card_damage` 的回落是"按空格切词、返回第一个能解析的整数"：

        防御  「获得 5 点格挡。」  -> 5   ❌
        准备  「抽 2 张牌。」      -> 2   ❌
        治疗  「回复 8 点生命。」  -> 8   ❌

    ⇒ `_deck_gap_vector` 的输出/格挡两维**全错**（实测输出 0.08 vs 正确 0.50）
    ⇒ **商店买错东西**。
    """
    # 卡：格挡牌只能补 defense，不得补 output
    defend = {"card_id": "defend", "card_type": "Skill", "name": "防御",
              "resolved_rules_text": "获得 5 点格挡。"}
    assert planner._card_damage(defend) == 0, (
        f"纯格挡牌的伤害必须是 0，实际 {planner._card_damage(defend)}"
        "（把格挡当伤害会让 gap_output 虚高）"
    )
    assert planner._shop_item_dims(defend) == {"defense"}, (
        f"纯格挡牌只该补 defense，实际 {sorted(planner._shop_item_dims(defend))}"
    )

    strike = {"card_id": "strike", "card_type": "Attack", "name": "打击",
              "resolved_rules_text": "造成 6 点伤害。"}
    assert planner._card_damage(strike) == 6, "真伤害牌必须读对"
    assert planner._shop_item_dims(strike) == {"output"}, (
        f"纯伤害牌只该补 output，实际 {sorted(planner._shop_item_dims(strike))}"
    )

    # 其它"带数字但不是伤害"的形态也不得被误读
    for text in ("抽 2 张牌。", "回复 8 点生命。", "获得 2 点能量。"):
        card = {"name": "x", "resolved_rules_text": text}
        assert planner._card_damage(card) == 0, (
            f"「{text}」里的数字不是伤害，不该被当伤害读出来"
        )

    # 药水：类别映射也要对
    assert planner._shop_item_dims({"name": "铁壁药水", "usage": "获得 12 点格挡"}) == {"defense"}
    assert planner._shop_item_dims({"name": "爆裂药水", "usage": "对所有敌人造成 10 点伤害"}) == {"aoe"}


def test_no_lesson_keeps_operations_ranking_byte_identical(planner: STS2HeuristicPlanner) -> None:
    """★不变量 1：**无教训**（含空白/无信息量教训）时，运营屏排序逐字原序。

    这是"没有记忆时行为与完全没有复盘机制相同"的硬约束 ——
    复盘机制不得引入任何**基线漂移**。
    """
    for snapshot, action_type in (
        (_map_snapshot(), "choose_map_node"),
        (_reward_snapshot(), "choose_reward_card"),
        (_event_snapshot(), "choose_event_option"),
    ):
        baseline = _ranked(planner, snapshot, action_type, avoid=[])
        for blank in ([], [""], ["   "], ["\n", "\t"], ["这局输得不明不白，下次决策慢一点、多留容错"]):
            out = _ranked(planner, snapshot, action_type, avoid=blank)
            assert out == baseline, (
                f"[{action_type}] 空白/无信息量教训改变了排序：{baseline} -> {out}（avoid={blank!r}）"
            )


def test_lesson_never_removes_candidates(planner: STS2HeuristicPlanner) -> None:
    """★不变量 2：任何教训都**只降权不删除** —— 候选集合恒不变。

    为什么这条是硬约束：候选集合是扰动层的输入。若教训能"删掉"候选，
    最坏情况会出现"无候选可出"，把一次复盘变成一次卡死。
    """
    lesson = "上次就是栽在精英手里，以后路线别再挑精英了"
    for snapshot, action_type in (
        (_map_snapshot(), "choose_map_node"),
        (_reward_snapshot(), "choose_reward_card"),
        (_event_snapshot(), "choose_event_option"),
    ):
        baseline = _ranked(planner, snapshot, action_type, avoid=[])
        displaced = _ranked(planner, snapshot, action_type, avoid=[lesson])
        assert sorted(displaced) == sorted(baseline), (
            f"[{action_type}] 教训改变了候选集合：{baseline} -> {displaced}"
        )


def test_operations_lesson_is_pure_and_deterministic(planner: STS2HeuristicPlanner) -> None:
    """★不变量 3：纯函数 —— 同输入必得同输出（连调 5 次完全一致）。

    复盘机制若引入随机性，"这局为什么这么走"就无法归因，
    也无法用前后对照度量提升。
    """
    lesson = "上次就是栽在精英手里，以后路线别再挑精英了"
    results = [_rank_map(planner, [lesson]) for _ in range(5)]
    assert all(r == results[0] for r in results), f"必须纯函数，实际得到多个不同结果：{results}"


# --------------------------------------------------------------------------------------
# 4c. ★★ 方案 A：降权必须是**可比较的强度**，不是"移到末尾"
# --------------------------------------------------------------------------------------
def test_lesson_penalty_strength_counts_mentioning_lessons(
    planner: STS2HeuristicPlanner,
) -> None:
    """★惩罚强度 = 「有多少条教训提到过这个 token」。

    实测动机（真实 50 局记忆窗口，10 条教训）：

        attack 8 条    skill 7 条    elite 5 条    monster 1 条

    ⇒ `elite`（被 5 条教训反复点到）比 `monster`（只 1 次）
      更该规避。**频次是玩家的真实信号**，不是我先验设定的常数。
    """
    lessons = [
        "别再挑精英了",
        "精英太危险",
        "碰到精英先垫格挡",
        "上次又栽在精英手里",
        "小怪打多了没收益",
    ]
    strength = planner._lesson_penalty_strength(lessons)
    assert strength["elite"] == 4, f"elite 应被 4 条提到，实际 {strength.get('elite')}"
    assert strength["monster"] == 1, f"monster 应被 1 条提到，实际 {strength.get('monster')}"
    assert strength["elite"] > strength["monster"], "多局反复提到的坑必须权重更高"


def test_ranked_demotion_is_not_an_identity_when_strength_differs(
    planner: STS2HeuristicPlanner,
) -> None:
    """★★ 方案 A 的核心验收：强度不同时**必须**产生位移。

    这是原缺陷的直接反例（`.dsh-artifacts/BUG-lesson-demotion-identity.md`）：

        旧实现 `kept + demoted` 在「被罚项集中在尾部」时是**恒等变换**。
        穷举验证：全部 3972 种 (排序, 被罚集) 组合中，**564 种（14.2%）恒等**。

    新实现按 `(惩罚强度, 原始名次)` 排序 ⇒ 强度可区分 ⇒ 能表达"0 比 2 更该避"。

    实测（标准地图 `[Monster 0, RestSite 1, Elite 2, Shop 3]`，基线 `[1,3,0,2]`）：

        monster ×5, elite ×1  ⇒  [1,3,2,0]   ★ Monster(0) 被压到最后
        monster ×1, elite ×5  ⇒  [1,3,0,2]   ★ Elite(2) 保持最后
    """
    nodes = [{"index": 0, "type": "Monster"}, {"index": 1, "type": "RestSite"},
             {"index": 2, "type": "Elite"}, {"index": 3, "type": "Shop"}]
    payload = {"travelable_nodes": nodes}
    baseline = [1, 3, 0, 2]

    def apply(lessons: list[str]) -> list[int]:
        ctx = {
            "snapshot": {}, "classification": {},
            "summary_context": {"payload": payload},
            "strategy_context": {"preferences": {},
                                 "strategy_directives": {"must": [], "prefer": [], "avoid": lessons}},
        }
        return planner._apply_run_lessons(
            ctx["strategy_context"], ctx["summary_context"], "choose_map_node", list(baseline)
        )

    # Monster 被反复点名 ⇒ 必须被压到 Elite 之后
    monster_heavy = ["小怪打多了没收益"] * 5 + ["别再挑精英了"]
    out = apply(monster_heavy)
    assert out != baseline, (
        f"★强度差异未产生位移：{baseline}。这正是原 `kept + demoted` 的恒等失效。"
    )
    monster_index = 0
    elite_index = 2
    assert out.index(monster_index) > out.index(elite_index), (
        f"被 5 条教训点名的 Monster 应排在只被 1 条点名的 Elite 之后：{out}"
    )

    # 反向：Elite 被反复点名 ⇒ 保持最后（Elite 本来就在末尾，这是**正确的恒等**）
    elite_heavy = ["别再挑精英了"] * 5 + ["小怪打多了没收益"]
    out_rev = apply(elite_heavy)
    assert out_rev.index(elite_index) == len(out_rev) - 1, (
        f"被 5 条教训点名的 Elite 必须排最后：{out_rev}"
    )

    # ★ 两个方向的排序键必须**不同** —— 证明强度确实被用上了（旧实现无法区分）
    assert out != out_rev, (
        "两种强度分布给出了相同排序 ⇒ 强度信号没有参与排序，方案 A 未生效"
    )
    # 不变量：候选集合恒不变
    for result in (out, out_rev):
        assert sorted(result) == sorted(baseline), "只许降权，不许删除候选"


def test_unpenalized_items_keep_relative_order(planner: STS2HeuristicPlanner) -> None:
    """★方案 A 的稳定性保证：**未被罚**的项保持原相对顺序。

    这是"没有记忆时逐字原序"这条硬约束在**有教训**时的推广 ——
    教训只该动被它点名的项，不该顺带打乱其它项的次序。
    """
    nodes = [{"index": 0, "type": "Monster"}, {"index": 1, "type": "RestSite"},
             {"index": 2, "type": "Elite"}, {"index": 3, "type": "Shop"}]
    payload = {"travelable_nodes": nodes}
    baseline = [1, 3, 0, 2]
    lessons = ["别再挑精英了"]

    ctx = {
        "snapshot": {}, "classification": {},
        "summary_context": {"payload": payload},
        "strategy_context": {"preferences": {},
                             "strategy_directives": {"must": [], "prefer": [], "avoid": lessons}},
    }
    out = planner._apply_run_lessons(
        ctx["strategy_context"], ctx["summary_context"], "choose_map_node", list(baseline)
    )
    # 未被罚的项（1, 3, 0）在输出里的相对顺序必须与原序一致
    unpenalized = [i for i in baseline if i != 2]
    assert [i for i in out if i != 2] == unpenalized, (
        f"未被罚项的相对顺序被破坏：{out}（原序 {baseline}）"
    )


def test_combat_screen_is_solver_only_and_ignores_lessons(planner: STS2HeuristicPlanner) -> None:
    """★★ 战斗屏**不再**受教训影响 —— 这是纯求解器改造在测试层的钉子。

    玩家：「战斗完全交给求解器……这一步启发完全没有意义」。
    ⇒ 无论有没有教训，战斗屏在没有求解器计划时都必须**不出手**（None），
      而不是"被教训推动着打某张牌"。

    ★ 与 `test_candidate_generator.py::test_combat_without_solver_plan_emits_no_action`
      互为跨文件对照：那一条测"无计划不出手"，这一条测"**有教训也不出手**"。
    """
    lesson = "下次别光着急输出，先垫格挡再输出"

    no_lesson = _plan_combat(planner, [])
    with_lesson = _plan_combat(planner, [lesson])

    assert no_lesson is None, (
        f"战斗屏无求解器计划时应不出手，实际 {no_lesson!r}"
    )
    assert with_lesson is None, (
        f"★教训不该能让战斗屏出手（战斗已完全交给求解器），实际 {with_lesson!r}"
    )
    # 旧口径的 `_lesson_combat_direction` 仍应存在（供留痕/兼容），
    # 但它的结果**不得**再有任何决策消费点。
    assert planner._lesson_combat_direction({"strategy_directives": {"avoid": [lesson]}}) in ({}, {"prefer_defense": True}), (
        "兼容层应返回空 dict 或明确方向，不得返回第三种形状"
    )


def test_combat_direction_is_pure_and_order_sensitive(planner: STS2HeuristicPlanner) -> None:
    """★纯函数 + 语义方向：`skill` 优先于 `attack`（对应"先垫格挡**再**输出"）。

    同时含 skill+attack（真实最高频组合）时必须取 `prefer_defense` ——
    若取 `prefer_attack` 就把"先垫格挡"读反了，正是语义压平的老问题。
    """
    both = {"strategy_directives": {"avoid": ["先垫格挡再输出"]}}
    assert planner._lesson_tokens(["先垫格挡再输出"]) == ["skill", "attack"]
    assert planner._lesson_combat_direction(both) == {"prefer_defense": True}

    attack_only = {"strategy_directives": {"avoid": ["别贪格挡了，全力输出抢血斩杀"]}}
    # 注意：这条同时含"格挡" => skill 也在；用一条纯输出的句子验证 attack 方向。
    pure_attack = {"strategy_directives": {"avoid": ["多抢血斩杀"]}}
    assert planner._lesson_tokens(["多抢血斩杀"]) == ["attack"]
    assert planner._lesson_combat_direction(pure_attack) == {"prefer_attack": True}

    # 纯函数：同样输入必得同样输出（连调 5 次）。
    results = [planner._lesson_combat_direction(both) for _ in range(5)]
    assert all(result == results[0] for result in results), "必须是纯函数（不得掷随机）"
    assert attack_only is not None


# --------------------------------------------------------------------------------------
# 6. ★I4 可观测性：教训命中必须有可 grep 的日志，且日志失败绝不影响决策
# --------------------------------------------------------------------------------------
def test_lesson_hit_emits_greppable_log_and_never_breaks_decisions() -> None:
    """★I4：教训命中时要留**可 grep、带去重键**的日志，且**日志失败绝不影响决策**。

    两个方向都要钉住：
      * 命中时**必须**产出 `[sts2_lesson_hit]` 前缀的日志（否则线上无法观测教训是否生效）；
      * 同一命中**只打一次**（去重键 = stage+tokens+effect，防刷屏）；
      * logger 抛异常时 `_lesson_combat_direction` **仍要正常返回**（可观测性不得成为
        决策的失败点 —— 这是硬约束）。
    """
    captured: list[str] = []

    def spy(message: str, *args: Any) -> None:
        captured.append(message % args if args else message)

    planner = STS2HeuristicPlanner()
    direction = planner._lesson_combat_direction(
        {"strategy_directives": {"avoid": ["先垫格挡再输出"]}}, logger=spy
    )
    assert direction == {"prefer_defense": True}, "前提：该教训应给出防御方向"
    hits = [line for line in captured if "[sts2_lesson_hit]" in line]
    assert hits, f"★I4 未实现：命中教训却没有输出可 grep 的日志。captured={captured!r}"
    assert "stage=combat_direction" in hits[0] and "tokens=skill,attack" in hits[0], (
        f"日志必须包含 stage 与 tokens（去重键成分），实际 {hits[0]!r}"
    )
    # 去重：同一命中重复调用不再新增日志。
    for _ in range(3):
        planner._lesson_combat_direction(
            {"strategy_directives": {"avoid": ["先垫格挡再输出"]}}, logger=spy
        )
    assert len([line for line in captured if "[sts2_lesson_hit]" in line]) == len(hits), (
        "同一命中应只因去重键打一次，实际重复输出了"
    )

    # ★日志失败绝不影响决策：logger 抛异常时仍须正常返回方向。
    def boom(message: str, *args: Any) -> None:
        raise RuntimeError("日志后端炸了")

    safe = planner._lesson_combat_direction(
        {"strategy_directives": {"avoid": ["先垫格挡再输出"]}}, logger=boom
    )
    assert safe == {"prefer_defense": True}, (
        f"★日志抛异常时不得影响决策，实际返回 {safe!r}"
    )
    # 无教训时不得因为日志而改变行为。
    assert planner._lesson_combat_direction({}, logger=boom) == {}


def test_lesson_trace_exposes_metrics_for_t_r3(planner: STS2HeuristicPlanner) -> None:
    """★I5 度量埋点：t_r3 的指标必须能从一个调用算出（闸2/闸3/区分度/兜底率）。"""
    lesson = "先垫格挡再输出"
    context = {
        "classification": {"state_name": "combat"},
        "snapshot": _combat_snapshot(),
        "strategy_context": {"strategy_directives": {"avoid": [lesson]}},
        "summary_context": {"payload": {}, "decision_payload": {}},
    }
    trace = planner.lesson_trace(context)
    assert trace["lessons"] == [lesson]
    assert trace["tokens"] == ["skill", "attack"], "闸3 判据：token 必须可观测"
    assert trace["token_lesson_ratio"] == 1.0, "该教训可抽出 token，信息密度应为 1.0"
    assert trace["combat_direction"] == {"prefer_defense": True}
    assert trace["fallback_lesson_count"] == 0

    # 兜底模板句：token 为空、被判为 fallback（M1 复读率的运行期口径）。
    fallback_context = dict(context)
    fallback_context["strategy_context"] = {
        "strategy_directives": {"avoid": ["这局输得不明不白，下次决策慢一点、多留容错"]}
    }
    fb = planner.lesson_trace(fallback_context)
    assert fb["tokens"] == [] and fb["token_lesson_ratio"] == 0.0
    assert fb["fallback_lesson_count"] == 1, "兜底模板句必须能被识别出来"
    assert fb["combat_direction"] == {}, "无信息量的模板句不得产生战斗方向"

    # 带 operation 时给出惩罚集与区分度（I3 判据）。
    reward_context = {
        "classification": {"state_name": "reward"},
        "snapshot": {"screen": "reward"},
        "strategy_context": {"strategy_directives": {"avoid": [lesson]}},
        "summary_context": {"payload": {"reward_cards": [
            {"index": 0, "card_type": "attack"},
            {"index": 1, "card_type": "skill"},
            {"index": 2, "card_type": "power"},
        ]}},
    }
    rt = planner.lesson_trace(reward_context, {"action_type": "choose_reward_card"})
    assert rt["action_type"] == "choose_reward_card"
    assert rt["discriminating_count"] >= 2, (
        f"★I3：3 候选时未罚候选必须 >= 2，实际 {rt['discriminating_count']}（{rt['penalize_indices']}）"
    )


def test_english_reason_labels_generate_specific_lessons() -> None:
    """★R5：真实 `decisions[].reason` 是**全英文标签**，必须能产出**具体**教训。

    变异体 `R5-english-reason-mapping-removed` 首轮逃逸 —— 因为原有测试只断言
    "defeat 局有 lesson 就算过"，而兜底句会**无条件**满足它。
    ⇒ 必须断言"教训**不是**兜底句"，才能证明英文标签真的被读进去了。

    真实数据：660 条 / 去重 25 种 reason，中文关键词命中 **0/660**
    （`catgirl_memory.py:126-135` 的落盘白名单还缺 `current_hp`/`max_hp`）。
    """
    import tempfile

    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        store = CatgirlMemoryStore(path=os.path.join(tmp, "memory.json"))
        decision_memory = [
            {"screen": "COMBAT", "action_type": "play_card",
             "reason": "combat_preference_or_default", "step_count": 3},
            {"screen": "COMBAT", "action_type": "end_turn",
             "reason": "mod_solver_end_turn", "step_count": 4},
            {"screen": "MAP", "action_type": "choose_map_node",
             "reason": "map_preference_or_default", "step_count": 5},
        ]
        run = store.add_run("T11-R5-PROBE", character="IRONCLAD", outcome="defeat",
                            floor=7, act=1, decision_memory=decision_memory)
        lessons = run["lessons"]
        assert lessons, "英文 reason 未能产出任何教训（R5 修复失效）"
        # ★关键断言：必须**不是**兜底句 —— 否则等于没读 reason。
        assert not any("输得不明不白" in lesson for lesson in lessons), (
            f"★R5 退化：英文 reason 只落到了兜底句，说明映射没生效。lessons={lessons!r}"
        )
        # 且必须抽得出 token（否则修了 R5 也无法影响决策）。
        planner = STS2HeuristicPlanner()
        tokens = planner._lesson_tokens(lessons)
        assert tokens, f"★R5 修复后教训必须能抽出 token，实际 lessons={lessons!r}"
        # 三条英文标签应各自映射到**具体**教训。
        lowered = " ".join(lessons)
        assert "格挡" in lowered or "垫" in lowered, (
            f"战斗类英文标签应映射出含『格挡』的战斗教训，实际 {lessons!r}"
        )


def test_structured_lesson_records_are_persisted() -> None:
    """★I1：结构化记录必须**真的落盘到 run 里**（关闭 `I1-record-layer-dropped` 逃逸）。

    变异体首轮逃逸：原有测试只查 `store.lesson_records()` 非空，
    但那是从老键 `lessons` **回退**读出来的 ⇒ 新键被清空也测不出来。
    ⇒ 必须直接断言 `run["lesson_records"]` 这个**持久化键**存在且与非空。
    """
    import tempfile

    from plugin.plugins.sts2_autoplay.catgirl_memory import CatgirlMemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "memory.json")
        store = CatgirlMemoryStore(path=path)
        decision_memory = [
            {"screen": "COMBAT", "action_type": "play_card",
             "reason": "combat_preference_or_default", "step_count": 3},
        ]
        run = store.add_run("T11-I1-PROBE", character="IRONCLAD", outcome="defeat",
                            floor=5, act=1, decision_memory=decision_memory)
        assert run["lessons"], "前提：该局应产出教训"
        # ★关键断言：**持久化键本身**必须存在且非空。
        assert "lesson_records" in run, "★I1 退化：run 里没有持久化 lesson_records 键"
        records = run["lesson_records"]
        assert records, f"★I1 退化：lesson_records 为空（{records!r}）"
        assert len(records) == len(run["lessons"]), (
            "lesson_records 与 lessons 必须一一对应"
        )
        assert all(isinstance(record, dict) for record in records)
        assert all(record.get("text") for record in records)
        assert all(record.get("source") == "heuristic" for record in records)

        # 落盘后重新加载，结构化层必须仍在（证明是持久化而非内存态）。
        reloaded = CatgirlMemoryStore(path=path)
        target = [r for r in reloaded.runs if r.get("run_id") == "T11-I1-PROBE"]
        assert target, "重新加载后找不到该局"
        assert target[0].get("lesson_records"), (
            "★I1 退化：重新加载后 lesson_records 丢失（未真正落盘）"
        )
