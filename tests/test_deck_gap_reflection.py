"""★六维缺口反思契约（`deck_gap_reflection.py`）。

## 背景

玩家口径（两段必须合起来读）：

> 「这游戏需要确保有**短轴爆发**、**长轴成长**、**高质量 aoe 输出**、
>   **防御牌浓度**、**商店的选择**」
> 「教训是需要反思整个链路的，不能单纯的说"这局优先选防御/回血节点"，需要**多维**，
>   比如遇到群怪就死就反思**有没有拿 aoe 卡**，
>   遇到 boss 和精英就死需要反思**选卡的数值和强化卡选择**」

而改造前：`_deck_gap_vector`（`heuristic_planner.py:2992`）**早就实现了六维**，
但**唯一生产调用点是商店评分** —— 六维只影响"买什么"，**从不影响"反思什么"**。

## 本文件钉住四件事

1. **死因 × 缺口要交叉** —— 同一牌组、不同死因，产出不同重点
2. **不该说的别说** —— 缺口不显著时沉默（避免"每局倒六个维度"）
3. **没数据不编造** —— 空牌组 ⇒ 空教训
4. **教训必须能抽出 token** —— 否则说了等于没说（这是改造前的老毛病）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN_ROOT.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugin.plugins.sts2_autoplay.deck_gap_reflection import (  # noqa: E402
    DEATH_KIND_TO_DIMS,
    DIM_CEILINGS,
    DIM_LESSON_TEMPLATES,
    DIM_THRESHOLDS,
    DIM_TOKENS,
    NOTABLE_RATIO,
    build_gap_lessons,
)
from plugin.plugins.sts2_autoplay.heuristic_planner import (  # noqa: E402
    STS2HeuristicPlanner,
)


@pytest.fixture()
def planner() -> STS2HeuristicPlanner:
    return STS2HeuristicPlanner()


def _card(card_id: str, card_type: str, name: str, *, damage: int = 0,
          block: int = 0, cost: int = 1, upgraded: bool = False,
          rarity: str = "Common") -> dict:
    """构造一张 mod 形状的 DeckCardPayload（字段名与 C# `BuildDeckCardPayload` 一致）。"""
    card: dict = {
        "card_id": card_id, "card_type": card_type, "name": name,
        "energy_cost": cost, "upgraded": upgraded, "rarity": rarity,
        "resolved_rules_text": "", "dynamic_values": [],
    }
    if damage:
        card["dynamic_values"].append({"name": "Damage", "current_value": damage})
    if block:
        card["dynamic_values"].append({"name": "Block", "current_value": block})
    return card


def _starter_deck() -> list[dict]:
    """初始牌组：5 打击 + 4 防御（**没有群伤、没有抽牌、全未升级**）。"""
    return [
        *[_card("strike", "Attack", "打击", damage=6) for _ in range(5)],
        *[_card("defend", "Skill", "防御", block=5) for _ in range(4)],
    ]


# ======================================================================================
# 1. 死因 × 缺口交叉（本条是玩家口径的直接实现）
# ======================================================================================
@pytest.mark.unit
def test_death_kind_changes_which_dimensions_are_emphasized(planner: STS2HeuristicPlanner) -> None:
    """★★ **同一牌组、不同死因 ⇒ 重点不同**。

    这是玩家原话的直接实现：
      「遇到群怪就死就反思有没有拿 aoe 卡」
      「遇到 boss 和精英就死需要反思选卡的数值和强化卡选择」

    ⚠️ 反向教训：只断言"产出了教训"是不够的 —— 那样无论死因是什么、
      结论都一样，就退化成"每局说同一句话"。
      ⇒ 必须断言**不同死因的产出集合不同**。
    """
    deck = {"cards": _starter_deck()}
    by_kind = {
        kind: set(
            build_gap_lessons(
                deck, planner=planner, floor=8, current_hp=20, max_hp=80,
                death_kind=kind,
            )
        )
        for kind in ("aoe", "elite", "attrition")
    }
    assert by_kind["aoe"], "死在群怪必须有产出"
    assert by_kind["elite"], "死在精英必须有产出"
    assert by_kind["aoe"] != by_kind["elite"], (
        "死因不同但产出完全相同 ⇒ 死因没有参与判断"
    )
    # 群怪必须点到 aoe 维；精英必须点到 output 或 quality 维
    aoe_text = " ".join(by_kind["aoe"])
    assert DIM_TOKENS["aoe"][0] in aoe_text, f"死在群怪应提到群伤维度：{by_kind['aoe']}"
    elite_text = " ".join(by_kind["elite"])
    assert any(
        token in elite_text for token in (*DIM_TOKENS["output"], *DIM_TOKENS["quality"])
    ), f"死在精英应提到数值/强化维度：{by_kind['elite']}"


@pytest.mark.unit
def test_focused_dimension_comes_first(planner: STS2HeuristicPlanner) -> None:
    """★★ 死因点名的维度必须**排在最前面**。

    为什么这是独立的契约：产出是 `[:_MAX_LESSONS]` 截断的，
    而遍历顺序是 `DIM_LESSON_TEMPLATES` 的声明序 —— **与死因无关**。
    ⇒ 若不做"点名维度优先"排序，"死在群怪"这条教训可能因为
      `aoe` 在声明序里靠后而被**截掉**，于是死因又等于没参与。

    （这个缺陷在真实 fixture 上会被掩盖：那时 `focused` 恰好只有 1~2 条，
      截断砍不到它。所以必须用"点名维度必然多条"的死因来测。）
    """
    # 初始牌组：output 低（5 张 6 伤，floor 8 时 gap_output≈0.39，不过 0.45 阈值）
    # ⇒ 用空壳牌组让点名维度真的产出多条
    deck = {"cards": [_card("strike", "Curse", "打击", cost=3) for _ in range(6)]}
    # attrition 点名 ("defense", "quality")
    lessons = build_gap_lessons(
        deck, planner=planner, floor=20, current_hp=10, max_hp=80,
        death_kind="attrition",
    )
    assert lessons, "空壳牌组 + 被磨死 必须有产出"
    assert DIM_TOKENS["defense"][0] in lessons[0], (
        f"被磨死时第一条应是格挡维度（死因点名），实际：{lessons}"
    )


@pytest.mark.unit
def test_non_focus_dimension_does_not_dilute_when_gap_is_middling(planner: STS2HeuristicPlanner) -> None:
    """★★ **缺口平平的非点名维度不得乱插话**（稀释死因信号）。

    ⚠️ 这是我实现时踩的坑，必须钉住：
      第一版用「阈值 + 0.25」判非点名维度"值得一提"，但 `gap_aoe` **内建封顶 0.7**
      ⇒ 对 aoe 的"值得一提"线是 0.65，而一副真实形态的成长期牌组 `gap_aoe = 0.70`
      ⇒ **不论死因是什么**，每条教训都夹一句"群伤也缺"，
        把死因信号稀释掉 —— 正是玩家抱怨的"反思不出来"。

    ⇒ 改用「上限比例」（`NOTABLE_RATIO`）。

    ★★ fixture 的**关键设计**（这里踩过第二次坑）：
      要区分新旧两条判据，`quality` 必须落在两线之间：
        * 新判据线 = `上限 1.0 × 0.9` = **0.90**
        * 旧判据线 = `阈值 0.30 + 0.25` = **0.55**
      我第一版 fixture 的 quality 只有 0.42/0.4167 —— **两线都不过**
      ⇒ 新旧行为相同 ⇒ 变异测试实测 M8 **逃逸**。
      ⇒ 必须挑一个 `0.55 ≤ quality < 0.90` 的牌组（下面这组实测 **0.6667**）。
    """
    # 2 张未升级基础牌 + 3 张诅咒 ⇒ quality 实测 0.6667（落在 0.55 ~ 0.90 之间）
    deck = {
        "cards": [
            _card("strike", "Attack", "打击", damage=6),
            _card("defend", "Skill", "防御", block=5),
            _card("curse_a", "Curse", "诅咒A", rarity="Curse"),
            _card("curse_b", "Curse", "诅咒B", rarity="Curse"),
            _card("curse_c", "Curse", "诅咒C", rarity="Curse"),
            _card("bash", "Attack", "重击", damage=10),
            _card("uppercut", "Attack", "上勾拳", damage=13),
            _card("bludgeon", "Attack", "重锤", damage=32),
            _card("impervious", "Skill", "铜墙铁壁", block=30),
            _card("shrug", "Skill", "咬牙", block=11),
        ]
    }
    gaps = planner._deck_gap_vector(deck, current_hp=60, max_hp=80, floor=15)
    q = float(gaps.get("quality", 0))
    old_line = DIM_THRESHOLDS["quality"] + 0.25
    new_line = NOTABLE_RATIO * DIM_CEILINGS["quality"]
    assert old_line <= q < new_line, (
        f"fixture 的 quality 缺口必须落在「旧判据会触发、新判据不触发」的区间 "
        f"[{old_line}, {new_line})，实际 {q} —— 否则测不出新旧判据的区别（M8 会逃逸）"
    )
    # 死因=群怪（不点名 quality）⇒ quality 不该插话
    lessons = build_gap_lessons(
        deck, planner=planner, floor=15, current_hp=60, max_hp=80, death_kind="aoe",
    )
    assert not any("废牌" in t for t in lessons), (
        f"quality 缺口未顶格时不该在群怪死因下插话：{lessons}"
    )


@pytest.mark.unit
def test_non_focus_extra_is_capped_at_one(planner: STS2HeuristicPlanner) -> None:
    """★★ 非点名维度**最多带 1 条** —— 保证点名维度不被截断挤掉。

    ★★ fixture 的**关键设计**（这是第三次踩坑，两次失败都是 fixture 的错）：

      坑 1：只让**一个**非点名维度顶格 ⇒ `notable[:1]` 与 `notable` 结果相同
            ⇒ 删掉上限也测不出来（M9 第一次**逃逸**）。
      坑 2：让**五个**维度同时顶格 ⇒ `focused` 就占满 3 条，`[:3]` 截断把
            `notable` 整个吃掉 ⇒ 两者又相同（M9 第二次**逃逸**，实测确认：
            那副牌组 `aoe/defense/draw/energy/output/quality` 全触发）。

      ⇒ 正确的 fixture 要同时满足两条：
        ① 非点名且顶格的维度 **≥ 2 个**（否则上限不可观测）
        ② **总条数 ≤ 3**（否则 `[:3]` 截断掩盖差异）

      实测构造：24 张 3 费诅咒牌 ⇒ `draw=1.0`、`energy=1.0` 双顶格（②个），
      而死因取 `elite`（点名 output/quality）—— 那两维也顶格，
      于是数字上仍是 3 条…

      ⇒ 所以反过来：**让点名维度不触发**。用一副"有输出有格挡"的牌组，
        `output`/`defense` 都低于阈值 ⟹ `focused` 为空，
        只剩 `draw`/`energy` 两个非点名维度顶格 ⟹ 总共 2 条，
        `notable[:1]` 得 2 条、`notable` 得 3 条，**可观测**。
    """
    # 24 张牌（>14 触发 draw），平均 3 费（触发 energy），
    # 但每张都有伤害和格挡 ⇒ output/defense 缺口低；且全部已升级 ⇒ quality 低
    deck = {
        "cards": [
            _card("heavy", "Attack", "重击", damage=14, block=12, cost=3,
                  upgraded=True, rarity="Rare")
        ] * 24
    }
    gaps = planner._deck_gap_vector(deck, current_hp=70, max_hp=80, floor=20)
    notable_dims = [
        d for d in DIM_CEILINGS
        if float(gaps.get(d, 0)) >= DIM_CEILINGS[d] * NOTABLE_RATIO
    ]
    assert len(notable_dims) >= 2, (
        f"fixture 必须让 ≥2 个维度顶格（否则上限不可观测），实际 {notable_dims}；gaps={gaps}"
    )
    # 死因取 elite（点名 output/quality）—— 必须都**不**触发，否则 [:3] 会掩盖差异
    focus_dims = DEATH_KIND_TO_DIMS["elite"]
    for dim in focus_dims:
        assert float(gaps.get(dim, 0)) < DIM_THRESHOLDS[dim], (
            f"fixture 的点名维度 {dim} 不该触发（gap={gaps.get(dim)} "
            f">= 阈值 {DIM_THRESHOLDS[dim]}）—— 会让 focused 占满额度，[:3] 掩盖差异"
        )

    lessons = build_gap_lessons(
        deck, planner=planner, floor=20, current_hp=70, max_hp=80, death_kind="elite",
    )
    extra = [t for t in lessons if "（也缺，" in t]
    assert len(lessons) <= 3, f"总数超上限：{lessons}"
    assert len(extra) <= 1, (
        f"非点名维度带了 {len(extra)} 条（上限 1）—— 顶格维度有 {notable_dims}，"
        f"说明上限失效：{lessons}"
    )


@pytest.mark.unit
def test_every_death_kind_maps_to_at_least_one_dimension() -> None:
    """★死因表必须覆盖 `death_attribution` 产出的**全部** kind。

    漏一个 ⇒ 那种死因下 `focus` 为空 ⇒ 只有"极缺"的维度才说话
    ⇒ 等于死因**没有参与反思**（静默退化，不会报错，最危险）。
    """
    from plugin.plugins.sts2_autoplay.death_attribution import (
        DEATH_KIND_AOE,
        DEATH_KIND_ATTRITION,
        DEATH_KIND_BOSS,
        DEATH_KIND_BURST,
        DEATH_KIND_ELITE,
        DEATH_KIND_UNKNOWN,
    )

    for kind in (DEATH_KIND_AOE, DEATH_KIND_ATTRITION, DEATH_KIND_BOSS,
                 DEATH_KIND_BURST, DEATH_KIND_ELITE):
        assert kind in DEATH_KIND_TO_DIMS, f"死因 {kind} 没有对应的反思维度"
        assert DEATH_KIND_TO_DIMS[kind], f"死因 {kind} 的维度集合为空"
        for dim in DEATH_KIND_TO_DIMS[kind]:
            assert dim in DIM_LESSON_TEMPLATES, f"{kind} 指向未知维度 {dim}"
    # UNKNOWN 刻意**不**在表里 —— 判不出死因就不该硬指一个方向
    assert DEATH_KIND_UNKNOWN not in DEATH_KIND_TO_DIMS, (
        "UNKNOWN 不该映射到维度（判不出就别硬指）"
    )


# ======================================================================================
# 2. 该说的说、不该说的不说
# ======================================================================================
@pytest.mark.unit
def test_severe_gap_is_marked_severe(planner: STS2HeuristicPlanner) -> None:
    """★缺口严重度必须体现在文本里（"明显缺" vs "有点缺"）。"""
    deck = {"cards": _starter_deck()}
    lessons = build_gap_lessons(
        deck, planner=planner, floor=8, current_hp=20, max_hp=80, death_kind="aoe",
    )
    assert any("明显缺" in text or "有点缺" in text for text in lessons), (
        f"教训应带严重度标记：{lessons}"
    )


@pytest.mark.unit
def test_strong_deck_produces_no_lessons(planner: STS2HeuristicPlanner) -> None:
    """★★ **牌组没缺口 ⇒ 不该产教训。**

    病因预防：若不设阈值，每局都会把六个维度各说一遍 ⇒ 教训退化成噪声，
    玩家看到的永远是"你这也缺那也缺"，等于没复盘。
    """
    # 一副"什么都有"的牌组：高伤 + 高格挡 + 抽牌 + 低费 + 群伤 + 全升级
    deck = {
        "cards": [
            _card("bludgeon", "Attack", "重击", damage=32, cost=3, upgraded=True),
            _card("impervious", "Skill", "铜墙铁壁", block=30, cost=2, upgraded=True),
            _card("pommel", "Attack", "打击柄", damage=9, cost=1, upgraded=True),
            _card("offering", "Skill", "献祭", cost=0, upgraded=True),
            _card("whirlwind", "Attack", "旋风斩", damage=8, cost=1, upgraded=True),
            _card("shrug", "Skill", "咬牙", block=11, cost=1, upgraded=True),
            _card("battle_trance", "Skill", "战斗恍惚", cost=0, upgraded=True),
            _card("cleave", "Attack", "横扫", damage=11, cost=1, upgraded=True),
            _card("true_grit", "Skill", "坚韧", block=9, cost=1, upgraded=True),
            _card("reaper", "Attack", "收割", damage=5, cost=2, upgraded=True),
        ]
    }
    # 给两张牌补上抽牌/群伤关键词，让对应维度真的被填满
    deck["cards"][2]["resolved_rules_text"] = "造成 9 点伤害。抽 1 张牌。"
    deck["cards"][4]["resolved_rules_text"] = "对所有敌人造成 8 点伤害。"
    deck["cards"][7]["resolved_rules_text"] = "对所有敌人造成 11 点伤害。"

    for kind in ("", "aoe", "elite", "attrition"):
        lessons = build_gap_lessons(
            deck, planner=planner, floor=20, current_hp=70, max_hp=80, death_kind=kind,
        )
        assert not lessons, (
            f"牌组没有明显缺口时不该产教训（death_kind={kind!r}），实际：{lessons}"
        )


@pytest.mark.unit
def test_at_most_three_lessons(planner: STS2HeuristicPlanner) -> None:
    """★一局最多倒 3 条 —— 倒满六个维度等于什么都没说。

    ★★ fixture 的关键设计（这里踩过一个坑）：
      **必须构造"六个维度全部严重缺口"的牌组**，否则测不出 `[:3]` 这个上限。
      我第一版只用了初始牌组（5 打击 4 防御），它只触发 2~3 条
      ⇒ 变异测试实测：把 `return lessons[:3]` 改成 `return lessons`
        **仍然全绿**（M5 逃逸）。

      ⇒ 用一个"空壳牌组"：既没伤害也没格挡、又胖、费用又高、
        还有诅咒和基础牌 ⇒ 六个维度同时爆表。
    """
    #: 空壳牌组：6 张 3 费无效果诅咒牌
    #:  * output: 0 伤害 → gap 1.0
    #:  * defense: 0 格挡 → gap 1.0
    #:  * draw: 6 张无抽牌 → 有缺口
    #:  * energy: 平均 3 费 → gap 1.0
    #:  * aoe: 0 群伤 → gap 0.7（封顶）
    #:  * quality: 全是未升级基础牌 + 诅咒 → gap 高
    deck = {"cards": [_card("strike", "Curse", "打击", cost=3) for _ in range(6)]}
    gaps = planner._deck_gap_vector(deck, current_hp=10, max_hp=80, floor=20)
    over = [d for d, t in DIM_THRESHOLDS.items() if float(gaps.get(d, 0)) >= t]
    assert len(over) >= 4, (
        f"fixture 没有让足够多的维度越阈值（只有 {over}），测不出上限。gaps={gaps}"
    )

    for kind in ("", "aoe", "elite", "attrition", "boss"):
        lessons = build_gap_lessons(
            deck, planner=planner, floor=20, current_hp=10, max_hp=80, death_kind=kind,
        )
        assert len(lessons) <= 3, (
            f"教训超过 3 条（{len(lessons)}，death_kind={kind!r}）：{lessons}"
        )
        assert lessons, f"这种空壳牌组必须有产出（death_kind={kind!r}）"


# ======================================================================================
# 3. 没数据不编造
# ======================================================================================
@pytest.mark.unit
@pytest.mark.parametrize("deck", [[], {"cards": []}, None, {}, "垃圾"])
def test_no_deck_means_no_lessons(planner: STS2HeuristicPlanner, deck: object) -> None:
    """★★ **读不到牌组 ⇒ 空教训**（不编造）。

    与 `_deck_gap_vector` 的既有约定一致：读不到就按"没缺口"处理，
    宁可不说，也不要凭猜反思。终局屏 payload 若不带 deck，会走这条路径。
    """
    assert build_gap_lessons(
        deck, planner=planner, floor=8, death_kind="aoe",
    ) == []


@pytest.mark.unit
def test_planner_failure_does_not_raise(planner: STS2HeuristicPlanner) -> None:
    """★量化失败**不得**抛错 —— 复盘链路绝不能被单维量化拖垮。

    终局屏会反复出现，这里抛错会让整局记忆丢失。
    """

    class Boom:
        def _deck_gap_vector(self, *_a, **_k):
            raise RuntimeError("量化炸了")

    assert build_gap_lessons(
        {"cards": _starter_deck()}, planner=Boom(), floor=8, death_kind="aoe",
    ) == []


# ======================================================================================
# 4. ★教训必须能抽出 token（否则说了等于没说）
# ======================================================================================
@pytest.mark.unit
def test_aoe_lesson_yields_aoe_token(planner: STS2HeuristicPlanner) -> None:
    """★★ 群伤教训必须能抽出 `aoe` token。

    这是**改造中最真实的坑**：`_LESSON_KEYWORD_MAP` 原本没有 `aoe` 项，
    于是「这局几乎没有群伤牌，下局想早点拿 AOE」这条教训
    **抽不出任何对应 token** ⇒ 决策层收不到信号 ⇒ 反思白做。

    实测（补 token 之前）：死因 aoe 的六维教训只抽出 `['attack','skill']`。
    """
    lessons = build_gap_lessons(
        {"cards": _starter_deck()},
        planner=planner, floor=8, current_hp=20, max_hp=80, death_kind="aoe",
    )
    assert any(DIM_TOKENS["aoe"][0] in text for text in lessons), (
        f"群伤教训应含「{DIM_TOKENS['aoe'][0]}」：{lessons}"
    )
    tokens = planner._lesson_tokens(lessons)
    assert "aoe" in tokens, f"群伤教训必须抽出 aoe token，实际 {tokens}"


#: ★ 维度 -> 该维度模板**理应抽出**的规范 token（`_lesson_tokens` 的返回值）。
#:
#: ⚠️ 为什么不能直接用 `DIM_TOKENS`：那一张表里放的是**中文关键词**
#: （"输出"/"格挡"/"群伤"），而 `_lesson_tokens` 返回的是**规范 token id**
#: （`attack`/`skill`/`aoe`）。两者之间隔着 `_LESSON_KEYWORD_MAP`
#: （`heuristic_planner.py:354`）—— 我第一版把中文词直接拿去比 token，测试写错了。
#:
#: 这张表**故意不写全**（如 quality 允许 `curse` 或 `quality` 任一）：
#: 只要指向该维度的**任一** token 抽到，决策层就收到了信号。
DIM_EXPECTED_TOKENS: dict[str, tuple[str, ...]] = {
    "output": ("attack",),
    "defense": ("skill",),
    "draw": ("quality",),      # 「精简牌组」→ quality（draw 没有独立 token）
    "energy": (),              # ⚠️ 见 test_energy_dimension_has_no_decision_token
    "aoe": ("aoe",),
    "quality": ("quality", "curse"),
}


@pytest.mark.unit
def test_every_dimension_template_yields_its_own_token(planner: STS2HeuristicPlanner) -> None:
    """★★ 每个维度模板都必须抽出**指向自己**的规范 token。

    为什么：模板是"给下一局的信号"。若某维模板抽不出 token，
    那条教训就只是给玩家看的话，**对决策零影响**（改造前三条死亡教训的老毛病）。
    """
    missing: list[str] = []
    for dim, template in DIM_LESSON_TEMPLATES.items():
        expected = DIM_EXPECTED_TOKENS[dim]
        if not expected:
            continue
        tokens = planner._lesson_tokens([template])
        if not any(token in tokens for token in expected):
            missing.append(
                f"{dim}: 模板抽不出 {expected} 中任一 token，实际 {tokens}"
            )
    assert not missing, "以下维度模板无法影响决策：\n  " + "\n  ".join(missing)


@pytest.mark.unit
def test_energy_dimension_has_no_decision_token(planner: STS2HeuristicPlanner) -> None:
    """★诚实记录：**费用/能量维度目前抽不出任何 token**。

    实测：`DIM_LESSON_TEMPLATES["energy"]` = 「这局费用曲线太顶，下局我想压低平均费用
    或者拿加能量的东西」—— `_lesson_tokens` 返回 `[]`。

    原因：`_LESSON_KEYWORD_MAP` 里**没有**"费用"/"能量"这两项
    （`heuristic_planner.py:354` 那张表只有 elite/monster/rest/shop/treasure/
    curse/max_hp/skill/attack/power/aoe/quality）。

    ⚠️ 本测试**不是在庆祝这个现状**，而是把它钉成可执行的待办标记：
      一旦给 energy 加了 token，这条会失败，提醒实现者同步更新契约
      （而不是让缺口悄悄变成"特性"）。

    影响面评估：energy 维度**仍会产出教训给玩家看**（有反思价值），
    只是不改变下一局的决策。这与"说了等于没说"不同 —— 它是"说了但只有一半用"。
    """
    tokens = planner._lesson_tokens([DIM_LESSON_TEMPLATES["energy"]])
    assert tokens == [], (
        f"energy 维度现在抽出 token 了（{tokens}）—— 请更新本测试与 "
        "`DIM_EXPECTED_TOKENS`，把它纳入决策链路"
    )


@pytest.mark.unit
def test_quality_template_does_not_collide_with_rest_lessons(planner: STS2HeuristicPlanner) -> None:
    """★「质量」token 不得误伤**路线**类教训（我补 token 时踩过的坑）。

    实测反例：`quality` 关键词一度收了裸「升级」，于是
    `"上次在篝火休息浪费了升级机会，以后别选休息"` —— 一句**讲休息点**的教训 ——
    额外抽出了 `quality`，破坏了"路线教训只影响路线"的契约
    （`test_run_lessons_affect_decision.py::test_route_lesson_changes_route_order` 立刻转红）。

    ⇒ 本测试把那条边界钉死在这里。
    """
    assert planner._lesson_tokens(["上次在篝火休息浪费了升级机会，以后别选休息"]) == ["rest"], (
        "路线类教训只该抽出 rest"
    )
    # 但真正的"质量"教训必须仍能抽出 quality
    assert "quality" in planner._lesson_tokens([DIM_LESSON_TEMPLATES["quality"]]), (
        "质量教训本身必须能抽出 quality token"
    )


# ======================================================================================
# 5. 阈值表自洽
# ======================================================================================
@pytest.mark.unit
def test_thresholds_are_within_possible_range() -> None:
    """★阈值必须落在该维度**实际能达到**的范围内。

    ⚠️ 为什么需要这条：`gap_aoe` 内建封顶 **0.7**
      （`heuristic_planner.py` 的 `0.7 * clamp01(...)`，规格说这情境是"缺口中"）。
      若把 `aoe` 阈值写成 0.8，那一维就**永远说不出话** ——
      这是静默失效，不会报错。
    """
    problems: list[str] = []
    for dim, threshold in DIM_THRESHOLDS.items():
        ceiling = DIM_CEILINGS[dim]
        if threshold >= ceiling:
            problems.append(
                f"{dim}: 阈值 {threshold} >= 实际上限 {ceiling} ⇒ 永远触发不了"
            )
        if threshold <= 0.15:
            problems.append(f"{dim}: 阈值 {threshold} 太低 ⇒ 几乎每局都触发（噪声）")
        # ★ 非点名维度的"值得一提"线也必须可达，否则那条分支形同虚设
        if ceiling * NOTABLE_RATIO > ceiling:
            problems.append(f"{dim}: NOTABLE_RATIO 判据不可达")
    assert not problems, "阈值表不自洽：\n  " + "\n  ".join(problems)
    assert set(DIM_THRESHOLDS) == set(DIM_LESSON_TEMPLATES), "阈值表与模板表维度不一致"
    assert set(DIM_TOKENS) == set(DIM_LESSON_TEMPLATES), "token 表与模板表维度不一致"
    assert set(DIM_CEILINGS) == set(DIM_LESSON_TEMPLATES), "上限表与模板表维度不一致"


@pytest.mark.unit
def test_dim_ceilings_match_deck_gap_vector_reality(planner: STS2HeuristicPlanner) -> None:
    """★★ `DIM_CEILINGS` 必须是**实测**出来的，不是抄来的。

    为什么：这张表直接决定"非点名维度什么时候插话"。
      若把 aoe 上限错写成 1.0，那 `0.70 >= 1.0*0.9=0.90` 为假
      ⇒ 群伤在**每一局**都不插话 ⇒ 需求「遇到群怪就死就反思有没有拿 aoe 卡」
      在 aoe 不是点名维度时**静默失效**。

    ⇒ 用一个"零群伤牌组"实测 `gap_aoe` 的真实上限。
    """
    # 完全没有群伤/状态牌的牌组 ⇒ gap_aoe 必然顶到它自己的最大值
    deck = {"cards": [_card("strike", "Attack", "打击", damage=6) for _ in range(8)]}
    gaps = planner._deck_gap_vector(deck, current_hp=70, max_hp=80, floor=5)
    assert float(gaps["aoe"]) == pytest.approx(DIM_CEILINGS["aoe"], abs=0.001), (
        f"实测 gap_aoe 上限 {gaps['aoe']} 与表里写的 {DIM_CEILINGS['aoe']} 不符"
    )
