from __future__ import annotations

import re
import time
from time import monotonic
from typing import Any

from .planner_interface import PlannedOperation


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


class STS2HeuristicPlanner:
    #: 跨局教训 → 可匹配选项的语义关键词。
    #:
    #: 复盘的产物是**整句中文**（如「下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出」），
    #: 整句拿去匹配选项描述必然匹配不上，所以把它压成下面这小组**语义 token**，再由
    #: ``_indices_worth_penalizing`` 落到具体屏（目前只落地「路线」与「选牌」两屏 —— 匹配不准的
    #: 屏宁可不动，避免把复盘变成噪声）。
    #:
    #: 键是 token（同时也是地图 `node.type` / 卡牌 `card_type` 的小写值），值是该 token 的
    #: 中文触发词。新增玩法时在这里加一行即可，不要在 `_lesson_tokens` 里堆 if。
    _LESSON_KEYWORD_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("elite", ("精英",)),
        ("monster", ("小怪", "打怪", "普通敌人")),
        ("rest", ("休息", "睡觉", "回血点", "篝火")),
        ("shop", ("商店", "买东西", "攒钱", "金币")),
        ("treasure", ("宝箱", "宝藏", "开箱")),
        ("curse", ("诅咒", "别拿咒")),
        # ★生命上限是**永久**损失（`max_hp` 不可恢复），用户报告的核心痛点：
        # 「有一些事件是可以消耗生命值上限的并且可以重复选择，这个时候脚本会一直选择消耗，
        #   直接把生命值变成了1」。没有这一条时，"别再扣生命上限"这类复盘**抽不出任何 token**
        # ⇒ 教训完全无法生效。
        #
        # ⚠️ 为什么不需要（也没有）"只在事件屏生效"的过滤：**结构上不可能泄漏**。
        # 实测确认各屏的匹配口径：
        #   * 路线屏 —— 只把节点 `type` 经 `_map_node_weight_key` 归一后与 token **等值**比较，
        #     取值域是 `rest`/`shop`/`elite`/... ，永远不等于 `max_hp`；
        #   * 选牌屏 —— 比较 `card_type`/`rarity` 与 token 等值，取值域是
        #     `attack`/`skill`/`power`/`curse`/... ，同样不可能等于 `max_hp`；
        #   * 其余屏 —— `_indices_worth_penalizing` 没有分支。
        # 我一度加过一个 `_EVENT_ONLY_TOKENS` 过滤，但**变异测试证明它不可观测**
        # （删掉它全部测试照样通过）⇒ 那是"无法被测试保护的死代码"，故删除。
        # 若将来给路线/选牌屏加上"按文案模糊匹配"的能力，才需要重新引入过滤。
        #
        # ⚠️ **刻意不加**普通掉血 token（`hp`）：实测真实反思「先垫格挡喝回血药再输出」
        # 含「回血」二字，会给纯**战斗**建议抽出 `hp`，从而破坏
        # `test_real_lesson_demotes_matching_node` 钉住的"战斗建议不得改动路线"契约。
        # 扣上限这一条已经完整覆盖用户报告，收益明确、风险为零。
        ("max_hp", ("生命上限", "最大生命", "扣上限", "命上限")),
        ("skill", ("格挡", "垫格挡", "防御", "保命", "保血")),
        ("attack", ("输出", "伤害", "抢血", "斩杀")),
        ("power", ("能力牌", "攒力量")),
        # ★★ 六维缺口的「群伤」与「牌组质量」（2026-10-19 补）。
        #
        # 为什么必须补：`deck_gap_reflection` 会产出「这局几乎没有群伤牌，
        # 下局我想早点拿 AOE」这类教训，但**关键词表里没有对应项** ⇒
        # `_lesson_tokens` 抽不出任何 token ⇒ 教训**完全无法生效**。
        # 实测（补之前）：死因 aoe 的六维教训只抽出 `attack`/`skill`，
        # AOE 那一维的信号**整个丢失** —— 这正是"说了等于没说"的老毛病。
        ("aoe", ("群伤", "群攻", "群怪", "多敌")),
        # ── 牌组质量 ──────────────────────────────────────────────────────
        # ⚠️ **刻意不收「升级」二字**：实测它会**误伤路线类教训** ——
        #    `"上次在篝火休息浪费了升级机会，以后别选休息"` 讲的是**休息点**，
        #    但含「升级」⇒ 会额外抽出 `quality` ⇒ 破坏
        #    `test_route_lesson_changes_route_order` 钉住的"路线教训只影响路线"契约。
        #    这是我自己补 token 时踩的坑（测试立刻抓到）。
        # ⇒ 只收**指向牌**的搭配词，不收裸「升级」。质量教训的模板用「升级关键牌」
        #    「删牌」，这里的 `关键牌`/`升级关键牌` 都能命中。
        ("quality", ("升级关键牌", "精简牌组", "删牌", "废牌", "基础牌")),
    )

    def __init__(self, logger: Any | None = None) -> None:
        self._logger = logger
        # ★ 战斗决策留痕的计数（**每场战斗**上限，不是每局/每实例）。见 _combat_log / _COMBAT_LOG_MAX。
        #   t39：原注释写「每局限流」，而实现是「每实例一次性累计」—— 两者不一致，
        #   且计数**永无重置点** ⇒ 打满上限后本实例永久静默。现改为随**战斗身份**重置。
        self._combat_log_count = 0
        self._combat_log_last_key = ""
        self._combat_log_last_at = 0.0
        # ★ t39：战斗身份 + 封顶告警只报一次的标志。
        #   身份 = (屏幕|层|是否战斗) 三元的拼装串，沿用 service.py:3217 的公式形态。
        #   ★ 为什么必须由身份驱动而不是时间驱动：时间驱动的重置会让**长战斗**
        #     仍然被封顶，而长战斗恰恰是最需要留痕的那种局面（boss 战动辄几十回合）。
        self._combat_log_battle_identity = ""
        self._combat_log_cap_warned = False
        # ★ 战斗纯求解器的**死锁保险**状态（见 _combat_solver_only_outcome）。
        #   语义：同一局面指纹下，求解器**看着最新局面**仍连续给不出可执行动作的次数。
        #   指纹变化即清零（新局面 = 旧计数无意义）。
        self._solver_deadlock_signature = ""
        self._solver_deadlock_count = 0
        # ★★ D1：与死锁计数**必须分开**的第二个计数器（见 _combat_solver_only_outcome）。
        #   语义：同一局面指纹下，「计划过期（建议的牌已不在手）」连续发生的次数。
        #   ★ 为什么不只是"加个 if 直接 return None"：反复过期本身也是异常
        #     （上游 SolverController.cs:175-177 把 DeploymentDrift 计入 UnexpectedReplanCount，
        #      当作"计划失效"的缺陷信号），若完全不计上界，一旦某局面每 tick 都过期
        #      就会**永久重取、卡死整局**。故给上界：达 _SOLVER_STALE_RECLAIM_LIMIT 次
        #      即**转入死锁计数**，最迟第 LIMIT+RETRIES 次仍会发 end_turn 脱离。
        self._solver_stale_reclaim_count = 0
        # ★ t13（D3）：非玩家行动窗口的**独立**计数（见 `_SOLVER_OUT_OF_WINDOW_LIMIT`）。
        #   与 `_solver_deadlock_count` 严格分开：窗口没开时不是"求解器无解"，
        #   把它计进 N/3 会让 N 越过阈值（实机 5/3、7/3）且永远等不到兜底。
        self._solver_out_of_window_count = 0
        # ══════════════════════════════════════════════════════════════════════════
        # ★★★ t45：**准入闸门**的记账（见 `_combat_solver_gate_blocked`）
        # ══════════════════════════════════════════════════════════════════════════
        #   三个计数**互不掩盖**（t37 契约第 3 条的同一精神：任一 tick 结束后，
        #   都要能从日志读出「闸门放行了几次 / 等了几次 / 判了几次真无解」）：
        #     allowed_count      —— 过闸门（= 本 tick 真的可能发动作）
        #     wait_count         —— `waiting_solver`：**不计数死锁、不发任何动作**
        #     cannot_act_count   —— `solver_cannot_act`：真无解，**走既有上界**
        #   ★ 与 `_solver_stale_reclaim_count` 的关系：后者是 t23 遗留的"事后补救"计数，
        #     只在闸门放行后、执行期才失败时仍可能 +1；`waiting_solver` 路径
        #     **绝不**动它 —— 这正是「上界不得被等待消耗掉」的落地方式。
        self._solver_gate_allowed_count = 0
        self._solver_gate_wait_count = 0
        self._solver_gate_cannot_act_count = 0
        # 最近一次闸门结论（供 service 侧观测与测试断言）。
        self._solver_gate_last: dict[str, Any] = {}
        # 等待态日志的限流状态：`为什么在等` 的键变了就立刻打一行；
        # 同因连续等待则每 _SOLVER_GATE_LOG_EVERY 次打一行（行内带**累计**计数，
        # 于是"被闸门拦下的 tick 数"可以逐字从日志读出来，不靠估算）。
        self._solver_gate_log_key = ""
        self._solver_gate_blocked_since_log = 0
        # 上一次闸门结论（用于"恢复放行"只在**跃迁**时留痕，避免每 tick 重复一行）。
        self._solver_gate_last_verdict = ""
        # 地图投票策略，由 service.startup 用插件配置覆盖：
        #   wait（默认）——多人局里彻底等待：不投票、也不自动跟票，直到玩家通过对 NEKO 说话下令
        #                 （见 sts2_set_route）。单机局没有队友可等，仍按自己的偏好选，否则会卡死。
        #   follow     ——玩家已投票就镜像跟随；无人投票满 map_vote_wait_seconds 后自己选。
        #   auto       ——不等，立即按自己的偏好选。
        self._map_policy = MAP_POLICY_WAIT
        self._map_vote_wait_seconds = 20.0
        self._map_wait_key = ""
        self._map_wait_started_at = 0.0
        # 选牌屏的推进游标：签名 + 这一屏已按过的 option_index 集合。同一屏上永远不重复按同一张卡，
        # 否则需要选 2 张的界面会一直重按第 0 张、max_select 永远到不了（"选两张卡卡住"）。
        # 注意 _selection_pressed 只由**真的按键**推进（note_selection_option_pressed），
        # 不由规划推进——plan() 每 tick 都跑，用规划推进会把候选白白耗光并导致永久卡屏。
        self._selection_cursor_key = ""
        self._selection_pressed: set[int] = set()
        # 同一屏连续"候选按遍仍返回 None"的次数，达到 _SELECTION_EXHAUSTED_RETRIES 就重置光标重按，
        # 保证这一屏永远不会陷入"再也规划不出动作"的死锁。
        self._selection_exhausted = 0
        # ★事件屏"同一选项选过几次"的记忆（缺陷 A3）。
        # 与 _selection_pressed 同范式：以**屏签名**为键，换屏即重置；
        # 只由**真的派发成功**推进（note_event_option_chosen），不在 plan() 里累加。
        # 作用：可重复选择的事件（选项选完仍在）不会把同一项选到死。
        self._event_memory_key = ""
        self._event_choice_counts: dict[int, int] = {}
        # 商店"这次进店看过货了没"：货架开过一次、又关掉之后**不能再开**。
        # mod 的 open_shop_inventory 是**开关**，而开关两态的 screen 都叫 SHOP，
        # 只看 open_shop_inventory 在不在 available_actions 里就会开->关->开无限来回（玩家看到的死循环）。
        self._shop_visit_key = ""
        self._shop_viewed = False
        # 商店统一评分的日志去重键：plan() 每 tick 都跑一次（约 1 次/秒），不去重就是刷屏。
        # 键里带"最高分/门槛/决定"，所以调参后参数一变就会重新记一行。
        self._shop_log_key = ""
        # 最近一次路线决定留下的"前方有没有精英"：**商店界面拿不到地图**（mod 的 map payload 只在
        # 地图屏下发，BuildMapPayload 一离开地图就返回 null），所以只能在选路那一刻把结论记下来，
        # 进店后照着用（规格「路线/精英信息：evaluate_map_route 那一套已经在算精英压力，复用它」）。
        self._route_elite_ahead = False
        self._last_route_node_type = ""
        # 最近一次记录过的地图路线评分（同一局面只记一次，防止每 tick 刷屏）。
        self._last_map_route_log_key = ""
        self._last_reward_route_log_key = ""
        #: ★ 最近一次选卡评估的留痕（供 `_log_reward_route` 输出，便于线上核对）
        self._last_reward_scored: list[tuple[int, float]] = []
        self._last_reward_archetype_tags: list[str] = []
        self._last_reward_bias: set[str] = set()
        self._last_reward_lesson_signals: dict[str, int] = {}
        # 最近一次记录过的事件屏评分（同上，事件屏此前完全没有决策日志）。
        self._last_event_route_log_key = ""
        # 最近一次记录过的变换屏评分（同上，变换屏此前同样是决策日志盲区）。
        self._last_transform_route_log_key = ""
        # 奖励领取游标：当前奖励清单的签名 + 已经**真的领过**的下标。
        # 药水栏满时"领药水奖励"发不下去，而 mod 照样回 completed —— 不记账就会无限重领同一个。
        self._reward_claim_key = ""
        self._reward_claim_tried: set[int] = set()
        # ★★ D11（t28）：本屏**已确认不可选**的事件选项下标（被游戏锁定 / 被拒）。
        #   ★ 与 `_reward_claim_*` 完全同构：**按屏键隔离**，换屏自动清空，不跨屏累积。
        #   实机缺陷（2026-10-20，ENDLESS_CONVEYOR）：options[0] `is_locked:true`（金币18不足），
        #   插件却给它 +10 分、给未锁的 options[1] 0 分 ⇒ 每轮选被锁项 ⇒ 99 次被拒 ⇒ 永久卡死。
        self._event_locked_set_key_cached = ""
        self._event_locked_set_cached: set[int] = set()
        # 选人界面：「随机」只按一次（按键由执行推动），按过就转入开局。
        self._character_pick_key = ""
        self._character_pick_done = False
        self._character_choice_log_key = ""
        self._rest_choice_log_key = ""
        # 同类的"别重复按同一个按钮"状态：bundle 选过没、capstone 扫到第几个。
        self._bundle_signature = ""
        self._bundle_chosen = False
        self._capstone_signature = ""
        self._capstone_last_option = -1

    _MAX_CAPSTONE_SWEEP = 8
    # 选牌屏：候选按遍后允许返回 None 的次数，超过就重置光标重按（防死锁，见 _next_selection_option）。
    _SELECTION_EXHAUSTED_RETRIES = 2

    def configure_map_policy(self, *, policy: str, vote_wait_seconds: float) -> None:
        self._map_policy = normalize_map_policy(policy)
        self._map_vote_wait_seconds = max(0.0, float(vote_wait_seconds or 0))
        self._map_wait_key = ""
        self._map_wait_started_at = 0.0

    def _log_reward_route(
        self,
        scored: list[tuple[int, float]],
        chosen: int | None,
        *,
        skipped: bool,
        archetype_tags: list[str] | None = None,
        bias: set[str] | None = None,
        lesson_signals: dict[str, int] | None = None,
    ) -> None:
        """把选卡/跳牌的评估结果用 info 级记一次。

        ★ 为什么必须单独写（与 `_log_map_route` 同因）：
        `_debug()` 在本文件里是**空函数**（`L456-457 return`），
        所有 `[sts2_reward_*]` / `[sts2_archetype]` 调试行**其实都没输出**。
        于是线上完全无法核对"这次到底为什么选/跳这张牌"——
        而"选卡质量"正是用户点名的核心（诅咒卡污染、垃圾卡进卡组）。
        选路已有 `_log_map_route` 留痕，选卡同样必须留痕。
        """
        if self._logger is None:
            return
        try:
            key = "%s|%s|%s|%s" % (
                chosen,
                skipped,
                ",".join(sorted(bias or set())),
                "|".join("%s:%s" % (i, s) for i, s in scored),
            )
            if key == self._last_reward_route_log_key:
                return
            self._last_reward_route_log_key = key
            self._logger.info(
                "[sts2_reward_route] 流派=%s 成型=%s 教训=%s 候选评分=%s -> %s",
                archetype_tags or [],
                sorted(bias or set()),
                lesson_signals or {},
                ", ".join("%s=%s" % (i, s) for i, s in scored),
                "跳牌（全候选不值得拿）" if skipped else "选 option_index=%s" % chosen,
            )
        except Exception:
            pass

    def _debug(self, message: str, *args: Any) -> None:
        return

    # ── ★ 战斗决策留痕（2026-10-18）────────────────────────────────────────────
    # 为什么必须单独写：`_debug` 在上方是个**空函数**，所有 `[sts2_*]` 战斗调试行
    # 其实一个字都没输出。战斗是玩家最容易质疑的决策（"求解器算得对，为什么不照打？"），
    # 而线上**完全没有留痕** ⇒ 既无法核对、也无法统计"到底按不按求解器打"。
    # 这与 `_log_map_route`（:507）当初补留痕的理由完全相同，但战斗这条最重要的
    # 路径一直漏着。这里补上，并且**显式对比"求解器建议"与"最终执行"**。
    #
    # ★★ 限流器的坑（2026-10-18 实机踩到，自查修）：
    #   第一版写成"每实例最多 _COMBAT_LOG_MAX 条"，而计数**永不重置** ⇒
    #   打满 60 条后**永久静默**。实测：插件 11:47:25 启动，11:47–11:49 打出
    #   56 条 follow + 4 条 fallback = 60 条，**之后整局再也不输出**
    #   （现象与"留痕没生效"难以区分，害我又排查了一轮）。
    #   ⇒ 改成**按"局面+动作"去重 + 每秒窗口限流**：
    #     同一个 (屏幕|层|回合|求解器建议|实际执行) 只记一次 —— 这既压掉了
    #     最要命的重复刷屏（实测同一局面重复 8~20 次），又**不会**因为总量到顶
    #     而再也看不到新决策。只有完全不同局面才产生新行，长局也能看全。
    #
    # ★★ 同一个坑的**第二次**踩（2026-10-20，t39）：上面那段注释说"改成去重+限流"，
    #   但**总量封顶这一层并没有被改掉** —— 200 这个阈值与 `:603` 的 return
    #   仍是同一个**永不重置**的全局计数。⇒ 只是把阈值从 60 推迟到 200，
    #   **不是修好了**。实机证据：10:59 同时段 Combat Solver 侧 ROUTE_ACTION
    #   3532 条 / 45 个 traceId，而 `[sts2_combat_follow]` **0 条** ⇒
    #   45 条建议全部"无可配对"，跨 mod 对照失效。
    #   ⇒ 现在把封顶语义改为**每场战斗**：计数与去重键随**战斗身份**归零
    #     （身份 = 屏幕|层|是否战斗，见 `_battle_identity`）。
    #   ★ 并且触及上限时**必须留一行告警**（见 `_combat_log`）：静默正是这个
    #     缺陷能潜伏两轮而无人察觉的原因。
    _COMBAT_LOG_MAX = 200  # 每场战斗的留痕上限（达到后打一行告警，换战斗即重置）
    _COMBAT_LOG_MIN_INTERVAL = 0.25  # 秒；同键去重之外再加一道时间闸

    def _battle_identity(self, snapshot: dict[str, Any] | None, classification: dict[str, Any] | None = None) -> str:
        """战斗身份 —— 留痕计数的重置依据。

        ★ 沿用 `service.py:3217` 的公式形态 `f"{screen}|{floor}|{act}|{in_combat}|..."`，
          但只用 planner 内确实拿得到的三个分量（`plan()` 的局部量 `snapshot` /
          `classification`），避免为了凑格式而去读 planner 不该读的状态。

        ★ 为什么是这三个分量：屏幕变了 / 层变了 / 进战斗状态变了，都意味着
          "这是另一场战斗"。反之同一场战斗内这三者恒定 —— 于是计数在其中累计、
          换战斗时归零。**不引入时间分量**（时间驱动会让长战斗仍被封顶）。
        """
        snap = snapshot if isinstance(snapshot, dict) else {}
        cls = classification if isinstance(classification, dict) else {}
        screen = str(snap.get("screen") or cls.get("state_name") or "")
        floor = str(snap.get("floor") if snap.get("floor") is not None else "")
        in_combat = cls.get("in_combat")
        if in_combat is None:
            in_combat = snap.get("in_combat")
        return f"{screen}|{floor}|{in_combat}"

    def _combat_log(
        self,
        message: str,
        *args: Any,
        dedup_key: str = "",
        battle_identity: str | None = None,
    ) -> None:
        """战斗决策的 info 级留痕。

        ★ 去重键优先（同一局面/同一决策只记一次），去重键为空时退回纯限流。
        ★ 总量封顶是**每场战斗**的：传入 `battle_identity` 且与上次不同 ⇒
          计数、去重键、告警标志全部归零，新战斗重新获得完整留痕预算
          （见 `_COMBAT_LOG_MAX` 上方的说明）。
        ★ 触及上限时**留一行告警**，绝不静默 return —— 静默正是本缺陷
          能潜伏两轮而无人察觉的原因。
        """
        if self._logger is None:
            return
        try:
            # ── ① 战斗身份变化 ⇒ 归零（必须在其余判定**之前**）──────────────
            identity = battle_identity
            if identity is None:
                identity = self._combat_log_battle_identity or ""
            if identity != self._combat_log_battle_identity:
                self._combat_log_battle_identity = identity
                self._combat_log_count = 0
                self._combat_log_last_key = ""   # 新战斗的首条不得被旧战斗的键吞掉
                self._combat_log_cap_warned = False

            now = time.time()
            if dedup_key:
                if dedup_key == self._combat_log_last_key:
                    return
                self._combat_log_last_key = dedup_key
            # ── ② 每场战斗上限：达上限 ⇒ 打一行告警（只一次），然后丢弃正文 ──
            if self._combat_log_count >= self._COMBAT_LOG_MAX:
                if not self._combat_log_cap_warned:
                    self._combat_log_cap_warned = True
                    self._logger.info(
                        "[sts2_combat_log_cap] 本场战斗留痕已达上限 计数=%s 上限=%s "
                        "战斗身份=%s —— 本场后续战斗留痕将不再输出（换战斗后自动恢复）",
                        self._combat_log_count,
                        self._COMBAT_LOG_MAX,
                        self._combat_log_battle_identity or "(未提供)",
                    )
                return
            if now - self._combat_log_last_at < self._COMBAT_LOG_MIN_INTERVAL:
                return
            self._combat_log_count += 1
            self._combat_log_last_at = now
            self._logger.info(message, *args)
        except Exception:
            pass

    def _combat_log_solver_vs_executed(
        self,
        *,
        solver_kind: Any,
        solver_card_id: Any,
        solver_target: Any,
        executed_action: str,
        executed_kwargs: dict[str, Any],
        matched: bool,
        fallback_reason: str = "",
        hand: list[Any] | None = None,
        snapshot: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
    ) -> None:
        """★ 核心留痕：**求解器建议了什么 / 实际执行了什么 / 是否一致**。

        这是回答"战斗时到底按不按求解器打"的唯一可靠依据。格式固定，便于后续
        用 grep / 正则批量统计"按求解器打的比例"：

            [sts2_combat_follow] 求解器=use_potion/EXPLOSIVE_AMPOULE -> 执行=use_potion{...}
                                 一致=是
            [sts2_combat_follow] 求解器=play_card/STRIKE_IRONCLAD -> 执行=heuristic{...}
                                 一致=否(手牌里没有这张卡)

        `matched=False` 时**必须**带 `fallback_reason` —— 静默降级是这次排查里
        最难发现的坑（`if effective_card_index is not None` 分支没有 else 日志）。

        ★ t39：新增 `snapshot` / `classification`（均可选）两个入参，仅用于推导
          **战斗身份**（见 `_battle_identity`），使每场战斗的留痕预算独立。
          它们**不影响**上面这个被消费的输出格式。
        """
        names = []
        if isinstance(hand, list):
            for card in hand:
                if isinstance(card, dict):
                    names.append(str(card.get("name") or card.get("card_id") or "?"))
        hand_key = ",".join(names[:9])
        # ★ t44：把 `turn` 与 `run` 追加到行尾，使**跨 mod 按回合对齐**成为可能。
        #
        #   为什么必须加（实测，2026-10-20）：做「插件实打 vs 上游 CombatSolver 路线」对照时，
        #   上游一条路线覆盖 turn T..T+8（35~46 个动作），而它在 turn T 开始时搜索、耗时中位
        #   2.5 秒，我们出牌间隔中位 1.0 秒 ⇒ 搜索期间我们已打掉 turn T 的前 1~3 张。
        #   只有 turn T 的开头被污染，**turn T+1 之后完全可比**
        #   ⇒ 正确的对齐单位是**回合**，不是整条路线。
        #   但本行原本不带回合信息，只能靠 `[sts2_combat_turn_*]` 的 `turn_key` 反推，
        #   而 `turn_key` 主要来自点评/推送路径，纯 autoplay 下会漏 ⇒ 实测出现
        #   「turn 4 我们 6 张 = 上游 3 张逐字重复两遍」这种"相邻两回合被合并"的结果。
        #   ★ 更早的教训（`loop_runner.py:862` 已记录）：「turn_key 跨 run 会撞值
        #     （实测 132/175=75%，0:2:combat:1 出现在 20 个 run）」
        #     ⇒ 除了 turn **必须同时带 run**，否则同名回合跨局合并（我实测踩了两次：
        #       先只用 turn → "turn 2 出 207 张"；再加 (act,floor,turn) → "turn 2 出 24 张"）。
        #
        #   ★ 兼容性：新字段追加在 `手牌=%s` **之后**，既有解析器的正则
        #     （锚 `一致=是|否` 与 `手牌=[...]`）逐字不受影响。
        #   取值沿用 `loop_runner.py:1112-1118` 的既有惯例，不另造路径。
        raw_state = (
            snapshot.get("raw_state")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict)
            else {}
        )
        run_obj = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        turn_val = raw_state.get("turn")
        if turn_val is None:
            combat_obj = (
                raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            )
            turn_val = combat_obj.get("turn")
        run_val = (
            raw_state.get("run_id")
            or run_obj.get("run_id")
            or run_obj.get("runId")
            or run_obj.get("id")
            or ""
        )
        # ★ 去重键 = 求解器建议 + 实际执行 + 手牌 + 原因。
        #   见 `_combat_log` 上方关于「限流器永久静默」的说明：这里按**内容**去重，
        #   于是同一局面反复重试（实测同一动作重复 8~20 次）只留一行，
        #   而局面一变就会立刻出新行 —— 长局也能看全，不再有"打满就瞎"。
        #   ★ t44 刻意**不**把 turn/run 加进去：那会改变限流密度（每回合强制出新行），
        #     而本次目标只是"能读出回合"，不是改留痕密度。
        self._combat_log(
            "[sts2_combat_follow] 求解器=%s/%s(target=%s) -> 执行=%s%s 一致=%s%s 手牌=%s turn=%s run=%s",
            solver_kind,
            solver_card_id,
            solver_target,
            executed_action,
            ("(" + ",".join(f"{k}={v}" for k, v in executed_kwargs.items()) + ")")
            if executed_kwargs
            else "",
            "是" if matched else "否",
            ("  原因=" + fallback_reason) if fallback_reason else "",
            names[:9],
            turn_val if turn_val is not None else "?",
            run_val if run_val else "?",
            dedup_key="|".join(
                (
                    str(solver_kind),
                    str(solver_card_id),
                    str(solver_target),
                    str(executed_action),
                    ",".join(f"{k}={v}" for k, v in sorted(executed_kwargs.items())),
                    "1" if matched else "0",
                    str(fallback_reason),
                    hand_key,
                )
            ),
            # ★ t39：传战斗身份，使每场战斗的留痕预算独立（否则又变成永不重置）。
            battle_identity=self._battle_identity(snapshot, classification),
        )

    def _log_combat_heuristic(
        self,
        reason: str,
        hand: list[Any] | None = None,
        *,
        snapshot: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
    ) -> None:
        """求解器**没参与**（或没匹配上）而走启发式时的留痕。"""
        names = []
        if isinstance(hand, list):
            for card in hand:
                if isinstance(card, dict):
                    names.append(str(card.get("name") or card.get("card_id") or "?"))
        self._combat_log(
            "[sts2_combat_fallback] 走启发式 原因=%s 手牌=%s",
            reason,
            names[:9],
            dedup_key="fallback|" + str(reason) + "|" + ",".join(names[:9]),
            # ★ t39：同上，传战斗身份。
            battle_identity=self._battle_identity(snapshot, classification),
        )

    def _log_map_route(self, scored: list[tuple[int, float]], chosen: int, *, hp_ratio: float, gold: int) -> None:
        """把这一 tick 的路线评估结果用 info 级记一次（同一局面只记一次，避免刷屏）。

        为什么要单独写：`_debug` 在本文件里是个**空函数**，所有 [sts2_*] 调试行其实都没输出，
        线上没法核对"到底按什么选的"。选路是玩家最容易质疑的决策，必须留痕。
        """
        if self._logger is None:
            return
        try:
            key = "|".join(f"{index}:{score:.2f}" for index, score in scored)
            if key == self._last_map_route_log_key:
                return
            self._last_map_route_log_key = key
            self._logger.info(
                "[sts2_map_route] 金币=%s 血比=%.2f 候选评分=%s -> 选 option_index=%s",
                gold,
                hp_ratio,
                ", ".join(f"{index}={score:.2f}" for index, score in scored),
                chosen,
            )
        except Exception:
            pass

    @staticmethod
    def _available_actions_signature(available_actions: list[dict[str, Any]]) -> str:
        return ",".join(sorted(str(action.get("type") or "") for action in available_actions if isinstance(action, dict)))

    def _next_capstone_option(self, available_actions: list[dict[str, Any]]) -> int | None:
        """capstone 选项游标：逐项往前，扫过 _MAX_CAPSTONE_SWEEP 项后返回 None（等界面自己变）。"""
        signature = self._available_actions_signature(available_actions)
        if signature != self._capstone_signature:
            self._capstone_signature = signature
            self._capstone_last_option = -1
        option = self._capstone_last_option + 1
        if option >= self._MAX_CAPSTONE_SWEEP:
            return None
        self._capstone_last_option = option
        return option

    # ---- 地图跟随玩家投票 ----------------------------------------------------------------

    def _other_player_vote_index(self, summary_context: dict[str, Any]) -> int | None:
        """别人已投节点位置索引（没人投 / 只有自己投 -> None）。summary_context_builder 已算好。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        index = payload.get("other_player_vote_index")
        if isinstance(index, int) and index >= 0:
            return index
        return None

    @staticmethod
    def _is_multiplayer_map(summary_context: dict[str, Any]) -> bool:
        """是否多人局。单机地图不该等玩家投票（等也等不到，只会拖慢自动玩）。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return bool(payload.get("is_multiplayer"))

    def _map_wait_pending(self, summary_context: dict[str, Any]) -> bool:
        """没人投票时是否还要继续等玩家。

        以地图身份（层级 + 当前节点 + 候选节点数）为键：换图/换层就重新开始计时，不会把上一张图的
        等待时间带过来。等满 _map_vote_wait_seconds 后返回 False，让插件自己选，避免整局卡死。
        """
        if self._map_vote_wait_seconds <= 0:
            return False
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("available_nodes") if isinstance(payload.get("available_nodes"), list) else []
        current = payload.get("current_node") if isinstance(payload.get("current_node"), dict) else {}
        key = f"{payload.get('floor')}|{current.get('row')},{current.get('col')}|{len(nodes)}"
        now = monotonic()
        if key != self._map_wait_key or self._map_wait_started_at <= 0:
            self._map_wait_key = key
            self._map_wait_started_at = now
            return True
        return (now - self._map_wait_started_at) < self._map_vote_wait_seconds

    # ---- 对话式地图协作（sts2_set_route）--------------------------------------------------

    @staticmethod
    def _map_nodes(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        """候选节点列表。所谓"位置"就是这个列表的下标，与 mod 侧 available_nodes 的顺序一致。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes")
        if not isinstance(nodes, list) or not nodes:
            nodes = payload.get("available_nodes")
        return [node for node in (nodes if isinstance(nodes, list) else []) if isinstance(node, dict)]

    @staticmethod
    def _map_option_index(nodes: list[dict[str, Any]], position: int) -> int:
        """位置 -> choose_map_node 的 option_index。mod 侧就是用这个下标取 available_nodes。"""
        node = nodes[position] if 0 <= position < len(nodes) and isinstance(nodes[position], dict) else {}
        raw = node.get("index")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else position

    @staticmethod
    def _map_node_type(node: dict[str, Any]) -> str:
        return str(node.get("type") or node.get("node_type") or "?").strip() or "?"

    def _map_decision(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> tuple[int, str] | None:
        """按 map_policy 决定这一 tick 要不要投票、投哪个；返回 None 表示什么都不做。

        wait 在多人局里永远返回 None：不投票、也不跟票，路线完全留给玩家。等玩家发话之后才动，
        那三种说法走 sts2_set_route（safe/mirror/pick），不经过这里。单机局没有队友可等，
        仍然自己选，否则每张图都会白等一轮。
        """
        multiplayer = self._is_multiplayer_map(summary_context)
        policy = self._map_policy
        if policy == MAP_POLICY_WAIT and multiplayer:
            return None
        if policy == MAP_POLICY_FOLLOW:
            follow_index = self._other_player_vote_index(summary_context)
            if follow_index is not None:
                return follow_index, "map_follow_player_vote"
            if multiplayer and self._map_wait_pending(summary_context):
                return None
        return self._map_own_pick(summary_context, preferences, snapshot)

    def _map_own_pick(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> tuple[int, str]:
        node_index = self._preferred_map_index(summary_context, preferences, snapshot)
        return (node_index if isinstance(node_index, int) else 0), "map_preference_or_default"

    def _safe_route_preferences(self, preferences: dict[str, Any] | None) -> dict[str, Any]:
        """"定个路线"用的路由权重：内置保守权重打底，玩家自己配过的权重覆盖其上。"""
        configured = self._route_policy(preferences or {})
        weights = dict(SAFE_MAP_WEIGHTS)
        user_weights = configured.get("weights") if isinstance(configured.get("weights"), dict) else {}
        for key, value in user_weights.items():
            try:
                weights[str(key).strip().lower()] = int(value)
            except (TypeError, ValueError):
                continue
        policy = {
            "instruction_type": "route_policy",
            "weights": weights,
            "conditions": {
                "low_hp": {
                    "hp_ratio_below": SAFE_MAP_HP_RATIO_BELOW,
                    "weights": dict(SAFE_MAP_LOW_HP_WEIGHTS),
                }
            },
            "lookahead_depth": configured.get("lookahead_depth") or 4,
        }
        return {"records": [{"value": policy}]}

    def resolve_route(
        self,
        summary_context: dict[str, Any],
        *,
        mode: str,
        pick: Any = None,
        index: Any = None,
        preferences: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把"定个路线 / 跟你一样 / 选中间"解析成一个具体的 choose_map_node 选项。

        message 是给猫娘直接说出口的中文；option_index 为 None 时调用方不应执行动作。
        """
        mode_text = str(mode or "").strip().lower()
        if mode_text not in {"safe", "mirror", "pick"}:
            mode_text = "safe"
        nodes = self._map_nodes(summary_context)
        catalog = [
            {
                "position": position + 1,
                "option_index": self._map_option_index(nodes, position),
                "node_type": self._map_node_type(node),
            }
            for position, node in enumerate(nodes)
        ]
        result: dict[str, Any] = {
            "ok": False,
            "mode": mode_text,
            "option_index": None,
            "node_type": None,
            "position": None,
            "node_count": len(nodes),
            "nodes": catalog,
            "message": "",
            "error": None,
        }
        if not nodes:
            result["error"] = "not_on_map"
            result["message"] = "现在不是选路线的界面，没办法投地图票。"
            return result

        position: int | None = None
        if mode_text == "mirror":
            mirror = self._other_player_vote_index(summary_context)
            if mirror is None:
                result["error"] = "no_player_vote"
                result["message"] = "你还没在地图上投票呢，我先等着；你选好之后说一声，我就跟你投同一个。"
                return result
            if not 0 <= mirror < len(nodes):
                result["error"] = "no_player_vote"
                result["message"] = "我读到的你的票不在当前候选里，等我刷新一下再说。"
                return result
            position = mirror
        elif mode_text == "pick":
            if isinstance(index, int) and not isinstance(index, bool):
                if 0 <= index < len(nodes):
                    position = index
                else:
                    result["error"] = "index_out_of_range"
                    result["message"] = f"下标 {index} 越界了，现在只有 {len(nodes)} 个可选节点。"
                    return result
            else:
                parsed = parse_map_pick(pick, len(nodes))
                if parsed is None:
                    result["error"] = "pick_unparsed"
                    result["message"] = (
                        f"我没听清要选哪个；说“中间”“第几个”“最左”“最右”都行，现在有 {len(nodes)} 个候选。"
                    )
                    return result
                position = parsed
        else:
            chosen = self._preferred_map_index(summary_context, self._safe_route_preferences(preferences), snapshot)
            if isinstance(chosen, int):
                position = chosen if 0 <= chosen < len(nodes) else None
                if position is None:
                    for position, item in enumerate(catalog):
                        if item["option_index"] == chosen:
                            break
                    else:
                        position = None

        if position is None:
            result["error"] = "no_safe_route"
            result["message"] = "这张图上我没找到更稳妥的节点，等你定我再跟着投。"
            return result

        node_type = self._map_node_type(nodes[position])
        result.update(
            ok=True,
            option_index=self._map_option_index(nodes, position),
            node_type=node_type,
            position=position + 1,
        )
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        if mode_text == "mirror":
            result["message"] = f"好，我跟你投第 {position + 1} 个（{node_type}）。"
        elif mode_text == "pick":
            result["message"] = f"好，我投第 {position + 1} 个（{node_type}）。"
        else:
            result["message"] = self._safe_route_message(payload, position, node_type)
        return result

    def _safe_route_message(self, payload: dict[str, Any], position: int, node_type: str) -> str:
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        if hp_ratio and hp_ratio < SAFE_MAP_HP_RATIO_BELOW:
            return f"我投第 {position + 1} 个（{node_type}）：血量只剩 {int(hp_ratio * 100)}%，先挑稳一点的。"
        return f"我投第 {position + 1} 个（{node_type}）：这条看着最稳妥。"

    def plan(self, context: dict[str, Any]) -> PlannedOperation | None:
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        strategy_context = context.get("strategy_context") if isinstance(context.get("strategy_context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        state_name = str(classification.get("state_name") or snapshot.get("screen") or "").strip().lower()
        available_actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        preferences = strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}

        if state_name == "main_menu":
            # 主菜单优先级：**先「继续」**。原来 open_character_select 排在前面，而主菜单只要单人按钮可用
            # 就会广告 open_character_select（GameStateService.CanOpenCharacterSelect），于是插件会去点
            # 「单人/新建局」，抢在存档前面 —— 这正是「和 AI 组队中途退出后回不到存档」的成因。
            # 只有在没有可继续存档时才允许开新局/看时间线。
            action = (
                self._find_action(available_actions, "continue_run")
                or self._find_action(available_actions, "open_character_select")
                or self._find_action(available_actions, "open_timeline")
            )
            if action is None:
                # 子菜单压着主菜单时「继续」按钮会被藏起来（CanContinueRun 要求 SubmenusOpen == false），
                # 先收干净子菜单，下一 tick 就能看到 continue_run。
                action = self._find_action(available_actions, "close_main_menu_submenu")
                if action is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="main_menu_close_submenu")
                action = self._find_action(available_actions, "choose_timeline_epoch")
            if action is not None:
                kwargs = self._main_menu_kwargs(action, summary_context)
                reason = "main_menu_continue_run" if str(action.get("type") or "").lower() == "continue_run" else "main_menu_default"
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.95, source="heuristic", reason=reason)

        if state_name == "multiplayer_lobby":
            # co-op 大厅：选角 -> ready。猫娘侧由 mod 的 NekoAutoplayDriver 驱动，这里是插件驱动的那一份。
            action = self._find_action(available_actions, "ready_multiplayer_lobby")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="multiplayer_lobby_ready")
            action = self._find_action(available_actions, "select_character")
            if action is not None:
                selected_index = self._selected_character_index(summary_context)
                kwargs = {"option_index": selected_index} if selected_index is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.85, source="heuristic", reason="multiplayer_lobby_select_character")
            action = self._find_action(available_actions, "join_multiplayer_lobby")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="multiplayer_lobby_join")

        if state_name == "character_select":
            action = self._find_action(available_actions, "select_character")
            embark_action = self._find_action(available_actions, "embark")
            # 玩家口径：**每局角色随机**。用游戏自带的「随机」候选（is_random），而不是插件自己摇 ——
            # 那样连"随机"这个语义都交给游戏，公告时再读它实际抽到谁（见 service._maybe_announce_character）。
            #
            # 老毛病提醒：`_selected_character_index` 取的是"当前已选中"的角色下标，所以再发一次
            # select_character(那个下标) 是 no-op —— 屏幕不推进、下一 tick 又发，无限重按。
            # 这里靠"按过一次就不再按"（记账由执行推动，见 note_character_select_dispatched）保证推进。
            signature = self._character_list_signature(summary_context)
            if signature != self._character_pick_key:
                self._character_pick_key = signature
                self._character_pick_done = False
            random_index = self._random_character_index(summary_context)
            already_random = self._selected_character_is_random(summary_context)
            if action is not None and random_index is not None and not already_random and not self._character_pick_done:
                self._log_character_choice("random", random_index)
                return PlannedOperation(
                    action_type=action["type"],
                    kwargs={"option_index": random_index},
                    confidence=0.9,
                    source="heuristic",
                    reason="character_select_random",
                )
            if embark_action is not None:
                # 角色已定（随机项或上一次玩的）就该开局。
                self._log_character_choice("embark", None)
                return PlannedOperation(action_type=embark_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="character_select_embark")
            if action is not None:
                selected_index = self._selected_character_index(summary_context)
                kwargs = {"option_index": selected_index} if selected_index is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.9, source="heuristic", reason="character_select_default")

        if state_name == "event":
            action = self._find_action(available_actions, "choose_event_option")
            if action is not None:
                # 事件房是纯启发式的：插件不再有任何 LLM 决策（2026-10 摘除 EventAdviceGenerator）。
                preferred_option = self._preferred_event_option(summary_context, preferences, strategy_context)
                self._debug(
                    "[sts2_event_plan] options=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("event_options") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = {"option_index": preferred_option if preferred_option is not None else 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.8, source="heuristic", reason="event_preference_or_default")

        if state_name == "map":
            action = self._find_action(available_actions, "choose_map_node")
            if action is not None:
                decision = self._map_decision(summary_context, preferences, snapshot)
                if decision is None:
                    # wait 策略下的多人局：这一 tick 什么都不做，把定路线的权力完全留给玩家。
                    # 自动玩循环会在下一 tick 重来，直到玩家通过 sts2_set_route 下令。
                    self._debug("[sts2_map_plan] policy=%s waiting for the player's instruction", self._map_policy)
                    return None
                node_index, reason = decision
                self._debug(
                    "[sts2_map_plan] policy=%s nodes=%s chosen=%s reason=%s payload=%s",
                    self._map_policy,
                    summary_context.get("payload", {}).get("travelable_nodes") if isinstance(summary_context.get("payload"), dict) else [],
                    node_index,
                    reason,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                return PlannedOperation(
                    action_type=action["type"],
                    kwargs={"option_index": node_index},
                    confidence=0.95 if reason == "map_follow_player_vote" else 0.82,
                    source="heuristic",
                    reason=reason,
                )

        if state_name in {"reward", "card_selection_reward", "card_selection"}:
            # 两段是**分开**的，动作清单天然区分：
            #   领奖励段：claim_reward 在册（另有 resolve_rewards / collect_rewards_and_proceed）
            #   选卡段  ：领出一张卡奖励后 claim_reward 消失，换成 choose_reward_card / skip_reward_cards
            card_action = self._find_action(available_actions, "choose_reward_card")
            reward_candidates = self._reward_candidates(summary_context)
            if card_action is not None and reward_candidates:
                # 选卡段：`_preferred_reward_option` 对「全候选都不值得拿」会返回 None
                # （见 :1548 `if best_candidate[0] < 0`——最佳卡得分为正才选）。
                # ★旧代码拿到 None 仍照选：`_reward_kwargs(..., None)` 退回"第一个选项"
                #   ⇒ 无条件选牌 ⇒ 诅咒卡/垃圾卡被收进卡组，卡组质量单调下降。
                #   这也是「变化卡事件会选到诅咒」的直接机制。
                # ⇒ 现在改为：None 表示"一张都不该拿"，优先跳牌（skip_reward_cards），
                #   没有跳牌动作时才退回原行为（保持向前兼容，不改变无 skip 动作的场景）。
                preferred_option = self._preferred_reward_option(summary_context, preferences, strategy_context)
                if preferred_option is None:
                    skip_action = self._find_action(available_actions, "skip_reward_cards")
                    if skip_action is not None:
                        self._debug("[sts2_reward_skip] 全候选不值得拿 -> 跳牌（避免污染卡组）")
                        self._log_reward_route(
                            self._last_reward_scored,
                            None,
                            skipped=True,
                            archetype_tags=self._last_reward_archetype_tags,
                            bias=self._last_reward_bias,
                            lesson_signals=self._last_reward_lesson_signals,
                        )
                        return PlannedOperation(
                            action_type=skip_action["type"],
                            kwargs={},
                            confidence=0.7,
                            source="heuristic",
                            reason="reward_skip_low_value",
                        )
                self._log_reward_route(
                    self._last_reward_scored,
                    preferred_option,
                    skipped=False,
                    archetype_tags=self._last_reward_archetype_tags,
                    bias=self._last_reward_bias,
                    lesson_signals=self._last_reward_lesson_signals,
                )
                kwargs = self._reward_kwargs(summary_context, card_action, preferred_option)
                return PlannedOperation(action_type=card_action["type"], kwargs=kwargs, confidence=0.72, source="heuristic", reason="reward_pick_card")
            claim_action = self._find_action(available_actions, "claim_reward")
            if claim_action is not None:
                # 逐个领；**药水排最后**，且"已经真的发过的下标"不再重发。
                # 药水栏满时领药水发不出去，而 mod 照样回 completed -> 奖励清单不变 ->
                # 旧代码每 tick 重发同一个 claim_reward(0)（正好是药水），无限重领（实测卡 5 分钟以上）。
                claim_index = self._next_reward_claim_index(summary_context)
                if claim_index is None and not self._reward_claimable_list(summary_context):
                    # payload 里没有 mod 的 rewards[] 形状（旧口径 / 别的来源）：退回原来的偏好下标，
                    # 不要因为"读不到新形状"就把动作整个吞掉。
                    preferred_option = self._preferred_reward_option(summary_context, preferences, strategy_context)
                    claim_index = preferred_option if preferred_option is not None else 0
                if claim_index is not None:
                    return PlannedOperation(
                        action_type=claim_action["type"],
                        kwargs={"option_index": claim_index},
                        confidence=0.72,
                        source="heuristic",
                        reason="reward_claim",
                    )
            # 奖励领完 / 都领不动了 -> 推进到下一屏。
            proceed = (self._find_action(available_actions, "collect_rewards_and_proceed")
                       or self._find_action(available_actions, "resolve_rewards")
                       or self._find_action(available_actions, "skip_reward_cards")
                       or self._find_action(available_actions, "proceed"))
            if proceed is not None:
                return PlannedOperation(action_type=proceed["type"], kwargs={}, confidence=0.85, source="heuristic", reason="reward_proceed_to_map")

        # plain "card_selection" 覆盖战斗内选牌（如「坚毅」烧牌 / combat_hand_select）与菜单卡牌选择；
        # reward 类走上面的 reward 分支（那里找 choose_reward_card/claim_reward，这里没有才落到 select_deck_card）。
        if state_name in {"card_selection", "card_selection_unusefull", "card_selection_delet", "card_selection_transform", "card_selection_remove"}:
            # 1) 能确认就先确认。老代码把 select_deck_card 排在 confirm_selection 前面，于是永远不确认；
            #    需要选 2 张的界面（min_select=2）在没选够时 confirm 不可用，就会一直重按同一张卡而卡死。
            confirm_action = self._find_action(available_actions, "confirm_selection")
            if confirm_action is not None and self._selection_can_confirm(summary_context):
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.95, source="heuristic", reason="selection_confirm")
            action = self._find_action(available_actions, "select_deck_card")
            if action is not None:
                preferred_option = self._next_selection_option(summary_context, preferences, context)
                self._debug(
                    "[sts2_remove_plan] cards=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                # 这一屏的候选都按过了就不再发 select_deck_card（发一个没有 option_index 的选牌动作只会
                # 让动作引擎去猜/报错），落到下面的确认或离开分支。
                if preferred_option is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={"option_index": preferred_option}, confidence=0.7, source="heuristic", reason="remove_preference_or_default")
            # 选牌屏没有可用的选牌动作时，先尝试确认，其次离开（关闭牌视图 / 继续）。
            if confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="selection_confirm")
            for fallback in ("close_cards_view", "proceed"):
                fallback_action = self._find_action(available_actions, fallback)
                if fallback_action is not None:
                    return PlannedOperation(action_type=fallback_action["type"], kwargs={}, confidence=0.8, source="heuristic", reason=f"selection_{fallback}")

        # 商店：**统一评分池**（规格 docs/shop_scoring.md）。卡 / 遗物 / 药水 / 删牌进同一个池子比总分，
        # 取最高分；全部低于门槛就一分不花，把钱留到下一个商店。
        #
        # 玩家口径（2026-10-07）：「商店好像很喜欢买遗物？这里最好评估一下那些是真正需要的。
        # 如果没有需要的就留到下一个商店」「买评分最高的，都不高就不买」。老实现是**死板的类别优先**
        # （遗物 → 高价值卡 → 药水 → 删牌 → 其他卡），遗物只要买得起就买、reason 却写着
        # `shop_high_value_relic` —— 这就是"商店很喜欢买遗物"的根因，也把买药冲精英的钱先花掉了。
        if state_name in {"shop", "shop_show"}:
            # 一次进店只开一次货架。open_shop_inventory 是**开关**：开 -> 没值得买的就关 ->
            # 下一 tick 又看见 open_shop_inventory 就又开，于是 open/close 无限来回（玩家报的"商店卡住"）。
            # "已经看过货"用 close_shop_inventory 是否在册判定——它只在货架**开着**时出现。
            visit_key = self._shop_visit_signature(summary_context)
            if visit_key != self._shop_visit_key:
                self._shop_visit_key = visit_key
                self._shop_viewed = False
            if self._find_action(available_actions, "close_shop_inventory") is not None:
                self._shop_viewed = True
            if not self._shop_viewed:
                open_action = self._find_action(available_actions, "open_shop_inventory")
                if open_action is not None:
                    return PlannedOperation(action_type="open_shop_inventory", kwargs={}, confidence=0.72, source="heuristic", reason="shop_open_inventory")

            decision = self._shop_preferred_purchase(summary_context, preferences, snapshot, available_actions)
            if decision is not None and decision.get("action_type"):
                return PlannedOperation(
                    action_type=str(decision["action_type"]),
                    kwargs=dict(decision.get("kwargs") or {}),
                    confidence=float(decision.get("confidence") or 0.7),
                    source="heuristic",
                    reason="shop_scored_buy",
                )

            # 一分不花：日志里已经写了"最高分 vs 门槛"，这里把 reason 也带上，方便事后查。
            reason = str((decision or {}).get("reason") or "shop_close")
            if self._find_action(available_actions, "close_shop_inventory"):
                return PlannedOperation(action_type="close_shop_inventory", kwargs={}, confidence=0.8, source="heuristic", reason=reason)
            if self._find_action(available_actions, "proceed"):
                return PlannedOperation(
                    action_type="proceed",
                    kwargs={},
                    confidence=0.8,
                    source="heuristic",
                    reason=reason if reason == "shop_score_below_threshold" else "shop_done",
                )
            return None

        if state_name == "rest":
            action = self._find_action(available_actions, "choose_rest_option")
            if action is not None:
                preferred_option = self._preferred_rest_option(summary_context, preferences)
                if preferred_option is not None:
                    self._log_rest_choice(summary_context, preferred_option)
                    return PlannedOperation(
                        action_type=action["type"],
                        kwargs={"option_index": preferred_option},
                        confidence=0.78,
                        source="heuristic",
                        reason="rest_option_preference",
                    )
                kwargs = {"option_index": 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.74, source="heuristic", reason="rest_preference_or_default")
            # 休息点无可挑选项（rest.options 空 / 已休息）-> 直接 proceed 离开，继续到地图。
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type="proceed", kwargs={}, confidence=0.8, source="heuristic", reason="rest_proceed")

        if state_name == "chest":
            # 宝箱是**两步**：mod 的流程是 open_chest -> choose_treasure_relic -> proceed。
            # 开箱**前**只有 open_chest 在册（choose_treasure_relic / proceed 都不在册，
            # chest.relic_options 还是空的），而旧分支只找后两个 —— 规划不出任何动作，
            # 猫娘永久停在宝箱屏（实测 floor 10 卡了 6 分钟以上，step_count 一直是 0）。
            # 顺序：能领遗物就先领（避免"已开箱但没领"再按开箱键），其次开箱，最后离开。
            relic_action = self._find_action(available_actions, "choose_treasure_relic")
            if relic_action is not None:
                return PlannedOperation(action_type="choose_treasure_relic", kwargs={"option_index": 0}, confidence=0.85, source="heuristic", reason="chest_claim_relic")
            open_action = self._find_action(available_actions, "open_chest")
            if open_action is not None:
                return PlannedOperation(action_type=open_action["type"], kwargs={}, confidence=0.85, source="heuristic", reason="chest_open")
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="chest_default")

        if state_name == "bundle_selection":
            # 先确认再重选：choose_bundle 选中后仍然留在 available_actions 里，老顺序会不停重选 bundle 0
            # （每次阻塞到界面关闭为止），confirm_bundle 永远够不到。
            confirm_action = self._find_action(available_actions, "confirm_bundle")
            choose_action = self._find_action(available_actions, "choose_bundle")
            signature = self._available_actions_signature(available_actions)
            if signature != self._bundle_signature:
                self._bundle_signature = signature
                self._bundle_chosen = False
            if self._bundle_chosen and confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.95, source="heuristic", reason="bundle_confirm")
            if choose_action is not None:
                self._bundle_chosen = True
                return PlannedOperation(action_type=choose_action["type"], kwargs={"option_index": 0}, confidence=0.8, source="heuristic", reason="bundle_choose")
            if confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="bundle_confirm")
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.75, source="heuristic", reason="bundle_proceed")

        if state_name == "capstone_selection":
            # 逐项往前扫，绝不重复按同一个按钮：index 0 可能是返回/关闭，一直按它永远离不开这个栈。
            action = self._find_action(available_actions, "choose_capstone_option")
            if action is not None:
                option = self._next_capstone_option(available_actions)
                if option is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={"option_index": option}, confidence=0.7, source="heuristic", reason="capstone_option")
            for fallback in ("proceed", "dismiss_modal", "close_main_menu_submenu"):
                fallback_action = self._find_action(available_actions, fallback)
                if fallback_action is not None:
                    return PlannedOperation(action_type=fallback_action["type"], kwargs={}, confidence=0.8, source="heuristic", reason=f"capstone_{fallback}")

        if state_name == "cards_view":
            action = self._find_action(available_actions, "close_cards_view") or self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.85, source="heuristic", reason="cards_view_close")

        if state_name == "modal":
            # 先 dismiss 再 confirm：confirm 可能落在"放弃本局"这类危险按钮上。
            action = self._find_action(available_actions, "dismiss_modal") or self._find_action(available_actions, "confirm_modal")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="modal_dismiss")

        if state_name in {"game_over", "victory", "defeat"}:
            action = self._find_action(available_actions, "return_to_main_menu")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="game_over_return_to_main_menu")

        if state_name == "combat":
            # ════════════════════════════════════════════════════════════════════
            # ★★ 战斗 = 纯求解器（PURE SOLVER）—— 冻结区，改前必读
            # ════════════════════════════════════════════════════════════════════
            #
            # 玩家指示（2026-10-18）：
            #   「战斗完全交给求解器。战斗以外的才需要启发式评分。」
            #   「求解器是穷举所有可能性，这一步启发完全没有意义，只需要按照路线走
            #     就一定能成功，启发了会导致更多变量导致失败，得不偿失」
            #
            # ⇒ 战斗内**不允许**启发式介入。理由是设计性的，不是性能性的：
            #   * 求解器已穷举所有可能性 ⇒ 启发式在一个已有最优解的空间里注入噪声；
            #   * 启发式不完美 ⇒ 它的介入**引入额外变量**，让结果不可归因；
            #   * 纯服从 ⇒ 可复现、可归因。
            #
            # 求解器无可用建议时的语义（玩家选定 **A**）：
            #   **丢弃缓存计划、立即重算求解器**，而不是降级启发式、也不是乱发动作。
            #   重算由 `loop_runner.tick` 在下一 tick 完成（它会重新取 /solver/plan）；
            #   本函数此时返回 None ⇒ 本 tick 不发任何动作 ⇒ **局面不变** ⇒ 下一 tick
            #   拿到新计划继续。这与"发一个错动作"有本质区别。
            #
            # ★ 死锁保险（为什么必须有）：
            #   若求解器对某局面**持续**给不出可执行建议（例如它一直建议一张打不出的牌），
            #   纯重算会表现为**永久卡住** —— 比启发式降级更糟。
            #   故：同一局面指纹连续重算 `_SOLVER_DEADLOCK_RETRIES` 次仍无解 ⇒
            #   **发 end_turn 打破僵局**并留痕。这是"不发动作"与"卡死"的分界线。
            #
            # ⚠️ 不要"顺手"把启发式加回来当兜底：那正是本次要移除的东西。
            #    战斗内的 `_plan_combat_turn_sequence` / `_preferred_combat_action` /
            #    `_combat_kwargs` 已删除（备份见 `.dsh-artifacts/deleted/`）。
            # ════════════════════════════════════════════════════════════════════
            solver_plan = context.get("solver_plan") if isinstance(context.get("solver_plan"), dict) else None
            # ★ 留痕要用到手牌快照（"执行了什么"要能对上具体牌）。
            _combat_hand = (
                snapshot.get("raw_state", {}).get("combat", {}).get("hand")
                if isinstance(snapshot.get("raw_state", {}).get("combat", {}).get("hand"), list)
                else []
            )
            # ★ 求解器为何没被采用（供统一留痕，避免"静默降级"）。
            solver_skip_reason = ""
            # ★ `first_step` 必须在**函数作用域**里先声明：求解器分支若未进入
            #   （无计划/未在战斗），留痕仍要引用它来记录"求解器本来建议什么"。
            first_step: dict[str, Any] | None = None
            if solver_plan is not None:
                first_step = _line_step_at(solver_plan, context.get("solver_step_index"))
            # ════════════════════════════════════════════════════════════════════
            # ★★★ t45：战斗出牌的**准入门槛（HARD GATE）** —— 必须排在**最前**。
            # ════════════════════════════════════════════════════════════════════
            #
            # 玩家指示（本次任务）：
            #   「严格等待求解器算出结果后才执行，禁止抢跑、禁止启发式兜底出牌。」
            #
            # 为什么位置必须在**这里**（而不是继续沿用下面那些事后判据）：
            #   缺陷形态是「只要 `line[0].steps[0]` 存在就出牌，出牌之后才发现计划过期，
            #   再进入补救流程」—— 那是**补偿**，不是准入。旧判据
            #   （`is_plan_absent` / `is_true_deadlock` / `is_signature_unchanged_since_fetch`）
            #   全都在 `_combat_solver_only_outcome` 里，**在出牌动作已经产生之后**才被求值
            #   ⇒ 对"这一次该不该出手"毫无约束力。
            #
            # 四条件（详见 `loop_runner.solver_play_gate` 的 docstring 与
            # `SOLVER_SEARCH_STATUSES` 上方那段"依据"长注释）：
            #   C1 `solver_plan` 是 dict 且 `in_combat is True`
            #   C2 `search_status` ∈ MapStatus 值域（= 搜索已算出结果）
            #   C3 `line` 非空且首步**在当前局面可执行**
            #   C4 这份计划确实基于当前局面（`plan_fetch_signature` / 整回合快照续行）
            #
            # ★ 不满足时本 tick 的语义（**玩家口径，逐字**）：
            #   「不发任何出牌动作、不发 end_turn、不落回启发式，只返回"继续等待"」。
            #   ⇒ 下面 `_combat_solver_gate_blocked` 在 `waiting_solver` 分支只写日志并 return None。
            #   ★ 唯一例外是 `solver_cannot_act`（求解器已给出**完整**结果、首步仍不可执行、
            #     且取回后指纹未变）—— 那才是真无解，交给既有上界（`_SOLVER_DEADLOCK_RETRIES`）
            #     在耗尽后发 end_turn 破局。没有这个例外，求解器对一个"建议了一张打不出的牌"
            #     的局面会永久静默 ⇒ 卡死，同样违反本次任务。
            # ★ 惰性导入（与 `_combat_solver_only_outcome` 里的同族处理一致）：
            #   `loop_runner` 侧也在模块级引用本模块（service 侧），用函数内导入把这个方向的
            #   依赖彻底推迟到"真的进入战斗分支"，万无一失地避免循环导入。
            from .loop_runner import GATE_ACT, solver_play_gate

            _gate = solver_play_gate(
                solver_plan=solver_plan,
                snapshot=snapshot,
                first_step=first_step,
                plan_origin=str(context.get("plan_origin") or "none"),
                plan_fetch_signature=str(context.get("plan_fetch_signature") or ""),
                current_signature=self._combat_deadlock_signature(snapshot),
            )
            if _gate["verdict"] != GATE_ACT:
                return self._combat_solver_gate_blocked(
                    gate=_gate,
                    snapshot=snapshot,
                    available_actions=available_actions,
                    solver_plan=solver_plan,
                    first_step=first_step,
                    hand=_combat_hand,
                    classification=classification,
                    context=context,
                )
            self._solver_gate_allowed_count += 1
            # ★ 闸门恢复（上一 tick 在等/判无解，这一 tick 放行）⇒ 打一行**带精确累计值**的收口行。
            #   为什么需要它：等待期的日志是节流的（同因每 N 次一行），单看那些行读不出"这一轮
            #   到底被拦了几次"。恢复行在**边界**上把三个计数逐字写出来 ⇒
            #   「被 gate 拦下的 tick 数 / 放行数」可精确核对，不靠数日志行。
            #   ★ 只在**状态跃迁**时打（上一 tick 是拦下的），否则每个放行 tick 都会重复一行。
            if self._solver_gate_last_verdict in {"waiting_solver", "solver_cannot_act"}:
                self._log_solver_gate_resume(gate=_gate)
            self._solver_gate_last_verdict = "act"
            self._log_solver_gate_act(gate=_gate)
            if not solver_plan or not solver_plan.get("in_combat"):
                solver_skip_reason = "无求解器计划（未在战斗/取计划失败）"
            if solver_plan and solver_plan.get("in_combat"):
                # 整回合缓存模式：按 solver_step_index 取当前回合第几步（循环逐个执行，不重查）。
                first_step = _line_step_at(solver_plan, context.get("solver_step_index"))
                solver_action = first_step.get("kind") if isinstance(first_step, dict) else None
                solver_card_index = first_step.get("card_index") if isinstance(first_step, dict) else None
                solver_card_id = first_step.get("card_id") if isinstance(first_step, dict) else None
                solver_target = first_step.get("target_index") if isinstance(first_step, dict) else None
                if not solver_action:
                    solver_skip_reason = "求解器计划的当前步为空（line 无 kind）"
                if solver_action == "play_card":
                    # 用 card_id 重新匹配实机手牌（mod 的 NekoAutoplayDriver 同样如此）：solver 给的是
                    # /solver/plan 捕获瞬间的位置卡号；捕获与执行之间手牌可能漂移，直接抽样会打在别的卡上。
                    effective_card_index, resolve_reason = self._resolve_solver_card_index(
                        snapshot, solver_card_id, solver_card_index
                    )
                    if effective_card_index is not None:
                        kw = {"card_index": effective_card_index}
                        if solver_target is not None:
                            kw["target_index"] = solver_target
                        self._combat_log_solver_vs_executed(
                            solver_kind=solver_action,
                            solver_card_id=solver_card_id,
                            solver_target=solver_target,
                            executed_action="play_card",
                            executed_kwargs=kw,
                            matched=True,
                            hand=_combat_hand,
                            # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                            snapshot=snapshot,
                            classification=classification,
                        )
                        return PlannedOperation(
                            action_type="play_card",
                            kwargs=kw,
                            confidence=0.9,
                            source="mod_solver",
                            reason="mod_combat_search",
                        )
                    # ★ 实机手牌里没有这张卡 ⇒ 计划已过期（漂移）。
                    #   纯求解器语义：**不发启发式动作**，丢弃本计划、让下一 tick 重算。
                    #   实测根因（`.dsh-artifacts/ROOTCAUSE-solver-adherence.md`）：
                    #   求解器搜索要 ~1 秒，期间上一步动作已让手牌变化 ⇒ 计划天然过期。
                    # ★ t43：改用 `_resolve_solver_card_index` 带回的**可区分原因**，
                    #   不再一律写成"手牌漂移"。两种成因（不在手牌 / 在手但 playable=false）
                    #   在统计与后续修法上完全不同，混在一起会让 1681 次重算无法归因。
                    solver_skip_reason = (
                        resolve_reason
                        or f"求解器建议 {solver_card_id} 但实机手牌里没有这张卡（手牌漂移）"
                    )
                if solver_action == "use_potion":
                    potion_index = self._solver_potion_option_index(snapshot, solver_card_id)
                    if potion_index is not None:
                        kw = {"option_index": potion_index}
                        if solver_target is not None:
                            kw["target_index"] = solver_target
                        self._combat_log_solver_vs_executed(
                            solver_kind=solver_action,
                            solver_card_id=solver_card_id,
                            solver_target=solver_target,
                            executed_action="use_potion",
                            executed_kwargs=kw,
                            matched=True,
                            hand=_combat_hand,
                            # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                            snapshot=snapshot,
                            classification=classification,
                        )
                        return PlannedOperation(
                            action_type="use_potion",
                            kwargs=kw,
                            confidence=0.9,
                            source="mod_solver",
                            reason="mod_solver_potion",
                        )
                    solver_skip_reason = f"求解器建议用药水 {solver_card_id} 但实机找不到对应药水"
                if solver_action == "end_turn":
                    self._combat_log_solver_vs_executed(
                        solver_kind=solver_action,
                        solver_card_id=solver_card_id,
                        solver_target=solver_target,
                        executed_action="end_turn",
                        executed_kwargs={},
                        matched=True,
                        hand=_combat_hand,
                        # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                        snapshot=snapshot,
                        classification=classification,
                    )
                    return PlannedOperation(
                        action_type="end_turn",
                        kwargs={},
                        confidence=0.9,
                        source="mod_solver",
                        reason="mod_solver_end_turn",
                    )
                if solver_action and not solver_skip_reason:
                    solver_skip_reason = f"求解器给了未知动作 kind={solver_action!r}"
            # ════════════════════════════════════════════════════════════════════
            # 求解器没给出可执行动作 ⇒ **不降级启发式**，交给下一 tick 重算（语义 A）。
            # 只保留死锁保险：同一局面连续无解 N 次就发 end_turn 打破僵局。
            # ════════════════════════════════════════════════════════════════════
            return self._combat_solver_only_outcome(
                snapshot=snapshot,
                available_actions=available_actions,
                solver_plan=solver_plan,
                first_step=first_step,
                solver_skip_reason=solver_skip_reason or "求解器未给出可执行首步",
                hand=_combat_hand,
                # ★ D1：本 tick 的计划究竟是"复用缓存"还是"刚重取" —— 由 loop_runner 的真实分支
                #   透传（见 loop_runner.py 的 `plan_origin`），**绝不在这里猜**。
                #   这是区分「计划过期（A，应重取不计死锁）」与「求解器真无解（B，应计数兜底）」
                #   的**唯一**客观事实（两者在局面上逐位不可区分，见 R1-D1-CONTRACT.md §0）。
                plan_origin=str(context.get("plan_origin") or "none"),
                # ★ t11：**取回计划那一刻**的局面指纹。用于识别"重取回来的计划已经漂移"
                #   （搜索 ~1s 期间手牌变了）⇒ 应归 A，而不是误判成 B 并推高死锁计数。
                plan_fetch_signature=str(context.get("plan_fetch_signature") or ""),
                # ★ t39：透传 classification，供留痕推导**战斗身份**（每场战斗独立预算）。
                classification=classification,
            )
        return None

    #: 同一局面连续无解多少次后，发 end_turn 打破僵局。
    #: 取值权衡：太小 ⇒ 求解器偶发超时就白扔回合；太大 ⇒ 玩家看到明显卡住。
    #: 3 次 × 每 tick ~1s ≈ 3 秒可见停顿，仍能自愈。
    #: ★ D1 修复后它**只**统计「求解器看着最新局面仍给不出可执行动作」（= B），
    #:   不再统计「计划过期（牌已不在手）」（= A）。见 `_combat_solver_only_outcome`。
    _SOLVER_DEADLOCK_RETRIES = 3
    # ★★ t13（D3）：非玩家行动窗口的**独立**计数上限。
    #   窗口没开时我们既不能发 end_turn（它不可用）也不该累加 N/3（那不是"求解器无解"），
    #   但必须有一个**可观测**的上限出口，否则窗口永不开时插件永久静默。
    #   达上限时记一条显式告警（含"局面疑似卡住"），并**回绕**计数 ⇒ 日志不会出现越界数字。
    _SOLVER_OUT_OF_WINDOW_LIMIT = 5

    #: ★ D1 新增：同一局面指纹下，「计划过期（漂移）⇒ 立即重取」连续发生多少次后，
    #: 就当**真死锁**处理（转入死锁计数，从而最终发 end_turn 脱离）。
    #:
    #: 为什么必须有这个上界（而不是无限重取）：
    #:   上游 `SolverController.cs:175-177` 用 `UnexpectedReplanCount` 把
    #:   `StateMismatch + DeploymentDrift` 上报为**缺陷信号** —— 即上游把「计划失效」
    #:   当 bug 证据，而不是正常运维事件。反复过期说明我们的计划快照与实机持续不一致，
    #:   若此时**只**重取、不设上界，一旦某局面每 tick 都过期就会永久卡住整局。
    #:
    #: 上界算术（可断言，与契约 §1.3 INV-3 / §5 的形式化陈述一致）：LIMIT=5、RETRIES=3 ⇒
    #:   第 1–5 次 A：不计数、不发动作；第 6/7/8 次 A：转入死锁计数 1/2/3
    #:   ⇒ **第 8 次发 end_turn**。⇒ 任一固定指纹下调用次数 ≤ 8，不可能无限重取。
    #:
    #: ★ 与契约 §1.2 伪代码的一处**有意偏离**（必须记录）：伪代码把过期计数在转入 B 时
    #:   **归零**（`count = 0`），但那样每次 A 只加 1 个 deadlock，而 deadlock 需 RETRIES
    #:   次才触发 end_turn ⇒ 实际要 `LIMIT + LIMIT × RETRIES = 20` 次才脱离，
    #:   **违反 §5 明确要求的「S ≤ 8」上界**。§1.2 与 §5 在这里不可兼得；
    #:   本实现取 §5 的**安全属性**（防无限重取卡死整局），令计数单调递增不归零。
    _SOLVER_STALE_RECLAIM_LIMIT = 5

    # ★★ D11（t28）：被锁事件选项的**哨兵分** —— 保证它排在任何未锁选项之后。
    #
    #   取 −1000 的**上界论证**（必须能断言，不能拍脑袋）：
    #     `_score_event_option` 内任一**单项**正向加分 ≤ +70（卡牌档最高），
    #     而各正向规则虽独立相加，其可命中集合有限（金币/遗物/卡牌/删牌/回血/加上限），
    #     实机观察到的最高合理总分为 **+50** 量级（遗物14+金币10+卡牌8+回血18）。
    #     ⇒ 1000 远大于任何可能总分的绝对值 ⇒ 被锁项恒为最低。
    #   ★ 且 `_normalize_percentile` 只做**相对**归一（把分数映射成 0–100 百分位），
    #     哨兵值不参与绝对量纲，故不影响未锁项之间的相对序。
    #   ★ 用哨兵而非 −60 的理由：若只给被锁项 −60，而某未锁项得 +50，
    #     则 50 > −60 ⇒ **仍会选被锁的** ⇒ 修不好（这正是实机 {0:10, 1:0} 的放大版）。
    _EVENT_LOCKED_OPTION_SCORE = -1000

    # ★★ D11（t28）：EVENT 屏「同一选项被拒且局面未变」连续多少次 ⇒ 写入本屏禁用集。
    #
    #   N 的取值依据（与 D9 同**口径**但更快，理由必须可核）：
    #     · D9 用 `_SOLVER_DEADLOCK_RETRIES = 3` —— 那是**战斗屏**：动作多、局面每回合大变，
    #       且"待确认"可能只是长动画 ⇒ 需要 3 次才敢断定无效。
    #     · EVENT 屏不同：一屏只有 2–3 个选项；一次**成功**选择会立刻改变局面；
    #       而**被锁**是游戏**逐字告知**的确定事实（`is_locked:true` / `"option is locked"`），
    #       不是概率性抖动 ⇒ "试探多次"没有信息价值。
    #   ⇒ N 取 **2**：既不像 N=1 那样可能被一次瞬时网络抖动误禁，
    #     也不像 N=3 那样在被锁项上多空烧一整轮（实机每轮 ≈ 2–8 秒）。
    #   ★ 但若**已拿到明确错误文本/字段**（`is_locked` 或 `option is locked` 或
    #     `details.option_index`），则**立即**禁用（不等 N 次）—— 见 service 侧实现。
    _EVENT_LOCKED_OPTION_RETRIES = 2

    def note_event_option_locked(self, summary_context: dict[str, Any], option_index: Any) -> None:
        """服务层在**游戏明确告知某选项被锁**之后调用：把该 index 记入**本屏禁用集**。

        ★★ D11（t28）的失效检测入口。两种触发（后者更精确，见 service 侧）：
          ① 准备阶段读到 `/state` 的 `option.is_locked == true`（**预防**）；
          ② 派发后被游戏拒：`code=invalid_target` + `message="...option is locked."`
             + `details.option_index=<n>`（**错误驱动的精确信号**）。

        ★ 为什么需要禁用集（而不是只靠打分层读 `is_locked`）：
          · 打分层读 `is_locked` 只在**该字段存在**时有效（旧 mod / 字段缺失就无效）；
          · 而**错误驱动**路径不依赖字段存在性 —— 游戏逐字告诉我们哪个 index 被锁。
          ⇒ 两条腿互为备份：字段缺失时仍有兜底。

        ★ 按屏隔离（见 `_event_locked_set_key`）：换屏 / 换事件自动清空，不跨局累积。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        key = self._event_locked_set_key(summary_context)
        if key != self._event_locked_set_key_cached:
            self._event_locked_set_cached = set()
            self._event_locked_set_key_cached = key
        if index not in self._event_locked_set_cached:
            self._event_locked_set_cached.add(index)
            if self._logger is not None:
                try:
                    self._logger.warning(
                        "[sts2_d11] 选项被判为已锁 ⇒ 加入本屏禁用集 index=%s（本屏禁用集=%s；"
                        "重算不会改变结论，将改选次优未锁项）"
                        % (index, sorted(self._event_locked_set_cached))
                    )
                except Exception:
                    pass

    def _event_locked_set_key(self, summary_context: dict[str, Any]) -> str:
        """本屏禁用集的隔离键：`screen|floor|event_id`（换其一即视为新屏 ⇒ 自动清空）。

        ★ 为什么必须隔离：禁用集若跨屏累积，会在后续**同一个 index 合法**的事件上
          错误地跳过它 ⇒ 变成新的误伤。用屏 + 事件 id 做键最短且足够。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        screen = str(payload.get("screen") or payload.get("_screen") or "")
        floor = str(payload.get("floor") or "")
        event_id = str(payload.get("event_id") or "")
        return "%s|%s|%s" % (screen, floor, event_id)

    #: ★★ t45：等待态日志的节流步长。
    #:   语义：`为什么在等` 的键（= verdict|missing|search_status|plan_origin）一变就立刻打一行；
    #:   同因连续等待则每 N 次打一行，行内带**累计**计数 ⇒「被闸门拦下的 tick 数」可逐字读出。
    #:   N 取 5：tick ≈ 1.5s ⇒ 同因等待时约每 7.5s 一行，既不刷屏也不会让日志出现死寂
    #:   （静默正是本项目历史上多个缺陷最难查的原因）。
    _SOLVER_GATE_LOG_EVERY = 5

    def _combat_solver_gate_blocked(
        self,
        *,
        gate: dict[str, Any],
        snapshot: dict[str, Any],
        available_actions: list[dict[str, Any]],
        solver_plan: dict[str, Any] | None,
        first_step: dict[str, Any] | None,
        hand: list[Any],
        classification: dict[str, Any],
        context: dict[str, Any],
    ) -> PlannedOperation | None:
        """战斗准入闸门**未通过**时的唯一出口（t45）。

        两种语义，**必须分开记账**（这就是本次任务第 3 条要求的"不许死锁但也不许把等待
        算成死锁"）：

        - `waiting_solver`：求解器**还没**给出可执行结果（未在出牌阶段 / search_status 缺失 /
          计划为空 / 局面正漂移）。⇒ 本 tick **不发任何动作（含 end_turn）、不落启发式**
          —— 玩家原话是「不发任何出牌动作、不发 end_turn、不发启发式，只返回继续等待」；
          并且**不碰** `_solver_stale_reclaim_count` / `_solver_deadlock_count`
          ⇒ **上界不会被等待消耗掉**。
          不死锁的保障来自另外两条，不是靠计数：
            ① 等待不产生任何状态变更 ⇒ 下一 tick 的局面与这一 tick 相同，重取一定会发生
               （`_reusable_plan` 判不过 ⇒ `plan_origin=refetched`）；
            ② 日志里带累计计数（可观测），且既有 `_maybe_fallback_refresh` 无进展看门狗
               与 SSE 业务静默重连仍在运行 —— "插件没动"与"mod 卡死"可区分。

        - `solver_cannot_act`：求解器**已给出完整结果**（search_status 合法）、首步在当前局面
          不可执行、且**取回后指纹未变** ⇒ 这才是真无解。
          ⇒ 直接交给既有上界机制 `_combat_solver_only_outcome`（判据同源，不另造）：
          计数到 `_SOLVER_DEADLOCK_RETRIES` 后发 `end_turn` 破局。
          ★ 为什么这里可以直接委托：`solver_play_gate` 判 cannot_act 的判据
            （refetched + 指纹未变 + 首步不可执行）与该方法里的 `is_true_deadlock`
            **逐条等价**，所以委托后它必然走 (B) 分支，不会误入 (A) 把真无解当漂移。
        """
        verdict = str(gate.get("verdict") or "")
        plan_origin = str(context.get("plan_origin") or "none")
        plan_fetch_signature = str(context.get("plan_fetch_signature") or "")
        self._solver_gate_last = dict(gate)
        self._solver_gate_last_verdict = verdict

        if verdict == "solver_cannot_act":
            self._solver_gate_cannot_act_count += 1
            self._log_solver_gate(gate=gate, plan_origin=plan_origin, force=True)
            return self._combat_solver_only_outcome(
                snapshot=snapshot,
                available_actions=available_actions,
                solver_plan=solver_plan,
                first_step=first_step,
                solver_skip_reason=("求解器已给出完整结果（search_status=%s）但首步不可执行：%s"
                                    % (gate.get("search_status") or "?", gate.get("exec_reason") or gate.get("why") or "")),
                hand=hand,
                plan_origin=plan_origin,
                plan_fetch_signature=plan_fetch_signature,
                classification=classification,
            )

        # ── waiting_solver：只等，不动作，不计死锁 ────────────────────────────────
        self._solver_gate_wait_count += 1
        self._log_solver_gate(gate=gate, plan_origin=plan_origin, force=False)
        return None

    def _log_solver_gate(self, *, gate: dict[str, Any], plan_origin: str, force: bool) -> None:
        """闸门留痕：一行说清「在等什么 / 三个计数各是多少 / 局面与计划是否对得上」。

        ★ 为什么必须留痕（与 t13/t23/t37 同一条纪律）：静默的等待与"插件卡死"在日志里
          无法区分 —— 这正是本项目历史上最贵的几类缺陷的共同形态。
        ★ 为什么带累计计数：本次验收要求报出「被 gate 拦下的 tick 数」，靠数日志行会漏
          （节流/去重/上限都会吃掉行）⇒ 直接把累计值写进行内，**数字自证**。
        """
        key = "|".join((
            str(gate.get("verdict") or ""),
            str(gate.get("missing") or ""),
            str(gate.get("search_status") or ""),
            plan_origin,
        ))
        self._solver_gate_blocked_since_log += 1
        if not force and key == self._solver_gate_log_key and (
            self._solver_gate_blocked_since_log % self._SOLVER_GATE_LOG_EVERY != 0
        ):
            return
        self._solver_gate_log_key = key
        if self._logger is None:
            return
        try:
            self._logger.info(
                "[sts2_solver_gate] verdict=%s 在等=%s plan_origin=%s in_combat=%s "
                "search_status=%s 指纹=%s 首步=%s 原因=%s "
                "｜累计 allowed=%d waiting=%d cannot_act=%d "
                "｜死锁计数 deadlock=%d/%d stale_reclaim=%d/%d（等待不计入）",
                gate.get("verdict"),
                gate.get("missing") or "-",
                plan_origin,
                gate.get("in_combat"),
                gate.get("search_status") or "-",
                gate.get("fingerprint_note") or "-",
                gate.get("step_kind") or "-",
                gate.get("why") or "-",
                self._solver_gate_allowed_count,
                self._solver_gate_wait_count,
                self._solver_gate_cannot_act_count,
                self._solver_deadlock_count,
                self._SOLVER_DEADLOCK_RETRIES,
                self._solver_stale_reclaim_count,
                self._SOLVER_STALE_RECLAIM_LIMIT,
            )
        except Exception:
            pass

    def _log_solver_gate_resume(self, *, gate: dict[str, Any]) -> None:
        """闸门**放行**时的收口行：把等待期累计的三个计数精确写出来（可逐字核对）。"""
        if self._logger is None:
            return
        try:
            self._logger.info(
                "[sts2_solver_gate] 恢复放行 verdict=%s search_status=%s 指纹=%s 首步=%s "
                "｜累计 allowed=%d waiting=%d cannot_act=%d "
                "｜死锁计数 deadlock=%d/%d stale_reclaim=%d/%d",
                gate.get("verdict"),
                gate.get("search_status") or "-",
                gate.get("fingerprint_note") or "-",
                gate.get("step_kind") or "-",
                self._solver_gate_allowed_count,
                self._solver_gate_wait_count,
                self._solver_gate_cannot_act_count,
                self._solver_deadlock_count,
                self._SOLVER_DEADLOCK_RETRIES,
                self._solver_stale_reclaim_count,
                self._SOLVER_STALE_RECLAIM_LIMIT,
            )
        except Exception:
            pass

    def _log_solver_gate_act(self, *, gate: dict[str, Any]) -> None:
        """**每一次放行**都留一行四条件证据（真机取证用，不节流）。

        ★ 为什么每次都要留（而不是只在"恢复"时留一行）：本次的核心验收是
          「每一次实际出牌之前，最近一次 /solver/plan 返回都满足 in_combat=True + 完整状态 +
          指纹一致」。要让这句话**可被日志逐条核对**，就必须让每次放行都带
          `in_combat / search_status / 指纹 / 首步` 四个字段，且紧挨在出牌行之前。
          出牌行本身（`[sts2_combat_follow] 一致=是` 与心跳的 `plan-return action_type=...`）
          保持格式不动 —— 既有解析器依赖那两个格式（t39/t44 的教训）。
        """
        if self._logger is None:
            return
        try:
            self._logger.info(
                "[sts2_solver_gate] verdict=act 四条件齐备 in_combat=%s search_status=%s "
                "指纹=%s 首步=%s ｜累计 allowed=%d waiting=%d cannot_act=%d",
                gate.get("in_combat"),
                gate.get("search_status") or "-",
                gate.get("fingerprint_note") or "-",
                gate.get("step_kind") or "-",
                self._solver_gate_allowed_count,
                self._solver_gate_wait_count,
                self._solver_gate_cannot_act_count,
            )
        except Exception:
            pass

    def _combat_solver_only_outcome(
        self,
        *,
        snapshot: dict[str, Any],
        available_actions: list[dict[str, Any]],
        solver_plan: dict[str, Any] | None,
        first_step: dict[str, Any] | None,
        solver_skip_reason: str,
        hand: list[Any],
        plan_origin: str = "none",
        plan_fetch_signature: str = "",
        classification: dict[str, Any] | None = None,
    ) -> PlannedOperation | None:
        """战斗内纯求解器的**唯一出口**：要么给出 end_turn 打破僵局，要么返回 None（本 tick 不发动作）。

        ★ 为什么需要这个函数而不是直接 return None：
          「求解器偶发给不出建议」和「求解器**持续**给不出建议」是两种病。
          前者重算就好；后者重算会**永久卡住**，比启发式降级更糟。
          用**局面指纹**区分二者：指纹不变 = 同一局面 = 卡住了。

        ★★ D1（本条修复的核心）：上面那段话里的「求解器给不出建议」其实是**两种**病，
          在修 D1 之前它们被混进同一个 `_solver_deadlock_count`：

          - **(A) 计划过期（漂移）**：求解器建议的牌**已不在手**。搜索要 ~1s，期间上一步
            动作已让手牌变了 ⇒ 计划天然过期。这是**运维常态**，不是求解器的错。
            上游等价动作是 `SolverController.cs:3190-3202` 的 `RequestSearch(DeploymentDrift)`
            —— **同回合重搜、不烧回合**。
          - **(B) 求解器真无解**：**看着最新局面重取回来**的计划，首步**仍然**做不了
            （或压根没给计划/首步）。这才是需要兜底打破僵局的情况。

          修 D1 前：A 也计数 ⇒ 3 次"正常漂移"就白烧一个回合（队长实测：10 次 end_turn
          **全部**由漂移触发）。修 D1 后：A 触发**立即重取且不计数**（有上界，见下），
          只有 B 才计数并在第 `_SOLVER_DEADLOCK_RETRIES` 次发 end_turn。

        ★ 判据 A/B 的唯一可观测差异 = `plan_origin`（由 `loop_runner.tick` 的真实分支透传，
          **不由本函数猜测**）：A 与 B 在**局面**上逐位不可区分（见 .dsh-artifacts/req/R1-D1-CONTRACT.md §0
          的只读探针实测：两者指纹相同、`_plan_step_is_executable` 返回相同、连续 3 次轨迹完全重合）。
          唯一客观事实是「这张失效的计划是不是本 tick 刚取回来的」：
          `refetched` + 首步仍不可执行 ⇒ B；`reused`（或未重取）⇒ A。

        ★ 局面指纹取材（`_combat_plan_signature` 同源，见 loop_runner）：
          手牌 card_id 多重集 + 能量 + 敌人血量。这三者任一变化都意味着
          "局面真的动了"，此时重算是有意义的，两个计数器都清零。

        ★ 为什么发 `end_turn` 而不是随便打一张：
          end_turn 是**唯一永远合法**的动作（不需要能量、不需要目标、不需要那张牌在手上），
          因此不会二次失败；且它让回合推进 ⇒ 下回合手牌重抽 ⇒ **必然**产生新局面
          ⇒ 彻底摆脱死锁。代价是浪费一个回合，但只在**真死锁**（B）时才付。
        """
        signature = self._combat_deadlock_signature(snapshot)
        if signature != self._solver_deadlock_signature:
            # ★★ t23 修复（D6/乙）：局面变了**不等于**在推进 —— 关键区别在这里。
            #
            # 缺陷（实机实测，313 条 `plan-call` 的运行时真实值）：
            #     stale   = {'0': 313}     ← ★ 313/313 恒为 0，**从未累加过**
            #     deadlock= {'0':170, '3':143}
            #     origin  = {'reused':175, 'refetched':131, 'none':7}
            #   ⇒ 破局 gate（`:1686` `_solver_deadlock_count >= 3`）**从未被求值**：
            #     `plan_origin=reused` ⇒ 恒走 (A) 分支 ⇒ 在 `:1609` 就 `return None` 了。
            #   ⇒ 配合下面 (A) 的改动，A 的有界计数才可能真正到达上界。
            #
            # 机制：本方法开头的"新局面 ⇒ 计数清零"用的是**局面指纹**，而指纹含
            #   手牌/能量/敌血（见 `_combat_deadlock_signature`）。那些值在**卡死**
            #   局面下也可能逐 tick 微动（例如 mod 回 pending 时状态回滚/重放），
            #   于是每 tick 都"看起来是新局面"⇒ `_solver_stale_reclaim_count`
            #   被反复清回 0 ⇒ (A) 的上界（`:1589`）**永远达不到**。
            #
            # 实测对照（.dsh-artifacts/_t23_counter_trace.log）：
            #   场景 A 指纹不变：stale 1..5 → deadlock 1,2 → 第 8 次 **end_turn**（正确）
            #   场景 B 指纹每 tick 变：stale 恒 1、deadlock 恒 0 ⇒ **永不脱困**（实机形态）
            #
            # ★ 修法：把"清零"与"推进"解耦 ——
            #   局面指纹变了只说明**手牌/血量的即时值**变了，不说明**死锁被解开了**。
            #   真正能证明"解开了"的是：**本 tick 求解器给出了可执行的首步**
            #   （即 `is_fresh_and_executable`）或**根本不是在本方法里判死锁**。
            #   ⇒ 因此 `_solver_stale_reclaim_count` / `_solver_deadlock_count` 的清除，
            #     改由**明确的"已恢复"信号**触发（见下方 `:1686` 之后与函数出口），
            #     而不是由指纹变化触发。
            #   ★ 保留 `_solver_out_of_window_count` 的指纹清零语义（它度量的是
            #     "窗口外连等多久"，换局面确实应重置）。
            #
            # 上游同构：`SolverController.cs:2805-2815` 的 `PlanExhausted` 重搜路径
            #   靠 `ContinuationSource`/重算次数记账，而**不是**靠"局面变了"清零；
            #   `CombatBugReportDescription.cs:113-115` 把
            #   `DeploymentDriftReplans` / `PlanExhaustedReplans` 作为**跨重试累计**的
            #   缺陷证据上报 —— 即上游也认为"反复重算"是**单调累计**的量。
            self._solver_deadlock_signature = signature
            # ★ t13：窗口外计数 —— 新局面下的"等了多久"不该背旧局面的数（语义不变）。
            self._solver_out_of_window_count = 0
            # ★★ t23：**不**在这里清 `_solver_deadlock_count` /
            #   `_solver_stale_reclaim_count`。它们表示"连续多少轮没能推进"，
            #   与"局面指纹动了没有"无关。清零点见 (A) 分支的脱困判定与下方恢复分支。

        # ── (1) 判 A 还是 B —— 本条修复**唯一**新增的判据 ────────────────────────────
        #     仅当「本 tick 刚取回的计划，其首步现在**仍然**做不了」时才归 B；
        #     计划为空/首步缺失同样归 B（重取后连计划都没有 = 求解器确实没给东西）。
        #     `_plan_step_is_executable` 是 loop_runner 里**同一个**判据（纯函数，无副作用），
        #     这里复用它是为了 A/B 与"快照该不该丢"依据同一套事实，不会互相打架。
        is_fresh_but_unexecutable = False
        if plan_origin == "refetched" and isinstance(first_step, dict):
            try:
                # 惰性导入：`loop_runner` 在**模块级**导入本模块的若干符号（service 侧），
                # 用函数内导入彻底避免"将来有一天"出现循环导入把插件启动打断。
                from .loop_runner import _plan_step_is_executable

                is_fresh_but_unexecutable = not _plan_step_is_executable(first_step, snapshot)[0]
            except Exception:
                # 判据本身不该抛；真抛了也不能因此把 A 误判成 B（会白烧回合）。
                is_fresh_but_unexecutable = False
        # ★★ t11 修复：`refetched` **还不够**判 B —— 还必须"取回时的局面指纹 == 当前指纹"。
        #
        # t5 复核发现、队长独立复核证实、我亦实机复现的误报：
        #   求解器搜索耗时约 1s，这期间手牌可能已经变了 ⇒ 重取回来的计划**首步就已漂移**。
        #   实机分类（t5 全量统计）：
        #     refetched + 手牌漂移 13 条 | refetched + 真无解（药水缺失） 2 条
        #   ⇒ 「refetched + 首步不可执行」**主要就是漂移**（13:2），判成 B 是误报，
        #     会让死锁计数涨到 3/3（实机 08:45:01 实测已达 3/3，只因那一 tick
        #     `end_turn_action is None` 才侥幸没烧回合）⇒ **下一次同指纹漂移就会白烧一整个回合**。
        #
        # 判据：`plan_fetch_signature` 由 loop_runner 在**取回计划那一瞬间**记下并透传。
        #   等 ⇒ 计划与"取回时的局面"仍然对得上，但首步还是做不了 ⇒ 真无解（B）。
        #   不等 ⇒ 取回后局面已变（漂移）⇒ 归 A、立即重取、**不计数**。
        #
        # ★ 保守方向：`plan_fetch_signature` 为空（调用方没传 / 非 loop_runner 调用点）
        #   时**视为"相同"**，即保留原有的 B 判定 —— 因为把真无解误判成 A 会导致
        #   无限重取、卡死整局（安全属性），比误报一次兜底更危险。
        current_signature = self._combat_deadlock_signature(snapshot)
        fetch_signature = str(plan_fetch_signature or "")
        is_signature_unchanged_since_fetch = (not fetch_signature) or (fetch_signature == current_signature)
        is_plan_absent = (solver_plan is None) or (first_step is None)
        # ★ 计划**刚刚重取、且首步确实可执行** —— 这不是"求解器给不出动作"，而是
        #   "重取回来的计划正好能用"。现实中它走不到这里（更早处会直接出牌），
        #   但判据必须保守：**只有"不可执行"才算过期/无解**，可执行绝不动任何计数。
        is_fresh_and_executable = (
            plan_origin == "refetched" and isinstance(first_step, dict) and not is_fresh_but_unexecutable
        )
        # 真无解 = 刚取回(B1) 且 取回后局面没变(B2) 且 首步仍不可执行；或 计划缺失。
        is_true_deadlock = (is_fresh_but_unexecutable and is_signature_unchanged_since_fetch) or is_plan_absent

        if not is_true_deadlock and not is_fresh_and_executable:
            # ── (A) 计划过期（漂移）：立即重取，**不消耗死锁计数** ──────────────────
            #   覆盖两种 A：
            #     A1 `plan_origin != "refetched"`（复用缓存/无来源）⇒ 旧计划，本就该重取；
            #     A2 `plan_origin == "refetched"` 但**取回后指纹已变** ⇒ t11 新增，
            #        修掉"重取回来的计划已漂移却被判 B"的误报。
            #   上界语义（契约 §1.2 伪代码 + §5 形式化陈述，两者必须同时成立）：
            #     第 1…LIMIT 次 A：不计数、不发动作；
            #     第 LIMIT+1 次起：本次**仍不发动作**，但转入死锁计数（+1）。
            #   ★ 关键实现细节：过期计数在转入 B 时**不归零**（单调递增）。
            #     契约 §1.2 的伪代码写的是 `if count >= LIMIT: count = 0; deadlock += 1`
            #     ——它把计数归零了；而 §5 的形式化陈述要求「任一固定指纹下调用次数
            #     S ≤ LIMIT + RETRIES = 8」。两者**不可兼得**：归零后每次 A 只加 1 个
            #     deadlock，而 deadlock 又需要 RETRIES 次才触发 end_turn ⇒ 实际要
            #     LIMIT + LIMIT × RETRIES 次才脱离（LIMIT=5 ⇒ 第 16 次），违反 §5 的
            #     「≤8」上界。**取 §5 的上界**（它是安全属性：防无限重取卡死整局），
            #     即让计数**单调**：第 6/7/8 次 A 依次把 deadlock 推到 1/2/3 ⇒ 第 8 次
            #     end_turn。§1.2 伪代码的"归零"按此修正，行为差异只体现在上界生效后。
            self._solver_stale_reclaim_count += 1
            # ★★ t23：破局 gate **不可被 (A) 的 `return None` 遮蔽**。
            #
            # 两种进入"应当贯穿"的情形：
            #   ① 过期次数越过上界（`:1624`）—— 「反复过期」本身已是异常信号；
            #   ② `_solver_deadlock_count` **已经**达到阈值 —— 这包含
            #      **t20 的交接**（service.py:1060-1061 把 deadlock 顶到 retries，
            #      期望"下一 tick planner 就发 end_turn"）与上一 tick 刚累加到阈值的情形。
            #      ★ 实机正是这一种：deadlock=3（143 条 plan-call 实测）、stale=0、
            #        origin=reused ⇒ 旧实现在此 `return None`，
            #        于是 t20 的交接**每次都被吞掉**、破局产出 0 次。
            reached_bound = self._solver_stale_reclaim_count >= self._SOLVER_STALE_RECLAIM_LIMIT + 1
            already_at_threshold = self._solver_deadlock_count >= self._SOLVER_DEADLOCK_RETRIES
            if reached_bound or already_at_threshold:
                # ★ 上界保护 / 交接兑现：转入（或维持）死锁计数 —— **本 tick 就允许破局落地**。
                #
                # ★★ t23 修复（D6/乙）：旧实现此处**仍然不发动作**（注释原文：
                #   "本 tick 仍不发动作 ⇒ 最迟第 LIMIT+RETRIES 次脱离"），
                #   于是要靠"下一次再走一遍 B 分支"才计数到 3。
                #   但实机恒 `plan_origin=reused` ⇒ 每 tick 都进 (A) 并在此 `return None`
                #   ⇒ `_solver_deadlock_count` 虽 +1，**下一 tick 走到 gate 前又先进 (A)**
                #   ⇒ 破局 gate 永远被 (A) 的 `return None` 遮蔽（实测 313/313）。
                #   ⇒ 既然已经判定"过期越过上界"或"计数已到阈值"= 明确的异常/交接信号，
                #     就**本 tick 立即**贯穿到 gate，不再 `return None`。
                #     这样上界语义（≤ LIMIT+RETRIES）才真的成立、
                #     且 t20 的破局交接才能兑现。
                if reached_bound:
                    self._solver_deadlock_count += 1
                self._combat_log_solver_vs_executed(
                    solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                    solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                    solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                    executed_action="(过期/交接转死锁判定)",
                    executed_kwargs={},
                    matched=False,
                    fallback_reason=(
                        f"计划连续过期 {self._solver_stale_reclaim_count} 次"
                        f"（上界 {self._SOLVER_STALE_RECLAIM_LIMIT}）"
                        f"/ 死锁计数 {self._solver_deadlock_count}"
                        f"（阈值 {self._SOLVER_DEADLOCK_RETRIES}）⇒ 转入死锁判定"
                        f"（plan_origin={plan_origin}；{solver_skip_reason}）"
                    ),
                    hand=hand,
                    # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                    snapshot=snapshot,
                    classification=classification,
                )
                # ★ 不 return：继续往下走窗口判定与破局 gate，
                #   使 `_solver_deadlock_count >= _SOLVER_DEADLOCK_RETRIES` 能真正生效。
            else:
                self._combat_log_solver_vs_executed(
                    solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                    solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                    solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                    executed_action="(不发动作，等重算)",
                    executed_kwargs={},
                    matched=False,
                    fallback_reason=(
                        f"计划过期（{solver_skip_reason}）⇒ 立即重取，不计死锁"
                        f"（第 {self._solver_stale_reclaim_count}/{self._SOLVER_STALE_RECLAIM_LIMIT} 次；"
                        f"plan_origin={plan_origin}；"
                        f"取回后指纹{'已变' if not is_signature_unchanged_since_fetch else '未变'}）"
                    ),
                    hand=hand,
                    # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                    snapshot=snapshot,
                    classification=classification,
                )
                return None
        elif is_true_deadlock:
            # ── (B) 求解器真无解：计数，达阈值才兜底 ────────────────────────────────
            self._solver_deadlock_count += 1
        else:
            # ── ★★ t23：**唯一的"已恢复"信号** —— `refetched` + 首步可执行。
            #   求解器看着最新局面给出了能照做的动作 ⇒ 死锁真的解开了 ⇒ 三个计数清零。
            #
            #   为什么清零点必须**搬到这里**（而不能继续挂在"局面指纹变了"上）：
            #     指纹含手牌/能量/敌血的**即时值**，卡死局面下它们仍可能逐 tick 微动
            #     ⇒ 每 tick 都误判成"新局面"⇒ 计数被反复清零 ⇒ A 的上界（`:1589`）
            #     永远达不到（实机 `stale` 313/313 恒 0、破局 gate 从未求值）。
            #   本条的判据是**语义性的**："求解器给出了可执行动作" = 真的能推进了。
            #
            #
            # ★★★ t37（D1 实机复现，2026-10-20 07:44–07:54，run=C6CM8CZRR9KQ）：
            #   上面这条判据本身**保留不动**（契约 T-F / INV-4 依赖它，见
            #   tests/test_solver_deadlock_contract.py:368 / :631）。
            #   t37 的真因**不在本文件**：实机每个 epicycle 都是
            #     `refetched(可执行)` → `reused(漂移)`；
            #   而让"同一份计划在同一 tick 内被判出相反结论"的是 loop_runner 的
            #   **两条 planner 调用路径**（candidate_generator 与 planning_context）
            #   读同一份 solver_plan 却用了不同口径 —— 修在 loop_runner.py。
            #   详见 .dsh-artifacts/T37-D1-ROOTCAUSE.md。
            #
            # ★★★ t37 契约第 3 条：「三个计数的语义在日志里互不掩盖」——
            #   本分支是**唯一**会把 stale/deadlock 清零的地方，而它原先**完全静默**。
            #   实机后果：日志里只见到 17 条 mismatch 行（stale 7..23），
            #   被清零的成功 tick **一行都不打印** ⇒ 多个小周期被拼成一条
            #   「看似单调」的数列 ⇒ 误导排查（我本人第一轮就被它骗过；
            #   队长用 grep 统计时也踩了同一个坑）。
            #   ⇒ 清零必须留痕：记一行，把「本次清零 + 清零前各计数 + 为什么清零」写出。
            #     这样任一 tick 结束时，都能从日志读出
            #     「求解器给了什么 / 为什么没执行 / 三个计数各是多少」。
            _stale_before = self._solver_stale_reclaim_count
            _deadlock_before = self._solver_deadlock_count
            self._solver_stale_reclaim_count = 0
            self._solver_deadlock_count = 0
            self._combat_log_solver_vs_executed(
                solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                executed_action="(计划已恢复：清零计数)",
                executed_kwargs={},
                matched=False,
                fallback_reason=(
                    f"求解器基于最新局面给出可执行首步 ⇒ 判定已恢复 ⇒ 清零"
                    f"（清零前 stale={_stale_before}"
                    f"/{self._SOLVER_STALE_RECLAIM_LIMIT}、"
                    f"deadlock={_deadlock_before}"
                    f"/{self._SOLVER_DEADLOCK_RETRIES}；"
                    f"清零后 stale=0、deadlock=0；plan_origin={plan_origin}）"
                ),
                hand=hand,
                # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                snapshot=snapshot,
                classification=classification,
            )

        end_turn_action = self._find_action(available_actions, "end_turn")

        # ── ★★ t13 修复（D3）：非玩家行动窗口期**既不计数、也不发动作**，但有界 ─────────
        #
        # 缺陷（实机证据，`N.E.K.O_Plugin_sts2_autoplay_20261019.log`）：
        #   09:14:42 死锁 5/3 origin=none 手牌=[] ｜ 09:14:43 死锁 7/3 手牌=[]
        #   09:14:54 5/3 ｜ 09:14:55 7/3 ｜ 09:15:20 5/3 ｜ 09:15:31 5/3   （post-reload 6 条）
        #   全部 `origin=none` + `手牌=[]` ⇒ 局面根本**不在玩家行动窗口**。
        #
        # 根因：`end_turn` 不在 `available_actions` 里（窗口没开）⇒ 上面那个
        #   `end_turn_action is not None` 的 guard 把兜底**静默挡住**，
        #   但 `_solver_deadlock_count` 已经先 +1 了 ⇒ 计数**越过自己的阈值**继续涨
        #   （`N/3` 的 N 到 5、7）⇒ 语义自相矛盾，且若该状态持续则**永久不发动作**。
        #
        # 判据：`end_turn` 不可用 ⇒ 我们**无法**用 end_turn 打破任何东西
        #   （end_turn 是唯一永远合法的动作，它不可用说明窗口没开）。
        #   ⇒ 此时计数毫无意义（不是"求解器无解"，是"轮不到我们"）⇒ **不累加**。
        #   ★ 关键：不能只是"不计数"就完事 —— 那会让 `N/3` 永远停在旧值而看起来正常，
        #     若窗口再也不开（mod 侧卡住），插件就永久静默。故加**独立的窗口外计数**
        #     `_solver_out_of_window_count`，达上限后记一条**可观测的**告警日志，
        #     让"插件没动"与"窗口没开"在日志里可区分（这是本缺陷最贵的部分：它静默）。
        #   ★ 不得把 `autoplay_state` 改成 error（那是别处的老 bug 模式），
        #     也不得伪造动作：窗口外我们**只能**等，但必须**说出来**。
        is_player_window_open = end_turn_action is not None
        if not is_player_window_open:
            # 窗口没开 ⇒ 清空僵局计数（它只对"玩家窗口内的僵局"有意义），
            # 改用一个**独立**的窗口外计数来度量"等了多久"，避免污染 N/3。
            self._solver_deadlock_count = 0
            self._solver_out_of_window_count += 1
            self._combat_log_solver_vs_executed(
                solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                executed_action="(不发动作，等玩家窗口)",
                executed_kwargs={},
                matched=False,
                fallback_reason=(
                    f"非玩家行动窗口（available_actions 无 end_turn）⇒ 不计数、不发动作"
                    f"（窗口外第 {self._solver_out_of_window_count} 次"
                    f"/{self._SOLVER_OUT_OF_WINDOW_LIMIT}；plan_origin={plan_origin}）"
                ),
                hand=hand,
                # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                snapshot=snapshot,
                classification=classification,
            )
            if self._solver_out_of_window_count >= self._SOLVER_OUT_OF_WINDOW_LIMIT:
                # ★ 出口的**下限**：只保证"可观测"，不越权发动作。
                #   `end_turn` 不可用时我们没有任何合法动作可发（end_turn 是唯一永远合法的），
                #   所以正确的出口是：**把它说出来**，并让 loop_runner 侧的看门狗
                #   （t14：无进展看门狗）能据此判定"loop 卡住了"从而重连/恢复。
                #   ★ 计数器达上限后**回绕**（而非继续涨），这样日志不会出现 `N/3` 那种越界数字，
                #     但每条都带"第 N/LIMIT 次"且每 LIMIT 次会再喊一次，不会只喊一次就沉默。
                self._combat_log_solver_vs_executed(
                    solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                    solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                    solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                    executed_action="(窗口外超时：等待恢复)",
                    executed_kwargs={},
                    matched=False,
                    fallback_reason=(
                        f"★ 非玩家行动窗口持续 {self._solver_out_of_window_count} 次"
                        f"（≥ {self._SOLVER_OUT_OF_WINDOW_LIMIT}）⇒ 局面疑似卡住："
                        f"既无 end_turn 可发、也没有可执行计划"
                        f"（{solver_skip_reason}；plan_origin={plan_origin}）"
                    ),
                    hand=hand,
                    # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                    snapshot=snapshot,
                    classification=classification,
                )
                self._solver_out_of_window_count = 0
            return None

        # 回到玩家窗口 ⇒ 窗口外计数清零（它只度量"连续窗口外"的时长）。
        self._solver_out_of_window_count = 0

        if self._solver_deadlock_count >= self._SOLVER_DEADLOCK_RETRIES:
            self._combat_log_solver_vs_executed(
                solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
                solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
                solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
                executed_action="end_turn",
                executed_kwargs={},
                matched=False,
                fallback_reason=(
                    f"求解器连续 {self._solver_deadlock_count} 次给不出可执行动作"
                    f"（{solver_skip_reason}）⇒ 发 end_turn 打破死锁"
                    f"（plan_origin={plan_origin}）"
                ),
                hand=hand,
                # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                snapshot=snapshot,
                classification=classification,
            )
            # 打破僵局后清零：下一回合是全新局面，不该继续背着旧计数。
            self._solver_deadlock_signature = ""
            self._solver_deadlock_count = 0
            self._solver_stale_reclaim_count = 0
            self._solver_out_of_window_count = 0
            return PlannedOperation(
                action_type="end_turn",
                kwargs={},
                confidence=0.5,
                source="solver_deadlock_breaker",
                reason="solver_deadlock_end_turn",
            )

        # 正常路径：本 tick 不发动作，等 loop_runner 下一 tick 重取 /solver/plan。
        # ★ 必须留痕，否则"插件没动"和"插件卡死"在日志里无法区分。
        self._combat_log_solver_vs_executed(
            solver_kind=(first_step.get("kind") if isinstance(first_step, dict) else None),
            solver_card_id=(first_step.get("card_id") if isinstance(first_step, dict) else None),
            solver_target=(first_step.get("target_index") if isinstance(first_step, dict) else None),
            executed_action="(不发动作，等重算)",
            executed_kwargs={},
            matched=False,
            fallback_reason=(
                f"{solver_skip_reason} ⇒ 纯求解器语义：丢弃本计划、重算"
                f"（死锁 {self._solver_deadlock_count}/{self._SOLVER_DEADLOCK_RETRIES}；"
                f"plan_origin={plan_origin}）"
            ),
            hand=hand,
            # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
            snapshot=snapshot,
            classification=classification,
        )
        return None

    def _combat_deadlock_signature(self, snapshot: dict[str, Any]) -> str:
        """战斗死锁判据用的**局面指纹**：只有"局面真的动了"才改变它。

        ★ t11：实现已**提取到 `loop_runner._combat_deadlock_signature`**（模块级纯函数），
          因为 `loop_runner` 也要在"取回计划那一瞬间"算同一个指纹（`plan_fetch_signature`），
          而它拿不到本实例方法。这里委托过去，**保证两侧永不走样** ——
          若各写一份，将来必然漂移，于是"判 B"与"判漂移"会依据不同的局面概念。

        取材刻意与 `_combat_plan_signature` 对齐（手牌/能量/敌人血量），
        这样"计划该重取"与"死锁该计数"依据同一个局面概念，不会互相打架。
        """
        from .loop_runner import _combat_deadlock_signature as _shared

        return _shared(snapshot)

    def _find_action(self, actions: list[dict[str, Any]], action_name: str) -> dict[str, Any] | None:
        target = str(action_name or "").strip().lower()
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            candidate = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
            if candidate == target:
                return action
        return None

    def _solver_potion_option_index(self, snapshot: dict[str, Any], potion_id: str | None) -> int | None:
        """用 potion_id 回查 snapshot.raw_state.run.potions[] 的真实下标（含空槽），且要求 can_use。

        mod /solver/plan 的 use_potion 给的是 potion_id；插件的 use_potion 动作要 option_index，
        所以必须回查完整 potions 列表（不能用压缩后的占用槽下标）。
        """
        if not potion_id:
            return None
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
        for p in run.get("potions") or []:
            if not isinstance(p, dict):
                continue
            if str(p.get("potion_id") or "") != potion_id:
                continue
            if not p.get("can_use"):
                return None
            if isinstance(p.get("index"), int):
                return p["index"]
        return None

    def _selected_character_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selected_character_id = payload.get("selected_character_id")
        if not selected_character_id:
            return None
        raw_state = payload.get("character_select") if isinstance(payload.get("character_select"), dict) else {}
        characters = raw_state.get("characters") if isinstance(raw_state.get("characters"), list) else []
        for character in characters:
            if not isinstance(character, dict):
                continue
            if character.get("character_id") == selected_character_id:
                try:
                    return int(character.get("index"))
                except Exception:
                    return None
        return None

    def _main_menu_kwargs(self, action: dict[str, Any], summary_context: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type != "choose_timeline_epoch":
            return {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        timeline = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else {}
        slots = timeline.get("slots") if isinstance(timeline.get("slots"), list) else []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if not bool(slot.get("is_actionable")):
                continue
            try:
                return {"option_index": int(slot.get("index"))}
            except Exception:
                continue
        return {"option_index": 0}

    def _first_available_map_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            if "index" in node:
                try:
                    return int(node.get("index"))
                except Exception:
                    continue
            return index
        return None

    def _event_screen_signature(self, summary_context: dict[str, Any]) -> str:
        """事件屏签名：用于判断"是否还是同一个事件屏"。

        刻意**不含** hp/gold —— 它们会在同一屏内因已执行的选项而变化，
        若纳入签名会让"已选次数"在每次选完后重置，记忆就失效了。
        只取"哪些选项"（文案+下标），这才是一屏的身份。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        parts: list[str] = []
        for index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            option_index = self._safe_int(option.get("index"))
            if option_index is None:
                option_index = index
            parts.append(f"{option_index}:{self._event_option_text(option)[:40]}")
        return "|".join(parts)

    def _note_event_option_chosen(self, summary_context: dict[str, Any], option_index: int) -> None:
        """记录"某个事件选项**已被真正派发**过几次"。

        ★修复缺陷 A3：`_rank_event_options` 原本是**无状态**的 —— 只读当前 hp/gold，
        完全不知道"这个选项我已经选过"。于是在**可重复选择**的事件上（每次选完屏幕不变、
        选项还在），它每轮都算出同一个最优解 ⇒ **无限重复**，直到 HP 变 1。

        与 `note_selection_option_pressed` 同范式：**只在真正派发成功后**调用
        （由 service 在动作成功后回调），不在 `plan()` 里累加 —— 因为 `plan()` 每 tick
        都被调用（约 1 次/秒），而实际派发约 2.5 秒一次，在 plan 里累加会凭空放大计数。
        """
        signature = self._event_screen_signature(summary_context)
        if signature != self._event_memory_key:
            self._event_memory_key = signature
            self._event_choice_counts = {}
        self._event_choice_counts[option_index] = self._event_choice_counts.get(option_index, 0) + 1

    def _event_repeat_penalty(self, summary_context: dict[str, Any], option_index: int) -> float:
        """同一事件屏上"该选项已选过 N 次"的**递减惩罚**。

        ★★ 这里有一个**必须避免的陷阱**（我在实现时踩到并已修正）：

        若对所有选项一视同仁地累加惩罚，会把**无害选项**（如"离开"）也一起压下去 ——
        实测后果：反复选"离开"4 次后，`离开` 被压到 `扣上限` 之下，**最优解反而翻回了扣上限的选项**
        （复现：第 1 轮 `[1,0]` 正确，第 4 轮 `[0,1]` 反转）。这会让修复**比不修更糟**。

        正确做法：**只惩罚"有害选项"的重复**。判据是该选项本身是否已被评分为负 ——
        "离开"这类中性选项（分数 0）**永不惩罚**，因此不会再出现"好选项被累积惩罚压过头"。

        于是语义变成：**同一屏上反复选一个"有害"选项，惩罚递增；选无害选项不累积。**
        这既不改变一次性事件的正常决策（第一次选零惩罚），又能在可重复选择的事件上止损。
        """
        signature = self._event_screen_signature(summary_context)
        if signature != self._event_memory_key:
            return 0.0
        times = self._event_choice_counts.get(option_index, 0)
        if times <= 0:
            return 0.0
        return float(self._EVENT_REPEAT_PENALTY_PER_TIME * times)

    def _preferred_event_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        # 1) 硬覆盖：玩家用自然语言钉死了某个选项，优先级高于任何评分。
        pinned = self._preferred_option_index(preferences)
        if pinned is not None:
            return pinned

        # 2) 纯启发式打分（事件房已无 LLM 评分融合，见 _rank_event_options）。
        ranked = self._rank_event_options(summary_context, strategy_context)
        return ranked[0] if ranked else None

    def _rank_event_options(
        self,
        summary_context: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> list[int]:
        """事件选项按启发式分数**从好到坏**排序（返回选项下标）。

        与 ``_preferred_event_option`` 的评分口径完全一致，唯一差别是不取最高分而是保留整条顺序，
        供游玩模式的扰动层挑选次优/最差——`. 0` 就是原来的最优。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        guidance_override = self._guidance_override_from_payload(summary_context)

        hei_scores: dict[int, int] = {}
        indexed: list[tuple[int, dict[str, Any]]] = []
        # ★★ D11（t28）：本屏禁用集（按 `screen|floor|event_id` 隔离）——只影响**本屏**。
        locked_key = self._event_locked_set_key(summary_context)
        if locked_key != self._event_locked_set_key_cached:
            self._event_locked_set_cached = set()
            self._event_locked_set_key_cached = locked_key
        banned = self._event_locked_set_cached
        locked_by_field: list[int] = []
        seen_indices: set[int] = set()
        for index, option in enumerate(event_options):
            if not isinstance(option, dict):
                continue
            option_index = self._safe_int(option.get("index"))
            if option_index is None:
                option_index = index
            seen_indices.add(option_index)
            # ★ 预防：字段直接说被锁 ⇒ 记入禁用集（跨 tick 保持，见 `note_event_option_locked`）。
            if self._event_option_is_locked(option):
                locked_by_field.append(option_index)
                banned.add(option_index)
            # ★★ 关键修正（t28 自测发现，实机 01:19:16 暴露 4 次误报）：
            #   `/state` 的 `is_locked` 是**权威且更新**的数据源；禁用集只是**错误驱动的补充**。
            #   ⇒ 若本 tick 的数据**明确**说明该 index 未被锁（字段为 False），
            #     则禁用集里的旧记录**已过期**，必须移除 —— 否则会误禁一个合法选项。
            #
            #   为什么必须这样（实机证据）：ENDLESS_CONVEYOR 是**多页事件**
            #     （`text_key` 含 `pages.ALL.options.LOCKED` /
            #       `pages.GRAB_SOMETHING_OFF_THE_BELT.options.LEAVE`），
            #   换页后 `screen|floor|event_id` **三者都不变** ⇒ 隔离键**无法区分页**
            #   ⇒ 上一页 index=0 被锁的禁用记录**串到下一页**，而下一页 index=0 合法
            #   ⇒ 实测 01:19:16 连打 4 条「本屏所有选项均在禁用集内 [0]」**误报**。
            #   ★ 修法不是继续加大隔离键（页 id 从 `/state` 拿不到），
            #     而是**让权威数据压制补充记录** —— 数据说没锁，就不该再禁。
            elif option.get("is_locked") is False:
                banned.discard(option_index)
            # ★ 运行时禁用集里的项**不参与打分**（但仍返回分数，供日志显示为何被排除）。
            hei_scores[option_index] = self._score_event_option(option, hp_ratio, gold)
            indexed.append((option_index, option))
        # ★ 已不在本屏出现的 index ⇒ 禁用记录无意义，清掉（防止跨屏累积）。
        banned &= seen_indices
        self._event_locked_set_cached = banned
        hei_norm = self._normalize_percentile(hei_scores)

        # ★★ 让【跨局复盘教训】作用于事件选择（用户点名的缺陷 A/B 所在屏）。
        #   此前事件屏只读 `guidance_override`（payload 里的临时指示）与启发分，
        #   **完全不读跨局教训** ⇒ 复盘学到"别再吃永久掉上限的选项"也无法阻止重犯。
        #   `strategy_directives` 在 `strategy_context`（兄弟键），故由调用方传入。
        #   ★ 教训的**唯一真相来源**是 `_event_final_scores` 内部（打分核心）；
        #     这里只为日志再取一次信号，**不参与打分**，避免出现第二个真相来源。
        lesson_signals = self._reward_lesson_signals(strategy_context)

        final_scores = self._event_final_scores(
            summary_context,
            strategy_context,
            hei_scores=hei_scores,
            hei_norm=hei_norm,
            indexed=indexed,
            guidance_override=guidance_override,
        )
        # 与 _preferred_event_option 同口径：分数高的在前，同分时下标小的在前。
        scored = sorted(final_scores.items(), key=lambda item: (-item[1], item[0]))
        # ★★ D11（t28）：把**已确认不可选**的下标排到**最后**。
        #
        #   ★ 为什么是"排到最后"而不是"直接删除"：
        #     若某屏**所有**选项都被判为锁定（数据异常 / 字段误读），直接删除会得到**空候选**
        #     ⇒ `_preferred_event_option` 返回 None ⇒ 插件**不发任何动作** ⇒ 反而制造新的卡死。
        #     排到最后可保证：只要还有任一未锁项就选它；全都锁时仍有一个（最差的）可发
        #     —— 此时"发出去被拒"至少留下**可观测**痕迹，而不是静默无动作。
        #   ★ 这是安全属性：**绝不因本修复把候选集变空**。
        banned_order = [ix for ix, _ in scored if ix in banned]
        free_order = [ix for ix, _ in scored if ix not in banned]
        ordered = free_order + banned_order
        if banned_order and not free_order:
            if self._logger is not None:
                try:
                    self._logger.warning(
                        "[sts2_d11] 本屏**所有**选项均在禁用集内 %s ⇒ 无未锁可选，"
                        "仍发出最差分项以避免静默无动作（请人工介入）" % (sorted(banned),)
                    )
                except Exception:
                    pass
        self._log_event_route(hei_scores, scored, ordered, event_options, hp_ratio, gold, lesson_signals)
        return ordered

    def _event_final_scores(
        self,
        summary_context: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
        *,
        hei_scores: dict[int, int] | None = None,
        hei_norm: dict[int, float] | None = None,
        indexed: list[tuple[int, dict[str, Any]]] | None = None,
        guidance_override: dict[str, Any] | None = None,
    ) -> dict[int, float]:
        """事件选项的**最终分**（排序前的唯一真相来源）。

        ★ 抽成独立函数的原因：把管线（启发分 → 归一化 → 教训 → 重复惩罚 → 最终分）
          集中在一处，`_rank_event_options` 与测试**共用同一实现**，
          避免"测试复刻了一份打分逻辑"导致测试通过而线上行为不同。

        教训（`strategy_context`）的三处作用：
          - `self_damage` 教训 ⇒ **永久扣生命上限**的选项 −45，普通掉血 −18（缺陷 A）
          - 诅咒教训 ⇒ 拿诅咒/塞牌的选项 −30（缺陷 B）
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        if guidance_override is None:
            guidance_override = self._guidance_override_from_payload(summary_context)
        if indexed is None:
            indexed = []
            for index, option in enumerate(event_options):
                if not isinstance(option, dict):
                    continue
                option_index = self._safe_int(option.get("index"))
                indexed.append((option_index if option_index is not None else index, option))
        if hei_scores is None:
            hei_scores = {
                option_index: self._score_event_option(option, hp_ratio, gold)
                for option_index, option in indexed
            }
        if hei_norm is None:
            hei_norm = self._normalize_percentile(hei_scores)

        lesson_signals = self._reward_lesson_signals(strategy_context)
        lesson_text = self._lesson_raw_text(strategy_context)
        wants_curse_avoid = ("curse" in lesson_signals) or any(
            token in lesson_text for token in ("诅咒", "curse")
        )

        out: dict[int, float] = {}
        for option_index, option in indexed:
            final = hei_norm.get(option_index, 50.0)
            text = self._event_option_text(option)
            if guidance_override.get("prefer_defense") and self._option_loses_hp(text):
                final -= 15.0
            if guidance_override.get("prefer_attack") and any(tok in text for tok in ["relic", "遗物", "gold", "金币", "card", "卡牌"]):
                final += 10.0
            # ★ 缺陷 A（用户点名）：**永久扣生命上限**的选项，在教训提到过血上限/自损时重罚。
            #   实际风险是「可重复选择 ⇒ 被反复选到把上限烧到很低」，故罚分要压过启发分。
            if lesson_signals.get("self_damage"):
                if self._option_loses_max_hp(text):
                    final -= 45.0
                elif self._option_loses_hp(text):
                    final -= 18.0
            # ★ 缺陷 B（用户点名）：教训里出现过"诅咒"相关教训时，拿诅咒/塞牌的选项降权。
            if wants_curse_avoid and any(
                tok in text for tok in ("诅咒", "curse", "放入一张", "塞入", "变为")
            ):
                final -= 30.0
            # ★缺陷 A3：同一屏上重复选"**有害**"选项要递减，否则可重复选择的事件会被选到死。
            #   ★只对原始评分为负的选项施加 —— 否则会把"离开"这类中性选项一起压下去，
            #   导致反复选"离开"后惩罚累积、最优解反而翻回有害选项（实测过这个反转）。
            if hei_scores.get(option_index, 0) < 0:
                final -= self._event_repeat_penalty(summary_context, option_index)
            out[option_index] = final
        return out

    def _log_event_route(
        self,
        hei_scores: dict[int, int],
        scored: list[tuple[float, int]],
        ordered: list[int],
        event_options: list[Any],
        hp_ratio: float,
        gold: int,
        lesson_signals: dict[str, int] | None = None,
    ) -> None:
        """把事件屏决策写进日志（与 `[sts2_map_route]` 同风格）。

        ★为什么要加：此前**事件屏决策完全不记日志** —— 地图有 `[sts2_map_route]`、
        战斗有 `[sts2_combat_turn_gate]`，唯独事件屏什么都没有。
        这让我无法实机验证 A1/A3/C 的任何修复（只能靠单测），
        也让玩家遇到"反复扣上限"时拿不到任何可诊断的痕迹。

        去重键取"原始分 + 最终序"，同一屏静止时不刷屏（与 `_last_map_route_log_key` 一致）。
        """
        if self._logger is None:
            return
        key = "|".join("%d:%d:%.2f" % (index, hei_scores.get(index, 0), final)
                       for final, index in scored)
        key += "|%s" % sorted((lesson_signals or {}).items())
        if key == self._last_event_route_log_key:
            return
        self._last_event_route_log_key = key
        texts = {}
        for index, option in enumerate(event_options):
            if isinstance(option, dict):
                option_index = self._safe_int(option.get("index"))
                texts[option_index if option_index is not None else index] = self._event_option_text(option)
        # 截断长度取 60：实测 18 时会把"失去6点生命上限"这类**关键区别**截掉
        # （只能看到"失去[red]6[/red]点生"），无法从日志判断是掉血还是永久扣上限 ——
        # 而那恰恰是缺陷 A 的判定核心。日志是实机验证的唯一证据来源，必须能看清。
        detail = " / ".join("%d:%s" % (index, texts.get(index, "")[:60]) for index in ordered)
        # ★ 标出哪些选项是"永久扣生命上限"（缺陷 A 的判定核心），便于实机核对。
        maxhp_flags = [index for index in ordered if self._option_loses_max_hp(texts.get(index, ""))]
        try:
            self._logger.info(
                "[sts2_event_route] 金币=%s 血比=%.2f 教训=%s 启发分=%s 重复惩罚=%s 扣上限选项=%s -> 选 option_index=%s | %s",
                gold,
                hp_ratio,
                lesson_signals or {},
                {index: hei_scores.get(index, 0) for index in ordered},
                {index: round(self._event_repeat_penalty({"payload": {
                    "event_options": event_options}}, index), 1) for index in ordered},
                maxhp_flags,
                ordered[0] if ordered else None,
                detail,
            )
        except Exception:
            # 日志绝不能影响决策路径。
            pass

    @staticmethod
    def _normalize_percentile(scores: dict[int, int]) -> dict[int, float]:
        """把一组整数分 min-max 归一化到 0-100（单值/全等分 → 50），供与 LLM 0-100 分融合。"""
        if not scores:
            return {}
        values = [float(v) for v in scores.values()]
        lo, hi = min(values), max(values)
        if hi <= lo:
            return {k: 50.0 for k in scores}
        return {k: 100.0 * (float(v) - lo) / (hi - lo) for k, v in scores.items()}

    @staticmethod
    def _strip_rich_text(text: str) -> str:
        """剥掉游戏文案里的 BBCode 式富文本标签，**再**做语义匹配。

        ★★这是实机抓出来的严重缺陷（A2 的隐性失效）：
        游戏下发的选项文案**带标签且标签正好插在关键词与数字之间**：
            ``失去[red]8[/red]点生命。获得1瓶随机药水。``
        而 `_option_loses_hp` 的正则是 ``失去\\s*\\d*\\s*点?\\s*(生命|血量)`` ——
        ``失去`` 与 ``8`` 之间夹着 ``[red]`` ⇒ **匹配失败** ⇒ 掉血惩罚完全丢失。

        实机后果（EVENT#3，血比 0.73）：
        ```
        启发分={0: 10, 1: 10} -> 选 0 | 0:获得藏宝图。 / 1:失去[red]8[/red]点生命。…
        ```
        两个选项**同分**（本该是 −8 vs 10 的明确差距），最后靠 index 平手规则才选中安全项
        —— **是运气，不是启发式在起作用**。

        同类失效还会波及：`获得[green]2[/green]点最大生命值`、
        `支付[red]150[/red][gold]金币[/gold]`（金币判定用的是裸子串，侥幸没坏）。
        ⇒ 所有事件文案匹配**统一先过这一层**，而不是逐个正则去容忍标签。
        """
        t = str(text or "")
        # 去掉 [xxx] / [/xxx]；游戏里也有 [img]res://...[/img] 这类跨段标签。
        return re.sub(r"\[/?[A-Za-z][^\]]*\]", "", t)

    @staticmethod
    def _option_loses_max_hp(text: str) -> bool:
        """事件选项是否**永久削减生命上限**（`max_hp`）。

        与 `_option_loses_hp`（一次性掉血、可被治疗回来）区分开：上限损失**不可恢复**，
        会永久降低整局容错空间，因此必须是**更高量级**的惩罚。

        实机证据：三个 run 里 `PVV8N7MJ4VGY` 的 `max_hp` 从 80 被扣到 54（−26）且此后不再恢复。

        匹配要点（中英双语，且不能被"失去生命"先命中而漏掉）：
        * 中文：「失去 5 点生命上限」「最大生命 -5」
        * 英文：`lose 5 max hp` / `maximum hp -5`（容忍点号与空格）

        ★★必须判定**方向**（实机发现的严重缺陷，我第一版漏了）：
        只匹配"提到了最大生命"是**错的** —— 实机真实事件里出现过
        「获得 11 点最大生命值。」（**增益**），旧实现把它也判成损失并罚 −60，
        于是脚本**主动放弃了 +11 最大生命**，与修复意图完全相反。
        ⇒ 必须同时要求"损失语义"与"没有增益语义"：
          * 有 `失去`/`lose`/`-` 等损失词 ⇒ 是损失
          * 有 `获得`/`gain`/`+`/`提升`/`增加` 等增益词 ⇒ **不是**损失
        「最大生命 -5」用负号表达，故负号也算损失信号。

        ★富文本标签必须先剥掉（见 `_strip_rich_text`）。
        """
        t = STS2HeuristicPlanner._strip_rich_text(text).lower()
        # 是否"提到了最大生命"这个概念
        mentions = (
            re.search(r"(生命|血量|最大)\s*(值)?\s*(上限|最大值)", t) is not None
            or re.search(r"最大生命", t) is not None
            or re.search(r"(max|maximum)\s*\.?\s*(hp|health)", t) is not None
        )
        if not mentions:
            return False
        # ★增益语义优先排除（实机：「获得 11 点最大生命值。」）
        #   「提高」「变成」等也要算增益 —— 例如「你的生命上限提高了」。
        # 损失语义：扣/失去/减少/负号
        loss = re.search(r"(失去|扣|减少|降低|lose|reduce|-\s*\d)", t) is not None
        # ★增益与损失都有时，**以损失为准**：`"Lose 5 Max HP, gain a relic"` 必须判为损失
        #   （先扣永久上限再给遗物，净收益远不足以抵消）。
#   注：此处**不需要**额外判断「有增益就放行」——
        #   那种情况下 `loss` 本身已是 False，`return loss` 结果完全相同
#   （实测确认「有增益就放行」与 return loss 等价，属冗余代码已删除；
        #   故本函数只计算 loss，不再保留 gain 变量。）
        return loss

    @staticmethod
    def _option_loses_hp(text: str) -> bool:
        """事件选项是否掉血（健壮匹配，容忍 "lose 7 HP"/"Lose HP"/中文表述）。

        **不含**生命上限损失 —— 那是 `_option_loses_max_hp` 的语义，量级不同。

        历史缺陷（本轮修正）：原实现用字面量 ``"失去生命"`` 匹配中文，于是
        ``"失去 10 点生命"``（中间有数字与量词）**匹配不到** —— 实测确认该缺陷在
        ``HEAD`` 版本即存在，非本次引入。中文里"失去 N 点生命"是常见形态，故改用正则容忍
        数字与量词：``失去\\s*\\d*\\s*点?\\s*(生命|血量)``。

        ★★富文本标签（实机二次修正）：即便容忍了数字，游戏真实文案是
        ``失去[red]8[/red]点生命`` —— 标签夹在 **动词与数字之间**，正则仍然失败。
        ⇒ 统一在 `_strip_rich_text` 里先剥标签（见该方法说明，附实机后果）。
        """
        t = STS2HeuristicPlanner._strip_rich_text(text).lower()
        # 上限损失走专门路径，避免被这里的"失去生命"宽泛匹配吞掉而失去区分度。
        if STS2HeuristicPlanner._option_loses_max_hp(t):
            return False
        return (
            "失去生命" in t                      # 无数字的最短形态，保留以兼容旧行为
            or re.search(r"失去\s*\d*\s*点?\s*(生命|血量)", t) is not None
            or "掉血" in t
            or "lose hp" in t
            or "lose health" in t
            or re.search(r"lose\s+\d+\s*(hp|health)", t) is not None
        )

    def _preferred_option_index(self, preferences: dict[str, Any]) -> int | None:
        record = preferences.get("record") if isinstance(preferences.get("record"), dict) else None
        if isinstance(record, dict):
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for item in records:
            if not isinstance(item, dict):
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        return None

    def _preferred_reward_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        reward_cards = self._reward_candidates(summary_context)
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        # ★ deck 在两个位置出现：新口径在 `payload` 顶层，旧口径在 `payload["deck"]`。
        #   只读后者会漏掉实机形状 ⇒ 流派永远推断为空。
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        if not deck.get("cards") and isinstance(payload.get("run"), dict):
            run_deck = payload["run"].get("deck")
            if isinstance(run_deck, list):
                deck = {"cards": run_deck}
            elif isinstance(run_deck, dict):
                deck = run_deck
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        # ★★ 缺陷 E 修复：外部 `deck_policy` 恒空（全仓库无人产出该记录）⇒
        #   `_score_reward_card` 里所有流派加权（archetype_bias +20 等）永不触发。
        #   这里用**卡组自身**推断的流派补位，让「卡牌流派的抉择」真正存在。
        #   外部指令若存在则合并（不覆盖），保持向前兼容。
        internal_policy = self._archetype_policy_from_deck(deck, archetype_tags)
        if internal_policy:
            merged_bias = set(deck_policy.get("archetype_bias") or set()) | set(internal_policy.get("archetype_bias") or set())
            deck_policy = dict(deck_policy)
            deck_policy["archetype_bias"] = merged_bias
            self._debug(
                "[sts2_archetype] 卡组流派=%s -> archetype_bias=%s"
                % (archetype_tags, sorted(merged_bias))
            )
        guidance_override = self._guidance_override_from_payload(summary_context)
        # ★★ 让【跨局复盘教训】真正作用于选卡质量。
        #   此前教训只经 `_apply_run_lessons` 进入通用排序（`ordered_candidates` 的扰动候选池），
        #   **完全不参与 `_score_reward_card`** ⇒ 复盘无论学到什么都不改变「选哪张卡」。
        #   这是用户点名的「强化/删除哪张卡片」缺口的直接原因。
        #   ★ 注意：`strategy_directives` 在 `strategy_context` 里，**不在** `summary_context` 里
        #     （`service.py:549-557` 把两者作为兄弟键放进 snapshot）
        #     ⇒ 必须显式传入，否则运行时读到的是不存在的键（恒空 = 白改）。
        lesson_signals = self._reward_lesson_signals(strategy_context)
        # ★ 留痕：把这次评估的输入与打分记下来，供 `_log_reward_route` 输出。
        #   （`_debug()` 是空函数，不留痕线上就无法核对选卡依据。）
        self._last_reward_archetype_tags = list(archetype_tags)
        self._last_reward_bias = set(deck_policy.get("archetype_bias") or set())
        self._last_reward_lesson_signals = dict(lesson_signals)
        self._last_reward_scored = []
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(reward_cards if isinstance(reward_cards, list) else []):
            if not isinstance(card, dict):
                continue
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            score += self._reward_lesson_bonus(card, lesson_signals, archetype_tags)
            if guidance_override.get("prefer_defense"):
                if self._card_block(card) > 0:
                    score += 18
                if self._reward_dynamic_value(card, "HpLoss") > 0:
                    score -= 12
            if guidance_override.get("prefer_attack"):
                if self._card_damage(card) > 0:
                    score += 14
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            self._last_reward_scored.append((card_index, round(score, 2)))
            candidate = (-score, card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None and best_candidate[0] < 0:
            return best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    #: 复盘教训 → 选卡倾向 的关键词表。
    #:
    #: ★ 背景（2026-10-17）：用户指出「强化/删除哪张卡片、卡牌流派的抉择」是败因，
    #:   但此前跨局教训**只**经 `_apply_run_lessons` 影响通用排序，
    #:   **完全不参与 `_score_reward_card`** ⇒ 复盘对「选哪张卡」零影响。
    #:   这里补上通道：把教训文本里的**方向性措辞**映射为选卡加权。
    #:
    #: ★★ 关键设计（否则会误判）：教训里往往**同时**提到两个方向
    #:   （如「输出不够，每回合都在垫格挡」同时含"输出"与"格挡"），
    #:   单纯"出现关键词就加权"会给两边都加分 ⇒ 净效果为 0 或错向。
    #:   ⇒ 必须区分**褒贬**：正向词（要多做）与负向词（别再这样）。
    _REWARD_LESSON_POSITIVE: dict[str, tuple[str, ...]] = {
        "defense": ("多垫", "先垫", "垫够", "留容错", "加格挡", "多格挡", "厚一点", "稳一点", "别头铁"),
        "attack": ("输出不够", "伤害不足", "打不死", "多输出", "加攻击", "斩杀不足", "节奏太慢"),
        "draw": ("抽牌不够", "过牌少", "卡手", "手牌少", "多抽"),
        "energy": ("能量不够", "费用不够", "卡能", "多来点能量"),
        "strength": ("力量流", "缺力量", "多力量", "易伤不够"),
        "orb_focus": ("电球不够", "充能不足", "多聚焦"),
    }
    _REWARD_LESSON_NEGATIVE: dict[str, tuple[str, ...]] = {
        "defense": ("光垫格挡", "只垫格挡", "一直在垫", "垫太多", "纯防御打不动"),
        "attack": ("光输出", "只输出", "硬刚", "头铁", "无脑攻击", "贪输出"),
        "draw": ("抽太多", "过牌过头"),
        "energy": ("能量溢出", "费用多余"),
        "strength": ("力量没用",),
        "orb_focus": ("电球没用",),
    }

    def _lesson_raw_text(self, source: dict[str, Any] | None) -> str:
        """取跨局教训的**原始文本**（小写拼接），供粗粒度关键词判据使用。

        与 `_reward_lesson_signals` 的分工：那个产出**方向信号**（选卡/删牌用），
        这个产出**原文**（事件屏的"诅咒"类判据用，不需要方向极性）。
        """
        source = source if isinstance(source, dict) else {}
        directives = source.get("strategy_directives")
        if not isinstance(directives, dict):
            directives = source if isinstance(source.get("avoid"), list) else {}
        lessons = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        return " ".join(str(item) for item in lessons if str(item).strip()).lower()

    def _reward_lesson_signals(self, source: dict[str, Any] | None) -> dict[str, int]:
        """从跨局教训里抽出「该多拿什么卡 / 该少拿什么卡」的方向信号。

        返回 {方向: 净权重}，正=多拿、负=少拿。教训为空 ⇒ {}（调用方加权为 0，行为不变）。

        ★ 入参可以是 `strategy_context`（含 `strategy_directives`，运行时形状）
          或直接是含 `avoid` 的 directives 字典 —— 两者都接受，
          避免调用方传错层导致**恒空**（本轮踩过一次：传 summary_context 恒返回 {}）。
        """
        source = source if isinstance(source, dict) else {}
        directives = source.get("strategy_directives")
        if not isinstance(directives, dict):
            # 兼容：直接把 directives 传进来（含顶层 avoid）
            directives = source if isinstance(source.get("avoid"), list) else {}
        lessons = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        text = " ".join(str(item) for item in lessons if str(item).strip()).lower()
        if not text:
            return {}
        signals: dict[str, int] = {}
        for direction, keywords in self._REWARD_LESSON_POSITIVE.items():
            if any(str(k).lower() in text for k in keywords):
                signals[direction] = signals.get(direction, 0) + 1
        for direction, keywords in self._REWARD_LESSON_NEGATIVE.items():
            if any(str(k).lower() in text for k in keywords):
                signals[direction] = signals.get(direction, 0) - 1
        # 自损/血上限担忧（独立轴：对"有自损的卡"降权）
        if any(k in text for k in ("自损", "失去生命", "扣血", "血上限", "生命上限", "烧血")):
            signals["self_damage"] = 1
        # ★★ 诅咒担忧（独立轴，用户点名的缺陷 B）：
        #   「涉及变化卡的事件或遗物要避开诅咒，因为选诅咒只会变成另一种诅咒，
        #     对提升卡牌质量的期望太低」。
        #   ⇒ 教训里出现"诅咒"时置位，供**事件屏**（拿诅咒的选项）与**选卡屏**
        #     （诅咒卡）降权。此前 `curse` 从不被产出 ⇒ 事件屏那条判据是死的。
        if any(k in text for k in ("诅咒", "curse", "污染卡组", "变废", "污染牌库")):
            signals["curse"] = 1
        return {k: v for k, v in signals.items() if v != 0}

    def _reward_lesson_bonus(self, card: dict[str, Any], signals: dict[str, int], archetype_tags: list[str]) -> int:
        """按复盘信号给单张候选卡加权。

        ★ 保守设计（避免噪声压过 `_score_reward_card` 的既有判据）：
          * 每方向每级 +18，最多两级（36）—— 低于"诅咒 −60"与"状态牌 −40"，
            不会把垃圾卡救回来，也不会压过流派/数值的主判据。
          * 负向方向（教训说"别光垫格挡"）对称扣分。
          * 教训为空 ⇒ 返回 0（保证零教训时行为与改动前**完全一致**）。
        """
        if not signals:
            return 0
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        # 各方向的"这张卡属不属于该方向"判据（复用 _score_reward_card 的关键词口径）
        card_is: dict[str, bool] = {
            "defense": any(k in text for k in ("block", "格挡", "护盾", "护甲", "defend")),
            "attack": any(k in text for k in ("damage", "伤害", "攻击", "attack", "strike")),
            "draw": bool(_ARCHETYPE_DRAW_RE.search(text)) or any(k in text for k in ("draw", "skim")),
            "energy": any(k in text for k in ("energy", "能量", "储能")),
            "strength": any(k in text for k in ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")),
            "orb_focus": any(k in text for k in ("orb", "focus", "电球", "充能", "聚焦", "宝珠")),
        }
        bonus = 0
        for direction, weight in signals.items():
            if direction == "self_damage":
                continue
            if card_is.get(direction):
                bonus += 18 * max(-2, min(2, weight))
        if signals.get("self_damage", 0) > 0 and self._reward_dynamic_value(card, "HpLoss") > 0:
            bonus -= 20
        return bonus

    def _reward_claim_signature(self, summary_context: dict[str, Any]) -> str:
        rewards = self._reward_claimable_list(summary_context)
        return "|".join(f"{item.get('index')}:{item.get('reward_type')}" for item in rewards)

    @staticmethod
    def _reward_claimable_list(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        rewards = reward.get("rewards") if isinstance(reward.get("rewards"), list) else []
        return [item for item in rewards if isinstance(item, dict) and bool(item.get("claimable", True))]

    def note_reward_claim_dispatched(self, option_index: Any) -> None:
        """服务层在**真的把 claim_reward 发出去**之后调用。

        "已经试过"必须由执行推动，不能由规划推动 —— 规划每 tick 都跑（事件流约 1 次/秒），
        而执行约 2.5 秒一次，用规划推进会把候选在几秒内白白耗光（选牌光标踩过这个坑）。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._reward_claim_tried.add(index)

    def _next_reward_claim_index(self, summary_context: dict[str, Any]) -> int | None:
        """该领哪一个奖励；都领不动就返回 None，交给调用方去推进界面。

        为什么需要这张账：**药水栏满时"领药水奖励"发不出去，而 mod 照样回 completed** ——
        奖励清单不变、`step_count` 照涨，于是插件每 tick 重发同一个 `claim_reward(0)`（药水），
        无限重领（实测 floor 13 的奖励屏卡了 5 分钟以上）。药水排到最后，领过的下标不再重发。
        """
        rewards = self._reward_claimable_list(summary_context)
        if not rewards:
            return None
        signature = self._reward_claim_signature(summary_context)
        if signature != self._reward_claim_key:
            self._reward_claim_key = signature
            self._reward_claim_tried = set()

        def rank(item: dict[str, Any]) -> tuple[int, int]:
            # 药水最容易"领不动"（栏位满），排到最后；其余按下标顺序。
            is_potion = 1 if str(item.get("reward_type") or "").strip().lower() == "potion" else 0
            index = self._safe_int(item.get("index"))
            return (is_potion, index if index is not None else 0)

        for item in sorted(rewards, key=rank):
            index = self._safe_int(item.get("index"))
            if index is None or index in self._reward_claim_tried:
                continue
            return index
        return None

    def _reward_candidates(self, summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward_cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
        if reward_cards:
            return reward_cards
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if selection_cards:
            return selection_cards
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        card_options = reward.get("card_options") if isinstance(reward.get("card_options"), list) else []
        if card_options:
            return card_options
        return []

    def _preferred_remove_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        selection_cards = summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else []
        # ★ 让【跨局复盘教训】作用于「该删哪张」。
        #   此前删牌屏只经 `_ranked_option_indices → _apply_run_lessons` 影响
        #   **扰动候选顺序**，不改 `preferred` 本身 ⇒ 复盘学到的取向
        #   无法改变真正删掉的那张（用户点名「删除哪张卡片」很影响战斗）。
        #
        #   ★★ 符号方向（我第一次写反了，实测抓到）：本函数取 `candidate` 的 **min**，
        #      即 `_score_remove_card` 分数**越低越该删**（诅咒 −100 最该删）。
        #      ⇒ "保护某张卡"必须**加上**保护量（抬高分数 ⇒ 更不该删），
        #        绝不能减。写成 `score -= protection` 会让被保护的卡变成**最先被删**的
        #        （实测：防御卡 eff=−8 反而胜出 ⇒ 正好反了）。
        lesson_signals = self._reward_lesson_signals(strategy_context if strategy_context is not None else summary_context)
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(selection_cards if isinstance(selection_cards, list) else []):
            if not isinstance(card, dict):
                continue
            if self._is_unremovable_card(card):
                continue
            score = self._score_remove_card(card)
            if lesson_signals:
                # 符合"要多拿"方向的卡 ⇒ **抬高**其"该删程度"分数 ⇒ 更不该删
                score += self._remove_lesson_protection(card, lesson_signals)
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            candidate = (score, -card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None:
            return -best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _remove_lesson_protection(self, card: dict[str, Any], signals: dict[str, int]) -> int:
        """复盘教训对"该删哪张"的保护量（正数 = 越不该删 ⇒ 调用方**加**到删牌分上）。

        ★ 与 `_reward_lesson_bonus` 的关系：那个用于**选卡**（符合方向 ⇒ 加分拿），
          这个用于**删牌**（符合方向 ⇒ 抬高删牌分 ⇒ 更不该删）。语义相反、方向一致。
        ★ 保守：与选卡同量级（每方向每级 18），不足以把诅咒（−100）救成"不该删"。
        """
        if not signals:
            return 0
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        card_is: dict[str, bool] = {
            "defense": any(k in text for k in ("block", "格挡", "护盾", "护甲", "defend")),
            "attack": any(k in text for k in ("damage", "伤害", "攻击", "attack", "strike")),
            "draw": bool(_ARCHETYPE_DRAW_RE.search(text)) or any(k in text for k in ("draw", "skim")),
            "energy": any(k in text for k in ("energy", "能量", "储能")),
            "strength": any(k in text for k in ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")),
            "orb_focus": any(k in text for k in ("orb", "focus", "电球", "充能", "聚焦", "宝珠")),
        }
        protection = 0
        for direction, weight in signals.items():
            if direction == "self_damage":
                continue
            if card_is.get(direction):
                protection += 18 * max(-2, min(2, weight))
        return protection

    def _preferred_route_option(self, preferences: dict[str, Any]) -> int | None:
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    # ---- 商店购买策略（规格 docs/shop_scoring.md）------------------------------------------
    #
    # `_score_reward_card` 的原始分越高越好（命中 deck 策略 +40、archetype +20、分支 +35~50，
    # 核心卡常 ≥40）。老代码里：
    #   `_SHOP_HIGH_VALUE_SCORE = 25` —— "原始分 25 以上就算高价值卡，直接买"；
    #   `_SHOP_ANY_CARD_SCORE = 1`    —— 前面都没得买时，1 分以上的卡也凑合买。
    # 这两个常量现在**并入统一池子**：它们不再是"买/不买"的开关，而是"原始分 -> 补缺分"的标尺
    # （见 `_score_shop_card`）：25 分折成补缺分约 0.42，扣掉代价惩罚后**大致落在门槛附近**，
    # 于是老口径（25 分以上就买）只对"便宜或真缺得厉害的卡"继续成立 —— 这正是玩家要的收紧。
    _SHOP_HIGH_VALUE_SCORE = 25
    _SHOP_ANY_CARD_SCORE = 1
    #: 「补缺分 = 1.0」对应的原始分。取 60 ≈ 2.4 × 老的"高价值线"（`_SHOP_HIGH_VALUE_SCORE` = 25），
    #: 让老口径的边界大致落在门槛上，于是老口径（25 分以上就买）在新口径里只对"便宜或真缺得厉害的卡"成立。
    _SHOP_CARD_FIT_SCALE = SHOP_CARD_FIT_SCALE
    #: 前方有精英时，从选路那一步起向前看几层找精英（规格：「复用它判断"前方有精英"」）。
    _SHOP_ELITE_LOOKAHEAD_ROWS = 4

    def _resolve_solver_card_index(
        self,
        snapshot: dict[str, Any] | None,
        card_id: str | None,
        fallback_index: int | None,
    ) -> tuple[int | None, str]:
        """把 /solver/plan 推荐的卡（card_id）重新映射到实机手牌位置，避免位置索引漂移。

        与 mod 的 NekoAutoplayDriver.DecideCombatAsync 一致：优先按 card_id 精确匹配且可打出；
        匹配不到再退回 solver 给的位置索引（仅当该位置卡可打出）；都不行返回 (None, reason)。

        ★★ 2026-10-18 修（`_test_stale_index.py` 复现的**真实缺陷**）：
          旧实现只要 `fallback_index` 落在手牌范围内**且那张牌可打出**就直接返回它，
          **不管它是不是求解器建议的那张**。后果：
            * 求解器在 T0 建议 hand[0]=打击；执行时手牌漂移，hand[0] 已变成"痛击"；
            * `card_id` 匹配不到 ⇒ 回退 `fallback_index=0` ⇒ **打出"痛击"**；
            * 返回值非 None ⇒ 上层记为 `source="mod_solver"`、`一致=是`。
          ⇒ 也就是**「看起来按求解器打了，其实打的是另一张牌」**，
            而且完全无痕 —— 这正是"求解器算得对却没照打"最隐蔽的成因。
          修法：区分"mod 没给 card_id"（可安全回退位置）与"给了 card_id 但当前手牌里
          没有它"（**必须放弃**，交给启发式重算）。判据是 `card_id` 是否为空，
          而不是"能不能找到一个可打出的位置"。

        ★★ t43（2026-10-20）：返回值从 `int | None` 改为 `(index, reason)`。
          旧版匹配不到只返回 None，调用方只能写一句
          「求解器建议 X 但实机手牌里没有这张卡（手牌漂移）」，把两种成因压在一起：
            ① 卡**真的不在手牌**（已打出/弃掉/消耗）⇒ 计划过期，重算正确；
            ② 卡**在手牌里但 `playable=false`**（能量不够等）⇒ 计划没错，只是暂不可执行。
          实测当天 `[sts2_plan]` 重算 4548 次里 **1681 次（37%）** 报这一句而无法归因，
          且漂移 Top 全是最基础的打击/防御 ⇒ 必须先拆开才能判断是不是模拟器能量模型的问题。
          `reason` 为空串 ⇒ 成功取到 index。原因文本由 `loop_runner._explain_hand_card_state`
          统一生成（两处共用同一实现，避免再次漂移）。
        """
        hand: Any = []
        player: Any = {}
        if isinstance(snapshot, dict):
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
            player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        # ★ 惰性导入（模块级导入会与 loop_runner 形成循环依赖；本文件已有同样先例，见 :1746/:2067）
        from .loop_runner import _explain_hand_card_state

        if card_id:
            for i, card in enumerate(hand):
                if isinstance(card, dict) and str(card.get("card_id") or "") == str(card_id) and bool(card.get("playable", True)):
                    return i, ""
            # ★ mod 明确给了 card_id，但当前手牌里找不到（或不可打出）⇒ 位置索引已不可信，
            #   绝不能拿陈旧下标去蒙一张牌。交给启发式按当前局面重算。
            return None, _explain_hand_card_state(str(card_id), hand, player)
        # mod 没给 card_id（旧版 /solver/plan 或该步本来就只有位置）：位置索引是唯一线索，
        # 只要落点可打出就沿用。
        if fallback_index is not None and 0 <= fallback_index < len(hand):
            card = hand[fallback_index]
            if isinstance(card, dict) and bool(card.get("playable", True)):
                return fallback_index, ""
            energy = player.get("energy") if isinstance(player, dict) else None
            return None, (
                f"求解器给的手牌下标 {fallback_index} 当前不可打出（playable=false，"
                f"energy_cost={card.get('energy_cost', '?') if isinstance(card, dict) else '?'}，"
                f"当前能量={energy if energy is not None else '?'}）"
            )
        return None, ""

    def _shop_visit_signature(self, summary_context: dict[str, Any]) -> str:
        """这一次逛的是哪家店：换店（楼层变 / 货品集合变）就重新算"没看过"。

        只看楼层不够——同一层的两次商店（罕见但可能）会互相顶掉；带上货品身份最稳。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        ids: list[str] = []
        for field in ("shop_cards", "shop_relics", "shop_potions"):
            items = payload.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids.append(str(item.get("card_id") or item.get("relic_id") or item.get("potion_id") or item.get("index")))
        return f"{payload.get('floor')}|" + "|".join(ids)

    def _selection_can_confirm(self, summary_context: dict[str, Any]) -> bool:
        """选牌屏是否已经满足可确认条件。

        mod 只对战斗内手牌选择填 can_confirm（BuildSelectionPayload 取自 combat-hand 元数据），牌库/网格
        选择屏恒为 false。所以这里保守判断：can_confirm 为真 → 可确认；否则不主动确认（select_deck_card
        自己会在选够后点确认按钮），避免在没选够时白点一次。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return bool(payload.get("selection_can_confirm"))

    def _next_selection_option(self, summary_context: dict[str, Any], preferences: dict[str, Any], context: dict[str, Any]) -> int | None:
        """选牌屏要按的 option_index：多选时逐张推进，绝不重复按同一张。

        先按偏好（/solver/plan 的 choice_card_id -> 删牌启发式）；那张在这一屏已经按过就换下一张没按过的。
        全部按过仍无法推进时返回 None，交给调用方走确认/离开分支。已按过的集合以"选牌屏签名"为键，
        换屏或候选集合变化即重置（新的一屏从干净状态开始）。

        **只在"真的按了"之后才推进光标**（见 note_selection_option_pressed，由 service 在成功派发
        select_deck_card 后调用）。历史教训：这里曾经在**规划时**就 add 进 _selection_pressed，而
        plan() 是**每 tick 都被调用一次**的（事件流约 1 次/秒），自动玩步进却约 2.5 秒一次 ——
        于是四个候选在几秒内被"规划"白白耗光，屏幕上一个都没选中（selected_count 仍为 0），
        此后 plan() 永远返回 None，猫娘就**永久停在这一屏**不动了（玩家看到的"卡住"）。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if not cards:
            return self._preferred_choice_option(summary_context, preferences, context)

        signature = "|".join(
            str(card.get("card_id") or card.get("index")) for card in cards if isinstance(card, dict)
        )
        if signature != self._selection_cursor_key:
            self._selection_cursor_key = signature
            self._selection_pressed = set()
            self._selection_exhausted = 0

        count = len(cards)
        preferred = self._preferred_choice_option(summary_context, preferences, context)
        if preferred is not None and 0 <= preferred < count and preferred not in self._selection_pressed:
            return preferred
        for index in range(count):
            if index not in self._selection_pressed:
                return index
        # 这一屏的候选都按过了。
        self._selection_exhausted += 1
        if self._selection_exhausted >= self._SELECTION_EXHAUSTED_RETRIES:
            # 按遍了整屏却没换过屏（多半是一次都没真正落地）。此时若一直返回 None，这一屏就再也
            # 出不了动作 = 死锁；宁可重头再按一遍，也不让猫娘永久停手。
            self._debug(
                "[sts2_selection] 候选按遍仍未推进，重置光标重按 (key=%s pressed=%s)",
                signature,
                sorted(self._selection_pressed),
            )
            self._selection_pressed = set()
            self._selection_exhausted = 0
            return preferred if (preferred is not None and 0 <= preferred < count) else 0
        return None

    def note_selection_option_pressed(self, option_index: Any) -> None:
        """服务层在**真的把 select_deck_card 发出去**之后调用：把该下标记进"已按过"。

        规划与执行不是一一对应的（每 tick 都会规划，但只有少数 tick 会真正执行），
        所以光标必须由"执行"推动，而不是由"规划"推动。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._selection_pressed.add(index)

    #: 事件屏上"同一选项每多选一次"的递减惩罚（归一化后的百分点）。
    #:
    #: 归一化参考（`_normalize_percentile` 把整数分压到 0–100）：
    #:   扣上限(−60) vs 离开(0) 的差距 → 归一化后约 100 点
    #:   诅咒+遗物(−26) vs 离开(0) → 约 43 点
    #: 取 **40** 的含义：**第二次**选同一项就足以让"扣上限"输给"离开"，
    #: 从而在可重复选择的事件上主动止损；第一次选完全不受影响（不改变正常决策）。
    _EVENT_REPEAT_PENALTY_PER_TIME = 40.0

    def note_event_option_chosen(self, summary_context: dict[str, Any], option_index: Any) -> None:
        """服务层在**真的把 choose_event_option 发出去**之后调用：给该选项的"已选次数"加一。

        ★缺陷 A3 的修复入口。`_rank_event_options` 原本无状态，在可重复选择的事件上
        每轮算出同一个最优解 ⇒ 无限重复（实机后果：反复选"消耗生命上限"直到 HP 变 1）。

        与 `note_selection_option_pressed` 同样的理由：规划与执行不是一一对应
        （`plan()` 每 tick 都跑，约 1 次/秒；实际派发约 2.5 秒一次），
        所以计数必须由"执行"推动，不能由"规划"推动 —— 否则计数会被凭空放大。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._note_event_option_chosen(summary_context if isinstance(summary_context, dict) else {}, index)

    #: `selection.kind` 里表示"**变换**卡牌"的取值（来自 mod 的 `BuildSelectionPayload`，
    #: `GameStateService.cs:4250` `NDeckTransformSelectScreen => "deck_transform_select"`）。
    #:
    #: ★为什么必须区分：删牌屏与变换屏的**最优选择语义相反** ——
    #:   * 删牌屏：选诅咒 = **移除**它 ⇒ 最优（`_score_remove_card` 给诅咒 −100）
    #:   * 变换屏：选诅咒 = **变成另一张诅咒** ⇒ 最劣
    #: 游戏侧源码证实诅咒变换是零期望：`CardFactory.GetFilteredTransformationOptions` 里
    #: `if ((uint)(rarity - 8) > 1u)` 对 `Curse`(=9) 为**假**（9−8=1，不 >1）⇒ 跳过
    #: "只保留 Common/Uncommon/Rare" 的稀有度约束 ⇒ 候选池放宽到整个牌池，**可含其它诅咒**。
    #:
    #: 历史缺陷：`card_selection_transform` 与 `card_selection_remove` 在 `:839` 被放进**同一分支**，
    #: 于是变换屏复用了删牌启发式，**每轮都主动选诅咒**。实机采样里
    #: `CARD_SELECTION_TRANSFORM` 出现过 6 次（527 份样本）。
    _TRANSFORM_SELECTION_KINDS = frozenset({"deck_transform_select"})

    #: 变换屏的**屏幕名**（与 `_TRANSFORM_SELECTION_KINDS` 取并集，见 `_is_transform_selection`）。
    #:
    #: 实机证据：日志里 `screen=CARD_SELECTION_TRANSFORM` 真实出现过（527 份样本中 6 次）。
    #: 只认 `selection_kind` 时若那个字段缺失，判定会静默失效 ⇒ 变换屏重新开始选诅咒。
    _TRANSFORM_SCREEN_NAMES = frozenset({"card_selection_transform"})

    #: 变换屏上"诅咒/状态牌"的**变换优先级分**。
    #:
    #: 取值依据 `_score_remove_card` 的标尺（分越低越该被移出/换掉）：
    #:   诅咒 −100 / 状态 −90 / 打击 −60 / 防御 −50 / 普通牌 +10 / 好牌 +60~70
    #:
    #: 定在 **−55** 的含义：**排在"打击"(−60)之后、"防御"(−50)之前** ——
    #:   * 即：换掉一张基础打击比换掉诅咒**更值得**（收益确定：基础牌换掉大概率变强）
    #:   * 而诅咒仍排在防御与所有"有价值牌"之前 ⇒ 有诅咒时**不会**去换好牌
    #:
    #: 为什么**不**沿用删牌屏的 −100：删牌是"移出牌组"，选诅咒**一定能**移除它；
    #: 而变换诅咒的期望**接近零** —— 游戏侧 `CardFactory.GetFilteredTransformationOptions` 对
    #: `Curse`(=9) 跳过稀有度过滤（`9−8=1` 不 `>1`），候选池含其它诅咒 ⇒ 可能换回另一张诅咒。
    #: 所以变换屏**不该把诅咒当首选目标**。
    _TRANSFORM_CURSE_SCORE = -55

    def _selection_kind(self, summary_context: dict[str, Any]) -> str:
        """取 mod 给出的选牌屏细分类型（`payload.selection_kind`），取不到返回空串。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return str(payload.get("selection_kind") or "").strip().lower()

    def _state_name(self, summary_context: dict[str, Any]) -> str:
        """取**屏幕名**（`payload.state_name`）。

        ★为什么变换屏判定必须同时看它（实测发现的静默失效风险）：
        真实对局里变换屏的屏幕名是 `card_selection_transform`（实机日志里
        `screen=CARD_SELECTION_TRANSFORM` 确实出现过），而 `selection_kind` 取自
        mod 的 `selection.kind`。两者是**独立**来源，一旦 `selection_kind` 缺失/改名，
        只靠它的判定会**静默失效** —— 变换屏会退回去复用删牌启发式，
        也就是**每轮主动选诅咒**（正是缺陷 B 本身），而且不报任何错。

        我实测过这个场景（`.dsh-artifacts/probe_b_statename.py`）：
        只给 `state_name`、不给 `selection_kind` 时，旧实现 `is_transform=False` ⇒ 选诅咒。
        ⇒ 两个信号取**并集**，任一命中即认定为变换屏。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw = payload.get("state_name")
        if not raw:
            classification = summary_context.get("classification")
            if isinstance(classification, dict):
                raw = classification.get("state_name")
        return str(raw or "").strip().lower()

    def _is_transform_selection(self, summary_context: dict[str, Any]) -> bool:
        """是否**变换**屏。两个独立信号取并集（任一命中即可），避免静默失效。"""
        if self._selection_kind(summary_context) in self._TRANSFORM_SELECTION_KINDS:
            return True
        return self._state_name(summary_context) in self._TRANSFORM_SCREEN_NAMES

    def _preferred_transform_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """**变换**屏该选哪张牌：优先把手上**已有的诅咒/废牌**换掉，但**绝不主动新增诅咒**。

        与删牌屏的区别（这是本方法存在的全部理由）：
        * 删牌屏要"移出牌组" ⇒ 选最该走的牌（诅咒最优先）。
        * 变换屏要"换成别的牌" ⇒ 选**最没价值、且换掉后不会更差**的牌。
          诅咒虽然是废牌，但变换它**期望接近零**（游戏侧豁免了稀有度过滤，可能换回另一张诅咒），
          因此**诅咒的优先级低于"普通低价值牌"** —— 换掉一张废攻击牌的收益更确定。

        排序逻辑（分越低越该被变换）：
          1. 不可移除的牌（`_is_unremovable_card`）直接排除
          2. 诅咒 / 状态牌：给**中性偏低**分（可换，但不是首选）—— 避免"变诅咒"的零期望赌注
          3. 其它牌：沿用 `_score_remove_card`（分越低越是废牌）
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        best: tuple[tuple[int, int], int] | None = None
        scored_cards: list[tuple[int, str, bool]] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            if self._is_unremovable_card(card):
                continue
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            rarity = str(card.get("rarity") or "").strip().lower()
            card_type = str(card.get("card_type") or "").strip().lower()
            is_curse = rarity in {"curse"} or card_type in {"curse", "status"}
            if is_curse:
                # 可换，但不优先：变换诅咒的期望接近零（可能换回另一张诅咒）。
                # 用一个固定值把它排在"有明确价值的牌"之后、"完全没得选"之前。
                score = self._TRANSFORM_CURSE_SCORE
            else:
                score = self._score_remove_card(card)
            scored_cards.append((score, str(card.get("name") or card_index), is_curse))
            candidate = (score, -card_index)
            if best is None or candidate < best[0]:
                best = (candidate, card_index)
        self._log_transform_route(scored_cards, best[1] if best is not None else None)
        if best is not None:
            return best[1]
        return self._preferred_remove_option(summary_context, preferences)

    def _log_transform_route(
        self,
        scored_cards: list[tuple[int, str, bool]],
        chosen: int | None,
    ) -> None:
        """把变换屏决策写进日志（与 `[sts2_event_route]` / `[sts2_map_route]` 同风格）。

        ★为什么要加：变换屏此前**没有任何专属决策日志** ——
        事件屏我已补（`_log_event_route`），但变换屏仍是盲区。
        而缺陷 B 恰恰在变换屏上，没有日志就无法实机验证"是否还在选诅咒"
        （`CARD_SELECTION_TRANSFORM` 在历史日志里有 67 次真实出现，却查不到任何决策痕迹）。

        去重键取"候选与分数快照 + 最终选择"，同一屏静止时不刷屏。
        """
        if self._logger is None:
            return
        key = "|".join("%d:%s:%d" % (score, name, 1 if curse else 0)
                       for score, name, curse in scored_cards) + "#%s" % chosen
        if key == self._last_transform_route_log_key:
            return
        self._last_transform_route_log_key = key
        detail = " / ".join(
            "%s=%d%s" % (name[:16], score, "(诅咒)" if curse else "")
            for score, name, curse in sorted(scored_cards)
        )
        try:
            self._logger.info(
                "[sts2_transform_route] 候选=%d 选择=%s | %s",
                len(scored_cards),
                chosen,
                detail,
            )
        except Exception:
            # 日志绝不能影响决策路径。
            pass

    def _is_upgrade_selection(self, summary_context: dict[str, Any]) -> bool:
        """本屏是不是"升级卡牌"屏。

        判据优先级（**先看 mod 明确下发的 kind，再看名称兜底**）：
          1. `payload["selection_kind"]` —— mod `GameStateService.cs:4249` 对
             `NDeckUpgradeSelectScreen` 下发 `"deck_upgrade_select"`，最可靠；
          2. `payload["selection"]["kind"]` 原始结构（同上，双保险）；
          3. 卡牌名/描述里出现"升级"字样（兜底，避免 mod 改名后彻底失效）。
        ★ 绝不把"删牌屏"误判成升级屏：`card_selection_remove/delet` 先排除。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        kind = str(payload.get("selection_kind") or "").strip().lower()
        if not kind:
            sel = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
            kind = str(sel.get("kind") or "").strip().lower()
        if kind:
            # 删牌/变换/附魔各有自己的语义，明确排除。
            if kind in {"deck_card_select", "deck_transform_select", "deck_enchant_select",
                        "choose_card_select", "combat_hand_select"}:
                return False
            if "upgrade" in kind or "smith" in kind:
                return True
            # kind 明确但非升级 ⇒ 不猜。
            return False
        # kind 缺失（旧版 mod / 其它屏）：用提示语兜底。
        prompt = ""
        sel = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        prompt = str(sel.get("prompt") or "").strip().lower()
        return any(token in prompt for token in ("升级", "upgrade", "smith", "锻造"))

    def _preferred_upgrade_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """升级屏选哪张卡：**分数越高越该升**（与删牌刚好相反）。

        不是升级屏时返回 None，让调用方继续走删牌/变换分支。
        """
        if not self._is_upgrade_selection(summary_context):
            return None
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if not cards:
            return None
        best: tuple[int, int] | None = None
        for position, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            if self._item_blocked(card):
                continue
            score = self._score_upgrade_card(card)
            index = self._safe_int(card.get("index"))
            if index is None:
                index = position
            # 取 **max**（分越高越该升）；同分时取较小 index 以保证确定性。
            candidate = (score, -index)
            if best is None or candidate > best:
                best = candidate
        if best is None:
            return None
        return -best[1]

    def _preferred_choice_option(self, summary_context: dict[str, Any], preferences: dict[str, Any], context: dict[str, Any]) -> int | None:
        """选牌（消耗/拉弃牌堆/生成到手/变换等，弹的是人选牌界面）：优先用 /solver/plan 的 choice_card_id
        （solver 已优化选哪张），命中不到再走删牌启发式。避免卡死在选牌上。

        ★变换屏（`selection.kind == "deck_transform_select"`）**不走删牌启发式** ——
        两者最优语义相反，详见 `_preferred_transform_option` 的说明。
        """
        pending = context.get("pending_card_choice_id") if isinstance(context, dict) else None
        if pending:
            payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
            cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
            for i, card in enumerate(cards):
                if isinstance(card, dict) and str(card.get("card_id") or "") == str(pending):
                    idx = self._safe_int(card.get("index"))
                    return idx if idx is not None else i
        if self._is_transform_selection(summary_context):
            return self._preferred_transform_option(summary_context, preferences)
        # ★★ 升级屏必须**先于**删牌启发式分流（2026-10-18 实机缺陷）：
        #   升级屏与删牌屏的**最优方向相反**，共用 _preferred_remove_option 会
        #   变成"优先升级最该删的卡"（实测：优先看打击 7/13，打击+ 出现 35 次）。
        #   判据用 mod 下发的 `selection.kind`（GameStateService.cs:4249
        #   `NDeckUpgradeSelectScreen => "deck_upgrade_select"`），
        #   经 summary_context_builder._selection_counts 落到 payload["selection_kind"]。
        upgrade_option = self._preferred_upgrade_option(summary_context, preferences)
        if upgrade_option is not None:
            return upgrade_option
        # ★ 显式传 `strategy_context`：跨局教训（`strategy_directives.avoid`）在它里面，
        #   不在 `summary_context` 里（`service.py:549-557` 把两者作为兄弟键放进 snapshot）。
        #   不传 ⇒ 教训通道恒空 ⇒ 复盘学到的取向无法影响「该删哪张」。
        return self._preferred_remove_option(
            summary_context,
            preferences,
            context.get("strategy_context") if isinstance(context, dict) else None,
        )

    def _shop_affordable_items(self, summary_context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}

        def affordable(items: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("is_stocked", True) and item.get("enough_gold", True):
                    out.append(item)
            return out

        return (
            affordable(payload.get("shop_cards")),
            affordable(payload.get("shop_relics")),
            affordable(payload.get("shop_potions")),
        )

    # ---- 商店：统一评分池（牌组量化 × 覆盖度 × 商品量化）----------------------------------
    #
    # 规格 docs/shop_scoring.md。三个老入口（_preferred_shop_relic_index / _preferred_shop_potion_index
    # / _shop_best_card_index）在这里**收敛成一个** `_shop_preferred_purchase()`：卡 / 遗物 / 药水 /
    # 删牌进同一个池子比总分，取最高分；全部低于门槛就一分不花。
    # `_shop_best_card_index` 仍然保留（选牌屏/扰动排序还在用它），但商店采购不再走它。

    def _shop_preferred_purchase(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
        available_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """商店买什么：把可买的每一项打分，买**最高分**的；都不够高就什么都不买。

        算法（规格式伪代码）：

            可买分 = 补缺分(牌组缺口 − 遗物覆盖) × 情境加权 − 代价惩罚 − 重复惩罚
            药水分 = 药水缺口 × 精英加权 − 代价惩罚（栏位满则不评）
            遗物预算 = 金币 − 药水预留 − 底钱（不够就不买遗物）

        返回 ``None`` 表示"没什么可看的"（调用方照旧关门/推进）；返回带 ``action_type`` 的 dict 表示
        买它；返回只有 ``reason`` 的 dict 表示**评估过了但一分不花**（reason 会写进 PlannedOperation，
        玩家能在 last_decision_reason 里看到是 `shop_score_below_threshold`）。
        """
        run = self._shop_run_data(summary_context, snapshot)
        gold = run["gold"]
        floor = run["floor"]
        gaps = self._deck_gap_vector(run["deck"], current_hp=run["current_hp"], max_hp=run["max_hp"], floor=floor)
        coverage = self._relic_coverage(run["relics"])
        residual = {dim: max(0.0, gaps.get(dim, 0.0) - coverage.get(dim, 0.0)) for dim in SHOP_DIMENSIONS}
        elite_ahead = self._shop_elite_ahead(summary_context, snapshot)
        policy = self._shop_policy(preferences)
        base_threshold = self._shop_threshold(gold=gold, elite_ahead=elite_ahead, gaps=gaps, residual=residual, policy=policy)

        cards, relics, potions = self._shop_affordable_items(summary_context)
        occupied, slots = self._shop_potion_slots(run["potions"], potions)
        slots_full = slots > 0 and occupied >= slots
        owned_potion_categories = {self._potion_category(item) for item in run["potions"] if isinstance(item, dict) and item.get("potion_id")}
        needed_categories = set(SHOP_ELITE_POTION_NEEDS) if elite_ahead else {"defense", "burst", "aoe", "heal", "cleanse"}
        missing_categories = needed_categories - owned_potion_categories
        potion_boost = float(policy.get("elite_potion_boost", SHOP_ELITE_POTION_BOOST)) if elite_ahead else 1.0

        # 买遗物之前先扣掉"留给药水的钱"和"下一个商店的底钱"（规格「三、4」）。
        gold_floor = int(policy.get("gold_floor", SHOP_GOLD_FLOOR) or 0)
        cheapest_potion = min([self._safe_int(item.get("price")) or 0 for item in potions if isinstance(item, dict)] or [SHOP_POTION_PRICE_FALLBACK])
        potion_reserve = self._shop_potion_reserve(policy, elite_ahead=elite_ahead, empty_slots=max(0, slots - occupied), potion_price=cheapest_potion)
        if not potions and "potion_reserve_gold" not in policy:
            # 这家店**没有药水可买**：预留就没有意义（钱花不出去，下一次能不能碰到药水也不知道）。
            # 不为买不到的东西压着一笔钱，否则会白白错过大遗物。
            potion_reserve = 0
        relic_budget = gold - potion_reserve - gold_floor

        allowed = None
        if isinstance(available_actions, list):
            allowed = {str(action.get("type") or "").strip().lower() for action in available_actions if isinstance(action, dict)}
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(run["deck"])]

        entries: list[dict[str, Any]] = []
        skipped: list[str] = []

        def action_ok(action_type: str) -> bool:
            return allowed is None or action_type in allowed

        for relic in relics:
            index = self._safe_int(relic.get("index"))
            if index is None:
                continue
            if not action_ok("buy_relic"):
                continue
            if self._shop_relic_is_duplicate(relic, run["relics"]):
                # 重复惩罚：同名 / 同 relic_id **直接排除**（规格「二、通用部分」）。
                skipped.append(f"遗物「{self._shop_item_name(relic)}」→ 不买(原因=与已有遗物重复)")
                continue
            price = self._safe_int(relic.get("price")) or 0
            score, detail = self._score_shop_relic(relic, residual, gold=gold)
            entry = {
                "kind": "relic",
                "action_type": "buy_relic",
                "option_index": index,
                "name": self._shop_item_name(relic),
                "price": price,
                "score": score,
                "detail": detail,
                "blocked": "",
            }
            if price > relic_budget:
                # 规格「三、4」：遗物预算 = 金币 − 药水预留 − 底钱。不够就不买遗物。
                entry["blocked"] = (
                    f"留给药水 {potion_reserve} + 下一个商店的底钱 {gold_floor}：遗物预算={relic_budget} < 价格 {price}"
                )
            entries.append(entry)

        for card in cards:
            index = self._safe_int(card.get("index"))
            if index is None or not action_ok("buy_card"):
                continue
            price = self._safe_int(card.get("price")) or 0
            score, detail = self._score_shop_card(card, residual, gold=gold, deck_policy=deck_policy, archetype_tags=archetype_tags, deck=run["deck"])
            entries.append(
                {
                    "kind": "card",
                    "action_type": "buy_card",
                    "option_index": index,
                    "name": self._shop_item_name(card),
                    "price": price,
                    "score": score,
                    "detail": detail,
                    "blocked": "",
                }
            )

        if not slots_full:
            for potion in potions:
                index = self._safe_int(potion.get("index"))
                if index is None or not action_ok("buy_potion"):
                    continue
                price = self._safe_int(potion.get("price")) or 0
                score, detail = self._score_shop_potion(
                    potion,
                    missing_categories=missing_categories,
                    owned_categories=owned_potion_categories,
                    boost=potion_boost,
                    gold=gold,
                )
                entries.append(
                    {
                        "kind": "potion",
                        "action_type": "buy_potion",
                        "option_index": index,
                        "name": self._shop_item_name(potion),
                        "price": price,
                        "score": score,
                        "detail": detail,
                        "blocked": "",
                    }
                )
        else:
            skipped.append(f"药水栏位已满（{occupied}/{slots}）→ 整类不评")

        removal = self._shop_card_removal(summary_context)
        if removal is not None and action_ok("remove_card_at_shop"):
            price = self._safe_int(removal.get("price")) or 0
            score, detail = self._score_shop_removal(residual, gold=gold)
            entries.append(
                {
                    "kind": "remove",
                    "action_type": "remove_card_at_shop",
                    "option_index": None,
                    "name": "删牌服务",
                    "price": price,
                    "score": score,
                    "detail": detail,
                    "blocked": "",
                }
            )

        best = None
        # 代价惩罚统一在这里扣（规格：`分数 = 补缺分 × 情境加权 − 代价惩罚`）：每一件都按
        # `价格 / 当前金币` 扣，花掉一半以上的钱再重罚一次。放在一处算，免得四种商品各写一遍还写歪。
        for entry in entries:
            entry["penalty"] = self._shop_cost_penalty(int(entry.get("price") or 0), gold)
            entry["score"] = entry["score"] - entry["penalty"]
        for entry in entries:
            if entry["blocked"]:
                continue
            if best is None or entry["score"] > best["score"]:
                best = entry
        best_blocked = None
        for entry in entries:
            if not entry["blocked"]:
                continue
            if best_blocked is None or entry["score"] > best_blocked["score"]:
                best_blocked = entry

        threshold = self._entry_threshold(base_threshold, entry=best, elite_ahead=elite_ahead, policy=policy)
        decision: dict[str, Any] = {"action_type": None, "score": None, "threshold": threshold, "reason": "shop_close"}
        if best is None:
            if best_blocked is not None:
                decision.update(reason="shop_score_below_threshold", blocked=best_blocked["blocked"], score=best_blocked["score"])
            reason = str(decision["reason"])
        elif best["score"] >= threshold:
            decision.update(
                action_type=str(best["action_type"]),
                kwargs={"option_index": best["option_index"]} if best["option_index"] is not None else {},
                score=best["score"],
                kind=best["kind"],
                name=best["name"],
                confidence={"relic": 0.72, "card": 0.7, "potion": 0.66, "remove": 0.7}.get(str(best["kind"]), 0.7),
                reason="shop_scored_buy",
            )
            reason = "shop_scored_buy"
        else:
            decision.update(reason="shop_score_below_threshold", score=best["score"])
            reason = "shop_score_below_threshold"

        self._log_shop_decision(
            summary_context,
            entries=entries,
            skipped=skipped,
            best=best,
            best_blocked=best_blocked,
            threshold=threshold,
            base_threshold=base_threshold,
            policy=policy,
            gold=gold,
            elite_ahead=elite_ahead,
            relic_budget=relic_budget,
            potion_reserve=potion_reserve,
            missing_categories=missing_categories,
            reason=reason,
        )
        return decision

    def _shop_run_data(self, summary_context: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """商店决策要用的局面数据：金币 / 血量 / 层数 / 牌组 / 已有遗物 / 已有药水。

        为什么不能只读 `summary_context.payload`：`_build_shop_context` 里那份 `deck` 取的是
        `raw_state["deck"]`，而 mod 的 /state **没有顶层 deck** —— 牌组在 `run.deck[]` 里、已有遗物在
        `run.relics[]`（只有它带 description）、药水栏位在 `run.potions[]`（只有它带 occupied）。
        所以这里一律以 `raw_state.run.*` 为权威来源；payload 里那几项（gold/hp/floor）优先，因为它们
        已经在别的分支里用过、口径一致。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}

        def first_int(*values: Any) -> int:
            for value in values:
                parsed = self._safe_int(value)
                if parsed is not None:
                    return parsed
            return 0

        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        if not (deck.get("cards") if isinstance(deck.get("cards"), list) else []):
            deck = {"cards": run.get("deck")} if isinstance(run.get("deck"), list) else {}
        relics = payload.get("relics") if isinstance(payload.get("relics"), list) else []
        if not relics:
            relics = run.get("relics") if isinstance(run.get("relics"), list) else []
        potions = payload.get("potions") if isinstance(payload.get("potions"), list) else []
        if not potions:
            potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        return {
            "gold": first_int(payload.get("gold"), run.get("gold")),
            "current_hp": first_int(payload.get("current_hp"), run.get("current_hp")),
            "max_hp": first_int(payload.get("max_hp"), run.get("max_hp")),
            "floor": first_int(payload.get("floor"), run.get("floor"), snapshot.get("floor") if isinstance(snapshot, dict) else None),
            "deck": deck if isinstance(deck, dict) else {},
            "relics": [item for item in relics if isinstance(item, dict)],
            "potions": [item for item in potions if isinstance(item, dict)],
        }

    @staticmethod
    def _clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)

    @staticmethod
    def _shop_item_name(item: dict[str, Any]) -> str:
        for field in ("name", "card_id", "relic_id", "potion_id"):
            text = str(item.get(field) or "").strip()
            if text:
                return text
        return "?"

    @staticmethod
    def _item_text(item: dict[str, Any]) -> str:
        return " ".join(
            str(item.get(field) or "")
            for field in ("name", "card_id", "relic_id", "potion_id", "description", "rules_text", "resolved_rules_text", "effect", "usage", "card_type", "type")
        ).lower()

    # ---- 牌组量化（缺口向量）--------------------------------------------------------------

    def _deck_gap_vector(
        self,
        deck: dict[str, Any],
        *,
        current_hp: int = 0,
        max_hp: int = 0,
        floor: int = 0,
    ) -> dict[str, float]:
        """牌组量化 → 缺口向量（六维 0..1，越大越缺）。规格「一、牌组量化」那张表。

        六个维度：输出 / 格挡生存 / 抽牌循环 / 费用曲线 / 状态群伤 / 牌组质量。
        **全 0 表示"什么都不缺"**（那就别买）。量法都只用 mod 已给的 deck[] 字段
        （card_type / upgraded / energy_cost / dynamic_values / resolved_rules_text），不额外猜。

        为什么按"每回合能打出多少"而不是"牌组总伤害"：牌组越大总伤害越高，但一回合只抽 5 张、只打
        3 张 —— 用总量算会让"堆了一堆打击"的胖牌组显得很强。所以取**平均每张牌的数值 × 每回合出牌数**。
        """
        cards = [
            card
            for card in (deck.get("cards") if isinstance(deck, dict) and isinstance(deck.get("cards"), list) else [])
            if isinstance(card, dict)
        ]
        if not cards:
            # 读不到牌组就按"没缺口"处理：宁可这一家不买，也不要凭猜乱花钱（玩家的原话就是这个）。
            return {dim: 0.0 for dim in SHOP_DIMENSIONS}
        size = len(cards)
        damage_total = 0.0
        block_total = 0.0
        draw_cards = 0
        aoe_cards = 0
        status_cards = 0
        zero_cost = 0
        cost_sum = 0.0
        basic_unupgraded = 0
        curses = 0
        for card in cards:
            text = self._item_text(card)
            card_type = str(card.get("card_type") or card.get("type") or "").lower()
            damage_total += self._deck_card_damage(card)
            block_total += self._card_block(card)
            if any(token in text for token in _DECK_DRAW_TOKENS):
                draw_cards += 1
            if any(token in text for token in _DECK_AOE_TOKENS):
                aoe_cards += 1
            if any(token in text for token in _DECK_STATUS_TOKENS):
                status_cards += 1
            cost = self._card_energy_cost(card)
            cost_sum += cost
            if cost <= 0:
                zero_cost += 1
            name_text = f"{card.get('name') or ''} {card.get('card_id') or ''}".lower()
            if not bool(card.get("upgraded")) and any(token in name_text for token in _DECK_BASIC_CARD_TOKENS):
                basic_unupgraded += 1
            if any(token in card_type for token in _DECK_CURSE_TYPE_TOKENS):
                curses += 1

        per_turn_damage = (damage_total / size) * SHOP_CARDS_PER_TURN
        per_turn_block = (block_total / size) * SHOP_CARDS_PER_TURN
        target_damage = SHOP_TARGET_DAMAGE_BASE + SHOP_TARGET_DAMAGE_PER_FLOOR * max(0, floor)
        target_block = SHOP_TARGET_BLOCK_BASE + SHOP_TARGET_BLOCK_PER_FLOOR * max(0, floor)
        gap_output = self._clamp01((target_damage - per_turn_damage) / target_damage)
        gap_defense = self._clamp01((target_block - per_turn_block) / target_block)
        if max_hp > 0 and current_hp > 0 and (current_hp / max_hp) < 0.6:
            # 血量低且格挡薄 → 缺口高（规格那张表的判据）。
            gap_defense = min(1.0, gap_defense + 0.2)

        # 牌组 > 14 张才开始嫌循环不动；有抽牌手段就按张数抹平（每 2 张抽牌牌抹掉一整档）。
        gap_draw = self._clamp01((size - SHOP_DRAW_SIZE_BASE) / SHOP_DRAW_SIZE_SPAN)
        gap_draw *= 1.0 - min(1.0, draw_cards / SHOP_DRAW_CARD_CREDIT)

        avg_cost = cost_sum / size
        zero_share = zero_cost / size
        gap_energy = self._clamp01((avg_cost - 1.2) / 0.8)
        gap_energy *= 1.0 - 0.5 * min(1.0, zero_share / 0.25)

        # 状态/群伤：群伤算 1 张、单体状态算半张；这个维度封顶 0.7（规格说这种情境是"缺口中"）。
        status_units = aoe_cards + status_cards * SHOP_STATUS_CARD_CREDIT
        gap_aoe = 0.7 * self._clamp01(1.0 - status_units / SHOP_AOE_TARGET_UNITS)

        gap_quality = self._clamp01(
            0.5 * min(1.0, basic_unupgraded / SHOP_QUALITY_BASIC_TARGET)
            + 0.5 * min(1.0, curses / SHOP_QUALITY_CURSE_TARGET)
        )
        return {
            SHOP_DIM_OUTPUT: round(gap_output, 4),
            SHOP_DIM_DEFENSE: round(gap_defense, 4),
            SHOP_DIM_DRAW: round(gap_draw, 4),
            SHOP_DIM_ENERGY: round(gap_energy, 4),
            SHOP_DIM_AOE: round(gap_aoe, 4),
            SHOP_DIM_QUALITY: round(gap_quality, 4),
        }

    def _deck_card_damage(self, card: dict[str, Any]) -> float:
        """牌组里一张牌"一回合能打出多少伤害"（含多段：Damage × Repeat/hit_count）。"""
        damage = self._card_damage(card)
        if damage <= 0:
            return 0.0
        repeat = self._reward_dynamic_value(card, "Repeat")
        hits = repeat or self._safe_int(card.get("hit_count")) or 1
        return float(damage * max(1, hits))

    # ---- 已有遗物 / 已有药水的覆盖度 -------------------------------------------------------

    def _relic_effect_dims(self, relic: dict[str, Any]) -> set[str]:
        """遗物 → 它覆盖哪几维缺口（规格「一之二」的类别表：输出/防御/能量/抽牌/生存/经济/条件）。

        生存/回复归到「格挡/生存」这一维（牌组量化里它就叫这个名字）；经济类**没有**对应的缺口维度，
        所以不进这个集合（钱够时"加金币"不补任何缺口 —— 这也是玩家说的"评估一下那些是真正需要的"）。
        """
        text = self._item_text(relic)
        dims: set[str] = set()
        for dim, tokens in _RELIC_EFFECT_DIM_TOKENS.items():
            if any(token in text for token in tokens):
                dims.add(dim)
        if any(token in text for token in _RELIC_SURVIVAL_TOKENS):
            dims.add(SHOP_DIM_DEFENSE)
        return dims

    def _relic_coverage(self, relics: list[dict[str, Any]]) -> dict[str, float]:
        """已有遗物 → 覆盖度向量：同类累加但**边际递减** `覆盖度 = 1 − 0.6^n`（规格「一之二」）。

        为什么要边际递减：手里已经有"力量+2"时，再买一件加力量的遗物几乎没有边际收益 ——
        `1-0.6^1 = 0.4`（第 1 件几乎补满 40% 缺口）、`1-0.6^2 = 0.64`（第 2 件只补 24%）、
        第 3 件起基本无价值（0.784）。一件遗物横跨多类别就按类别**分摊**（半个计入每一类）。
        """
        counts: dict[str, float] = {dim: 0.0 for dim in SHOP_DIMENSIONS}
        for relic in relics:
            if not isinstance(relic, dict):
                continue
            dims = self._relic_effect_dims(relic)
            if not dims:
                continue
            share = 1.0 / len(dims)
            for dim in dims:
                counts[dim] += share
        return {dim: (0.0 if n <= 0 else round(1.0 - 0.6 ** n, 4)) for dim, n in counts.items()}

    def _shop_relic_is_duplicate(self, relic: dict[str, Any], owned: list[dict[str, Any]]) -> bool:
        """这把遗物是不是已经有一件了（同 relic_id 或同名）—— 重复惩罚是"直接排除"，不是降分。"""
        relic_id = str(relic.get("relic_id") or "").strip().lower()
        name = str(relic.get("name") or "").strip().lower()
        for item in owned:
            if not isinstance(item, dict):
                continue
            if relic_id and relic_id == str(item.get("relic_id") or "").strip().lower():
                return True
            if name and name == str(item.get("name") or "").strip().lower():
                return True
        return False

    def _potion_category(self, item: dict[str, Any]) -> str:
        """药水 → 用途类别（单体爆发 / 群伤 / 减伤 / 回血 / 清状态 / 抽牌 / 其它）。

        判定顺序是刻意的：`EXPLOSIVE_POTION`（对所有敌人造成伤害）必须落到"群伤"，不能被"伤害"先判成
        "单体爆发"；`BLOCK_POTION` 先于"回血"（它只给格挡）。
        """
        text = self._item_text(item)
        for tokens, category in (
            (_POTION_AOE_TOKENS, "aoe"),
            (_POTION_DEFENSE_TOKENS, "defense"),
            (_POTION_HEAL_TOKENS, "heal"),
            (_POTION_CLEANSE_TOKENS, "cleanse"),
            (_POTION_BURST_TOKENS, "burst"),
            (_POTION_DRAW_TOKENS, "draw"),
        ):
            if any(token in text for token in tokens):
                return category
        return "other"

    def _shop_potion_slots(self, run_potions: list[dict[str, Any]], shop_potions: list[dict[str, Any]]) -> tuple[int, int]:
        """(已占用, 总栏位)。

        规格里写的是 `shop.potions[].occupied`，但 mod 的 ShopPotionPayload **没有这个字段**
        （只有 index/potion_id/name/rarity/usage/price/is_stocked/enough_gold —— 而且栏位满时它直接把
        `enough_gold` 算成 false）。所以以 `run.potions[]`（每格都带 occupied/potion_id）为准，
        shop 那份只在 run 读不到时兜底；两边都读不到就返回 (0, 0) = "未知"，此时**不**因此拦下药水。
        """
        slots = [item for item in run_potions if isinstance(item, dict)]
        if slots:
            occupied = sum(1 for item in slots if item.get("occupied", bool(item.get("potion_id"))))
            return occupied, len(slots)
        shop_slots = [item for item in shop_potions if isinstance(item, dict) and "occupied" in item]
        if shop_slots:
            return sum(1 for item in shop_slots if item.get("occupied")), len(shop_slots)
        return 0, 0

    # ---- 情境与门槛 -----------------------------------------------------------------------

    def _shop_policy(self, preferences: dict[str, Any]) -> dict[str, Any]:
        """玩家自己配过的商店策略（`instruction_type == "shop_policy"`）。

        与 `route_policy` / `deck_policy` 一个路子：没配过就用模块常量的保守默认。
        可配键就是规格「五、配置项」那几个：score_threshold / elite_potion_boost /
        potion_reserve_gold / gold_floor（外加战斗用药水的 potion_hp_loss_threshold）。
        """
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "shop_policy":
                continue
            return value
        return {}

    def _shop_elite_ahead(self, summary_context: dict[str, Any], snapshot: dict[str, Any] | None = None) -> bool:
        """前方有没有精英（决定药水加权、门槛、遗物预算）。

        玩家口径：「买两瓶药可以冲精英」。麻烦在于**商店界面拿不到地图**：mod 的 `BuildMapPayload`
        一离开地图屏就返回 null，所以进店时 `raw_state.map` 是空的。于是这里按三级取：
        1. payload 里显式给了 `elite_ahead` / `route_elite_ahead`（策略/测试注入口）；
        2. 快照里恰有地图（选路那一刻、或以后 mod 改了行为）→ 现算；
        3. 否则用**选路时记下的结论**（`_note_route_choice`，由 evaluate_map_route 那一套算出来的）。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        for field in ("elite_ahead", "route_elite_ahead"):
            flag = payload.get(field)
            if isinstance(flag, bool):
                return flag
        graph = self._map_graph_from_snapshot(snapshot)
        if graph:
            raw_state = snapshot.get("raw_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict) else {}
            game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
            current = game_map.get("current_node") if isinstance(game_map.get("current_node"), dict) else None
            if current is not None:
                return self._route_elite_lookahead(graph, current)
        return bool(self._route_elite_ahead)

    def _route_elite_lookahead(self, graph: dict[str, Any], start: dict[str, Any]) -> bool:
        """从 `start` 这个节点往下看 `_SHOP_ELITE_LOOKAHEAD_ROWS` 层，路上有没有精英/Boss。

        复用 `_map_graph_index` / `_map_child_keys`（`evaluate_map_route` 那一套用的同一份图），
        所以坐标语义与链式递推完全一致：children 是 `{row,col}`，row 越大越靠近 boss。
        """
        index = self._map_graph_index(graph)
        if not index:
            return False
        row = self._safe_int(start.get("row"))
        col = self._safe_int(start.get("col"))
        if row is None or col is None:
            return False
        frontier: list[tuple[int, int]] = [(row, col)]
        seen: set[tuple[int, int]] = set()
        for _ in range(max(1, self._SHOP_ELITE_LOOKAHEAD_ROWS)):
            nxt: list[tuple[int, int]] = []
            for key in frontier:
                if key in seen:
                    continue
                seen.add(key)
                node = index.get(key)
                if node is None:
                    continue
                if self._map_node_weight_key(node) in {"elite", "boss"}:
                    return True
                nxt.extend(self._map_graph_child_keys_checked(index, node))
            frontier = nxt
            if not frontier:
                break
        return False

    def _map_graph_child_keys_checked(self, index: dict[tuple[int, int], dict[str, Any]], node: dict[str, Any]) -> list[tuple[int, int]]:
        return [key for key in self._map_child_keys(node) if key in index]

    def _note_route_choice(self, node: dict[str, Any] | None, graph: dict[str, Any] | None = None) -> None:
        """记下"我选的这条路前面有没有精英"。

        商店界面拿不到地图（见 `_shop_elite_ahead`），所以结论必须在**选路那一刻**算好存起来。
        同一个值还供战斗用药水判断"这是不是精英/Boss 战"（§八节流）。
        """
        node_type = self._map_node_weight_key(node) if isinstance(node, dict) else ""
        self._last_route_node_type = node_type
        if node_type in {"elite", "boss"}:
            self._route_elite_ahead = True
            return
        self._route_elite_ahead = self._route_elite_lookahead(graph, node) if (graph and isinstance(node, dict)) else False

    def _shop_threshold(
        self,
        *,
        gold: int,
        elite_ahead: bool,
        gaps: dict[str, float],
        residual: dict[str, float],
        policy: dict[str, Any],
    ) -> float:
        """统一门槛（随情境浮动，规格「三、3」）。

        - 前方有精英 → 药水门槛下调（只在药水那一项上减，见 `_entry_threshold`）；
        - 金币很多（>300）→ 门槛略降，避免"攒着不花"；
        - 金币很少（<100）→ 门槛升高，优先留钱；
        - **遗物覆盖度整体很高（构筑已成型）→ 门槛上调**，更倾向留钱到下一个商店。
        """
        try:
            threshold = float(policy.get("score_threshold", SHOP_SCORE_THRESHOLD))
        except (TypeError, ValueError):
            threshold = SHOP_SCORE_THRESHOLD
        if gold > SHOP_GOLD_RICH:
            threshold += SHOP_THRESHOLD_RICH_DELTA
        elif gold < SHOP_GOLD_POOR:
            threshold += SHOP_THRESHOLD_POOR_DELTA
        total_gap = sum(gaps.values())
        total_residual = sum(residual.values())
        if total_gap > 0 and (total_gap - total_residual) / total_gap >= SHOP_COVERAGE_MATURE_RATIO:
            threshold += SHOP_MATURE_THRESHOLD_DELTA
        return max(SHOP_THRESHOLD_MIN, min(SHOP_THRESHOLD_MAX, threshold))

    def _entry_threshold(self, base_threshold: float, *, entry: dict[str, Any] | None, elite_ahead: bool, policy: dict[str, Any]) -> float:
        """单项门槛：只有药水会在"前方有精英"时下调（规格「三、3」第一句）。"""
        if not elite_ahead or entry is None or entry.get("kind") != "potion":
            return base_threshold
        try:
            delta = float(policy.get("elite_threshold_delta", SHOP_ELITE_THRESHOLD_DELTA))
        except (TypeError, ValueError):
            delta = SHOP_ELITE_THRESHOLD_DELTA
        return max(SHOP_THRESHOLD_MIN, base_threshold + delta)

    def _shop_potion_reserve(self, policy: dict[str, Any], *, elite_ahead: bool, empty_slots: int, potion_price: int) -> int:
        """买遗物前要给"药水"预留多少钱（规格「五」：默认 冲精英时=补满栏位的钱）。"""
        configured = policy.get("potion_reserve_gold")
        parsed = self._safe_int(configured)
        if parsed is not None:
            return max(0, parsed)
        if not elite_ahead:
            return 0
        return max(0, empty_slots) * max(0, potion_price)

    @staticmethod
    def _shop_cost_penalty(price: int, gold: int) -> float:
        """代价惩罚：`价格 / 当前金币`（花掉一半以上的钱要重罚）。"""
        if gold <= 0:
            return SHOP_COST_PENALTY_SCALE * 2 + SHOP_COST_PENALTY_HALF_BONUS
        ratio = max(0.0, float(price) / float(gold))
        penalty = ratio * SHOP_COST_PENALTY_SCALE
        if ratio >= 0.5:
            penalty += SHOP_COST_PENALTY_HALF_BONUS
        return penalty

    # ---- 单件商品的补缺分 -----------------------------------------------------------------

    def _score_shop_relic(self, relic: dict[str, Any], residual: dict[str, float], *, gold: int) -> tuple[float, str]:
        """遗物：与缺口的匹配度 + 稀有度 + 关键词 − 条件苛刻降权（规格「二、分类细则」）。

        读不出效果类别时按"最缺那一维 × 0.5"折算：mod 的商店遗物 payload 只有
        `index/relic_id/name/rarity/price/is_stocked/enough_gold`（**没有 description**，只有已有遗物
        `run.relics[]` 才带 description），所以中文名字/英文 id 认不出来是常态。这个先验让"牌组真的
        缺东西"时还能买遗物，牌组没事时（缺口全 0）一律不买 —— 正是玩家要的那条线。
        折算只看**效果维度**（输出/格挡/抽牌/费用/群伤），不算"牌组质量"——遗物基本不会帮你升级或删牌。
        """
        text = self._item_text(relic)
        dims = self._relic_effect_dims(relic)
        if dims:
            matched = max(residual.get(dim, 0.0) for dim in dims)
            detail = "补缺=" + " ".join(f"{SHOP_DIM_LABELS[dim]}{residual.get(dim, 0.0):.2f}" for dim in sorted(dims))
        else:
            effect_gaps = [residual.get(dim, 0.0) for dim in SHOP_DIMENSIONS if dim != SHOP_DIM_QUALITY]
            matched = SHOP_UNKNOWN_RELIC_GAP_RATIO * max(effect_gaps or [0.0])
            detail = f"补缺={matched:.2f}(读不出效果类别→最缺维度×{SHOP_UNKNOWN_RELIC_GAP_RATIO})"
        rarity = str(relic.get("rarity") or "").strip().lower()
        rarity_bonus = SHOP_RELIC_RARITY_BONUS.get(rarity, 6.0)
        keyword_hits = sum(1 for token in _RELIC_STRONG_TOKENS if token in text)
        keyword_bonus = min(SHOP_RELIC_KEYWORD_BONUS_CAP, SHOP_RELIC_KEYWORD_BONUS * keyword_hits)
        harsh = any(token in text for token in _RELIC_HARSH_TOKENS)
        score = matched * 100.0 + rarity_bonus + keyword_bonus - (SHOP_RELIC_CONDITION_PENALTY if harsh else 0.0)
        detail += " 稀有%s=%+.0f" % (rarity or "?", rarity_bonus)
        if keyword_bonus:
            detail += " 关键词=%+.0f" % keyword_bonus
        if harsh:
            detail += " 条件苛刻=%+.0f" % (-SHOP_RELIC_CONDITION_PENALTY)
        return score, detail

    def _score_shop_card(
        self,
        card: dict[str, Any],
        residual: dict[str, float],
        *,
        gold: int,
        deck_policy: dict[str, Any],
        archetype_tags: list[str],
        deck: dict[str, Any],
    ) -> tuple[float, str]:
        """卡牌：沿用 `_score_reward_card` 的评分，接进统一池子（规格「二、分类细则」）。

        折算方式：原始分 / `_SHOP_CARD_FIT_SCALE`(60) = 补缺分，再乘"这张卡命中的那一维有多缺"
        （`0.25 + 0.75 × 缺口`）。没命中的牌（诅咒/状态，或读不出效果）缺口算 0 → 至多 0.25 倍，
        于是**牌组没缺口就不买卡**（规格「三、5」），而老口径"25 分以上就买"在便宜/真缺时仍然成立。
        """
        raw = float(self._score_reward_card(card, deck_policy, archetype_tags))
        # `_SHOP_ANY_CARD_SCORE` 的老语义是"原始分 1 以上就凑合买"；新口径里它成了**入库下限**：
        # 连 1 分都不到的卡（读不出效果、策略也不认）补缺分为 0，于是只剩代价惩罚 → 永远低于门槛。
        if raw < self._SHOP_ANY_CARD_SCORE:
            fit = 0.0
        else:
            fit = self._clamp01(raw / self._SHOP_CARD_FIT_SCALE) if self._SHOP_CARD_FIT_SCALE > 0 else 0.0
        dims = self._card_effect_dims(card)
        gap_fit = max([residual.get(dim, 0.0) for dim in dims], default=0.0)
        score = fit * (0.25 + 0.75 * gap_fit) * 100.0
        if dims:
            dim_label = "/".join(SHOP_DIM_LABELS[dim] for dim in sorted(dims))
            gap_detail = f"最高{gap_fit:.2f}({dim_label})"
        else:
            gap_detail = "未命中维度0.00"
        detail = f"补缺={gap_detail} 原始分={raw:.0f}"
        if self._is_duplicate_heavy_card(card, deck):
            # 卡牌与牌组里已有同名过多：降分（规格「二、通用部分」的重复惩罚）。
            score *= 0.5
            detail += " 同名过多×0.5"
        return score, detail

    def _card_effect_dims(self, card: dict[str, Any]) -> set[str]:
        """这张卡补的是哪几维（攻击→输出、格挡→格挡、抽牌→抽牌、能量→费用、群伤/状态→群伤/状态）。

        诅咒/状态牌**不补**任何维度（它们只会把牌组拖坏）——所以永远不该在商店里买。
        """
        text = self._item_text(card)
        card_type = str(card.get("card_type") or card.get("type") or "").lower()
        if any(token in card_type for token in _DECK_CURSE_TYPE_TOKENS):
            return set()
        dims: set[str] = set()
        if any(token in (card_type + " " + text) for token in _DECK_ATTACK_TYPE_TOKENS) or self._deck_card_damage(card) > 0:
            dims.add(SHOP_DIM_OUTPUT)
        if self._card_block(card) > 0 or any(token in text for token in ("block", "格挡", "护盾")):
            dims.add(SHOP_DIM_DEFENSE)
        if any(token in text for token in _DECK_DRAW_TOKENS):
            dims.add(SHOP_DIM_DRAW)
        if self._reward_dynamic_value(card, "Energy") > 0 or any(token in text for token in ("能量", "energy")):
            dims.add(SHOP_DIM_ENERGY)
        if any(token in text for token in _DECK_AOE_TOKENS) or any(token in text for token in _DECK_STATUS_TOKENS):
            dims.add(SHOP_DIM_AOE)
        return dims

    def _is_duplicate_heavy_card(self, card: dict[str, Any], deck: dict[str, Any]) -> bool:
        """牌组里同名牌是否已经很多（>=3 张）：重复惩罚（规格「卡牌与牌组里已有同名过多降分」）。"""
        name = str(card.get("name") or card.get("card_id") or "").strip().lower()
        if not name:
            return False
        cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        same = sum(
            1
            for item in cards
            if isinstance(item, dict) and str(item.get("name") or item.get("card_id") or "").strip().lower() == name
        )
        return same >= 3

    def _score_shop_potion(
        self,
        potion: dict[str, Any],
        *,
        missing_categories: set[str],
        owned_categories: set[str],
        boost: float,
        gold: int,
    ) -> tuple[float, str]:
        """药水：按"还缺哪几类"评分（规格「一之二」「二」）。

        - 命中"冲精英还缺的那一类" → 高分；**已经有的那一类** → 低分（栏位有限，重复买同一类不值）；
        - 前方有精英时整体加权上调（这就是"买两瓶药冲精英"）；
        - 栏位满**整类不评**（调用方在 `_shop_preferred_purchase` 里就跳过了）。
        """
        category = self._potion_category(potion)
        priority = _POTION_CATEGORY_PRIORITY.get(category, 0.3)
        if category in missing_categories:
            fit = SHOP_POTION_NEEDED_FIT * priority
            note = "还缺这一类"
        elif category in owned_categories:
            fit = SHOP_POTION_COVERED_FIT * priority
            note = "同类已有"
        else:
            fit = SHOP_POTION_BASELINE_FIT.get(category, 0.2)
            note = "非冲精英必需品"
        score = fit * 100.0 * max(0.1, boost)
        return score, f"用途={_POTION_CATEGORY_LABELS.get(category, category)}({note}) 补缺={fit:.2f} 情境={boost:.2f}"

    def _score_shop_removal(self, residual: dict[str, float], *, gold: int) -> tuple[float, str]:
        """删牌服务：分数来自"牌组质量"缺口（未升级的打击/防御、诅咒/状态牌的张数）。"""
        gap = residual.get(SHOP_DIM_QUALITY, 0.0)
        return gap * 100.0, f"补缺=质量{gap:.2f}"

    # ---- 日志 -----------------------------------------------------------------------------

    def _log_shop_decision(
        self,
        summary_context: dict[str, Any],
        *,
        entries: list[dict[str, Any]],
        skipped: list[str],
        best: dict[str, Any] | None,
        best_blocked: dict[str, Any] | None,
        threshold: float,
        base_threshold: float,
        policy: dict[str, Any],
        gold: int,
        elite_ahead: bool,
        relic_budget: int,
        potion_reserve: int,
        missing_categories: set[str],
        reason: str,
    ) -> None:
        """每件商品一行 + 一句结论（规格「四、日志与可观测」）。

        玩家口径要求"日志要说实话"：老实现给的 reason 是 `shop_high_value_relic`，而它其实只判断了
        "买得起"。这里把每项的分数构成、门槛、以及"没买时最高分是多少"都写清楚，方便事后调参。
        `_debug` 在本文件里是空函数，所以这些行必须走 info 级。
        """
        if self._logger is None:
            return
        kind_labels = {"relic": "遗物", "card": "卡牌", "potion": "药水", "remove": "删牌"}
        lines: list[str] = []
        for entry in entries:
            penalty = float(entry.get("penalty") or 0.0)
            own_threshold = self._entry_threshold(base_threshold, entry=entry, elite_ahead=elite_ahead, policy=policy)
            if entry["blocked"]:
                verdict = f"不买({entry['blocked']})"
            elif best is not None and entry is best and reason == "shop_scored_buy":
                verdict = "买"
            elif entry["score"] >= own_threshold:
                # 够门槛但不是最高分：**如实说**。（以前这里会被写成"低于门槛"，那是假日志。）
                verdict = f"不买(够门槛 {own_threshold:.1f}，但最高分是 {best['score']:.1f} 的{kind_labels.get(str(best['kind']), best['kind'])})"
            else:
                verdict = f"不买(低于门槛 {own_threshold:.1f})"
            lines.append(
                f"[sts2_shop] item={kind_labels.get(str(entry['kind']), str(entry['kind']))}「{entry['name']}」"
                f" score={entry['score']:.1f} ({entry['detail']} 价={entry['price']} 代价={-penalty:.1f}) → {verdict}"
            )
        lines.extend(f"[sts2_shop] {line}" for line in skipped)
        if best is not None and reason == "shop_scored_buy":
            lines.append(
                f"[sts2_shop] 最高分={best['score']:.1f}（{kind_labels.get(str(best['kind']), best['kind'])}「{best['name']}」）"
                f" ≥ 门槛 {threshold:.1f} → 买"
            )
        elif best is not None:
            lines.append(
                f"[sts2_shop] 最高分={best['score']:.1f}（{kind_labels.get(str(best['kind']), best['kind'])}「{best['name']}」）"
                f" < 门槛 {threshold:.1f} → 一分不花，钱留到下一个商店"
            )
        elif best_blocked is not None:
            lines.append(
                f"[sts2_shop] 最高分={best_blocked['score']:.1f} 但被预算拦下（{best_blocked['blocked']}）"
                f"：留给药水 {potion_reserve} + 底钱，遗物预算={relic_budget} → 一分不花"
            )
        else:
            lines.append("[sts2_shop] 没有可买项 → 一分不花")
        lines.append(
            f"[sts2_shop] 金币={gold} 门槛={threshold:.1f}(基础 {base_threshold:.1f}) 前方有精英={elite_ahead} "
            f"还缺药水类别={sorted(missing_categories) or '-'} 决定={reason}"
        )
        key = f"{self._shop_visit_signature(summary_context)}|" + "\n".join(lines)
        if key == self._shop_log_key:
            return
        self._shop_log_key = key
        try:
            for line in lines:
                self._logger.info(line)
        except Exception:
            pass

    def _shop_card_removal(self, summary_context: dict[str, Any]) -> dict[str, Any] | None:
        """删牌服务（`shop.card_removal`）：只有牌组质量缺口高才值得买。

        注意 `remove_card_at_shop` 动作**不接 option_index**；下一步的 `card_selection_remove` 屏由
        现有的 `_preferred_remove_option` 挑诅咒→打击/防御。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        shop = payload.get("shop") if isinstance(payload.get("shop"), dict) else {}
        removal = shop.get("card_removal") if isinstance(shop.get("card_removal"), dict) else None
        if removal is None:
            # 兜底：payload 里也可能把删牌服务铺平在顶层（`_build_shop_context` 一直在铺平别的字段）。
            flat = payload.get("card_removal")
            removal = flat if isinstance(flat, dict) else None
        if removal is None:
            return None
        if removal.get("used") is True or removal.get("available") is False:
            return None
        return removal

    def _shop_best_card_index(self, summary_context: dict[str, Any], preferences: dict[str, Any], min_score: int) -> int | None:
        """按 `_score_reward_card` 原始分取最好的卡（选牌屏/扰动排序仍在用）。

        商店采购**不再**走这个方法：那边走统一评分池 `_shop_preferred_purchase`。
        """
        cards, _relics, _potions = self._shop_affordable_items(summary_context)
        if not cards:
            return None
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        best: tuple[int, int] | None = None
        for card in cards:
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            if score < min_score:
                continue
            index = self._safe_int(card.get("index"))
            if index is None:
                index = self._safe_int(card.get("card_index"))
            if index is None:
                continue
            if best is None or score > best[0]:
                best = (score, index)
        return best[1] if best else None

    def _preferred_map_index(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        route_policy = self._route_policy(preferences)
        # 优先走**链式递推**：拿到整幕 DAG 就对每个候选评估"递归到 boss 的整条路线"。
        # 拿不到图（旧 mod / payload 里没有 map.nodes）才退回单步评分，行为与旧版一致。
        graph = self._map_graph_from_snapshot(snapshot)
        if graph:
            scored: list[tuple[int, float]] = []
            best_route: tuple[float, int] | None = None
            nodes_by_index: dict[int, dict[str, Any]] = {}
            for position, node in enumerate(travelable_nodes):
                if not isinstance(node, dict):
                    continue
                route_score = self.evaluate_map_route(
                    graph,
                    node,
                    route_policy=route_policy,
                    hp_ratio=hp_ratio,
                    gold=gold,
                )
                if route_score is None:
                    best_route = None
                    break
                node_index = self._safe_int(node.get("index"))
                if node_index is None:
                    node_index = position
                scored.append((node_index, route_score))
                nodes_by_index[node_index] = node
                candidate = (-route_score, node_index)
                if best_route is None or candidate < best_route:
                    best_route = candidate
            if best_route is not None:
                self._log_map_route(scored, best_route[1], hp_ratio=hp_ratio, gold=gold)
                # 顺手记下"这条路前面有没有精英/是不是精英房"：商店里拿不到地图（见 _shop_elite_ahead），
                # 战斗里也只有节点类型能告诉我们这是不是精英/Boss 战（见 in_elite_or_boss_combat）。
                self._note_route_choice(nodes_by_index.get(best_route[1]), graph)
                # 有图就一律取评估最优：**"每条路都不好"正是最需要认真挑的时候**，
                # 这时退回"随手取最左边"等于把评估结果扔了。
                return best_route[1]
        best_candidate: tuple[int, int] | None = None
        best_node: dict[str, Any] | None = None
        for index, node in enumerate(travelable_nodes):
            if not isinstance(node, dict):
                continue
            score = self._score_map_node(node, route_policy, hp_ratio, gold)
            node_index = self._safe_int(node.get("index"))
            if node_index is None:
                node_index = index
            candidate = (-score, node_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
                best_node = node
        if best_candidate is not None and best_candidate[0] < 0:
            self._note_route_choice(best_node, graph)
            return best_candidate[1]
        preferred = self._first_record_option(preferences)
        chosen = preferred if preferred is not None else self._first_available_map_index(summary_context)
        # 拿不到图也要记账（至少知道"选的是不是精英房"）；此时算不出"前方有没有精英"。
        for index, node in enumerate(travelable_nodes):
            if not isinstance(node, dict):
                continue
            node_index = self._safe_int(node.get("index"))
            if node_index is None:
                node_index = index
            if node_index == chosen:
                self._note_route_choice(node, graph)
                break
        return chosen

    def _log_rest_choice(self, summary_context: dict[str, Any], chosen: int) -> None:
        """把篝火的选择用 info 级记一次（同一决定只记一次）。

        玩家会直接质问"为什么又回血不锻造"，所以这里必须留痕：选项 id 列表 + 血量 + 选中的下标。
        """
        if self._logger is None:
            return
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        rest = payload.get("rest") if isinstance(payload.get("rest"), dict) else {}
        options = rest.get("options") if isinstance(rest.get("options"), list) else []
        ids = [str(item.get("option_id") or item.get("title") or "?") for item in options if isinstance(item, dict)]
        key = f"{chosen}|{'/'.join(ids)}"
        if key == self._rest_choice_log_key:
            return
        self._rest_choice_log_key = key
        try:
            self._logger.info(
                "[sts2_rest] 选项=%s 血=%s/%s -> 选 option_index=%s",
                ids,
                payload.get("current_hp"),
                payload.get("max_hp"),
                chosen,
            )
        except Exception:
            pass

    def _preferred_rest_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """篝火选哪一项：**默认优先锻造（升级卡牌）**，血低才回血。

        玩家口径：「插件偏爱休息回血，而不是锻造卡牌。锻造可以升级卡牌、提升数值、降低费用，
        对战力提升很大，是需要优先思考的」。原来的实现是

            preferred_option = self._preferred_route_option(preferences) or 0

        —— 要么用**路由**偏好（跟篝火选项没关系），要么直接取下标 0，也就是"列表第一项"，
        而列表第一项通常就是回血。所以插件事实上永远在回血。

        现在的口径：

        1. 玩家显式配过的偏好优先（他说话算数）；
        2. 血比 < `REST_HEAL_HP_RATIO` → 回血优先（快死了还去锻造就是送）；
        3. 否则**锻造/升级优先**（升数值、降费用，战力涨得比几十点血值）；
        4. 认不出类型就取第一个**可用**选项。

        选项类型靠 `option_id` / `title` / `description` 的关键词判断 —— mod 发的是 `option_id`
        （见 GameStateService 的 RestOptionPayload），不是 `type`/`name`。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        rest = payload.get("rest") if isinstance(payload.get("rest"), dict) else {}
        options = [item for item in (rest.get("options") if isinstance(rest.get("options"), list) else []) if isinstance(item, dict)]
        if not options:
            return None

        def option_index(position: int, item: dict[str, Any]) -> int:
            index = self._safe_int(item.get("index"))
            return index if index is not None else position

        # 1) 玩家显式偏好（route/rest preference）—— 只要落在范围内就听他的。
        explicit = self._preferred_route_option(preferences)
        if explicit is not None and any(option_index(position, item) == explicit for position, item in enumerate(options)):
            return explicit

        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 1.0

        def kind(item: dict[str, Any]) -> str:
            text = " ".join(
                str(item.get(key) or "") for key in ("option_id", "title", "description", "name", "type")
            ).lower()
            if any(token in text for token in _REST_SMITH_TOKENS):
                return "smith"
            if any(token in text for token in _REST_HEAL_TOKENS):
                return "heal"
            return "other"

        usable = [(position, item) for position, item in enumerate(options) if bool(item.get("is_enabled", True))]
        pool = usable or list(enumerate(options))
        wanted = "heal" if hp_ratio < REST_HEAL_HP_RATIO else "smith"
        for position, item in pool:
            if kind(item) == wanted:
                return option_index(position, item)
        for position, item in pool:
            if kind(item) == "other":
                return option_index(position, item)
        return option_index(*pool[0])

    def _reward_kwargs(self, summary_context: dict[str, Any], action: dict[str, Any], preferred_option: int | None) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "claim_reward":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        if action_type == "choose_reward_card":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        return {}

    def _guidance_override(self, context: dict[str, Any]) -> dict[str, bool]:
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        return self._guidance_override_from_payload(summary_context)

    def _guidance_override_from_payload(self, summary_context: dict[str, Any]) -> dict[str, bool]:
        decision_payload = summary_context.get("decision_payload") if isinstance(summary_context.get("decision_payload"), dict) else {}
        instructions = decision_payload.get("instructions") if isinstance(decision_payload.get("instructions"), list) else []
        guidance_lines = [
            str(item.get("content") or "")
            for item in instructions
            if isinstance(item, dict) and str(item.get("source") or "") == "neko_guidance"
        ]
        merged = " ".join(guidance_lines).lower()
        if not merged:
            guidance = decision_payload.get("guidance") if isinstance(decision_payload.get("guidance"), dict) else {}
            pending = guidance.get("pending") if isinstance(guidance.get("pending"), list) else []
            merged = " ".join(str(item.get("content") or "") for item in pending if isinstance(item, dict)).lower()
        if not merged:
            return {}
        prefer_defense = any(token in merged for token in ["先防", "优先防", "保血", "保命", "别贪", "不要贪", "求稳", "稳一点"])
        prefer_attack = any(token in merged for token in ["优先输出", "抢伤害", "压血", "收掉", "收尾", "斩杀"])
        return {
            "prefer_defense": prefer_defense,
            "prefer_attack": prefer_attack,
            "avoid_greed": any(token in merged for token in ["别贪", "不要贪"]),
            "prefer_lethal": any(token in merged for token in ["斩杀", "收掉", "收尾"]),
            "conservative": prefer_defense or any(token in merged for token in ["求稳", "稳一点"]),
        }




    def _preferred_combat_target_index(self, snapshot: dict[str, Any], card_index: int) -> int | None:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        for card in hand:
            if not isinstance(card, dict):
                continue
            if self._safe_int(card.get("index")) != card_index:
                continue
            valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
            if not valid_target_indices:
                return None
            try:
                return int(valid_target_indices[0])
            except Exception:
                return None
        return None

    def _first_matching_playable_card(self, hand: list[dict[str, Any]], keywords: list[str]) -> int | None:
        normalized_keywords = [keyword.lower() for keyword in keywords]
        for card in hand:
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            haystack = " ".join(
                [
                    str(card.get("name") or ""),
                    str(card.get("id") or ""),
                    str(card.get("description") or ""),
                    str(card.get("effect") or ""),
                    str(card.get("card_type") or ""),
                ]
            ).lower()
            if any(keyword in haystack for keyword in normalized_keywords):
                return self._safe_int(card.get("index"))
        return None


    def _find_labeled_lethal_sequence(self, cards: list[dict[str, Any]], current_energy: int, enemies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for enemy_index, enemy in enumerate(enemies):
            if not isinstance(enemy, dict):
                continue
            enemy_hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            if enemy_hp <= 0:
                continue
            lethal_cards = self._find_lethal_card_sequence(cards, current_energy, enemy_hp)
            if not lethal_cards:
                continue
            return [
                {
                    "card_index": self._safe_int(card.get("index")) or 0,
                    "target_index": self._lethal_target_index(card, enemy_index),
                    "reason": "combat_lethal_sequence",
                }
                for card in lethal_cards
            ]
        return []

    def _best_defense_card(self, cards: list[dict[str, Any]], current_energy: int) -> dict[str, Any] | None:
        candidates = [card for card in cards if self._card_energy_cost(card) <= current_energy and self._card_block(card) > 0]
        if not candidates:
            return None
        return max(candidates, key=self._card_block)

    def _best_attack_card(self, cards: list[dict[str, Any]], current_energy: int) -> dict[str, Any] | None:
        candidates = [card for card in cards if self._card_energy_cost(card) <= current_energy and self._card_damage(card) > 0]
        if not candidates:
            return None
        return max(candidates, key=self._card_damage)

    def _preferred_target_for_card(self, card: dict[str, Any], enemies: list[dict[str, Any]]) -> int | None:
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            return None
        damage = self._card_damage(card)
        best_target = None
        best_priority: tuple[int, int, int] | None = None
        for target_index in valid_target_indices:
            try:
                normalized_index = int(target_index)
            except Exception:
                continue
            if normalized_index >= len(enemies) or normalized_index < 0:
                continue
            enemy = enemies[normalized_index]
            if not isinstance(enemy, dict):
                continue
            hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            incoming = self._safe_int(enemy.get("intent_damage")) or 0
            lethal = 1 if damage > 0 and hp <= damage else 0
            priority = (lethal, incoming, -hp)
            if best_priority is None or priority > best_priority:
                best_priority = priority
                best_target = normalized_index
        return best_target if best_target is not None else self._lethal_target_index(card, 0)

    def _card_block(self, card: dict[str, Any]) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == "block":
                parsed = self._safe_int(item.get("current_value"))
                if parsed is not None:
                    return parsed
        block = self._safe_int(card.get("block"))
        if block is not None:
            return block
        effect_text = " ".join(
            [
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
            ]
        )
        lowered = effect_text.lower()
        if "block" not in lowered and "护盾" not in effect_text and "格挡" not in effect_text:
            return 0
        for token in effect_text.replace("Gain", " ").replace("Block", " ").split():
            parsed = self._safe_int(token)
            if parsed is not None:
                return parsed
        match = re.search(r"获得\s*(\d+)\s*点?(?:格挡|护盾|护甲)", effect_text)
        if match:
            return int(match.group(1))
        return 0

    def _is_unremovable_card(self, card: dict[str, Any]) -> bool:
        if bool(card.get("unremovable")) or bool(card.get("cannot_remove")):
            return True
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
            ]
        ).lower()
        return "eternal" in text or "永恒" in text

    def _score_remove_card(self, card: dict[str, Any]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
                str(card.get("rarity") or ""),
            ]
        ).lower()
        if any(token in text for token in ["curse", "诅咒"]):
            return -100
        if any(token in text for token in ["status", "状态"]):
            return -90
        if any(token in text for token in ["strike", "打击"]):
            return -60
        if any(token in text for token in ["defend", "防御"]):
            return -50
        if any(token in text for token in ["draw", "skim", "coolheaded", "抽牌", "过牌"]):
            return 60
        if any(token in text for token in ["energy", "turbo", "double energy", "能量"]):
            return 70
        if any(token in text for token in ["focus", "orb", "电球", "充能"]):
            return 65
        return 10

    def _route_policy(self, preferences: dict[str, Any]) -> dict[str, Any]:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "route_policy":
                continue
            return value
        return {}

    def _score_event_option(self, option: dict[str, Any], hp_ratio: float, gold: int) -> int:
        # ★★ D11（t28）：**被锁选项绝对不可选** —— 必须在一切加分之前短路返回。
        #
        #   实机缺陷（2026-10-20 00:24:24 起，run 6W6FGS6W4TPS / ENDLESS_CONVEYOR / floor=14）：
        #     `/state` 的 `event.options` 逐字带了 `is_locked`：
        #       options[0] {title:破产了, is_locked:true }   ← 金币 18 不足支付
        #       options[1] {title:离开,   is_locked:false}
        #     但本函数只经 `_event_option_text()` 做**文本子串**匹配，而 `is_locked` 是**布尔字段**、
        #     不在文本里 ⇒ 结构上不可能被感知 ⇒ 给被锁项 **+10**、给可选项 **0**：
        #       被锁项文本「你还能吃得下更多，但是兜里没金币了。」命中 `"金币"` ⇒ +10（见下方 :4356 规则）
        #       ★ 即：**因为提到了金币，就给一个付不起钱的选项加分**。
        #     然后游戏 99 次回 `invalid_target: The selected event option is locked.`，
        #     而该错误在 EVENT 屏上"重算"仍得同一结论 ⇒ 永久卡死。
        #
        #   ★ 为什么必须**短路**而不是"减分"：当前可选选项的合理最高分约
        #     遗物14 + 金币10 + 卡牌8 + 回血18 ≈ 50；若只给被锁项 −60，则 50 > −60
        #     ⇒ 仍会选被锁的 ⇒ 修不好。只有让被锁项**低于任何可能总分**才能保证绝不选中。
        #
        #   ★ 为什么是 −1000：任一单项正向加分 ≤ +70；即便所有正向规则全部命中，
        #     总和也远小于 1000。且 `_normalize_percentile` 只做**相对**归一，
        #     哨兵值保证被锁项恒排末位。
        #
        #   ★ 不误伤（三条）：
        #     ① 全部选项未锁 ⇒ 本分支永不进入 ⇒ 打分**逐位不变**；
        #     ② 字段**缺失**（旧 mod / 测试替身）⇒ `is_locked=None` ⇒ 视为**未锁**（保守）；
        #     ③ 只认**真值**（True 或 "true"/"1" 这类显式真），不把 0/""/None 当已锁。
        if self._event_option_is_locked(option):
            return self._EVENT_LOCKED_OPTION_SCORE
        text = self._event_option_text(option)
        score = 0
        # ★生命上限损失：**永久不可恢复**，单独且**最重**地惩罚。
        #   必须先于普通掉血判断，否则会被 `"hp" in text` 之类的宽泛匹配吞掉。
        #   实机教训：`"失去 5 点生命上限，获得 100 金币"` 原先得 **+10**（金币 +10 盖过一切），
        #   于是脚本每轮都选它，把 max_hp 从 80 一路扣到 54；可重复选择的事件上会一直选到 HP 变 1。
        #   惩罚量级取 60 —— 高于任何单项正向收益（遗物 +14、删牌 +12、金币 +10），
        #   使"任何数量的即时收益都不足以换一次永久上限损失"。
        if self._option_loses_max_hp(text):
            score -= 60
        # ★普通掉血：必须用 `_option_loses_hp`（它已判方向），**不能**用下面的裸子串匹配。
        #   实机抓到的缺陷：原实现是 `any(token in text for token in ["lose hp", "hp", ...])`，
        #   而裸 `"hp"` 子串会命中 `"Gain 5 Max HP"` ⇒ 把**加最大生命**也罚了 −8。
        #   中文侧同理（"获得11点最大生命值"里的"生命"）。
        if self._option_loses_hp(text):
            # ★量级必须**高于**普通战利品，否则"掉血换药水"会被奖励盖过。
            #   实机（EVENT#3，血比 0.73）：「失去8点生命。获得1瓶随机药水。」
            #   若掉血只罚 8，而药水给 +14 ⇒ 净 **+6** ⇒ 会**主动选掉血**，
            #   与"低血时不拿命换物资"的意图相反。
            #   故提高到 24/12：任一常见战利品（药水14 / 金币10 / 卡牌8）都无法单独抵消。
            score -= 24 if hp_ratio < 0.6 else 12
        # ★"掉血换物资"必须**净负**：即使两种战利品同时给（药水14 + 金币10 = 24），
        #   在血比 ≥0.6 时 24 − 12 = **+12** 仍是正分 ⇒ 会主动拿命换。
        #   实测确认过这个漏洞（血比0.73: 掉血8+药水14 → 净 +2）。
        #   这里再加一道**乘法抑制**：掉血时把所有正向收益减半 ——
        #   在低血时干脆全抹掉（拿命换物资是最该避免的），高血时也只拿一半好处。
        #   ★必须在所有加分规则**之后**执行，所以这里只记标记，末尾统一处理。
        loses_hp = self._option_loses_hp(text) or self._option_loses_max_hp(text)
        # ★加上限（与扣上限相反方向）：永久提升容错空间，必须给**正分**。
        #   实机：「获得[blue]11[/blue]点最大生命值。」—— 修复方向判定后不再被罚，
        #   但此前也没有任何规则给它**加分** ⇒ 落到 0 分，与"什么都不发生"等价。
        #   量级取 +16：低于扣上限的 −60（不对称是故意的：永久损失比永久收益更该避），
        #   但高于单次掉血、与遗物(14)同档。
        if self._option_gains_max_hp(text):
            score += 16
        if any(token in text for token in ["gain gold", "gold", "获得金币", "金币"]):
            score += 10 if gold < 150 else 4
        # ★遗物/道具：游戏文案里不叫"遗物"，而是**具体道具名**（如"藏宝图"）。
        #   实机：「获得[gold]藏宝图[/gold]。」原先得 **0 分** —— 一个遗物级道具
        #   与"什么都不给"同分。除字面词外，加入两个高频形态：
        #   * 「获得X」+ 无其它类别词 ⇒ 视为道具（保守：这类事件选项几乎都是给东西）
        #   * 药水/宝物类关键词
        if any(token in text for token in ["relic", "遗物", "宝物", "宝图", "药水", "potion"]):
            score += 14
        # ★卡牌：实机文案是"稀有牌"/"普通牌"/"卡牌包"，**不含**字面"卡牌"二字
        #   （「获得一张随机[gold]稀有牌[/gold]。」原先得 0 分）。
        #   故补齐"X牌"这一族（稀有牌/罕见牌/普通牌/诅咒牌/升级牌…）。
        if any(token in text for token in ["card", "卡牌", "获得卡", "稀有牌", "普通牌", "罕见牌", "牌包"]):
            score += 8
        if any(token in text for token in ["remove", "删除", "移除"]):
            score += 12
        # ★回血：量随血量而定 —— 低血时是救命的，满血时几乎无价值。
        #   实机：「回复[green]10[/green]点生命。」原先得 **0 分**
        #   （血比 0.81 时它确实价值不高，但 0 分让它与"什么都没发生"等价；
        #   血比 0.30 时它却依然是 0 分 —— 这才是真问题）。
        if re.search(r"(回复|恢复|治疗|heal|restore)", text) and re.search(r"(生命|血量|hp|health)", text):
            score += 18 if hp_ratio < 0.5 else (8 if hp_ratio < 0.8 else 2)
        if any(token in text for token in ["curse", "诅咒", "愧疚", "笨拙", "晕眩", "伤口"]):
            # ★诅咒在"卡牌质量"维度是**零/负期望**：诅咒变换时被游戏豁免了稀有度过滤
            #   （`CardFactory.GetFilteredTransformationOptions`：`(uint)(rarity-8) > 1u` 对
            #   Curse(9) 为假 ⇒ 跳过"只保留 Common/Uncommon/Rare"的约束），
            #   候选池可含其它诅咒 ⇒「诅咒只会变成另一种诅咒」。
            #   因此惩罚必须**高于遗物(+14)与卡牌(+8)之和**，不能被"有得有失"的选项抵消。
            #   ★补入具体诅咒卡名：实机文案写的是"愧疚"/"笨拙"，不写"诅咒"二字
            #   （「将1张[red]笨拙[/red]」「并将1张[red]愧疚[/red]」原先都**逃过惩罚**）。
            score -= 40
        # ★★"掉血换物资"必须**净负**（最后一道闸门）。
        #   为什么需要它：上面各条正向规则是**独立相加**的，所以"给得多"可以盖过掉血：
        #   实测（血比 0.73）「失去8点生命。获得1瓶随机药水。」= −12 +14 = **+2** ⇒
        #   仍会**主动拿命换药水**。同理若同时给金币与卡牌，净分更高。
        #   ⇒ 掉血/扣上限的选项，其正向收益一律**减半并再减 1**，保证**恒为负**；
        #   血比 <0.6 时**全部抹掉**（低血时拿命换物资是本项目最该避免的决策）。
        #   实测：只减半时血比 0.73 的「掉血8+药水14」= −12 +7 = **+1** 仍为正 ⇒
        #   故 `//2 - 1` 把上界钉死在 **≤ −1**（"绝不主动拿命换"是硬约束，不是偏好）。
        #   注意：惩罚项（诅咒 −40）保留原样，只压缩**收益**。
        if loses_hp and score > 0:
            score = 0 if hp_ratio < 0.6 else min(score // 2 - 1, -1)
        return score

    @staticmethod
    def _event_option_is_locked(option: dict[str, Any]) -> bool:
        """事件选项是否被游戏**锁定**（`/state` 的 `event.options[].is_locked`）。

        ★ 实机字段（2026-10-20，ENDLESS_CONVEYOR，逐字取自 `/state`）：
            {"index": 0, "title": "破产了", "is_locked": true,  ...}   ← 金币不足支付
            {"index": 1, "title": "离开",   "is_locked": false, ...}
          字段名确为 `is_locked`（实测 `option` 键 =
          `['index','text_key','title','description','is_locked','is_proceed',
            'will_kill_player','has_relic_preview']`）。

        ★ 判据做**窄**（保守），避免误伤：
          · 只认真布尔 `True`，以及显式真值串（"true"/"1"/"yes"/"locked"）；
          · 字段缺失 / `None` / `False` / `0` / `""` ⇒ **未锁**。
          ⇒ 旧版 mod 或测试替身不带该字段时，行为与修复前**完全一致**。
        """
        if not isinstance(option, dict):
            return False
        raw = option.get("is_locked")
        if raw is True:
            return True
        if isinstance(raw, str):
            return raw.strip().lower() in {"true", "1", "yes", "locked"}
        return False

    @staticmethod
    def _option_gains_max_hp(text: str) -> bool:
        """事件选项是否**永久提升生命上限**（`_option_loses_max_hp` 的反方向）。

        与"回复生命"区分：回复是即时治疗（可被消耗掉），提升上限是永久容错空间。

        实机依据：「获得[blue]11[/blue]点最大生命值。」/「获得[green]2[/green]点最大生命值。」
        —— 两者在修复方向判定后都变成 **0 分**，即"给了永久上限提升"与"什么都没发生"同分。
        """
        t = STS2HeuristicPlanner._strip_rich_text(text).lower()
        mentions = (
            re.search(r"(生命|血量|最大)\s*(值)?\s*(上限|最大值)", t) is not None
            or re.search(r"最大生命", t) is not None
            or re.search(r"(max|maximum)\s*\.?\s*(hp|health)", t) is not None
        )
        if not mentions:
            return False
        gain = re.search(r"(获得|得到|增加|提升|提高|gain|increase|\+\s*\d)", t) is not None
        loss = re.search(r"(失去|扣|减少|降低|lose|reduce|-\s*\d)", t) is not None
        # 有得有失时以**损失**为准（与 `_option_loses_max_hp` 保持同一口径）。
        return gain and not loss

    def _event_option_text(self, option: dict[str, Any]) -> str:
        """事件选项的归一化文本：拼接三个来源字段 + 转小写 + **剥富文本标签**。

        ★这是事件文案匹配的**唯一入口**，因此富文本剥离放在这里 ——
        所有下游判断（`_option_loses_max_hp` / `_option_loses_hp` / 金币/遗物/诅咒加分）
        都会自动拿到干净文本，不必各自容忍标签。

        实机原因见 `_strip_rich_text`：游戏文案是 ``失去[red]8[/red]点生命``，
        标签夹在动词与数字之间会让 `失去\\s*\\d*` 这类正则整体失效。
        """
        return STS2HeuristicPlanner._strip_rich_text(
            " ".join(
                [
                    str(option.get("label") or ""),
                    str(option.get("text") or ""),
                    str(option.get("description") or ""),
                ]
            )
        ).lower()

    def _score_map_node(self, node: dict[str, Any], route_policy: dict[str, Any], hp_ratio: float, gold: int) -> int:
        node_type = self._map_node_weight_key(node)
        weights = self._route_effective_weights(route_policy, hp_ratio, gold)
        return int(weights.get(node_type, 0))

    def _route_effective_weights(self, route_policy: dict[str, Any], hp_ratio: float, gold: int) -> dict[str, float]:
        """把"内置打底 -> 玩家配置 -> 低血替换"整理成一张明确的权重表。

        优先级：内置保守表 < 玩家的 route_policy.weights；血量低于阈值时整张表换成低血表
        （玩家配了 conditions.low_hp.weights 就用玩家的，否则用内置 SAFE_MAP_LOW_HP_WEIGHTS）。

        注意：低血替换现在对**自动选路**也生效了。以前只有玩家手点的"定个路线 safe"会走
        `_safe_route_preferences`（带 conditions），自动选路拿到的 route_policy 是空表，于是
        `_score_map_node` 内部的兜底里只有商店/精英两处硬编码的血量判断 —— 血再低也不会整体转向休息。
        """
        base: dict[str, float] = {}
        user_weights = route_policy.get("weights") if isinstance(route_policy.get("weights"), dict) else {}
        if user_weights:
            for key, value in user_weights.items():
                try:
                    base[str(key).strip().lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        else:
            # 没配过就用与历史行为一致的内置值（这样"未配置"时选路口径不变）。
            base = {
                "rest": 12.0,
                "shop": 8.0 if gold >= ROUTE_BOSS_PREP_GOLD else 4.0,
                "elite": -10.0 if hp_ratio < SAFE_MAP_HP_RATIO_BELOW else -4.0,
                "monster": 3.0,
                "event": 4.0,
                "treasure": 5.0,
                "question": 4.0,
            }
        conditions = route_policy.get("conditions") if isinstance(route_policy.get("conditions"), dict) else {}
        low_hp = conditions.get("low_hp") if isinstance(conditions.get("low_hp"), dict) else {}
        try:
            threshold = float(low_hp.get("hp_ratio_below", SAFE_MAP_HP_RATIO_BELOW))
        except (TypeError, ValueError):
            threshold = SAFE_MAP_HP_RATIO_BELOW
        if hp_ratio < threshold:
            low_table = low_hp.get("weights") if isinstance(low_hp.get("weights"), dict) else SAFE_MAP_LOW_HP_WEIGHTS
            for key, value in low_table.items():
                try:
                    base[str(key).strip().lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        return base

    # ---- 全图 DAG 的读取与链式递推 ---------------------------------------------------------

    def _map_graph_from_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """从 snapshot 里取出整幕地图 DAG（mod /state 的 map.nodes）。

        为什么从 snapshot 取而不是塞进 summary_context.payload：那张图有几十个节点，
        payload 会跟着 sync 包推给 NEKO，之前已经因为"往状态里塞大对象"踩过 ZMQ 超时。
        图只在规划器内部用，就地取最省事。
        """
        if not isinstance(snapshot, dict):
            return {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
        nodes = [node for node in (game_map.get("nodes") if isinstance(game_map.get("nodes"), list) else []) if isinstance(node, dict)]
        if not nodes:
            return {}
        return {"nodes": nodes, "boss_node": game_map.get("boss_node"), "rows": game_map.get("rows")}

    def _map_graph_index(self, graph: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
        """(row, col) -> 节点。mod 的 children/parents 是坐标对，必须靠这张索引才能解出类型。"""
        index: dict[tuple[int, int], dict[str, Any]] = {}
        for node in graph.get("nodes") if isinstance(graph.get("nodes"), list) else []:
            if not isinstance(node, dict):
                continue
            row = self._safe_int(node.get("row"))
            col = self._safe_int(node.get("col"))
            if row is None or col is None:
                continue
            index[(row, col)] = node
        return index

    def _map_boss_row(self, graph: dict[str, Any], index: dict[tuple[int, int], dict[str, Any]]) -> int | None:
        boss = graph.get("boss_node") if isinstance(graph.get("boss_node"), dict) else {}
        row = self._safe_int(boss.get("row"))
        if row is not None:
            return row
        rows = [r for (r, _c), node in index.items() if bool(node.get("is_boss"))]
        return max(rows) if rows else None

    def _map_child_keys(self, node: dict[str, Any]) -> list[tuple[int, int]]:
        """节点的子节点坐标列表（mod 给的是 children: [{row, col}, ...]）。"""
        keys: list[tuple[int, int]] = []
        for child in node.get("children") if isinstance(node.get("children"), list) else []:
            if not isinstance(child, dict):
                continue
            row = self._safe_int(child.get("row"))
            col = self._safe_int(child.get("col"))
            if row is None or col is None:
                continue
            keys.append((row, col))
        return keys

    def evaluate_map_route(
        self,
        graph: dict[str, Any],
        start: dict[str, Any],
        *,
        route_policy: dict[str, Any] | None = None,
        hp_ratio: float = 1.0,
        gold: int = 0,
        lookahead_rows: int | None = None,
    ) -> float | None:
        """从 `start` 这个候选节点出发、递归到 boss 的**整条路线**评分。

        - 分支点取 **max**：到那一层我们还会再选一次，所以是"沿途都选最好"而不是求和。
        - 每远一层乘 `ROUTE_DISCOUNT_PER_ROW`：远处的不确定性更大。
        - 同类节点按出现次数递减（`ROUTE_REPEAT_DECAY`）：两个休息点不等于两倍休息。
        - 临门（距 boss <= ROUTE_BOSS_PREP_ROWS 层）的休息/商店加权：补血、把金币花掉。
        - 精英按血量缓和：血越满，精英那点机会成本越低（精英给遗物+金币，未必是坏事）。

        返回 None 表示拿不到图（旧 mod / 非地图界面），调用方应退回单步评分。
        """
        index = self._map_graph_index(graph)
        if not index:
            return None
        row = self._safe_int(start.get("row"))
        col = self._safe_int(start.get("col"))
        if row is None or col is None or (row, col) not in index:
            return None
        policy = route_policy if isinstance(route_policy, dict) else {}
        weights = self._route_effective_weights(policy, hp_ratio, gold)
        try:
            depth = int(lookahead_rows if lookahead_rows is not None else (policy.get("lookahead_depth") or ROUTE_LOOKAHEAD_ROWS_FALLBACK))
        except (TypeError, ValueError):
            depth = ROUTE_LOOKAHEAD_ROWS_FALLBACK
        boss_row = self._map_boss_row(graph, index)
        return self._route_value(
            index,
            (row, col),
            weights=weights,
            hp_ratio=hp_ratio,
            gold=gold,
            boss_row=boss_row,
            depth=max(1, depth),
            counters={},
            path=set(),
        )

    def _route_value(
        self,
        index: dict[tuple[int, int], dict[str, Any]],
        key: tuple[int, int],
        *,
        weights: dict[str, float],
        hp_ratio: float,
        gold: int,
        boss_row: int | None,
        depth: int,
        counters: dict[str, int],
        path: set[tuple[int, int]],
    ) -> float:
        node = index.get(key)
        if node is None or depth < 0 or key in path:
            return 0.0
        node_type = self._map_node_weight_key(node)
        value = float(weights.get(node_type, 0.0))

        # 同类饱和：第 k 次出现按 ROUTE_REPEAT_DECAY^(k-1) 递减。
        seen = counters.get(node_type, 0)
        value *= ROUTE_REPEAT_DECAY ** seen

        # 精英缓和：血越满，精英那点机会成本越低。
        if node_type == "elite" and value < 0:
            relief = min(1.0, max(0.0, hp_ratio)) * ROUTE_ELITE_RELIEF_MAX
            value *= (1.0 - relief)

        # 临门一脚：紧邻 boss 的休息/商店价值远高于中途。
        if node_type in {"rest", "shop"} and self._is_boss_prep_row(node, boss_row):
            if node_type != "shop" or gold >= ROUTE_BOSS_PREP_GOLD:
                value *= ROUTE_BOSS_PREP_MULTIPLIER

        counters[node_type] = seen + 1
        path.add(key)
        best_child = 0.0
        if depth > 0:
            for child_key in self._map_child_keys(node):
                best_child = max(
                    best_child,
                    self._route_value(
                        index,
                        child_key,
                        weights=weights,
                        hp_ratio=hp_ratio,
                        gold=gold,
                        boss_row=boss_row,
                        depth=depth - 1,
                        counters=counters,
                        path=path,
                    ),
                )
        path.discard(key)
        counters[node_type] = seen
        return value + ROUTE_DISCOUNT_PER_ROW * best_child

    @staticmethod
    def _map_node_weight_key(node: dict[str, Any]) -> str:
        """node_type -> 权重表的键。认不出就原样返回（玩家仍可为原始名配权重）。"""
        raw = str(node.get("type") or node.get("node_type") or "").strip().lower()
        if not raw:
            return ""
        return _MAP_NODE_TYPE_ALIASES.get(raw, raw)

    @staticmethod
    def _character_list(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw = payload.get("character_select") if isinstance(payload.get("character_select"), dict) else {}
        chars = raw.get("characters") if isinstance(raw.get("characters"), list) else []
        return [c for c in chars if isinstance(c, dict)]

    def _character_list_signature(self, summary_context: dict[str, Any]) -> str:
        return "|".join(
            f"{c.get('index')}:{c.get('character_id')}" for c in self._character_list(summary_context)
        )

    def _random_character_index(self, summary_context: dict[str, Any]) -> int | None:
        """游戏自带的「随机」候选的下标（跳过锁着的）。"""
        for character in self._character_list(summary_context):
            if not bool(character.get("is_random")) or bool(character.get("is_locked")):
                continue
            index = self._safe_int(character.get("index"))
            if index is not None:
                return index
        return None

    def _selected_character_is_random(self, summary_context: dict[str, Any]) -> bool:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selected = str(payload.get("selected_character_id") or "")
        if not selected:
            return False
        for character in self._character_list(summary_context):
            if str(character.get("character_id") or "") == selected:
                return bool(character.get("is_random"))
        return False

    def _log_character_choice(self, decision: str, option_index: Any) -> None:
        """把选人界面的决定用 info 级记一次（同一决定只记一次）。

        为什么要留痕：`_debug` 在本文件里是**空函数**，玩家问"角色真随机了吗"时，
        日志里必须能看出"按了随机的那个候选"，而不是只能靠猜。
        """
        if self._logger is None:
            return
        key = f"{decision}:{option_index}"
        if key == self._character_choice_log_key:
            return
        self._character_choice_log_key = key
        try:
            self._logger.info("[sts2_character_select] decision=%s option_index=%s", decision, option_index)
        except Exception:
            pass

    def note_character_select_dispatched(self, option_index: Any) -> None:
        """服务层在**真的发出了** select_character 之后调用（用于"随机"只按一次）。"""
        if self._safe_int(option_index) is None:
            return
        self._character_pick_done = True

    @staticmethod
    def _is_boss_prep_row(node: dict[str, Any], boss_row: int | None) -> bool:
        if boss_row is None:
            return False
        own_row = node.get("row")
        if not isinstance(own_row, int) or isinstance(own_row, bool):
            try:
                own_row = int(own_row)
            except (TypeError, ValueError):
                return False
        return 0 <= (boss_row - own_row) <= ROUTE_BOSS_PREP_ROWS

    def _score_reward_card(self, card: dict[str, Any], deck_policy: dict[str, set[str] | list[dict[str, Any]]], archetype_tags: list[str]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        energy_gain = self._reward_dynamic_value(card, "Energy")
        score = 0
        tag_keywords = {
            "draw": ["draw", "抽牌", "skim", "coolheaded"],
            "defense": ["block", "护盾", "格挡", "defend", "charge battery"],
            "energy": ["energy", "能量", "turbo", "double energy"],
            "orb_focus": ["orb", "focus", "电球", "充能", "defragment", "frost"],
            "expensive_attack": ["2 cost", "3 cost", "x cost", "meteor", "heavy blade"],
            "attack": ["damage", "伤害", "attack", "strike"],
            "strength": ["strength", "力量"],
            "self_damage_attack": ["失去", "lose", "hp loss", "hemokinesis", "御血术"],
            "slow_block_only": ["block", "护盾", "格挡"],
        }
        for tag in deck_policy.get("prefer_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 40
        for tag in deck_policy.get("avoid_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score -= 40
        for tag in deck_policy.get("archetype_bias", set()):
            if tag in archetype_tags and any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 20

        score += self._base_reward_score(text, energy_gain)
        # 数值打分：用 dynamic_values 的实际量级（伤害×连击/格挡/抽牌/能量/易伤），按费用与自损扣减。
        score += self._reward_value_score(card)

        branches = deck_policy.get("branches") if isinstance(deck_policy.get("branches"), list) else []
        matched_branch = self._matched_reward_branch(branches, archetype_tags, text)
        if matched_branch is not None:
            prefer_tags = matched_branch.get("prefer_tags") if isinstance(matched_branch.get("prefer_tags"), list) else []
            avoid_tags = matched_branch.get("avoid_tags") if isinstance(matched_branch.get("avoid_tags"), list) else []
            prefer_cards = matched_branch.get("prefer_cards") if isinstance(matched_branch.get("prefer_cards"), list) else []
            avoid_cards = matched_branch.get("avoid_cards") if isinstance(matched_branch.get("avoid_cards"), list) else []
            for tag in prefer_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score += 35
            for tag in avoid_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score -= 35
            if any(str(card_name).strip().lower() in text for card_name in prefer_cards):
                score += 50
            if any(str(card_name).strip().lower() in text for card_name in avoid_cards):
                score -= 50
        return score

    #: ★ 流派标签 → 该流派"成型"所需的卡牌特征（用于把 deck 自身判成某流派）。
    #:
    #: 背景（缺陷 E）：`_reward_deck_policy` 只认 `instruction_type == "deck_policy"` 的记录，
    #: 而全仓库**没有任何地方产出**这种记录（唯一 `upsert` 点由用户自然语言指令驱动，
    #: `_DOMAINS` 里连 `deck_policy` 域都没有）⇒ `prefer_tags/avoid_tags/archetype_bias/branches`
    #: **恒为空** ⇒ `_score_reward_card` 里所有 ±35/±40/±50 的流派加权**永不触发**。
    #: 这正是用户点名的「卡牌流派的抉择」——系统里**根本没有流派机制**。
    #:
    #: 修法：不再依赖"外部指令"，而是**从当前卡组自身推断流派**
    #: （`_infer_archetype_tags` 已能识别中文卡组），
    #: 再用它生成一份"内部 deck_policy"，让既有的加权分支真正跑起来。
    _ARCHETYPE_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
        "defense": ("block", "格挡", "护盾", "护甲", "defend", "bodyguard", "护卫", "守墓人"),
        "attack": ("damage", "伤害", "attack", "strike", "打击"),
        "draw": ("draw", "抽牌", "skim", "思虑", "过牌"),
        "energy": ("energy", "能量", "储能", "turbo"),
        "strength": ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak"),
        "orb_focus": ("orb", "focus", "电球", "充能", "聚焦", "宝珠"),
    }

    #: 流派"成型"阈值：该标签的卡达到这么多张，就认为卡组在走这个流派。
    _ARCHETYPE_COMMIT_COUNT = 3

    def _archetype_policy_from_deck(self, deck: dict[str, Any], archetype_tags: list[str]) -> dict[str, Any]:
        """从**卡组自身**生成流派策略（替代恒空的外部 `deck_policy`）。

        ★ 这是缺陷 E 的修复：让「卡牌流派的抉择」真正存在。
        规则（保守、可解释）：
          * 已经在走某流派（该流派卡 ≥ `_ARCHETYPE_COMMIT_COUNT` 张）
            ⇒ `archetype_bias` 收录它 ⇒ 后续同流派卡 +20；
          * 该流派**成型后**，明显**偏离**它的卡被轻微降权（`avoid_tags`），
            避免"什么都要"把卡组摊薄（这是 Roguelike 卡组构建的核心取舍）；
          * 卡组仍很小时（< 阈值）不做任何偏好 ⇒ 早期保持开放，不锁死流派。

        返回空字典即表示"无流派偏好"（行为与改动前一致）。
        """
        if not archetype_tags:
            return {}
        cards = deck.get("cards") if isinstance(deck, dict) else None
        cards = cards if isinstance(cards, list) else []
        if len(cards) < self._ARCHETYPE_COMMIT_COUNT:
            return {}
        # 统计各流派在卡组里的实际张数（用同一套关键词表，口径与 _infer_archetype_tags 对齐）
        counts: dict[str, int] = {}
        for card in cards:
            if not isinstance(card, dict):
                continue
            text = " ".join(
                str(card.get(field) or "")
                for field in ("name", "card_id", "rules_text", "resolved_rules_text", "description", "effect", "card_type")
            ).lower()
            for tag, keywords in self._ARCHETYPE_TAG_KEYWORDS.items():
                if any(keyword in text for keyword in keywords):
                    counts[tag] = counts.get(tag, 0) + 1
        committed = {tag for tag, n in counts.items() if n >= self._ARCHETYPE_COMMIT_COUNT}
        if not committed:
            return {}
        return {
            "archetype_bias": committed,
            # 成型流派之外的"攻击/防御"倾向：若已专注防御流，则少拿纯攻击，反之亦然。
            "committed_counts": counts,
        }

    def _reward_deck_policy(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        prefer_tags: set[str] = set()
        avoid_tags: set[str] = set()
        archetype_bias: set[str] = set()
        branches: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "deck_policy":
                continue
            prefer_tags.update(str(tag).strip().lower() for tag in (value.get("prefer_tags") if isinstance(value.get("prefer_tags"), list) else []) if str(tag).strip())
            avoid_tags.update(str(tag).strip().lower() for tag in (value.get("avoid_tags") if isinstance(value.get("avoid_tags"), list) else []) if str(tag).strip())
            archetype_bias.update(str(tag).strip().lower() for tag in (value.get("archetype_bias") if isinstance(value.get("archetype_bias"), list) else []) if str(tag).strip())
            if isinstance(value.get("branches"), list):
                branches.extend(item for item in value.get("branches") if isinstance(item, dict))
        return {
            "prefer_tags": prefer_tags,
            "avoid_tags": avoid_tags,
            "archetype_bias": archetype_bias,
            "branches": branches,
        }

    def _base_reward_score(self, text: str, energy_gain: int) -> int:
        score = 0
        # ★ 负面卡惩罚（原先【完全缺失】）：`_base_reward_score` 与 `_reward_value_score`
        #   都只对正向关键词加分，于是「诅咒」与「防御」同为 0 分，
        #   平分时还会被 index 次序选中 ⇒ 诅咒/状态牌被收进卡组。
        #   这是「变化卡事件会选到诅咒」的第二个机制（第一个见 :838 跳牌判定被丢弃）。
        #   ⇒ 明确扣分，使"一张都不该拿"的判定能真正成立（配合 reward_skip_low_value）。
        if any(token in text for token in
               ["curse", "诅咒", "无法打出", "unplayable", "不能被打出", "无法被移除"]):
            score -= 60
        if any(token in text for token in
               ["wound", "伤口", "dazed", "晕眩", "slimed", "黏液", "burn", "灼伤", "void", "虚空"]):
            score -= 40
        if any(token in text for token in ["block", "护盾", "格挡"]):
            score += 20
        if any(token in text for token in ["draw", "抽牌"]):
            score += 18
        if any(token in text for token in ["damage", "伤害", "attack", "攻击"]):
            score += 12
        if any(token in text for token in ["strength", "力量"]):
            score += 10
        if energy_gain > 0 or any(token in text for token in ["获得能量", "gain energy", "恢复能量", "refund energy"]):
            score += 16 + (energy_gain * 4)
        return score

    def _reward_value_score(self, card: dict[str, Any]) -> float:
        """基于卡牌 dynamic_values 的实际量级打分（补充关键词打分的盲区：不区分 4 伤和 9 伤）。

        伤害×连击、格挡、抽牌、能量、易伤/虚弱均按量加分；自损(HpLoss)与费用扣分。
        """
        dmg = self._reward_dynamic_value(card, "Damage")
        blk = self._reward_dynamic_value(card, "Block")
        draw = self._reward_dynamic_value(card, "Cards")
        energy = self._reward_dynamic_value(card, "Energy")
        vuln = self._reward_dynamic_value(card, "VulnerablePower")
        weak = self._reward_dynamic_value(card, "WeakPower")
        hp_loss = self._reward_dynamic_value(card, "HpLoss")
        # 多段攻击：Repeat 动态值（如 3伤×3）优先，其次 hit_count。
        repeat = self._reward_dynamic_value(card, "Repeat")
        hits = max(1, repeat or self._safe_int(card.get("hit_count")) or 1)
        cost = self._safe_int(card.get("energy_cost")) or 0
        star_cost = self._safe_int(card.get("star_cost")) or 0
        return (
            dmg * hits * 1.0
            + blk * 0.8
            + draw * 6.0
            + energy * 8.0
            + vuln * 3.0
            + weak * 2.0
            - hp_loss * 2.5
            - cost * 2.0
            - star_cost * 1.0
        )

    def _reward_dynamic_value(self, card: dict[str, Any], field_name: str) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == field_name.strip().lower():
                return self._safe_int(item.get("current_value")) or 0
        return 0

    def _matched_reward_branch(self, branches: list[dict[str, Any]], archetype_tags: list[str], text: str) -> dict[str, Any] | None:
        for branch in branches:
            when_has = branch.get("when_has") if isinstance(branch.get("when_has"), list) else []
            if when_has and not any(str(token).strip().lower() in text for token in when_has):
                continue
            branch_archetypes = [str(tag).strip().lower() for tag in (branch.get("archetype_bias") if isinstance(branch.get("archetype_bias"), list) else []) if str(tag).strip()]
            if branch_archetypes and not any(tag in archetype_tags for tag in branch_archetypes):
                continue
            return branch
        return None

    def _infer_archetype_tags(self, deck: dict[str, Any]) -> list[str]:
        """从卡组推断流派标签。

        ★ 修两个真实缺陷（2026-10-17，实机校准）：
        1. **关键词表严重英文偏置**：实机卡名是中文（游戏跑中文），
           原表 `orb/zap/coolheaded/...` 对「打击/护卫/出击/守墓人」**全部不命中**
           ⇒ 实测真实 12 张卡组只推断出 `['defense','energy']`，
             且 `defense` 还只是误撞到「防」字（命中"防御"）。
           ⇒ 补齐中文关键词。
        2. **只读 `name`/`card_id`，完全不看效果文本**：
           `规则文本/rules_text/description` 里才有「造成N点伤害」这类可判据信息。
           实战中卡名往往猜不出效果（如「守墓人」「负能量脉冲」）
           ⇒ 改为 name + card_id + 全部效果文本一起做 haystack。
        """
        cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        haystack: list[str] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            parts = [
                str(card.get("name") or ""),
                str(card.get("card_id") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
            haystack.append(" ".join(parts).lower())
        # ★ 中文关键词要选**不会被数量词插断**的短词：
        #   实测真实文本是「抽2张牌」（数字插在"抽"和"牌"之间）⇒ 关键词"抽牌"不命中。
        #   故一律用单词素（抽/牌/格挡…）或容忍插入的形式。
        tags: list[str] = []
        if any(keyword in text for text in haystack for keyword in
               ("orb", "zap", "coolheaded", "ball lightning", "focus", "电球", "充能", "聚焦", "宝珠")):
            tags.append("orb_focus")
        if any(keyword in text for text in haystack for keyword in
               ("block", "defend", "charge battery", "格挡", "护盾", "防护", "防御", "护甲")):
            tags.append("defense")
        # ★ "抽N张牌"（数字插入）与"抽牌堆"（draw pile）必须区分：
        #   用正则而非子串。`抽[0-9一二三四五六七八九十]+张牌` 才是真抽牌。
        if any(
            _ARCHETYPE_DRAW_RE.search(text)
            or any(k in text for k in ("draw", "skim", "coolheaded"))
            for text in haystack
        ):
            tags.append("draw")
        if any(keyword in text for text in haystack for keyword in
               ("energy", "turbo", "double energy", "能量", "储能")):
            tags.append("energy")
        if any(keyword in text for text in haystack for keyword in
               ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")):
            tags.append("strength")
        # ★ 补 `attack` 标签（原实现**从不产出**它）：
        #   后果实测 —— 纯攻击卡组 `_infer_archetype_tags` 返回 `[]`
        #   ⇒ `_archetype_policy_from_deck` 拿不到流派 ⇒ 攻击卡组**永远没有流派偏好**，
        #     而防御卡组有 ⇒ 系统性地把玩家往防御推。
        if any(keyword in text for text in haystack for keyword in
               ("damage", "strike", "造成", "点伤害", "攻击")):
            tags.append("attack")
        return tags

    def _lethal_combat_operation(self, snapshot: dict[str, Any], actions: list[dict[str, Any]]) -> PlannedOperation | None:
        play_card = self._find_action(actions, "play_card")
        if play_card is None:
            return None
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        current_energy = self._safe_int(combat.get("player", {}).get("energy")) or 0
        playable_cards = [card for card in hand if isinstance(card, dict) and bool(card.get("playable"))]
        for enemy_index, enemy in enumerate(enemies):
            if not isinstance(enemy, dict):
                continue
            enemy_hp = self._safe_int(enemy.get("current_hp") if enemy.get("current_hp") is not None else enemy.get("hp")) or 0
            if enemy_hp <= 0:
                continue
            lethal_cards = self._find_lethal_card_sequence(playable_cards, current_energy, enemy_hp)
            if lethal_cards:
                first = lethal_cards[0]
                kwargs: dict[str, Any] = {"card_index": self._safe_int(first.get("index")) or 0}
                target_index = self._lethal_target_index(first, enemy_index)
                if target_index is not None:
                    kwargs["target_index"] = target_index
                return PlannedOperation(action_type="play_card", kwargs=kwargs, confidence=0.98, source="heuristic", reason="combat_lethal_sequence")
        return None

    def _find_lethal_card_sequence(self, cards: list[dict[str, Any]], current_energy: int, enemy_hp: int) -> list[dict[str, Any]]:
        ordered = sorted(cards, key=self._card_damage, reverse=True)
        chosen: list[dict[str, Any]] = []
        remaining_energy = current_energy
        remaining_hp = enemy_hp
        for card in ordered:
            cost = self._card_energy_cost(card)
            if cost > remaining_energy:
                continue
            damage = self._card_damage(card)
            if damage <= 0:
                continue
            chosen.append(card)
            remaining_energy -= cost
            remaining_hp -= damage
            if remaining_hp <= 0:
                return chosen
        return []

    def _card_damage(self, card: dict[str, Any]) -> int:
        """这张牌的**伤害数值**（读不出就 0 —— 宁可算 0 也不要瞎猜）。

        ★★ 2026-10-19 修掉一个**生产环境真在跑**的严重误判（`BUG-card-damage-unit-confusion`）：

        原实现在 `damage` 字段缺失时，把整段效果文本**按空格切词、返回第一个能解析的整数**：

            for token in effect_text.split():
                parsed = self._safe_int(token)
                if parsed is not None:
                    return parsed            # ← 不看单位！

        于是**任何带数字的非伤害牌都会被当成伤害牌**。实测：

            防御    「获得 5 点格挡。」   -> 5   ❌ 格挡被当成伤害
            准备    「抽 2 张牌。」       -> 2   ❌ 抽牌数被当成伤害
            治疗    「回复 8 点生命。」   -> 8   ❌ 回血被当成伤害
            充能    「获得 2 点能量。」   -> 2   ❌ 能量被当成伤害

        **为什么这在生产里真的会触发**：`ShopCardPayload`（`GameStateService.cs:6180`）
        **没有 `damage` 字段**，只有 `dynamic_values` + `resolved_rules_text`
        ⇒ 商店屏**必然**走这条回落路径 ⇒ `_deck_gap_vector` 的输出/格挡两维全错。

        实测后果（牌组 = 2×6伤 + 2×5格挡，第 10 层）：

            正确：gap_output=0.50  gap_defense=0.38
            实测：gap_output=0.08  gap_defense=0.58   ← **两维都错了，且方向相反**

        ⇒ 输出被虚高（因为格挡牌也算输出），防御被虚低/虚高错乱
        ⇒ **商店买错东西**，正是玩家说的「商店好像很喜欢买遗物？评估一下哪些是真正需要的」。

        ★ 修法：**先认单位**，再取数。按"伤害"语义显式匹配，
          匹配不到就返回 0（不再猜）。顺序刻意是：
            1. `dynamic_values` 里的 `Damage`（最可靠，mod 已结构化）
            2. `damage` 顶层字段
            3. 文本里**明确表述伤害**的形态（"造成 N 点伤害" / "Deal N damage"）
          第 3 步用正则**连带单位一起匹配**，而不是切词取数。
        """
        # 1. 结构化：dynamic_values 里的 Damage
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == "damage":
                parsed = self._safe_int(item.get("current_value"))
                if parsed is not None:
                    return parsed
        # 2. 顶层 damage 字段
        damage = self._safe_int(card.get("damage"))
        if damage is not None:
            return damage
        # 3. 文本：必须**连带单位**匹配，否则会把格挡/抽牌/回血的数字当成伤害
        effect_text = " ".join(
            [
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
            ]
        )
        # 中文形态：造成 N 点伤害（允许中间有修饰，如"对全体敌人造成 N 点伤害"）
        match = re.search(r"造成\s*(\d+)\s*点?伤害", effect_text)
        if match:
            return int(match.group(1))
        # 英文形态：Deal N damage / deals N damage
        match = re.search(r"deal[s]?\s*(\d+)\s*damage", effect_text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        # 读不出伤害语义 ⇒ 0。**不再**切词猜数（那正是本缺陷的根源）。
        return 0

    def _card_energy_cost(self, card: dict[str, Any]) -> int:
        cost = self._safe_int(card.get("energy_cost"))
        if cost is not None:
            return max(0, cost)
        return 1

    def _lethal_target_index(self, card: dict[str, Any], fallback_target: int) -> int | None:
        valid_target_indices = card.get("valid_target_indices") if isinstance(card.get("valid_target_indices"), list) else []
        if not valid_target_indices:
            return None
        if fallback_target in valid_target_indices:
            return fallback_target
        try:
            return int(valid_target_indices[0])
        except Exception:
            return None

    def _incoming_attack_total(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        total = 0
        for enemy in enemies:
            if not isinstance(enemy, dict):
                continue
            direct = self._safe_int(enemy.get("intent_damage"))
            if direct is not None:
                total += direct
                continue
            intents = enemy.get("intents") if isinstance(enemy.get("intents"), list) else []
            enemy_total = 0
            for intent in intents:
                if not isinstance(intent, dict):
                    continue
                total_damage = self._safe_int(intent.get("total_damage"))
                if total_damage is not None:
                    enemy_total += total_damage
                    continue
                damage = self._safe_int(intent.get("damage")) or 0
                hits = self._safe_int(intent.get("hits")) or 1
                enemy_total += damage * hits
            total += enemy_total
        return total

    def _current_block(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        return self._safe_int(player.get("block")) or 0

    # ---- 战斗中用药水（规格 docs/shop_scoring.md 「八」）------------------------------------
    #
    # 玩家口径（2026-10-07）：「还有每次对战如果损血大于8就鼓励使用药水」。
    # 与商店那条是同一套思路（药水是"能换命"的资源，该用就用、但不乱用），只是触发点从
    # "前方有精英"换成"这一下要挨超过 8 点"。这里是**纯决策**，记账（同一回合只喝一瓶）由 service 做。

    def in_elite_or_boss_combat(self, snapshot: dict[str, Any] | None = None) -> bool:
        """这一场是不是精英/Boss 战（§八节流：普通小怪战更省、精英/Boss 更愿意喝）。

        唯一可用的线索是"上一次选路选了什么节点"（`_note_route_choice`）：mod 的
        `combat.enemies[]` **没有** elite/boss 标记，实测层面分不出来。所以拿不到线索就返回 False，
        也就是**按玩家的原始口径（8 点）**走，绝不自作主张把阈值调高。
        """
        return self._last_route_node_type in {"elite", "boss"}

    def _combat_potion_threshold(self, *, elite_or_boss: bool | None = None) -> float:
        """这一场用的是哪条触发线。

        默认就是玩家说的 8 点。已经**明确知道**是普通小怪战时按
        `COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER`（1.25 → 10 点）略微收紧一点 —— 小怪战把药水喝光
        不划算；精英/Boss 战与"不知道是哪一场"都保持 8 点，不会把玩家的 8 点线悄悄抬高。
        """
        threshold = COMBAT_POTION_HP_LOSS_THRESHOLD
        if elite_or_boss is None:
            elite_or_boss = self.in_elite_or_boss_combat()
        if not elite_or_boss and self._last_route_node_type == "monster":
            threshold *= COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER
        return threshold

    def _combat_expected_damage(self, snapshot: dict[str, Any]) -> tuple[int | None, int]:
        """这一下要挨多少：`Σ intents[].total_damage − player.block`（不够减就取 0）。

        返回 `(预计损血, 意图总伤害)`；**拿不到意图数据时返回 `(None, 0)`** —— 玩家口径是
        "宁可不喝，也不乱灌"，所以读不到意图就不触发这条规则（`intents[]` 整个缺失、
        或每一条都没有伤害字段时都算"读不到"；意图是"给自己上 buff"这种非攻击则算读到了伤害 0）。
        """
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        alive = [enemy for enemy in enemies if isinstance(enemy, dict) and enemy.get("is_alive", True)]
        if not alive:
            return None, 0
        incoming = 0
        known = False
        for enemy in alive:
            direct = self._safe_int(enemy.get("intent_damage"))
            if direct is not None:
                incoming += direct
                known = True
                continue
            intents = enemy.get("intents")
            if not isinstance(intents, list):
                continue
            for intent in intents:
                if not isinstance(intent, dict):
                    continue
                total = self._safe_int(intent.get("total_damage"))
                if total is not None:
                    incoming += total
                    known = True
                    continue
                damage = self._safe_int(intent.get("damage"))
                if damage is not None:
                    incoming += damage * (self._safe_int(intent.get("hits")) or 1)
                    known = True
        if not known:
            return None, 0
        return max(0, incoming - self._current_block(snapshot)), incoming

    def _current_hp(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        hp = self._safe_int(player.get("current_hp"))
        if hp is None:
            hp = self._safe_int(run.get("current_hp"))
        return hp or 0

    def _current_max_hp(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        max_hp = self._safe_int(player.get("max_hp"))
        if not max_hp:
            max_hp = self._safe_int(run.get("max_hp"))
        return max_hp or 0

    def _potion_target_index(self, potion: dict[str, Any], snapshot: dict[str, Any]) -> int | None:
        """有指向的药水要指谁（`requires_target=true` 时 `valid_target_indices` 必须非空）。

        伤害类挑**血最少**的那只敌人（能秒就秒，少挨几轮）；其余（指向队友/自己）取第一个合法值。
        """
        if not bool(potion.get("requires_target")):
            return None
        valid: list[int] = []
        for item in potion.get("valid_target_indices") if isinstance(potion.get("valid_target_indices"), list) else []:
            parsed = self._safe_int(item)
            if parsed is not None:
                valid.append(parsed)
        if not valid:
            return None
        if str(potion.get("target_index_space") or "") != "enemies":
            return valid[0]
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []

        def enemy_hp(index: int) -> int:
            if 0 <= index < len(enemies) and isinstance(enemies[index], dict):
                return self._safe_int(enemies[index].get("current_hp") if enemies[index].get("current_hp") is not None else enemies[index].get("hp")) or 0
            return 1 << 30

        return min(valid, key=enemy_hp)

    @staticmethod
    def _combat_potion_score(category: str, *, lethal: bool, hp_ratio: float) -> float:
        """一瓶药水"能不能把这 8 点以上损血压下去"的评分（规格「用哪一瓶」的 1~5 条）。

        1. 减伤/闪避/格挡 → 最高；2. 群伤/清场 → 次高；3. 回血 → 只在"预计会死"或血量 < 30% 时才算数
        （**回血不减少这一下的伤害**，所以平时是 0 = 不因这条规则触发）；4. 清状态 → 与减伤同档的备选；
        5. 抽牌等 → 0（战斗里不当保命手段）。
        """
        if category == "heal":
            if lethal:
                return COMBAT_POTION_LETHAL_HEAL_SCORE
            if hp_ratio < COMBAT_POTION_LOW_HP_RATIO:
                return COMBAT_POTION_LOW_HP_HEAL_SCORE
            return 0.0
        return float(COMBAT_POTION_SCORES.get(category, 0.0))

    def decide_combat_potion(
        self,
        snapshot: dict[str, Any],
        *,
        already_used_this_turn: bool = False,
        elite_or_boss: bool | None = None,
        threshold: float | None = None,
    ) -> dict[str, Any]:
        """战斗中要不要喝药水、喝哪一瓶（规格「八」）。**永远返回 dict，不抛异常。**

        判据：
        - 预计损血 = `Σ intents[].total_damage − player.block`，`> 8` 触发（精英/Boss 战与"不知道"
          都按 8；已知普通小怪战按 10）；
        - **保命优先**：`current_hp − 预计损血 <= 0` → 无条件喝（这条压过"没到线"和"用哪一瓶"）；
        - 同一回合只喝一瓶（`already_used_this_turn` 由 service 用 runtime_state 记账传进来）；
        - 意图数据缺失 → 不触发。

        返回字段：`use` / `option_index` / `target_index` / `name` / `category` / `score` /
        `incoming` / `expected_loss` / `lethal` / `log`（`log` 是一句给人看的中文，service 加前缀
        `[sts2_potion] ` 打出去）。
        """
        if threshold is None:
            threshold = self._combat_potion_threshold(elite_or_boss=elite_or_boss)
        decision: dict[str, Any] = {
            "use": False,
            "option_index": None,
            "target_index": None,
            "name": "",
            "category": "",
            "score": 0.0,
            "incoming": 0,
            "expected_loss": 0,
            "lethal": False,
            "already_used_this_turn": bool(already_used_this_turn),
            "threshold": threshold,
            "log": "",
        }
        expected, incoming = self._combat_expected_damage(snapshot)
        if expected is None:
            decision["log"] = "拿不到意图数据（intents[] 缺失/没有伤害字段）→ 不触发用药水"
            return decision
        decision["incoming"] = incoming
        decision["expected_loss"] = expected
        current_hp = self._current_hp(snapshot)
        max_hp = self._current_max_hp(snapshot)
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 1.0
        lethal = current_hp > 0 and (current_hp - expected) <= 0
        decision["lethal"] = lethal

        if already_used_this_turn:
            decision["log"] = f"预计损血={expected} 但不喝（原因：本回合已经喝过一瓶）"
            return decision
        if not lethal and expected <= threshold:
            decision["log"] = f"预计损血={expected} ≤ {threshold:g} → 不喝（没到用药水的线）"
            return decision

        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        candidates: list[tuple[float, int, dict[str, Any], str, int | None]] = []
        for position, potion in enumerate(potions):
            if not isinstance(potion, dict) or not potion.get("potion_id"):
                continue
            if not potion.get("can_use"):
                continue
            target_index = self._potion_target_index(potion, snapshot)
            if bool(potion.get("requires_target")) and target_index is None:
                # 需要目标却没有合法目标：这一瓶现在按不下去，跳过。
                continue
            index = self._safe_int(potion.get("index"))
            if index is None:
                index = position
            category = self._potion_category(potion)
            score = self._combat_potion_score(category, lethal=lethal, hp_ratio=hp_ratio)
            candidates.append((score, index, potion, category, target_index))
        if not candidates:
            decision["log"] = f"预计损血={expected} 但不喝（原因：手里没有可用的药水）"
            return decision
        candidates.sort(key=lambda item: (-item[0], item[1]))
        score, index, potion, category, target_index = candidates[0]
        name = str(potion.get("name") or potion.get("potion_id") or "药水")
        label = _POTION_CATEGORY_LABELS.get(category, category)
        if score <= 0 and not lethal:
            decision["log"] = f"预计损血={expected} 但不喝（原因：手里只有{label}类，不当保命手段）"
            return decision
        decision.update(
            use=True,
            option_index=index,
            target_index=target_index,
            name=name,
            category=category,
            score=score,
        )
        if lethal:
            # 保命优先：会死就喝，没到线也喝（规格「八、判据」：「这条永远压过其它考虑」）。
            # 只剩抽牌类时评分是 0，但仍然喝 —— 它排在所有保命类后面，所以有更好的就先喝更好的。
            qualifier = "无条件用" if expected <= threshold else "用"
            decision["log"] = f"预计损血={expected} 会致命（血量 {current_hp}）→ {qualifier}〈{name}〉({label}, 评分 {score:.0f})"
        else:
            decision["log"] = f"预计损血={expected} > {threshold:g} → 用〈{name}〉({label}, 评分 {score:.0f})"
        return decision

    def _first_record_option(self, preferences: dict[str, Any]) -> int | None:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = value.get("preferred_option_index")
            normalized = self._safe_int(preferred)
            if normalized is not None:
                return normalized
        return None

    def _safe_int(self, value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    # ---- 游玩模式（扰动）用的候选排序 ------------------------------------------------------
    #
    # 这里只做两件事：把"该屏有哪些**合法**下标"枚举出来，以及"按**既有评分**把这些下标排好序"。
    # 评分一律复用各屏原来的 _score_* 函数，不另写一套口径——排序的第 0 个必须就是规划器原本会选的
    # 那一个，否则"不扰动"就不等于改动前的行为。
    #
    # 扰动层（service.run_autoplay_step）只在非 serious 档才调用这些方法：serious 档连排序都不算，
    # 保证默认档零影响。

    #: 下标键：只有 play_card 用 card_index，其余选项类动作都是 option_index。
    _INDEX_KEY_BY_ACTION = {"play_card": "card_index"}
    _DEFAULT_INDEX_KEY = "option_index"

    #: 任一为 False 就认为该项不可选（宁可少给候选，也不能扰动出一个非法动作）。
    _UNSELECTABLE_FLAGS = ("playable", "enabled", "is_enabled", "affordable", "can_afford")

    def _score_upgrade_card(self, card: dict[str, Any]) -> int:
        """升级屏：一张牌"多值得被升级"。**分数越高越该升。**

        ★★ 为什么必须单独写这一个函数（2026-10-18 实机缺陷）：
          升级屏此前**复用了删牌评分** `_score_remove_card`（该函数给"打击"−60 =
          "最该删的卡"），而 `_preferred_remove_option` 取的是 **min** ⇒
          于是升级屏恰好选中**最该删的那张** ⇒ 实测「可以优先看：打击」7/13 次，
          `打击+` 在日志里出现 35 次。**删牌与升级的最优方向正好相反**：
            删牌：选**最没价值**的（分越低越该删）
            升级：选**最有价值**的（分越高越该升）
          ⇒ 两者**绝不能共用**同一个评分函数。旧行为是语义反转，不是调参问题。

        判据（尽量只用 mod 已下发的字段，不猜）：
          * 已升级过（`upgraded` 为真）→ 返回极低分（不能再升，排除）
          * 诅咒牌 → 极低分（升它们没有意义）
          * 基础打击/防御 → 低分（升一次收益极小，是"浪费升级"的典型）
          * 高费牌 → 加分（升级降费收益大，费用越高收益越大）
          * 抽牌/能量/充能/能力等引擎牌 → 加分（长期价值高）

        ★★ 实机自查修掉的三个缺陷（2026-10-18，`_selfaudit_upgrade.py` 复现）：
          1. **不能照抄删牌的关键词**。删牌用 `['status','状态']` 判"该删"是合理的
             （状态牌确实该删），但**升级**若也这么判，就会把效果文本里含"状态"
             一词的**好卡**（如「巨像：使敌人陷入易伤状态」「痛击：施加易伤状态」）
             全打成 −500 而排到最后。实机样本已复现：巨像/痛击/头槌 均被误判。
             ⇒ 这里**只认真正的诅咒牌**（`curse`/`诅咒`），**不看** status/状态。
          2. **"打击/防御" 不能用裸子串**。`"双重打击"`、`"剑柄打击"`、`"灰烬打击"`
             都含"打击"，但它们是好卡；一律 −60 会**把好卡当基础牌降权**。
             ⇒ 改成**名称精确匹配**基础牌名（打击/防御/Defend/Strike + 可选 "+"）。
          3. **"能量/充能" 也不该裸匹配效果文本**。`"消耗 1 点能量"` 这种
             普通效果会被当成引擎牌加分。⇒ 只在**名称**里判引擎属性，不扫描述。
        """
        if bool(card.get("upgraded")):
            # 已经升过，不能再升。给最低分让它排在最后（而不是直接删掉，
            # 以便所有卡都升过时仍能选出一张、不发生死锁）。
            return -1000
        name = str(card.get("name") or "").strip()
        name_l = name.lower()
        # 只认真诅咒。★ 不看 status/状态 —— 效果文本里的"状态"是正常机制词。
        text_for_curse = " ".join([
            name,
            str(card.get("card_type") or card.get("type") or ""),
            str(card.get("rarity") or ""),
        ]).lower()
        if "curse" in text_for_curse or "诅咒" in text_for_curse:
            return -500
        # ★ 基础牌判定用**名称精确匹配**，避免 "双重打击"/"剑柄打击" 被误伤。
        base_names = {"打击", "防御", "strike", "defend", "defend_ironclad",
                      "strike_ironclad", "打击+", "防御+"}
        is_base = name_l in base_names or name_l.rstrip("+").strip() in base_names
        score = -60 if is_base and ("打击" in name or "strike" in name_l) else (
            -45 if is_base else 20)
        # 引擎类：★ 只看**名称**，不扫描述（避免"消耗1点能量"被当成引擎牌）。
        if any(token in name_l for token in ["draw", "skim", "coolheaded", "抽牌", "过牌"]):
            score += 40
        if any(token in name_l for token in ["energy", "turbo", "能量"]):
            score += 50
        if any(token in name_l for token in ["focus", "orb", "电球", "充能"]):
            score += 45
        # ★降费收益：费用越高，升级减费赚得越多。读 mod 已给的 energy_cost。
        cost = self._safe_int(card.get("energy_cost"))
        if cost is None:
            cost = self._safe_int(card.get("cost"))
        if isinstance(cost, int) and cost >= 2:
            score += 15 * min(cost, 3)
        # 稀有度：越稀有通常越值得投资。
        rarity = str(card.get("rarity") or "").lower()
        if any(token in rarity for token in ["rare", "稀有"]):
            score += 25
        elif any(token in rarity for token in ["uncommon", "罕见"]):
            score += 12
        return score

    #: 用 _score_remove_card 排名的选牌屏（"移除/删牌"）。其它选牌屏（升级/变换/附魔）没有对应评分，
    #: 走通用枚举。
    _REMOVE_SELECTION_SCREENS = {"card_selection_remove", "card_selection_delet"}

    @classmethod
    def index_key_for(cls, action_type: Any) -> str:
        """该动作类型用哪个 kwargs 键承载"选第几个"。"""
        return cls._INDEX_KEY_BY_ACTION.get(str(action_type or "").strip().lower(), cls._DEFAULT_INDEX_KEY)

    @classmethod
    def _item_blocked(cls, item: dict[str, Any]) -> bool:
        return any(flag in item and item.get(flag) is False for flag in cls._UNSELECTABLE_FLAGS)

    @classmethod
    def _item_indices(cls, items: Any) -> list[int]:
        """一列选项 dict → 下标列表（优先用 item['index']，缺省按位置）。不可选的排除掉。"""
        result: list[int] = []
        if not isinstance(items, list):
            return result
        for position, item in enumerate(items):
            if not isinstance(item, dict) or cls._item_blocked(item):
                continue
            raw = item.get("index")
            result.append(int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position)
        return result

    @staticmethod
    def _operation_dict(operation: Any) -> dict[str, Any]:
        if isinstance(operation, dict):
            return operation
        if hasattr(operation, "as_dict"):
            try:
                return dict(operation.as_dict())
            except Exception:
                return {}
        return {}

    @staticmethod
    def _context_parts(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        strategy_context = context.get("strategy_context") if isinstance(context.get("strategy_context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        return summary_context, strategy_context, snapshot

    def legal_option_indices(self, context: dict[str, Any], operation: Any) -> list[int]:
        """当前操作所在屏的**合法**下标集合；枚举不出就返回空列表（= 不扰动）。"""
        action_type = str(self._operation_dict(operation).get("action_type") or "").strip().lower()
        summary_context, _strategy_context, snapshot = self._context_parts(context)
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}

        if action_type == "choose_event_option":
            return self._item_indices(payload.get("event_options"))
        if action_type == "choose_map_node":
            nodes = payload.get("travelable_nodes")
            if not isinstance(nodes, list) or not nodes:
                nodes = payload.get("available_nodes")
            return self._item_indices(nodes)
        if action_type == "choose_reward_card":
            return self._item_indices(self._reward_candidates(summary_context))
        if action_type == "select_deck_card":
            return self._item_indices(payload.get("selection_cards"))
        if action_type == "choose_rest_option":
            return self._item_indices(payload.get("rest_options"))
        if action_type == "buy_card":
            return self._item_indices(payload.get("shop_cards"))
        if action_type == "buy_relic":
            return self._item_indices(payload.get("shop_relics"))
        if action_type == "buy_potion":
            return self._item_indices(payload.get("shop_potions"))
        if action_type == "play_card":
            return [index for index, _card in self._playable_hand(context)]
        if action_type == "choose_treasure_relic":
            chest = raw_state.get("chest") if isinstance(raw_state.get("chest"), dict) else {}
            return self._item_indices(chest.get("relic_options"))
        if action_type == "choose_bundle":
            return self._item_indices(raw_state.get("bundles"))
        if action_type == "choose_capstone_option":
            # capstone 选项在 payload 里没有列表，只有按钮。规划器自己的游标正是扫
            # 0.._MAX_CAPSTONE_SWEEP-1（见 _next_capstone_option），所以这个范围就是它会去碰的下标集合。
            return list(range(self._MAX_CAPSTONE_SWEEP))
        # claim_reward / proceed / confirm_* / end_turn 等没有"选第几个"，不扰动。
        return []

    def _playable_hand(self, context: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
        _summary_context, _strategy_context, snapshot = self._context_parts(context)
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        result: list[tuple[int, dict[str, Any]]] = []
        for position, card in enumerate(hand):
            if not isinstance(card, dict) or not bool(card.get("playable")):
                continue
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            result.append((index, card))
        return result

    def _ranked_option_indices(self, context: dict[str, Any], operation: Any) -> list[int]:
        """按各屏**既有评分**给出的"从好到坏"顺序；没有评分可用的屏返回空列表。

        **跨局教训（strategy_directives["avoid"]）在这里生效**：排序完成后，命中教训关键词的
        选项会被降权重排。这是复盘闭环唯一的消费点 —— 没有它，`avoid` 就只是个只写不读的死字段
        （见 `.agent-teams/sts2-omnissiah-audit/reflection-audit-t2.md`）。
        """
        action_type = str(self._operation_dict(operation).get("action_type") or "").strip().lower()
        summary_context, strategy_context, _snapshot = self._context_parts(context)
        preferences = strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        state_name = str(classification.get("state_name") or "").strip().lower()

        if action_type == "choose_event_option":
            ranked = self._rank_event_options(summary_context, strategy_context)
        elif action_type == "choose_map_node":
            ranked = self._rank_map_nodes(summary_context, preferences)
        elif action_type == "choose_reward_card":
            ranked = self._rank_reward_cards(summary_context, preferences)
        elif action_type == "select_deck_card" and state_name in self._REMOVE_SELECTION_SCREENS:
            ranked = self._rank_remove_options(summary_context)
        elif action_type == "select_deck_card" and self._is_upgrade_selection(summary_context):
            # ★ 升级屏的排序必须与删牌相反（分越高越该升），否则扰动池的"第 0 位"
            #   会与 plan() 实际选出的那张不一致 —— 而扰动层的不变量正是
            #   "排序第 0 个 == 规划器原本会选的那一个"。共用删牌排序会破坏该不变量。
            ranked = self._rank_upgrade_options(summary_context)
        else:
            return []
        return self._apply_run_lessons(strategy_context, summary_context, action_type, ranked)

    def _run_memory_lessons(self, strategy_context: dict[str, Any]) -> list[str]:
        """取出跨局教训文本（`strategy_directives["avoid"]`）。

        这些文本由 `strategy_repository._inject_run_memory_directives` 从猫娘记忆注入，
        每个 tick 重算，所以此处只读不写。
        """
        directives = strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}
        raw = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        return [str(item).strip() for item in raw if str(item).strip()]

    #: ★I2 战斗屏着力点：哪些教训 token 会改变**战斗出牌顺序**，以及怎么改。
    #:
    #: 背景（t10 基线实测）：`_indices_worth_penalizing` 对 `play_card` 返回空集，
    #: 而 32/43 的死亡发生在 COMBAT。但**天真的补法（给 play_card 加降权分支）是错的**：
    #:   * `_apply_run_lessons` 只被 `ordered_candidates`（**扰动候选池**）调用，
    #:     `plan()` 的战斗路径在 solver 命中时于 :1049 直接 return，从不经过它
    #:     ⇒ 只改 `_indices_worth_penalizing` 对真实战斗决策的位移为 **0**（t1 N1，已复现）。
    #:   * `avoid` 语义是「哪张牌别出」，而战斗教训的真实语义是「**先垫格挡再输出**」——
    #:     这是**顺序**，不是**排除**。用降权表达顺序会把语义压平（t2 F2 / t1 §3.5）。
    #: 所以正确接线是：把教训转成**顺序偏好**，接进既有的战斗决策链
    #: `_plan_combat_turn_sequence:3125` / `_preferred_combat_card_index:3046`。
    #:
    #: ⚠️ 防双重计分（t1 Q2 规则 R2/R3）：
    #:   * 只在**没有任何既有指引**（solver 未命中、guidance 未给方向）时才补默认方向；
    #:   * 绝不改写 solver 已给出的第一步；
    #:   * 与 guidance 冲突时 **guidance 优先**（玩家显式指令 > 跨局教训）。
    _LESSON_COMBAT_DIRECTION: tuple[tuple[str, str], ...] = (
        # token -> 方向。顺序即优先级：先保命，再输出。
        #
        # ★t17 调查结论（**保留原语义**，不要"修"成极性分歧）：
        # 真实 10 条注入教训里有 8 条含 `['skill','attack']`（另 2 条是兜底句/纯 skill），
        # 因此这里**恒**返回 `prefer_defense`。我一度以为这是"极性锁定"缺陷，
        # 实测后确认它**语义上是对的**：这批真实反思原文写的就是
        # 「先垫够格挡再输出」「先把格挡叠够再还手」——**首步确实该防御**。
        # 所以恒取 `prefer_defense` 不是 bug，是**正确地读懂了反思**。
        #
        # 真正的机制在别处：生产形态下 `gap>0`（挨打）时，:1145 的默认兜底
        # **也会**给出 `prefer_defense` ⇒ 两者**同值** ⇒ 真实注入在该带内
        # **不可见**（冗余，非被吞）。位移只能出现在默认兜底**不触发**的带
        # （`gap<=0`，即玩家格挡已够）—— 实测那里位移 = 1。详见 `_lesson_combat_direction` 文档。
        ("skill", "prefer_defense"),
        ("attack", "prefer_attack"),
    )

    def _lesson_combat_direction(
        self,
        strategy_context: dict[str, Any],
        *,
        logger: Any | None = None,
    ) -> dict[str, bool]:
        """把跨局教训翻译成战斗屏的**顺序偏好**（`prefer_defense` / `prefer_attack`）。

        返回值可直接并入既有的 `guidance_override` 通路
        （`_guidance_override_from_payload` → `:1137-1143`）。

        ★保守性（无教训时必须逐字保持现状）：
        * 无教训 / 抽不出 token / token 与战斗无关 ⇒ 返回 `{}`（**空 dict**，不是 False）。
        * 同时含 `skill` 与 `attack`（真实最高频组合）⇒ 取 **`prefer_defense`**：
          这正对应反思原意「先垫格挡**再**输出」——先保命，再输出。
          若取 `prefer_attack` 就会把「先垫格挡」读反。
        * 纯函数：不掷随机、不读时间。

        ★★t17 关键机制澄清（**必须连读，否则会做出错误推断**）：
        在本函数恒返回 `prefer_defense` 的前提下，它与生产形态下
        `:1145` 的默认兜底（`gap>0` ⇒ `prefer_defense`）**同值**。
        ⇒ 真实注入在「挨打」带内**看不见位移**，但这**不是**被吞、也**不是**接线失效，
          而是**冗余**——两者给出同一个方向。

        位移可观测的唯一带是**默认兜底不触发**的地方（`gap<=0`，玩家格挡已够）：
        ```
        gap<=0 :  无教训 -> card_index=0 ;  真实注入 -> card_index=1   ★位移=1
        gap>0  :  无教训 -> card_index=1 ;  真实注入 -> card_index=1     位移=0（冗余）
        ```
        ⇒ 因此「删掉任一闸即可覆盖」是**错的**；本函数与默认兜底是**两个独立来源**，
          同时指向防御时自然无法区分贡献。要测量教训的**净贡献**，必须
          在该函数**返回空 dict** 的带（或摘掉教训层）上做对照 —— 见 `_t17_e2e.py`。
        """
        lessons = self._run_memory_lessons(strategy_context)
        if not lessons:
            return {}
        tokens = self._lesson_tokens(lessons)
        if not tokens:
            return {}
        direction: dict[str, bool] = {}
        for token, name in self._LESSON_COMBAT_DIRECTION:
            if token in tokens:
                direction[name] = True
                break
        if direction:
            self._lesson_hit_debug("combat_direction", lessons, tokens, direction, logger=logger)
        return direction

    def lesson_trace(self, context: dict[str, Any], operation: Any = None) -> dict[str, Any]:
        """★I5 度量埋点：把"这一 tick 教训层做了什么"完整暴露出来。

        让 t_r3 的每个指标都能从**一个**调用算出，不必再猜内部状态：

        * ``lessons``      —— 注入的教训原文（`strategy_directives["avoid"]`）
        * ``tokens``       —— `_lesson_tokens()` 抽出的 token（闸3 判据）
        * ``token_lesson_ratio`` —— 有多少条教训能抽出 token（信息密度）
        * ``combat_direction``   —— 教训给出的战斗方向（I2 接线是否生效）
        * ``fallback_lessons``   —— 其中有多少条是那个无信息量的兜底模板句
        * ``penalize_indices``   —— 该屏被降权的下标（I3 区分度判据）

        **只读**：不掷随机、不写状态（`_lesson_hit_debug` 的去重集合除外，且它不影响决策）。
        `operation` 省略时只报教训侧，不报屏相关字段。
        """
        _summary_context, strategy_context, _snapshot = self._context_parts(context)
        lessons = self._run_memory_lessons(strategy_context)
        tokens = self._lesson_tokens(lessons)
        trace: dict[str, Any] = {
            "lessons": lessons,
            "lesson_count": len(lessons),
            "tokens": tokens,
            "token_count": len(tokens),
            "tokenizable_lessons": [text for text in lessons if self._lesson_tokens([text])],
            "combat_direction": self._lesson_combat_direction(strategy_context),
        }
        trace["tokenizable_lesson_count"] = len(trace["tokenizable_lessons"])
        trace["token_lesson_ratio"] = (
            round(trace["tokenizable_lesson_count"] / len(lessons), 4) if lessons else 0.0
        )
        # 兜底模板句识别（供 M1 复读率的运行期口径）。
        trace["fallback_lessons"] = [text for text in lessons if "输得不明不白" in text]
        trace["fallback_lesson_count"] = len(trace["fallback_lessons"])
        if operation is not None:
            action_type = str(self._operation_dict(operation).get("action_type") or "").strip().lower()
            digest = self._operation_dict(operation)
            try:
                ranked = self._ranked_option_indices(context, digest)
            except Exception:
                ranked = []
            trace["action_type"] = action_type
            trace["penalize_indices"] = sorted(self._indices_worth_penalizing(
                (self._context_parts(context)[0].get("payload")
                 if isinstance(self._context_parts(context)[0].get("payload"), dict) else {}),
                action_type,
                tokens,
            ))
            trace["ranked_indices"] = ranked
            trace["candidate_count"] = len(ranked)
            # I3 区分度：罚完之后还剩几个"未被罚"的候选。<=1 说明这条教训没有区分度。
            trace["discriminating_indices"] = [
                index for index in ranked if index not in set(trace["penalize_indices"])
            ]
            trace["discriminating_count"] = len(trace["discriminating_indices"])
        return trace

    def _lesson_hit_debug(
        self,
        stage: str,
        lessons: list[str],
        tokens: list[str],
        effect: Any,
        *,
        logger: Any | None = None,
    ) -> None:
        """★I4 教训命中时的可 grep 日志（沿用既有 `[sts2_*]` 前缀风格）。

        **去重键** = `stage + tokens + effect`：同一 tick 内同样的命中只打一次，
        避免每个决策步骤都刷屏把日志淹掉。去重状态放在实例上（按 planner 生命周期）。

        ★**日志失败绝不影响决策路径**：整段包在 `try/except` 里，
        且 `logger` 缺省时回落到 `self._debug`（同样可能不可用）。
        任何异常都被吞掉 —— 可观测性永远不许成为决策的失败点。
        """
        try:
            dedup_key = f"{stage}|{','.join(tokens)}|{effect!r}"
            seen = getattr(self, "_lesson_hit_seen", None)
            if not isinstance(seen, set):
                seen = set()
                self._lesson_hit_seen = seen
            if dedup_key in seen:
                return
            seen.add(dedup_key)
            message = "[sts2_lesson_hit] stage=%s tokens=%s effect=%r lessons=%d"
            args = (stage, ",".join(tokens), effect, len(lessons))
            emit = logger if callable(logger) else getattr(self, "_debug", None)
            if callable(emit):
                emit(message, *args)
        except Exception:
            # 可观测性失败绝不允许冒泡到决策路径。
            return

    #: 每命中一条教训 token，被罚项的"降权档位"加多少。
    #:
    #: ★★ 方案 A（2026-10-19，玩家选定的修法）：把"降权"实现成**可比较的排序键**，
    #: 而不是"移到末尾"。
    #:
    #: 病因（`.dsh-artifacts/BUG-lesson-demotion-identity.md`）：
    #: 原实现是 `kept + demoted`（两个列表各自保持原相对顺序再拼接）。
    #: 当被罚集合恰好是"候选尾部连续若干项"时，拼接结果**与原序逐字相同** ⇒
    #: **恒等变换、教训完全失效**。而真实数据正落在这个失效区：
    #:     基线 = [1, 3, 0, 2]，被罚 = {0, 2}（本来就在末尾）
    #:     kept=[1,3] + demoted=[0,2] = [1,3,0,2]  ← 没变
    #:
    #: 为什么"移到末尾"这个语义本身就不对：
    #:   它无法表达"已经在末尾了，不该再动"与"该降 3 位"的区别。
    #:   一个恒等于尾部的映射，对被罚项的相对次序**没有任何信息**。
    #:
    #: 修法：给每个候选算一个**惩罚档位** `penalty`（0 = 未被罚），
    #: 然后按 `(penalty, 原始名次)` 排序。这样：
    #:   * 惩罚为 0 的项保持原相对顺序（等价于"无教训时逐字原序"）；
    #:   * 被罚项按其**惩罚强度**分档，强的更靠后；
    #:   * **已在末尾的被罚项仍是恒等** —— 但这次是**正确的恒等**：
    #:     它已经在最低档了，没有更低的地方可去（这是语义正确，不是缺陷）。
    #:
    #: ⚠️ 与旧实现的**可观测差异**（本修法的价值所在）：
    #:   旧实现在"被罚项分散"时才有效，在"被罚项集中尾部"时恒等；
    #:   新实现在**两种情况下都有效** —— 分散时能把中间项压到尾部，
    #:   集中时至少能把"更该罚的"排到"较该罚的"后面（旧实现做不到）。
    _LESSON_PENALTY_PER_TOKEN = 1

    def _lesson_penalty_strength(self, lessons: list[str]) -> dict[str, int]:
        """每个教训 token 的**惩罚强度** = 有多少条教训提到过它。

        ★★ 为什么需要"强度"而不是布尔命中（实测驱动，2026-10-19）：

        真实 50 局记忆的窗口（10 条教训）实测频次：

            attack  8 条     skill  7 条     elite  5 条     monster  1 条

        若只按"命中/未命中"（布尔），`elite` 与 `monster` 同档 ⇒
        排序键退化成 `(1, 原始名次)` ⇒ **保持原序 ⇒ 仍然恒等失效**。
        这正是我第一版方案 A 实现的问题：换了个写法，病没治。

        而频次是**真实的信号**：被 5 条教训反复提到的坑，
        比只被提到 1 次的不该同等对待。用频次当强度，档位就能区分开。

        语义上这也对：复盘是"多局统计"，某类死因越常出现，
        说明它越没被纠正 ⇒ 越该规避。**这本来就是玩家要的"多维反思"的最小形式。**
        """
        strength: dict[str, int] = {}
        for lesson in lessons:
            for token in self._lesson_tokens([lesson]):
                strength[token] = strength.get(token, 0) + 1
        return strength

    def _apply_run_lessons(
        self,
        strategy_context: dict[str, Any],
        summary_context: dict[str, Any],
        action_type: str,
        ranked: list[int],
    ) -> list[int]:
        """把跨局教训应用到候选排序上（稳定重排：只降权，不删除）。

        保守实现，三条硬约束：

        * **无教训时逐字返回原序** —— 没有记忆时行为与改动前完全一致。
        * **只降权不删除** —— 命中的选项仍留在候选里（只是靠后），所以扰动层拿到的仍是合法集合，
          且最坏情况退化为"顺序不同"而不会"无候选可出"。
        * **纯函数式** —— 不掷随机数、不读时间，同样的输入必得同样的输出，可确定性测试。

        ★★ 2026-10-19 方案 A（玩家选定），修掉原 `kept + demoted` 的**恒等失效**：

            旧：kept + demoted ⇒ 被罚项集中在尾部时**逐字原序**（教训完全失效）
            新：按 (惩罚强度, 原始名次) 排序 ⇒ 强度可区分，任意分布都能表达

        实测修复效果（真实 50 局注入，基线 `[1,3,0,2]`，命中 `{Monster(0), Elite(2)}`）：

            elite=5 条、monster=1 条  ⇒  强度 5 > 1
            ⇒ Monster(0) 被压到 Elite(2) 之后
            ⇒ `[1,3,0,2]` → `[1,3,2,0]`  ★ **教训终于生效**

        对照：若两者频次相同，则**保持原序是正确的** ——
        同等该罚的两项之间没有理由改变相对次序（稳定性优于随机）。
        """
        lessons = self._run_memory_lessons(strategy_context)
        if not lessons or len(ranked) < 2:
            return ranked

        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        tokens = self._lesson_tokens(lessons)
        if not tokens:
            return ranked
        penalize_indices = self._indices_worth_penalizing(payload, action_type, tokens)
        if not penalize_indices:
            # 这一屏没有该教训的着力点（或是未支持的屏）：原序返回，等于没有记忆时的行为。
            return ranked

        # ★ 强度 = "有多少条教训提到过这个 token"（见 `_lesson_penalty_strength`）。
        #   必须**逐 token** 求命中集，才能把强度落到具体选项上。
        strength = self._lesson_penalty_strength(lessons)
        penalties: dict[int, int] = {}
        for token, weight in strength.items():
            for index in self._indices_worth_penalizing(payload, action_type, [token]):
                penalties[index] = penalties.get(index, 0) + weight * self._LESSON_PENALTY_PER_TOKEN

        # 未被罚的项 penalty = 0；排序键 (penalty, 原始名次) ⇒ 稳定、单调、可表达任意强度。
        order = {index: position for position, index in enumerate(ranked)}
        return sorted(ranked, key=lambda index: (penalties.get(index, 0), order.get(index, 0)))

    def _lesson_tokens(self, lessons: list[str]) -> list[str]:
        """把教训文本压成一小组可匹配的关键词。

        教训是整句中文（例如「下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出」），
        直接拿整句去匹配选项描述必然匹配不上，所以抽取其中的**行为关键词**。
        未命中任何关键词的教训会被忽略 —— 宁可没影响，也不要用模糊匹配乱改决策。
        """
        tokens: list[str] = []
        for lesson in lessons:
            text = lesson.lower()
            for token, keywords in self._LESSON_KEYWORD_MAP:
                if token in tokens:
                    continue
                if any(keyword in text for keyword in keywords):
                    tokens.append(token)
        return tokens

    def _indices_worth_penalizing(
        self,
        payload: dict[str, Any],
        action_type: str,
        tokens: list[str],
    ) -> set[int]:
        """哪些选项应当因为跨局教训被降权。

        处理三种语义明确的屏：**路线**（规避某个节点类型）、**选牌奖励**（规避某类卡）、
        **事件选项**（规避某类事件后果）。
        其余屏返回空集 —— 匹配不准就别动，避免把复盘变成噪声。

        ★事件屏是**缺陷 C** 的核心：此前它不在返回值内，于是复盘教训**永远作用不到事件屏**，
        这正是用户说的「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。
        """
        targets: set[int] = set()
        if not tokens:
            return targets
        if action_type == "choose_map_node":
            nodes = payload.get("travelable_nodes")
            if not isinstance(nodes, list) or not nodes:
                nodes = payload.get("available_nodes")
            for position, node in enumerate(nodes if isinstance(nodes, list) else []):
                if not isinstance(node, dict):
                    continue
                raw = node.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                # **必须复用**既有别名表：mod 真实下发的是 `RestSite`/`Treasure` 这类枚举名，
                # 而教训 token 是规范短键（`rest`/`treasure`）。只 lower() 不做归一会永远匹配不上——
                # 这正是"短键能重排、真实对局却毫无反应"的根因（见 `_map_node_weight_key`）。
                node_type = self._map_node_weight_key(node)
                if node_type and node_type in tokens:
                    targets.add(index)
        elif action_type == "choose_reward_card":
            cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
            if not cards:
                cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
            # ★I3 有区分度的奖励选牌着力点 —— 修掉「全罚等于没罚 / 偏罚」两种退化。
            #
            # 病因：真实最高频的教训（9/10 条）抽出 `["skill","attack"]`，而奖励屏常给出
            # attack/skill/power 三选一 ⇒ 前两张**全被罚**，只剩 power 没被罚。
            # 实测（t10 `g4_degeneracy`）：区分度 3/3 → **1/3**，且**恒定**把玩家推向 power，
            # 与反思本意（「先垫格挡再输出」是**顺序**）毫无关系。
            # `_apply_run_lessons:4467` 的 `kept + demoted if kept else ranked` 对此无能为力：
            # 只要还剩 1 个未罚项，它就会把其余全部推到后面 —— 这是**偏罚**，比 no-op 更危险。
            #
            # 修法：**只有当惩罚集不至于把候选压成单一选择时才应用**。
            # 判据是"留下 ≥2 个未被罚的候选"：低于此数就说明这次匹配**没有区分度**
            # （罚掉了几乎所有选项 ⇒ 剩下的那一个是运气，不是判断），于是本屏放弃降权、返回原序。
            # 保守性优先：宁可不动，也不要用一条压平语义的教训去改选牌。
            candidates: list[int] = []
            matched: set[int] = set()
            for position, card in enumerate(cards):
                if not isinstance(card, dict):
                    continue
                raw = card.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                candidates.append(index)
                card_type = str(card.get("card_type") or "").strip().lower()
                rarity = str(card.get("rarity") or "").strip().lower()
                if "curse" in tokens and rarity == "curse":
                    matched.add(index)
                # ★ 六维缺口的「牌组质量」着力点（2026-10-19）：
                #   「这局基础牌和诅咒太多，下局我想早点升级关键牌、删掉废牌」
                #   ⇒ `quality` token ⇒ 把**会拉低牌组质量**的奖励降权
                #     （诅咒牌 + 已持有的基础牌再拿一张）。
                #
                # ★ 为什么这是**正确方向**：`quality` 缺口的定义就是
                #   "基础牌没升级 + 诅咒太多"（`_deck_gap_vector` 的 gap_quality），
                #   所以"别再拿基础牌/诅咒"正是它该产生的行为。
                elif "quality" in tokens and (rarity == "curse" or _is_basic_card_like(card)):
                    matched.add(index)
                elif card_type and card_type in tokens:
                    matched.add(index)
            if len(candidates) - len(matched) >= 2:
                targets |= matched
        elif action_type == "choose_event_option":
            # ★缺陷 C：让复盘教训能作用于**事件屏**。
            # 此前事件屏不在本函数覆盖范围内 ⇒ `avoid` 对事件决策**零影响**
            # （inquisitor t12 实测：有无 avoid 的 plan() 逐字相同）。
            # 玩家口径：「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。
            #
            # 判据与路线/选牌同源：教训 token 命中选项文本所表达的**后果类型**。
            # 复用既有的 `_LESSON_KEYWORD_MAP` 语义（curse/shop/rest/...），
            # 但事件选项的文案是自然语言（"失去 5 点生命上限，获得 100 金币"），
            # 因此这里按**关键词共现**匹配，而不是等值比较。
            options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
            for position, option in enumerate(options):
                if not isinstance(option, dict):
                    continue
                raw = option.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                text = self._event_option_text(option).lower()
                if not text:
                    continue
                # 教训 token -> 事件选项文本的后果关键词
                for token in tokens:
                    keywords = self._EVENT_CONSEQUENCE_KEYWORDS.get(token)
                    if not keywords:
                        continue
                    if any(keyword in text for keyword in keywords):
                        targets.add(index)
                        break
        elif action_type == "choose_shop_item":
            # ★★ 让复盘教训能作用于**商店屏**（此前完全无着力点）。
            #
            # 病因：本函数没有 `choose_shop_item` 分支 ⇒ `avoid` 对商店决策**零影响**，
            # 教训学不到"这局缺格挡"就永远改不了"该不该买格挡牌"。
            # 已由 `test_shop_screen_has_no_lesson_fulcrum` 钉住该缺口。
            #
            # ★ 与其它屏的**本质差别**：路线/选牌屏是"**规避**某类选项"（降权），
            #   而商店的教训是"**别买**某类东西" —— 语义仍是降权，故复用同一机制。
            #
            # 判据：教训 token 映射到**牌组维度**，再看这件商品补的是不是那一维。
            #   例：「这局栽在精英手里」→ `elite` token → 不对应具体维度 ⇒ **不动商店**
            #       （商店里没有"精英"这类商品，硬匹配只是噪声）
            #   「早点补格挡浓度」→ `skill` token → 格挡维 ⇒ 把"纯输出"类商品降权
            #
            # ⚠️ 刻意**保守**：只在 token 明确指向某一维时才动（`_LESSON_TOKEN_TO_SHOP_DIM`），
            #   其余 token 一律不动 —— 商店是花金币的地方，误降权等于白花/不花，
            #   代价比路线屏更高（与全局"匹配不准就别动"一致）。
            dims = self._lesson_shop_dims(tokens)
            if dims:
                for position, item in enumerate(self._shop_payload_items(payload)):
                    if not isinstance(item, dict):
                        continue
                    raw = item.get("index")
                    index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                    # 这件商品补的维度（卡走 `_card_effect_dims`，遗物/药水走类别）
                    item_dims = self._shop_item_dims(item)
                    # ★ 降权的是"**没在补**该维度"的商品 —— 教训说缺格挡，
                    #   那买攻击牌就该被压后（把额度留给格挡牌）。
                    if item_dims and not (item_dims & dims):
                        targets.add(index)
        return targets

    #: 教训 token -> 商店商品的**牌组维度**。
    #:
    #: 只收"能明确对应到某一维"的 token。`elite`/`boss`/`monster`/`rest`/`treasure`
    #: 等**路线/敌人**类 token 刻意不收 —— 商店里没有对应的商品，
    #: 硬匹配只会变成噪声（与 `_EVENT_CONSEQUENCE_KEYWORDS` 的保守口径一致）。
    _LESSON_TOKEN_TO_SHOP_DIM: dict[str, tuple[str, ...]] = {
        # 「先垫格挡」「血量低优先保命叠甲」-> 缺格挡/生存
        "skill": ("defense",),
        # 「输出不够」「斩杀不了」-> 缺输出
        "attack": ("output",),
        # ★ 六维缺口新增（2026-10-19）：
        #   「几乎没有群伤牌，下局想早点拿群伤」-> 缺群伤
        #   ⇒ 商店里**没在补 aoe** 的商品被压后。
        "aoe": ("aoe",),
        # ⚠️ `quality` **刻意不映射到商店维度**。
        #
        # 我一度写了 `"quality": ("quality",)`，但实测它会造成**全罚**：
        #   * `_shop_item_dims` 只给商品标 output/defense/draw/energy/aoe，
        #     **永远不会**标 `quality`（质量不是"某张牌补的维度"，而是"牌组整体状态"）
        #   * 于是 `item_dims and not (item_dims & {"quality"})` 对**每一件**商品恒为真
        #   * 实测：`_indices_worth_penalizing({shop_cards:[打击,防御]}, "choose_shop_item", ["quality"])`
        #     -> `[0, 1]`（两件全罚）
        #
        # 全罚在加权排序里是**无操作**（所有项同时 +1 权重，相对序不变），
        # 但它会让"降权集"这个中间量失真，且一旦将来排序改成非线性就会变成真 bug。
        # ⇒ 撤掉。`quality` 的着力点在**奖励屏**（`_is_basic_card_like`，
        #   语义明确："别再拿基础牌/诅咒"），不在商店。
        #
        # 这正是全局口径「匹配不准就别动，避免把复盘变成噪声」的应用。
    }

    def _lesson_shop_dims(self, tokens: list[str]) -> set[str]:
        """教训 token 指向的**商店维度**集合（空集 = 本屏不该动）。"""
        dims: set[str] = set()
        for token in tokens:
            for dim in self._LESSON_TOKEN_TO_SHOP_DIM.get(token, ()):
                dims.add(dim)
        return dims

    def _shop_payload_items(self, payload: dict[str, Any]) -> list[Any]:
        """商店屏的可买项（卡/遗物/药水），按 mod 下发的顺序拼成一个候选序列。

        ★ 顺序必须与 `_shop_affordable_items` 一致（卡 → 遗物 → 药水），
          否则 `index` 会与真实的商品下标错位。
        """
        out: list[Any] = []
        for key in ("shop_cards", "shop_relics", "shop_potions"):
            items = payload.get(key)
            if isinstance(items, list):
                out.extend(items)
        return out

    #: 药水/遗物**类别** -> 牌组维度。
    #:
    #: 卡牌有 `_card_effect_dims` 可直接映射，药水/遗物没有 ⇒ 需要这张表。
    #: 键取自 `_potion_category` 的返回值（aoe/defense/heal/cleanse/burst/draw/other）。
    #: `burst` 映射到 `output`（单体爆发就是输出）；`heal`/`cleanse` 不对应六维中的任何一维
    #: （六维里没有"续航"）⇒ 刻意留空，表示"这类商品在维度判据下**不动它**"。
    _POTION_CATEGORY_TO_SHOP_DIM: dict[str, tuple[str, ...]] = {
        "defense": ("defense",),
        "burst": ("output",),
        "aoe": ("aoe",),
        "draw": ("draw",),
        # heal / cleanse / other：六维里没有对应项 ⇒ 不参与降权
    }

    def _shop_item_dims(self, item: dict[str, Any]) -> set[str]:
        """这件商店商品补的是哪些**牌组维度**（读不出就返回空集 ⇒ 不动它）。"""
        # 卡牌：直接复用既有映射（攻击→输出、格挡→格挡、抽牌→抽牌…）
        if str(item.get("card_id") or "") or str(item.get("card_type") or ""):
            return set(self._card_effect_dims(item))
        # 遗物/药水：走类别映射
        category = self._potion_category(item)
        return set(self._POTION_CATEGORY_TO_SHOP_DIM.get(category, ()))

    #: 教训 token -> **事件选项文案**里表示该后果的关键词（缺陷 C 用）。
    #:
    #: 与 `_LESSON_KEYWORD_MAP`（面向路线/选牌屏的简短词）不同：事件选项是自然语言长句，
    #: 且中英混排，所以要单列一张表。刻意保守 —— 只收"语义明确、误判代价高"的几类，
    #: 匹配不准就不动（与 `_indices_worth_penalizing` 的整体口径一致）。
    _EVENT_CONSEQUENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
        # 诅咒：永恒话题，且游戏侧已证变换期望近零（见 `_TRANSFORM_CURSE_SCORE` 注释）
        "curse": ("诅咒", "curse"),
        # ★生命**上限**损失：用户报告的核心痛点，优先级高于普通掉血
        "max_hp": ("生命上限", "最大生命", "上限", "max hp", "maximum health", "max. hp"),
        # 生命：掉血（一次性、可恢复）—— 用户报告的核心痛点
        "hp": ("失去生命", "掉血", "失去", "lose hp", "lose ", "生命值", "血量"),
        "skill": ("格挡", "防御", "护盾", "block"),
        "attack": ("伤害", "攻击", "damage", "attack"),
        # 金币：复盘里"攒钱"类教训应当能影响"花钱/得钱"的选项
        "shop": ("金币", "gold", "购买", "buy", "花费", "spend"),
        "rest": ("回复", "回血", "heal", "休息", "生命"),
        "treasure": ("遗物", "relic", "宝箱", "chest"),
        "elite": ("精英", "elite"),
    }

    def _rank_map_nodes(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> list[int]:
        """与 _preferred_map_index 同口径：分数高的在前，同分时下标小的在前。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes")
        if not isinstance(nodes, list) or not nodes:
            nodes = payload.get("available_nodes")
        nodes = nodes if isinstance(nodes, list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        route_policy = self._route_policy(preferences)

        scored: list[tuple[int, int]] = []
        for position, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            raw = node.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_map_node(node, route_policy, hp_ratio, gold), index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]

    def _rank_reward_cards(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> list[int]:
        """与 _preferred_reward_option 同口径（含 guidance 调整）：分数高的在前，同分时下标小的在前。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward_cards = self._reward_candidates(summary_context)
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        guidance_override = self._guidance_override_from_payload(summary_context)

        scored: list[tuple[int, int]] = []
        for position, card in enumerate(reward_cards if isinstance(reward_cards, list) else []):
            if not isinstance(card, dict):
                continue
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            if guidance_override.get("prefer_defense"):
                if self._card_block(card) > 0:
                    score += 18
                if self._reward_dynamic_value(card, "HpLoss") > 0:
                    score -= 12
            if guidance_override.get("prefer_attack"):
                if self._card_damage(card) > 0:
                    score += 14
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]

    def _rank_remove_options(self, summary_context: dict[str, Any]) -> list[int]:
        """与 `_preferred_remove_option` **同口径**：最该删的在前。

        ★ 修一个真实缺陷（2026-10-17）：本函数原为
        `scored.sort(key=lambda item: (-item[0], -item[1]))`，即**分数高者在前**，
        与 `_preferred_remove_option` 的 `min((score, -index))`
        （**分数低者优先删**）**方向相反**，但 docstring 却写"同口径"。

        后果：`_score_remove_card` 对诅咒 = −100（最低 ⇒ 最该删），
        而排序把它放**最后** ⇒ `ordered_candidates` 的扰动候选里，
        `items[1]`（mischievous 档位会选它）指向的是**最不该删的卡**（如能量卡 +70）。
        实测：`[诅咒, 打击, 储能]` ⇒ preferred=0(诅咒) 正确，但 rank=[2,1,0]
        ⇒ 扰动时优先删掉储能卡。

        现在改为与 preferred 同口径（`(score, -index)` 升序），
        ⇒ 扰动候选顺序变为"次该删 → 再次该删"，与决策对齐。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        scored: list[tuple[int, int]] = []
        for position, card in enumerate(selection_cards):
            if not isinstance(card, dict) or self._is_unremovable_card(card):
                continue
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_remove_card(card), index))
        # 与 `_preferred_remove_option` 完全同序：分数低者在前（最该删），
        # 同分时下标**大**者在前（与 preferred 的 `-card_index` 取 min 等价）。
        scored.sort(key=lambda item: (item[0], -item[1]))
        return [index for _score, index in scored]

    def _rank_upgrade_options(self, summary_context: dict[str, Any]) -> list[int]:
        """与 `_preferred_upgrade_option` **同口径**：最该升的在前。

        ★ 刻意与 `_rank_remove_options` 方向相反 —— 升级要"最有价值"，
        删牌要"最没价值"。第 0 个必须等于规划器真正会选的那张，
        否则扰动层的"不扰动 == 取第 0 个"不变量会被破坏。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        scored: list[tuple[int, int]] = []
        for position, card in enumerate(selection_cards):
            if not isinstance(card, dict) or self._item_blocked(card):
                continue
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_upgrade_card(card), index))
        # 与 `_preferred_upgrade_option` 的 `max((score, -index))` 等价：
        # 分数**高**者在前，同分时下标**小**者在前。
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]

    def ordered_candidates(self, context: dict[str, Any], operation: Any) -> list[int]:
        """给扰动层的候选列表，**最优在前**。

        * 第 0 个一定就是操作自己选的那个下标——所以"不扰动"== 取第 0 个 == 改动前的行为；
        * 其余候选一律取自该屏的**合法**下标集合；
        * 拿不到任何合法替代时只返回 ``[最优]``，扰动层据此放弃（永远退得回最优）；
        * 操作本身不带下标（claim_reward / proceed 之类）时返回空列表。
        """
        planned = self._operation_dict(operation)
        action_type = str(planned.get("action_type") or "").strip().lower()
        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        best = self._safe_int(kwargs.get(self.index_key_for(action_type)))
        if best is None:
            return []

        legal = self.legal_option_indices(context, operation)
        ranked = self._ranked_option_indices(context, operation)
        if ranked and legal:
            legal_set = set(legal)
            pool = [index for index in ranked if index in legal_set]
        else:
            pool = list(ranked) if ranked else list(legal)
        return [best] + [index for index in pool if index != best]

    def build_perturbed_operation(self, context: dict[str, Any], operation: Any, candidate: int) -> dict[str, Any] | None:
        """把操作的下标换成 ``candidate``，并补齐随之变化的附属参数。

        战斗卡牌的目标**必须跟着新卡走**：沿用旧卡的目标可能超出新卡的 ``valid_target_indices``，
        那就是一个非法动作。换不出合法操作时返回 ``None``，调用方放弃扰动、退回最优。
        """
        planned = self._operation_dict(operation)
        action_type = str(planned.get("action_type") or "").strip().lower()
        kwargs = planned.get("kwargs") if isinstance(planned.get("kwargs"), dict) else {}
        key = self.index_key_for(action_type)
        best = self._safe_int(kwargs.get(key))
        if best is None or candidate == best:
            return None

        new_kwargs = dict(kwargs)
        new_kwargs[key] = candidate
        if action_type == "play_card":
            _summary_context, _strategy_context, snapshot = self._context_parts(context)
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
            enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
            card = next(
                (item for item in hand if isinstance(item, dict) and self._safe_int(item.get("index")) == candidate),
                None,
            )
            if card is None or not bool(card.get("playable")):
                return None
            new_kwargs.pop("target_index", None)
            target_index = self._preferred_target_for_card(card, enemies)
            if target_index is not None:
                new_kwargs["target_index"] = target_index

        perturbed = dict(planned)
        perturbed["kwargs"] = new_kwargs
        return perturbed


__all__ = ["STS2HeuristicPlanner"]
