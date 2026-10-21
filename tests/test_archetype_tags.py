"""卡牌流派推断契约。

★ 背景（2026-10-17 实机校准）：
游戏跑**中文**，但 `_infer_archetype_tags` 的关键词表原先**严重英文偏置**
（`orb/zap/coolheaded/block/defend/draw/...`），
实测对真实卡组（打击/防御/护卫/出击/守墓人…）几乎**全部不命中**。
且它**只读 `name`/`card_id`，完全不看效果文本**。

用户明确指出「卡牌流派的抉择」是败因之一，故此处把行为钉死。
"""
from __future__ import annotations

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner


def _tags(cards: list[dict]) -> list[str]:
    return STS2HeuristicPlanner()._infer_archetype_tags({"cards": cards})


def test_chinese_block_card_is_defense() -> None:
    """实机真实文本「获得5点格挡。」必须识别为防御。"""
    assert "defense" in _tags([{"name": "防御", "resolved_rules_text": "获得5点格挡。"}])


def test_chinese_draw_with_digit_insertion() -> None:
    """★真实文本是「抽2张牌」（数字插在中间）⇒ 子串关键词"抽牌"不命中，必须用正则。"""
    assert "draw" in _tags([{"name": "思虑", "resolved_rules_text": "抽2张牌。"}])


def test_chinese_draw_with_hanzi_numeral() -> None:
    """中文数字形态「抽三张牌。」同样必须命中。"""
    assert "draw" in _tags([{"name": "谋略", "resolved_rules_text": "抽三张牌。"}])


def test_draw_pile_is_not_draw_archetype() -> None:
    """★「抽牌堆」(draw pile) 不是抽牌流派 —— 实机「守墓人」含此措辞，必须排除。

    若不排除，任何提及"放入抽牌堆"的卡都会被误判为抽牌流派。
    """
    cards = [{"name": "守墓人", "resolved_rules_text": "获得8点格挡。将一张灵魂放入你的抽牌堆中。"}]
    assert "draw" not in _tags(cards), "『抽牌堆』不得被判为抽牌流派"


def test_english_still_works() -> None:
    """英文文本（mod 回退/英文客户端）不得因补中文而失效。"""
    assert "draw" in _tags([{"name": "Skim", "rules_text": "Draw 3 cards."}])
    assert "defense" in _tags([{"name": "Defend", "rules_text": "Gain 5 Block."}])


def test_strength_from_effect_text() -> None:
    """「获得2点力量。」⇒ strength（原先只看卡名，效果文本被忽略）。"""
    assert "strength" in _tags([{"name": "力量", "resolved_rules_text": "获得2点力量。"}])


def test_orb_focus_chinese() -> None:
    assert "orb_focus" in _tags([{"name": "电球", "resolved_rules_text": "充能一个电球。"}])


def test_energy_chinese() -> None:
    assert "energy" in _tags([{"name": "储能", "resolved_rules_text": "获得2点能量。"}])


def test_effect_text_is_read_not_just_name() -> None:
    """★卡名猜不出效果（如实机「护卫」= BODYGUARD「召唤5。」）
    ⇒ 必须读 rules_text/resolved_rules_text，而不能只看卡名。"""
    # 卡名毫无提示，效果是格挡 ⇒ 必须识别
    assert "defense" in _tags([{"name": "XYZ神秘卡", "rules_text": "获得7点格挡。"}])
    # 卡名像抽牌，效果其实不是 ⇒ 不得误判
    assert "draw" not in _tags([{"name": "抽奖券", "rules_text": "造成9点伤害。"}])


def test_empty_and_malformed_deck_never_raises() -> None:
    """向前兼容：空/畸形卡组不得抛异常。"""
    assert _tags([]) == []
    planner = STS2HeuristicPlanner()
    assert planner._infer_archetype_tags({}) == []
    assert planner._infer_archetype_tags({"cards": "not-a-list"}) == []
    assert planner._infer_archetype_tags({"cards": [None, 1, "x"]}) == []


def test_real_live_deck_shape() -> None:
    """实机 12 张卡组（2026-10-17 抓取）应识别出防御，且不含 draw。"""
    deck = [
        {"name": "打击", "card_id": "STRIKE_NECROBINDER", "card_type": "Attack", "resolved_rules_text": "造成6点伤害。"},
        {"name": "防御", "card_id": "DEFEND_NECROBINDER", "card_type": "Skill", "resolved_rules_text": "获得5点格挡。"},
        {"name": "护卫", "card_id": "BODYGUARD", "card_type": "Skill", "resolved_rules_text": "召唤5。"},
        {"name": "守墓人", "card_id": "GRAVE_WARDEN", "card_type": "Skill",
         "resolved_rules_text": "获得8点格挡。将一张灵魂放入你的抽牌堆中。"},
    ]
    tags = _tags(deck)
    assert "defense" in tags
    assert "draw" not in tags
