"""★死亡归因契约测试（`death_attribution.py`）。

## 为什么需要这一组

玩家的口径：「教训是需要反思整个链路的……遇到群怪就死就反思有没有拿 aoe 卡，
遇到 boss 和精英就死需要反思选卡的数值和强化卡选择」。
**死亡归因是其余五维的触发器** —— 先知道"死在什么上"，才知道该反思哪一维。

而改造前 `catgirl_memory._heuristic_lessons` **只读 `decision_memory` 的 reason 文本**，
从不读牌组、不读分层损血、不读敌人类型 ⇒
50 局真实记忆里三条死亡教训**全是同一句兜底句**，抽不出任何 token。

## 本文件钉住的三件事

1. **非战斗房必须被滤掉** —— 否则 `room:MAP`（实测 15435 血）会成为"最大死因"
2. **死因判据与真实数据形态匹配** —— 含 Boss 误判修复、群怪"合计占比"判据
3. **证据不足时不编造** —— `UNKNOWN` 必须不产教训
"""

from __future__ import annotations

import pytest
from plugin.plugins.sts2_autoplay.death_attribution import (
    DEATH_KIND_AOE,
    DEATH_KIND_ATTRITION,
    DEATH_KIND_BOSS,
    DEATH_KIND_BURST,
    DEATH_KIND_ELITE,
    DEATH_KIND_UNKNOWN,
    attribution_to_lessons,
    build_death_attribution,
    classify_death,
    is_combat_room,
)


# --------------------------------------------------------------------------------------
# 1. 非战斗房过滤（★实测陷阱）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "room,expected",
    [
        # `service._room_identity` 无 id 时的占位兜底
        ("room:MAP", False),
        ("room:REST", False),
        ("room:SHOP", False),
        ("room:CHEST", False),
        # 已知非战斗事件
        ("NEOW", False),
        ("", False),
        # 真敌人
        ("FUZZY_WURM_CRAWLER", True),
        ("VANTOM", True),
        # 群怪（`+` 连接）
        ("CALCIFIED_CULTIST+SEAPUNK", True),
        ("LEAF_SLIME_M+LEAF_SLIME_S", True),
    ],
)
def test_non_combat_rooms_are_filtered(room: str, expected: bool) -> None:
    """★非战斗房必须被滤掉。

    实测（50 局真实记忆）跨局"累计损血最高"排名：

        room:MAP            15435 血   ← 若不滤，会成为"最大死因"
        NEOW                 6505
        room:REST            5958
        room:SHOP            2817
        FUZZY_WURM_CRAWLER   1843      ← 真正的头号敌人

    不滤会得出「最该防的是地图和商店」这种荒谬结论。
    """
    assert is_combat_room(room) is expected


def test_non_combat_pollution_would_change_the_verdict() -> None:
    """★把"过滤真的有用"做成可证伪断言，而不是只写在注释里。

    构造一局：非战斗房损血远大于战斗房。若不过滤，
    最重房会是 `room:MAP`，判据会得出错误的死因。
    """
    run = {
        "outcome": "defeat",
        "floor": 10,
        "route": [
            {"room": "room:MAP", "floor": 3, "hp_lost": 999},      # 非战斗，应被滤
            {"room": "room:REST", "floor": 5, "hp_lost": 500},     # 非战斗，应被滤
            {"room": "FUZZY_WURM_CRAWLER", "floor": 7, "hp_lost": 30},
            {"room": "NIBBIT", "floor": 8, "hp_lost": 20},
            {"room": "SEAPUNK", "floor": 9, "hp_lost": 25},
        ],
    }
    attribution = build_death_attribution(run)
    assert attribution["worst_room"] == "FUZZY_WURM_CRAWLER", (
        f"最重房必须是真敌人，实际 {attribution['worst_room']} —— 非战斗房没被滤掉"
    )
    assert attribution["combat_lost"] == 75, (
        f"战斗损血应只算三场真战斗(30+20+25=75)，实际 {attribution['combat_lost']}"
    )


# --------------------------------------------------------------------------------------
# 2. 死因判据
# --------------------------------------------------------------------------------------
def _deaths(*pairs):
    return [{"room": room, "lost": lost} for room, lost in pairs]


def test_boss_is_recognized_even_without_boss_in_name() -> None:
    """★Boss 必须按 **id** 识别，不能只靠名字里含 "boss"。

    实测教训：`VANTOM` 出现在真实记忆里（玩家点名「死在Vantom手下」），
    但房名**不含** "boss" 字样 ⇒ 只靠 hint 会把它误判成精英
    （改造中确实误判过一次：floor=17 死于 Vantom 的一局被归成 `elite`）。
    """
    kind = classify_death(
        deaths=_deaths(("VANTOM", 200), ("NIBBIT", 20)),
        total_floors=17,
        lost_total=220,
    )
    assert kind == DEATH_KIND_BOSS, f"VANTOM 是 Boss，实际判成 {kind}"


def test_boss_takes_priority_over_attrition() -> None:
    """Boss 是**具体身份**证据，必须优先于"跨多房"这类统计判据。"""
    kind = classify_death(
        deaths=_deaths(("VANTOM", 100), ("NIBBIT", 60), ("SEAPUNK", 50), ("TOADPOLE", 40)),
        total_floors=17,
        lost_total=250,
    )
    assert kind == DEATH_KIND_BOSS, (
        f"多房且有 Boss 时应判 Boss（具体身份优先），实际 {kind}"
    )


def test_elite_is_recognized_by_known_id() -> None:
    """精英敌人在游戏里的 `enemy_id` 常**不含** "elite" ⇒ 必须靠白名单。

    ⚠️ 这里刻意**只用**仍然留在 `_KNOWN_ELITES` 里的名字。
       `MAWLER` / `VINE_SHAMBLER` / `BYRDONIS` 之类原先也在名单里，已被实测证伪
       （在 3~15 层的普通遭遇里大面积出现）⇒ 已移出，见
       `test_common_trash_is_no_longer_classified_as_elite`。
    """
    for room in ("TERROR_EEL", "PHROG_PARASITE", "PHANTASMAL_GARDENER"):
        kind = classify_death(
            deaths=_deaths((room, 100), ("NIBBIT", 10)),
            total_floors=14,
            lost_total=110,
        )
        assert kind == DEATH_KIND_ELITE, f"{room} 应判精英，实际 {kind}"


def test_common_trash_is_no_longer_classified_as_elite() -> None:
    """★ 实测修正：5 个**常见小怪**曾被当成精英，实测共 59 场误判。

    证伪方式：看它们在真实数据里的**全部楼层集合**（`encounter_db.json` 775 场
    + 记忆库 50 局 route）—— 与普通遭遇完全重合，且在 3~15 层的普通战斗里反复出现：

        VINE_SHAMBLER    8,11,13,14,15      （全项目最常见敌人之一，累计 615 血）
        SELF_HELP_BOOK   3,6,7,15,28,29
        HAUNTED_SHIP     5,11,14,15
        MAWLER           6,8,11,12,13,15
        BYGONE_EFFIGY    11,12,13,14

    ⇒ 判成 `elite` 会指向 output/quality（"补数值、升级关键牌"），
      而真实形态是"被小怪磨死" ⇒ 该反思防御浓度。方向错了。
    """
    for room in ("VINE_SHAMBLER", "SELF_HELP_BOOK", "HAUNTED_SHIP",
                 "MAWLER", "BYGONE_EFFIGY"):
        kind = classify_death(
            deaths=_deaths((room, 100), ("NIBBIT", 10)),
            total_floors=13,
            lost_total=110,
        )
        assert kind != DEATH_KIND_ELITE, (
            f"{room} 是小怪（普通层 3~15 层大面积出现），不该判精英，实际 {kind}"
        )


def test_multi_enemy_uses_aggregate_share_not_single_room() -> None:
    """★群怪判据用**合计占比**，不是"最重那一间"。

    实测驱动：真实 50 局里群怪房**很常见但不占主导** —— 典型分布：

        VINE_SHAMBLER                615  (18.9%)   ← 单敌，最重
        EYE_WITH_TEETH+FOGMOG        230  ( 7.1%)   ★群怪
        LEAF_SLIME_M+LEAF_SLIME_S    189  ( 5.8%)   ★群怪
        FLYCONID+SNAPPING_JAXFRUIT   165  ( 5.1%)   ★群怪
        ─────────────────────────────────────────
        群怪合计                      584+ (≈18%)

    单看"最重那间"永远选不到群怪（它们各自都小），
    但**合计**过半说明这一局确实主要被多敌消耗。

    ★★ fixture 的关键设计（这里踩过一个坑）：
      **最重的那间必须是单敌**，且群怪**各自都更小**。
      我第一版写成"3 个群怪房各 30 + 1 个单敌 20" ——
      `max()` 在并列时取第一个，恰好取到群怪房，
      于是"只看最重那间"也返回 AOE ⇒ **变异逃逸**（变异测试实测发现）。

      ⇒ 必须让"最重那间"与"群怪"是**不同的房**，断言才有区分力。
    """
    kind = classify_death(
        deaths=_deaths(
            ("BIG_SINGLE_FOE", 40),   # ← 最重，但是**单敌**
            ("A+ARROW", 25),          # ← 群怪，各自更小
            ("B+BEAR", 25),
            ("C+CAT", 20),
        ),
        total_floors=12,
        lost_total=110,
    )
    assert kind == DEATH_KIND_AOE, (
        f"群怪合计 70/110 = 64%（且最重那间是单敌）应判 AOE，实际 {kind}。"
        "若判成别的，说明用的是『最重那一间』而不是『合计』。"
    )


def test_single_room_burst_when_dominant() -> None:
    """单房被快速打空（占比 ≥60%）⇒ 短轴爆发缺口。

    ⚠️ fixture 刻意**不用** `TERROR_EEL` 这类精英名 ——
       精英检查在 burst 之前（具体身份优先），用精英名会先被判成 elite。
       （我第一版正是这么写的，跑出 `elite != burst`，是**测试挑错了名字**，不是代码错。）
    """
    kind = classify_death(
        deaths=_deaths(("SOME_BIG_FOE", 80), ("SINGLE", 30), ("OTHER", 10)),
        total_floors=14,
        lost_total=120,
    )
    assert kind == DEATH_KIND_BURST, f"单房 80/120=67% 应判 burst，实际 {kind}"


def test_elite_priority_over_burst() -> None:
    """★精英/Boss 的**具体身份**优先于"单房占比"这个统计判据。

    这也是实测修正的一部分：真实数据里 `TERROR_EEL` 单房占 64%，
    但它**是精英** ⇒ 该反思"数值与强化"，而不是笼统的"短轴爆发"。
    """
    kind = classify_death(
        deaths=_deaths(("TERROR_EEL", 80), ("SINGLE", 30), ("OTHER", 10)),
        total_floors=14,
        lost_total=120,
    )
    assert kind == DEATH_KIND_ELITE, (
        f"TERROR_EEL 是精英且占 67%，应优先判 elite，实际 {kind}"
    )


def test_attrition_needs_no_floor_gate() -> None:
    """★长轴判据**不看层数**（原设 `floor>=8` 会让 10 局早期死亡全落 UNKNOWN）。

    实测：死在 5~7 层、跨 5~8 房、最重房只占 20~44% ——
    形态与"长轴被磨死"**完全一致**，只是磨得更快。
    死在低层不代表"不是被磨死的"，只代表**前期输出/防御浓度都不够**。
    """
    kind = classify_death(
        deaths=_deaths(("A", 40), ("B", 35), ("C", 30), ("D", 25)),
        total_floors=6,  # ← 低层也必须能判
        lost_total=130,
    )
    assert kind == DEATH_KIND_ATTRITION, (
        f"跨 4 房、无单一主因（最重仅 31%）应判 attrition，实际 {kind}"
    )


def test_two_rooms_split_is_unknown_not_attrition() -> None:
    """★两房 50/50 ⇒ **UNKNOWN**（不是 attrition，也不是 burst）。

    这是**刻意的保守**：
      * `worst_share = 0.5 < 0.6` ⇒ 不是"单房被打空"
      * `len(per_room) = 2 < 3`  ⇒ 不够"跨多房"的证据量

    ⇒ 两个判据都不满足 ⇒ **承认判不出来**，交给上层用兜底句。
      这比硬塞进某一类更诚实（与全局"证据不足就别编"一致）。

    ★ 与真实数据形态一致：实测 50 局里 rooms=2 的 3 局
      都是某一房主导（判 burst/elite），**没有** 50/50 的自然样本 ——
      本测试覆盖的是**构造边界**，不是真实分布。
    """
    kind = classify_death(
        deaths=_deaths(("A", 50), ("B", 50)),
        total_floors=8,
        lost_total=100,
    )
    assert kind == DEATH_KIND_UNKNOWN, (
        f"两房各半证据不足，应判 unknown（不编造），实际 {kind}"
    )


def test_no_deaths_is_unknown() -> None:
    """**证据不足 ⇒ 不编造。**"""
    assert classify_death(deaths=[], total_floors=5, lost_total=0) == DEATH_KIND_UNKNOWN


# --------------------------------------------------------------------------------------
# 2b. ★★ Boss 归因：结构性判据（幕末层）+ 名字白名单，二者互补
# --------------------------------------------------------------------------------------
#
# 实测背景（50 局真实记忆）：
#
#   · 30 局死在幕末层（本快照：floor 17 ×20 / 33 ×9 / 48 ×1）
#   · 只按房名判 Boss 的旧 `_looks_boss` 在这 30 局里只认出 1 局
#   · 旧判决分布：attrition 25 / elite 3 / burst 1 / boss 1
#     ⇒ 新判决：boss 30
#   · 后果不是数字难看，而是**反思方向被带反**：
#     `DEATH_KIND_TO_DIMS["boss"] = ("output","quality")`，
#     而 `attrition` 是 `("defense","quality")` ⇒ 一直劝"多垫格挡"，
#     真问题却是"升级关键牌、补数值"。

#: 从**真实数据**统计出的楼层集合（原始统计，未做任何筛选）：
#: 来源 = `encounter_db.json` 的 775 场遭遇（`encounter_key.act_id` × `floor`）
#:        + `catgirl_memory.json` 50 局的 `route[].room/floor`。
#: ★ 这张表是**证据**，不是期望值 —— 它让"为什么这个名字能进白名单"可复核。
_MEASURED_BOSS_FLOORS: dict[str, tuple[int, ...]] = {
    # act 0 的 Boss（幕末层 17）
    "VANTOM": (17,),
    "CEREMONIAL_BEAST": (17,),
    "WATERFALL_GIANT": (17,),
    "LAGAVULIN_MATRIARCH": (17,),
    "SOUL_FYSH": (17,),
    "KIN_PRIEST": (17,),
    "KIN_FOLLOWER": (17,),
    # act 1 的 Boss（幕末层 33）
    "THE_INSATIABLE": (33,),
    "KNOWLEDGE_DEMON": (33,),
    "CRUSHER": (33,),
    "ROCKET": (33,),
    # act 2 的 Boss（幕末层 48）
    "AEONGLASS": (48,),
}

#: 被证伪的"伪精英"的楼层集合（普通层大面积出现 ⇒ 不是 Boss、也不该当精英）。
_MEASURED_TRASH_FLOORS: dict[str, tuple[int, ...]] = {
    "VINE_SHAMBLER": (8, 11, 13, 14, 15),
    "SELF_HELP_BOOK": (3, 6, 7, 15, 28, 29),
    "HAUNTED_SHIP": (5, 11, 14, 15),
    "MAWLER": (6, 8, 11, 12, 13, 15),
    "BYGONE_EFFIGY": (11, 12, 13, 14),
}


def _act_final_floors() -> frozenset[int]:
    from plugin.plugins.sts2_autoplay.death_attribution import (
        _ACT_FINAL_FLOORS,
        ACT_FLOOR_SPANS,
    )

    # 幕末层必须**由**幕区间推导（两张表不许漂移）
    assert _ACT_FINAL_FLOORS == frozenset(end for _start, end in ACT_FLOOR_SPANS)
    return _ACT_FINAL_FLOORS


def test_act_final_floors_match_the_measured_floor_spans() -> None:
    """★ 结构性判据的数据底座：幕末层 = {17, 33, 48}（每一幕的最大层）。

    实测（`encounter_db.json` 775 条遭遇的 `act_id` × `floor`）：

        act 0 : floor 2..17    act 1 : floor 19..33    act 2 : floor 35..48

    记忆库 50 局的 `act` 字段与之交叉一致（floor=17 的局 `act` 全为 0）。
    """
    assert _act_final_floors() == frozenset({17, 33, 48})


@pytest.mark.parametrize("room", sorted(_MEASURED_BOSS_FLOORS))
def test_real_boss_names_are_classified_as_boss(room: str) -> None:
    """★ 真 Boss 房名 → `boss`（含**双敌人组合名**与"名字里有小怪味"的 Boss）。

    `KIN_FOLLOWER+KIN_PRIEST` 是**双敌人组合名**（`service._room_identity` 用 `+`
    连接同场敌人），不是单个 id —— 必须靠 `+` 拆分后逐段匹配。
    """
    floors = _MEASURED_BOSS_FLOORS[room]
    floor = max(floors)
    kind = classify_death(
        deaths=[{"room": room, "floor": floor, "lost": 120},
                {"room": "NIBBIT", "floor": 4, "lost": 30}],
        total_floors=floor,
        lost_total=150,
    )
    assert kind == DEATH_KIND_BOSS, f"{room} 是真 Boss（实测只在 floor {floors}），实际判成 {kind}"


@pytest.mark.parametrize(
    "combo",
    ["KIN_FOLLOWER+KIN_PRIEST", "KIN_FOLLOWER+KIN_FOLLOWER+KIN_PRIEST", "CRUSHER+ROCKET"],
)
def test_boss_combo_names_are_recognized(combo: str) -> None:
    """★ 双敌人组合名必须能识别（组合名不是单个 id，靠 `+` 拆段匹配）。"""
    floor = 33 if "CRUSHER" in combo else 17
    kind = classify_death(
        deaths=[{"room": combo, "floor": floor, "lost": 100}],
        total_floors=floor,
        lost_total=100,
    )
    assert kind == DEATH_KIND_BOSS, f"组合名 {combo} 应判 Boss，实际 {kind}"


def test_new_unknown_boss_is_still_recognized_by_floor() -> None:
    """★★ 结构性判据的**全部意义**：没见过的新 Boss 也必须判对。

    名字白名单对"还没见过的 Boss"必然失效；幕末层不会。
    这里刻意用一个**不在任何名单里**的房名，只给"死在幕末层"这一个证据。
    """
    kind = classify_death(
        deaths=[{"room": "SOME_BRAND_NEW_2027_BOSS", "floor": 33, "lost": 90},
                {"room": "SEAPUNK", "floor": 21, "lost": 60}],
        total_floors=33,
        lost_total=150,
    )
    assert kind == DEATH_KIND_BOSS, (
        f"死在幕末层(33) 就必是 Boss 战（不依赖名字），实际 {kind}"
    )


@pytest.mark.parametrize("room", sorted(_MEASURED_BOSS_FLOORS))
def test_boss_name_on_a_normal_floor_is_not_boss(room: str) -> None:
    """★★★ **防过拟合的关键测试**：同名但出现在普通楼层 ⇒ **不**判 boss。

    这是把白名单加进判据时最容易犯的错：只要名字对上就算 Boss，
    等于假设"这个敌人永远只在 Boss 房出现"。一旦它（或同名变体）在普通层出现，
    就会把"被小怪磨死"改判成"被 Boss 打死" —— 错误只是从一头搬到另一头，
    而且更糟（原来至少判对了大类）。

    ⇒ 楼层是闸门：楼层明确且不是幕末层时，名字说什么都不算数。
    """
    normal_floor = 11 if room != "AEONGLASS" else 40
    kind = classify_death(
        deaths=[{"room": room, "floor": normal_floor, "lost": 80},
                {"room": "SEAPUNK", "floor": normal_floor + 1, "lost": 40}],
        total_floors=normal_floor + 1,
        lost_total=120,
    )
    assert kind != DEATH_KIND_BOSS, (
        f"{room} 出现在普通层 {normal_floor} ⇒ 不是 Boss 战，实际判成 {kind}"
    )


def test_surviving_the_boss_then_dying_later_is_not_a_boss_death() -> None:
    """★★ 打过 Boss 之后才死 ⇒ **不是** Boss 死因（实测形态的真实案例）。

    实测 `B2F1CVUP9KB0`：route 里 floor 17（act 0 的 Boss 层）有损血，
    但它一路打到 floor 23 才死 ⇒ Boss 没能杀死它，真凶是后来的消耗。
    若不看"死在**哪一层**"而只看"损血过的层里有没有幕末层"，
    这一局会被误判成 boss。
    """
    kind = classify_death(
        deaths=[
            {"room": "WATERFALL_GIANT", "floor": 17, "lost": 30},   # 打掉了血，但活着出来了
            {"room": "SEAPUNK", "floor": 19, "lost": 40},
            {"room": "NIBBIT", "floor": 21, "lost": 35},
            {"room": "PAEL", "floor": 23, "lost": 30},
        ],
        total_floors=23,      # ← 死在 23，不是幕末层
        lost_total=135,
    )
    assert kind != DEATH_KIND_BOSS, (
        f"死在 floor 23（非幕末层）⇒ 不该判 Boss，实际 {kind}"
    )
    assert kind == DEATH_KIND_ATTRITION, f"跨 4 房无单一主因应判 attrition，实际 {kind}"


def test_whitelist_names_all_satisfy_the_floor_invariant() -> None:
    """★ 白名单的**入榜纪律**：只能收"实测只在幕末层出现"的名字。

    这条把"验证结果"变成可执行断言：日后若有人凭印象往 `_KNOWN_BOSSES`
    里塞一个在普通层出现过的名字，这里会立刻变红（数字对不上就说明没核对）。
    """
    from plugin.plugins.sts2_autoplay.death_attribution import _KNOWN_BOSSES

    act_final = _act_final_floors()
    for name, floors in _MEASURED_BOSS_FLOORS.items():
        assert name.lower() in _KNOWN_BOSSES, f"{name} 已验证却不在白名单里"
        assert set(floors) <= act_final, (
            f"{name} 的实测楼层 {floors} 不全是幕末层 {sorted(act_final)} ⇒ 不许进 Boss 白名单"
        )
    # 反向：白名单里凡是**有实测数据**的名字，都必须满足楼层不变式
    for name in _KNOWN_BOSSES:
        measured = _MEASURED_BOSS_FLOORS.get(name.upper())
        if measured is None:
            continue
        assert set(measured) <= act_final, f"{name} 在普通层出现过，不许当 Boss"


def test_proven_trash_is_not_a_boss_either() -> None:
    """★ 被证伪的"伪精英"同时也不满足 Boss 不变式（它们绝对不是 Boss）。"""
    act_final = _act_final_floors()
    for name, floors in _MEASURED_TRASH_FLOORS.items():
        assert not (set(floors) <= act_final), (
            f"{name} 的实测楼层 {floors} 与幕末层 {sorted(act_final)} 高度重合却" 
            "被判成只在 Boss 层出现 —— 统计口径有问题"
        )


def test_boss_without_any_floor_evidence_falls_back_to_name() -> None:
    """★ 向后兼容：**完全没有楼层证据**时退回按名字判 Boss（旧行为）。

    `classify_death` 的调用方不一定带 `floor`（`hp.heaviest` 回落路径就可能缺）；
    这种情况下没有楼层可用，只能信名字 —— 宁可保守取真。
    """
    kind = classify_death(
        deaths=[{"room": "VANTOM", "lost": 100}, {"room": "NIBBIT", "lost": 20}],
        total_floors=0,
        lost_total=120,
    )
    assert kind == DEATH_KIND_BOSS, f"无楼层证据时应按名字判 Boss，实际 {kind}"


def test_build_attribution_counts_boss_rooms_by_floor() -> None:
    """`boss_rooms` 计数也走同一套楼层闸门（不得出现"两套 Boss 判据"）。"""
    boss_run = {
        "outcome": "defeat",
        "floor": 17,
        "route": [
            {"room": "WATERFALL_GIANT", "floor": 17, "hp_lost": 40},
            {"room": "SEAPUNK", "floor": 21, "hp_lost": 30},
        ],
    }
    attribution = build_death_attribution(boss_run)
    assert attribution["kind"] == DEATH_KIND_BOSS
    assert attribution["boss_rooms"] >= 1

    normal_run = {
        "outcome": "defeat",
        "floor": 12,
        "route": [
            {"room": "WATERFALL_GIANT", "floor": 11, "hp_lost": 40},   # 普通层 ⇒ 不算 Boss 房
            {"room": "SEAPUNK", "floor": 12, "hp_lost": 30},
        ],
    }
    assert build_death_attribution(normal_run)["boss_rooms"] == 0


# --------------------------------------------------------------------------------------
# 3. 教训产出：只在输、且只在有证据时
# --------------------------------------------------------------------------------------
def test_unknown_produces_no_lesson() -> None:
    """★`UNKNOWN` 必须**不产教训** —— 证据不足就别编。

    这与全局口径一致（"宁可没影响，也不要用模糊匹配乱改决策"）：
    生产不出教训时，上层会继续用兜底句，而不是被一条瞎猜的教训带偏。
    """
    assert attribution_to_lessons({"kind": DEATH_KIND_UNKNOWN, "outcome": "defeat"}) == []


def test_victory_produces_no_lesson() -> None:
    """赢了不该产"别这样"的教训。"""
    assert attribution_to_lessons({"kind": DEATH_KIND_ELITE, "outcome": "victory"}) == []


def test_defeat_produces_dimension_specific_lesson() -> None:
    """★产出必须指向**维度**，不是"下次选防御节点"这类空话。

    玩家原话：「不能单纯的说"这局优先选防御/回血节点"，需要多维」。
    """
    for kind, keyword in (
        (DEATH_KIND_ELITE, "精英"),
        (DEATH_KIND_AOE, "群伤"),
        (DEATH_KIND_ATTRITION, "格挡浓度"),
        (DEATH_KIND_BURST, "格挡"),
    ):
        lessons = attribution_to_lessons({"kind": kind, "outcome": "defeat"})
        assert lessons, f"{kind} 应产出教训"
        assert keyword in lessons[0], (
            f"{kind} 的教训应指向维度关键词「{keyword}」，实际「{lessons[0]}」"
        )


def test_death_lessons_are_distinct_across_kinds() -> None:
    """不同死因必须产出**不同**教训 —— 否则"多维反思"退化成一句话。"""
    kinds = [DEATH_KIND_ELITE, DEATH_KIND_AOE, DEATH_KIND_ATTRITION, DEATH_KIND_BURST]
    texts = [
        attribution_to_lessons({"kind": k, "outcome": "defeat"})[0] for k in kinds
    ]
    assert len(set(texts)) == len(kinds), f"不同死因的教训重复了：{texts}"


def test_build_attribution_keys_are_stable() -> None:
    """`build_death_attribution` 的键必须恒定（缺数据给中性值，调用方无需判空）。"""
    empty = build_death_attribution({})
    for key in (
        "kind", "outcome", "floor", "combat_rooms", "combat_lost",
        "worst_room", "worst_lost", "worst_share",
        "multi_enemy_rooms", "multi_enemy_lost", "multi_enemy_share",
        "elite_rooms", "boss_rooms",
    ):
        assert key in empty, f"缺少键 {key}"
    assert empty["kind"] == DEATH_KIND_UNKNOWN
