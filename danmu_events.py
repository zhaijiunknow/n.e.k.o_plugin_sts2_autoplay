# -*- coding: utf-8 -*-
"""STS2 弹幕 — 快照 diff 事件流（把 DanmakuSpire 事件驱动架构移植到仓库）。

局面快照是响应来源：``DanmuEventTracker.feed(raw_state, snapshot)`` 在每次快照时
提取特征、与上一快照 diff，把「状态切换 / 满足固定条件」发布为事件
（combat_started / player_damaged / card_obtained / rest_sleep 等），
再由规则引擎（danmu_spire.match_events）把事件映射到弹幕规则。

与模组事件钩子的区别：本模块没有游戏内部钩子，事件源是「相邻快照的差」，
因此只发布能从快照可靠观察到的事实；需要卡牌行动流/伤害结算的规则
（BigTurn / SingleCardHighDamage / NumberExtreme 等）不在此列。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .danmu_spire import _card_in_category

# 场景分类（对齐 state_machine._screen_class 的语义）
_SCENES = {
    "combat": "combat",
    "reward": "reward",
    "shop": "shop",
    "rest": "rest",
    "selection": "selection",
    "event": "event",
    "map": "map",
    "menu": "menu",
    "terminal": "terminal",
}
# 场景切换时发布的进入事件类型（scene → event.type）
_SCENE_ENTER_EVENT = {
    "combat": "combat_started",
    "reward": "reward_opened",
    "shop": "shop_opened",
    "rest": "rest_opened",
    "selection": "reward_opened",  # 选牌视为奖励界面
    "event": "event_opened",
}

# 手牌数 ≥ 该值触发 DrawOverflow（对齐模组）
DRAW_OVERFLOW_HAND = 10
# 战斗内药水使用 ≥ 该次触发 CombatBinge（对齐模组）
COMBAT_BINGE_POTIONS = 2
# 单卡单次伤害 ≥ 该值触发 SingleCardHighDamage（对齐模组，快照近似用敌方总掉血）
HIGH_DAMAGE_THRESHOLD = 40
# 同一回合内打出 ≥ 该次触发 BigTurn（对齐模组 PlayCount≥5）
BIG_TURN_PLAYS = 5
# 实验体累计受伤 ≥ 该次触发 ExperimentChipDamage（对齐模组）
EXPERIMENT_CHIP_HITS = 5
# 无操作 tick 数 ≥ 该值触发 FakeThinking（12s 近似：tick 间隔 ~1.5s 时约 8 tick）
FAKE_THINKING_TICKS = 8

# 特殊敌人 ModelId（MonsterIds.cs）
ENEMY_QUEEN = "QUEEN"
ENEMY_TORCH_HEAD = "TORCH_HEAD_AMALGAM"
ENEMY_SCROLL_BITING = "SCROLL_OF_BITING"
ENEMY_BOWLBUG_ROCK = "BOWLBUG_ROCK"
ENEMY_TEST_SUBJECT = "TEST_SUBJECT"
ENEMY_DEVOTED_SCULPTOR = "DEVOTED_SCULPTOR"
ENEMY_EXOSKELETON = "EXOSKELETON"

# 强力组合 A→B（PowerfulCombos.cs，A=None 需分类判断、跳过）
_COLLECTIBLE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("TEMPEST", "VOLTAIC", "tempest-to-voltaic"),
    ("DOUBLE_ENERGY", "ICE_CREAM", "double-energy-to-ice-cream"),
    ("MEMBERSHIP_CARD", "THE_COURIER", "membership-card-to-courier"),
    ("THE_COURIER", "MEMBERSHIP_CARD", "courier-to-membership-card"),
    ("MEAT_CLEAVER", "MINIATURE_TENT", "meat-cleaver-to-miniature-tent"),
)


@dataclass
class DanmuEvent:
    """一条已发布的弹幕事件。"""

    type: str
    context: dict[str, Any] = field(default_factory=dict)
    phase: str = "run"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _card_id(card: Any) -> str:
    if not isinstance(card, dict):
        return ""
    return str(card.get("id") or card.get("card_id") or card.get("name") or "")


def _card_name(card: Any) -> str:
    if not isinstance(card, dict):
        return ""
    return str(card.get("name") or card.get("card_id") or "")


def _card_damage_value(card: Any) -> int:
    """提取卡的攻击力（dynamic_values Damage / resolved_rules_text 数字），用于候选高伤害判定。"""
    if not isinstance(card, dict):
        return 0
    for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
        if isinstance(item, dict) and str(item.get("name") or "").strip().lower() == "damage":
            return _safe_int(item.get("current_value") or item.get("base_value"))
    text = " ".join(
        str(card.get("resolved_rules_text") or card.get("description") or card.get("effect") or "")
        .replace("造成", " ")
        .replace("Deal", " ")
        .replace("damage", " ")
        .split()
    )
    for token in text.split():
        val = _safe_int(token)
        if val is not None:
            return val
    return 0


def _relic_id(relic: Any) -> str:
    if not isinstance(relic, dict):
        return ""
    return str(relic.get("id") or relic.get("relic_id") or relic.get("name") or "")


def _enemy_id(enemy: Any) -> str:
    if not isinstance(enemy, dict):
        return ""
    return str(enemy.get("id") or enemy.get("enemy_id") or enemy.get("model_id") or enemy.get("name") or "")


class DanmuEventTracker:
    """状态追踪器：维护上一快照特征与跨快照 run 状态，diff 出事件。"""

    def __init__(self, logger: Any = None) -> None:
        self._logger = logger
        self._prev: dict[str, Any] | None = None
        self._scene: str = "unknown"
        # 场景内部上下文（进入场景时快照，离开时清理）
        self._rest_enter_hp: int = 0
        self._rest_enter_deck_upgrades: dict[str, int] = {}
        self._rest_upgraded: bool = False
        self._reward_enter_candidates: list[str] = []
        self._shop_enter_gold: int = -1
        # 跨快照 run 状态
        self._seen_enemies: set[str] = set()
        self._won_combat_enemies: set[str] = set()
        self._no_damage_streak: int = 0
        self._upgrade_streak: int = 0
        self._potion_used_in_combat: int = 0
        self._card_visit_count: dict[str, int] = {}
        self._current_combat_enemies: set[str] = set()
        self._combat_turn: int = 0
        self._run_id: str = ""
        self._character: str = ""
        # ---- ①/② 规则所需状态 ----
        self._combat_turn_plays: int = 0            # 本回合打出牌数（B5 BigTurn，回合切换重置）
        self._big_turn_fired_this_turn: bool = False  # 本回合 BigTurn 是否已触发（每回合一次）
        self._combat_damage_count: int = 0          # 本场受伤次数（ExperimentChipDamage）
        self._combat_enemy_hps: dict[str, int] = {}  # 每敌 hp（SingleCardHighDamage/OneTurnKill）
        self._combat_enemy_intents: dict[str, str] = {}  # 每敌 intent（DefenseLack/OffenseLack/NumberExtreme）
        self._idle_ticks: int = 0                   # 无操作 tick 数（FakeThinking）
        self._elite_combat: bool = False            # 当前战斗是否精英房（EliteStreak）
        self._combat_is_first_turn: bool = False    # 是否首回合（OneTurnKill）
        self._queen_combat: bool = False            # 战斗含女王/火炬头（QueenDamaged）
        self._scroll_biting_combat: bool = False    # 战斗含咬人卷轴（ScrollMaxHpProtected）
        self._bowlbug_rock_combat: bool = False     # 战斗含盛碗虫（BowlbugRockExtreme）
        self._test_subject_combat: bool = False     # 战斗含实验体（ExperimentChipDamage）
        self._sculptor_combat: bool = False         # 战斗含雕刻师（SculptorPreChant/Chant）
        self._sculptor_chaned: bool = False         # 本场雕刻师是否用过禁忌唱颂（按 move 名判断）
        self._elite_count_by_act: dict[int, set[str]] = {}  # 每 Act 打过的精英节点（EliteStreak）
        self._owned: set[str] = set()               # 本 run 已获得物品 id（CollectiblePair）
        self._pair_notified: set[str] = set()       # 已通知过的组合（防刷屏）
        self._event_architect: bool = False         # 当前事件是否建筑师（ArchitectWithPotion）
        self._combat_notified: set[str] = set()     # 本场已通知的手牌质量规则（去重）
        self._discarded_block_this_turn: bool = False  # 本回合弃掉可打防御牌（B3 HasBlockNoPlay）
        self._scroll_max_hp_lost: bool = False      # 本场是否掉过血上限（D7 ScrollMaxHpProtected）
        self._big_deck_triggered: bool = False      # 本 run 是否已触发过 BigDeck（防刷）
        self._saved_quit_to_menu: bool = False      # 非死亡保存退出到主菜单（H1 SaveLoad）
        self._multiplayer: bool = False             # 多人会话（I1-I3 行为播报）
        self._multiplayer_forced: bool = False      # 是否由配置强制指定（否则自动检测）

    # ---- 外部入口 ----

    def feed(self, raw_state: dict[str, Any], snapshot: dict[str, Any]) -> list[DanmuEvent]:
        """处理一个新快照：提取特征 → diff 出事件 → 更新状态。返回事件列表。"""
        if not isinstance(raw_state, dict):
            raw_state = {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        new = self._extract(raw_state, snapshot)
        self._character = new["character"]
        if not self._multiplayer_forced:
            self._multiplayer = self._detect_multiplayer(raw_state)
        events: list[DanmuEvent] = []
        if self._prev is not None:
            events = self._diff(self._prev, new)
        else:
            # 首次快照：建立场景基线；若 run 已在进行，发布 run_started；
            # 场景进入逻辑同样执行（初始化 combat 状态、特殊敌标记等）
            self._scene = new["scene"]
            if new["run_id"]:
                self._run_id = new["run_id"]
                self._reset_run_state()
                self._ev(events, "run_started", {"character": new["character"]}, "run")
            self._on_scene_enter(events, new["scene"], new)
        self._prev = new
        return events

    def reset(self) -> None:
        """清空状态（run 结束 / 插件重启）。"""
        self._prev = None
        self._scene = "unknown"
        self._run_id = ""
        self._seen_enemies.clear()
        self._won_combat_enemies.clear()
        self._no_damage_streak = 0
        self._upgrade_streak = 0
        self._potion_used_in_combat = 0
        self._card_visit_count.clear()
        self._current_combat_enemies.clear()
        self._combat_turn = 0

    # ---- 特征提取 ----

    def set_multiplayer(self, enabled: bool) -> None:
        """强制多人模式（I1-I3 行为播报）。配置指定后不再自动检测。"""
        self._multiplayer_forced = True
        self._multiplayer = bool(enabled)

    def _detect_multiplayer(self, raw_state: dict[str, Any]) -> bool:
        """从快照尽力检测多人会话（session/run 的玩家数量字段）。"""
        if not isinstance(raw_state, dict):
            return False
        session = raw_state.get("session") if isinstance(raw_state.get("session"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        for src in (session, run):
            for key in ("player_count", "num_players", "players", "player_ids"):
                value = src.get(key)
                if isinstance(value, list) and len(value) > 1:
                    return True
                if isinstance(value, int) and value > 1:
                    return True
        return False

    def _scene_of(self, raw_state: dict[str, Any], snapshot: dict[str, Any]) -> str:
        """判定当前场景（对齐 state_machine 屏幕分类语义）。"""
        agent_view = raw_state.get("agent_view") if isinstance(raw_state.get("agent_view"), dict) else {}
        screen = str(
            snapshot.get("screen") or agent_view.get("screen") or raw_state.get("screen") or raw_state.get("screen_type") or "unknown"
        ).strip().lower()
        in_combat = bool(snapshot.get("in_combat", False) or raw_state.get("in_combat", False))
        if in_combat or screen in ("combat", "battle"):
            return "combat"
        if screen in _SCENES:
            return _SCENES[screen]
        if screen in ("shop_show",):
            return "shop"
        if screen in ("card_selection", "card_selection_unusefull", "card_selection_reward", "card_selection_delet"):
            return "selection"
        if screen in ("main_menu", "character_select", "timeline"):
            return "menu"
        if screen in ("game_over", "victory", "defeat"):
            return "terminal"
        return "unknown"

    def _extract(self, raw_state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """提取可可靠比较的特征。字段缺失一律防御性兜底。"""
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        session = raw_state.get("session") if isinstance(raw_state.get("session"), dict) else {}
        # 牌库/遗物/药水：真实 STS2-Agent 数据在 run 里（run.deck/run.relics/run.potions），
        # 也兼容顶层格式（旧测试/旧格式）。deck 真实是列表，也兼容 {cards:[...]} 字典。
        deck = raw_state.get("deck") if isinstance(raw_state.get("deck"), (dict, list)) else {}
        if not deck:
            deck = run.get("deck") if isinstance(run.get("deck"), (dict, list)) else {}
        if isinstance(deck, list):
            deck_cards = deck
        else:
            deck_cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        relics = raw_state.get("relics") if isinstance(raw_state.get("relics"), list) else []
        if not relics:
            relics = run.get("relics") if isinstance(run.get("relics"), list) else []
        potions = raw_state.get("potions") if isinstance(raw_state.get("potions"), list) else []
        if not potions:
            potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        reward = raw_state.get("reward") if isinstance(raw_state.get("reward"), dict) else {}
        reward_cards = reward.get("cards") if isinstance(reward.get("cards"), list) else []
        selection = raw_state.get("selection") if isinstance(raw_state.get("selection"), dict) else {}
        selection_cards = selection.get("cards") if isinstance(selection.get("cards"), list) else []
        shop = raw_state.get("shop") if isinstance(raw_state.get("shop"), dict) else {}
        shop_items: list[str] = []
        for key in ("cards", "relics", "potions"):
            items = shop.get(key)
            if isinstance(items, list):
                shop_items.extend(str(_card_id(it) or _relic_id(it)) for it in items if isinstance(it, dict))
        shop_cards_raw = shop.get("cards") if isinstance(shop.get("cards"), list) else []
        shop_cards = [_card_id(c) for c in shop_cards_raw if isinstance(c, dict)]
        event = raw_state.get("event") if isinstance(raw_state.get("event"), dict) else {}
        game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
        current_node = game_map.get("current_node")

        enemy_hps: dict[str, int] = {}
        enemy_intents: dict[str, str] = {}
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            eid = _enemy_id(enemy)
            if not eid:
                continue
            enemy_hps[eid] = _safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp"))
            enemy_intents[eid] = str(enemy.get("intent") or enemy.get("move_id") or "")

        hand_counts: dict[str, int] = {}
        for card in hand:
            if not isinstance(card, dict):
                continue
            cid = _card_id(card)
            if cid:
                hand_counts[cid] = hand_counts.get(cid, 0) + 1

        hp = _safe_int(player.get("current_hp") if player.get("current_hp") is not None else player.get("hp"))
        # 战斗外 player 为空 → 用 run 血量；战斗内明确为 0（死亡）不覆盖
        if hp <= 0 and not player:
            hp = _safe_int(run.get("current_hp")) if run.get("current_hp") is not None else hp
        max_hp = _safe_int(player.get("max_hp")) if player.get("max_hp") is not None else _safe_int(run.get("max_hp"))
        # gold 缺失哨兵 -1：快照未上报 gold 时不用 0 兜底（否则 shop_purchased 会被误触发）
        gold = -1
        if run.get("gold") is not None:
            gold = _safe_int(run.get("gold"), -1)
        elif raw_state.get("gold") is not None:
            gold = _safe_int(raw_state.get("gold"), -1)

        deck_upgrades: dict[str, int] = {}
        deck_counts: dict[str, int] = {}
        card_names: dict[str, str] = {}
        for card in deck_cards:
            if not isinstance(card, dict):
                continue
            cid = _card_id(card)
            if cid:
                deck_upgrades[cid] = _safe_int(card.get("upgrade_level") or card.get("level"))
                deck_counts[cid] = deck_counts.get(cid, 0) + 1
                card_names[cid] = str(card.get("name") or cid)
        # 手牌/奖励卡的显示名补充（多人播报 {card} 用）
        for card in list(hand) + reward_cards + selection_cards:
            if isinstance(card, dict):
                cid = _card_id(card)
                if cid and cid not in card_names:
                    card_names[cid] = str(card.get("name") or cid)

        return {
            "run_id": str(raw_state.get("run_id") or run.get("run_id") or ""),
            "scene": self._scene_of(raw_state, snapshot),
            "session_phase": str(session.get("phase") or ""),
            "screen": str(snapshot.get("screen") or raw_state.get("screen") or "").lower(),
            "floor": _safe_int(snapshot.get("floor") if snapshot.get("floor") is not None else run.get("floor")),
            "act": _safe_int(snapshot.get("act") if snapshot.get("act") is not None else run.get("act")),
            "character": str(snapshot.get("character") or run.get("character_id") or "").upper(),
            "hp": hp,
            "max_hp": max_hp,
            "gold": gold,
            "block": _safe_int(player.get("block")),
            "turn": _safe_int(combat.get("turn") if combat.get("turn") is not None else raw_state.get("turn")),
            # B5 BigTurn：agent 权威上报的本回合打出牌数（精确，替代手牌 diff 近似）
            "cards_played_this_turn": _safe_int(player.get("cards_played_this_turn")),
            "deck_counts": deck_counts,
            "deck_ids": frozenset(deck_counts.keys()),
            "deck_upgrades": deck_upgrades,
            "card_names": card_names,
            "relic_ids": frozenset(_relic_id(r) for r in relics),
            "potion_ids": frozenset(str(p.get("id") or p.get("potion_id") or p.get("name") or "") for p in potions if isinstance(p, dict)),
            "hand_counts": hand_counts,
            "hand_ids": frozenset(hand_counts.keys()),
            "enemy_ids": frozenset(enemy_hps.keys()),
            "enemy_hps": enemy_hps,
            "enemy_intents": enemy_intents,
            "has_block": _safe_int(player.get("block")) > 0,
            "map_node_type": str((current_node.get("type") or current_node.get("node_type") or "") if isinstance(current_node, dict) else "").lower(),
            "potions_count": len(potions),
            "reward_candidates": [_card_id(c) for c in reward_cards if isinstance(c, dict)],
            "selection_candidates": [_card_id(c) for c in selection_cards if isinstance(c, dict)],
            # 候选卡伤害（SingleCardHighDamage：高伤害卡出现即触发）
            "candidate_damages": {
                _card_id(c): _card_damage_value(c)
                for c in list(reward_cards) + list(selection_cards)
                if isinstance(c, dict)
            },
            "shop_items": frozenset(shop_items),
            "shop_cards": shop_cards,
            "event_name": str(event.get("name") or event.get("event_id") or ""),
        }

    def features(self, raw_state: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        """返回当前用于弹幕条件判定的**全部参数**（JSON 可序列化）。

        = ``_extract`` 的当前特征 + tracker 内部计数器/状态（BigTurn 计数、
        商店基准金币、连胜、首回合等），供「当前游戏信息状态」监控面板展示。
        """
        try:
            extracted = self._extract(raw_state, snapshot) if isinstance(raw_state, dict) else {}
        except Exception:
            extracted = {}

        def _plain(value: Any) -> Any:
            if isinstance(value, frozenset):
                return sorted(value)
            if isinstance(value, set):
                return sorted(value)
            return value

        feat: dict[str, Any] = {k: _plain(v) for k, v in extracted.items()}
        # tracker 内部状态（参与判定的计数 / 标志）
        feat.update(
            {
                "combat_turn_plays": self._combat_turn_plays,
                "big_turn_fired_this_turn": self._big_turn_fired_this_turn,
                "shop_enter_gold": self._shop_enter_gold,
                "no_damage_streak": self._no_damage_streak,
                "upgrade_streak": self._upgrade_streak,
                "potion_used_in_combat": self._potion_used_in_combat,
                "combat_turn": self._combat_turn,
                "combat_is_first_turn": self._combat_is_first_turn,
                "combat_damage_count": self._combat_damage_count,
                "idle_ticks": self._idle_ticks,
                "elite_combat": self._elite_combat,
                "big_deck_triggered": self._big_deck_triggered,
                "saved_quit_to_menu": self._saved_quit_to_menu,
                "multiplayer": self._multiplayer,
                "seen_enemies": sorted(self._seen_enemies),
                "won_combat_enemies": sorted(self._won_combat_enemies),
                "owned": sorted(self._owned),
                "elite_count_by_act": {str(k): sorted(v) for k, v in self._elite_count_by_act.items()},
            }
        )
        # 遗物/卡牌的数量与变化（BuyPremiumRelic 看遗物变化、card_obtained 看卡牌数量）
        relic_ids = feat.get("relic_ids") if isinstance(feat.get("relic_ids"), list) else []
        deck_counts = feat.get("deck_counts") if isinstance(feat.get("deck_counts"), dict) else {}
        prev = self._prev if isinstance(self._prev, dict) else {}
        prev_relics = set(prev.get("relic_ids", []) or [])
        prev_deck = prev.get("deck_counts") if isinstance(prev.get("deck_counts"), dict) else {}
        feat["relic_count"] = len(relic_ids)
        feat["card_count"] = sum(deck_counts.values())
        feat["relic_change"] = sorted(set(relic_ids) - prev_relics)
        feat["card_change"] = sorted(cid for cid, cnt in deck_counts.items() if cnt > prev_deck.get(cid, 0))
        return feat

    # ---- diff ----

    def _ev(self, events: list[DanmuEvent], type_: str, context: dict[str, Any] | None = None, phase: str = "run") -> None:
        events.append(DanmuEvent(type=type_, context=context or {}, phase=phase))

    def _diff(self, prev: dict[str, Any], new: dict[str, Any]) -> list[DanmuEvent]:
        events: list[DanmuEvent] = []

        # run 生命周期
        if not prev["run_id"] and new["run_id"]:
            self._run_id = new["run_id"]
            self._reset_run_state()
            self._ev(events, "run_started", {"character": new["character"]}, "run")
        elif prev["run_id"] and not new["run_id"]:
            self._run_id = ""
            self._ev(events, "run_ended", {}, "run")
        elif prev["run_id"] and new["run_id"] and prev["run_id"] != new["run_id"]:
            # run_id 变化：reset run 状态（SaveLoad 由「保存退出→回主菜单→回游戏」判定，新 run 不算）
            self._run_id = new["run_id"]
            self._reset_run_state()

        # 楼层/Act 推进
        if prev["floor"] != new["floor"] and new["floor"] > prev["floor"] and new["floor"] > 0:
            self._ev(events, "floor_changed", {"floor": new["floor"], "act": new["act"]}, "run")
        if prev["act"] != new["act"] and new["act"] > prev["act"] and new["act"] > 0:
            self._ev(events, "act_changed", {"act": new["act"]}, "run")

        # 场景切换
        prev_scene, new_scene = self._scene, new["scene"]
        if prev_scene != new_scene:
            # H1 SaveLoad：非死亡保存退出到主菜单 → 再回到游戏 → 触发（新 run / 死亡切出不算）
            if new_scene == "menu" and prev_scene != "menu" and prev["hp"] > 0:
                self._saved_quit_to_menu = True
            elif prev_scene == "menu" and new_scene != "menu" and self._saved_quit_to_menu:
                self._saved_quit_to_menu = False
                self._ev(events, "save_loaded", {}, "run")
            self._on_scene_left(events, prev_scene, prev, new)
            self._on_scene_enter(events, new_scene, new)
            # 通用场景切换事件：所有场景切换都发（含 map/menu/selection 等无专属事件的），
            # 供弹幕条件引擎 / LLM 生成重查当前局面。
            self._ev(
                events,
                "scene_changed",
                {"scene": new_scene, "prev_scene": prev_scene, "screen": new["screen"], "floor": new["floor"], "act": new["act"], "hp": new["hp"], "max_hp": new["max_hp"]},
                "run",
            )
            self._scene = new_scene

        # H1 SaveLoad 提前触发：读档时屏幕还没从菜单切走，但 session phase 已 menu→run
        # （agent 在加载完成前就把 phase 切到 run）。此时触发，不等菜单→COMBAT 场景切换，
        # 消除 SaveLoad 弹幕延迟。
        if (
            self._saved_quit_to_menu
            and new_scene == "menu"
            and prev.get("session_phase") == "menu"
            and new.get("session_phase") == "run"
        ):
            self._saved_quit_to_menu = False
            self._ev(events, "save_loaded", {}, "run")

        # 全局数值变化（战斗与非战斗统一）
        self._diff_vitals(events, prev, new)

        # 牌库 / 遗物 / 药水
        self._diff_collection(events, prev, new)

        # 战斗内细化
        if new["scene"] == "combat":
            self._diff_combat(events, prev, new)

        # 场景内细化（火堆/奖励/商店）
        if new["scene"] == "rest":
            self._diff_rest(events, prev, new)
        elif new["scene"] in ("reward", "selection"):
            self._diff_reward(events, prev, new)
        elif new["scene"] == "shop":
            self._diff_shop(events, prev, new)

        # 敌人相遇记录（战斗内外都可能出现敌人集合）
        if prev["enemy_ids"] and new["enemy_ids"]:
            for eid in new["enemy_ids"] - prev["enemy_ids"]:
                self._ev(events, "enemy_encountered", {"enemy": eid}, "combat")
        return events

    # ---- 场景生命周期 ----

    def _on_scene_enter(self, events: list[DanmuEvent], scene: str, new: dict[str, Any]) -> None:
        if scene == "combat":
            self._current_combat_enemies = set(new["enemy_ids"])
            self._combat_turn = new["turn"]
            self._potion_used_in_combat = 0
            # 战斗内计数/状态重置
            self._combat_turn_plays = 0
            self._big_turn_fired_this_turn = False
            self._combat_damage_count = 0
            self._combat_enemy_hps = dict(new["enemy_hps"])
            self._combat_enemy_intents = dict(new["enemy_intents"])
            self._idle_ticks = 0
            self._combat_is_first_turn = True
            # 特殊敌人标记
            self._queen_combat = bool(new["enemy_ids"] & {ENEMY_QUEEN, ENEMY_TORCH_HEAD})
            self._scroll_biting_combat = ENEMY_SCROLL_BITING in new["enemy_ids"]
            self._bowlbug_rock_combat = ENEMY_BOWLBUG_ROCK in new["enemy_ids"]
            self._test_subject_combat = ENEMY_TEST_SUBJECT in new["enemy_ids"]
            self._sculptor_combat = ENEMY_DEVOTED_SCULPTOR in new["enemy_ids"]
            self._sculptor_chaned = False
            # 重遇检测：进入前已在已遇集合的敌人（供 EncounteredBefore 规则）
            encountered_before = [eid for eid in new["enemy_ids"] if eid in self._seen_enemies]
            for eid in new["enemy_ids"]:
                self._seen_enemies.add(eid)
            ctx: dict[str, Any] = {
                "enemy_ids": sorted(new["enemy_ids"]),
                "hp": new["hp"],
                "max_hp": new["max_hp"],
                "block": new["block"],
                "floor": new["floor"],
                "act": new["act"],
            }
            if encountered_before:
                ctx["encountered_before"] = sorted(encountered_before)
            # A6 EliteStreak：第 3 个不同精英房（map current_node 类型判定，按层计数）
            self._elite_combat = new["map_node_type"] == "elite"
            if self._elite_combat and new["floor"] > 0:
                act_set = self._elite_count_by_act.setdefault(new["act"], set())
                act_set.add(new["floor"])
                if len(act_set) == 3:
                    self._ev(events, "elite_streak", {"count": 3, "act": new["act"]}, "combat")
            # 组合 waiting：进入战斗不检测，获牌时检测
            self._ev(events, "combat_started", ctx, "combat")
            return
        if scene == "reward":
            self._reward_enter_candidates = list(new["reward_candidates"]) + list(new["selection_candidates"])
            # 候选牌出现次数（一次界面算一次，避免多帧重复累加）
            for cid in self._reward_enter_candidates:
                self._card_visit_count[cid] = self._card_visit_count.get(cid, 0) + 1
            self._ev(events, "reward_opened", self._candidate_ctx(self._reward_enter_candidates, new), "reward")
            return
        if scene == "shop":
            self._shop_enter_gold = new["gold"]
            self._ev(events, "shop_opened", self._candidate_ctx(new["shop_cards"], new), "shop")
            return
        if scene == "rest":
            self._rest_enter_hp = new["hp"]
            self._rest_enter_deck_upgrades = dict(new["deck_upgrades"])
            self._rest_upgraded = False
            self._ev(events, "rest_opened", {"hp": new["hp"]}, "rest")
            return
        if scene == "event":
            is_architect = "architect" in str(new["event_name"] or "").lower()
            self._event_architect = is_architect
            self._ev(events, "event_opened", {"event_name": new["event_name"], "floor": new["floor"]}, "event")
            # G1 ArchitectWithPotion：建筑师事件且药水栏非空
            if is_architect and new["potions_count"] > 0:
                self._ev(events, "architect_with_potion", {"potions": new["potions_count"]}, "event")

    def _candidate_ctx(self, candidates: list[str], new: dict[str, Any]) -> dict[str, Any]:
        """候选上下文：candidates + 牌库重复标记 + 出现次数 + 候选伤害（出现即算规则用）。"""
        cand_damages = new.get("candidate_damages") if isinstance(new.get("candidate_damages"), dict) else {}
        return {
            "candidates": list(candidates),
            "candidate_duplicates": {cid: cid in new["deck_ids"] for cid in candidates},
            "visit_counts": {cid: self._card_visit_count.get(cid, 0) for cid in candidates},
            "candidate_damages": {cid: cand_damages.get(cid, 0) for cid in candidates},
        }

    def _on_scene_left(self, events: list[DanmuEvent], scene: str, prev: dict[str, Any], new: dict[str, Any]) -> None:
        if scene == "combat":
            won = new["hp"] > 0
            # 无伤连胜：本场未被敌怪打掉血（进入与离开时 hp 一致，或更高）
            damaged = new["hp"] < prev["hp"]
            if won and not damaged:
                self._no_damage_streak += 1
            elif damaged:
                self._no_damage_streak = 0
            self._won_combat_enemies.update(self._current_combat_enemies)
            # D7 ScrollMaxHpProtected：卷轴战胜利且整场未掉血上限
            if won and self._scroll_biting_combat and not self._scroll_max_hp_lost:
                self._ev(events, "scroll_max_hp_protected", {}, "combat")
            self._ev(
                events,
                "combat_ended",
                {"won": won, "no_damage_streak": self._no_damage_streak, "damaged": damaged},
                "combat",
            )
            # 清理战斗级状态
            self._combat_notified.clear()
            self._scroll_biting_combat = False
            self._bowlbug_rock_combat = False
            self._test_subject_combat = False
            self._sculptor_combat = False
            self._sculptor_chaned = False
            self._queen_combat = False
            self._scroll_max_hp_lost = False
            return
        if scene in ("reward", "selection"):
            # 奖励界面关闭：候选牌是否被拿（牌库新增 → 已选）
            gained = new["deck_ids"] - prev["deck_ids"]
            if not gained:
                candidates = list(self._reward_enter_candidates)
                self._ev(
                    events,
                    "reward_skipped",
                    {
                        "candidates": candidates,
                        # 候选显示名（MissedKeyCard「不抓{card}」用随机未选候选填充）
                        "candidate_names": [new["card_names"].get(c, c) for c in candidates],
                    },
                    "reward",
                )
            self._reward_enter_candidates = []
            return
        if scene == "shop":
            self._shop_enter_gold = -1
            return
        if scene == "rest":
            # 火堆结束：升级则连胜 +1（≥3 触发 UpgradeStreak），否则中断清零
            if self._rest_upgraded:
                self._upgrade_streak += 1
                if self._upgrade_streak >= 3:
                    self._ev(events, "upgrade_streak", {"count": self._upgrade_streak}, "rest")
            else:
                self._upgrade_streak = 0
                healed = new["hp"] > self._rest_enter_hp
                if not healed:
                    self._ev(events, "rest_other", {"hp_before": self._rest_enter_hp, "hp": new["hp"], "max_hp": new["max_hp"]}, "rest")
            self._rest_enter_hp = 0
            self._rest_enter_deck_upgrades = {}
            return

    # ---- 数值 / 收藏 diff ----

    def _diff_vitals(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        # 生命值变化（来源：combat.player 或 run.current_hp）
        if prev["hp"] > new["hp"] > 0:
            amount = prev["hp"] - new["hp"]
            ctx: dict[str, Any] = {
                "amount": amount,
                "hp": new["hp"],
                "max_hp": new["max_hp"],
                "block": new["block"],
            }
            phase = "combat" if new["scene"] == "combat" else "run"
            # 连续无伤战斗被破功（供 StreakBreak 规则）
            if phase == "combat" and self._no_damage_streak >= 1:
                self._no_damage_streak = 0
                ctx["streak_broken"] = True
            self._ev(events, "player_damaged", ctx, phase)
            # C3 BowlbugRockExtreme：盛碗虫（石）攻击恰好 1 点 HP
            if phase == "combat" and self._bowlbug_rock_combat and amount == 1:
                self._ev(events, "bowlbug_rock_extreme", {"amount": amount}, "combat")
            # C7 ExperimentChipDamage：实验体战斗累计受伤次数（近似无实体）
            if phase == "combat" and self._test_subject_combat:
                self._combat_damage_count += 1
                if self._combat_damage_count >= EXPERIMENT_CHIP_HITS:
                    self._ev(events, "experiment_chip_damage", {"count": self._combat_damage_count}, "combat")
            # B3 HasBlockNoPlay：本回合弃过可打防御牌后受击
            if phase == "combat" and self._discarded_block_this_turn:
                self._discarded_block_this_turn = False
                self._ev(events, "has_block_no_play", {"amount": amount}, "combat")
        elif (
            new["hp"] <= 0 < prev["hp"]
            and new["scene"] in ("combat", "terminal")  # 仅战斗/终局算死亡；保存退出主菜单(hp缺失=0)不算
        ):
            self._ev(events, "player_death", {"hp": new["hp"]}, "terminal")
        elif new["hp"] > prev["hp"]:
            self._ev(events, "player_healed", {"amount": new["hp"] - prev["hp"], "hp": new["hp"]}, "run")
        # 最大生命值
        if prev["max_hp"] > new["max_hp"] > 0:
            self._ev(events, "max_hp_lost", {"amount": prev["max_hp"] - new["max_hp"], "max_hp": new["max_hp"]}, "run")
            if new["scene"] == "combat" and self._scroll_biting_combat:
                self._scroll_max_hp_lost = True  # D7：卷轴战掉血上限

    def _diff_collection(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        prev_counts = prev["deck_counts"]
        for cid, cnt in new["deck_counts"].items():
            prev_cnt = prev_counts.get(cid, 0)
            if cnt <= prev_cnt:
                continue
            ctx: dict[str, Any] = {"card": cid, "act": new["act"], "floor": new["floor"], "max_hp": new["max_hp"], "hp": new["hp"]}
            if prev_cnt > 0:
                ctx["duplicate"] = True  # 牌库已有同名 → DuplicateCard
            ctx["deck_size"] = sum(new["deck_counts"].values())  # E11 BigDeck
            ctx["card_name"] = new["card_names"].get(cid, cid)  # I1 播报显示名
            # 未选择的候选卡显示名（AcquiredCard「不拿{skipped}」随机挑一张）
            candidate_ids = new.get("selection_candidates") or new.get("reward_candidates") or []
            ctx["skipped_names"] = [new["card_names"].get(c, c) for c in candidate_ids if c != cid]
            self._card_visit_count[cid] = self._card_visit_count.get(cid, 0) + 1
            # card_obtained：获得卡牌即触发，不管场景（奖励/选牌/商店/事件等）
            self._ev(events, "card_obtained", ctx, "run")
            # I1 MultiplayerRewardSelect：多人会话中本地玩家选牌
            if self._multiplayer and new["scene"] in ("reward", "selection"):
                self._ev(events, "multiplayer_reward_select", {"card": cid, "card_name": ctx["card_name"]}, "reward")
            if ctx["deck_size"] > 40 and not self._big_deck_triggered:
                self._big_deck_triggered = True
                self._ev(events, "big_deck", {"deck_size": ctx["deck_size"]}, "run")
            self._owned.add(cid.upper())
            self._check_pairs(events, cid, "run")
        for cid in prev["deck_ids"] - new["deck_ids"]:
            self._ev(events, "card_removed", {"card": cid}, "run")
        # 卡牌升级
        for cid, level in new["deck_upgrades"].items():
            if level > prev["deck_upgrades"].get(cid, 0):
                card_name = new["card_names"].get(cid, cid)
                self._ev(events, "card_upgraded", {"card": cid, "level": level, "card_name": card_name}, "run")
                if new["scene"] == "rest":
                    self._rest_upgraded = True
                    # I3 MultiplayerRestSite：多人会话中本地玩家火堆敲牌
                    if self._multiplayer:
                        self._ev(events, "multiplayer_rest_site", {"card": cid, "card_name": card_name}, "rest")
        # 遗物
        for rid in new["relic_ids"] - prev["relic_ids"]:
            self._ev(events, "relic_obtained", {"item": rid}, "run")
            self._owned.add(rid.upper())
            self._check_pairs(events, rid, "run")
        # 药水减少
        if len(new["potion_ids"]) < len(prev["potion_ids"]):
            if new["scene"] == "combat":
                self._potion_used_in_combat += 1
                if self._potion_used_in_combat >= COMBAT_BINGE_POTIONS:
                    self._ev(events, "combat_binge", {"count": self._potion_used_in_combat}, "combat")
            else:
                self._ev(events, "potion_used", {}, "run")

    def _check_pairs(self, events: list[DanmuEvent], item: str, phase: str) -> None:
        """强力组合 A→B 检测：获得 A 提示等 B（waiting），之后集齐（completed）。"""
        item_up = str(item or "").upper()
        if not item_up:
            return
        for a, b, pair_id in _COLLECTIBLE_PAIRS:
            if not a:
                continue
            if item_up == a and b not in self._owned and (pair_id, "waiting") not in self._pair_notified:
                self._pair_notified.add((pair_id, "waiting"))
                self._ev(events, "collectible_pair", {"item": b, "variant": "waiting"}, phase)
            elif item_up == b and a in self._owned and (pair_id, "completed") not in self._pair_notified:
                self._pair_notified.add((pair_id, "completed"))
                self._ev(events, "collectible_pair", {"item": b, "variant": "completed"}, phase)

    def _diff_combat(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        # 回合推进
        turn_changed = prev["turn"] != new["turn"] and new["turn"] > prev["turn"]
        if turn_changed:
            if self._combat_is_first_turn and new["turn"] > 1:
                self._combat_is_first_turn = False
            self._combat_turn = new["turn"]
            # B5 BigTurn：回合切换重置计数（同一回合内打出 ≥5 才触发）
            self._combat_turn_plays = 0
            self._big_turn_fired_this_turn = False
            self._ev(events, "turn_started", {"turn": new["turn"]}, "combat")
            # B3 HasBlockNoPlay：结束回合手牌防御牌消失（近似弃掉可打防御牌）
            had_block_card = any(_card_in_category(c, "Block") for c in prev["hand_ids"])
            now_block_card = any(_card_in_category(c, "Block") for c in new["hand_ids"])
            self._discarded_block_this_turn = had_block_card and not now_block_card
        # D4/D5 雕刻师唱颂检测：按个体行动名（FORBIDDEN_INCANTATION_MOVE）
        if self._sculptor_combat and not self._sculptor_chaned:
            for intent in new["enemy_intents"].values():
                up = str(intent or "").upper()
                if "FORBIDDEN" in up or "INCANTATION" in up:
                    self._sculptor_chaned = True
                    break
        # 敌人死亡 / 击杀检测
        killed = prev["enemy_ids"] - new["enemy_ids"]
        for eid in killed:
            self._ev(events, "enemy_killed", {"enemy": eid}, "combat")
            # C4 OneTurnKill：首回合击杀
            if self._combat_is_first_turn:
                self._ev(events, "one_turn_kill", {"enemy": eid}, "combat")
            # D4/D5 雕刻师击杀：唱颂过 → SculptorChant，否则 → SculptorPreChant
            if self._sculptor_combat and eid == ENEMY_DEVOTED_SCULPTOR:
                if self._sculptor_chaned:
                    self._ev(events, "sculptor_chant", {"enemy": eid}, "combat")
                else:
                    self._ev(events, "sculptor_pre_chant", {"enemy": eid}, "combat")
        # 手牌减少 → 近似 card_played（打出/弃牌）；本回合牌数计数（B5 BigTurn，回合切换重置）
        played_deltas = {
            cid: cnt - new["hand_counts"].get(cid, 0)
            for cid, cnt in prev["hand_counts"].items()
            if cnt > new["hand_counts"].get(cid, 0)
        }
        played = frozenset(played_deltas.keys())
        for cid in played:
            self._ev(events, "card_played", {"card": cid}, "combat")
            # B5 BigTurn：用 agent 权威上报的 cards_played_this_turn（见下方），
            # 手牌 diff 只用于 card_played / 攻击类规则，不再累加 combat_turn_plays。
            if _card_in_category(cid, "Attack"):
                # D3 QueenDamaged：女王战单体攻击女王（非 AOE）
                if self._queen_combat and not _card_in_category(cid, "Aoe"):
                    self._ev(events, "queen_damaged", {"card": cid}, "combat")
                # D1 CounterMatch：AOE 牌 + 场上敌怪 ≥3
                if _card_in_category(cid, "Aoe") and len(new["enemy_ids"]) >= 3:
                    self._ev(events, "counter_match", {"card": cid, "enemies": len(new["enemy_ids"])}, "combat")
        # B5 BigTurn：直接用 agent 权威上报的本回合打出牌数 cards_played_this_turn
        # （比手牌 diff 近似精确——弃牌/同名多张都准确）。
        plays_now = _safe_int(new.get("cards_played_this_turn"))
        if plays_now <= 0 and self._combat_turn_plays > 0:
            # agent 计数归零（进入新回合）→ 重置 fired 标记（不依赖 turn 字段变化）
            self._big_turn_fired_this_turn = False
        self._combat_turn_plays = plays_now
        if plays_now >= BIG_TURN_PLAYS and not self._big_turn_fired_this_turn:
            self._ev(events, "big_turn", {"count": plays_now}, "combat")
            self._big_turn_fired_this_turn = True  # 每回合最多触发一次
        # 抽牌溢出
        total_hand = sum(new["hand_counts"].values())
        if total_hand >= DRAW_OVERFLOW_HAND:
            self._ev(events, "draw_overflow", {"count": total_hand}, "combat")
        # 敌方血量变化 → C5 SingleCardHighDamage（本帧敌方总掉血 ≥40 且本帧有打出）
        total_enemy_damage = 0
        for eid, hp_now in new["enemy_hps"].items():
            hp_prev = prev["enemy_hps"].get(eid)
            if hp_prev is not None and hp_now < hp_prev:
                total_enemy_damage += hp_prev - hp_now
        if total_enemy_damage >= HIGH_DAMAGE_THRESHOLD and played:
            self._ev(events, "single_card_high_damage", {"amount": total_enemy_damage, "card": next(iter(played))}, "combat")
        # B1/B2/C2 手牌质量（每场一次）
        self._diff_combat_hand_quality(events, prev, new)
        # idle 计时（B6 FakeThinking）：有操作重置，无操作累计
        acted = bool(played) or prev["hp"] > new["hp"] or len(new["potion_ids"]) < len(prev["potion_ids"])
        if acted:
            self._idle_ticks = 0
        else:
            self._idle_ticks += 1
            if self._idle_ticks == FAKE_THINKING_TICKS:
                self._ev(events, "fake_thinking", {"ticks": self._idle_ticks}, "combat")
                self._idle_ticks = 0  # 触发后重置避免连续刷

    def _diff_combat_hand_quality(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        """B1 DefenseLack / B2 OffenseLack / C2 NumberExtreme 近似（每场一次，靠 notified 去重）。"""
        if new["act"] != 1:
            return
        hand = new["hand_ids"]
        has_attack_card = any(_card_in_category(c, "Attack") for c in hand)
        has_block_card = any(_card_in_category(c, "Block") for c in hand)
        has_draw_card = any(_card_in_category(c, "Draw") for c in hand)
        has_attack_intent = self._has_attack_intent(new["enemy_intents"])
        # B1 DefenseLack：敌攻击意图 + 手牌无防无过牌
        if "DefenseLack" not in self._combat_notified and has_attack_intent and not has_block_card and not has_draw_card:
            self._combat_notified.add("DefenseLack")
            self._ev(events, "defense_lack", {"act": new["act"]}, "combat")
        # B2 OffenseLack：敌无攻击意图 + 手牌无攻无过牌
        elif "OffenseLack" not in self._combat_notified and not has_attack_intent and not has_attack_card and not has_draw_card:
            self._combat_notified.add("OffenseLack")
            self._ev(events, "offense_lack", {"act": new["act"]}, "combat")
        # C2 NumberExtreme 近似：block 从 >0 → 0，hp 不变，敌有攻击意图 → 精确格挡
        if (
            "NumberExtreme" not in self._combat_notified
            and prev["has_block"] and not new["has_block"]
            and prev["hp"] == new["hp"] and new["hp"] > 0
            and has_attack_intent
        ):
            self._combat_notified.add("NumberExtreme")
            self._ev(events, "number_extreme", {}, "combat")

    @staticmethod
    def _has_attack_intent(intents: dict[str, str]) -> bool:
        """敌人是否有攻击意图（关键字匹配，对齐模组 intent 命名）。"""
        for intent in intents.values():
            up = str(intent or "").upper()
            if any(k in up for k in ("ATTACK", "STRIKE", "TACKLE", "HEAVY", "SMASH", "BITE", "SWIPE", "LUNGE", "VOLLEY")):
                return True
        return False

    def _diff_rest(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        # 火堆内 hp 上升 → 睡觉
        if new["hp"] > self._rest_enter_hp:
            self._ev(events, "rest_sleep", {"hp_before": self._rest_enter_hp, "hp_after": new["hp"]}, "rest")

    def _diff_reward(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        # 候选牌出现次数在场景进入时累计（_on_scene_enter），此处只处理同场景内选择
        pass

    def _diff_shop(self, events: list[DanmuEvent], prev: dict[str, Any], new: dict[str, Any]) -> None:
        # 商店内金币减少 → 购买
        # new["gold"] >= 0：排除缺失哨兵 -1（真实金币永不 < 0）
        if self._shop_enter_gold >= 0 and new["gold"] >= 0 and new["gold"] < self._shop_enter_gold:
            spent = self._shop_enter_gold - new["gold"]
            gained_relics = new["relic_ids"] - prev["relic_ids"]
            self._ev(
                events,
                "shop_purchased",
                {"gold_before": self._shop_enter_gold, "gold_after": new["gold"], "spent": spent, "gained_relics": sorted(gained_relics)},
                "shop",
            )
            # I2 MultiplayerShopPurchase：多人会话中本地玩家购买
            if self._multiplayer:
                gained = sorted(gained_relics)
                self._ev(events, "multiplayer_shop_purchase", {"item": gained[0] if gained else "", "variant": ""}, "shop")
            self._shop_enter_gold = new["gold"]
        # 商店内删牌（牌库减少）
        for cid in prev["deck_ids"] - new["deck_ids"]:
            self._ev(events, "shop_card_removal", {"card": cid}, "shop")
            # I2 删牌变体
            if self._multiplayer:
                self._ev(events, "multiplayer_shop_purchase", {"item": cid, "variant": "removal"}, "shop")

    # ---- 状态重置 ----

    def _reset_run_state(self) -> None:
        self._seen_enemies.clear()
        self._won_combat_enemies.clear()
        self._no_damage_streak = 0
        self._upgrade_streak = 0
        self._card_visit_count.clear()
        self._current_combat_enemies.clear()
        self._owned.clear()
        self._pair_notified.clear()
        self._elite_count_by_act.clear()
        self._combat_notified.clear()
        self._combat_turn_plays = 0
        self._big_turn_fired_this_turn = False
        self._combat_damage_count = 0
        self._big_deck_triggered = False

    # ---- 供规则引擎读取 ----

    @property
    def seen_enemies(self) -> set[str]:
        return self._seen_enemies

    @property
    def won_combat_enemies(self) -> set[str]:
        return self._won_combat_enemies

    @property
    def no_damage_streak(self) -> int:
        return self._no_damage_streak

    @property
    def upgrade_streak(self) -> int:
        return self._upgrade_streak

    @property
    def card_visit_count(self) -> dict[str, int]:
        return self._card_visit_count

    @property
    def current_combat_enemies(self) -> set[str]:
        return self._current_combat_enemies

    @property
    def scene(self) -> str:
        return self._scene

    @property
    def character(self) -> str:
        return self._character

    @property
    def act(self) -> int:
        return _safe_int(self._prev.get("act")) if self._prev else 0

    @property
    def floor(self) -> int:
        return _safe_int(self._prev.get("floor")) if self._prev else 0

    @property
    def multiplayer(self) -> bool:
        return self._multiplayer


__all__ = ["DanmuEvent", "DanmuEventTracker"]
