"""猫娘台词不许漏内部 id —— 引擎枚举、动作 id、卡牌 id 都不能直接念出来。

**实盘证据（2026-10-07 06:05，插件日志 `[sts2_instant_line]`）**：

    key=MAP|7|None    text=当前可走路线里，可以优先考虑：RestSite。
    key=MAP|8|None    text=当前可走路线里，可以优先考虑：Monster。
    key=REWARD|7|None text=建议优先贴合策略偏好的奖励：偏好这类选择：end_turn。

玩家在游戏里看到的是直播弹幕，念出 `RestSite` / `end_turn` 就是穿帮。这两处都出在
`companion_evaluator._suggestion`：地图分支把 mod 侧的引擎枚举直接当台词，奖励分支把
策略指令原文（脚手架 + 动作 id）排在"念具体牌名"前面。

判据是**能不能说给人听**：中文照念；纯 ASCII 的 snake_case / CamelCase 一律不算人话，
要么翻成中文标签，要么去下划线拆词，要么整条作废退到下一个分支。
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.companion_evaluator import (
    _MAP_NODE_TYPE_LABELS,
    STS2CompanionEvaluator,
    _clean_preference,
    _display_name,
    _display_node_type,
    _enemy_intent_label,
    _trim_trailing_punct,
)
from plugin.plugins.sts2_autoplay.heuristic_planner import _MAP_NODE_TYPE_ALIASES


def _evaluate(
    *,
    kind: str,
    payload: dict,
    directives: dict | None = None,
) -> str:
    """按 `evaluate()` 的真实入口跑一遍，返回猫娘这句台词。"""
    evaluator = STS2CompanionEvaluator(i18n=None)
    result = evaluator.evaluate(
        summary_context={"payload": dict(payload)},
        situation_summary={"kind": kind},
        strategy_context={
            "strategy_name": "test",
            "strategy_directives": dict(directives or {}),
        },
        runtime_state=None,
    )
    return str(result.get("primary_message") or "")


# ---- 节点枚举翻人话 -----------------------------------------------------------------------


@pytest.mark.unit
def test_every_weight_key_has_a_display_label() -> None:
    """权重键与显示标签必须同步 —— 加了一种新房间类型，两边都得有。

    没有这条，`heuristic_planner` 认得某种节点、而台词层只会原样念枚举名，穿帮就会复发。
    """
    labels = set(_MAP_NODE_TYPE_LABELS)
    missing = sorted(set(_MAP_NODE_TYPE_ALIASES.values()) - labels)
    assert not missing, f"这些节点类型有中文标签缺失: {missing}"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("RestSite", "营地"),
        ("rest", "营地"),
        ("Monster", "普通敌人"),
        ("SecondBoss", "Boss"),
        ("Shop", "商店"),
        ("Treasure", "宝箱"),
        ("Unknown", "未知房间"),
    ],
)
def test_node_type_is_translated(raw: str, expected: str) -> None:
    assert _display_node_type(raw) == expected


@pytest.mark.unit
def test_unknown_node_type_never_leaks_camel_case() -> None:
    """将来引擎加了新枚举，也不能原样穿帮（至少拆成可读的词）。"""
    rendered = _display_node_type("SomeNewNode")
    assert "SomeNewNode" not in rendered
    assert rendered == "Some New Node"


@pytest.mark.unit
def test_chinese_node_type_passes_through() -> None:
    assert _display_node_type("营地") == "营地"


@pytest.mark.unit
def test_map_suggestion_speaks_chinese() -> None:
    """实盘那条 `可以优先考虑：RestSite。` 的回归测试。"""
    text = _evaluate(
        kind="map",
        payload={"travelable_nodes": [{"index": 0, "type": "RestSite"}]},
        directives={"preferred_option_index": 0},
    )
    assert "RestSite" not in text, f"引擎枚举漏进台词了: {text}"
    assert "营地" in text, f"应该念中文标签: {text}"


# ---- 策略偏好：只念说给人听的那部分 -------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    ["偏好这类选择：end_turn", "end_turn", "play_card", "GUILDING_STAR", "", None],
)
def test_unreadable_preference_is_dropped(raw: object) -> None:
    assert _clean_preference(raw) == ""


@pytest.mark.unit
def test_readable_preference_is_kept() -> None:
    assert _clean_preference("优先高质量攻击牌") == "优先高质量攻击牌"
    # 带脚手架但有中文尾巴：只留尾巴，别把脚手架念出来。
    assert _clean_preference("偏好这类选择：高质量攻击牌") == "高质量攻击牌"


@pytest.mark.unit
def test_reward_with_raw_action_id_falls_back_to_card_name() -> None:
    """实盘那条 `...：偏好这类选择：end_turn。` 的回归测试。"""
    text = _evaluate(
        kind="reward",
        payload={"reward_cards": [{"index": 0, "card_id": "RAGE", "name": "暴走"}]},
        directives={"preferred_option_index": 0, "prefer": ["偏好这类选择：end_turn"]},
    )
    assert "end_turn" not in text, f"动作 id 漏进台词了: {text}"
    assert "暴走" in text, f"应该退到念具体牌名: {text}"


@pytest.mark.unit
def test_reward_with_readable_preference_still_prefers_it() -> None:
    """把闸加上不等于把"策略优先"这条行为改掉：能念就还是念偏好。"""
    text = _evaluate(
        kind="reward",
        payload={"reward_cards": [{"index": 0, "card_id": "RAGE", "name": "暴走"}]},
        directives={"preferred_option_index": 0, "prefer": ["优先高质量攻击牌"]},
    )
    assert "优先高质量攻击牌" in text, text


@pytest.mark.unit
def test_reward_card_id_fallback_is_humanized() -> None:
    """只有 id、没有中文名时，别念 `GUILDING_STAR`。"""
    text = _evaluate(
        kind="reward",
        payload={"reward_cards": [{"index": 0, "card_id": "GUILDING_STAR"}]},
        directives={"preferred_option_index": 0},
    )
    assert "GUILDING_STAR" not in text, text
    assert "guilding star" in text, text


@pytest.mark.unit
def test_display_name_keeps_chinese_and_splits_camel_case() -> None:
    assert _display_name("暴走") == "暴走"
    assert _display_name("GUILDING_STAR") == "guilding star"
    assert _display_name("TwinStrike") == "Twin Strike"


# ---- 敌人意图：优先结构化，别念招式 id -----------------------------------------------------


@pytest.mark.unit
def test_enemy_intent_prefers_structured_data_with_damage() -> None:
    """有 `intents[]` 就说「攻击 9」—— 这是真看得懂的信息。"""
    payload_enemy = {"name": "树叶史莱姆（中）", "intent": "CLUMP_SHOT"}
    raw_enemy = {"intents": [{"intent_type": "Attack", "damage": 9, "hits": 1, "total_damage": 9}]}
    assert _enemy_intent_label(payload_enemy, raw_enemy) == "攻击 9"


@pytest.mark.unit
def test_enemy_intent_shows_multi_hit() -> None:
    raw_enemy = {"intents": [{"intent_type": "Attack", "hits": 3, "total_damage": 9}]}
    assert _enemy_intent_label({"intent": "X"}, raw_enemy) == "攻击 9x3"


@pytest.mark.unit
def test_enemy_intent_non_attack_has_no_number() -> None:
    raw_enemy = {"intents": [{"intent_type": "Defend"}]}
    assert _enemy_intent_label({"intent": "X"}, raw_enemy) == "防御"


@pytest.mark.unit
@pytest.mark.parametrize("raw_intent", ["UNSET_MOVE", "unset", "unknown", "None", ""])
def test_unknown_intent_says_nothing(raw_intent: str) -> None:
    """「还没决定」就别硬念一个 id 出来 —— 实盘念过 `敌:蛮兽/UNSET_MOVE`。"""
    assert _enemy_intent_label({"name": "蛮兽", "intent": raw_intent}) == ""


@pytest.mark.unit
def test_structured_unknown_intent_also_says_nothing() -> None:
    """结构化路径的 `intent_type: Unknown` 同理：说"意图不明"等于没说。"""
    assert _enemy_intent_label({"intent": "X"}, {"intents": [{"intent_type": "Unknown"}]}) == ""


# ---- 标点别叠：名字自带标点 + 模板自带标点 ------------------------------------------------


@pytest.mark.unit
def test_trailing_punctuation_is_trimmed() -> None:
    assert _trim_trailing_punct("下去！") == "下去"
    assert _trim_trailing_punct("药水的未来？") == "药水的未来"
    assert _trim_trailing_punct("暴走") == "暴走"
    assert _trim_trailing_punct("") == ""
    assert _trim_trailing_punct(None) == ""


@pytest.mark.unit
def test_selection_line_does_not_double_punctuation() -> None:
    """实盘念过「可以优先看：下去！。」（卡名自带叹号 + 模板句号）。"""
    text = _evaluate(
        kind="selection",
        payload={"selection_cards": [{"index": 0, "name": "下去！"}]},
        directives={"preferred_option_index": 0},
    )
    assert "下去！。" not in text, text
    assert "下去" in text, text


@pytest.mark.unit
def test_event_line_does_not_double_punctuation() -> None:
    """实盘念过「当前事件 药水的未来？，建议优先看…」。"""
    text = _evaluate(kind="event", payload={"event_name": "药水的未来？"})
    assert "？，" not in text, text
    assert "药水的未来" in text, text


@pytest.mark.unit
def test_known_move_id_is_humanized_when_no_structured_data() -> None:
    """拿不到结构化意图时，至少别做大写喊叫。"""
    assert _enemy_intent_label({"name": "飞蝇菌子", "intent": "FRAIL_SPORE"}) == "frail spore"
    assert _enemy_intent_label({"name": "蛮兽", "intent": "RIP_AND_TEAR_MOVE"}) == "rip and tear move"


@pytest.mark.unit
def test_enemy_intent_accepts_structured_intent_type_field() -> None:
    assert _enemy_intent_label({"intent_type": "Attack", "move_id": "SEA_KICK_MOVE"}) == "攻击"


@pytest.mark.unit
def test_instant_line_speaks_intent_in_chinese() -> None:
    """端到端走**即时弹幕**那条真实路径（`service._instant_line_text`）。

    实盘线上这里念出过 `敌:树叶史莱姆（中）/STICKY_SHOT`、`敌:蛮兽/UNSET_MOVE`。
    有结构化的 `intents[]` 就该念「攻击 N」，哨兵值就不念。
    """
    from plugin.plugins.sts2_autoplay.service import STS2AutoplayService

    class _Logger:
        def info(self, *args: object, **kwargs: object) -> None: ...
        def warning(self, *args: object, **kwargs: object) -> None: ...
        def error(self, *args: object, **kwargs: object) -> None: ...
        def debug(self, *args: object, **kwargs: object) -> None: ...

    service = STS2AutoplayService(_Logger(), lambda payload: None, lambda **kwargs: None)
    snapshot = {
        "screen": "COMBAT",
        "floor": 11,
        "turn": 3,
        "raw_state": {
            "combat": {
                "enemies": [
                    {"intents": [{"intent_type": "Attack", "hits": 1, "total_damage": 12}]},
                    {"intents": [{"intent_type": "Unknown"}]},
                ]
            }
        },
    }
    payload = {
        "screen": "COMBAT",
        "summary_kind": "combat",
        "player": {"current_hp": 57, "max_hp": 75},
        "enemies": [
            {"name": "飞蝇菌子", "intent": "FRAIL_SPORE"},
            {"name": "蛮兽", "intent": "UNSET_MOVE"},
        ],
    }
    text = service._instant_line_text(snapshot, {}, payload)
    assert "攻击 12" in text, text
    assert "FRAIL_SPORE" not in text, text
    assert "UNSET_MOVE" not in text, text
    # 「还没决定」的敌人只留名字，不念一个哨兵值出来。
    assert "蛮兽" in text and "蛮兽/" not in text, text
