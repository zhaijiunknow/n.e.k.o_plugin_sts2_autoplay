"""回归测试：事件屏"反复消耗生命上限"与变化卡屏"主动选诅咒"两个实机缺陷。

## 缺陷 A —— 反复消耗生命上限，直到 HP 变 1

**实机证据**（527 份原始 `/state` 采样，`.dsh-artifacts/live-run-v7/state-samples/`）：

```
run PBMS95EEL6P7 : max_hp = 80  (228 样本，全程恒定)
run H2BMEF71EPNG : max_hp = 80  (211 样本，全程恒定)
run PVV8N7MJ4VGY : max_hp = 54  ( 84 样本，全程恒定)   ← 被扣 26 点且不再恢复
```

三条根因：
1. `_score_event_option` 的关键词表里**没有**"生命上限"/"max hp" ⇒
   `"失去 5 点生命上限，获得 100 金币"` 得 **+10**（金币 +10 盖过一切）⇒ 每轮都选它。
2. `_option_loses_hp` 对 `"失去 5 点生命上限"` / `"lose 5 max hp"` 返回 `False` ⇒ 无探测能力。
3. 附带发现（非本次引入，`HEAD` 版本即存在）：中文 `"失去生命"` 字面量匹配不到
   `"失去 10 点生命"`（中间有数字与量词）。

## 缺陷 B —— 变化卡屏主动选诅咒

`card_selection_transform` 与 `card_selection_remove` 在 `heuristic_planner.py:839`
被放进**同一分支**，于是变换屏复用了删牌启发式 `_preferred_remove_option`，
而它给诅咒打 **−100**（"最该移除"）⇒ 变换屏每轮都主动选诅咒。

但两屏的最优语义**相反**：
* 删牌屏：选诅咒 = **移除**它 ⇒ 最优
* 变换屏：选诅咒 = **变成另一张诅咒** ⇒ 最劣

游戏侧源码证实诅咒变换是零期望
（`CardFactory.GetFilteredTransformationOptions`：
`if ((uint)(rarity - 8) > 1u)` 对 `Curse`(=9) 为**假**（9−8=1，不 `>1`）⇒
跳过"只保留 Common/Uncommon/Rare"的约束 ⇒ 候选池含其它诅咒）。

mod 侧**已经**给出了判别依据：`selection.kind == "deck_transform_select"`
（`GameStateService.cs:4250`），插件此前从未读取它。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_repo_planner():
    """加载**本测试文件所在副本**的 `heuristic_planner.py`，且**零副作用**。

    ★为什么需要这个（两个已实测的坑）：

    坑 1（假闸门）：测试口径设了 `PYTHONPATH=D:\\NekoClaw\\N.E.K.O`，
    而 `from plugin.plugins.sts2_autoplay.heuristic_planner import ...` 会解析到 **builtin 副本**，
    不是本文件旁边的副本。实测：把本副本改坏，用包路径导入的测试**照样全绿** ⇒ 假闸门。

    坑 2（拖垮全轮，我第一版踩到）：不能替换 `sys.modules` 里的父包 ——
    ① 子模块已在 `sys.modules` 时 `import_module` **直接返回缓存**，替换父包无效；
    ② 留下的空命名空间包会让**其它**测试（`test_push_delivery.py` 等）导入
       `STS2AutoplayPlugin` 失败，**整轮 collection 中止，一个数字都拿不到**。

    本实现改用 `spec_from_file_location` + **显式设定 `__package__`**，
    让包内相对导入（`from .planner_interface import ...`）成立，同时不碰 `sys.modules` 里的父包。

    ★另外**不在 collection 阶段硬断言** —— 一条测试不该让 700+ 条拿不到结果。
    副本不符时只在各测试用例内失败（见 `_assert_repo_copy`）。
    """
    import importlib
    import importlib.util

    planner_path = (REPO_ROOT / "heuristic_planner.py").resolve()
    pkg_name = "plugin.plugins.sts2_autoplay"
    mod_name = f"{pkg_name}.heuristic_planner"

    # ★先让**真实包**正常导入（父链、sibling 模块如 parser/summary 都来自 PYTHONPATH 上的副本）。
    # 这一步是必要的：`heuristic_planner.py` 用包内相对导入（`from .planner_interface import ...`），
    # 没有真实父包就 `ModuleNotFoundError`。**绝不**自己伪造父链 ——
    # 我第一版伪造了 `plugin` 空包，导致 `plugin.sdk` 解析失败、本仓库 `__init__.py` 导入崩溃、
    # 700+ 测试连锁报错（"整轮中止，一个数字都拿不到"）。
    importlib.import_module(pkg_name)

    # ★再把**本副本的文件**按路径编译进那个包的名字空间，覆盖掉 builtin 版本。
    # 用已导入的真实包对象承载，于是相对导入照常工作，而模块来源**确定是本副本**。
    spec = importlib.util.spec_from_file_location(mod_name, planner_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {planner_path} 建立 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # 失败时把原来的模块放回去，不留半成品在 sys.modules 里。
        sys.modules.pop(mod_name, None)
        raise
    return module


_PLANNER_MODULE = _load_repo_planner()
STS2HeuristicPlanner = _PLANNER_MODULE.STS2HeuristicPlanner


def _assert_repo_copy() -> None:
    """确认被测代码就是本文件旁边的副本（否则所有断言都无意义）。"""
    actual = Path(_PLANNER_MODULE.__file__).resolve()
    expected = (REPO_ROOT / "heuristic_planner.py").resolve()
    if actual != expected:
        pytest.fail(
            f"被测 planner 不是本副本！\n  实际: {actual}\n  期望: {expected}\n"
            "（PYTHONPATH 里别的副本抢占了导入路径，断言结果不可信）"
        )


@pytest.fixture()
def planner() -> STS2HeuristicPlanner:
    _assert_repo_copy()
    return STS2HeuristicPlanner()


# --------------------------------------------------------------------------
# 缺陷 A1：生命上限损失的语义识别
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "失去 5 点生命上限，获得 100 金币",
        "失去 10 点生命上限",
        "最大生命 -5",
        "生命最大值 -3",
        "Lose 5 Max HP, gain a relic",
        "Lose Max HP",
        "lose 7 maximum health",
        "Lose 5 max. hp",
    ],
)
def test_max_hp_loss_is_detected(planner: STS2HeuristicPlanner, text: str) -> None:
    """生命上限损失必须被识别（中英双语）。"""
    assert planner._option_loses_max_hp(text.lower()) is True


@pytest.mark.parametrize(
    "text",
    [
        "失去 10 点生命",          # 一次性掉血，可恢复 —— 不是上限损失
        "lose 10 hp",
        "获得 100 金币",
        "获得 1 个遗物",
        "",
    ],
)
def test_plain_hp_loss_is_not_max_hp_loss(planner: STS2HeuristicPlanner, text: str) -> None:
    """普通掉血 / 无损失文本**不得**被误判为上限损失。

    这是关键的反向断言 —— 若把普通掉血也算成永久损失，所有"掉血换收益"的
    正常事件都会被一并否决，属于过度修正。
    """
    assert planner._option_loses_max_hp(text.lower()) is False


def test_option_loses_hp_excludes_max_hp(planner: STS2HeuristicPlanner) -> None:
    """`_option_loses_hp` 必须把上限损失让给专门的判定，保持两种语义可区分。"""
    assert planner._option_loses_hp("失去 5 点生命上限") is False
    assert planner._option_loses_hp("lose 5 max hp") is False


@pytest.mark.parametrize(
    "text",
    ["失去 10 点生命", "失去 3 点生命", "失去生命", "掉血"],
)
def test_chinese_hp_loss_with_numeral_is_detected(planner: STS2HeuristicPlanner, text: str) -> None:
    """中文「失去 N 点生命」必须被识别。

    回归背景：原实现用字面量 ``"失去生命"`` 匹配，``"失去 10 点生命"`` 中间夹着
    数字与量词 ⇒ **匹配不到**。已实测该缺陷在 ``HEAD`` 版本即存在。
    """
    assert planner._option_loses_hp(text.lower()) is True


# --------------------------------------------------------------------------
# 缺陷 A2：评分必须让"扣上限"选项输给"离开"
# --------------------------------------------------------------------------


def test_max_hp_loss_outweighs_gold_reward(planner: STS2HeuristicPlanner) -> None:
    """`失去上限 + 100 金币` 的总分必须**低于**"离开"（0 分）。

    修复前该选项得 **+10**（金币 +10 完全盖过一切）⇒ 脚本每轮都选它。
    这是"反复消耗生命上限直至 HP 变 1"的直接来源。
    """
    loss = planner._score_event_option({"text": "失去 5 点生命上限，获得 100 金币"}, 0.8, 100)
    leave = planner._score_event_option({"text": "离开"}, 0.8, 100)
    assert loss < leave, f"扣上限({loss}) 仍不劣于离开({leave}) —— 修复未生效"


def test_max_hp_loss_outweighs_relic_reward(planner: STS2HeuristicPlanner) -> None:
    """扣上限 + 遗物也必须为负，不能被 +14 遗物加成抵消。"""
    score = planner._score_event_option({"text": "Lose 5 Max HP, gain a relic"}, 0.8, 100)
    assert score < 0, f"扣上限+遗物得分 {score} ≥ 0 —— 惩罚被遗物加成抵消"


def test_max_hp_loss_penalty_beats_any_single_reward(planner: STS2HeuristicPlanner) -> None:
    """上限损失惩罚量级必须**高于任何单项正向收益**，否则"有得有失"的选项会翻盘。

    当前最大单项正向收益 = 遗物(+14)；惩罚取 −60，留足余量。
    """
    relic_only = planner._score_event_option({"text": "获得 1 个遗物"}, 0.8, 100)
    with_loss = planner._score_event_option(
        {"text": "失去 5 点生命上限，获得 1 个遗物，获得 100 金币"}, 0.8, 100
    )
    assert with_loss < 0, "多项收益叠加后仍应为负"
    assert with_loss < relic_only


# --------------------------------------------------------------------------
# 缺陷 B（事件侧）：诅咒惩罚不得被遗物/卡牌加成抵消
# --------------------------------------------------------------------------


def test_curse_penalty_survives_relic_reward(planner: STS2HeuristicPlanner) -> None:
    """★核心回归：`获得诅咒 + 获得遗物` 的总分必须**为负**。

    修复前该选项得 **−4**（诅咒 −18，遗物 +14，几乎完全抵消）⇒
    在"有得有失"的变化卡/遗物类事件里，脚本会为了遗物接受诅咒。

    玩家口径：「选择诅咒只会变成另一种诅咒，对于提升卡牌质量来说期望太低了」。
    游戏侧源码已证实诅咒变换的零期望性质
    （`CardFactory.GetFilteredTransformationOptions` 对 `Curse` 跳过稀有度过滤）。
    """
    score = planner._score_event_option({"text": "获得 1 张诅咒牌，获得 1 个遗物"}, 0.8, 100)
    assert score < 0, f"诅咒+遗物得分 {score} ≥ 0 —— 惩罚被遗物加成抵消"


def test_curse_penalty_exceeds_relic_plus_card(planner: STS2HeuristicPlanner) -> None:
    """诅咒惩罚必须**高于遗物(+14)与卡牌(+8)之和**，否则多重收益叠加仍能翻盘。"""
    with_everything = planner._score_event_option(
        {"text": "获得 1 张诅咒牌，获得 1 个遗物，获得 1 张卡牌，获得 100 金币"}, 0.8, 100
    )
    assert with_everything < 0, (
        f"诅咒+遗物+卡牌+金币 总分 {with_everything} ≥ 0 —— 惩罚量级不足，"
        "可被叠加收益抵消"
    )


def test_pure_curse_is_worse_than_curse_with_relic(planner: STS2HeuristicPlanner) -> None:
    """反向断言：纯诅咒应**比**"诅咒+遗物"更差 —— 说明加分逻辑仍然生效。

    这条防止"为了修缺陷而把诅咒惩罚加到淹没一切"的过度修正：
    遗物仍应带来正向贡献，只是不足以翻转净符号。
    """
    pure = planner._score_event_option({"text": "获得 1 张诅咒牌"}, 0.8, 100)
    with_relic = planner._score_event_option({"text": "获得 1 张诅咒牌，获得 1 个遗物"}, 0.8, 100)
    assert pure < with_relic, "纯诅咒没有比诅咒+遗物更差 —— 遗物加成丢失了"


# --------------------------------------------------------------------------
# 缺陷 A3：事件屏无状态 ⇒ 可重复选择的选项被反复选中
# --------------------------------------------------------------------------

_REPEAT_OPTS = [
    {"index": 0, "text": "失去 5 点生命上限，获得 100 金币"},
    {"index": 1, "text": "离开"},
]


def _repeat_summary():
    return {
        "payload": {
            "event_options": _REPEAT_OPTS,
            "current_hp": 50,
            "max_hp": 80,
            "gold": 100,
        }
    }


def test_repeat_choice_does_not_pull_back_to_harmful_option(planner: STS2HeuristicPlanner) -> None:
    """★核心回归：反复选**无害**选项不得让最优解翻回有害选项。

    这是我在实现时实际踩到的陷阱：若对所有选项一视同仁地累加"重复惩罚"，
    会把"离开"（分数 0）也一起压下去 —— 实测第 4 轮 `[1,0]` 翻转成 `[0,1]`，
    **修复比不修更糟**。正确做法是只惩罚"原始评分为负"的选项。
    """
    for _ in range(6):
        ranked = planner._rank_event_options(_repeat_summary())
        assert ranked[0] == 1, (
            f"反复选『离开』后最优解翻回了扣上限的选项（ranked={ranked}）—— 惩罚机制写反了"
        )
        planner.note_event_option_chosen(_repeat_summary(), ranked[0])


def test_repeated_harmful_choice_gets_penalized(planner: STS2HeuristicPlanner) -> None:
    """反复选**有害**选项时，惩罚必须真的把它推下去。

    注意：不能只断言"最终选离开" —— 因为"离开"本来就是最优，
    即使**完全没有**惩罚机制也仍然选它（实测过这个假闸门）。
    所以这里直接断言**惩罚值本身**随重复次数增长。
    """
    first = planner._event_repeat_penalty(_repeat_summary(), 0)
    assert first == 0.0, "第一次选择不应有惩罚"

    planner.note_event_option_chosen(_repeat_summary(), 0)
    once = planner._event_repeat_penalty(_repeat_summary(), 0)
    planner.note_event_option_chosen(_repeat_summary(), 0)
    twice = planner._event_repeat_penalty(_repeat_summary(), 0)

    assert once > 0, "重复选择『扣上限』后惩罚仍为 0 —— 记忆没有生效"
    assert twice > once, f"惩罚未随重复次数增长（一次={once}, 两次={twice}）"


def test_repeat_memory_is_zero_on_first_choice(planner: STS2HeuristicPlanner) -> None:
    """第一次选择**不得**有重复惩罚 —— 否则会改变一次性事件的正常决策。"""
    assert planner._event_repeat_penalty(_repeat_summary(), 0) == 0.0


def test_repeat_memory_resets_on_screen_change(planner: STS2HeuristicPlanner) -> None:
    """换屏必须重置记忆（以"屏签名"为键，与 `_selection_pressed` 同范式）。"""
    planner.note_event_option_chosen(_repeat_summary(), 0)
    planner.note_event_option_chosen(_repeat_summary(), 0)
    assert planner._event_repeat_penalty(_repeat_summary(), 0) > 0, "同屏记账未生效"
    other = {"payload": {"event_options": [{"index": 0, "text": "获得遗物"},
                                           {"index": 1, "text": "离开"}],
                         "current_hp": 50, "max_hp": 80, "gold": 100}}
    assert planner._event_repeat_penalty(other, 0) == 0.0, "换屏后记忆没有重置"


def test_repeat_penalty_does_not_also_suppress_harmless_options(planner: STS2HeuristicPlanner) -> None:
    """★核心回归（archivist 实测发现）：重复惩罚**只能**作用于已评为负的选项。

    若对**所有**选项一视同仁地累加，会把无害的"离开"也压下去 ——
    实测后果：反复选"离开"4 次后它被压到"扣上限"之下，**最优解反转回扣上限**，
    **修复比不修更糟**（我实现第一版时正是踩了这个坑）。

    这条测试必须直接检验**排名行为**（而非只看惩罚函数）：
    把两个选项都记成"已选过"后，"离开"仍须排第一 ——
    只有无害选项豁免惩罚，才守得住这个结果。
    """
    summary = {"payload": {"event_options": _REPEAT_OPTS, "current_hp": 50, "max_hp": 80, "gold": 100}}
    for _ in range(4):
        planner.note_event_option_chosen(summary, 0)
        planner.note_event_option_chosen(summary, 1)
    ranked = planner._rank_event_options(summary)
    assert ranked[0] == 1, (
        f"两个选项都被重复选择后，最优解变成了 index {ranked[0]}（ordered={ranked}）—— "
        "重复惩罚错误地也压了无害选项"
    )


def test_event_screen_signature_ignores_hp_and_gold(planner: STS2HeuristicPlanner) -> None:
    """屏签名**不得**包含 hp/gold。

    它们会在同一屏内因已执行的选项而变化（扣上限/得金币），
    若纳入签名会让"已选次数"在每次选完后被重置，记忆直接失效。
    """
    a = _repeat_summary()
    b = _repeat_summary()
    b["payload"]["current_hp"] = 20
    b["payload"]["gold"] = 999
    b["payload"]["max_hp"] = 54
    assert planner._event_screen_signature(a) == planner._event_screen_signature(b), (
        "屏签名把 hp/gold 也算进去了 —— 记忆会在选完后被重置"
    )


def test_max_hp_gain_is_not_penalized(planner: STS2HeuristicPlanner) -> None:
    """★★核心回归（**实机发现**）：**获得**最大生命值绝不能被当成损失。

    真实事件日志（run `CMRNFBGPJGCV`）：
    ```
    启发分={2: 18, 0: 0, 1: -60} -> 选 option_index=2
       1: 获得[11]点最大生命值。      ← 被罚了 −60!
    ```
    我第一版 `_option_loses_max_hp` **只匹配"提到了最大生命"**，不判方向 ⇒
    把 `+11 最大生命` 这种**增益**也判成损失并重罚，脚本于是**主动放弃加血上限** ——
    与修复意图**完全相反**。这是实机数据抓出来的、单测覆盖不到的缺陷。
    """
    gain_cases = [
        "获得 11 点最大生命值。",
        "获得最大生命值",
        "+11 最大生命",
        "提升 5 点生命上限",
        "增加 10 点最大生命值",
        "Gain 5 Max HP",
        "你的生命上限提高了",     # 「提高」也是增益
    ]
    for text in gain_cases:
        assert planner._option_loses_max_hp(text) is False, f"增益被误判为损失：{text!r}"
        # ★断言"不扣分"而不是"== 0"：加上限现在**故意给正分**（+16，见 `_option_gains_max_hp`）。
        #   旧断言 `== 0` 编码的是"加上限无价值"这一缺陷 —— 实机里
        #   「获得11点最大生命值」与"什么都不发生"同分，于是它永远排在杂项之后。
        assert planner._score_event_option({"text": text}, 0.8, 99) > 0, (
            f"永久提升生命上限应给正分：{text!r}"
        )


def test_neutral_max_hp_mentions_are_not_treated_as_loss(planner: STS2HeuristicPlanner) -> None:
    """★中性提及最大生命**不得**被判为损失（补上的变异缺口）。

    为什么单列一条：`_option_loses_max_hp` 最后一行是 `return loss` ——
    即"提到最大生命"只是**必要条件**，还必须有**损失语义**。
    若把它误写成 `return True`（"提到就罚"），**先前的测试全部照样通过**
    （实测 47 passed），因为已有用例要么是增益（被 `gain and not loss` 提前拦下），
    要么是明确损失。**只有"中性提及"能区分这两种实现** ⇒ 必须显式覆盖。

    这类文案在真实事件里会出现（如"查看你的生命上限"），
    误判会让脚本无端放弃一个无害选项。
    """
    neutral_cases = [
        "查看生命上限",
        "最大生命为 80",
        "生命上限变化",
        "你的最大生命值",
        "生命上限",
    ]
    for text in neutral_cases:
        assert planner._option_loses_max_hp(text) is False, (
            f"中性提及被误判为损失：{text!r}"
        )


def test_max_hp_loss_is_still_detected_after_direction_fix(planner: STS2HeuristicPlanner) -> None:
    """反向钉子：加了方向判断后，**真正的损失**必须仍被识别（不能矫枉过正）。

    特别要保住的边界：`Lose 5 Max HP, gain a relic` —— 同时含损失词与增益词，
    必须以**损失为准**（先扣上限再给遗物，净收益远不足以抵消永久减上限）。
    """
    loss_cases = [
        "失去 5 点生命上限，获得 100 金币",
        "失去 10 点生命上限",
        "最大生命 -5",
        "Lose 5 Max HP",
        "Lose 5 Max HP, gain a relic",   # ★损失优先于增益
        "Lose Max HP",
    ]
    for text in loss_cases:
        assert planner._option_loses_max_hp(text) is True, f"真实损失未被识别：{text!r}"
    # 净损失必须得到显著负分
    assert planner._score_event_option({"text": "失去 5 点生命上限，获得 100 金币"}, 0.8, 99) < -20


def test_rich_text_tags_do_not_hide_hp_loss(planner: STS2HeuristicPlanner) -> None:
    """★★核心回归（**实机发现**）：富文本标签不得让掉血惩罚失效。

    真实实机文案（EVENT#3，血比 0.73）：
    ```
    启发分={0: 10, 1: 10} -> 选 0 | 0:获得藏宝图。 / 1:失去[red]8[/red]点生命。…
    ```
    游戏把标签**插在动词与数字之间**（`失去` `[red]` `8` `[/red]` `点生命`），
    于是 `失去\\s*\\d*\\s*点?(生命|血量)` **整体匹配失败** ⇒ 掉血惩罚完全丢失 ⇒
    两个选项**同分**，最后靠"平手取小下标"才选中安全项 —— **是靠运气，不是启发式**。
    """
    raw = "失去[red]8[/red]点生命。获得1瓶随机[gold]药水[/gold]。"
    clean = "失去8点生命。获得1瓶随机药水。"
    # 剥标签后两侧必须一致
    assert planner._strip_rich_text(raw) == clean
    assert planner._option_loses_hp(raw) is True, "带标签的掉血文案未被识别"
    # 归一化入口也必须生效（真正被调用的路径）。
    # 注意 `_event_option_text` 会拼接 label/text/description 三个字段，
    # 缺失字段会留下空格填充 ⇒ 只断言"内容被剥干净"，不比对精确串。
    normalized = planner._event_option_text({"text": raw})
    assert "[red]" not in normalized and "[/red]" not in normalized
    assert "[gold]" not in normalized and "[/gold]" not in normalized
    assert clean.lower() in normalized
    # 归一化后：掉血选项必须**严格低于**纯收益选项
    hurt = planner._score_event_option({"text": raw}, 0.73, 109)
    safe = planner._score_event_option({"text": "获得[gold]藏宝图[/gold]。"}, 0.73, 109)
    assert hurt < safe, f"掉血选项({hurt}) 不应高于安全选项({safe})"


def test_hp_loss_option_is_never_net_positive(planner: STS2HeuristicPlanner) -> None:
    """★★掉血/扣上限的选项**在任何血量下都不得净正分**。

    为什么需要这条：各条正向规则是**独立相加**的，"给得多"可以盖过掉血。
    实测确认过该漏洞（血比 0.73）：
      「失去8点生命。获得1瓶随机药水。」= −12 +14 = **+2** ⇒ 会**主动拿命换药水**。
    ⇒ 这是硬约束（"绝不主动拿命换命/换物资"），用**全血量扫描**钉死，而不是只测一个点。
    """
    cases = [
        "失去[red]8[/red]点生命。获得1瓶随机药水。",     # 掉血+药水（增益最大）
        "失去6点生命。获得100金币。",                    # 掉血+金币
        "失去 5 点生命上限，获得 100 金币",              # 扣上限+金币（实机缺陷 A 原型）
        "Lose 7 HP, gain a rare card and 200 gold.",   # 英文+多重收益
    ]
    for text in cases:
        for hp_ratio in (0.05, 0.30, 0.55, 0.60, 0.73, 0.94, 1.0):
            score = planner._score_event_option({"text": text}, hp_ratio, 50)
            assert score < 0, (
                f"掉血/扣上限选项在血比 {hp_ratio} 下净分 {score} ≥ 0（会主动拿命换）：{text!r}"
            )


def test_pure_gain_options_keep_positive_value(planner: STS2HeuristicPlanner) -> None:
    """反向钉子：纯收益选项**不得**被上面的抑制逻辑误伤。

    实机里这些文案原先都得 **0 分**（与"什么都不发生"同分）：
      * 获得[gold]藏宝图[/gold]。          → 遗物级道具
      * 获得一张随机[gold]稀有牌[/gold]。  → 文案不含"卡牌"二字，规则漏判
      * 回复[green]10[/green]点生命。       → 回血应随血量变化
    """
    # 道具与卡牌：与血量无关，恒为正
    for text in ("获得[gold]藏宝图[/gold]。", "获得一张随机[gold]稀有牌[/gold]。",
                 "获得[blue]11[/blue]点最大生命值。"):
        for hp_ratio in (0.3, 0.7, 0.95):
            assert planner._score_event_option({"text": text}, hp_ratio, 99) > 0, (
                f"纯收益选项被误判为零/负分：{text!r} @ {hp_ratio}"
            )
    # 回血：低血 > 满血（必须随血量变化，而不是恒定）
    heal = "回复[green]10[/green]点生命。"
    low = planner._score_event_option({"text": heal}, 0.30, 99)
    high = planner._score_event_option({"text": heal}, 0.94, 99)
    assert low > high, f"回血应更珍贵于低血：低血{low} 满血{high}"


def test_named_curses_are_penalized_like_the_curse_keyword(planner: STS2HeuristicPlanner) -> None:
    """★具体诅咒**卡名**必须与"诅咒"二字同等惩罚。

    实机文案写的是卡名而非类别词：「将一张[red]笨拙[/red]」「并将1张[red]愧疚[/red]」
    —— 原先都**逃过 −40 惩罚**（规则只认字面"诅咒"/"curse"）。
    """
    generic = planner._score_event_option({"text": "获得1张诅咒"}, 0.8, 99)
    for name in ("笨拙", "愧疚", "晕眩", "伤口", "诅咒"):
        named = planner._score_event_option({"text": f"将一张{name}"}, 0.8, 99)
        assert named == generic, f"卡名 {name} 的惩罚({named}) 应与类别词({generic}) 一致"
        assert named <= -40


# --------------------------------------------------------------------------
# 缺陷 C：复盘教训必须能作用于**事件屏**（且不破坏无教训时的契约）
# --------------------------------------------------------------------------

_CURSE_EVENT_OPTS = [
    {"index": 0, "text": "获得 1 张诅咒牌，获得 100 金币"},
    {"index": 1, "text": "失去 5 点生命"},
]


def _lesson_context(avoid: list[str], opts=None) -> dict:
    return {
        "classification": {"state_name": "event"},
        "summary_context": {
            "payload": {"event_options": opts if opts is not None else _CURSE_EVENT_OPTS,
                        "current_hp": 50, "max_hp": 80, "gold": 100}
        },
        "strategy_context": {
            "preferences": {},
            "strategy_directives": {"must": [], "prefer": [], "avoid": avoid},
        },
    }


def test_lesson_can_penalize_event_option(planner: STS2HeuristicPlanner) -> None:
    """★核心回归：`avoid` 教训必须能**降权事件选项**（缺陷 C）。

    此前 `_indices_worth_penalizing` 只覆盖路线与选牌两屏，事件屏返回空集 ⇒
    `avoid` 对事件决策**零影响**（inquisitor t12 实测：有无 avoid 的 plan() 逐字相同）。
    玩家的原话：「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。
    """
    payload = {"event_options": _CURSE_EVENT_OPTS}
    tokens = planner._lesson_tokens(["下次别拿诅咒了"])
    assert "curse" in tokens, "教训未能抽出 curse token，测试前提不成立"
    penalized = planner._indices_worth_penalizing(payload, "choose_event_option", tokens)
    assert 0 in penalized, f"诅咒选项未被降权（penalized={penalized}）—— 缺陷 C 未修复"


def test_lesson_reorders_event_options(planner: STS2HeuristicPlanner) -> None:
    """教训必须能真的**改变事件选项排序**（行为级断言，而非只看降权集合）。"""
    ctx = _lesson_context(["下次别拿诅咒了"])
    ranked = planner._ranked_option_indices(ctx, {"action_type": "choose_event_option",
                                                  "kwargs": {"option_index": 0}})
    assert ranked[0] == 1, f"诅咒项仍在最前（ranked={ranked}）—— 教训没改变事件决策"


def test_no_lesson_keeps_event_ranking_byte_identical(planner: STS2HeuristicPlanner) -> None:
    """★契约（必须保留）：**无教训**时排序与"完全没有记忆机制"逐字相同。

    这条对应 `test_run_lessons_affect_decision.py` 的既有契约钉，
    防止缺陷 C 的修复把无记忆场景也改掉。
    """
    ctx = _lesson_context([])
    ranked = planner._ranked_option_indices(ctx, {"action_type": "choose_event_option",
                                                  "kwargs": {"option_index": 0}})
    baseline = planner._rank_event_options(ctx["summary_context"])
    assert ranked == baseline, f"无教训时排序被改动：{ranked} != {baseline}"


def test_route_lesson_does_not_touch_event_screen(planner: STS2HeuristicPlanner) -> None:
    """反向断言：**路线类**教训不应影响事件屏（避免"教训乱打"）。

    这条防止把 `_indices_worth_penalizing` 写得太宽 —— 那会把复盘变成噪声。
    """
    ctx = _lesson_context(["碰到精英直接绕开"])
    ranked = planner._ranked_option_indices(ctx, {"action_type": "choose_event_option",
                                                  "kwargs": {"option_index": 0}})
    baseline = planner._rank_event_options(ctx["summary_context"])
    assert ranked == baseline, f"路线教训错误地影响了事件屏：{ranked} != {baseline}"


def test_lesson_penalty_demotes_without_deleting(planner: STS2HeuristicPlanner) -> None:
    """只降权不删除 —— 命中的选项仍须留在候选里（保证扰动层拿到的仍是合法集合）。"""
    ctx = _lesson_context(["下次别拿诅咒了"])
    ranked = planner._ranked_option_indices(ctx, {"action_type": "choose_event_option",
                                                  "kwargs": {"option_index": 0}})
    assert sorted(ranked) == sorted([0, 1]), f"教训删除了候选项：{ranked}"


def test_event_lesson_penalizes_max_hp_option(planner: STS2HeuristicPlanner) -> None:
    """「别再扣生命上限」类教训应能降权扣上限的选项（用户报告的核心痛点）。"""
    opts = [{"index": 0, "text": "失去 5 点生命上限，获得 100 金币"},
            {"index": 1, "text": "获得 1 个遗物"}]
    payload = {"event_options": opts}
    tokens = planner._lesson_tokens(["别再扣生命上限了"])
    penalized = planner._indices_worth_penalizing(payload, "choose_event_option", tokens)
    assert 0 in penalized, f"扣上限选项未被降权（penalized={penalized}, tokens={tokens}）"


def test_max_hp_token_cannot_leak_to_other_screens(planner: STS2HeuristicPlanner) -> None:
    """★`max_hp` token **结构上不可能**影响非事件屏。

    ⚠️ 写这条测试踩过的两个坑（记录以防后人重犯）：

    1. 我第一版断言"路线屏上 `max_hp` 不命中"，但那是**恒真**的 ——
       路线分支只把节点 `type` 与 token **等值**比较，`max_hp` 本就不可能等于 `rest`/`shop`。
    2. 于是我加了一个 `_EVENT_ONLY_TOKENS` 过滤函数想让断言语义化，
       但**变异测试证明它不可观测**：删掉过滤后全部 42 条测试照样通过
       （实测：`变异 -> 42 passed`）⇒ 那是"无法被测试保护的死代码"，已删除。

    本条测试改为主张**真正成立、且真的会被破坏**的不变量：
    token 与各屏取值域是等值语义，`max_hp` 不在任何非事件屏的取值域内。
    它同时钉住了路线屏归一后的实际取值域 —— 若有人把 `max_hp` 混进
    `_map_node_weight_key` 的返回集，这条会立刻变红。
    """
    # 1) 事件屏：max_hp 必须能命中扣上限选项（这是它存在的理由）
    event_payload = {"event_options": [{"index": 0, "text": "失去 5 点生命上限，获得 100 金币"},
                                       {"index": 1, "text": "获得 1 个遗物"}]}
    assert 0 in planner._indices_worth_penalizing(event_payload, "choose_event_option", ["max_hp"]), (
        "事件屏上 max_hp token 应能命中扣上限选项"
    )

    # 2) 路线屏：max_hp 不命中（因为取值域不含它 —— 这是**结构性**保证，不是过滤保证）
    map_payload = {"travelable_nodes": [{"index": 0, "type": "Rest"}, {"index": 1, "type": "Shop"}]}
    assert planner._indices_worth_penalizing(map_payload, "choose_map_node", ["max_hp"]) == set(), (
        "max_hp 命中了路线屏 —— 取值域被污染了"
    )

    # 3) 钉住取值域本身：路线屏归一后只会产出这些短键，其中没有 max_hp
    normalized = {planner._map_node_weight_key({"type": node_type})
                  for node_type in ("Elite", "Monster", "Rest", "Shop", "Treasure", "Event", "Boss")}
    assert "max_hp" not in normalized, f"路线屏取值域混入了 max_hp：{normalized}"
    assert normalized, "路线屏归一函数返回空集，测试前提不成立"


def test_transform_is_detected_from_screen_name_when_selection_kind_is_missing(
    planner: STS2HeuristicPlanner,
) -> None:
    """★核心回归（实测发现的静默失效）：`selection_kind` 缺失时**仍须**认出变换屏。

    为什么必须钉住：变换屏有两个**独立**信号来源 ——
      * `payload.selection_kind`（mod 的 `selection.kind`，值 `deck_transform_select`）
      * `payload.state_name`（屏幕名，值 `card_selection_transform`；
        实机日志 `screen=CARD_SELECTION_TRANSFORM` 真实出现过）
    只认前者时，一旦该字段缺失/改名，判定会**静默失效** ——
    变换屏退回复用删牌启发式，**每轮主动选诅咒**（正是缺陷 B 本身），且不报任何错。

    我实测过这个场景（`.dsh-artifacts/probe_b_statename.py`）：
    只给 state_name 时旧实现 `is_transform=False` ⇒ 选了诅咒。
    """
    cards = [{"index": 0, "name": "诅咒", "rarity": "curse", "card_type": "curse"},
             {"index": 1, "name": "打击", "rarity": "basic", "card_type": "attack"}]

    # 只给屏幕名，不给 selection_kind —— 这才是风险场景
    sm = {"payload": {"state_name": "card_selection_transform", "selection_cards": cards}}
    assert planner._is_transform_selection(sm) is True, (
        "只有 state_name=card_selection_transform 时未被识别为变换屏 —— 会退回去选诅咒"
    )

    # 只给 selection_kind，不给屏幕名 —— 原路径必须继续有效
    sm2 = {"payload": {"selection_kind": "deck_transform_select", "selection_cards": cards}}
    assert planner._is_transform_selection(sm2) is True, (
        "只有 selection_kind=deck_transform_select 时未被识别为变换屏"
    )


def test_remove_screen_is_never_mistaken_for_transform(planner: STS2HeuristicPlanner) -> None:
    """反向断言：删牌屏**绝不能**被误判成变换屏（否则删诅咒的功能被破坏）。

    删牌屏与变换屏的语义相反：
      * 删牌屏选诅咒 = 把它**移除** ⇒ 最优
      * 变换屏选诅咒 = 变成**另一张诅咒** ⇒ 最劣
    所以这个判定的两个方向都必须钉住。
    """
    sm = {"payload": {"state_name": "card_selection_remove",
                      "selection_kind": "deck_card_select",
                      "selection_cards": [{"index": 0, "name": "诅咒", "rarity": "curse"}]}}
    assert planner._is_transform_selection(sm) is False, (
        "删牌屏被误判为变换屏 —— 会导致删牌屏不再选择诅咒"
    )


def test_transform_and_remove_pick_opposite_cards(planner: STS2HeuristicPlanner) -> None:
    """同一副牌，变换屏与删牌屏的选择必须**相反**（端到端行为断言）。"""
    cards = [
        {"index": 0, "name": "诅咒", "rarity": "curse", "card_type": "curse", "cost": -2},
        {"index": 1, "name": "打击", "rarity": "basic", "card_type": "attack", "cost": 1, "damage": 6},
        {"index": 2, "name": "稀有强牌", "rarity": "rare", "card_type": "attack", "cost": 1, "damage": 20},
    ]

    def choose(state_name: str, kind: str) -> int | None:
        sm = {"payload": {"state_name": state_name, "selection_kind": kind,
                          "selection_cards": cards, "current_hp": 50, "max_hp": 80, "gold": 100}}
        ctx = {"classification": {"state_name": state_name}, "summary_context": sm,
               "strategy_context": {"preferences": {"records": []}, "strategy_directives": {}}}
        return planner._preferred_choice_option(sm, {"records": []}, ctx)

    transform_pick = choose("card_selection_transform", "deck_transform_select")
    remove_pick = choose("card_selection_remove", "deck_card_select")

    assert transform_pick == 1, f"变换屏应选普通废牌(index=1)，实际选了 {transform_pick}"
    assert remove_pick == 0, f"删牌屏应选诅咒(index=0)，实际选了 {remove_pick}"
    assert transform_pick != remove_pick, "变换屏与删牌屏选了同一张牌 —— 分流没有生效"


def test_transform_screen_emits_a_decision_log(planner: STS2HeuristicPlanner) -> None:
    """★变换屏必须留下决策日志（缺陷 B 的**可观测性**保障）。

    为什么单列一条：`CARD_SELECTION_TRANSFORM` 在历史日志里真实出现过 **67 次**，
    但**查不到任何决策痕迹** —— 变换屏此前和事件屏一样是盲区。
    没有日志就无法实机验证"是否还在选诅咒"，也无法在玩家报告"卡组越玩越差"时定位。

    去重同样要钉住：同一屏静止时不该每 tick 刷一行。
    """
    logged: list[str] = []

    class _Capture:
        def info(self, fmt: str, *args: object) -> None:
            logged.append(fmt % args if args else str(fmt))

    planner._logger = _Capture()
    cards = [{"index": 0, "name": "诅咒", "rarity": "curse", "card_type": "curse"},
             {"index": 1, "name": "打击", "rarity": "basic", "card_type": "attack", "damage": 6}]
    sm = {"payload": {"state_name": "card_selection_transform", "selection_cards": cards}}

    planner._preferred_transform_option(sm, {"records": []})
    assert logged, "变换屏没有输出任何决策日志 —— 实机无法验证缺陷 B"
    assert "sts2_transform_route" in logged[0], f"日志前缀不对：{logged[0]!r}"
    assert "诅咒" in logged[0], f"日志里应能看到诅咒候选：{logged[0]!r}"

    # 同一屏重复调用不得刷屏
    before = len(logged)
    planner._preferred_transform_option(sm, {"records": []})
    assert len(logged) == before, "同一屏重复输出了日志（去重失效）"


# --------------------------------------------------------------------------
# 缺陷 B：变化卡屏不得复用删牌启发式
# --------------------------------------------------------------------------

_CARDS = [
    {"index": 0, "card_id": "C1", "name": "诅咒-痛苦", "card_type": "Curse", "rarity": "Curse"},
    {"index": 1, "card_id": "S1", "name": "打击", "card_type": "Attack", "rarity": "Basic"},
    {"index": 2, "card_id": "R1", "name": "核心能力", "card_type": "Power", "rarity": "Rare"},
]


def _planner_pick(planner: STS2HeuristicPlanner, kind: str, cards=None):
    cards = cards if cards is not None else _CARDS
    summary = {"payload": {"selection_cards": cards, "selection_kind": kind}}
    return planner._preferred_choice_option(summary, {}, {})


def test_transform_screen_does_not_pick_curse_over_dead_card(planner: STS2HeuristicPlanner) -> None:
    """★核心回归：变化卡屏面对 `[诅咒, 打击, 稀有能力]` **不得**选诅咒。

    修复前它选 index 0（诅咒）—— 因为复用了删牌启发式（诅咒 −100 分 = 最该移除），
    于是把诅咒**变换**成另一张诅咒（游戏侧已证候选池含诅咒）。
    """
    picked = _planner_pick(planner, "deck_transform_select")
    assert picked is not None
    chosen = _CARDS[picked]
    assert "curse" not in str(chosen["rarity"]).lower(), (
        f"变化卡屏仍选中诅咒 {chosen['name']} —— 缺陷 B 未修复"
    )


def test_remove_screen_still_picks_curse(planner: STS2HeuristicPlanner) -> None:
    """反向断言：删牌屏**必须**继续选诅咒（那是"移除它"，语义正确）。

    这条防止修复"过度扩散" —— 若把删牌屏也改了，就破坏了原本正确的行为。
    """
    picked = _planner_pick(planner, "deck_card_select")
    assert picked == 0, f"删牌屏不再优先移除诅咒（选中 index={picked}）—— 过度修正"


def test_transform_prefers_removing_basic_over_rare(planner: STS2HeuristicPlanner) -> None:
    """变化卡屏应优先换掉**基础牌**，而不是稀有牌。"""
    cards = [
        {"index": 0, "card_id": "R1", "name": "核心能力", "card_type": "Power", "rarity": "Rare"},
        {"index": 1, "card_id": "S1", "name": "打击", "card_type": "Attack", "rarity": "Basic"},
    ]
    picked = _planner_pick(planner, "deck_transform_select", cards)
    assert picked == 1, "变化卡屏应换掉基础打击而不是稀有牌"


def test_transform_still_picks_curse_when_only_curse_and_good_card(planner: STS2HeuristicPlanner) -> None:
    """★陷阱规避断言：只剩 `[诅咒, 稀有牌]` 时，仍应选诅咒。

    这里刻意**不**断言"从不选诅咒" —— 目标是"避坑"，不是"绝对不碰诅咒"。
    当替代项是稀有牌时，把稀有牌换掉显然更亏，选诅咒（期望虽低但非负资产）才是合理的。
    """
    cards = [
        {"index": 0, "card_id": "C1", "name": "诅咒-痛苦", "card_type": "Curse", "rarity": "Curse"},
        {"index": 1, "card_id": "R1", "name": "核心能力", "card_type": "Power", "rarity": "Rare"},
    ]
    picked = _planner_pick(planner, "deck_transform_select", cards)
    assert picked == 0, "只剩诅咒与稀有牌时应选诅咒（换稀有牌更亏）"


def test_missing_selection_kind_falls_back_to_remove_heuristic(planner: STS2HeuristicPlanner) -> None:
    """取不到 `selection_kind` 时**必须**退回原行为，保证向后兼容。

    mod 老版本 / 其它选牌屏可能不提供该字段 —— 缺失时不能改变既有行为。
    """
    summary = {"payload": {"selection_cards": _CARDS}}
    legacy = planner._preferred_choice_option(summary, {}, {})
    assert legacy == _planner_pick(planner, "deck_card_select"), (
        "缺失 selection_kind 时行为变了 —— 破坏向后兼容"
    )
