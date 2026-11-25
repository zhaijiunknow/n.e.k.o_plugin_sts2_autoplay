"""局末对照复盘：玩家亲手打的每一步，与插件当时的推荐对照（`choice_review.py` + `service.review_tick`）。

钉住四件事：

1. 模组报来的选择能对上网络的候选（同名目标靠下标区分、手里两张同名牌算同一个选择）；
2. 没有模型时，规划器的一步能翻译成同一套说法，并判出"一样 / 不一样"；
3. 只记**玩家**的步：插件自己动过手的那一步不进对照，一条推荐只被回答一次；
4. 局末挑的是"差得最多"的几步：网络按概率差，旧策略按当时血量。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.choice_review import (
    MIN_GAP,
    SOURCE_NETWORK,
    SOURCE_PLANNER,
    ChoiceReview,
    choice_label,
    collect_names,
    compare_planner,
    divergent_steps,
    humanize,
    match_candidate,
    planner_recommendation,
    review_digest,
)
from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

pytestmark = pytest.mark.unit


def _candidate(index: int, kind: str, label: str, **extra: Any) -> dict[str, Any]:
    return {"index": index, "kind": kind, "label": label, "executable": True, **extra}


_COMBAT_CANDIDATES = [
    _candidate(0, "PlayCard", "play STRIKE_IRONCLAD -> NIBBIT", action="play_card", card_index=0, target_index=0),
    _candidate(1, "PlayCard", "play STRIKE_IRONCLAD -> NIBBIT", action="play_card", card_index=0, target_index=1),
    _candidate(2, "PlayCard", "play DEFEND_IRONCLAD", action="play_card", card_index=1),
    _candidate(3, "PlayCard", "play STRIKE_IRONCLAD -> NIBBIT", action="play_card", card_index=2, target_index=0),
    _candidate(4, "UsePotion", "use FIRE_POTION -> NIBBIT", action="use_potion", option_index=0, target_index=1),
    _candidate(5, "EndTurn", "end turn", action="end_turn"),
]
_COMBAT_PROBS = [0.05, 0.10, 0.60, 0.07, 0.03, 0.15]


def _scored(candidates: list[dict[str, Any]] = _COMBAT_CANDIDATES, probs: list[float] = _COMBAT_PROBS, *, kind: str = "COMBAT",
            decision_id: str = "d1") -> dict[str, Any]:
    return {
        "decision_id": decision_id, "kind": kind, "candidates": candidates, "probs": probs,
        "choice": max(range(len(probs)), key=probs.__getitem__), "value": 1.5, "forced": False,
    }


# ---- 1. 选择 -> 网络候选 ----------------------------------------------------------------


def test_play_card_is_matched_by_card_and_target_index() -> None:
    choice = {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": 1}
    assert match_candidate(_COMBAT_CANDIDATES, choice, _COMBAT_PROBS) == 1


def test_two_copies_of_the_same_card_are_one_choice_and_the_better_scored_one_wins() -> None:
    choice = {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": 0}
    assert match_candidate(_COMBAT_CANDIDATES, choice, _COMBAT_PROBS) == 3


def test_target_falls_back_to_the_label_when_the_enemy_is_already_gone() -> None:
    choice = {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": None}
    assert match_candidate(_COMBAT_CANDIDATES, choice, _COMBAT_PROBS) == 1


def test_untargeted_play_end_turn_and_potion() -> None:
    assert match_candidate(_COMBAT_CANDIDATES, {"kind": "play_card", "id": "DEFEND_IRONCLAD"}) == 2
    assert match_candidate(_COMBAT_CANDIDATES, {"kind": "end_turn"}) == 5
    assert match_candidate(_COMBAT_CANDIDATES, {"kind": "use_potion", "id": "FIRE_POTION", "target": "NIBBIT", "target_index": 1}) == 4
    assert match_candidate(_COMBAT_CANDIDATES, {"kind": "play_card", "id": "BASH"}) is None


@pytest.mark.parametrize(
    ("candidates", "choice", "expected"),
    [
        ([_candidate(0, "MapMove", "-> (1,3) Monster"), _candidate(1, "MapMove", "-> (2,3) Elite")],
         {"kind": "choose_map_node", "id": "2,3"}, 1),
        ([_candidate(0, "PickCardReward", "take BASH"), _candidate(1, "PickCardReward", "take ANGER"), _candidate(2, "CardRewardAlt", "SKIP")],
         {"kind": "card_choice", "id": "ANGER", "offered": ["BASH", "ANGER"]}, 1),
        ([_candidate(0, "PickCardReward", "take BASH"), _candidate(1, "CardRewardAlt", "SKIP")],
         {"kind": "card_choice", "id": None, "offered": ["BASH"]}, 1),
        ([_candidate(0, "RestOption", "HEAL"), _candidate(1, "RestOption", "SMITH")], {"kind": "rest_option", "id": "smith"}, 1),
        ([_candidate(0, "EventOption", "NEOW.pages.INITIAL.options.A"), _candidate(1, "EventOption", "NEOW.pages.INITIAL.options.B")],
         {"kind": "event_option", "id": "NEOW.pages.INITIAL.options.B.title"}, 1),
        ([_candidate(0, "Buy", "buy BASH (75)"), _candidate(1, "Buy", "buy ANCHOR (150)"), _candidate(2, "Leave", "leave")],
         {"kind": "buy_relic", "id": "ANCHOR"}, 1),
        ([_candidate(0, "SelectCard", "select STRIKE_IRONCLAD"), _candidate(1, "SelectCard", "select BASH")],
         {"kind": "card_upgraded", "id": "BASH"}, 1),
        ([_candidate(0, "PickRelic", "take ANCHOR"), _candidate(1, "SkipRelic", "skip")], {"kind": "relic_choice", "id": None}, 1),
    ],
)
def test_non_combat_choices_are_matched(candidates: list[dict[str, Any]], choice: dict[str, Any], expected: int) -> None:
    assert match_candidate(candidates, choice) == expected


def test_a_skip_is_not_guessed_when_several_alternative_buttons_exist() -> None:
    candidates = [_candidate(0, "PickCardReward", "take BASH"), _candidate(1, "CardRewardAlt", "SKIP"), _candidate(2, "CardRewardAlt", "REROLL")]
    assert match_candidate(candidates, {"kind": "card_choice", "id": None}) is None


# ---- 2. 没有模型时：规划器的一步 --------------------------------------------------------

_RAW_STATE = {
    "combat": {
        "hand": [{"index": 0, "card_id": "STRIKE_IRONCLAD", "name": "打击"}, {"index": 1, "card_id": "DEFEND_IRONCLAD", "name": "防御"}],
        "enemies": [{"index": 0, "enemy_id": "NIBBIT", "name": "小啃兽"}],
    },
    "map": {"available_nodes": [{"index": 0, "row": 3, "col": 1}, {"index": 1, "row": 3, "col": 2}]},
    "reward": {"cards": [{"index": 0, "card_id": "BASH", "name": "痛击"}, {"index": 1, "card_id": "ANGER", "name": "愤怒"}]},
    "rest": {"options": [{"index": 0, "option_id": "HEAL"}, {"index": 1, "option_id": "SMITH"}]},
    "event": {"options": [{"index": 0, "text_key": "E.options.A", "title": "接受"}, {"index": 1, "text_key": "E.options.B", "title": "拒绝"}]},
    "shop": {"cards": [{"index": 0, "card_id": "BASH"}], "relics": [{"index": 0, "relic_id": "ANCHOR", "name": "船锚"}], "potions": []},
    "potions": [{"index": 0, "potion_id": "FIRE_POTION", "name": "火焰药水"}],
}


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ({"action_type": "play_card", "kwargs": {"card_index": 1}}, {"kind": "play_card", "id": "DEFEND_IRONCLAD", "target_index": None}),
        ({"action_type": "play_card", "kwargs": {"card_index": 0, "target_index": 0}}, {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target_index": 0}),
        ({"action_type": "end_turn", "kwargs": {}}, {"kind": "end_turn", "id": None}),
        ({"action_type": "use_potion", "kwargs": {"option_index": 0}}, {"kind": "use_potion", "id": "FIRE_POTION", "target_index": None}),
        ({"action_type": "choose_map_node", "kwargs": {"option_index": 1}}, {"kind": "choose_map_node", "id": "2,3"}),
        ({"action_type": "choose_reward_card", "kwargs": {"option_index": 1}}, {"kind": "card_choice", "id": "ANGER"}),
        ({"action_type": "choose_rest_option", "kwargs": {"option_index": 1}}, {"kind": "rest_option", "id": "SMITH"}),
        ({"action_type": "choose_event_option", "kwargs": {"option_index": 0}}, {"kind": "event_option", "id": "E.options.A"}),
        ({"action_type": "buy_relic", "kwargs": {"option_index": 0}}, {"kind": "buy_relic", "id": "ANCHOR"}),
    ],
)
def test_planner_step_is_translated_into_the_choice_vocabulary(operation: dict[str, Any], expected: dict[str, Any]) -> None:
    assert planner_recommendation(operation, _RAW_STATE) == expected


def test_planner_step_that_cannot_be_named_is_dropped() -> None:
    assert planner_recommendation({"action_type": "play_card", "kwargs": {"card_index": 9}}, _RAW_STATE) is None
    assert planner_recommendation({"action_type": "proceed", "kwargs": {}}, _RAW_STATE) is None
    assert planner_recommendation(None, _RAW_STATE) is None


def test_compare_planner_same_different_and_not_this_step() -> None:
    play = {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target_index": 0}
    assert compare_planner(play, {"kind": "play_card", "id": "strike_ironclad", "target_index": 0}) is True
    assert compare_planner(play, {"kind": "play_card", "id": "STRIKE_IRONCLAD", "target_index": 1}) is False
    assert compare_planner(play, {"kind": "play_card", "id": "DEFEND_IRONCLAD"}) is False
    assert compare_planner(play, {"kind": "end_turn"}) is False, "推荐出牌、玩家结束回合：同一类选择，选得不同"
    assert compare_planner(play, {"kind": "card_choice", "id": "BASH"}) is None, "不是同一类选择：这条选择不属于这一步"
    assert compare_planner({"kind": "event_option", "id": "E.options.A"}, {"kind": "event_option", "id": "E.options.A.title"}) is True
    assert compare_planner({"kind": "card_select", "id": "BASH"}, {"kind": "card_upgraded", "id": "BASH"}) is True
    assert compare_planner({"kind": "card_choice", "id": None}, {"kind": "card_choice", "id": None}) is True


# ---- 3. 累加器：只记玩家的步 ------------------------------------------------------------


def _context() -> dict[str, Any]:
    return {"floor": 6, "act": 1, "room": "NIBBIT", "situation": "血 30/80", "hp": 30, "max_hp": 80, "names": collect_names(_RAW_STATE)}


def test_network_step_records_probabilities_and_readable_names() -> None:
    review = ChoiceReview()
    review.capture_network(_scored(), context=_context())

    steps = review.resolve([{"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": 1, "floor": 6, "act": 1, "round": 2}])

    assert len(steps) == 1
    step = steps[0]
    assert step["source"] == SOURCE_NETWORK and step["same"] is False
    assert step["chosen"] == "play 打击 -> 小啃兽" and step["recommended"] == "play 防御"
    assert step["p_chosen"] == 0.10 and step["p_recommended"] == 0.60 and step["gap"] == 0.50
    assert step["floor"] == 6 and step["round"] == 2 and step["room"] == "NIBBIT"
    assert review.pending is None, "一条推荐只被回答一次"
    assert review.resolve([{"kind": "end_turn"}]) == [], "没有新的推荐时，后面的选择不进对照"


def test_following_the_recommendation_counts_as_agreement() -> None:
    review = ChoiceReview()
    review.capture_network(_scored(), context=_context())

    step = review.resolve([{"kind": "play_card", "id": "DEFEND_IRONCLAD"}])[0]

    assert step["same"] is True and step["gap"] == 0.0
    assert review.summary()["agreed"] == 1 and review.summary()["steps"] == []


def test_a_step_the_plugin_made_itself_is_not_the_players_choice() -> None:
    review = ChoiceReview()
    review.capture_network(_scored(), context=_context())
    after = review.pending["captured_at"] + 1.0

    assert review.resolve([{"kind": "play_card", "id": "DEFEND_IRONCLAD"}], last_plugin_action_at=after) == []
    assert review.pending is None and review.steps == []


def test_a_choice_from_another_screen_keeps_the_recommendation_pending() -> None:
    review = ChoiceReview()
    review.capture_network(_scored(), context=_context())

    assert review.resolve([{"kind": "card_upgraded", "id": "BASH"}, {"kind": "choose_map_node", "id": "1,3"}]) == []
    assert review.pending is not None
    assert len(review.resolve([{"kind": "end_turn"}])) == 1


def test_a_late_record_still_answers_the_recommendation_it_belongs_to() -> None:
    """游戏有些记录写得晚（锻造要等选完牌、跳过的选牌奖励要等离开房间）：被顶掉的旧推荐还能被回答。"""
    review = ChoiceReview()
    rest = [_candidate(0, "RestOption", "HEAL"), _candidate(1, "RestOption", "SMITH")]
    select = [_candidate(0, "SelectCard", "select STRIKE_IRONCLAD"), _candidate(1, "SelectCard", "select BASH")]
    review.capture_network(_scored(rest, [0.2, 0.8], kind="REST_SITE", decision_id="rest"), context=_context())
    review.capture_network(_scored(select, [0.9, 0.1], kind="CARD_SELECT", decision_id="select"), context=_context())

    steps = review.resolve([{"kind": "rest_option", "id": "SMITH"}, {"kind": "card_upgraded", "id": "BASH"}])

    assert [(s["decision"], s["same"]) for s in steps] == [("REST_SITE", True), ("CARD_SELECT", False)]
    assert review.pending is None and review.backlog == []


def test_an_event_option_that_grants_a_relic_is_recorded_under_the_relics_title() -> None:
    candidates = [_candidate(0, "EventOption", "NEOW.pages.INITIAL.options.LARGE_CAPSULE"), _candidate(1, "EventOption", "NEOW.pages.INITIAL.options.PLANISPHERE")]
    assert match_candidate(candidates, {"kind": "event_option", "id": "PLANISPHERE.title"}) == 1


def test_a_self_targeted_potion_matches_a_candidate_without_a_target() -> None:
    candidates = [_candidate(0, "UsePotion", "use ATTACK_POTION"), _candidate(1, "EndTurn", "end turn")]
    assert match_candidate(candidates, {"kind": "use_potion", "id": "ATTACK_POTION", "target": "player"}) == 0


def test_planner_step_is_binary_and_new_run_clears_everything() -> None:
    review = ChoiceReview()
    review.begin_run("run-a")
    operation = {"action_type": "play_card", "kwargs": {"card_index": 1}, "source": "combat_solver"}
    review.capture_planner(operation, _RAW_STATE, key="k1", context=_context())
    captured_at = review.pending["captured_at"]
    review.capture_planner(operation, _RAW_STATE, key="k1", context=_context())
    assert review.pending["captured_at"] == captured_at, "同一刻的推荐不重复挂（否则「插件有没有动过手」的时间基准会被刷新）"

    step = review.resolve([{"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": 0, "floor": 6}])[0]

    assert step["source"] == SOURCE_PLANNER and step["same"] is False
    assert step["chosen"] == "play 打击 -> 小啃兽" and step["recommended"] == "play 防御"
    assert "gap" not in step and step["decision_source"] == "combat_solver"

    review.begin_run("run-a")
    assert len(review.steps) == 1, "同一局不清空"
    review.begin_run("run-b")
    assert review.steps == [] and review.pending is None


# ---- 4. 局末：差得最多的几步 ------------------------------------------------------------


def _step(source: str, same: bool, *, gap: float = 0.0, hp: int = 80, floor: int = 1) -> dict[str, Any]:
    return {"source": source, "same": same, "gap": gap, "hp": hp, "max_hp": 80, "floor": floor, "chosen": "a", "recommended": "b",
            "p_chosen": 0.1, "p_recommended": 0.1 + gap}


def test_network_steps_are_ranked_by_probability_gap_and_reported_in_play_order() -> None:
    steps = [
        _step(SOURCE_NETWORK, False, gap=0.30, floor=1),
        _step(SOURCE_NETWORK, True, floor=2),
        _step(SOURCE_NETWORK, False, gap=0.90, floor=3),
        _step(SOURCE_NETWORK, False, gap=MIN_GAP - 0.01, floor=4),
        _step(SOURCE_NETWORK, False, gap=0.50, floor=5),
        _step(SOURCE_PLANNER, False, hp=1, floor=6),
    ]

    picked = divergent_steps(steps, top=2)

    assert [s["floor"] for s in picked] == [3, 5], "取概率差最大的两步，按发生顺序给出；有网络的对照时不混入旧策略的"
    assert divergent_steps(steps, top=5)[0]["floor"] == 1
    assert all(s["floor"] != 4 for s in divergent_steps(steps, top=5)), "概率差太小的不算差得多"


def test_planner_only_steps_are_ranked_by_how_low_the_hp_was() -> None:
    steps = [_step(SOURCE_PLANNER, False, hp=70, floor=1), _step(SOURCE_PLANNER, False, hp=10, floor=2), _step(SOURCE_PLANNER, True, hp=1, floor=3)]

    assert [s["floor"] for s in divergent_steps(steps, top=1)] == [2]


def test_digest_names_both_choices_with_the_networks_probabilities() -> None:
    review = ChoiceReview()
    review.capture_network(_scored(), context=_context())
    review.resolve([{"kind": "play_card", "id": "STRIKE_IRONCLAD", "target": "NIBBIT", "target_index": 1, "floor": 6, "round": 2}])

    digest = review_digest({"outcome": "defeat", "character": "IRONCLAD", "floor": 7}, review.summary())

    assert "亲手打的 1 步里有 0 步和推荐一样" in digest
    assert "第6层 第2回合（NIBBIT）" in digest and "局面:血 30/80" in digest
    assert "玩家选:play 打击 -> 小啃兽（网络给它 10%）" in digest and "推荐:play 防御（60%）" in digest


def test_labels_and_names() -> None:
    assert choice_label({"kind": "card_choice", "id": None}) == "skip"
    assert choice_label({"kind": "choose_map_node", "id": "2,3"}) == "-> (2,3)"
    names = collect_names(_RAW_STATE)
    assert names["E.options.B"] == "拒绝" and names["ANCHOR"] == "船锚"
    assert humanize("use FIRE_POTION -> NIBBIT", names) == "use 火焰药水 -> 小啃兽"


# ---- 接线：service.review_tick ----------------------------------------------------------


class _Logger:
    def __getattr__(self, name: str):
        return lambda *a, **k: None


class _Client:
    def __init__(self) -> None:
        self.seq = 10
        self.choices: list[dict[str, Any]] = []
        self.asked: list[int] = []
        self.fail = False

    async def get_player_choices(self, since: int = 0) -> dict[str, Any]:
        self.asked.append(since)
        if self.fail:
            raise RuntimeError("Route not found")
        return {"latest_seq": self.seq, "choices": [c for c in self.choices if c["seq"] > since]}

    def add(self, **choice: Any) -> None:
        self.seq += 1
        self.choices.append({"seq": self.seq, **choice})


def _snapshot() -> dict[str, Any]:
    return {
        "run_id": "run-a", "screen": "COMBAT", "floor": 6, "act": 1, "character": "IRONCLAD", "raw_state": _RAW_STATE,
        "classification": {"state_name": "combat"},
        "summary_context": {"payload": {"current_hp": 30, "max_hp": 80}},
    }


def _service() -> STS2AutoplayService:
    service = STS2AutoplayService(_Logger(), lambda payload: None, lambda **k: None)
    service._policy_advisor.enabled = False  # 没有模型：推荐来自规划器
    return service


_PLANNED = {"action_type": "play_card", "kwargs": {"card_index": 1}, "source": "combat_solver"}


def test_review_tick_records_the_players_step_against_the_planner() -> None:
    service, client = _service(), _Client()
    client.add(kind="play_card", id="BASH")  # 插件接上之前的旧选择

    async def drive() -> None:
        await service.review_tick(client, _snapshot(), _PLANNED, {"text": "血 30/80"})   # 第一次只对表
        client.add(kind="play_card", id="STRIKE_IRONCLAD", target="NIBBIT", target_index=0, floor=6, act=1, round=2)
        await service.review_tick(client, _snapshot(), {"action_type": "end_turn", "kwargs": {}}, {"text": "血 30/80"})

    asyncio.run(drive())

    assert client.asked == [0, 11], "第一次接上只对表，之后从上次读到的序号接着读"
    steps = service._choice_review.steps
    assert len(steps) == 1 and steps[0]["same"] is False
    assert steps[0]["chosen"] == "play 打击 -> 小啃兽" and steps[0]["recommended"] == "play 防御"
    assert steps[0]["situation"] == "血 30/80" and steps[0]["hp"] == 30
    assert service._choice_review.pending["recommendation"] == {"kind": "end_turn", "id": None}, "认领完再挂这一刻的推荐"


def test_review_tick_ignores_steps_while_the_plugin_is_driving() -> None:
    service, client = _service(), _Client()
    service._state.autoplay_state = "running"

    async def drive() -> None:
        await service.review_tick(client, _snapshot(), _PLANNED, {})
        client.add(kind="play_card", id="STRIKE_IRONCLAD")
        await service.review_tick(client, _snapshot(), _PLANNED, {})

    asyncio.run(drive())

    assert service._choice_review.steps == [] and service._choice_review.pending is None


def test_review_tick_backs_off_when_the_mod_has_no_choices_route() -> None:
    service, client = _service(), _Client()
    client.fail = True

    async def drive() -> None:
        await service.review_tick(client, _snapshot(), _PLANNED, {})
        await service.review_tick(client, _snapshot(), _PLANNED, {})

    asyncio.run(drive())

    assert client.asked == [0], "旧版模组没有 /choices：问一次就歇 5 分钟，不每个 tick 撞一次"
