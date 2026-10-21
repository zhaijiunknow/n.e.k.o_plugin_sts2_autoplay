"""选卡质量契约：跳牌判定与负面卡惩罚。

对应两个真实缺陷（用户报障）：
1. 「变化卡事件会选到诅咒」—— `_preferred_reward_option` 对全负面候选正确返回 None，
   但旧代码 `:838-840` 拿到 None 仍照选（`_reward_kwargs(..., None)` 退回第一个选项）
   ⇒ 无条件选牌 ⇒ 卡组质量单调下降。
2. 诅咒/状态牌与「防御」同为 0 分（评分函数只加正向分、无负向惩罚），
   平分时被 index 次序选中 ⇒ 诅咒被收进卡组。

基线（2026-10-17，用户 80 小时经验 + 50 局数据）：
  act 分布 = {0: 45, 1: 5, 2: 0} ⇒ 从未到达第三层。
  卡组质量是用户指出的核心败因（战斗已交给 mod 的 CombatSolver）。
"""
from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def _dv(**kw):
    """dynamic_values 的真实形状是 list[{name,current_value}]，不是普通 dict。"""
    return [{"name": k, "current_value": v} for k, v in kw.items()]


def _ctx(cards: list[dict], actions: list[dict]) -> dict:
    return {
        "classification": {"state_name": "reward"},
        "snapshot": {"screen": "reward", "available_actions": actions},
        "summary_context": {"payload": {"reward": {"card_options": cards}, "deck": {"cards": []}}},
        "strategy_context": {"preferences": {}, "strategy_directives": {"must": [], "prefer": [], "avoid": []}},
    }


SKIP_ACTIONS = [{"type": "choose_reward_card"}, {"type": "skip_reward_cards"}]
NO_SKIP_ACTIONS = [{"type": "choose_reward_card"}, {"type": "claim_reward"}]

CURSE = {"index": 0, "name": "痛苦", "type": "CURSE", "description": "无法打出"}
DEFEND = {"index": 1, "name": "防御", "type": "SKILL", "description": "获得5点格挡", "dynamic_values": _dv(Block=5)}
STRIKE = {"index": 2, "name": "打击", "type": "ATTACK", "description": "造成6点伤害", "dynamic_values": _dv(Damage=6)}


def _pick(cards: list[dict], actions: list[dict] | None = None):
    planner = STS2HeuristicPlanner()
    op = planner.plan(_ctx(cards, actions or SKIP_ACTIONS))
    assert op is not None, "plan() 不应返回 None"
    decision = op.as_dict() if hasattr(op, "as_dict") else op
    return decision.get("action_type"), decision.get("reason"), (decision.get("kwargs") or {}).get("option_index")


# --------------------------------------------------------------------------
# 1) 跳牌判定必须被兑现
# --------------------------------------------------------------------------

def test_all_junk_candidates_triggers_skip() -> None:
    """★核心：全负面候选时必须跳牌，而不是把诅咒收进卡组。"""
    action, reason, _ = _pick([CURSE])
    assert action == "skip_reward_cards", f"全诅咒候选应跳牌，实际 {action}"
    assert reason == "reward_skip_low_value"


def test_multiple_junk_candidates_also_skip() -> None:
    junk = [
        {"index": 0, "name": "伤口", "type": "STATUS", "description": "无法打出"},
        {"index": 1, "name": "灼伤", "type": "STATUS", "description": "无法打出"},
    ]
    action, reason, _ = _pick(junk)
    assert action == "skip_reward_cards"
    assert reason == "reward_skip_low_value"


def test_good_candidate_still_picked() -> None:
    """不得因为跳牌改动而误跳好卡。"""
    action, reason, index = _pick([STRIKE])
    assert action == "choose_reward_card"
    assert reason == "reward_pick_card"
    assert index == 2


def test_skip_absent_falls_back_to_old_behavior() -> None:
    """★向前兼容：动作清单里没有 skip_reward_cards 时，行为必须与改动前一致（照选）。"""
    action, reason, _ = _pick([CURSE], NO_SKIP_ACTIONS)
    assert action == "choose_reward_card", "无跳牌动作时不得改变既有行为"
    assert reason == "reward_pick_card"


# --------------------------------------------------------------------------
# 2) 负面卡惩罚（诅咒/状态牌不得与普通卡同分）
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "card",
    [
        {"name": "痛苦", "type": "CURSE", "description": "无法打出"},
        {"name": "Regret", "type": "CURSE", "description": "Unplayable"},
        {"name": "伤口", "type": "STATUS", "description": "无法打出"},
        {"name": "灼伤", "type": "STATUS", "description": "无法打出"},
    ],
)
def test_negative_cards_score_below_zero(card: dict) -> None:
    """诅咒/状态牌必须为负分，否则会被当成"可以拿的卡"。"""
    planner = STS2HeuristicPlanner()
    assert planner._score_reward_card(card, {}, []) < 0, f"{card['name']} 应被扣分"


@pytest.mark.parametrize(
    "card",
    [
        # ★ 这些卡的描述里**没有**诅咒类关键词（"无法打出"等），
        #   只靠状态牌分支扣分 ⇒ 删除状态牌惩罚后它们会退化到 0 分并被误选。
        {"name": "Wound", "type": "STATUS", "description": "A useless card."},
        {"name": "Dazed", "type": "STATUS", "description": "Ethereal."},
        {"name": "Slimed", "type": "STATUS", "description": "Exhaust."},
        {"name": "Void", "type": "STATUS", "description": "Lose energy."},
    ],
)
def test_status_cards_without_curse_wording_are_penalized(card: dict) -> None:
    """★独立覆盖状态牌惩罚：描述不含诅咒措辞时仍必须被扣分。

    否则「删除状态牌惩罚」的变异会逃逸（测试无法区分两个分支）。
    """
    planner = STS2HeuristicPlanner()
    score = planner._score_reward_card(card, {}, [])
    assert score < 0, f"{card['name']} 仅靠名称就应被扣分，实际 {score}"
    # 且必须低于任何真实可用卡
    assert score < planner._score_reward_card(DEFEND, {}, [])


def test_curse_scores_below_any_real_card() -> None:
    """诅咒必须低于任何真实可用卡（防御/打击），不得同分。"""
    planner = STS2HeuristicPlanner()
    curse = planner._score_reward_card(CURSE, {}, [])
    defend = planner._score_reward_card(DEFEND, {}, [])
    strike = planner._score_reward_card(STRIKE, {}, [])
    assert curse < defend, "诅咒不得与防御同分"
    assert curse < strike, "诅咒不得与打击同分"
    assert defend > 0 and strike > 0


# --------------------------------------------------------------------------
# 3) 好卡必须被选中（不得被负面卡或劣卡抢走）
# --------------------------------------------------------------------------

def test_best_card_wins_among_mixed_candidates() -> None:
    """混合候选里选分数最高的那张（铁斩波 攻+格 应胜过纯防御/纯攻击）。"""
    iron_wave = {
        "index": 4, "name": "铁斩波", "type": "ATTACK",
        "description": "造成5点伤害并获得5点格挡",
        "dynamic_values": _dv(Damage=5, Block=5),
    }
    cards = [CURSE, DEFEND, STRIKE, iron_wave]
    action, reason, index = _pick(cards)
    assert action == "choose_reward_card"
    assert reason == "reward_pick_card"
    assert index == 4, f"应选铁斩波(index=4)，实际 {index}"


def test_curse_never_selected_when_real_card_available() -> None:
    """★对应用户报障 B：有真实卡可选时，绝不能选到诅咒。"""
    cards = [CURSE, DEFEND]
    _, _, index = _pick(cards)
    assert index != 0, "有防御可选时不得选诅咒"


def test_dynamic_value_shape_is_list_of_dicts() -> None:
    """固化 dynamic_values 的真实形状，避免 fixture 用错形状导致测试空转。"""
    planner = STS2HeuristicPlanner()
    with_values = {"name": "打击", "type": "ATTACK", "dynamic_values": _dv(Damage=9)}
    without = {"name": "打击", "type": "ATTACK"}
    assert planner._score_reward_card(with_values, {}, []) > planner._score_reward_card(without, {}, [])
    assert planner._reward_dynamic_value(with_values, "Damage") == 9
    # 传普通 dict（错误形状）必须返回 0 —— 显式钉死"形状不对就不生效"
    assert planner._reward_dynamic_value({"dynamic_values": {"Damage": 9}}, "Damage") == 0
