"""★死亡归因：把一局的损血轨迹变成**结构化死因**，供反思消费。

## 为什么单独一个模块

玩家的口径：「教训是需要反思整个链路的……遇到群怪就死就反思有没有拿 aoe 卡，
遇到 boss 和精英就死需要反思选卡的数值和强化卡选择」。
「死亡归因」是其余五维的**触发器** —— 先知道"死在什么上"，
才知道该反思哪一维（群怪→AOE、精英→数值与强化、长局→成长、短轴→爆发）。

而现有 `_heuristic_lessons`（`catgirl_memory.py:519`）**只读 `decision_memory` 的
reason 文本**，从不读牌组、不读分层损血、不读敌人类型 ⇒
50 局真实记忆里三条死亡教训全是同一句兜底句。

## 数据来源（实测可用性：50 局 / 1588 条 route）

`route[]` 每条：`room / screen / floor / hp_start / hp_end / lowest_hp /
hp_lost / cards_gained / relics_gained / potions_gained / ended_by`
（`room` 100% 有值，`hp_lost > 0` 占 92%）

`hp` 块：`samples / max_hp / min_hp / final_hp / total_lost / loss_events /
heaviest[{room, screen, floor, hp_before, hp_after, lost, at_step}]`

## ★★ 关键陷阱：非战斗房间污染归因

实测跨局"累计损血最高"排名：

    room:MAP      15435 血   ← ❌ 非战斗（走路）
    NEOW           6505      ← ❌ 起始事件
    room:REST      5958      ← ❌ 休息
    room:SHOP      2817      ← ❌ 商店
    FUZZY_WURM_CRAWLER 1843  ← ✅ 真敌人

若不滤掉，会得出「最该防的是地图和商店」这种荒谬结论。
非战斗房的"损血"多半是事件代价/诅咒，**语义上与战斗损血不是一回事**，
不该混进"该拿什么牌"的归因。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "is_combat_room",
    "classify_death",
    "build_death_attribution",
    "attribution_to_lessons",
    "attribution_to_record",
    "ACT_FLOOR_SPANS",
    "DEATH_KIND_AOE",
    "DEATH_KIND_ELITE",
    "DEATH_KIND_BOSS",
    "DEATH_KIND_ATTRITION",
    "DEATH_KIND_BURST",
    "DEATH_KIND_UNKNOWN",
    "DEATH_RECORD_KEY",
    "ATTRIBUTION_VERSION",
]

#: 归因类别。前五个对应玩家点名的维度，`UNKNOWN` 是"证据不足"的诚实答案。
DEATH_KIND_AOE = "aoe"              # 死于群怪 → 反思有没有拿 AOE
DEATH_KIND_ELITE = "elite"          # 死于精英 → 反思选卡数值与强化
DEATH_KIND_BOSS = "boss"            # 死于 Boss
DEATH_KIND_ATTRITION = "attrition"  # 长轴消耗战，多房间累计失血 → 反思成长/续航
DEATH_KIND_BURST = "burst"          # 短轴爆发：单个房间内被快速打空 → 反思爆发与防御浓度
DEATH_KIND_UNKNOWN = "unknown"      # 证据不足，**不编造**

#: 非战斗房间的**占位标识**（`service._room_identity` 在无 id 时返回 `room:<SCREEN>`）。
_NON_COMBAT_PREFIX = "room:"

#: 已知的非战斗事件/房间 id（大小写不敏感）。这些**不是**敌人。
#: 刻意保守：只收确认过的；其余按"有 + 或像敌人"处理。
_NON_COMBAT_ROOMS: frozenset[str] = frozenset({
    "neow",           # 起始事件
    "map",            # 地图
    "rest",           # 篝火
    "shop",           # 商店
    "chest",          # 宝箱
    "treasure",
    "event",          # 事件
    "card_selection",
    "boss_chest",
    "unknown",
})

#: 房间名里表示"精英"的标记（真正的类型信息在 `MapPointType`，但 route 里只有房名）。
#: 上限：拿不到就归到 ELITE 或 UNKNOWN，不硬猜。
_ELITE_HINTS = ("elite", "精英", "gremlin_nob", "book_of_stabbing")

#: 精英敌人 id 白名单 —— 比"名字里含 elite"更可靠（游戏里精英的 enemy_id 常不含 elite）。
#: 来源：本项目真实记忆里反复出现且损血大的非 Boss 强敌。
#:
#: ★★ 本名单**无法用数据证明**（诚实记录，勿再凭印象扩）：
#:   游戏侧的对局记录里**没有**精英标记 —— `encounter_db.json` 775 条遭遇的
#:   `node_type` 全是 `null`，`enemy_rows` 也没有 is_elite 字段；
#:   楼层分布同样分不开（精英房在 StS 里是**地图随机节点**，不固定在某一层）。
#:   实测：候选精英与非精英的楼层区间、是否单敌、max_hp **三段全部重叠**，无判据可分。
#:   ⇒ 能做证伪的只有**一个方向**："出现在幕末层的才可能是 Boss"（见 `_KNOWN_BOSSES`），
#:     以及"名字在普通层大面积出现 ⇒ 不是 Boss"。反过来证明"是精英"没有数据支撑。
#:
#: ⇒ 因此这里只做**已证伪者的删除**（下列 5 个是本项目实测的常见小怪，
#:   楼层分布与普通遭遇完全一致），不再新增任何未经证实的名字：
#:
#:     名字                实测出现楼层（encounter_db / 记忆库）
#:     VINE_SHAMBLER       8,11,13,14,15        ← 全项目最常见敌人之一（615 血）
#:     SELF_HELP_BOOK      3,6,7,15,28,29
#:     HAUNTED_SHIP        5,11,14,15
#:     MAWLER              6,8,11,12,13,15
#:     BYGONE_EFFIGY       11,12,13,14
#:     SOUL_FYSH           17（**幕末层**）⇒ 它是 act 0 的 Boss，已移入 `_KNOWN_BOSSES`
#:
#:   它们原先被当成精英，实测造成 59 场误判（此前的统计口径，任务书给出的数字）；
#:   判成 elite ⇒ 反思 output/quality，而真实形态是"被小怪磨死" ⇒ 该反思防御浓度。
#:
#: ⚠️ 余下名字同样**未经证实**（数据无法证实/证伪），保留是因为删掉会让真正的
#:   精英战退化成 attrition/burst；它们都不会影响 Boss 判定（Boss 判据在它们之前）。
_KNOWN_ELITES: frozenset[str] = frozenset({
    "byrdonis", "phrog_parasite",
    "phantasmal_gardener", "terror_eel", "fogmog",
    "slithering_strangler", "snapping_jaxfruit",
})

# ── ★★ Boss 的结构性判据：幕末层（比记住 Boss 名字鲁棒）────────────────────────
#
# 「死在 Boss 层」的正确判据**不是**"这个敌人叫什么"，而是"**这一局死在哪一层**"：
# Boss 住在**每一幕的最后一层**，这是地图结构，不随角色/种子/Boss 身份变化。
#
# ## 数据来源（775 条真实遭遇 + 50 局记忆库，双向核对）
#
# `encounter_db.json` 的 `encounter_key` 同时带 `act_id` 与 `floor`，
# 按幕聚合得到的楼层区间（实测）：
#
#     act 0 : floor 2..17    ← 幕末 17
#     act 1 : floor 19..33   ← 幕末 33
#     act 2 : floor 35..48   ← 幕末 48
#
# 记忆库 50 局的 `act` 字段与之交叉一致（floor=17 的局 `act` 均为 0）。
# ⇒ 幕末层 = 每一幕的最大层 = {17, 33, 48}，且**幕末层上只出现过 Boss 遭遇**：
#
#     floor 17: VANTOM / LAGAVULIN_MATRIARCH / WATERFALL_GIANT /
#               CEREMONIAL_BEAST / SOUL_FYSH / KIN_PRIEST+KIN_FOLLOWER
#     floor 33: THE_INSATIABLE / KNOWLEDGE_DEMON / CRUSHER+ROCKET
#     floor 48: AEONGLASS
#
# ## 为什么必须补这一条（实测：旧判据几乎全漏）
#
# 本快照的 50 局里 **30 局**死在幕末层（floor 17 ×20 / 33 ×9 / 48 ×1），
# 而只按房名判 Boss 的 `_looks_boss` 在这 30 局里**只认出 1 局**：
#
#     判决分布 = attrition 25 / elite 3 / burst 1 / boss 1
#
# 后果不是"数字难看"，而是**反思方向被带反**：
# `DEATH_KIND_TO_DIMS["boss"] = ("output","quality")`，而 `attrition` 是
# `("defense","quality")` ⇒ 一直劝"多垫格挡"，真问题却是"升级关键牌、补数值"。
#
# ★ 判据设计：**结构优先、名字只是补充**（见 `classify_death` 的第 0 / 1 条）。
#   名字白名单对"还没见过的新 Boss"天然失效，幕末层不会。
ACT_FLOOR_SPANS: tuple[tuple[int, int], ...] = (
    (2, 17),    # act 0（floor 1 是 NEOW 起始事件，非战斗）
    (19, 33),   # act 1
    (35, 48),   # act 2
)

#: 每一幕的**最大层**（幕末层）—— Boss 所在层。由 `ACT_FLOOR_SPANS` 推导，
#: 不另写一份数字（同源：改一处即可，避免两张表漂移）。
_ACT_FINAL_FLOORS: frozenset[int] = frozenset(end for _start, end in ACT_FLOOR_SPANS)

#: Boss 的 enemy_id。★ 实测教训：`VANTOM` 在真实记忆里出现过
#: （玩家点名「死在Vantom手下」），但房名里**不含** "boss" 字样 ⇒
#: 若只靠 `_BOSS_HINTS` 会把它误判成精英（实测确实误判过：
#: floor=17 死于 Vantom 的一局被归成 `elite`）。故必须显式列出。
#:
#: ★ 入榜纪律（**已验证**，勿凭印象加）：名单里的每个 id 都必须满足
#:   "在 775 场遭遇（`encounter_db.json`）+ 50 局记忆库（`catgirl_memory.json`）
#:    的**全部**出现记录里，楼层集合 ⊆ 幕末层"。
#:   在普通层出现过的名字**一律不许**进这里 —— 那会把"被小怪磨死"改成"被 Boss 打死"，
#:   错误只是从一头搬到另一头。核对方式见 `tests/test_death_attribution.py`
#:   里 `test_whitelist_names_all_satisfy_the_floor_invariant`
#:   与 `test_boss_name_on_a_normal_floor_is_not_boss`。
_KNOWN_BOSSES: frozenset[str] = frozenset({
    # ── 旧名单（保留；其中 architect/heart/queen/king/champ 在本项目数据里未出现，
    #    但已被 `_is_act_final_floor` 的楼层闸门保护，不会在普通层误判）──
    "vantom", "architect", "the_architect", "heart", "the_heart",
    "queen", "king", "champ", "corrupt_heart",
    # ── 实测补充：只在幕末层出现（逐名核对过楼层集合）──
    "ceremonial_beast",      # floor {17}
    "waterfall_giant",       # floor {17}
    "lagavulin_matriarch",   # floor {17}
    "kin_priest",            # floor {17}  ← 单看像小怪，实测 19 场全在 17 层
    "kin_follower",          # floor {17}  ← 同上，23 场全在 17 层
    "the_kin",               # 数据里的 `boss_id="THE_KIN_BOSS"`（组合名呈 KIN_* 双敌，
                             # 本名在 route 里未出现过 ⇒ 惰性项，只为名字兜底路径留位）
    "soul_fysh",             # floor {17}  ← ★ 原先被错列在 `_KNOWN_ELITES` 里
    "the_insatiable",        # floor {33}
    "knowledge_demon",       # floor {33}
    "crusher",               # floor {33}（CRUSHER+ROCKET 双敌 Boss 的一半）
    "rocket",                # floor {33}
    "aeonglass",             # floor {48}
})

#: Boss 的 enemy_id 特征（兜底，只收**不会误伤普通敌人**的形态）。
_BOSS_HINTS = ("boss", "act_", "_boss")


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def is_combat_room(room: str) -> bool:
    """这个 `room` 标识是不是**一场战斗**。

    判据（按可靠性排序）：

    1. `room:` 前缀 ⇒ 非战斗（`_room_identity` 的占位兜底）
    2. 命中已知非战斗房名集合 ⇒ 非战斗
    3. `+` 分隔（如 `LEAF_SLIME_M+LEAF_SLIME_S`）⇒ 战斗（且是**群怪**）
    4. 其余非空 ⇒ 视为战斗（enemy_id / event_id 里的敌人名）

    ★ 第 4 条是**保守取真**：route 里能留下房名的非战斗房，
      基本都被 `room:` 前缀或白名单覆盖；漏网的多是敌名。
      宁可把罕见事件误算成战斗，也不要把真敌人（玩家最该防的）滤掉。
    """
    text = _norm(room)
    if not text:
        return False
    if text.startswith(_NON_COMBAT_PREFIX):
        return False
    if text in _NON_COMBAT_ROOMS:
        return False
    return True


def _is_multi_enemy(room: str) -> bool:
    """这个房间是不是**群怪**（多个敌人）。

    `service._room_identity` 用 `+` 连接同场多个敌人 id
    （注释原文：「如 `LEAF_SLIME_M+LEAF_SLIME_S`」），所以 `+` 是可靠的多敌信号。
    """
    return "+" in str(room or "")


def _looks_elite(room: str) -> bool:
    text = _norm(room)
    if any(hint in text for hint in _ELITE_HINTS):
        return True
    return any(part in _KNOWN_ELITES for part in re.split(r"[+|]", text) if part)


def _looks_boss(room: str) -> bool:
    """**只看名字**：这一间房像不像 Boss（不含楼层证据）。

    ★ 这不是 Boss 的最终判据 —— 名字只能证明"像"，不能证明"是"。
      定死因请用 `_died_on_act_final_floor()`（结构性）与 `_is_boss_room()`。
    """
    text = _norm(room)
    if any(part in _KNOWN_BOSSES for part in re.split(r"[+|]", text) if part):
        return True
    return any(hint in text for hint in _BOSS_HINTS)


def _is_act_final_floor(floor: Any) -> bool:
    """这一层是不是**某一幕的最大层**（= Boss 层）。

    结构性判据的原子谓词。`bool`/`float`/`str` 一律当"不是"：
    楼层只在是真正的整数层号时才有证据力（`"17"` 这种字符串不做隐式转换，
    宁可漏判也不要把非楼层字段当成层号）。
    """
    if isinstance(floor, bool) or not isinstance(floor, int):
        return False
    return floor in _ACT_FINAL_FLOORS


def _fatal_floor(deaths: list[dict[str, Any]], total_floors: int) -> int | None:
    """这局**死在**哪一层。

    优先用 `total_floors`（一局记录的"到达层数"；死局里它就是死亡所在层）；
    它缺失时退回"损血事件里的最大层"。
    """
    if isinstance(total_floors, int) and not isinstance(total_floors, bool) and total_floors > 0:
        return total_floors
    floors = [
        e.get("floor") for e in deaths
        if isinstance(e, dict) and isinstance(e.get("floor"), int) and not isinstance(e.get("floor"), bool)
    ]
    return max(floors) if floors else None


def _died_on_act_final_floor(deaths: list[dict[str, Any]], total_floors: int) -> bool:
    """★★ 结构性 Boss 判据：**死在幕末层** ⇒ 那一场就是 Boss 战。

    为什么这不是"换一种硬编码"：层号不是 Boss 的名字，而是**地图结构**
    （每一幕的最后一层）。换角色、换种子、出全新 Boss 都仍然成立 ——
    白名单对新 Boss 必然失效，幕末层不会。

    两个条件**同时**成立才算（缺一不可，都是为了防误判）：

      1. 死亡所在层是本幕最大层（`_ACT_FINAL_FLOORS` = {17, 33, 48}）
      2. 该层确实有**战斗**损血事件

    ★ 第 2 条是"过boss没死"的护栏：一局可能 floor 17 打掉血、floor 23 才死，
      这时 `total_floors=23` 已经让第 1 条不成立；但若 `total_floors` 缺失，
      第 2 条能挡住"事件在幕末层把它打死、那一层其实没有战斗"的形态。

    ★ 若损血事件**完全没有**层号（只有 `total_floors`），就不做第 2 条 ——
      那是"层数信息不全"，不是"该层没有战斗"，此时 `total_floors` 是唯一证据。
    """
    fatal = _fatal_floor(deaths, total_floors)
    if not _is_act_final_floor(fatal):
        return False
    known = [
        (e.get("floor"), str(e.get("room") or ""))
        for e in deaths
        if isinstance(e, dict) and isinstance(e.get("floor"), int) and not isinstance(e.get("floor"), bool)
    ]
    if not known:
        return True
    return any(floor == fatal and is_combat_room(room) for floor, room in known)


def _is_boss_room(room: str, floor: Any = None) -> bool:
    """**单间房**是不是 Boss 战（用于计数/审计 `boss_rooms`）。

    ★ 楼层是闸门，名字只是补充 —— 这是**防过拟合**的关键：
      若这间房的楼层明确且**不是幕末层**，那它就不是 Boss，名字说什么都不算。
      （否则把普通层的 `KIN_PRIEST` 之类加进白名单，就会把"被小怪磨死"
       改判成"被 Boss 打死"，错误只是从一头搬到另一头。）
      只有在**完全没有楼层证据**时才退回纯名字判断（保守取真）。

    ★ 定死因用的是 `_died_on_act_final_floor()`（看"死在**哪一层**"），
      不是本函数 —— 本函数只看单间房，分不清"打过 Boss 但活着出来了"。
    """
    if _is_act_final_floor(floor):
        return is_combat_room(room)     # 幕末层的战斗房必是 Boss 战
    if not _looks_boss(room):
        return False
    return floor is None


def _combat_entries(route: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留**战斗**房间的 route 条目（滤掉 `room:MAP` 这类污染）。"""
    out: list[dict[str, Any]] = []
    for entry in route:
        if not isinstance(entry, dict):
            continue
        if is_combat_room(str(entry.get("room") or "")):
            out.append(entry)
    return out


def classify_death(
    *,
    deaths: list[dict[str, Any]],
    total_floors: int,
    lost_total: int,
) -> str:
    """由战斗房间的损血事件判定**死因类别**。

    Args:
        deaths: 战斗房间的损血事件（含 `room` / `floor` / `lost`），按时间序。
        total_floors: 本局到达的层数。
        lost_total: 战斗房间累计损血。

    ★ 判据顺序（先问最具体的）：

      0. **★ 死在幕末层 ⇒ Boss**（`_died_on_act_final_floor`，**结构性证据**） ——
         最强的一条，因为"Boss 住在每一幕的最后一层"是地图结构而不是名字。
         ★ 它排在第 1 条**之前**：旧版只按房名判 Boss，实测 30 局幕末层死亡里
         只认出 1 局，其余几乎全被第 4 条"长轴消耗"吞掉 ——
         于是反思一直劝"多垫格挡"，而真问题是"升级关键牌、补数值"。
         ⇒ 楼层证据优先于"最重那一间叫什么"：被 Boss 打死的一局，
           最重损血房完全可能是前面某只小怪（实测 30 局里 22 局如此）。
      1. **Boss（按名字）/ 精英**：具体敌人身份是强证据，优先于统计判据
         （实测：Vantom 一局若不先判 Boss 会被后面的统计规则吞掉）。
         ★ 名字判 Boss 只在**没有楼层证据**时才算数（见 `_is_boss_room`）。
      2. **群怪为主**：多敌房的**合计**损血占战斗总损血 ≥ 50%
         ⇒ AOE 缺口（★见下方"为什么是合计而不是单房"）
      3. **单房被快速打空**（同一房损血占战斗总损血 ≥ 60%）⇒ 短轴爆发缺口
      4. **跨多房累计失血** ⇒ 长轴消耗（成长/续航缺口）
      5. 否则 UNKNOWN —— **不编造**

    ★★ 为什么群怪用"**合计**"而不是"最重那一间"（实测驱动）：

    真实 50 局数据显示，群怪房**很常见但不占主导** —— 典型分布：

        VINE_SHAMBLER                615  (18.9%)   ← 单敌，最重
        FUZZY_WURM_CRAWLER           561  (17.3%)
        EYE_WITH_TEETH+FOGMOG        230  ( 7.1%)   ★群怪
        LEAF_SLIME_M+LEAF_SLIME_S    189  ( 5.8%)   ★群怪
        FLYCONID+SNAPPING_JAXFRUIT   165  ( 5.1%)   ★群怪
        SLITHERING_STRANGLER+...      70  ( 2.2%)   ★群怪
        ─────────────────────────────────────────
        群怪合计                      654  (20.2%)

    单看"最重那间"永远选不到群怪（它们各自都小），
    但**群怪合计 20%** 是清楚的信号：这一局确实被多敌磨掉了不少血。
    ⇒ 用合计占比，判据才与真实数据形态匹配。

    阈值定 50%：低于此说明群怪只是"顺带损点血"，
    真正致死的是别的东西，不该把死因记到 AOE 头上（宁可少判也不要错判）。
    """
    if not deaths:
        return DEATH_KIND_UNKNOWN

    # 按房间聚合损血（同一场战斗可能有多条采样）
    per_room: dict[str, int] = {}
    for event in deaths:
        room = str(event.get("room") or "")
        lost = event.get("lost")
        if not isinstance(lost, (int, float)) or lost <= 0:
            continue
        per_room[room] = per_room.get(room, 0) + int(lost)

    if not per_room:
        return DEATH_KIND_UNKNOWN

    combat_lost = sum(per_room.values())
    if combat_lost <= 0:
        return DEATH_KIND_UNKNOWN

    worst_room, worst_lost = max(per_room.items(), key=lambda kv: kv[1])
    worst_share = worst_lost / combat_lost

    # ★ 死亡所在层（用于"名字判 Boss"的楼层闸门；完全没有层号时为 None）
    fatal_floor = _fatal_floor(deaths, total_floors)

    # 0. ★★ 结构性 Boss 判据：**死在幕末层** ⇒ 那一场就是 Boss 战。
    #    必须排在"最重那一间叫什么"之前：被 Boss 打死的一局，最重损血房
    #    经常是前面某只小怪（实测 30 局里 22 局如此），按名字永远看不到 Boss。
    if _died_on_act_final_floor(deaths, total_floors):
        return DEATH_KIND_BOSS

    # 1. Boss（按名字）/ 精英 —— 具体身份优先于统计判据。
    #    ★ 名字只判 Boss 的**兜底**：仅在完全没有楼层证据时使用。
    #      楼层明确却不是幕末层时，名字说什么都不算数 —— 否则"同名出现在普通层"
    #      就会把"被小怪磨死"改判成"被 Boss 打死"（防过拟合，见 `_is_boss_room`）。
    if fatal_floor is None and _looks_boss(worst_room):
        return DEATH_KIND_BOSS
    if _looks_elite(worst_room):
        return DEATH_KIND_ELITE

    # 2. 群怪**合计**占比够高 ⇒ AOE 缺口
    multi_lost = sum(lost for room, lost in per_room.items() if _is_multi_enemy(room))
    if multi_lost >= 0.5 * combat_lost:
        return DEATH_KIND_AOE

    # 3. 短轴：单房被快速打空
    if worst_share >= 0.6:
        return DEATH_KIND_BURST

    # 4. 长轴：跨多房累计失血（成长/续航缺口）
    #
    # ★ 层数门槛已**取消**（实测驱动）：原设 `total_floors >= 8`，
    #   导致 10 局"早期死亡"全部落到 UNKNOWN。看真实数据：

    #       [1] floor=5  6 房  282 血  worst=SHRINKER_BEETLE   share=0.23
    #       [4] floor=6  7 房  317 血  worst=NIBBIT            share=0.20
    #       [5] floor=7  8 房  756 血  worst=CUBEX_CONSTRUCT   share=0.31
    #       [7] floor=6  5 房  439 血  worst=SLUDGE_SPINNER    share=0.44

    #   形态与"长轴"**完全一致** —— 多房、无单一主因、最重房占比只有 20~44%。
    #   死在 5~7 层不代表"不是被磨死的"，只代表**磨死得更快**
    #   （这本身就是重要信息：低层就耗光血量 ⇒ 前期的输出/防御浓度都不够）。
    #   ⇒ 判据只看"**是否跨多房、无单一主因**"，不看层数。
    #
    #   为什么门槛是 3 房：2 房时"两场各半"与"单房爆发"难以区分，
    #   且样本太少，判成 burst 更保守（见上一条 worst_share >= 0.6）。
    if len(per_room) >= 3:
        return DEATH_KIND_ATTRITION

    # 5. 证据不足 —— **不编造**
    return DEATH_KIND_UNKNOWN


def build_death_attribution(run: dict[str, Any]) -> dict[str, Any]:
    """把一局的记录压成**结构化死因**。

    返回（键恒定，缺数据时给中性值 —— 调用方无需判空）：

        {
          "kind": str,              # 死因类别（见 DEATH_KIND_*）
          "outcome": str,           # defeat / victory / ...
          "floor": int,             # 到达层数
          "combat_rooms": int,      # 战斗房间数（已滤非战斗）
          "combat_lost": int,       # 战斗累计损血
          "worst_room": str,        # 最重损血房
          "worst_lost": int,
          "worst_share": float,     # 最重房占战斗损血比例
          "multi_enemy_rooms": int, # 群怪房间数
          "elite_rooms": int,
          "boss_rooms": int,
        }
    """
    route = run.get("route") if isinstance(run.get("route"), list) else []
    combat = _combat_entries(route)

    # 损血事件优先取 route 的 hp_lost；不足时回落到 hp.heaviest
    deaths: list[dict[str, Any]] = []
    for entry in combat:
        lost = entry.get("hp_lost")
        if isinstance(lost, (int, float)) and lost > 0:
            deaths.append({"room": str(entry.get("room") or ""), "floor": entry.get("floor"), "lost": int(lost)})
    if not deaths:
        hp = run.get("hp") if isinstance(run.get("hp"), dict) else {}
        for event in (hp.get("heaviest") if isinstance(hp.get("heaviest"), list) else []):
            if not isinstance(event, dict):
                continue
            room = str(event.get("room") or "")
            if not is_combat_room(room):
                continue
            lost = event.get("lost")
            if isinstance(lost, (int, float)) and lost > 0:
                deaths.append({"room": room, "floor": event.get("floor"), "lost": int(lost)})

    per_room: dict[str, int] = {}
    for event in deaths:
        per_room[event["room"]] = per_room.get(event["room"], 0) + event["lost"]
    combat_lost = sum(per_room.values())
    worst_room = max(per_room.items(), key=lambda kv: kv[1])[0] if per_room else ""
    worst_lost = per_room.get(worst_room, 0)

    floor = run.get("floor")
    total_floors = int(floor) if isinstance(floor, (int, float)) else 0

    rooms = [str(e.get("room") or "") for e in combat]
    multi_rooms = {r for r in rooms if _is_multi_enemy(r)}
    multi_lost = sum(lost for room, lost in per_room.items() if _is_multi_enemy(room))

    # 房名 -> 该房出现过的层号（取第一条，够用于"是不是幕末层"的闸门）
    room_floor: dict[str, Any] = {}
    for entry in combat:
        room = str(entry.get("room") or "")
        if room and room not in room_floor:
            room_floor[room] = entry.get("floor")

    return {
        "kind": classify_death(deaths=deaths, total_floors=total_floors, lost_total=combat_lost),
        "outcome": str(run.get("outcome") or ""),
        "floor": total_floors,
        "combat_rooms": len({r for r in rooms if r}),
        "combat_lost": combat_lost,
        "worst_room": worst_room,
        "worst_lost": worst_lost,
        "worst_share": round(worst_lost / combat_lost, 4) if combat_lost > 0 else 0.0,
        "multi_enemy_rooms": len(multi_rooms),
        "multi_enemy_lost": multi_lost,
        "multi_enemy_share": round(multi_lost / combat_lost, 4) if combat_lost > 0 else 0.0,
        "elite_rooms": sum(1 for r in rooms if _looks_elite(r)),
        "boss_rooms": sum(1 for r in rooms if _is_boss_room(r, room_floor.get(r))),
    }


# ── 落库（t38）：让死因判决可复现、可审计 ──────────────────────────────────────
#
# ★ 为什么必须落库（CANON §22「执行可验证性」）：
#   接线前 `build_death_attribution()` 只在 `service._build_gap_lessons` 里被**现算即丢** ——
#   只把 `kind` 传给牌组缺口量化，判决本身从不写进记忆库。
#   ⇒ 「死因分布」这类结论（如"attrition 占比下降 / aoe 首次出现"）**无法从已落盘产物复算**，
#     只能用一次性脚本现场跑一遍 ⇒ 那不是可验证的结论，只是一次性观察。

#: 记忆库里承载死因判决的**单键**。刻意只占一个顶层键，避免污染 runs[] 命名空间。
DEATH_RECORD_KEY = "death_attribution"

#: schema 版本。将来若改判据/字段含义，**升版本**即可让读者分辨"旧算法算的"与"新算法算的"。
#: ★ 这不是装饰：没有版本号时，历史记录的 `kind` 无法与当前 `classify_death` 对齐。
#:
#: v2（本次）：判据增加了**结构性 Boss 判据**（死在幕末层 ⇒ Boss）。这是一次
#: **判决口径变更** —— 同一份 route 数据在 v1/v2 下会得出不同的 `kind`
#: （实测 50 局：v1 的 31 局 Boss 层死亡里 30 局判成 attrition/elite/burst，
#: v2 判成 boss）⇒ 必须升版本，否则新旧记录的 `kind` 无法区分。
ATTRIBUTION_VERSION = 2


def attribution_to_record(attribution: dict[str, Any]) -> dict[str, Any]:
    """把 `build_death_attribution()` 的产物转成**可落库、可审计**的记录。

    ★★ 同源纪律（契约要求）：本函数**只做透传与补版本**，
      **绝不**重新判定 `kind`、也**不**新增任何阈值 ——
      一切结论必须来自既有函数（`classify_death` / `build_death_attribution`），
      否则记忆库里的"死因"会与实际决策链路里的"死因"变成两套互相矛盾的说法
      （这正是 mod 侧 `IsEventScreen` 同源教训的形态）。

    落库内容 = **结论 + 判据依据**（契约要求"不只写结论"）：
      · ``kind``              结论
      · ``worst_share``       最重房 / 战斗累计损血   → 判 burst 的门槛量（>=0.6）
      · ``multi_enemy_share`` 群怪合计 / 战斗累计损血 → 判 aoe 的门槛量（>=0.5）
      · ``combat_rooms``      跨了几房               → 判 attrition 的门槛量（>=3）
      · ``worst_room``        最强证据（Boss/精英按**具体敌人**判，优先于统计判据）
      · ``elite_rooms``/``boss_rooms`` → 判 boss/elite 的房间计数依据
    没有这些量，`kind` 就是一个不可审计的黑盒：无法回答"凭什么算成 attrition"。
    """
    src = attribution if isinstance(attribution, dict) else {}
    return {
        "version": ATTRIBUTION_VERSION,
        # ── 结论 ──
        "kind": str(src.get("kind") or DEATH_KIND_UNKNOWN),
        "outcome": str(src.get("outcome") or ""),
        "floor": int(src.get("floor") or 0),
        # ── 判据依据（可审计的核心）──
        "combat_rooms": int(src.get("combat_rooms") or 0),
        "combat_lost": int(src.get("combat_lost") or 0),
        "worst_room": str(src.get("worst_room") or ""),
        "worst_lost": int(src.get("worst_lost") or 0),
        "worst_share": float(src.get("worst_share") or 0.0),
        "multi_enemy_rooms": int(src.get("multi_enemy_rooms") or 0),
        "multi_enemy_lost": int(src.get("multi_enemy_lost") or 0),
        "multi_enemy_share": float(src.get("multi_enemy_share") or 0.0),
        "elite_rooms": int(src.get("elite_rooms") or 0),
        "boss_rooms": int(src.get("boss_rooms") or 0),
    }


def read_death_record(run: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """从一条 run 记录里读出死因判决，并**区分三种"没有"**（契约的向后兼容要求）。

    返回 ``(record, status)``，``status`` ∈：

      · ``"present"``  —— 有该键且是 dict ⇒ 正常读到（``record`` 非 None）
      · ``"absent"``   —— **旧数据**：根本没有这个键。t38 之前的 50 局全是这一类。
                          ★ 与 "unknown" **绝不可混同**：
                          "absent" = 当时没落库（信息缺失，原因在系统）；
                          "unknown" = 当时算了，但证据不足（原因在这局对局）。
                          把两者混起来，会让"旧数据缺字段"被误读成"这些局都证据不足"。
      · ``"malformed"``—— 有键但类型不对（不是 dict）⇒ 同样不当成有效判决。

    ★ 这是 t30 里 zero vs null 的同款要求：**"没有值"与"值是空"是两件事**。
    """
    if not isinstance(run, dict):
        return None, "absent"
    if DEATH_RECORD_KEY not in run:
        return None, "absent"
    raw = run.get(DEATH_RECORD_KEY)
    if not isinstance(raw, dict):
        return None, "malformed"
    return raw, "present"


def summarize_kinds(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """从**已落库**的记录统计死因分布（t38 的复现入口）。

    返回含三档计数的结构 —— 三档分开报，读者一眼能看出样本里有多少是不可判定的：

        {"present": n, "absent": n, "malformed": n, "kinds": {kind: count}}

    ★ 只读落库字段，**不重算**（重算就退回"现场判"的老路了）。
    """
    from collections import Counter

    kinds: Counter = Counter()
    present = absent = malformed = 0
    for run in runs if isinstance(runs, list) else []:
        record, status = read_death_record(run)
        if status == "present":
            present += 1
            kinds[str((record or {}).get("kind") or DEATH_KIND_UNKNOWN)] += 1
        elif status == "malformed":
            malformed += 1
        else:
            absent += 1
    return {
        "present": present,
        "absent": absent,
        "malformed": malformed,
        "kinds": dict(kinds.most_common()),
    }


#: 死因类别 -> 该反思的**维度**与教训模板。
#:
#: ★ 模板刻意指向"该看牌组的哪一维"，而不是"下次选防御节点"这类空话 ——
#: 玩家的原话：「不能单纯的说"这局优先选防御/回血节点"，需要多维」。
_DEATH_KIND_TO_LESSON: dict[str, tuple[str, str]] = {
    DEATH_KIND_AOE: (
        "aoe",
        "这局在群怪房被打空血，以后打多敌前先看看牌组够不够群伤，缺就先拿群伤卡",
    ),
    DEATH_KIND_ELITE: (
        "elite",
        "这局栽在精英手里了，以后碰精英前先确认输出数值够不够、关键牌升级了没有",
    ),
    DEATH_KIND_BOSS: (
        "boss",
        "这局没打过 Boss，以后进 Boss 前先把格挡垫厚、关键牌升级，别拿血硬换",
    ),
    DEATH_KIND_ATTRITION: (
        "attrition",
        "这局是跨房间被慢慢磨死的，以后早点补格挡浓度和回复手段，别一路硬吃伤害",
    ),
    DEATH_KIND_BURST: (
        "burst",
        "这局被单个敌人一回合打空，以后先垫够格挡再输出，血线低就直接喝药别省",
    ),
}


def attribution_to_lessons(attribution: dict[str, Any]) -> list[str]:
    """把结构化死因变成**可执行教训文本**（供注入 `strategy_directives.avoid`）。

    ★ 只在 `defeat`（或血量归零）时产出 —— 赢了不该产"别这样"的教训。
    ★ `UNKNOWN` 不产出教训 —— **证据不足就别编**，
      让上层继续用兜底句（这与"宁可没影响也不要乱改决策"的整体口径一致）。
    """
    kind = str(attribution.get("kind") or DEATH_KIND_UNKNOWN)
    if kind == DEATH_KIND_UNKNOWN:
        return []
    outcome = _norm(attribution.get("outcome"))
    final_hp = attribution.get("final_hp")
    died = outcome == "defeat" or (isinstance(final_hp, (int, float)) and final_hp <= 0)
    if not died:
        return []
    entry = _DEATH_KIND_TO_LESSON.get(kind)
    if not entry:
        return []
    return [entry[1]]
