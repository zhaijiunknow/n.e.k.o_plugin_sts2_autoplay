from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.danmu_spire import detect_trigger, pick_rule_phrase


def _combat(**overrides: object) -> dict:
    base = {
        "screen": "COMBAT",
        "summary_kind": "combat",
        "player": {"current_hp": 60, "max_hp": 75, "block": 10},
        "enemies": [{"name": "小怪", "intent": "DEFEND_MOVE"}],
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_detect_naked_hit_when_no_block_and_attack_intent() -> None:
    payload = _combat(player={"current_hp": 60, "max_hp": 75, "block": 0},
                      enemies=[{"name": "小怪", "intent": "TACKLE_MOVE"}])
    assert detect_trigger(payload) == "NakedHit"


@pytest.mark.unit
def test_detect_player_death() -> None:
    payload = {"screen": "GAME_OVER", "summary_kind": "game_over", "player": {}, "enemies": []}
    assert detect_trigger(payload) == "PlayerDeath"


@pytest.mark.unit
def test_detect_rest_by_hp() -> None:
    full = {"screen": "REST", "summary_kind": "rest", "player": {"current_hp": 75, "max_hp": 75}, "enemies": []}
    assert detect_trigger(full) == "FullHpRestSiteSleep"
    low = {"screen": "REST", "summary_kind": "rest", "player": {"current_hp": 20, "max_hp": 75}, "enemies": []}
    assert detect_trigger(low) == "LowHpSkippedRest"
    mid = {"screen": "REST", "summary_kind": "rest", "player": {"current_hp": 50, "max_hp": 75}, "enemies": []}
    assert detect_trigger(mid) == "RestSiteSleep"


@pytest.mark.unit
def test_detect_low_hp_elite_and_many_enemies() -> None:
    low = _combat(player={"current_hp": 15, "max_hp": 75, "block": 5})
    assert detect_trigger(low) == "LowHpElite"
    many = _combat(enemies=[{"name": "a", "intent": "DEFEND"}, {"name": "b", "intent": "BUFF"}, {"name": "c", "intent": "HEAVY"}])
    assert detect_trigger(many) == "StrongMonster"


@pytest.mark.unit
def test_detect_attack_defense_card_removed() -> None:
    """手牌同时有攻牌+防牌不再触发铁斩波（铁斩波仅由单张攻防一体牌事件触发）。"""
    payload = _combat(cards=[{"name": "打击"}, {"name": "防御"}, {"name": "闪电"}])
    assert detect_trigger(payload) is None


@pytest.mark.unit
def test_detect_none_for_plain_combat() -> None:
    payload = _combat()
    assert detect_trigger(payload) is None


@pytest.mark.unit
def test_attack_defense_phrase_from_rules() -> None:
    payload = _combat(cards=[{"name": "打击"}, {"name": "防御"}])
    hit = pick_rule_phrase("AttackDefenseCard", payload)
    assert hit
    assert "{" not in hit["text"] and "}" not in hit["text"]


@pytest.mark.unit
def test_pick_rule_phrase_returns_from_rules() -> None:
    payload = _combat(player={"current_hp": 60, "max_hp": 75, "block": 0},
                      enemies=[{"name": "小怪", "intent": "TACKLE_MOVE"}])
    hit = pick_rule_phrase("NakedHit", payload)
    assert hit
    assert hit["style"] in ("catgirl", "narration")
    assert "{" not in hit["text"] and "}" not in hit["text"]


@pytest.mark.unit
def test_pick_rule_phrase_fills_hp_placeholder() -> None:
    payload = {"screen": "REST", "summary_kind": "rest", "player": {"current_hp": 20, "max_hp": 75}, "enemies": []}
    hit = pick_rule_phrase("LowHpSkippedRest", payload)
    assert hit
    # 占位符必须全部解析干净（{hp}→20，无占位符词条原样）
    assert "{" not in hit["text"] and "}" not in hit["text"]


@pytest.mark.unit
def test_detect_scroll_max_hp_lost_with_previous() -> None:
    prev = _combat(player={"current_hp": 60, "max_hp": 80, "block": 10})
    cur = _combat(player={"current_hp": 60, "max_hp": 70, "block": 10})
    assert detect_trigger(cur, prev) == "ScrollMaxHpLost"


@pytest.mark.unit
def test_detect_streak_break_with_previous() -> None:
    prev = _combat(player={"current_hp": 75, "max_hp": 75, "block": 10})
    cur = _combat(player={"current_hp": 40, "max_hp": 75, "block": 10})
    assert detect_trigger(cur, prev) == "StreakBreak"


@pytest.mark.unit
def test_event_rules_need_previous() -> None:
    cur = _combat(player={"current_hp": 40, "max_hp": 75, "block": 10})
    assert detect_trigger(cur) is None


@pytest.mark.unit
def test_detect_encountered_before_when_seen_before() -> None:
    payload = _combat(enemies=[{"name": "树枝史莱姆", "intent": "DEFEND_MOVE"}])
    assert detect_trigger(payload, seen_before=True) == "EncounteredBefore"
    # 首次（未见过）不触发
    assert detect_trigger(payload, seen_before=False) is None
