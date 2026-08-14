from __future__ import annotations

import json

import pytest
from plugin.plugins.sts2_autoplay.danmu_text import _CORPUS_PATH, build_viewer_danmu


def _corpus() -> dict[str, list[str]]:
    return json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))


def _payload(**overrides: object) -> dict:
    base = {
        "screen": "COMBAT",
        "summary_kind": "combat",
        "player": {"current_hp": 60, "max_hp": 75, "block": 0},
        "enemies": [{"name": "树枝史莱姆（小）", "intent": "TACKLE_MOVE"}],
        "companion_evaluation": {"primary_message": "建议优先防御。"},
    }
    base.update(overrides)
    return base


def _rule_texts(rules: dict[str, object], key: str) -> set[str]:
    """从规则词条对象数组提取文本集合。"""
    out: set[str] = set()
    for entry in rules.get(key, []):
        if isinstance(entry, dict) and entry.get("text"):
            out.add(str(entry["text"]))
    return out


@pytest.mark.unit
def test_combat_high_hp_picks_combat_related() -> None:
    hit = build_viewer_danmu(_payload())
    assert hit
    text = hit["text"]
    assert hit["style"] in ("catgirl", "narration")
    # 规则优先（NakedHit 裸奔：默认 block=0 + TACKLE 意图）或 combat 分桶兜底
    from plugin.plugins.sts2_autoplay.danmu_spire import _load_rules
    valid = _rule_texts(_load_rules(), "NakedHit") | set(_corpus()["combat"])
    assert text in valid
    assert "TACKLE_MOVE" not in text
    assert "树枝史莱姆" not in text


@pytest.mark.unit
def test_combat_low_hp_picks_low_hp_related() -> None:
    hit = build_viewer_danmu(_payload(player={"current_hp": 18, "max_hp": 75}))
    assert hit
    # 规则优先（NakedHit 裸奔 / LowHpElite）或分桶兜底（low_hp），都应是低血战斗相关
    from plugin.plugins.sts2_autoplay.danmu_spire import _load_rules
    rules = _load_rules()
    valid = _rule_texts(rules, "LowHpElite") | _rule_texts(rules, "NakedHit") | set(_corpus()["low_hp"])
    assert hit["text"] in valid


@pytest.mark.unit
def test_reward_screen_no_rule_returns_none() -> None:
    # 无规则命中时返回 None（不做无条件分桶兜底）
    hit = build_viewer_danmu(_payload(screen="REWARD", summary_kind="reward", player={}, enemies=[]))
    assert hit is None


@pytest.mark.unit
def test_shop_screen_no_rule_returns_none() -> None:
    hit = build_viewer_danmu(_payload(screen="SHOP", summary_kind="shop", player={}, enemies=[]))
    assert hit is None


@pytest.mark.unit
def test_death_screen_picks_from_death_bucket() -> None:
    hit = build_viewer_danmu(_payload(screen="GAME_OVER", summary_kind="game_over", player={}, enemies=[]))
    assert hit
    # 规则优先（PlayerDeath）或 death 分桶兜底
    from plugin.plugins.sts2_autoplay.danmu_spire import _load_rules
    valid = _rule_texts(_load_rules(), "PlayerDeath") | set(_corpus()["death"])
    assert hit["text"] in valid


@pytest.mark.unit
def test_corpus_has_no_placeholder_phrases() -> None:
    corpus = _corpus()
    for bucket, phrases in corpus.items():
        for p in phrases:
            assert "{" not in p and "}" not in p, f"placeholder in {bucket}: {p}"


@pytest.mark.unit
def test_empty_payload_returns_none() -> None:
    assert build_viewer_danmu({}) is None
