"""删牌排序与首选必须同口径（契约）。

★ 缺陷（2026-10-17 发现）：
`_rank_remove_options` 原为 `sort(key=lambda item: (-item[0], -item[1]))`（**分数高者在前**），
而 `_preferred_remove_option` 用 `min((score, -card_index))`（**分数低者优先删**）
—— 两者**方向相反**，但 docstring 却声称"同口径"。

`_score_remove_card` 的语义是「**该删程度**」：
  诅咒 −100（最该删）… 能量卡 +70（最不该删）。

后果：`ordered_candidates` 返回 `[best] + ranked`，扰动层（mischievous 取 `items[1]`）
拿到的第一个候选是**最不该删的那张**（如能量卡）⇒ 扰动会删错牌。

用户明确说「**删除哪张卡片**」很影响战斗，故此处把一致性钉死。
"""
from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner

CURSE = {"index": 0, "name": "诅咒", "type": "CURSE"}
STRIKE = {"index": 1, "name": "打击", "type": "ATTACK"}
ENERGY = {"index": 2, "name": "储能", "type": "SKILL", "resolved_rules_text": "获得2点能量。"}
DEFEND = {"index": 3, "name": "防御", "type": "SKILL", "resolved_rules_text": "获得5点格挡。"}


def _ctx(cards: list[dict]) -> dict:
    return {"payload": {"selection_cards": cards}}


def test_rank_head_equals_preferred() -> None:
    """★核心不变式：排序首位必须等于首选（否则扰动层会指向错牌）。"""
    planner = STS2HeuristicPlanner()
    ctx = _ctx([CURSE, STRIKE, ENERGY])
    assert planner._rank_remove_options(ctx)[0] == planner._preferred_remove_option(ctx, {})


def test_curse_is_removed_first_not_last() -> None:
    """诅咒最该删 ⇒ 必须排**第一**，绝不能排最后。"""
    planner = STS2HeuristicPlanner()
    ranked = planner._rank_remove_options(_ctx([CURSE, STRIKE, ENERGY]))
    assert ranked[0] == 0, f"诅咒应最先被删，实际顺序 {ranked}"
    energy_pos = ranked.index(2)
    assert energy_pos > 0, f"能量卡（最不该删）不得排第一，实际顺序 {ranked}"


def test_rank_order_is_by_removal_priority() -> None:
    """整体顺序 = 该删程度降序：诅咒 → 打击 → 防御 → 能量。"""
    planner = STS2HeuristicPlanner()
    ranked = planner._rank_remove_options(_ctx([CURSE, STRIKE, ENERGY, DEFEND]))
    assert ranked == [0, 1, 3, 2], f"顺序应为 诅咒→打击→防御→能量，实际 {ranked}"


def test_perturbation_candidate_is_second_best_not_worst() -> None:
    """★扰动候选（items[1]）应是**次该删**的，而不是最不该删的。"""
    planner = STS2HeuristicPlanner()
    ranked = planner._rank_remove_options(_ctx([CURSE, STRIKE, ENERGY]))
    assert ranked[1] == 1, f"次位应为打击(次该删)，实际 {ranked}"


@pytest.mark.parametrize(
    "cards",
    [
        [CURSE],
        [CURSE, STRIKE],
        [STRIKE, DEFEND],
        [CURSE, STRIKE, ENERGY, DEFEND],
    ],
)
def test_consistency_holds_for_all_shapes(cards: list[dict]) -> None:
    """任意候选组合下，rank 首位 == preferred。"""
    planner = STS2HeuristicPlanner()
    ctx = _ctx(cards)
    ranked = planner._rank_remove_options(ctx)
    preferred = planner._preferred_remove_option(ctx, {})
    if ranked:
        assert ranked[0] == preferred, f"cards={cards} ranked={ranked} preferred={preferred}"


def test_unremovable_excluded_from_both() -> None:
    """不可移除的卡在两个函数里都必须被排除。"""
    planner = STS2HeuristicPlanner()
    cards = [{"index": 0, "name": "诅咒", "type": "CURSE"},
             {"index": 1, "name": "核心卡", "unremovable": True},
             {"index": 2, "name": "打击"}]
    ranked = planner._rank_remove_options(_ctx(cards))
    assert 1 not in ranked
    assert planner._preferred_remove_option(_ctx(cards), {}) != 1


def test_empty_selection_never_raises() -> None:
    planner = STS2HeuristicPlanner()
    assert planner._rank_remove_options(_ctx([])) == []
    assert planner._rank_remove_options({"payload": {}}) == []
    assert planner._rank_remove_options({}) == []
