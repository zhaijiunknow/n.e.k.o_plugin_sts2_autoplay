from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.danmu_events import DanmuEvent, DanmuEventTracker
from plugin.plugins.sts2_autoplay.danmu_spire import DanmuTriggerHit, match_events, pick_rule_phrase


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None


def _ev(type_: str, *, phase: str = "run", **ctx: object) -> DanmuEvent:
    return DanmuEvent(type=type_, context=ctx, phase=phase)


def _tracker(*, character: str = "DEFECT", act: int = 1) -> DanmuEventTracker:
    t = DanmuEventTracker(DummyLogger())
    # 用一次 feed 建立 _prev，让 act/character 属性生效
    raw = {
        "run_id": "run-1",
        "screen": "map",
        "run": {"floor": 1, "act": act, "gold": 100, "current_hp": 60, "max_hp": 75},
        "deck": {"cards": []},
    }
    t.feed(raw, {"raw_state": raw, "screen": "map", "character": character, "in_combat": False, "floor": 1, "act": act})
    return t


def _triggers(hits: list[DanmuTriggerHit]) -> set[str]:
    return {h.trigger for h in hits}


@pytest.mark.unit
def test_combat_started_strong_monster() -> None:
    t = _tracker()
    hits = match_events([_ev("combat_started", enemy_ids=["LOUSE", "BYRDONIS"], hp=60, max_hp=75, block=0, floor=1, act=1)], t)
    assert "StrongMonster" in _triggers(hits)


@pytest.mark.unit
def test_combat_started_low_hp_elite() -> None:
    t = _tracker()
    hits = match_events([_ev("combat_started", enemy_ids=["LOUSE"], hp=15, max_hp=75, block=0, floor=1, act=1)], t)
    assert "LowHpElite" in _triggers(hits)


@pytest.mark.unit
def test_combat_started_encountered_before() -> None:
    t = _tracker()
    hits = match_events([_ev("combat_started", enemy_ids=["LOUSE"], encountered_before=["LOUSE"], hp=60, max_hp=75, block=0, floor=1, act=1)], t)
    assert "EncounteredBefore" in _triggers(hits)


@pytest.mark.unit
def test_player_damaged_naked_hit() -> None:
    t = _tracker()
    hits = match_events([_ev("player_damaged", phase="combat", amount=10, hp=50, max_hp=75, block=0)], t)
    assert "NakedHit" in _triggers(hits)


@pytest.mark.unit
def test_player_damaged_with_block_does_not_trigger_naked_hit() -> None:
    t = _tracker()
    hits = match_events([_ev("player_damaged", phase="combat", amount=10, hp=50, max_hp=75, block=5)], t)
    assert "NakedHit" not in _triggers(hits)


@pytest.mark.unit
def test_non_combat_damage_does_not_trigger_naked_hit() -> None:
    """非战斗掉血（事件/地图扣血）不得触发 NakedHit（裸奔挨打）。"""
    t = _tracker()
    # phase="run"（非战斗）掉血 10、block=0 → 不应触发 NakedHit
    hits = match_events([_ev("player_damaged", amount=10, hp=50, max_hp=75, block=0)], t)
    assert "NakedHit" not in _triggers(hits)


@pytest.mark.unit
def test_player_damaged_streak_broken() -> None:
    t = _tracker()
    hits = match_events([_ev("player_damaged", amount=3, hp=57, max_hp=75, block=0, streak_broken=True)], t)
    assert "StreakBreak" in _triggers(hits)


@pytest.mark.unit
def test_max_hp_lost_and_death() -> None:
    t = _tracker()
    hits = match_events([_ev("max_hp_lost", amount=5, max_hp=70), _ev("player_death", hp=0)], t)
    triggers = _triggers(hits)
    assert "ScrollMaxHpLost" in triggers
    assert "PlayerDeath" in triggers


@pytest.mark.unit
def test_reward_duplicate_card_candidate() -> None:
    """奖励候选在牌库已有同名 → DuplicateCard（卡牌出现即算，不需获得）。"""
    t = _tracker()
    hits = match_events(
        [_ev("reward_opened", candidates=["STRIKE"], candidate_duplicates={"STRIKE": True}, visit_counts={"STRIKE": 1})],
        t,
    )
    assert "DuplicateCard" in _triggers(hits)


@pytest.mark.unit
def test_card_obtained_key_card() -> None:
    t = _tracker(character="DEFECT")
    hits = match_events([_ev("card_obtained", card="REBOOT", duplicate=False, act=1, max_hp=75, hp=60, floor=1)], t)
    assert "GotKeyCard" in _triggers(hits)


@pytest.mark.unit
def test_card_obtained_overpowered() -> None:
    t = _tracker()
    hits = match_events([_ev("card_obtained", card="WRAITH_FORM", duplicate=False, act=1, max_hp=75, hp=60, floor=1)], t)
    assert "GotOverpoweredCard" in _triggers(hits)


@pytest.mark.unit
def test_card_obtained_future_card_act1() -> None:
    t = _tracker()
    hits = match_events([_ev("card_obtained", card="DARK_EMBRACE", duplicate=False, act=1, max_hp=75, hp=60, floor=1)], t)
    assert "DraftFutureCard" in _triggers(hits)


@pytest.mark.unit
def test_card_obtained_future_card_act3_not_draft() -> None:
    t = _tracker(act=3)
    hits = match_events([_ev("card_obtained", card="DARK_EMBRACE", duplicate=False, act=3, max_hp=75, hp=60, floor=1)], t)
    assert "DraftFutureCard" not in _triggers(hits)


@pytest.mark.unit
def test_card_obtained_apotheosis() -> None:
    t = _tracker()
    hits = match_events([_ev("card_obtained", card="APOTHEOSIS", duplicate=False, act=1, max_hp=75, hp=60, floor=1)], t)
    assert "FullApotheosis" in _triggers(hits)


@pytest.mark.unit
def test_relic_obtained_overpowered() -> None:
    t = _tracker()
    hits = match_events([_ev("relic_obtained", item="MINIATURE_TENT")], t)
    assert "GotOverpoweredCard" in _triggers(hits)


@pytest.mark.unit
def test_rest_sleep_full_and_partial() -> None:
    t = _tracker()
    full = match_events([_ev("rest_sleep", hp_before=75, hp_after=75, max_hp=75)], t)
    assert "FullHpRestSiteSleep" in _triggers(full)
    partial = match_events([_ev("rest_sleep", hp_before=40, hp_after=75, max_hp=75)], t)
    assert "RestSiteSleep" in _triggers(partial)


@pytest.mark.unit
def test_rest_other_low_hp_skipped() -> None:
    t = _tracker()
    hits = match_events([_ev("rest_other", hp_before=15, hp=15, max_hp=75)], t)
    assert "LowHpSkippedRest" in _triggers(hits)


@pytest.mark.unit
def test_upgrade_streak_and_shop() -> None:
    t = _tracker()
    hits = match_events(
        [
            _ev("upgrade_streak", count=3),
            _ev("shop_purchased", gold_before=100, gold_after=70, spent=30, gained_relics=["ICE_CREAM"]),
        ],
        t,
    )
    triggers = _triggers(hits)
    assert "UpgradeStreak" in triggers
    assert "BuyPremiumRelic" in triggers


@pytest.mark.unit
def test_combat_binge_draw_overflow() -> None:
    t = _tracker()
    hits = match_events([_ev("combat_binge", count=2), _ev("draw_overflow", count=10)], t)
    triggers = _triggers(hits)
    assert "CombatBinge" in triggers
    assert "DrawOverflow" in triggers


@pytest.mark.unit
def test_enemy_killed_strong_and_reconviction() -> None:
    t = _tracker()
    t._won_combat_enemies.add("LOUSE")
    hits = match_events([_ev("enemy_killed", enemy="BYRDONIS"), _ev("enemy_killed", enemy="LOUSE")], t)
    triggers = _triggers(hits)
    assert "StrongMonsterKill" in triggers
    assert "Reconviction" in triggers


@pytest.mark.unit
def test_reward_skipped_missed_key_card() -> None:
    t = _tracker(character="DEFECT")
    hits = match_events([_ev("reward_skipped", candidates=["REBOOT", "STRIKE"])], t)
    assert "MissedKeyCard" in _triggers(hits)
    assert "SkipCardReward" in _triggers(hits)


@pytest.mark.unit
def test_save_loaded() -> None:
    t = _tracker()
    hits = match_events([_ev("save_loaded")], t)
    assert "SaveLoad" in _triggers(hits)


@pytest.mark.unit
def test_matched_rules_have_resolvable_phrase() -> None:
    """命中的规则都能从词条库抽到可解析词条。"""
    t = _tracker(character="DEFECT")
    events = [
        _ev("combat_started", enemy_ids=["LOUSE", "BYRDONIS"], hp=60, max_hp=75, block=0, floor=1, act=1),
        _ev("card_obtained", card="REBOOT", duplicate=True, act=1, max_hp=75, hp=60, floor=1),
        _ev("player_damaged", amount=10, hp=50, max_hp=75, block=0),
        _ev("rest_sleep", hp_before=40, hp_after=75, max_hp=75),
        _ev("player_death", hp=0),
    ]
    hits = match_events(events, t)
    assert hits
    for hit in hits:
        phrase = pick_rule_phrase(hit.trigger, {**hit.context, "player": {"current_hp": 60, "max_hp": 75}})
        assert phrase, f"规则 {hit.trigger} 无可解析词条"
        assert phrase["style"] in ("catgirl", "narration")
