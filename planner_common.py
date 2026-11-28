from __future__ import annotations

import re
from math import isfinite
from typing import Any


def _first_line_step(solver_plan: dict[str, Any]) -> dict[str, Any] | None:
    """Return the plan's first executable move (line[0].steps[0]) when /solver/plan is turn-grouped."""
    line = solver_plan.get("line") if isinstance(solver_plan.get("line"), list) else None
    if not line or not isinstance(line[0], dict):
        return None
    steps = line[0].get("steps") if isinstance(line[0].get("steps"), list) else None
    if not steps or not isinstance(steps[0], dict):
        return None
    return steps[0]


def _line_step_at(solver_plan: dict[str, Any], index: int | None) -> dict[str, Any] | None:
    """Return line[0].steps[index] (当前回合第 index 步)，越界时夹到首/末步。整回合缓存时按步进取。"""
    line = solver_plan.get("line") if isinstance(solver_plan.get("line"), list) else None
    if not line or not isinstance(line[0], dict):
        return None
    steps = line[0].get("steps") if isinstance(line[0].get("steps"), list) else None
    if not steps or not steps:
        return None
    i = index if isinstance(index, int) else 0
    if i < 0:
        i = 0
    if i >= len(steps):
        i = len(steps) - 1
    step = steps[i]
    return step if isinstance(step, dict) else None


MAP_POLICY_WAIT = "wait"
MAP_POLICY_FOLLOW = "follow"
MAP_POLICY_AUTO = "auto"
MAP_POLICIES = (MAP_POLICY_WAIT, MAP_POLICY_FOLLOW, MAP_POLICY_AUTO)

# "定个路线"（sts2_set_route mode=safe）用的内置权重：躲精英、血少优先休息。玩家自己配过 route_policy
# 时以玩家的权重为准（见 _safe_route_preferences），这里只是没配过时的保守默认。
SAFE_MAP_WEIGHTS: dict[str, int] = {
    "rest": 16,
    "shop": 8,
    "treasure": 6,
    "event": 6,
    "question": 5,
    "monster": 2,
    "elite": -18,
}
SAFE_MAP_LOW_HP_WEIGHTS: dict[str, int] = {
    "rest": 26,
    "shop": 12,
    "elite": -40,
    "monster": -2,
    "event": 4,
    "question": 3,
    "treasure": 2,
}
SAFE_MAP_HP_RATIO_BELOW = 0.6

# ---- 路线链式递推（lookahead）------------------------------------------------------------
#
# mod 的 /state `map.nodes` 给了**整幕的 DAG**：每个节点带 row/col/node_type，且 children/parents
# 是 `{row,col}` **坐标对**。旧代码也有"前瞻"（`_score_map_node` 里累加 `_collect_route_descendants`），
# 但它是**假前瞻**：只读节点自身的 children，而交给规划器的 available_nodes 根本没这个字段；
# 就算读全图，children 里是坐标、没有 node_type，取出来是空串 -> 权重 0 -> 前瞻恒等于 0。
#
# 现在改成真正的链式递推：用坐标建索引，从每个候选节点递归到 boss，按"饱和收益 + 逐层折扣 +
# 临门加权"评估**整条路线**，而不是只看眼前那一步。
ROUTE_DISCOUNT_PER_ROW = 0.8      # 每远一层，未来收益打几折（越远越不确定）
ROUTE_REPEAT_DECAY = 0.5          # 同类节点第 2、3 次出现按此递减：两个休息点 ≠ 两倍休息
ROUTE_BOSS_PREP_ROWS = 2          # 距 boss 不超过这几层算"临门一脚"
ROUTE_BOSS_PREP_MULTIPLIER = 2.0  # 临门休息/商店的倍率（补血、把金币花掉）
ROUTE_BOSS_PREP_GOLD = 120        # 金币不到这个数，"临门商店"就没那么香
ROUTE_LOOKAHEAD_ROWS_FALLBACK = 6 # 没配 lookahead_depth 时向前看几层（幕内层数一般 10~17）

# 篝火选项的语义关键词（mod 发的是 option_id / title / description）。
# 玩家口径：默认该**优先锻造升级**（升数值、降费用，战力涨得比几十点血值），血低才回血。
_REST_SMITH_TOKENS = ("smith", "upgrade", "forge", "temper", "锻造", "升级", "淬炼", "打磨")
_REST_HEAL_TOKENS = ("rest", "heal", "recover", "mend", "sleep", "休息", "回血", "治疗", "恢复")
# 血比低于这个值才把"回血"排在"锻造"前面。
REST_HEAL_HP_RATIO = 0.6
# 精英的机会成本随血量缓和：满血时惩罚打对折（精英给遗物+金币，未必是坏事），
# 血少时保持原样。**只打折、不清零**，否则满血会让精英变成免费。
ROUTE_ELITE_RELIEF_MAX = 0.5

# mod 下发的 node_type 是**游戏枚举名**（`Point.PointType.ToString()`），不是权重表的短键：
# 取值见 mod 自己的脚本 game_mod/scripts/test-natural-room-chain.ps1 与实测：
#   Monster / Elite / Shop / RestSite / Treasure / Unknown / Event（+ Ancient 起始点、Boss / SecondBoss）
# 关键的一条：`RestSite` 与权重键 `rest` **拼不上**，于是休息点权重恒为 0 —— 而 `_preferred_map_index`
# 过去还要求"分数 > 0 才采纳"，结果怪物（3 分）永远压过营地（0 分），**猫娘从不主动回血**。
_MAP_NODE_TYPE_ALIASES: dict[str, str] = {
    "monster": "monster",
    "enemy": "monster",
    "fight": "monster",
    "elite": "elite",
    "shop": "shop",
    "merchant": "shop",
    "restsite": "rest",
    "rest": "rest",
    "campfire": "rest",
    "treasure": "treasure",
    "chest": "treasure",
    "unknown": "question",
    "question": "question",
    "event": "event",
    "boss": "boss",
    "secondboss": "boss",
    "ancient": "ancient",
}

# 方位词 -> 取候选里的哪一项。按"先长词后短词"的顺序匹配，避免"最右边"被"右"提前吃掉。
_MAP_PICK_FIRST_TOKENS = ("最左", "第一个", "第一", "左边", "左", "first", "left")
_MAP_PICK_LAST_TOKENS = ("最右", "最后一个", "最后", "右边", "最右", "右", "last", "right")
_MAP_PICK_MIDDLE_TOKENS = ("中间", "正中", "当中", "中间那个", "middle", "center", "centre", "mid")
_MAP_PICK_CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# ★ 中文"抽N张牌"的判定：数字插在中间（实测真实文本是「抽2张牌」），
#   子串关键词"抽牌"不命中；同时必须**排除**「抽牌堆」(draw pile，不是抽牌流派)。
_ARCHETYPE_DRAW_RE = re.compile(r"抽[0-9一二三四五六七八九十两]+张牌|抽牌(?!堆)|摸牌")


def normalize_map_policy(value: Any) -> str:
    """未知取值一律退化为 wait：宁可不投，也不去抢玩家已经定好的路线。"""
    text = str(value or "").strip().lower()
    return text if text in MAP_POLICIES else MAP_POLICY_WAIT


def validate_map_vote_wait_seconds(value: Any) -> float:
    """UI 与配置共用秒数校验；0 表示不等，拒绝会造成无限等待的 NaN/inf。"""
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("地图等待时间必须是 0～86400 秒之间的数字") from exc
    if isinstance(value, bool) or not isfinite(seconds) or not 0 <= seconds <= 86400:
        raise ValueError("地图等待时间必须是 0～86400 秒之间的数字")
    return seconds


def parse_map_pick(token: Any, count: int) -> int | None:
    """把"中间 / 第二个 / 最左 / 最右 / 3"这类说法解析成候选位置（0 起算）。

    数字按**序数**理解（2 = 第二个 = 下标 1），与口语一致；要按 0 起算的明确下标请用 index 参数。
    解析不出返回 None，由调用方决定是报错还是退回安全路线。
    """
    if count <= 0:
        return None
    if isinstance(token, bool):
        return None
    if isinstance(token, int):
        return token - 1 if 1 <= token <= count else None
    text = str(token or "").strip().lower()
    if not text:
        return None
    for probe in _MAP_PICK_MIDDLE_TOKENS:
        if probe in text:
            return (count - 1) // 2
    for probe in _MAP_PICK_FIRST_TOKENS:
        if probe in text:
            return 0
    for probe in _MAP_PICK_LAST_TOKENS:
        if probe in text:
            return count - 1
    digits = re.findall(r"\d+", text)
    if digits:
        ordinal = int(digits[0])
        return ordinal - 1 if 1 <= ordinal <= count else None
    for char in text:
        if char in _MAP_PICK_CN_DIGITS:
            ordinal = _MAP_PICK_CN_DIGITS[char]
            return ordinal - 1 if 1 <= ordinal <= count else None
    return None


# ---- 商店统一评分 × 战斗用药水（规格 docs/shop_scoring.md，玩家口径 2026-10-07）----------------
#
# 玩家原话：
#   「商店好像很喜欢买遗物？这里最好评估一下那些是真正需要的。如果没有需要的就留到下一个商店」
#   「还可以买药，买两瓶药可以冲精英」
#   「这一块需要量化牌组和商店的可购买项，买评分最高的，都不高就不买」
#   「还有每次对战如果损血大于8就鼓励使用药水」
#
# 这套常量就是那份规格里的「五、配置项」（默认按保守策略）。玩家自己配过 shop_policy 记录时，
# 用同一批键名覆盖（见 _shop_policy），与 route_policy / deck_policy 一个路子。
SHOP_SCORE_THRESHOLD = 40.0          # shop_score_threshold：统一门槛
SHOP_ELITE_POTION_BOOST = 1.4        # shop_elite_potion_boost：冲精英时药水加权
SHOP_GOLD_FLOOR = 120                # shop_gold_floor：留给下一个商店的底钱
SHOP_GOLD_RICH = 300                 # 金币多于它 -> 门槛略降（避免"攒着不花"）
SHOP_GOLD_POOR = 100                 # 金币少于它 -> 门槛升高（优先留钱）
SHOP_THRESHOLD_RICH_DELTA = -8.0     # 金币很多时的门槛偏移
SHOP_THRESHOLD_POOR_DELTA = 12.0     # 金币很少时的门槛偏移
SHOP_ELITE_THRESHOLD_DELTA = -12.0   # 前方有精英 -> 门槛下调（药水更容易买）
SHOP_MATURE_THRESHOLD_DELTA = 10.0   # 遗物覆盖度整体很高（构筑成型）-> 门槛上调，倾向留钱
SHOP_COVERAGE_MATURE_RATIO = 0.5     # 缺口被已有遗物覆盖掉一半以上 = "构筑已成型"
SHOP_THRESHOLD_MIN = 10.0
SHOP_THRESHOLD_MAX = 90.0
SHOP_POTION_PRICE_FALLBACK = 60      # 读不到商店药水标价时，"补满栏位要多少钱"按这个估
SHOP_CARD_FIT_SCALE = 60.0           # _score_reward_card 原始分 -> 补缺分 0..1 的折算（见 _score_shop_card）
SHOP_UNKNOWN_RELIC_GAP_RATIO = 0.5   # 读不出效果类别的遗物：按"最缺那一维"打折算（保守先验）
SHOP_RELIC_RARITY_BONUS = {          # 稀有度加分（点数，直接加在 0..100 的补缺分上）
    "rare": 18.0,
    "uncommon": 10.0,
    "common": 4.0,
    "boss": 22.0,
    "ancient": 22.0,
    "event": 12.0,
    "shop": 8.0,
    "starter": 0.0,
}
SHOP_RELIC_KEYWORD_BONUS = 6.0       # 命中一个"正面关键词组"加多少
SHOP_RELIC_KEYWORD_BONUS_CAP = 12.0  # 关键词加分上限（不让稀有度+关键词把"没缺口"顶过门槛）
SHOP_RELIC_CONDITION_PENALTY = 10.0  # 条件苛刻/负面遗物的降权
SHOP_COST_PENALTY_SCALE = 50.0       # 代价惩罚 = 价格/金币 × 这个系数
SHOP_COST_PENALTY_HALF_BONUS = 30.0  # 花掉一半以上的钱：重罚
SHOP_POTION_NEEDED_FIT = 0.85        # 命中"还缺的那一类"药水的补缺分
SHOP_POTION_COVERED_FIT = 0.20       # 已经有的那一类：低分（药水栏位有限，重复买同一类不值）
SHOP_POTION_BASELINE_FIT = {         # 没有"冲精英"情境时的基础救急价值
    "defense": 0.60,
    "burst": 0.55,
    "aoe": 0.50,
    "heal": 0.50,
    "cleanse": 0.45,
    "draw": 0.30,
    "other": 0.20,
}
# "冲精英需要的那几类"：爆发 + 群伤 + 减伤 + 回血（规格「一之二」给的例子是"还缺爆发类"）。
SHOP_ELITE_POTION_NEEDS = ("defense", "burst", "aoe", "heal")

# 牌组缺口 / 遗物覆盖 / 药水用途共用的六维（规格「一、牌组量化」）。
SHOP_DIM_OUTPUT = "output"
SHOP_DIM_DEFENSE = "defense"
SHOP_DIM_DRAW = "draw"
SHOP_DIM_ENERGY = "energy"
SHOP_DIM_AOE = "aoe"
SHOP_DIM_QUALITY = "quality"
SHOP_DIMENSIONS = (
    SHOP_DIM_OUTPUT,
    SHOP_DIM_DEFENSE,
    SHOP_DIM_DRAW,
    SHOP_DIM_ENERGY,
    SHOP_DIM_AOE,
    SHOP_DIM_QUALITY,
)
SHOP_DIM_LABELS = {
    SHOP_DIM_OUTPUT: "输出",
    SHOP_DIM_DEFENSE: "格挡",
    SHOP_DIM_DRAW: "抽牌",
    SHOP_DIM_ENERGY: "费用",
    SHOP_DIM_AOE: "群伤",
    SHOP_DIM_QUALITY: "质量",
}
# 每回合大概能打出几张牌（估算"一回合能打多少伤害/多少格挡"）。牌组量化只用得到它的量级。
SHOP_CARDS_PER_TURN = 3
# 每层期望：输出/格挡的目标线随层数抬高（规格：「低于每层期望 → 缺口高」）。
SHOP_TARGET_DAMAGE_BASE = 10.0
SHOP_TARGET_DAMAGE_PER_FLOOR = 0.8
SHOP_TARGET_BLOCK_BASE = 6.0
SHOP_TARGET_BLOCK_PER_FLOOR = 0.6
# 牌组大于这么多张才开始嫌"循环不动"；没抽牌手段时缺口按比例放大。
SHOP_DRAW_SIZE_BASE = 14
SHOP_DRAW_SIZE_SPAN = 10
SHOP_DRAW_CARD_CREDIT = 2          # 每 2 张抽牌牌就把"循环缺口"抹平
SHOP_AOE_TARGET_UNITS = 3.0        # 群伤/状态牌的目标张数（群伤算 1 张，单体状态算半张）
SHOP_STATUS_CARD_CREDIT = 0.5
# "牌组质量"缺口：未升级的打击/防御 与 诅咒/状态牌。
SHOP_QUALITY_BASIC_TARGET = 6.0
SHOP_QUALITY_CURSE_TARGET = 2.0

# 牌组量化用的词表（mod 下发的 card_type 是 C# 枚举名：Attack/Skill/Power/Status/Curse；
# 卡名/规则文本可能是中文，所以中英都留一份）。
_DECK_ATTACK_TYPE_TOKENS = ("attack", "攻击")
_DECK_BASIC_CARD_TOKENS = ("strike", "defend", "打击", "防御")

#: 基础牌（初始牌组那些）的**卡名子串** —— 用于"这张奖励牌会不会拉低牌组质量"。
#:
#: 与 `_DECK_BASIC_CARD_TOKENS` 是同一套词，但那份是给"**已在牌组里**的牌"做统计用的
#: （`_deck_gap_vector:3043` 还会额外检查 `upgraded`）；
#: 这里只判"**要不要再拿一张**"，所以不看升级状态。
#: ★ 只用于 `quality` token 的着力点（见 `_indices_worth_penalizing` 的奖励屏分支）。
def _is_basic_card_like(card: dict[str, Any]) -> bool:
    """这张卡是不是"基础牌"（打击/防御这类初始牌）。

    读 `card_id` / `name` 做子串匹配（中英都认），读不出就返回 False ——
    与全局"匹配不准就别动"一致。
    """
    text = f"{card.get('card_id') or ''} {card.get('name') or ''}".lower()
    if not text.strip():
        return False
    return any(token in text for token in _DECK_BASIC_CARD_TOKENS)

_DECK_CURSE_TYPE_TOKENS = ("curse", "诅咒", "status", "状态")
_DECK_DRAW_TOKENS = ("draw", "抽", "replay", "重放", "exhaust", "消耗", "scry", "预见", "discard", "弃牌")
_DECK_AOE_TOKENS = ("all enemies", "all enemy", "所有敌人", "全体敌人", "全部敌人", "each enemy", "每个敌人", "aoe")
_DECK_STATUS_TOKENS = ("vulnerable", "易伤", "weak", "虚弱", "poison", "中毒", "burn", "燃烧", "slow", "迟缓", "debuff", "负面")
# 药水类别之间的优先级（同样是"还缺这一类"时，减伤 > 爆发 > 群伤 > 回血 > 清状态）。
_POTION_CATEGORY_PRIORITY = {
    "defense": 1.0,
    "burst": 0.95,
    "aoe": 0.9,
    "heal": 0.85,
    "cleanse": 0.8,
    "draw": 0.5,
    "other": 0.3,
}

# 遗物效果类别 -> 缺口维度（规格「一之二」的类别表）。一张表同时给"已有遗物覆盖度"和"商店遗物补缺"用。
_RELIC_EFFECT_DIM_TOKENS: dict[str, tuple[str, ...]] = {
    SHOP_DIM_OUTPUT: ("力量", "strength", "伤害", "damage", "攻击", "attack"),
    SHOP_DIM_DEFENSE: ("格挡", "block", "敏捷", "dexterity", "减伤", "护盾", "护甲", "plated", "shield"),
    SHOP_DIM_ENERGY: ("能量", "energy", "费用", "cost", "energy gain"),
    SHOP_DIM_DRAW: ("抽牌", "draw", "手牌", "hand", "弃牌", "discard"),
}
# 生存/回复也算"格挡/生存"这一维（规格里牌组量化的维度就叫「格挡/生存」）。
_RELIC_SURVIVAL_TOKENS = ("治疗", "heal", "回复", "restore", "regenerate", "再生", "最大生命", "max hp", "复活", "revive")
# 经济类没有对应的牌组缺口维度：钱够时它几乎没用，只在钱少时略微值一点（见 _score_shop_relic）。
_RELIC_ECON_TOKENS = ("金币", "gold", "商店", "shop", "价格", "price")
_RELIC_COMBAT_START_TOKENS = ("战斗开始时", "at the start of combat", "每回合开始时", "at the start of each turn")
_RELIC_STRONG_TOKENS = ("力量", "strength", "敏捷", "dexterity", "能量", "energy", "格挡", "block", "抽牌", "draw", "治疗", "heal")
# 条件苛刻 / 有代价的遗物要降权（规格：「条件苛刻或负面的降权」）。
_RELIC_HARSH_TOKENS = (
    "生命值低于", "血量低于", "hp is below", "低于 50", "失去", "lose",
    "诅咒", "curse", "无法", "cannot", "随机", "random", "只在", "only if",
)

# 药水用途 -> 类别（规格「一之二」「二」：单体爆发 / 群伤 / 减伤 / 回血 / 清状态 / 抽牌 / 其它）。
# 判定顺序很重要：EXPLOSIVE_POTION（对所有敌人造成伤害）必须落到"群伤"，不能被"伤害"先判成爆发。
_POTION_AOE_TOKENS = ("all enemies", "all enemy", "所有敌人", "全体敌人", "全部敌人", "each enemy", "每个敌人", "aoe", "炸弹", "bomb", "explosive", "爆炸")
_POTION_DEFENSE_TOKENS = ("block", "格挡", "护盾", "shield", "dexterity", "敏捷", "dodge", "闪避", "intangible", "无形", "ghost", "幽灵", "weak", "虚弱", "减伤", "damage reduction", "armor", "护甲")
_POTION_HEAL_TOKENS = ("heal", "回复", "治疗", "regen", "再生", "restore", "恢复", "blood", "血", "fruit", "max hp", "最大生命")
_POTION_CLEANSE_TOKENS = ("remove", "清除", "cure", "净化", "purify", "debuff", "负面", "artifact", "神器", "免疫", "dispel", "驱散")
_POTION_BURST_TOKENS = ("strength", "力量", "fire", "火焰", "damage", "伤害", "rage", "狂暴", "duplicate", "复制", "fear", "恐惧", "poison", "中毒")
# "抽牌"这一类按规格的命名收口，装的是**资源型**药水（抽牌 / 找牌 / 加能量）：它们都能让你多打几张，
# 但都不是"能把这一下伤害压下去"的保命手段，所以在战斗里一样评 0 分（规格「八、用哪一瓶」第 5 条）。
_POTION_DRAW_TOKENS = ("draw", "抽牌", "hand", "手牌", "discard", "弃牌", "liquid memories", "记忆", "insight", "洞察", "replay", "重放", "card", "卡牌", "能量", "energy")

_POTION_CATEGORY_LABELS = {
    "burst": "单体爆发",
    "aoe": "群伤",
    "defense": "减伤",
    "heal": "回血",
    "cleanse": "清状态",
    "draw": "抽牌",
    "other": "其它",
}

# ---- 战斗用药水（规格「八」）---------------------------------------------------------------
COMBAT_POTION_HP_LOSS_THRESHOLD = 8.0     # potion_hp_loss_threshold：预计损血 > 8 就鼓励用药水
COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER = 1.25  # 已知是普通小怪战：阈值上调一点（更省）
COMBAT_POTION_ELITE_THRESHOLD_MULTIPLIER = 1.0   # 精英/Boss：不动阈值（更愿意喝）
COMBAT_POTION_LOW_HP_RATIO = 0.3          # 血量低于三成：回血类也值得喝
# 「能不能把这 8 点以上损血压下去」的评分（规格「用哪一瓶」的 1~5 条）。
COMBAT_POTION_SCORES = {
    "defense": 88.0,   # 1. 减伤/闪避/格挡：最高（直接抵掉这一下）
    "cleanse": 86.0,   # 4. 清状态：与减伤同档的备选
    "aoe": 72.0,       # 2. 群伤/清场：次高（把还在打你的怪清掉）
    "burst": 55.0,     # 单体爆发：能秒掉一只，但压不下这一下
    "heal": 0.0,       # 3. 回血：只在会死 / 血量 < 30% 时才算数（见 _combat_potion_score）
    "draw": 0.0,       # 5. 抽牌等：战斗里不当保命手段，不因这条规则触发
    "other": 0.0,
}
COMBAT_POTION_LETHAL_HEAL_SCORE = 86.0    # 预计会死时的回血类评分
COMBAT_POTION_LOW_HP_HEAL_SCORE = 80.0    # 血量低于三成时的回血类评分
