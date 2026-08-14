from __future__ import annotations

import pytest

from plugin.plugins.sts2_autoplay.danmu_events import DanmuEventTracker


def _card(cid: str, level: int = 0) -> dict:
    return {"id": cid, "name": cid, "card_id": cid, "upgrade_level": level}


def _snap(
    *,
    screen: str = "map",
    in_combat: bool = False,
    run_id: str = "run-1",
    hp: int = 60,
    max_hp: int = 75,
    deck: tuple = (),
    relics: tuple = (),
    potions: tuple = (),
    hand: tuple = (),
    enemies: tuple = (),
    turn: int = 0,
    gold: int = 100,
    floor: int = 1,
    act: int = 1,
    reward_cards: tuple = (),
    selection_cards: tuple = (),
    shop_relics: tuple = (),
    deck_levels: dict | None = None,
) -> dict:
    """构造一个 tracker 可消费的快照（raw_state + snapshot）。"""
    deck_cards = [dict(_card(c, deck_levels.get(c, 0) if deck_levels else 0)) for c in deck]
    combat: dict = {}
    if in_combat or screen == "combat":
        combat = {
            "player": {"current_hp": hp, "max_hp": max_hp, "block": 0},
            "hand": [_card(c) for c in hand],
            "enemies": [
                e if isinstance(e, dict) else {"id": e, "name": e, "enemy_id": e}
                for e in enemies
            ],
            "turn": turn,
        }
    raw_state = {
        "run_id": run_id,
        "screen": screen,
        "in_combat": bool(in_combat),
        "run": {"floor": floor, "act": act, "gold": gold, "current_hp": hp, "max_hp": max_hp},
        "deck": {"cards": deck_cards},
        "relics": [{"id": r, "name": r, "relic_id": r} for r in relics],
        "potions": [{"id": p, "name": p} for p in potions],
        "combat": combat,
        "reward": {"cards": [_card(c) for c in reward_cards]},
        "selection": {"cards": [_card(c) for c in selection_cards]},
        "shop": {"relics": [{"id": r, "name": r, "relic_id": r} for r in shop_relics]},
        "event": {"name": ""},
    }
    return {"raw_state": raw_state, "screen": screen, "in_combat": bool(in_combat), "floor": floor, "act": act, "character": "DEFECT"}


def _types(events: list) -> list[str]:
    return [e.type for e in events]


def _feed(tracker: DanmuEventTracker, snap: dict) -> list:
    return tracker.feed(snap["raw_state"], snap)


class DummyLogger:
    def debug(self, *a, **k):
        return None

    def info(self, *a, **k):
        return None

    def warning(self, *a, **k):
        return None


@pytest.mark.unit
def test_run_started_on_first_run_id() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    events = _feed(tracker, _snap(run_id="run-1"))
    assert "run_started" in _types(events)
    # 相同 run 继续 → 无 run 级事件
    assert _feed(tracker, _snap(run_id="run-1")) == []
    # run_id 变化（新 run）不直接触发 SaveLoad（需「保存退出→回主菜单→回游戏」场景切换）
    events2 = _feed(tracker, _snap(run_id="run-2"))
    assert "save_loaded" not in _types(events2)


@pytest.mark.unit
def test_combat_started_and_enemy_seen() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map"))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE", "CULTIST")))
    types = _types(events)
    assert "combat_started" in types
    started = next(e for e in events if e.type == "combat_started")
    assert started.context["enemy_ids"] == ["CULTIST", "LOUSE"]
    assert tracker.seen_enemies == {"LOUSE", "CULTIST"}


@pytest.mark.unit
def test_player_damaged_in_combat() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=60))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=45))
    types = _types(events)
    assert "player_damaged" in types
    damaged = next(e for e in events if e.type == "player_damaged")
    assert damaged.context["amount"] == 15
    assert damaged.context["hp"] == 45


@pytest.mark.unit
def test_player_death_when_hp_zero() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=20))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",), hp=0))
    assert "player_death" in _types(events)


@pytest.mark.unit
def test_card_obtained_and_removed() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    # card_obtained 仅在奖励/选牌/商店场景触发
    _feed(tracker, _snap(screen="reward", deck=("STRIKE", "DEFEND")))
    events = _feed(tracker, _snap(screen="reward", deck=("STRIKE", "DEFEND", "APOTHEOSIS")))
    types = _types(events)
    assert "card_obtained" in types
    obtained = next(e for e in events if e.type == "card_obtained")
    assert obtained.context["card"] == "APOTHEOSIS"


@pytest.mark.unit
def test_relic_obtained() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(relics=()))
    events = _feed(tracker, _snap(relics=("MINIATURE_TENT",)))
    assert "relic_obtained" in _types(events)


@pytest.mark.unit
def test_max_hp_lost() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, max_hp=75, hp=60))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, max_hp=70, hp=60))
    assert "max_hp_lost" in _types(events)


@pytest.mark.unit
def test_enemy_killed_in_combat() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("A", "B")))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("A",)))
    types = _types(events)
    assert "enemy_killed" in types
    killed = next(e for e in events if e.type == "enemy_killed")
    assert killed.context["enemy"] == "B"


@pytest.mark.unit
def test_combat_ended_won_updates_no_damage_streak() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("A",), hp=60))
    events = _feed(tracker, _snap(screen="map", in_combat=False, enemies=(), hp=60))
    types = _types(events)
    assert "combat_ended" in types
    ended = next(e for e in events if e.type == "combat_ended")
    assert ended.context["won"] is True
    assert ended.context["damaged"] is False
    assert tracker.no_damage_streak == 1


@pytest.mark.unit
def test_rest_sleep_when_hp_increases() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map", hp=40))
    _feed(tracker, _snap(screen="rest", hp=40))
    events = _feed(tracker, _snap(screen="rest", hp=70))
    assert "rest_sleep" in _types(events)


@pytest.mark.unit
def test_rest_upgrade_tracks_deck_level() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map", deck=("STRIKE",)))
    _feed(tracker, _snap(screen="rest", deck=("STRIKE",)))
    events = _feed(tracker, _snap(screen="rest", deck=("STRIKE",), deck_levels={"STRIKE": 1}))
    types = _types(events)
    assert "card_upgraded" in types
    assert "rest_upgrade" not in types  # 升级事件由 card_upgraded 承担


@pytest.mark.unit
def test_reward_opened_and_skipped() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map"))
    opened = _feed(tracker, _snap(screen="reward", reward_cards=("KEY_CARD", "STRIKE")))
    assert "reward_opened" in _types(opened)
    # 关闭奖励界面且牌库未增 → skipped
    closed = _feed(tracker, _snap(screen="map", deck=()))
    assert "reward_skipped" in _types(closed)


@pytest.mark.unit
def test_shop_purchased_when_gold_drops() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map", gold=100))
    _feed(tracker, _snap(screen="shop", gold=100))
    events = _feed(tracker, _snap(screen="shop", gold=70, relics=("ICE_CREAM",)))
    types = _types(events)
    assert "shop_purchased" in types
    assert "relic_obtained" in types


@pytest.mark.unit
def test_shop_card_removal_when_deck_shrinks() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="shop", deck=("STRIKE", "DEFEND"), gold=100))
    events = _feed(tracker, _snap(screen="shop", deck=("DEFEND",), gold=75))
    assert "shop_card_removal" in _types(events)


@pytest.mark.unit
def test_draw_overflow_when_hand_ten() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, hand=("a", "b", "c")))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, hand=("a", "b", "c", "d", "e", "f", "g", "h", "i", "j")))
    assert "draw_overflow" in _types(events)


@pytest.mark.unit
def test_run_ended_when_run_id_clears() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(run_id="run-1"))
    events = _feed(tracker, _snap(run_id=""))
    assert "run_ended" in _types(events)


@pytest.mark.unit
def test_encountered_before_detected_via_seen_enemies() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",)))
    _feed(tracker, _snap(screen="map", enemies=()))
    events = _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("LOUSE",)))
    started = next(e for e in events if e.type == "combat_started")
    # 规则引擎阶段会结合 seen_enemies 判断 EncounteredBefore
    assert "LOUSE" in tracker.seen_enemies
    assert started.context["enemy_ids"] == ["LOUSE"]


@pytest.mark.unit
def test_stable_over_repeated_idle_frames() -> None:
    tracker = DanmuEventTracker(DummyLogger())
    # 带真实手牌（攻防都有）+ 无攻击意图敌人，避免手牌质量规则在空数据下误触发
    base = _snap(
        screen="combat", in_combat=True,
        enemies=({"id": "LOUSE", "name": "LOUSE", "intent": "DEFEND_MOVE"},),
        hand=("STRIKE_IRONCLAD", "DEFEND_IRONCLAD"),
        hp=60, turn=1,
    )
    _feed(tracker, base)
    # 同一状态多帧 → 无新事件
    for _ in range(3):
        assert _feed(tracker, base) == []


@pytest.mark.unit
def test_save_quit_to_menu_not_death() -> None:
    """保存并退出到主菜单（hp 数据缺失=0）不应判定为死亡。"""
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map", hp=60))
    events = _feed(tracker, _snap(screen="main_menu", hp=0))
    assert "player_death" not in _types(events)
    # 主菜单场景应识别为 menu
    assert tracker.scene == "menu"


@pytest.mark.unit
def test_save_load_after_quit_to_menu_and_return() -> None:
    """保存退出到主菜单（非死亡）→ 再回到游戏 → 触发 SaveLoad。"""
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="map", hp=60))
    _feed(tracker, _snap(screen="main_menu", hp=60))  # 保存退出（非死亡）
    events = _feed(tracker, _snap(screen="map", hp=60))  # 回到游戏
    assert "save_loaded" in _types(events)


@pytest.mark.unit
def test_save_load_not_on_death_exit() -> None:
    """死亡/失败切出（hp=0 → game_over）不触发 SaveLoad。"""
    tracker = DanmuEventTracker(DummyLogger())
    _feed(tracker, _snap(screen="combat", in_combat=True, enemies=("A",), hp=60))
    _feed(tracker, _snap(screen="game_over", hp=0))  # 死亡
    _feed(tracker, _snap(screen="main_menu", hp=0))  # 从终局回主菜单
    events = _feed(tracker, _snap(screen="map", hp=0))
    assert "save_loaded" not in _types(events)
