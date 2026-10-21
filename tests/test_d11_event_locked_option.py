# -*- coding: utf-8 -*-
"""D11（t28）门：EVENT 屏选中被锁选项 ⇒ 游戏拒收 ⇒ 永久卡死。

## 实机缺陷（2026-10-20，run 6W6FGS6W4TPS / ENDLESS_CONVEYOR / floor=14）
```
/state 的 event.options 原文（逐字，01:11:21 抓取）：
  options[0] {title:破产了, is_locked:true }   ← 金币 18 不足支付
  options[1] {title:离开,   is_locked:false}
插件 [sts2_event_route]：启发分={0: 10, 1: 0} ⇒ 选 option_index=0（被锁的那个）
游戏：[sts2] 409 invalid_target "The selected event option is locked." × 445
⇒ 局面不变 ⇒ 每轮重算又得同一结论 ⇒ 永久卡死（00:24:24 → 01:1x 仍在卡）
```

## 真响应原文（逐字，2026-10-20 01:15:34，POST /action option_index=0）
```json
{"ok": false, "request_id": "req_20261019_171534_7028_4676",
 "error": {"code": "invalid_target",
           "message": "The selected event option is locked.",
           "details": {"action": "choose_event_option", "option_index": 0},
           "retryable": false}}
```
★ 实战抓取证明局面**逐位未变**（floor 14 / gold 18 / state_version 10 前后相同）⇒ 该 probe 无害。

## 四条根因（①③ 与原契约一致；② 被实测**推翻**；④⑤ 为实测新增）
- ① `_score_event_option`（:4320）只做**文本子串**匹配；`is_locked` 是布尔字段 ⇒ 结构上读不到。
     实测：被锁项文本「…兜里没[gold]金币[/gold]了。」命中 `"金币"` ⇒ **+10**（:4356），
     即「因为提到了金币，就给一个付不起钱的选项加分」。
- ② ❌ **推翻**：契约称被按「target_index 越界」处理（`action_engine.py:187`）——
     但真响应带 `retryable: false`，而 `transport_client.py:339` 优先用它
     ⇒ `service.py:1912` 的 `exc.retryable` 分支**从未执行**。
     独立复核：全日志 `[sts2_recover] 动作被拒` = **0** 条（对照 `[sts2_d9]` = 162）。
     ⇒ 注释描述的是「注释的语义」，不是「运行的语义」。
- ③ 无失效检测 —— EVENT 屏没有 D9 那类保护。
- ④ **新增**：`details.option_index` 被丢弃（游戏**精确点名**了元凶）。
- ⑤ **新增**：`details` **不含 `screen`** ⇒ 无法按屏区分 `invalid_target` 语义
     ⇒ 必须从「准备阶段预防」入手，而非「错误发生后再区分」。
"""

from __future__ import annotations

from typing import Any

import pytest
from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.transport_client import (
    STS2ActionRejected,
    rejection_from_envelope,
)

# ======================================================================================
# ★ 实机真形状（逐字抓取，禁止臆造）
# ======================================================================================

#: `/state` → `event.options` 原文（01:11:21）
REAL_OPTIONS: list[dict[str, Any]] = [
    {
        "index": 0,
        "text_key": "ENDLESS_CONVEYOR.pages.ALL.options.LOCKED",
        "title": "破产了",
        "description": "你还能吃得下更多，但是兜里没[gold]金币[/gold]了。",
        "is_locked": True,
        "is_proceed": False,
        "will_kill_player": False,
        "has_relic_preview": False,
    },
    {
        "index": 1,
        "text_key": "ENDLESS_CONVEYOR.pages.GRAB_SOMETHING_OFF_THE_BELT.options.LEAVE",
        "title": "离开",
        "description": "",
        "is_locked": False,
        "is_proceed": False,
        "will_kill_player": False,
        "has_relic_preview": False,
    },
]

#: 实机环境值：血 5/16（比 0.32）、金币 18
REAL_HP_RATIO = 0.32
REAL_GOLD = 18

#: 实机错误包原文（01:15:34，HTTP 409）
REAL_REJECTION: dict[str, Any] = {
    "ok": False,
    "request_id": "req_20261019_171534_7028_4676",
    "error": {
        "code": "invalid_target",
        "message": "The selected event option is locked.",
        "details": {"action": "choose_event_option", "option_index": 0},
        "retryable": False,
    },
}


def _ctx(options: list[dict[str, Any]] | None = None, *, floor: int = 14,
         event_id: str = "ENDLESS_CONVEYOR") -> dict[str, Any]:
    return {
        "payload": {
            "screen": "EVENT",
            "floor": floor,
            "event_id": event_id,
            "event_options": list(options if options is not None else REAL_OPTIONS),
            "current_hp": 5,
            "max_hp": 16,
            "gold": REAL_GOLD,
        }
    }


@pytest.fixture()
def planner() -> STS2HeuristicPlanner:
    return STS2HeuristicPlanner()


# ======================================================================================
# 1. 缺陷复现（AC1）：修复前给被锁项 +10、给未锁项 0 ⇒ 选中被锁项
# ======================================================================================


@pytest.mark.unit
def test_repro_locked_option_would_win_without_the_fix(planner: STS2HeuristicPlanner) -> None:
    """★ 复现契约描述的打分：被锁 10 分 vs 未锁 0 分（= 实机 `{0: 10, 1: 0}`）。

    ★ 本用例钉住的是**修复前**的算术（不是修复后行为）——
      它证明"被锁项确实曾因提及金币而得分更高"，是缺陷复现的一部分。
      做法：直接调 `_event_option_text` + 手工走金币规则，绕开已修复的 `_score_event_option`。
    """
    text_locked = planner._event_option_text(REAL_OPTIONS[0])
    text_free = planner._event_option_text(REAL_OPTIONS[1])
    # 实机取值：金币 18 < 150 ⇒ 命中 `"金币"` 规则给 +10（heuristic_planner.py:4356）
    assert "金币" in text_locked, f"被锁项文本应含'金币'（实测原文），得 {text_locked!r}"
    assert "金币" not in text_free
    # 其余正向规则对这两项都不命中（标题/描述均无遗物/卡牌/回血/上限词）
    assert not planner._option_loses_hp(text_locked)
    assert not planner._option_loses_max_hp(text_locked)
    assert not planner._option_gains_max_hp(text_locked)
    # ⇒ 修复前：被锁 +10、未锁 0 ⇒ 排序 [0, 1] ⇒ 选被锁项（正是实机 `启发分={0: 10, 1: 0}`）
    assert text_locked.count("金币") >= 1


# ======================================================================================
# 2. 修复后的行为（AC3）：选中**未锁**项
# ======================================================================================


@pytest.mark.unit
def test_locked_option_gets_sentinel_score(planner: STS2HeuristicPlanner) -> None:
    """★ 被锁项必须得**哨兵负分**，绝不低于任何未锁项。"""
    locked = planner._score_event_option(REAL_OPTIONS[0], REAL_HP_RATIO, REAL_GOLD)
    free = planner._score_event_option(REAL_OPTIONS[1], REAL_HP_RATIO, REAL_GOLD)
    assert locked == STS2HeuristicPlanner._EVENT_LOCKED_OPTION_SCORE, (
        f"★ 被锁项应得哨兵分 {STS2HeuristicPlanner._EVENT_LOCKED_OPTION_SCORE}，实测 {locked}"
    )
    assert locked < free, "★ 被锁项必须低于未锁项"


@pytest.mark.unit
def test_ranking_prefers_unlocked_option(planner: STS2HeuristicPlanner) -> None:
    """★★ AC3 核心：同一局面下首选必须是**未锁**的 index=1（修复前是 0）。"""
    ranked = planner._rank_event_options(_ctx())
    assert ranked, "排序不得为空"
    assert ranked[0] == 1, (
        f"★ 首选必须是被锁之外的 index=1（修复前=0=被锁项），实测 {ranked}"
    )
    assert ranked == [1, 0], f"★ 未锁项在前、被锁项在后，实测 {ranked}"


@pytest.mark.unit
def test_sentinel_beats_every_plausible_positive_score(planner: STS2HeuristicPlanner) -> None:
    """★ 哨兵分的**量级论证**必须成立：低于任何可能的单项/多项正向之和。

    这是"为什么不能用 −60"的可断言形式：若哨兵不够低，
    "遗物14+金币10+卡牌8+回血18 ≈ 50 > −60" ⇒ 仍会选被锁项 ⇒ 修不好。
    """
    sentinel = STS2HeuristicPlanner._EVENT_LOCKED_OPTION_SCORE
    # 构造一个把所有正向规则都命中的"超级未锁选项"
    jackpot = {
        "index": 9,
        "title": "获得遗物 卡牌 药水 稀有牌",
        "description": "获得[gold]100[/gold]金币。回复[green]20[/green]点生命。"
                       "获得[blue]11[/blue]点最大生命值。删除一张牌。",
        "is_locked": False,
    }
    rich = planner._score_event_option(jackpot, 0.30, 0)
    assert rich > 0, f"构造的'超级选项'应为正分，实测 {rich}"
    assert sentinel < rich, (
        f"★ 哨兵 {sentinel} 必须低于最强未锁项 {rich} ⇒ 否则仍会选被锁项"
    )
    # 且量级要足够宽松：即便正向规则全部命中并相加，也不该逼近哨兵
    assert sentinel < -100, f"★ 哨兵 {sentinel} 量级过小，不足以覆盖全部正向规则之和"


# ======================================================================================
# 3. ★ 不误伤（AC4）：所有选项未锁 / 字段缺失 ⇒ 行为逐位不变
# ======================================================================================


@pytest.mark.unit
def test_all_unlocked_ranking_is_unchanged(planner: STS2HeuristicPlanner) -> None:
    """★ AC4：全部未锁时，排序必须与"完全没有 is_locked 字段"**逐位相同**。"""
    unlocked = [dict(o, is_locked=False) for o in REAL_OPTIONS]
    without_field = [{k: v for k, v in o.items() if k != "is_locked"} for o in REAL_OPTIONS]

    with_false = STS2HeuristicPlanner()._rank_event_options(_ctx(unlocked))
    with_missing = STS2HeuristicPlanner()._rank_event_options(_ctx(without_field))
    assert with_false == with_missing, (
        f"★ is_locked=False 与字段缺失必须同序，实测 {with_false} vs {with_missing}"
    )


@pytest.mark.unit
def test_missing_is_locked_field_is_treated_as_unlocked(planner: STS2HeuristicPlanner) -> None:
    """★ 字段缺失（旧 mod / 测试替身）⇒ 视为**未锁**（保守），不得用哨兵分。"""
    without_field = [{k: v for k, v in o.items() if k != "is_locked"} for o in REAL_OPTIONS]
    scores = [planner._score_event_option(o, REAL_HP_RATIO, REAL_GOLD) for o in without_field]
    assert all(s != STS2HeuristicPlanner._EVENT_LOCKED_OPTION_SCORE for s in scores), (
        f"★ 字段缺失被判成已锁 ⇒ 误伤！实测 {scores}"
    )
    ranked = planner._rank_event_options(_ctx(without_field))
    assert ranked == [0, 1], f"★ 字段缺失时排序应与修复前一致（金币规则仍生效），实测 {ranked}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw_value,expected_locked",
    [
        (True, True),
        (False, False),
        (None, False),      # 字段为 null ⇒ 未锁
        ("true", True),     # 字符串真值（防御性；mod 实际给布尔）
        ("false", False),
        ("", False),
        (0, False),
        (1, False),         # ★ 只有真布尔 True 与显式真值串才算锁；整数 1 不认（保守）
    ],
)
def test_is_locked_predicate_is_narrow(
    planner: STS2HeuristicPlanner, raw_value: Any, expected_locked: bool
) -> None:
    """★ 判据必须**窄** —— 只认真 True / 显式真值串，其余一律"未锁"，避免误伤。"""
    option = {"index": 0, "title": "x", "is_locked": raw_value}
    assert planner._event_option_is_locked(option) is expected_locked, (
        f"is_locked={raw_value!r} 应判为 locked={expected_locked}"
    )


# ======================================================================================
# 4. ★ 解析游戏侧报错（AC5）：区分语义 + 读 option_index
# ======================================================================================


@pytest.mark.unit
def test_real_locked_rejection_is_recognised() -> None:
    """★★ AC5：实机真响应必须被识别为「选项被锁」，且读出被点名的 index。"""
    exc = rejection_from_envelope(REAL_REJECTION, 409)
    assert isinstance(exc, STS2ActionRejected)
    assert exc.code == "invalid_target"
    assert exc.retryable is False, (
        "★ 真响应 retryable=false ⇒ 必须原样保留（不得被 RETRYABLE_CODES 推断覆盖）"
    )
    assert exc.is_locked_option_rejection is True, "★ 必须识别为『选项被锁』"
    assert exc.locked_option_index == 0, "★ 必须读出游戏点名的 option_index"


@pytest.mark.unit
def test_combat_target_rejection_is_not_mistaken_for_locked() -> None:
    """★★ AC5 安全边界：战斗屏的 `target_index` 越界**不得**被判成「选项被锁」。

    ★ 依据（实机）：全日志 `invalid_target` 文本去重**只有** locked 这一种；
      而战斗越界的 message 是 `target_index is out of range for combat.enemies[]`，
      **不含** locked 字样、且 `details` 里**没有** `option_index` ⇒ 三重条件天然不命中。
    """
    combat = {
        "ok": False,
        "error": {
            "code": "invalid_target",
            "message": "target_index is out of range for combat.enemies[]",
            "details": {"action": "play_card", "target_index": 3},
            "retryable": True,
        },
    }
    exc = rejection_from_envelope(combat, 400)
    assert isinstance(exc, STS2ActionRejected)
    assert exc.is_locked_option_rejection is False, (
        "★ 战斗越界被误判成『选项被锁』⇒ 会把战斗路径带偏！"
    )
    assert exc.locked_option_index is None
    assert exc.retryable is True, "★ 战斗越界仍应可重试（原语义不得破坏）"


@pytest.mark.unit
def test_locked_message_without_index_is_not_treated_as_locked() -> None:
    """★ 边界：有 locked 文本但**没有** `option_index` ⇒ 不猜（保守）。"""
    no_index = {
        "ok": False,
        "error": {
            "code": "invalid_target",
            "message": "The selected event option is locked.",
            "details": {"action": "choose_event_option"},
            "retryable": False,
        },
    }
    exc = rejection_from_envelope(no_index, 409)
    assert exc.is_locked_option_rejection is False, "★ 没点名 index 就不猜"


# ======================================================================================
# 5. ★ 失效检测（AC6）：被点名后写入本屏禁用集，改选次优未锁项
# ======================================================================================


@pytest.mark.unit
def test_locked_option_is_banned_for_the_screen(planner: STS2HeuristicPlanner) -> None:
    """★★ AC6：游戏点名 index=0 被锁 ⇒ 该 index 进入本屏禁用集，后续不再首选。"""
    ctx = _ctx()
    planner.note_event_option_locked(ctx, 0)
    assert 0 in planner._event_locked_set_cached, "★ 被点名的 index 必须入禁用集"
    ranked = planner._rank_event_options(ctx)
    assert ranked[0] == 1, f"★ 禁用后首选必须是未锁项，实测 {ranked}"


@pytest.mark.unit
def test_ban_persists_across_ticks_for_same_screen(planner: STS2HeuristicPlanner) -> None:
    """★ 跨 tick 保持（同一屏）—— 否则每 tick 重新选中被锁项，等于没修。"""
    ctx = _ctx()
    planner.note_event_option_locked(ctx, 0)
    for i in range(5):
        ranked = planner._rank_event_options(ctx)
        assert ranked[0] == 1, f"第 {i + 1} tick 又选回被锁项 ⇒ 未修复"


@pytest.mark.unit
def test_ban_is_scoped_to_screen_and_clears_on_change(planner: STS2HeuristicPlanner) -> None:
    """★ AC6 关键：禁用集**按屏隔离** —— 换屏后必须清空，不得跨屏串味。

    ★ 本用例第一版写错过（我的失误，记录在此）：我用**同一批带 `is_locked=True` 的选项**
      只改 floor，然后断言"禁用集应清空"——但 `is_locked` 是**选项自身**属性，
      新屏上它**依然被锁**（游戏数据如此）⇒ 理应仍被禁。
      ⇒ 要测隔离，新屏的选项必须**不再被锁**。这正是"测试前提写错"的形态。
    """
    planner.note_event_option_locked(_ctx(floor=14), 0)
    assert 0 in planner._event_locked_set_cached

    # 新屏：index=0 **不再被锁**（这才是真正的"换了一个屏"）
    new_screen = [dict(REAL_OPTIONS[0], is_locked=False), REAL_OPTIONS[1]]
    ranked = planner._rank_event_options(_ctx(new_screen, floor=15))
    assert 0 not in planner._event_locked_set_cached, (
        "★ 换屏后禁用集必须清空（否则跨屏误禁）"
    )
    # 新屏上 index=0 合法 ⇒ 允许它凭分数胜出（金币规则仍给 +10 > 0）
    assert ranked[0] == 0, f"★ 新屏上 index=0 未被锁 ⇒ 应恢复可选，实测 {ranked}"


@pytest.mark.unit
def test_ban_key_differs_by_event_id(planner: STS2HeuristicPlanner) -> None:
    """★ 同一 floor 但**换了事件 id** 也必须算新屏（隔离键含 event_id）。"""
    planner.note_event_option_locked(_ctx(event_id="ENDLESS_CONVEYOR"), 0)
    assert planner._event_locked_set_key(_ctx(event_id="ENDLESS_CONVEYOR")) != (
        planner._event_locked_set_key(_ctx(event_id="OTHER_EVENT"))
    )


@pytest.mark.unit
def test_safety_valve_never_returns_empty_ranking(planner: STS2HeuristicPlanner) -> None:
    """★★ 安全属性：**所有**选项都被禁时，排序**不得为空**。

    为什么必须有：若直接"删除"被禁项，全禁时会得到空候选
    ⇒ `_preferred_event_option` 返回 None ⇒ 插件**不发任何动作** ⇒ 制造新的卡死。
    ⇒ 本修复必须保证"候选集永不为空"。
    """
    ctx = _ctx()
    planner.note_event_option_locked(ctx, 0)
    planner.note_event_option_locked(ctx, 1)
    ranked = planner._rank_event_options(ctx)
    assert ranked, "★ 全禁时排序为空 ⇒ 会退化成静默无动作，违反安全属性"
    assert len(ranked) == 2


@pytest.mark.unit
def test_ban_expires_when_authoritative_field_says_unlocked(planner: STS2HeuristicPlanner) -> None:
    """★★ 我自己引入的误报（实机 01:19:16 暴露 4 次）——必须钉死。

    ## 缺陷（t28 自测发现，属**我引入**的新问题）
    `ENDLESS_CONVEYOR` 是**多页事件**（`text_key` 含 `pages.ALL.options.LOCKED` /
    `pages.GRAB_SOMETHING_OFF_THE_BELT.options.LEAVE`）。换页后
    `screen|floor|event_id` **三者都不变** ⇒ 我的隔离键**无法区分页**
    ⇒ 上一页 `index=0` 被锁的禁用记录**串到下一页**，而下一页 `index=0` 是合法的
    ⇒ 实机连打 4 条「本屏所有选项均在禁用集内 [0]」**误报**。

    ## 修法
    `/state` 的 `is_locked` 是**权威且更新**的数据源；禁用集只是**错误驱动的补充**。
    ⇒ 本 tick 若明确说明该 index **未**被锁（字段为 `False`），则旧禁用记录**已过期**，必须移除。
    （「页 id」从 `/state` 拿不到，故不能靠加大隔离键解决。）

    ## 本用例
    同一隔离键（同 screen/floor/event_id）下，从"0 被锁"切到"0 未锁" ⇒ 禁用必须立即失效。
    """
    # 第一页：index=0 被锁
    page1 = [dict(REAL_OPTIONS[0], is_locked=True), REAL_OPTIONS[1]]
    planner.note_event_option_locked(_ctx(page1), 0)
    assert 0 in planner._event_locked_set_cached
    planner._rank_event_options(_ctx(page1))
    assert 0 in planner._event_locked_set_cached, "第一页上 0 确实被锁 ⇒ 禁用集应保留"

    # 第二页：**同一个隔离键**（同 screen/floor/event_id），但 index=0 **未被锁**
    page2 = [dict(REAL_OPTIONS[0], is_locked=False), REAL_OPTIONS[1]]
    ctx2 = _ctx(page2)
    assert planner._event_locked_set_key(ctx2) == planner._event_locked_set_key(_ctx(page1)), (
        "前提：两页的隔离键相同（这正是原缺陷的成因）"
    )
    ranked = planner._rank_event_options(ctx2)
    assert 0 not in planner._event_locked_set_cached, (
        "★★ 权威字段说 index=0 未锁 ⇒ 旧禁用记录必须失效，否则误禁合法选项"
    )
    # 第二页上 0 未锁 ⇒ 允许它凭分数（金币 +10）胜出
    assert ranked[0] == 0, f"★ 第二页 index=0 合法 ⇒ 应恢复可选，实测 {ranked}"


@pytest.mark.unit
def test_ban_survives_while_field_still_reports_locked(planner: STS2HeuristicPlanner) -> None:
    """★ 反向保护：字段**仍说被锁**时，禁用集**不得**被清除（否则修复失效）。"""
    ctx = _ctx()
    planner.note_event_option_locked(ctx, 0)
    for i in range(3):
        planner._rank_event_options(ctx)   # 每 tick option[0].is_locked 仍为 True
        assert 0 in planner._event_locked_set_cached, f"第 {i + 1} tick 禁用集被错误清除"


@pytest.mark.unit
def test_ban_dropped_for_indices_absent_from_screen(planner: STS2HeuristicPlanner) -> None:
    """★ 清理：index 已不在本屏出现 ⇒ 禁用记录应被清掉（防跨屏累积）。"""
    ctx = _ctx()
    planner.note_event_option_locked(ctx, 0)
    planner.note_event_option_locked(ctx, 1)
    assert planner._event_locked_set_cached == {0, 1}
    # 下一屏只剩 index=0 且它未被锁
    only_one = {"payload": dict(ctx["payload"],
                                event_options=[dict(REAL_OPTIONS[0], is_locked=False)])}
    planner._rank_event_options(only_one)
    assert planner._event_locked_set_cached == set(), (
        f"★ index=1 已不在本屏 ⇒ 须清掉；index=0 未被锁 ⇒ 也须清掉。"
        f"实测 {planner._event_locked_set_cached}"
    )


@pytest.mark.unit
def test_ban_retries_constant_is_two() -> None:
    """★ AC6 要求给出 N 的取值依据 —— 把 N=2 钉住，防止被无声改动。"""
    assert STS2HeuristicPlanner._EVENT_LOCKED_OPTION_RETRIES == 2, (
        "★ N=2 的依据：EVENT 屏选项少、失败是游戏**逐字明说**的确定事实"
        "（非概率抖动）⇒ 不需要像战斗屏 D9 那样容忍 3 次"
    )


# ======================================================================================
# 6. 与既有行为共存：日志与分数不变
# ======================================================================================


@pytest.mark.unit
def test_hei_scores_still_expose_locked_option(planner: STS2HeuristicPlanner) -> None:
    """★ 被锁项仍应**出现在分数表**里（供 `[sts2_event_route]` 日志显示为何被排除），
    只是排到最后 —— 不得从分数表里消失（否则日志无法诊断）。"""
    ranked = planner._rank_event_options(_ctx())
    assert set(ranked) == {0, 1}, f"排序必须包含全部选项，实测 {ranked}"


@pytest.mark.unit
def test_unlocked_only_screen_score_identity(planner: STS2HeuristicPlanner) -> None:
    """★ 不误伤（数值级）：全部未锁时，每项的分数必须与"字段不存在"时**逐位相同**。"""
    unlocked = [dict(o, is_locked=False) for o in REAL_OPTIONS]
    without = [{k: v for k, v in o.items() if k != "is_locked"} for o in REAL_OPTIONS]
    a = [planner._score_event_option(o, REAL_HP_RATIO, REAL_GOLD) for o in unlocked]
    b = [planner._score_event_option(o, REAL_HP_RATIO, REAL_GOLD) for o in without]
    assert a == b, f"★ 分数必须逐位一致，实测 {a} vs {b}"
    assert a == [10, 0], f"★ 修复前这两个分数的取值应保持（金币规则），实测 {a}"
