"""补全规则（① 快照可达 / ② 近似可达）的 tracker 事件 + 规则映射测试。"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.danmu_events import DanmuEventTracker
from plugin.plugins.sts2_autoplay.danmu_spire import match_events


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None


def _snap(
    *,
    screen: str = "map",
    in_combat: bool = False,
    enemies: tuple = (),
    hand: tuple = (),
    hp: int = 60,
    max_hp: int = 75,
    block: int = 0,
    deck: tuple = (),
    relics: tuple = (),
    potions: tuple = (),
    turn: int = 1,
    act: int = 1,
    floor: int = 1,
    gold: int = 100,
    map_node_type: str = "",
    event_name: str = "",
    reward_cards: tuple = (),
    deck_levels: dict | None = None,
    cards_played: int = 0,
) -> dict:
    """构造 tracker 快照（含 combat 手牌/意图/map 节点/事件名/奖励候选/升级）。"""
    combat: dict = {}
    if in_combat or screen == "combat":
        combat = {
            "player": {"current_hp": hp, "max_hp": max_hp, "block": block, "cards_played_this_turn": cards_played},
            "hand": [{"id": c, "name": c, "card_id": c} for c in hand],
            "enemies": [
                e if isinstance(e, dict) else {"id": e, "name": e, "enemy_id": e}
                for e in enemies
            ],
            "turn": turn,
        }
    deck_cards = []
    for c in deck:
        card = {"id": c, "name": c, "card_id": c}
        if deck_levels and c in deck_levels:
            card["upgrade_level"] = deck_levels[c]
        deck_cards.append(card)
    raw = {
        "run_id": "run-1",
        "screen": screen,
        "in_combat": bool(in_combat),
        "run": {"floor": floor, "act": act, "gold": gold, "current_hp": hp, "max_hp": max_hp},
        "deck": {"cards": deck_cards},
        "relics": [{"id": r, "name": r, "relic_id": r} for r in relics],
        "potions": [{"id": p, "name": p} for p in potions],
        "combat": combat,
        "reward": {"cards": [{"id": c, "name": c, "card_id": c} for c in reward_cards]},
        "selection": {"cards": []},
        "shop": {},
        "event": {"name": event_name},
        "map": {"current_node": {"type": map_node_type} if map_node_type else {}},
    }
    return {"raw_state": raw, "screen": screen, "in_combat": bool(in_combat), "floor": floor, "act": act, "character": "DEFECT"}


def _feed(tracker: DanmuEventTracker, snap: dict) -> list:
    return tracker.feed(snap["raw_state"], snap)


def _triggers(hits: list) -> set[str]:
    return {h.trigger for h in hits}


def _tracker() -> DanmuEventTracker:
    return DanmuEventTracker(DummyLogger())


def _map_snap() -> dict:
    return _snap(screen="map")


# ---- ① 快照能力够 ----

@pytest.mark.unit
def test_elite_streak_third_elite() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    # 3 个不同精英房（每层 map → combat 切换）
    events = []
    for floor in (1, 2, 3):
        _feed(t, _snap(screen="map", floor=floor))
        events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), floor=floor, map_node_type="elite"))
    assert "elite_streak" in [e.type for e in events]
    hits = match_events(events, t)
    assert "EliteStreak" in _triggers(hits)


@pytest.mark.unit
def test_collectible_pair_waiting_then_completed() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    events1 = _feed(t, _snap(deck=("TEMPEST",)))
    assert "collectible_pair" in [e.type for e in events1]
    waiting = next(e for e in events1 if e.type == "collectible_pair")
    assert waiting.context["variant"] == "waiting"
    events2 = _feed(t, _snap(deck=("TEMPEST", "VOLTAIC")))
    completed = next(e for e in events2 if e.type == "collectible_pair")
    assert completed.context["variant"] == "completed"
    hits = match_events(events2, t)
    assert any(h.trigger == "CollectiblePair" and h.variant == "completed" for h in hits)


@pytest.mark.unit
def test_one_turn_kill() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("A", "B"), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), turn=1))
    assert "one_turn_kill" in [e.type for e in events]
    assert "OneTurnKill" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_queen_damaged() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("QUEEN", "TORCH_HEAD_AMALGAM"), hand=("STRIKE_IRONCLAD",), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("QUEEN", "TORCH_HEAD_AMALGAM"), hand=(), turn=1))
    assert "queen_damaged" in [e.type for e in events]
    assert "QueenDamaged" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_scroll_max_hp_protected() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("SCROLL_OF_BITING",), hp=60))
    events = _feed(t, _snap(screen="map", in_combat=False, enemies=(), hp=60))
    assert "scroll_max_hp_protected" in [e.type for e in events]
    assert "ScrollMaxHpProtected" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_architect_with_potion() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    events = _feed(t, _snap(screen="event", event_name="ARCHITECT", potions=("POTION",)))
    assert "architect_with_potion" in [e.type for e in events]
    assert "ArchitectWithPotion" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_attack_defense_card_obtained() -> None:
    t = _tracker()
    # 奖励场景获得 DASH（同时在 Attack + Block 分类）
    _feed(t, _snap(screen="reward", deck=()))
    events = _feed(t, _snap(screen="reward", deck=("DASH",)))
    assert "AttackDefenseCard" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_big_deck_over_40() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    big_deck = tuple(f"CARD_{i:02d}" for i in range(41))
    events = _feed(t, _snap(deck=big_deck))
    assert "big_deck" in [e.type for e in events]
    assert "BigDeck" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_card_three_visits() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    # 同一张牌在奖励候选出现 3 次 → 第 3 次出现即触发（不需获得）
    events = []
    for _ in range(3):
        events = _feed(t, _snap(screen="reward", reward_cards=("REBOOT",)))
        _feed(t, _map_snap())
    assert "CardThreeVisits" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_hard_choice_two_premium_candidates() -> None:
    t = _tracker()
    _feed(t, _map_snap())
    events = _feed(t, _snap(screen="reward", reward_cards=("REBOOT", "WRAITH_FORM")))
    assert "HardChoice" in _triggers(match_events(events, t))


# ---- ② 近似可达 ----

@pytest.mark.unit
def test_big_turn_attack_plays() -> None:
    t = _tracker()
    # 6 张真实攻击牌，逐张打出（agent 上报 cards_played_this_turn 递增）
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * 6, turn=1, cards_played=0))
    last_events = []
    for i in range(5, 0, -1):
        last_events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * i, turn=1, cards_played=6 - i))
    assert "big_turn" in [e.type for e in last_events]
    assert "BigTurn" in _triggers(match_events(last_events, t))


@pytest.mark.unit
def test_big_turn_is_per_turn_and_resets_on_turn_change() -> None:
    t = _tracker()
    # 回合1：逐张打出 3 张（agent 计数到 3），不足 5 不触发
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * 3, turn=1, cards_played=0))
    last_events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * 2, turn=1, cards_played=1))
    last_events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * 1, turn=1, cards_played=2))
    last_events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=(), turn=1, cards_played=3))
    assert "big_turn" not in [e.type for e in last_events]  # 3 张不足
    # 回合2：切回合（agent 计数归零），逐张打出 5 张 → 触发
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * 5, turn=2, cards_played=0))
    for i in (4, 3, 2, 1, 0):
        last_events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A",), hand=("STRIKE_IRONCLAD",) * i, turn=2, cards_played=5 - i))
    assert "big_turn" in [e.type for e in last_events]
    assert "BigTurn" in _triggers(match_events(last_events, t))


@pytest.mark.unit
def test_single_card_high_damage() -> None:
    t = _tracker()
    enemy_hi = {"id": "LOUSE", "name": "LOUSE", "current_hp": 120, "hp": 120, "intent": "DEFEND_MOVE"}
    enemy_lo = {"id": "LOUSE", "name": "LOUSE", "current_hp": 60, "hp": 60, "intent": "DEFEND_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy_hi,), hand=("STRIKE_IRONCLAD",), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy_lo,), hand=(), turn=1))
    assert "single_card_high_damage" in [e.type for e in events]
    assert "SingleCardHighDamage" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_number_extreme_block_consumed() -> None:
    t = _tracker()
    enemy = {"id": "A", "name": "A", "intent": "TACKLE_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), block=10, hp=60, turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), block=0, hp=60, turn=1))
    assert "number_extreme" in [e.type for e in events]
    assert "NumberExtreme" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_defense_lack_act1() -> None:
    t = _tracker()
    enemy = {"id": "A", "name": "A", "intent": "TACKLE_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=("STRIKE_IRONCLAD",), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=("STRIKE_IRONCLAD",), turn=1))
    assert "defense_lack" in [e.type for e in events]
    assert "DefenseLack" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_offense_lack_act1() -> None:
    t = _tracker()
    enemy = {"id": "A", "name": "A", "intent": "DEFEND_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), turn=1))
    assert "offense_lack" in [e.type for e in events]
    assert "OffenseLack" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_has_block_no_play_then_damaged() -> None:
    t = _tracker()
    enemy = {"id": "A", "name": "A", "intent": "TACKLE_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=("DEFEND_IRONCLAD",), turn=1))
    # 结束回合弃掉防御牌（turn=2 手牌空）
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), turn=2))
    # 随后受击
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=(), turn=2, hp=50))
    assert "has_block_no_play" in [e.type for e in events]
    assert "HasBlockNoPlay" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_bowlbug_rock_extreme() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("BOWLBUG_ROCK",), hp=60, turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("BOWLBUG_ROCK",), hp=59, turn=1))
    assert "bowlbug_rock_extreme" in [e.type for e in events]
    assert "BowlbugRockExtreme" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_experiment_chip_damage() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("TEST_SUBJECT",), hp=60, turn=1))
    events = []
    for i in range(1, 6):
        events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("TEST_SUBJECT",), hp=60 - i, turn=1))
    assert "experiment_chip_damage" in [e.type for e in events]
    assert "ExperimentChipDamage" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_sculptor_pre_chant_killed() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("DEVOTED_SCULPTOR",), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(), turn=1))
    assert "sculptor_pre_chant" in [e.type for e in events]
    assert "SculptorPreChant" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_sculptor_chant_when_chanted() -> None:
    t = _tracker()
    # 雕刻师意图为禁忌唱颂 → 标记已唱颂
    sculptor = {"id": "DEVOTED_SCULPTOR", "name": "DEVOTED_SCULPTOR", "intent": "FORBIDDEN_INCANTATION_MOVE"}
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(sculptor,), turn=1))
    _feed(t, _snap(screen="combat", in_combat=True, enemies=(sculptor,), turn=1))  # 唱颂意图被检测
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=(), turn=1))
    assert "sculptor_chant" in [e.type for e in events]
    assert "SculptorChant" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_counter_match_aoe_three_enemies() -> None:
    t = _tracker()
    _feed(t, _snap(screen="combat", in_combat=True, enemies=("A", "B", "C"), hand=("WHIRLWIND",), turn=1))
    events = _feed(t, _snap(screen="combat", in_combat=True, enemies=("A", "B", "C"), hand=(), turn=1))
    assert "counter_match" in [e.type for e in events]
    assert "CounterMatch" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_bridge_event_card_removed() -> None:
    t = _tracker()
    _feed(t, _snap(screen="event", event_name="bridge", deck=("STRIKE_IRONCLAD", "DEFEND_IRONCLAD")))
    events = _feed(t, _snap(screen="event", event_name="bridge", deck=("DEFEND_IRONCLAD",)))
    hits = match_events(events, t)
    assert "BridgeEvent" in _triggers(hits)
    assert any(h.trigger == "BridgeEvent" and h.variant == "weak" for h in hits)


@pytest.mark.unit
def test_fake_thinking_after_idle_ticks() -> None:
    t = _tracker()
    enemy = {"id": "A", "name": "A", "intent": "DEFEND_MOVE"}
    base = _snap(screen="combat", in_combat=True, enemies=(enemy,), hand=("STRIKE_IRONCLAD",), turn=1)
    _feed(t, base)
    all_events: list = []
    for _ in range(9):  # 累计 ≥ FAKE_THINKING_TICKS(8)
        all_events.extend(_feed(t, base))
    assert "fake_thinking" in [e.type for e in all_events]
    assert "FakeThinking" in _triggers(match_events(all_events, t))


# ---- I1-I3 多人行为播报 ----

@pytest.mark.unit
def test_multiplayer_reward_select() -> None:
    t = _tracker()
    t.set_multiplayer(True)
    _feed(t, _snap(screen="reward", reward_cards=("REBOOT",), deck=()))
    events = _feed(t, _snap(screen="reward", reward_cards=("REBOOT",), deck=("REBOOT",)))
    assert "multiplayer_reward_select" in [e.type for e in events]
    assert "MultiplayerRewardSelect" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_multiplayer_shop_purchase() -> None:
    t = _tracker()
    t.set_multiplayer(True)
    _feed(t, _snap(screen="shop", gold=100, relics=()))
    events = _feed(t, _snap(screen="shop", gold=70, relics=("ICE_CREAM",)))
    assert "multiplayer_shop_purchase" in [e.type for e in events]
    hits = match_events(events, t)
    assert "MultiplayerShopPurchase" in _triggers(hits)


@pytest.mark.unit
def test_shop_purchased_on_real_gold_decrease() -> None:
    """商店金币减少 → shop_purchased；但只买卡（无遗物变化）不触发 BuyPremiumRelic。"""
    t = _tracker()
    _feed(t, _snap(screen="shop", gold=100))
    events = _feed(t, _snap(screen="shop", gold=70))
    assert "shop_purchased" in [e.type for e in events]
    hits = match_events(events, t)
    assert "BuyPremiumRelic" not in _triggers(hits)  # 无遗物变化 → 不弹


@pytest.mark.unit
def test_shop_purchased_with_relic_triggers_buy_premium_relic() -> None:
    """商店买到遗物（遗物变化）→ 才触发 BuyPremiumRelic。"""
    t = _tracker()
    _feed(t, _snap(screen="shop", gold=100, relics=()))
    events = _feed(t, _snap(screen="shop", gold=70, relics=("ICE_CREAM",)))
    assert "shop_purchased" in [e.type for e in events]
    hits = match_events(events, t)
    assert "BuyPremiumRelic" in _triggers(hits)


@pytest.mark.unit
def test_shop_purchased_not_triggered_when_gold_missing() -> None:
    """回归：快照未上报 gold（缺失→哨兵 -1）不得误触发购买。"""
    t = _tracker()
    _feed(t, _snap(screen="shop", gold=100))
    events = _feed(t, _snap(screen="shop", gold=None))
    assert "shop_purchased" not in [e.type for e in events]


@pytest.mark.unit
def test_multiplayer_rest_site() -> None:
    t = _tracker()
    t.set_multiplayer(True)
    _feed(t, _snap(screen="rest", deck=("STRIKE_IRONCLAD",)))
    events = _feed(t, _snap(screen="rest", deck=("STRIKE_IRONCLAD",), deck_levels={"STRIKE_IRONCLAD": 1}))
    assert "multiplayer_rest_site" in [e.type for e in events]
    assert "MultiplayerRestSite" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_multiplayer_auto_detect_from_session() -> None:
    t = DanmuEventTracker(DummyLogger())
    raw = {
        "run_id": "run-1", "screen": "map",
        "session": {"phase": "run", "player_count": 2},
        "run": {"floor": 1, "act": 1, "gold": 100, "current_hp": 60, "max_hp": 75},
        "deck": {"cards": []},
    }
    t.feed(raw, {"raw_state": raw, "screen": "map", "in_combat": False, "floor": 1, "act": 1, "character": "DEFECT"})
    assert t.multiplayer is True


@pytest.mark.unit
def test_multiplayer_phrases_resolvable() -> None:
    """多人词条 {card}/{item} 填显示名后可解析。"""
    from plugin.plugins.sts2_autoplay.danmu_spire import pick_rule_phrase
    cases = (
        ("MultiplayerRewardSelect", {"card": "回响形态"}, ""),
        ("MultiplayerShopPurchase", {"item": "冰淇淋"}, ""),
        ("MultiplayerShopPurchase", {"card": "打击"}, "removal"),
        ("MultiplayerRestSite", {"card": "打击"}, ""),
    )
    for trigger, ctx, variant in cases:
        hit = pick_rule_phrase(trigger, ctx, variant=variant)
        assert hit, f"{trigger}(variant={variant}) 无可解析词条"
        assert "{" not in hit["text"] and "}" not in hit["text"]


@pytest.mark.unit
def test_acquired_card_fallback_for_common_card() -> None:
    """奖励场景获得普通牌（非关键/超模/未来/攻防一体）→ AcquiredCard 兜底弹幕。"""
    t = _tracker()
    _feed(t, _snap(screen="reward", deck=()))
    events = _feed(t, _snap(screen="reward", deck=("STRIKE_IRONCLAD",)))
    assert "AcquiredCard" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_card_obtained_when_reward_pick_transitions_scene() -> None:
    """回归：奖励选卡后屏幕切走（reward→map）同一快照里牌库已+1，
    new 场景非奖励但 prev 是 → 仍触发 card_obtained（AcquiredCard）。"""
    t = _tracker()
    _feed(t, _snap(screen="reward", deck=()))
    events = _feed(t, _snap(screen="map", deck=("STRIKE_IRONCLAD",)))
    assert "card_obtained" in [e.type for e in events]
    assert "AcquiredCard" in _triggers(match_events(events, t))


@pytest.mark.unit
def test_card_obtained_fires_regardless_of_scene() -> None:
    """获得卡牌即触发 card_obtained，不管场景（如事件获牌也触发）。"""
    t = _tracker()
    _feed(t, _snap(screen="event", deck=()))
    events = _feed(t, _snap(screen="event", deck=("STRIKE_IRONCLAD",)))
    assert "card_obtained" in [e.type for e in events]
    assert "AcquiredCard" in _triggers(match_events(events, t))
