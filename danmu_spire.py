# -*- coding: utf-8 -*-
"""DanmakuSpire 56 规则 → 弹幕 精确映射接入。

从 STS2 局面快照（catgirl_sync payload）检测可识别的 DanmakuSpire 触发条件，
从 ``danmu_spire_rules.json``（56 规则 / 171 条社区词条）精确选弹幕；
未命中任何规则时返回 None，由上层回退到局面分桶语料（danmu_corpus.json）。

快照是「当前状态」而非事件流，能可靠识别的规则有限：
- 死亡 → PlayerDeath
- 火堆 → FullHpRestSiteSleep / LowHpSkippedRest / RestSiteSleep（按血量）
- 战斗 + 无格挡 + 敌人有攻击意图 → NakedHit（裸奔挨打）
- 战斗 + 低血量 → LowHpElite
- 战斗 + 多敌人 → StrongMonster
其余规则（选牌/商店/事件等）需要事件流检测，暂不接入。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SPIRE_RULES_PATH = Path(__file__).resolve().parent / "danmu_spire_rules.json"
# 词条对象：{"text": str, "style": "character"|"narration", "variant": str}
_SPIRE_RULES: dict[str, list[dict[str, Any]]] | None = None
# 卡牌分类（CardCategories.cs：Attack/Block/Draw/Aoe/MultiHit/XCost）
_CARD_CATEGORIES_PATH = Path(__file__).resolve().parent / "danmu_card_categories.json"
_CARD_CATEGORIES: dict[str, frozenset[str]] | None = None

# 敌人攻击意图关键字（根据 STS2 agent 的 intent 命名）
_ATTACK_INTENT_KEYWORDS = (
    "ATTACK", "STRIKE", "TACKLE", "HEAVY", "SMASH", "HIT", "SLAM", "CRUSH",
    "BITE", "LUNGE", "SWIPE", "PUNCH", "KICK", "THRUST", "BARRAGE", "VOLLEY",
    "LASH", "CHOP", "GOUGE", "CLAMP", "CLAW", "FLAIL", "POUND", "REAP",
    "POUNCE", "SKEWER", "IMPALE", "MAUL", "GNAW", "RIP", "TEAR",
)
# 奖励牌名启发式（判断关键/超模牌）
_POWER_CARD_KEYWORDS = (
    "神话", "神化", "超模", "主宰", "至高", "传说", "无敌", "天启", "大师", "王牌", "灭世",
)
_KEY_CARD_KEYWORDS = (
    "核心", "基石", "关键", "联动", "引擎", "爆发", "大招", "王牌",
)


def _extract_offered_names(data: dict[str, Any]) -> list[str]:
    """从奖励/选择原始数据提取可选牌名（防御性解析）。"""
    names: list[str] = []
    candidates: list[Any] = []
    if isinstance(data.get("cards"), list):
        candidates = data["cards"]
    elif isinstance(data.get("rewards"), list):
        candidates = data["rewards"]
    elif isinstance(data.get("card_choices"), list):
        candidates = data["card_choices"]
    elif isinstance(data.get("offerings"), list):
        candidates = data["offerings"]
    for c in candidates:
        if not isinstance(c, dict):
            continue
        n = str(c.get("name") or c.get("card_id") or c.get("card") or "")
        if n:
            names.append(n)
    return names


def _extract_shop_items(data: dict[str, Any]) -> list[tuple[str, str]]:
    """从商店原始数据提取货品 (类型, 名称)（防御性解析）。"""
    items: list[tuple[str, str]] = []
    for key in ("relics", "cards", "services", "items", "wares", "offerings"):
        val = data.get(key)
        if not isinstance(val, list):
            continue
        for it in val:
            if not isinstance(it, dict):
                continue
            n = str(it.get("name") or it.get("id") or it.get("type") or "").lower()
            if n:
                items.append((key, n))
    return items


def _safe_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def _load_rules() -> dict[str, list[dict[str, Any]]]:
    global _SPIRE_RULES
    if _SPIRE_RULES is None:
        try:
            data = json.loads(_SPIRE_RULES_PATH.read_text(encoding="utf-8"))
            _SPIRE_RULES = data if isinstance(data, dict) else {}
        except Exception:
            _SPIRE_RULES = {}
    return _SPIRE_RULES


def _load_card_categories() -> dict[str, frozenset[str]]:
    """加载卡牌分类（Attack/Block/Draw/Aoe/MultiHit/XCost）。"""
    global _CARD_CATEGORIES
    if _CARD_CATEGORIES is None:
        try:
            data = json.loads(_CARD_CATEGORIES_PATH.read_text(encoding="utf-8"))
            _CARD_CATEGORIES = {
                str(key): frozenset(str(item) for item in value if isinstance(item, str))
                for key, value in data.items()
                if isinstance(value, list)
            }
        except Exception:
            _CARD_CATEGORIES = {}
    return _CARD_CATEGORIES


def _card_in_category(card_id: str, category: str) -> bool:
    """卡牌 id 是否属于指定分类（大写匹配）。"""
    if not card_id:
        return False
    return card_id.upper() in _load_card_categories().get(category, frozenset())


def _fill_placeholders(text: str, context: dict[str, Any]) -> str:
    """替换可解析的占位符（{hp}/{card}/{item}/{char}/{count}/{x}）；其余保留（调用方过滤）。

    context 既可以是 catgirl_sync payload（含 player.current_hp），
    也可以是事件 context（含 hp / card / item / char 等直接键）。
    """
    if not isinstance(context, dict):
        return text
    player = context.get("player") if isinstance(context.get("player"), dict) else {}
    hp = _safe_int(player.get("current_hp") if player else context.get("hp"))
    if "{hp}" in text and hp > 0:
        text = text.replace("{hp}", str(hp))
    for key in ("card", "item", "char"):
        token = "{%s}" % key
        if token not in text:
            continue
        val = str(context.get(key) or "").strip()
        if val:
            text = text.replace(token, val)
    # {skipped}：未选择的候选卡（AcquiredCard「不拿{skipped}」随机挑一张）
    if "{skipped}" in text:
        skipped_names = context.get("skipped_names") if isinstance(context.get("skipped_names"), list) else []
        picked = random.choice([str(n) for n in skipped_names if str(n).strip()]) if skipped_names else ""
        if picked:
            text = text.replace("{skipped}", picked)
    count = _safe_int(context.get("count") or context.get("x"))
    if count > 0:
        if "{count}" in text:
            text = text.replace("{count}", str(count))
        if "{x}" in text:
            text = text.replace("{x}", str(count))
    # {card} 未直接给（MissedKeyCard/SkipCardReward 等「未选候选」场景）→ 从候选随机挑一张
    if "{card}" in text:
        names = context.get("candidate_names") if isinstance(context.get("candidate_names"), list) else []
        pool = [str(n) for n in names if str(n).strip()]
        if not pool:
            pool = [str(c) for c in (context.get("candidates") or []) if str(c).strip()]
        if pool:
            text = text.replace("{card}", random.choice(pool))
    return text


def pick_rule_phrase(trigger: str, context: dict[str, Any], *, variant: str = "") -> dict[str, str] | None:
    """从指定规则随机选一条可用词条；返回 {"text","style"}（style 已映射 catgirl/narration）。

    variant：非空时优先抽该 variant 词条（对齐模组 GetEntries 语义：有匹配用之，
    无匹配退回无 variant 词条）；空 variant 只抽无 variant 词条。
    占位符无法解析的词条跳过。
    """
    rules = _load_rules()
    phrases = rules.get(trigger) or []
    variant = str(variant or "").strip()
    dict_entries = [e for e in phrases if isinstance(e, dict)]
    if variant:
        variant_entries = [e for e in dict_entries if e.get("variant") == variant]
        if variant_entries:
            phrases = variant_entries
        else:
            phrases = [e for e in dict_entries if not e.get("variant")]
    else:
        phrases = [e for e in dict_entries if not e.get("variant")]
    candidates: list[dict[str, str]] = []
    for entry in phrases:
        text = _fill_placeholders(str(entry.get("text") or ""), context)
        # 仍有无法解析的占位符 → 跳过
        if "{" in text or "}" in text:
            continue
        if text.strip():
            style = "narration"  # 规则词条统一旁白；catgirl 轨道只放 LLM 弹幕
            candidates.append({"text": text.strip(), "style": style})
    return random.choice(candidates) if candidates else None


def pick_rule_burst(trigger: str, context: dict[str, Any], *, variant: str = "", count: int = 1) -> list[dict[str, str]]:
    """抽 count 条词条（对齐 mod RollingDanmakuPlanner：保证 1 条角色弹幕，其余旁白补足）。

    返回 [{"text", "style"}]；为保证大窗口（轨道多）下也能填满，词条不足时**允许重复**。
    """
    rules = _load_rules()
    phrases = rules.get(trigger) or []
    variant = str(variant or "").strip()
    dict_entries = [e for e in phrases if isinstance(e, dict)]
    if variant:
        variant_entries = [e for e in dict_entries if e.get("variant") == variant]
        if variant_entries:
            dict_entries = variant_entries
        else:
            dict_entries = [e for e in dict_entries if not e.get("variant")]
    else:
        dict_entries = [e for e in dict_entries if not e.get("variant")]
    resolvable: list[dict[str, str]] = []
    for entry in dict_entries:
        text = _fill_placeholders(str(entry.get("text") or ""), context)
        if "{" in text or "}" in text:
            continue
        text = text.strip()
        if not text:
            continue
        resolvable.append({"text": text, "style": "narration"})  # 规则词条统一旁白
    if not resolvable:
        return []
    chars = [r for r in resolvable if r["style"] == "catgirl"]
    narrs = [r for r in resolvable if r["style"] == "narration"]
    picks: list[dict[str, str]] = []

    def _pick_unused(pool: list[dict[str, str]]) -> dict[str, str] | None:
        used = {p["text"] for p in picks}
        unused = [r for r in pool if r["text"] not in used]
        return random.choice(unused) if unused else None

    if chars:
        first = _pick_unused(chars) or random.choice(chars)
        picks.append(first)
    while len(picks) < count:
        candidate = _pick_unused(narrs) if narrs else None
        if candidate is None and chars:
            candidate = _pick_unused(chars)
        if candidate is None:
            # 词条用尽 → 允许重复补满（docstring 承诺"允许重复"，此前未实现导致发不满）
            pool = chars or narrs
            if not pool:
                break
            candidate = random.choice(pool)
        picks.append(candidate)
    return picks


def detect_trigger(
    payload: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    seen_before: bool = False,
) -> str | None:
    """从局面快照（及可选的前一快照/重遇标记）检测 DanmakuSpire 触发条件；未命中返回 None。"""
    if not isinstance(payload, dict):
        return None
    screen = str(payload.get("screen") or "unknown").upper()
    kind = str(payload.get("summary_kind") or "")
    player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
    enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []

    hp = _safe_int(player.get("current_hp"))
    max_hp = _safe_int(player.get("max_hp"))
    block = _safe_int(player.get("block"))
    ratio = (hp / max_hp) if max_hp > 0 else 1.0

    is_combat = screen in ("COMBAT", "BATTLE") or kind == "combat"
    is_death = screen in ("GAME_OVER", "GAMEOVER", "DEFEAT") or kind == "game_over"
    is_rest = screen in ("REST", "CAMPFIRE") or kind == "rest"

    # 事件型规则（基于前后快照对比）
    if isinstance(previous, dict):
        prev_player = previous.get("player") if isinstance(previous.get("player"), dict) else {}
        prev_max = _safe_int(prev_player.get("max_hp"))
        prev_hp = _safe_int(prev_player.get("current_hp"))
        if prev_max > 0 and max_hp > 0 and max_hp < prev_max:
            return "ScrollMaxHpLost"  # 血上限掉了
        if (
            prev_max > 0 and prev_hp >= prev_max
            and max_hp > 0 and hp < max_hp
            and (is_combat or kind == "combat")
        ):
            return "StreakBreak"  # 满血状态被破（战斗中掉血）

    # 重遇已见过的屏幕/敌人组合（离开后又回来）
    if seen_before and (is_combat or screen in ("SHOP", "STORE", "EVENT", "EVENTS") or kind in ("shop", "event")):
        return "EncounteredBefore"

    if is_death:
        return "PlayerDeath"
    if is_rest:
        if max_hp > 0 and ratio >= 1.0:
            return "FullHpRestSiteSleep"
        if max_hp > 0 and ratio < 0.5:
            return "LowHpSkippedRest"
        return "RestSiteSleep"
    if is_combat:
        has_attack = any(
            str(e.get("intent") or "").upper() in _ATTACK_INTENT_KEYWORDS
            or any(k in str(e.get("intent") or "").upper() for k in _ATTACK_INTENT_KEYWORDS)
            for e in enemies
            if isinstance(e, dict)
        )
        # 裸奔挨打：无格挡 + 敌人有攻击意图
        if block == 0 and has_attack and enemies:
            return "NakedHit"
        # 低血
        if max_hp > 0 and ratio < 0.3:
            return "LowHpElite"
        # 多敌人
        if len(enemies) >= 3:
            return "StrongMonster"

    # 奖励选牌 / 卡牌选择
    screen_is_reward = screen in ("REWARD", "REWARDS") or kind in ("reward",)
    screen_is_selection = screen in ("SELECTION",) or kind in ("selection",)
    if screen_is_reward or screen_is_selection:
        offers = payload.get("_offers") if isinstance(payload.get("_offers"), dict) else {}
        offered = _extract_offered_names(offers)
        if offered:
            cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
            hand_names = [str(c.get("name") or "") for c in cards if isinstance(c, dict)]
            if any(n in hand_names for n in offered):
                return "DuplicateCard"  # 奖励重复牌
            for n in offered:
                if any(k in n for k in _POWER_CARD_KEYWORDS):
                    return "GotOverpoweredCard"
                if any(k in n for k in _KEY_CARD_KEYWORDS):
                    return "GotKeyCard"
            return "DraftFutureCard"  # 通用选牌（战未来）

    # 商店
    screen_is_shop = screen in ("SHOP", "STORE") or kind in ("shop",)
    if screen_is_shop:
        shop = payload.get("_shop") if isinstance(payload.get("_shop"), dict) else {}
        items = _extract_shop_items(shop)
        if any(kind == "relics" or "relic" in name for kind, name in items):
            return "BuyPremiumRelic"
        if any(k in name for kind, name in items for k in ("removal", "remove", "delete", "删")):
            return "ShopCardRemoval"
    return None


# ---------------------------------------------------------------------------
# 事件流规则引擎：把 DanmuEventTracker 发布的事件 → 弹幕规则触发
# （DanmakuSpire 规则在快照 diff 事件流上的投影，见 danmu_events.py）
# ---------------------------------------------------------------------------

# ---- 模组集合（MonsterIds.cs / ManualCardSets.cs / ManualRelicSets.cs）----
# 强敌 A 类：前两层遇且牌库无 AOE 才算强；仓库简化（无 AOE 检测），遇即触发
_STRONG_MONSTERS_A = {
    "PHROG_PARASITE",  # 异蛙寄生虫
    "TERROR_EEL",  # 花园幽灵鳗
    "EXOSKELETON",  # 外骨骼虫
    "DECIMILLIPEDE_SEGMENT",  # 残杀千足虫
}
# 强敌 B 类：无条件强
_STRONG_MONSTERS_B = {
    "BYRDONIS",  # 多尼斯异鸟
    "LIVING_FOG",  # 活雾
    "SKULKING_COLONY",  # 鬼祟珊瑚群
    "INFESTED_PRISM",  # 感染棱柱
}
_STRONG_MONSTERS = _STRONG_MONSTERS_A | _STRONG_MONSTERS_B
# 女王 / 火炬头（D2/D3）
_QUEEN = "QUEEN"
_TORCH_HEAD = "TORCH_HEAD_AMALGAM"

# 关键牌（ManualCardSets.KeyCardsByCharacter）
_KEY_CARDS: dict[str, set[str]] = {
    "IRONCLAD": {"BLOODLETTING", "OFFERING", "STOKE", "DOMINATE", "DARK_EMBRACE"},
    "SILENT": {"PREPARED", "PIERCING_WAIL", "FOOTWORK", "ADRENALINE"},
    "DEFECT": {"COMPACT", "WHITE_NOISE", "DOUBLE_ENERGY", "SUPERCRITICAL", "REBOOT"},
    "REGENT": {"REFLECT", "BULWARK", "DYING_STAR", "CONVERGENCE"},
    "NECROBINDER": {"DEFY", "DREDGE", "ENFEEBLING_TOUCH"},
}
# 超模牌 / 超模遗物
_OVERPOWERED_CARDS = {"WELL_LAID_PLANS", "THE_SEALED_THRONE", "HIDDEN_GEM", "BRIGHTEST_FLAME", "WRAITH_FORM"}
_OVERPOWERED_RELICS = {"MINIATURE_TENT", "BEATING_REMNANT"}
# 未来牌（Act1 战未来）
_FUTURE_CARDS = {
    "DARK_EMBRACE", "PYRE", "DEMON_FORM", "ACCELERANT", "CORROSIVE_WAVE", "BULLET_TIME",
    "TRACKING", "SUPERMASSIVE", "ORBIT", "PILLAR_OF_CREATION", "VOID_FORM", "REAPER_FORM",
    "TEMPEST", "VOLTAIC", "ECHO_FORM",
}
# 神化
_APOTHEOSIS = "APOTHEOSIS"

# 低血阈值（对齐模组 LowHpElite：HP < 30%）
_LOW_HP_RATIO = 0.3


# 强度 → 抽选条数 / 顶部概率（对齐 mod DanmakuIntensityCatalog + RollingDanmakuCountProfile）
# 密度 100% 时范围：Light 4-6 / Medium 8-12 / Strong 15-22（供 Qt 浮层按窗口/字号轨道填满）；
# 顶部概率 Light 0 / Medium 15% / Strong 30%
_INTENSITY_RANGE: dict[str, tuple[int, int]] = {
    "light": (4, 6),
    "medium": (8, 12),
    "strong": (15, 22),
}
_INTENSITY_TOP: dict[str, float] = {"light": 0.0, "medium": 0.15, "strong": 0.3}

# 规则 → 强度（对齐 mod DanmakuIntensityCatalog.cs）
_RULE_INTENSITY: dict[str, str] = {
    # Strong
    "LowHpElite": "strong", "EliteStreak": "strong", "OneTurnKill": "strong",
    "PlayerDeath": "strong", "GotOverpoweredCard": "strong", "FullApotheosis": "strong",
    # Medium
    "StrongMonster": "medium", "StrongMonsterKill": "medium", "EncounteredBefore": "medium",
    "HasBlockNoPlay": "medium", "BigTurn": "medium", "NumberExtreme": "medium",
    "BowlbugRockExtreme": "medium", "SingleCardHighDamage": "medium", "StreakBreak": "medium",
    "ExperimentChipDamage": "medium", "NoDamageStreak": "medium", "QueenTorchhead": "medium",
    "SculptorPreChant": "medium", "SculptorChant": "medium", "ScrollMaxHpLost": "medium",
    "ScrollMaxHpProtected": "medium", "DuplicateCard": "medium", "GotKeyCard": "medium",
    "MissedKeyCard": "medium", "RejectFutureCard": "medium", "ShopCardRemoval": "medium",
    "ArchitectWithPotion": "medium", "BridgeEvent": "medium", "CloneEnchantment": "medium",
    "LowHpSkippedRest": "medium", "CollectiblePair": "medium",
    # Light
    "Reconviction": "light", "DefenseLack": "light", "OffenseLack": "light",
    "StartupCard": "light", "FakeThinking": "light", "CombatBinge": "light",
    "DrawOverflow": "light", "NakedHit": "light", "CounterMatch": "light",
    "QueenDamaged": "light", "DraftFutureCard": "light", "AttackDefenseCard": "light",
    "SkipCardReward": "light", "HardChoice": "light", "BigDeck": "light",
    "CardThreeVisits": "light", "BuyPremiumRelic": "light", "RestSiteSleep": "light",
    "FullHpRestSiteSleep": "light", "UpgradeStreak": "light", "SaveLoad": "light",
    "MultiplayerRewardSelect": "light", "MultiplayerShopPurchase": "light", "MultiplayerRestSite": "light",
    # 自定义：普通获牌兜底
    "AcquiredCard": "light",
}


def rule_intensity(trigger: str) -> str:
    """返回规则强度：light / medium / strong。"""
    return _RULE_INTENSITY.get(str(trigger or ""), "medium")


def burst_profile(trigger: str, density: int = 100) -> tuple[int, float]:
    """按强度 + 词条密度算本次触发的（抽选条数, 顶部概率）。"""
    intensity = rule_intensity(trigger)
    low, high = _INTENSITY_RANGE[intensity]
    density = max(50, min(200, int(density or 100)))
    base = random.randint(low, high)
    count = max(1, int(round(base * density / 100)))
    return count, _INTENSITY_TOP[intensity]


def create_delays(count: int) -> list[float]:
    """按 mod RollingDanmakuPlanner.CreateDelays 生成延迟（秒），乱序。"""
    if count <= 0:
        return []
    if count == 1:
        return [random.uniform(0.1, 0.55)]
    delays = [random.uniform(0.1, 0.4)]
    delays.extend(_truncated_normal(1.6, 0.5, 0.55, 2.75) for _ in range(count - 2))
    delays.append(random.uniform(4.0, 5.6) if count >= 6 else random.uniform(3.3, 4.5))
    random.shuffle(delays)
    return delays


def _truncated_normal(mean: float, deviation: float, minimum: float, maximum: float) -> float:
    for _ in range(12):
        value = mean + random.gauss(0.0, deviation)
        if minimum <= value <= maximum:
            return value
    return min(max(mean, minimum), maximum)


@dataclass
class DanmuTriggerHit:
    """一次事件流命中的弹幕规则。"""

    trigger: str
    context: dict[str, Any] = field(default_factory=dict)
    variant: str = ""


def match_events(events: list[Any], tracker: Any) -> list[DanmuTriggerHit]:
    """把 DanmuEventTracker 产出的事件列表映射到弹幕规则触发。

    tracker 提供跨快照 run 状态（character / seen_enemies / won_combat_enemies 等）。
    每个事件可命中 0..n 条规则；已由 tracker 在事件里带足事实上下文，规则只做匹配。
    """
    hits: list[DanmuTriggerHit] = []
    for event in events:
        match = _match_event(event, tracker)
        if match:
            hits.extend(match)
    return hits


def _norm(value: Any) -> str:
    """归一化 id 供集合匹配（大写）。"""
    return str(value or "").upper()


def _match_event(event: Any, tracker: Any) -> list[DanmuTriggerHit]:
    etype = getattr(event, "type", "")
    ctx = event.context if isinstance(getattr(event, "context", None), dict) else {}
    if etype == "save_loaded":
        return [DanmuTriggerHit("SaveLoad", {})]
    if etype == "combat_started":
        return _match_combat_started(ctx)
    if etype == "player_damaged":
        return _match_player_damaged(ctx, str(getattr(event, "phase", "") or ""), tracker)
    if etype == "max_hp_lost":
        return [DanmuTriggerHit("ScrollMaxHpLost", ctx)]
    if etype == "player_death":
        return [DanmuTriggerHit("PlayerDeath", ctx)]
    if etype == "card_obtained":
        return _match_card_obtained(ctx, tracker)
    if etype == "relic_obtained":
        return _match_relic_obtained(ctx)
    if etype == "card_removed":
        hits: list[DanmuTriggerHit] = []
        if tracker.scene == "shop":
            hits.append(DanmuTriggerHit("ShopCardRemoval", ctx))
        if tracker.scene == "event":
            # G2 BridgeEvent：事件失去卡牌（近似为失去弱牌）
            hits.append(DanmuTriggerHit("BridgeEvent", ctx, variant="weak"))
        return hits
    if etype == "rest_sleep":
        return _match_rest_sleep(ctx)
    if etype == "rest_other":
        return _match_rest_other(ctx)
    if etype == "upgrade_streak":
        return [DanmuTriggerHit("UpgradeStreak", ctx)]
    if etype == "shop_purchased":
        # BuyPremiumRelic：shop_purchased 仅在商店场景触发（_diff_shop 由 new.scene==shop
        # 才调用），这里只需真正获得遗物（遗物变化）才映射；只买卡/删牌等不弹
        # （那些另有 AcquiredCard / ShopCardRemoval 覆盖）。
        gained = ctx.get("gained_relics") if isinstance(ctx.get("gained_relics"), list) else []
        if gained:
            return [DanmuTriggerHit("BuyPremiumRelic", ctx)]
        return []
    if etype == "combat_binge":
        return [DanmuTriggerHit("CombatBinge", ctx)]
    if etype == "draw_overflow":
        return [DanmuTriggerHit("DrawOverflow", ctx)]
    if etype == "enemy_killed":
        return _match_enemy_killed(ctx, tracker)
    if etype == "reward_skipped":
        return _match_reward_skipped(ctx, tracker)
    if etype == "reward_opened":
        return _match_reward_opened(ctx, tracker)
    if etype == "shop_opened":
        # 商店候选卡牌出现也算（DuplicateCard/CardThreeVisits）
        return _match_reward_opened(ctx, tracker)
    if etype == "card_played":
        return _match_card_played(ctx)
    if etype == "combat_ended":
        return _match_combat_ended(ctx, tracker)
    # ---- ① 补：快照能力够 ----
    if etype == "elite_streak":
        return [DanmuTriggerHit("EliteStreak", ctx)]
    if etype == "collectible_pair":
        return [DanmuTriggerHit("CollectiblePair", ctx, variant=str(ctx.get("variant") or ""))]
    if etype == "one_turn_kill":
        return [DanmuTriggerHit("OneTurnKill", ctx)]
    if etype == "queen_damaged":
        return [DanmuTriggerHit("QueenDamaged", ctx)]
    if etype == "scroll_max_hp_protected":
        return [DanmuTriggerHit("ScrollMaxHpProtected", ctx)]
    if etype == "architect_with_potion":
        return [DanmuTriggerHit("ArchitectWithPotion", ctx)]
    if etype == "big_deck":
        return [DanmuTriggerHit("BigDeck", ctx)]
    # ---- ② 补：近似可达 ----
    if etype == "big_turn":
        return [DanmuTriggerHit("BigTurn", ctx)]
    if etype == "fake_thinking":
        return [DanmuTriggerHit("FakeThinking", ctx)]
    if etype == "single_card_high_damage":
        return [DanmuTriggerHit("SingleCardHighDamage", ctx)]
    if etype == "number_extreme":
        return [DanmuTriggerHit("NumberExtreme", ctx)]
    if etype == "defense_lack":
        return [DanmuTriggerHit("DefenseLack", ctx)]
    if etype == "offense_lack":
        return [DanmuTriggerHit("OffenseLack", ctx)]
    if etype == "has_block_no_play":
        return [DanmuTriggerHit("HasBlockNoPlay", ctx)]
    if etype == "bowlbug_rock_extreme":
        return [DanmuTriggerHit("BowlbugRockExtreme", ctx)]
    if etype == "experiment_chip_damage":
        return [DanmuTriggerHit("ExperimentChipDamage", ctx)]
    if etype == "sculptor_pre_chant":
        return [DanmuTriggerHit("SculptorPreChant", ctx)]
    if etype == "sculptor_chant":
        return [DanmuTriggerHit("SculptorChant", ctx)]
    if etype == "counter_match":
        return [DanmuTriggerHit("CounterMatch", ctx)]
    # ---- I1-I3 多人行为播报（多人会话） ----
    if etype == "multiplayer_reward_select":
        return [DanmuTriggerHit("MultiplayerRewardSelect", {"card": ctx.get("card_name") or ctx.get("card", "")})]
    if etype == "multiplayer_shop_purchase":
        return [DanmuTriggerHit("MultiplayerShopPurchase", {"item": ctx.get("item", "")}, variant=str(ctx.get("variant") or ""))]
    if etype == "multiplayer_rest_site":
        return [DanmuTriggerHit("MultiplayerRestSite", {"card": ctx.get("card_name") or ctx.get("card", "")})]
    return []


def _match_combat_started(ctx: dict[str, Any]) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    enemy_ids = [_norm(e) for e in (ctx.get("enemy_ids") or [])]
    # A1 StrongMonster
    if any(e in _STRONG_MONSTERS for e in enemy_ids):
        hits.append(DanmuTriggerHit("StrongMonster", {"enemy_ids": enemy_ids}))
    # D2/D3 女王战（敌集合含女王/火炬头，死亡/受击单独判定）
    # A5 LowHpElite（近似：战斗开始低血，无精英房信息）
    max_hp = _safe_int(ctx.get("max_hp"))
    hp = _safe_int(ctx.get("hp"))
    if max_hp > 0 and hp / max_hp < _LOW_HP_RATIO:
        hits.append(DanmuTriggerHit("LowHpElite", {"hp": hp, "max_hp": max_hp}))
    # A3 EncounteredBefore：离开过又回来
    if ctx.get("encountered_before"):
        hits.append(DanmuTriggerHit("EncounteredBefore", {"enemies": ctx["encountered_before"]}))
    return hits


def _match_player_damaged(ctx: dict[str, Any], phase: str = "", tracker: Any = None) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    amount = _safe_int(ctx.get("amount"))
    block = _safe_int(ctx.get("block"))
    # C1 NakedHit：仅战斗内，≥5 点 HP 伤害且未消耗格挡
    # （非战斗掉血（事件/地图扣血）block=0，若无战斗限定会误触发）
    if phase == "combat" and amount >= 5 and block == 0:
        # 按角色抽裸奔词条（战士/储君/骨头人有专属，其余角色用通用）
        variant = _norm(getattr(tracker, "character", "")) if tracker is not None else ""
        hits.append(DanmuTriggerHit("NakedHit", {"amount": amount, "hp": ctx.get("hp")}, variant=variant))
    # C6 StreakBreak：连续无伤被破
    if ctx.get("streak_broken"):
        hits.append(DanmuTriggerHit("StreakBreak", {"hp": ctx.get("hp")}))
    return hits


def _match_card_obtained(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    card = _norm(ctx.get("card"))
    if not card:
        return hits
    # 展示名（{card} 占位符用中文卡名，而非英文 id）
    display = str(ctx.get("card_name") or ctx.get("card") or "").strip()
    act = _safe_int(ctx.get("act"))
    # E3 GotKeyCard
    key_cards = _KEY_CARDS.get(_norm(getattr(tracker, "character", "")), set())
    if card in key_cards:
        hits.append(DanmuTriggerHit("GotKeyCard", {"card": display}))
    # E4 GotOverpoweredCard（牌）
    if card in _OVERPOWERED_CARDS:
        hits.append(DanmuTriggerHit("GotOverpoweredCard", {"card": display}))
    # E1 DraftFutureCard（仅 Act1）
    if act <= 1 and card in _FUTURE_CARDS:
        hits.append(DanmuTriggerHit("DraftFutureCard", {"card": display}))
    # E8 FullApotheosis（获得神化）
    if card == _APOTHEOSIS:
        hits.append(DanmuTriggerHit("FullApotheosis", {"card": display}))
    # E5 AttackDefenseCard：同时是攻击牌且带格挡（铁斩波）
    if _card_in_category(card, "Attack") and _card_in_category(card, "Block"):
        hits.append(DanmuTriggerHit("AttackDefenseCard", {"card": display}))
    # 普通获牌兜底：未命中任何特殊规则 → 通用选牌弹幕（奖励/商店获得普通牌也有弹幕）
    if not hits:
        hit_ctx: dict[str, Any] = {"card": display}
        skipped_names = ctx.get("skipped_names") if isinstance(ctx.get("skipped_names"), list) else []
        if skipped_names:
            hit_ctx["skipped_names"] = skipped_names  # 供「不拿{skipped}」随机挑一张未选的
        hits.append(DanmuTriggerHit("AcquiredCard", hit_ctx))
    return hits


def _match_relic_obtained(ctx: dict[str, Any]) -> list[DanmuTriggerHit]:
    item = _norm(ctx.get("item"))
    if item in _OVERPOWERED_RELICS:
        return [DanmuTriggerHit("GotOverpoweredCard", {"card": item})]
    return []


def _match_rest_sleep(ctx: dict[str, Any]) -> list[DanmuTriggerHit]:
    hp_before = _safe_int(ctx.get("hp_before"))
    max_hp = _safe_int(ctx.get("max_hp"))
    if max_hp > 0 and hp_before >= max_hp:
        return [DanmuTriggerHit("FullHpRestSiteSleep", ctx)]
    return [DanmuTriggerHit("RestSiteSleep", ctx)]


def _match_rest_other(ctx: dict[str, Any]) -> list[DanmuTriggerHit]:
    hp_before = _safe_int(ctx.get("hp_before"))
    max_hp = _safe_int(ctx.get("max_hp"))
    if max_hp > 0 and hp_before < max_hp * _LOW_HP_RATIO:
        return [DanmuTriggerHit("LowHpSkippedRest", {"hp": hp_before})]
    return []


def _match_enemy_killed(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    enemy = _norm(ctx.get("enemy"))
    if not enemy:
        return hits
    # A2 StrongMonsterKill：击杀本场强怪
    if enemy in _STRONG_MONSTERS:
        hits.append(DanmuTriggerHit("StrongMonsterKill", {"enemy": enemy}))
    # A4 Reconviction：曾在本 run 已胜利的战斗中出现
    won = {_norm(e) for e in getattr(tracker, "won_combat_enemies", set())}
    if enemy in won:
        hits.append(DanmuTriggerHit("Reconviction", {"enemy": enemy}))
    # D2 QueenTorchhead：击杀火炬头
    if enemy == _TORCH_HEAD:
        hits.append(DanmuTriggerHit("QueenTorchhead", {"enemy": enemy}))
    return hits


def _match_reward_skipped(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    candidates = [_norm(c) for c in (ctx.get("candidates") or [])]
    if not candidates:
        return hits
    act = _safe_int(getattr(tracker, "act", 0))
    key_cards = _KEY_CARDS.get(_norm(getattr(tracker, "character", "")), set())
    candidate_names = ctx.get("candidate_names") if isinstance(ctx.get("candidate_names"), list) else []
    missed_ctx: dict[str, Any] = {"candidates": candidates}
    if candidate_names:
        missed_ctx["candidate_names"] = candidate_names  # 「不抓/不拿{card}」随机未选候选
    # E6 MissedKeyCard：候选含关键/超模未选
    if any(c in key_cards or c in _OVERPOWERED_CARDS for c in candidates):
        hits.append(DanmuTriggerHit("MissedKeyCard", missed_ctx))
    # E7 RejectFutureCard：Act1 候选唯一未来牌未选
    future = [c for c in candidates if c in _FUTURE_CARDS]
    if act <= 1 and len(future) == 1:
        hits.append(DanmuTriggerHit("RejectFutureCard", {"card": future[0]}))
    # E9 SkipCardReward：跳过选牌
    hits.append(DanmuTriggerHit("SkipCardReward", {"candidates": candidates}))
    return hits


def _match_event_opened(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    # G1 ArchitectWithPotion 由 architect_with_potion 事件处理
    return []


# 候选出现即触发 SingleCardHighDamage 的卡牌基础伤害阈值（可调）
_CANDIDATE_HIGH_DAMAGE = 20


def _match_reward_opened(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    """奖励/商店界面出现候选时判断（卡牌出现即可，不需获得）。"""
    hits: list[DanmuTriggerHit] = []
    candidates = [_norm(c) for c in (ctx.get("candidates") or [])]
    if not candidates:
        return hits
    duplicates = {_norm(k): v for k, v in (ctx.get("candidate_duplicates") or {}).items()}
    visits = {_norm(k): v for k, v in (ctx.get("visit_counts") or {}).items()}
    # E2 DuplicateCard：候选在牌库已有同名（出现即算）
    dup = [c for c in candidates if duplicates.get(c)]
    if dup:
        hits.append(DanmuTriggerHit("DuplicateCard", {"card": dup[0]}))
    # SingleCardHighDamage：候选出现高伤害卡（基础伤害 ≥ 阈值，出现即算）
    damages = {_norm(k): v for k, v in (ctx.get("candidate_damages") or {}).items()}
    high = [c for c in candidates if damages.get(c, 0) >= _CANDIDATE_HIGH_DAMAGE]
    if high:
        hits.append(DanmuTriggerHit("SingleCardHighDamage", {"card": high[0]}))
    # E12 CardThreeVisits：候选出现 ≥3 次
    for c in candidates:
        if visits.get(c, 0) >= 3:
            hits.append(DanmuTriggerHit("CardThreeVisits", {"card": c}))
    # E10 HardChoice：候选含 ≥2 张超模/关键牌
    key_cards = _KEY_CARDS.get(_norm(getattr(tracker, "character", "")), set())
    premium = [c for c in candidates if c in key_cards or c in _OVERPOWERED_CARDS]
    if len(premium) >= 2:
        hits.append(DanmuTriggerHit("HardChoice", {"candidates": candidates}))
    return hits


def _match_card_played(ctx: dict[str, Any]) -> list[DanmuTriggerHit]:
    # B4 StartupCard：打出启动牌（快照手牌差近似，id 匹配启动牌集合）
    card = _norm(ctx.get("card"))
    if card in _STARTUP_CARDS:
        return [DanmuTriggerHit("StartupCard", {"card": card, "x": 1})]
    return []


def _match_combat_ended(ctx: dict[str, Any], tracker: Any) -> list[DanmuTriggerHit]:
    hits: list[DanmuTriggerHit] = []
    # C9 NoDamageStreak：无伤连胜里程碑（5/7/9…）
    count = _safe_int(ctx.get("no_damage_streak"))
    if count >= 5 and (count - 5) % 2 == 0:
        hits.append(DanmuTriggerHit("NoDamageStreak", {"count": count}))
    return hits


# 启动牌（ManualCardSets.Startup，打出时触发 StartupCard）
_STARTUP_CARDS = {
    "STOKE", "DARK_EMBRACE", "PYRE", "DEMON_FORM", "PRIMAL_FORCE", "ACCELERANT", "ADRENALINE",
    "SHADOW_STEP", "BULLET_TIME", "WELL_LAID_PLANS", "TRACKING", "MASTER_PLANNER", "ORBIT",
    "DECISIONS_DECISIONS", "THE_SMITH", "ARSENAL", "SWORD_SAGE", "VOID_FORM", "PILLAR_OF_CREATION",
    "DIRGE", "BORROWED_TIME", "HANG", "NEUROSURGE", "REAPER_FORM", "DOUBLE_ENERGY", "SUPERCRITICAL",
    "REBOOT", "VOLTAIC", "ECHO_FORM", "THE_BALL", "JACKPOT", "HIDDEN_GEM", "ROLLING_BOULDER", "APOTHEOSIS",
}


__all__ = ["detect_trigger", "pick_rule_phrase", "match_events", "DanmuTriggerHit"]
