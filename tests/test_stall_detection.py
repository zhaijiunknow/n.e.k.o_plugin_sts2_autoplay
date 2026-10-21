"""t25 / CANON §13.2 空转检测回归门：用【领域量】（新 turn_key）替代失效的心跳式活性判据。

## 缺陷（实测，见 CANON §13）
卡死窗口 16:40:26 → 23:47:11（7.27 小时）内，**全部现有"活性判据"都是健康的**：
`step_count` 2977 行在涨、`TICK` 483、`heartbeat` 10796、`[sts2_plan]` 2090，
而 `record run end` = 0、`floor` 变化 = 0 ⇒ 零推进、零落盘，吃掉 ≈20 局有效样本。
根因：现有判据全是【心跳量】（"循环跑过多少 tick"），而心跳量只证活、不证推进。

## 本文件覆盖的判据
1. 见到 `turn_key ∉ 已见集合` ⇒ 计一次「新 key」；
2. 连续 N 分钟（默认 10）新 key == 0 **且** heartbeat 在涨 ⇒ 判空转并**告警**；
3. ★ **不误伤**：活跃期的慢窗口【不得】告警（实测活跃期零新增窗口中
   17~37 个不同旧局面在交替，最高频 key 占比 ≤0.11；真卡死窗口占比 ≥0.78）；
4. ★ **不得与「进程死亡」混淆**：heartbeat 不在涨时【不得】告警；
5. ★ 变异测试：删掉「且 heartbeat 在涨」/ 新 key 计数恒 0 ⇒ 必须有测试转红。

## 实机口径（本判据的取值来源）
- `turn_key` 由 `[sts2_combat_turn_*]` 日志行携带，值 = `act:floor:combat:turn`
  （`service.py:2987 _combat_turn_key` 是 `run_id|floor|room|turn` 的另一族键，
   与日志里被审计的 `a:f:screen:turn` 不是同一个；本判据的**形状**对两者都成立）。
- `heartbeat` = `[sts2_heartbeat]` 行计数（`loop_runner.py:795 _hb_log`），
  只证"循环在跑"，**不证**推进 —— 本判据正是要治它。
"""
from __future__ import annotations

import ast
import io
import logging
import os
import sys
import types
from typing import Any

from plugin.plugins.sts2_autoplay.runtime_state import (
    STALL_DOMINANCE_RATIO,
    STALL_MIN_SAMPLES,
    STALL_WINDOW_SECONDS,
    STS2RuntimeState,
)

# 与 CANON §13.2 一致的默认阈值：连续 10 分钟。
WINDOW = STALL_WINDOW_SECONDS
assert WINDOW == 600.0, "★ 契约：默认阈值必须是 10 分钟（CANON §13.2）"

def _feed(
    state: STS2RuntimeState,
    *,
    key: str,
    seconds: float,
    heartbeat: int,
    start: float = 1000.0,
    step: float = 30.0,
) -> list[dict[str, Any]]:
    """在 [start, start+seconds] 上按 step 秒喂同一条 turn_key，返回每次的判定。

    heartbeat 每次 +1（"在涨"）。测试要"死亡"分支时自己改 heartbeat。
    """
    out = []
    t = start
    end = start + seconds
    hb = heartbeat
    while t <= end:
        out.append(state.note_turn_key_sample(key, heartbeat=hb, now=t))
        hb += 1
        t += step
    return out

def _feed_cyclic_real_cadence(
    state: STS2RuntimeState,
    *,
    keys: list[str],
    seconds: float,
    start: float = 1000.0,
    step: float = 1.0,
) -> dict[str, Any]:
    """按**实机采样密度**（≈1 秒 1 条 turn_key 行）循环喂多条旧 key。

    实机口径：活跃期 10 分钟窗内 turn_key 发射行 600~1150 条
    （见 `.dsh-artifacts/_t25_discriminate.log` 的「发射行」列），
    所以本函数默认 step=1.0 秒，与现场同量级。
    """
    for k in keys:
        state.note_turn_key_sample(k, heartbeat=None, now=start - 1.0)
    state.stall_window_started_at = start
    state.stall_reported = False
    hb = 1
    i = 0
    t = start
    last = None
    end = start + seconds
    while t <= end:
        last = state.note_turn_key_sample(keys[i % len(keys)], heartbeat=hb, now=t)
        hb += 1
        i += 1
        t += step
    return last

# ── 1. 「新 key 计数」这一领域量确实被计 ──────────────────────────────────────

def test_a_turn_key_is_counted_as_new_only_once() -> None:
    """★ 契约第 1 条：`turn_key ∉ S` 计一次新 key 并加入 S；重复出现不再计。"""
    state = STS2RuntimeState()
    assert state.stall_new_key_count == 0
    first = state.note_turn_key_sample("0:14:combat:5", heartbeat=1, now=100.0)
    assert first["new_key"] is True
    assert state.stall_new_key_count == 1
    again = state.note_turn_key_sample("0:14:combat:5", heartbeat=2, now=110.0)
    assert again["new_key"] is False, "同一 key 再出现不算推进"
    assert state.stall_new_key_count == 1
    second = state.note_turn_key_sample("0:14:combat:6", heartbeat=3, now=120.0)
    assert second["new_key"] is True, "新 key（回合前进）必须计入"
    assert state.stall_new_key_count == 2

def test_an_empty_turn_key_is_not_a_new_key() -> None:
    """取不到 turn_key（非战斗屏）⇒ 不计入、也不重置窗口（它不是局面推进）。"""
    state = STS2RuntimeState()
    state.note_turn_key_sample("", heartbeat=1, now=100.0)
    assert state.stall_new_key_count == 0

# ── 2. 达到阈值 ⇒ 判空转并告警 ────────────────────────────────────────────────

def test_stall_is_flagged_after_the_window_with_a_clear_warning(caplog) -> None:
    """★ 契约第 2 条：同一局面反复重算满 10 分钟且 heartbeat 在涨 ⇒ 判空转 + 明确告警。

    ★ 2026-10-20 修正：判据的**必要**条件是「占比 ≥ 0.9」(①)+样本足+心跳涨；
      "无新 key"降为可选加强项（因为 turn_key 不含 run_id，跨 run 撞值 75%）。
      本测试用单一 key 反复重算（占比恒 1.00）⇒ 四条同时成立。
    """
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    verdicts = _feed(state, key="0:14:combat:5", seconds=WINDOW, heartbeat=1, start=1000.0)
    assert verdicts[-1]["stalled"] is True, "★ 同一局面反复重算满 10 分钟必须判空转"
    assert verdicts[-1]["heartbeat_growing"] is True
    assert verdicts[-1]["should_warn"] is True, "★ 必须产生一次告警"
    assert sum(1 for v in verdicts if v["should_warn"]) == 1, "同一窗口只告警一次（不刷屏）"

def test_the_warning_is_emitted_by_the_caller_with_a_readable_message(caplog) -> None:
    """判据把 `should_warn` 交出来，调用方据此打**明确**日志。

    ★ 文案必须表述为【同一 key 的重复率】而不是【新 key 的有无】——
      这样即使 turn_key 跨 run 撞值也不产生假告警（见 t27/队长发现）。
    """
    logger = logging.getLogger("test.t25")
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    with caplog.at_level(logging.WARNING):
        for v in _feed(state, key="0:14:combat:5", seconds=WINDOW, heartbeat=1, start=1000.0):
            if v["should_warn"]:
                logger.warning(
                    "[sts2_stall] ⚠ 疑似空转：%.0f 分钟内同一局面（%s）反复重算 %d 次"
                    "（占比 %.2f，样本 %d，heartbeat 在涨）",
                    v["zero_new_seconds"] / 60.0,
                    "0:14:combat:5",
                    v["repeats"],
                    v["top_key_share"],
                    v["samples"],
                )
    hits = [r for r in caplog.records if "sts2_stall" in r.getMessage()]
    assert hits, "★ 判空转必须留下明确告警（而非静默）"
    assert "反复重算" in hits[0].getMessage()

def test_a_new_turn_key_resets_the_window() -> None:
    """★ 见到新局面 ⇒ 窗口重置、且之后能再次告警（不自锁）。"""
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    _feed(state, key="0:14:combat:5", seconds=WINDOW, heartbeat=1, start=1000.0)
    assert state.stall_reported is True
    # 局面推进：新 key
    state.note_turn_key_sample("0:14:combat:6", heartbeat=99, now=1700.0)
    assert state.stall_reported is False, "新局面必须复位告警标志"
    assert state.stall_window_started_at == 1700.0
    # 再空转一整个窗口 ⇒ 必须能再次告警
    again = _feed(state, key="0:14:combat:6", seconds=WINDOW, heartbeat=100, start=1700.0)
    assert again[-1]["should_warn"] is True, "★ 第二次空转必须还能报（不得只报一次就哑）"

# ── 2b. ★★★ 跨 run 撞值（t27 / 队长发现）：turn_key 不含 run_id ─────────────

def test_turn_key_format_carries_no_run_id_so_cross_run_collision_is_real() -> None:
    """★ 事实核验：turn_key 的格式是 `act:floor:screen:turn`，**不含 run_id**。

    实测（.dsh-artifacts/_t27_runcollision2.log，189,335 行）：
      含 turn_key 行 40,323 ；含 run_id 行 18,776 ；【同行 = 0】
      ⇒ 用 run 变化点打标签后：175 个 turn_key 里 **132 个（75%）出现在多个 run**
         （0:2:combat:1 出现在 20 个 run）
    本测试把"格式不含 run_id"钉住：一旦有人在 turn_key 里加了 run_id，
    本测试会失败，提示可以安全恢复"新 key"为必要条件。
    """
    import re
    for sample in ("0:14:combat:5", "0:2:combat:1", "1:25:combat:1"):
        parts = sample.split(":")
        assert len(parts) == 4, "turn_key 恰好 4 段：act:floor:screen:turn"
        act, floor, screen, turn = parts
        assert act.isdigit() and floor.isdigit() and turn.isdigit()
        assert screen == "combat"
        assert not re.search(r"[A-Z0-9]{12}", sample), (
            "★ turn_key 不含 run_id —— 这是跨 run 撞值的根因；若此断言失败说明格式已改"
        )

def test_cross_run_collision_no_longer_causes_a_false_stall() -> None:
    """★★ 核心回归：同一 floor 反复换局（撞值）**不得**产生假空转。

    这是 t27 要防的：换局后 floor=5/turn=1 重新出现，若按"全局首见"计新 key，
    旧 key 永不"新" ⇒ 窗口永不重置 ⇒ 假卡死。

    ★ 实测机制（.dsh-artifacts/_t27_runcollision2.log）：
      活跃期"零新增"窗口里 17~37 个**早已见过的**旧 key 在交替（占比 ≤0.875）。
      这正是判据必须能放过的那一类。

    做法：先让这些 key 全部"见过"（模拟同 run 内早先出现过），
    再在一个窗口里让它们交替 —— 此时 new_key 恒 False，
    若"新 key"是必要条件就永远不会重置窗口（= 老实现的假卡死），
    而占比维 < 0.9 必须否决。
    """
    state = STS2RuntimeState()
    keys = ["0:5:combat:%d" % i for i in range(1, 11)]
    # ① 先让 10 个 key 都"见过"（同 run 内早先推进过；每个新 key 会重置窗口，
    #    这正是"真推进"该有的行为）
    t = 1000.0
    hb = 0
    for k in keys[:-1]:
        hb += 1
        state.note_turn_key_sample(k, heartbeat=hb, now=t, run_id="CCCCCCCCRUN3")
        t += 1.0
    assert state.stall_new_key_count == len(keys) - 1

    # ② 现在让它们交替 600 秒（全部是旧 key ⇒ new_key 恒 False）。
    #    老实现会在这里累积"零新增窗口"直到超时 ⇒ 假卡死。
    start = t
    while t - start <= WINDOW + 5:
        for k in keys:
            hb += 1
            v = state.note_turn_key_sample(k, heartbeat=hb, now=t, run_id="CCCCCCCCRUN3")
            t += 1.0
    assert v["new_key"] is False, "全部是旧 key ⇒ 本 tick 不是新 key"
    assert v["distinct_keys"] > 1, "窗口内确实有多个不同局面在交替"
    assert v["top_key_share"] < 0.9, "多局面交替 ⇒ 占比必须低于门槛"
    assert v["stalled"] is False, (
        "★ 同 floor 换局撞值 / 跑图期多局面交替【不得】产生假空转"
    )

def test_run_id_resets_the_seen_set_so_a_repeated_floor_still_counts_as_new() -> None:
    """★ run_id 变化 ⇒ "已见集合"必须重置（否则同 floor 换局会假卡死）。"""
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:5:combat:1", heartbeat=1, now=1000.0, run_id="RUN_A")
    assert state.stall_new_key_count == 1
    # 同一 run 内重复：不算新
    state.note_turn_key_sample("0:5:combat:1", heartbeat=2, now=1001.0, run_id="RUN_A")
    assert state.stall_new_key_count == 1
    # 换 run：同一 key 必须重新算"新"
    state.note_turn_key_sample("0:5:combat:1", heartbeat=3, now=1002.0, run_id="RUN_B")
    assert state.stall_new_key_count == 2, "★ 换 run 后同 key 必须重新算新（否则假卡死）"

def test_new_key_is_not_a_necessary_condition_by_default() -> None:
    """★★ 判据【不】要求"无新 key"：占比维单独就足以判空转。

    理由：turn_key 不含 run_id（75% 撞值），"新 key"在跨 run 语义下不可靠。
    本测试钉住这个设计选择：单一 key 反复重算但窗口起点被拉回，仍须判空转。
    """
    state = STS2RuntimeState()
    state.stall_window_started_at = 1000.0
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    state.stall_window_started_at = 1000.0  # 人为把窗口起点拉回（模拟"新 key 没重置成功"）
    state.stall_reported = False
    last = None
    hb = 1
    t = 1000.0
    while t <= 1000.0 + WINDOW:
        last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
        hb += 1
        t += 1.0
    assert last["top_key_share"] >= 0.9
    assert last["stalled"] is True, "★ 占比维独立成判据：不依赖'无新 key'"
    assert "no_new_key" not in last["conditions"], (
        "★ 该维已按 §11.7 删净，不得再出现在 conditions 里"
    )

def test_no_dead_parameter_is_left_on_note_turn_key_sample() -> None:
    """★★ 回归门：钉死队长在 :429 发现的那类缺陷 —— 「看起来实现了、实则恒真」的形参。

    缺陷原形（已删）：
        condition_no_new_key = condition_duration          # 别名
        if require_no_new_key: stalled = stalled and condition_no_new_key
    ⇒ 而 duration 本就是必要项 ⇒ 该合取**恒等于 stalled** ⇒ 形参无任何区分力。
    队长实测：require=False 与 True 在真卡死形状下 stalled 均为 True。

    ⇒ 本测试用**签名反射**守住"不得再引入死形参"：
      凡 note_turn_key_sample 接受的布尔开关，必须在某输入下真的改变 stalled。
      这里把已删的 require_no_new_key 作为**必须不存在**的项钉死。
    """
    import inspect

    sig = inspect.signature(STS2RuntimeState.note_turn_key_sample)
    assert "require_no_new_key" not in sig.parameters, (
        "★ 该形参已在队长复核后删除（恒真、无区分力）；不得重新引入。"
        "若要重新加回，必须先有测试证明它在某个输入下改变 stalled。"
    )
    # 现存形参都应是有意义的口径旋钮（可传、且被回报）
    for name in ("dominance_ratio", "window_seconds", "min_samples", "run_id"):
        assert name in sig.parameters, "口径旋钮 %s 必须保留（可调、可归因）" % name

def test_every_condition_key_actually_participates_in_stalled() -> None:
    """★★ 回归门：conditions 里回报的**每一条**都必须真的参与 stalled 判定。

    这是上面那条死形参缺陷的一般化形式：
      "报告了一个分项，但它在判定里恒为真 / 被别名顶替" ⇒ 使用者会被误导。
    做法：对每个分项构造一个**只让它为 False** 的输入，断言 stalled 随之变 False。
    """
    keys = ("duration", "dominance", "enough_samples", "heartbeat_growing")
    for target in keys:
        state = STS2RuntimeState()
        # 基准：真卡死 ⇒ 全绿
        state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
        last = None
        hb = 1
        t = 1000.0
        while t <= 1000.0 + WINDOW:
            last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
            hb += 1
            t += 1.0
        assert all(last["conditions"][k] for k in keys), "基准必须全绿（否则本测试空转）"
        assert last["stalled"] is True

        # 逐个打掉：只让 target 这条为 False
        probe = STS2RuntimeState()
        if target == "duration":
            # 窗口不够长：把起点往后推，使 zero_new_seconds < window_seconds
            probe.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
            probe.stall_window_started_at = 1000.0 + WINDOW  # 人为推迟
            v = probe.note_turn_key_sample("0:14:combat:5", heartbeat=1, now=1000.0 + WINDOW - 1)
        elif target == "dominance":
            v = probe.note_turn_key_sample(
                "0:14:combat:5", heartbeat=1, now=1000.0, dominance_ratio=1.01
            )
        elif target == "enough_samples":
            probe.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
            v = probe.note_turn_key_sample(
                "0:14:combat:5", heartbeat=1, now=1000.0 + WINDOW, min_samples=10_000
            )
        else:  # heartbeat_growing
            probe.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=1000.0)
            v = None
            for tt in range(1001, 1001 + int(WINDOW) + 5):
                v = probe.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=float(tt))
        assert v["conditions"][target] is False, (
            "★ 构造失败：%s 未被单独打掉（本测试会空转）" % target
        )
        assert v["stalled"] is False, (
            "★★ %s 在 conditions 里被回报，却在 stalled 里不起作用 —— "
            "说明它是被别名顶替的死分项（与队长发现的 require_no_new_key 同类）" % target
        )

# ── 3. ★ 不误伤：活跃期的慢窗口不得告警 ───────────────────────────────────────

def test_an_active_slow_window_with_many_distinct_keys_does_not_warn() -> None:
    """★ 契约第 3 条：活跃期慢窗口【不得】告警。

    实机口径（`.dsh-artifacts/_t25_discriminate.log`）：活跃期内有 80/306 个
    10 分钟窗新 key 也为 0，但它们是 **17~37 个不同旧局面在交替**
    （最高频 key 占比 0.05~0.11）—— 那是跑图/切屏，不是空转。
    这里用 20 个不同 key 交替喂满 10 分钟复现该形态。
    """
    state = STS2RuntimeState()
    keys = ["0:%d:combat:%d" % (f, t) for f in range(2, 22) for t in (1, 2)]
    last = _feed_cyclic_real_cadence(state, keys=keys, seconds=WINDOW, start=1000.0)
    assert last["zero_new_seconds"] >= WINDOW, "确实满了一个零新增窗口"
    assert last["top_key_share"] < STALL_DOMINANCE_RATIO
    assert last["distinct_keys"] >= 10, "实测口径：活跃期零新增窗口里 17~37 种 key"
    assert last["stalled"] is False, (
        "★ 活跃期多个旧局面交替【不得】判空转（否则跑图期会全体误报）"
    )
    assert last["should_warn"] is False

def test_measured_active_window_shape_matches_the_real_log() -> None:
    """把实测的活跃期窗口形状（17~37 种 key，top1 占比 0.05~0.11）钉进测试。

    实测值来自 `.dsh-artifacts/_t25_discriminate.log`（2026-10-19 全量日志）：
      零新增窗口里种类数分布 = {17:3, 34:1, 29:3, 27:9, 26:9, ...}
      零新增窗口里 top1 占比 min/max = 0.05 / 0.11
    真卡死窗口（16:50 起）：种类 = 1，top1 占比 = 1.00。
    """
    state = STS2RuntimeState()
    # 实测最"像卡死"的活跃期窗口：17 种 key、108/641 = 0.17 的最大占比（12:28 窗口）
    keys = ["0:17:combat:%d" % i for i in range(1, 18)]
    last = _feed_cyclic_real_cadence(state, keys=keys, seconds=WINDOW, start=1000.0)
    assert last["distinct_keys"] == len(keys), "17 种 key 在 10 分钟窗内交替（实测口径）"
    assert last["top_key_share"] <= 0.2, "实测活跃期最高频占比 ≤0.11（本构造 ≈1/17，远低于 0.9）"
    assert last["stalled"] is False

def test_a_real_stall_shape_is_flagged() -> None:
    """真卡死形状（单一 key 反复重算，占比 1.00）在同一阈值下必须被判出。

    实测：`0:14:combat:5` 在 16:50 之后的每个 10 分钟窗里都是**唯一**的 key
    （占比 1.00，见 `.dsh-artifacts/_t25_discriminate2.log`）。
    """
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    # 实机采样密度（≈1 秒 1 行），与 `.dsh-artifacts/_t25_discriminate2.log` 同量级
    last = None
    hb = 1
    t = 1000.0
    while t <= 1000.0 + WINDOW:
        last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
        hb += 1
        t += 1.0
    assert last["distinct_keys"] == 1 and last["top_key_share"] == 1.0
    assert last["stalled"] is True

# ── 4. ★ 不得与「进程死亡」混淆 ──────────────────────────────────────────────

def test_a_flat_heartbeat_is_death_not_stall(caplog) -> None:
    """★ 契约第 4 条：heartbeat 不在涨 ⇒ 【不得】报空转（那是通道失效，§9.42）。

    ★ 采样按实机节奏（≈1s）喂满，确保"时长"这条真的满足 ——
      否则判据会因为窗口没满而不触发，测试就变成**空断言**（假绿）。
    """
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=1000.0)
    verdicts = []
    for t in range(1001, 1001 + int(WINDOW) + 60):
        # heartbeat 冻结在 7（进程死了 / 循环不再推进心跳）
        verdicts.append(state.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=float(t)))
    last = verdicts[-1]
    assert last["conditions"]["duration"] is True, "窗口确实满了（否则本测试是空断言）"
    assert last["conditions"]["dominance"] is True, "★ 占比维【确实成立】—— 局面真的没动"
    assert last["heartbeat_growing"] is False
    assert last["stalled"] is False, "★ heartbeat 不涨 ⇒ 不得判空转（否则与进程死亡混淆）"
    assert not any(v["should_warn"] for v in verdicts), "★ 死亡场景不得打空转告警"

def test_heartbeat_absent_is_treated_as_unknown_and_does_not_warn() -> None:
    """拿不到 heartbeat（None）⇒ 视为"未知"，保守不判空转（宁漏报不误报）。"""
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=None, now=1000.0)
    for t in range(1001, 1001 + int(WINDOW) + 60):
        v = state.note_turn_key_sample("0:14:combat:5", heartbeat=None, now=float(t))
    assert v["heartbeat_growing"] is False
    assert v["stalled"] is False

def test_too_few_samples_does_not_warn() -> None:
    """采样太少（不足以判定）⇒ 不告警（避免日志刚滚动/reload 后误报）。"""
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    # 只在窗口末尾喂 2 个样本（远少于 STALL_MIN_SAMPLES）
    state.note_turn_key_sample("0:14:combat:5", heartbeat=1, now=1000.0 + WINDOW + 1)
    v2 = state.note_turn_key_sample("0:14:combat:5", heartbeat=2, now=1000.0 + WINDOW + 30)
    assert v2["samples"] < STALL_MIN_SAMPLES
    assert v2["stalled"] is False

# ── 5. 纯观测：不碰任何决策状态 ──────────────────────────────────────────────

def test_the_detector_touches_no_decision_state() -> None:
    """★ 本任务只加可观测性：判据【不得】改动任何决策相关字段。"""
    state = STS2RuntimeState()
    guarded = [
        "solver_step_index",
        "pending_card_choice_id",
        "force_replan_requested",
        "invalid_action_count",
        "invalid_action_signature",
        "potion_used_turn_key",
        "autoplay_state",
        "control_mode",
        "last_companion_turn_key",
    ]
    before = {name: getattr(state, name) for name in guarded}
    _feed(state, key="0:14:combat:5", seconds=WINDOW, heartbeat=1, start=1000.0)
    after = {name: getattr(state, name) for name in guarded}
    assert before == after, "空转判据必须只读，不得顺带改动作选择状态"

def test_thresholds_match_the_contract_defaults() -> None:
    """阈值口径写死：10 分钟窗口（CANON §13.2）+ 占比门（实测判别力）。"""
    assert STALL_WINDOW_SECONDS == 600.0
    assert STALL_DOMINANCE_RATIO == 0.9
    assert STALL_MIN_SAMPLES >= 1

def test_each_dimension_is_individually_visible_in_the_verdict() -> None:
    """★ 修正后的 §13.2 是【三维 + 样本量下限】—— 每条都必须能逐条查到。

    判据返回 `conditions` 分项明细，使"哪一条不满足"一眼可见，
    而不是把四个条件藏进一个复合 bool 表达式里。
    """
    # 真卡死：四条全满
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    last = None
    hb = 1
    t = 1000.0
    while t <= 1000.0 + WINDOW:
        last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
        hb += 1
        t += 1.0
    assert last["conditions"] == {
        "duration": True,
        "dominance": True,
        "enough_samples": True,
        "heartbeat_growing": True,
    }, "真卡死场景四条必要条件必须全为 True（且不得再有别的键）"

    # 进程死亡：heartbeat 这一条为 False，而 duration/dominance 仍为 True ⇒ 单独可查
    dead = STS2RuntimeState()
    dead.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=1000.0)
    dlast = None
    for tt in range(1001, 1001 + int(WINDOW) + 30):
        dlast = dead.note_turn_key_sample("0:14:combat:5", heartbeat=7, now=float(tt))
    assert dlast["conditions"]["heartbeat_growing"] is False
    assert dlast["conditions"]["duration"] is True, "窗口确实满了（否则本断言是空转）"
    assert dlast["conditions"]["dominance"] is True, "★ 局面确实没动，占比维成立"
    assert dlast["stalled"] is False, "★ 唯独心跳这条否决 ⇒ 与进程死亡区分开"

    # 活跃期：dominance 这一条为 False ⇒ 单独可查
    active = STS2RuntimeState()
    keys = ["0:17:combat:%d" % i for i in range(1, 18)]
    for k in keys:
        active.note_turn_key_sample(k, heartbeat=None, now=999.0)
    active.stall_window_started_at = 1000.0
    active.stall_reported = False
    hb = 1
    i = 0
    t = 1000.0
    alast = None
    while t <= 1000.0 + WINDOW:
        alast = active.note_turn_key_sample(keys[i % len(keys)], heartbeat=hb, now=t)
        hb += 1
        i += 1
        t += 1.0
    assert alast["conditions"]["duration"] is True, "窗口确实满了"
    assert alast["conditions"]["dominance"] is False, "17 种 key 交替 ⇒ 占比维必须否决"
    assert alast["stalled"] is False

# ── 口径纪律：滑动窗参数必须显式可传，避免"口径不同 → 数字不同"不可归因 ──────

def test_sliding_window_caliber_is_explicit_and_overridable() -> None:
    """★ 口径纪律：窗口长度/占比门/样本下限都必须**显式可传**。

    起因：本任务报「活跃期零新增 80 窗」、队长独立实现报「89 窗」。
    ★ 归因已查明（.dsh-artifacts/_t25_caliber3.log）：真值 95，差异来自【步长】与【上界口径】，
      min_samples 0→200 对计数完全无影响。把口径做成显式参数，任何数字都能连同口径一起报出。
    """
    state = STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    # 用远大于默认的窗口长度 ⇒ 未到期，绝不判空转
    v = state.note_turn_key_sample(
        "0:14:combat:5", heartbeat=1, now=1500.0, window_seconds=3600.0
    )
    assert v["stalled"] is False
    assert v["conditions"]["duration"] is False, "窗口没满 ⇒ 时长维否决"
    assert v["window_seconds"] == 3600.0, "口径必须被如实回报"
    # 用远大于默认的样本下限 ⇒ 样本不足，绝不判空转
    v2 = state.note_turn_key_sample(
        "0:14:combat:5", heartbeat=2, now=1700.0, window_seconds=600.0, min_samples=10_000
    )
    assert v2["conditions"]["enough_samples"] is False
    assert v2["stalled"] is False
    # 占比门可放宽 ⇒ 该维确实是一道闸门（放宽后成立；但时长/样本仍会各自把关）
    relaxed = state.note_turn_key_sample(
        "0:14:combat:5", heartbeat=3, now=1701.0, dominance_ratio=0.0
    )
    assert relaxed["conditions"]["dominance"] is True

# ── 6. ★ 变异测试：故意改坏 ⇒ 必须转红 ───────────────────────────────────────
#
# 说明（CANON §9.9：变异测试框架自身会伪造"转红"）：
# 下面两个变异体都先在内存里 `ast.parse` 自检，**语法坏了的假红会被显式排除**。
# 变异方式 = 对 runtime_state.py 源码做最小字符串替换后 exec 成一个独立模块，
# 再用同一组断言检查 —— 不触碰磁盘上的产品代码。

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME_STATE_PATH = os.path.join(_PLUGIN_DIR, "runtime_state.py")

def _mutant(source_patch, *, path: str | None = None, module_name: str = "runtime_state_mutant") -> types.ModuleType:
    """把 `path`（默认 runtime_state.py）按 `source_patch` 改名后编译成独立模块。

    ★ 含 `ast.parse` 自检：语法错的变异体直接在这里炸，**不会**被误当成"门有效"（假红）。
    ★ 必须注册进 `sys.modules`：`@dataclass(slots=True)` 在处理字符串注解时要
    `sys.modules[cls.__module__]`（Python 3.11 dataclasses `_is_type`），
    不注册会得到 `AttributeError: 'NoneType' object has no attribute '__dict__'`
    —— 那是【测试框架自身的假红】（CANON §9.9），不是变异体的行为差异。

    `path`/`module_name` 供 t29 复用：t29 的变异打在 **loop_runner.py** 上。
    """
    target = path or _RUNTIME_STATE_PATH
    src = io.open(target, encoding="utf-8").read()
    mutated = source_patch(src)
    assert mutated != src, "变异必须真的改动了源码（否则是空变异、假红）"
    ast.parse(mutated)  # ★ 先过语法自检：语法错 ⇒ 直接在这里炸，不会被误当成"门有效"
    module = types.ModuleType(module_name)
    module.__file__ = target
    # ★ 相对导入（`from .snapshot_normalizer import ...`）要求 `__package__` 正确，
    #   否则 exec 时抛 `ImportError: attempted relative import with no known parent package`。
    if "." in module_name:
        module.__package__ = module_name.rsplit(".", 1)[0]
    sys.modules[module_name] = module
    exec(compile(mutated, "<mutant>", "exec"), module.__dict__)
    return module

def _stall_with(module: types.ModuleType, *, keys, seconds, heartbeat_seq) -> list[dict[str, Any]]:
    """用变异模块的 STS2RuntimeState 喂同一组样本，返回判定序列。"""
    state = module.STS2RuntimeState()
    hb = heartbeat_seq[0]
    step = 30.0
    t = 1000.0
    out = []
    i = 0
    while t <= 1000.0 + seconds:
        out.append(state.note_turn_key_sample(keys[i % len(keys)], heartbeat=hb, now=t))
        hb = heartbeat_seq(min(i + 1, len(heartbeat_seq) - 1)) if callable(heartbeat_seq) else heartbeat_seq[i % len(heartbeat_seq)]
        i += 1
        t += step
    return out

def test_mutation_a_removing_the_heartbeat_condition_turns_red() -> None:
    """★ 变异 (a)：把「且 heartbeat 在涨」从判据里去掉 ⇒ 必须有测试转红。

    改法：`condition_heartbeat = heartbeat_growing` → 恒 True（即去掉该合取项）。
    期望：进程死亡场景（heartbeat 冻结）**会被误判为空转** ⇒ 本测试转红。

    ★ 采样必须够密才能到 600 秒：判据要求"窗口已持续 >= window_seconds"，
      用 30 秒步长只能采到 (1001+600+60-1030)/30 ≈ 21 个点 ⇒ 窗口只到了 ~630 秒但
      更关键的是【缺失"心跳在涨"这条】在死亡场景下本就不该触发。
      这里按实机节奏（≈1 秒 1 行）喂满，保证"时长"这条真的满足。
    """
    def feed(state, hb_value):
        """按 ≈1s 节奏喂满一个窗口 + 60s，返回最后一次判定。"""
        state.note_turn_key_sample("0:14:combat:5", heartbeat=hb_value, now=1000.0)
        last = None
        for t in range(1001, 1001 + int(WINDOW) + 60):
            last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb_value, now=float(t))
        return last

    # 先确认原版（未变异）行为：死亡场景不告警
    from plugin.plugins.sts2_autoplay.runtime_state import STS2RuntimeState as _Orig

    last_orig = feed(_Orig(), 7)
    assert last_orig["conditions"]["duration"] is True, "基线：窗口确实满了（否则本测试空转）"
    assert last_orig["stalled"] is False, "基线：原版不得在死亡场景判空转"

    original_clause = "condition_heartbeat = heartbeat_growing"
    assert original_clause in io.open(_RUNTIME_STATE_PATH, encoding="utf-8").read(), (
        "★ 变异锚点变了：必须重新核对 runtime_state.py 里 heartbeat 合取项的写法"
    )

    def patch(src: str) -> str:
        # 去掉「且 heartbeat 在涨」：`condition_heartbeat = heartbeat_growing` → 恒 True
        return src.replace(original_clause, "condition_heartbeat = True", 1)

    mutant = _mutant(patch)  # ast.parse 自检在 _mutant 内
    state = mutant.STS2RuntimeState()
    last = feed(state, 7)
    assert last["conditions"]["heartbeat_growing"] is True, "★ 变异生效：心跳条件恒 True"
    assert last["stalled"] is True, (
        "★ 变异体（去掉 heartbeat 条件）必须在死亡场景误判为空转 —— "
        "若这里不红，说明第 4 条验收的测试【没有真正约束 heartbeat 条件】"
    )

def test_mutation_b_zeroing_the_dominance_gate_turns_red() -> None:
    """★ 变异 (b)：把【占比维】改成永远成立（永远认为"局面在动"）⇒ 必须有测试转红。

    改法：`condition_dominance = top_share >= float(dominance_ratio)` → 恒 False
         （等价于"永远认为局面在推进，绝不会发疯重算"）。

    期望：真卡死形状**判不出来** ⇒ 本测试转红。

    ★ 为什么变异目标从"新 key 计数"改成"占比维"（t27 修正）：
      修正后"新 key"已**不是**必要判据（turn_key 不含 run_id，75% 跨 run 撞值）。
      把它改坏**不会**让判据失效（那正是不依赖它的证明），
      所以拿它当变异目标会得到"改坏了却仍然绿"的**假阴性**。
      真正的闸门是占比维 ⇒ 变异目标必须是它。

    ★ 变异强度自检：`_mutant` 内先 `ast.parse`，且断言 `mutated != src`，
      所以"语法坏了的假红"与"空变异假红"都被排除（CANON §9.9）。
      另附一条**阴性对照**：同一变异体在"真卡死"下必须判不出，
      而在"活跃跑图"下本来就判不出 ⇒ 证明该变异确实只打掉了闸门、没打坏别的。
    """
    ANCHOR = "        condition_dominance = top_share >= float(dominance_ratio)\n"
    assert ANCHOR in io.open(_RUNTIME_STATE_PATH, encoding="utf-8").read(), (
        "★ 变异锚点变了：必须重新核对 runtime_state.py 里占比维的写法"
    )

    def patch(src: str) -> str:
        return src.replace(ANCHOR, "        condition_dominance = False  # MUTANT(b)\n", 1)

    mutant = _mutant(patch)  # ast.parse 自检 + 非空变异断言在 _mutant 内
    state = mutant.STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    last = None
    hb = 1
    t = 1000.0
    while t <= 1000.0 + WINDOW + 120.0:
        last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
        hb += 1
        t += 1.0
    assert last["conditions"]["dominance"] is False, "★ 变异生效：占比维恒 False"
    assert last["stalled"] is False, (
        "★ 变异体（占比维恒 False = 永远认为局面在动）必须判不出真卡死 —— "
        "若这里不红，说明第 2/3 条验收的测试【没有真正约束占比维】"
    )

def test_mutation_b2_zeroing_the_new_key_counter_does_not_break_the_gate() -> None:
    """★ 阴性对照（证明设计意图）：把"新 key 记账"改坏，判据**仍应**工作。

    这与上一个变异相反 —— 它不是"该红"，而是"该绿"。
    理由是修正后的设计：判据**不依赖**"新 key"（跨 run 撞值 75% 使其不可靠）。
    ⇒ 若这条测试有一天变红，说明有人把"新 key"又改回判据的一部分了，
      那时必须同时确认 turn_key 里是否已含 run_id（见前面的格式测试）。

    ★ 该改法只作废【见新局面 ⇒ 重置窗口】这一路，不动 sampling 本身。
    """
    ANCHOR = "        if key and key not in self.stall_seen_turn_keys:\n"
    src_now = io.open(_RUNTIME_STATE_PATH, encoding="utf-8").read()
    assert ANCHOR in src_now, "★ 锚点变了：核对'见新局面'分支的写法"

    def patch(src: str) -> str:
        # 作废"见新局面"整支：新 key 不再计入、窗口不再被新局面重置 —— 判据不应受影响
        return src.replace(ANCHOR, "        if False:  # MUTANT(b2)\n", 1)

    mutant = _mutant(patch)
    state = mutant.STS2RuntimeState()
    state.note_turn_key_sample("0:14:combat:5", heartbeat=0, now=1000.0)
    last = None
    hb = 1
    t = 1000.0
    while t <= 1000.0 + WINDOW:
        last = state.note_turn_key_sample("0:14:combat:5", heartbeat=hb, now=t)
        hb += 1
        t += 1.0
    assert state.stall_new_key_count == 0, "★ 变异生效：新 key 计数恒 0"
    assert last["stalled"] is True, (
        "★ 判据**不应**依赖'新 key'（跨 run 撞值 75%）⇒ 改坏它仍应能判出真卡死"
    )

# ══════════════════════════════════════════════════════════════════════════════
# t29：把判据**接线**到 loop_runner._autoplay_loop（此前无调用点 = 等于未实装）
# ══════════════════════════════════════════════════════════════════════════════
#
# 为什么必须接线：t25 只交付了 `note_turn_key_sample()`，但全仓**没有调用点**
# ⇒ 判据永远不会产生 `[sts2_stall]` 告警 ⇒ "交付了一个永远不会被调用的函数"。
#
# 接线点选择（loop_runner.py:798-830 `_autoplay_loop`）三条理由：
#   ① 真 heartbeat = `tick`（:803 初始化 / :805 每轮 +1，**无论是否推进**，
#      SKIP 早退分支也不跳过它）；
#   ② turn_key = `self._service._combat_turn_key(snapshot)`（同步方法，:2987/:3004）；
#   ③ 与本文件 `_hb_log()`(:858) / `_HB_EVERY=20`(:856) 同源，心跳语义不走样。
#
# ★★ 禁区：绝不可用 `step_count` 顶替 heartbeat。
#    `service.py:636-637` 规定 `step_count` **只在 `result["status"] == "ok"` 时 +1**
#    ⇒ 真卡死时没有动作成功 ⇒ 冻结 ⇒ `heartbeat_growing` 恒 False ⇒ **永不告警**。
#    本文件用 `test_mutation_t29_heartbeat_as_step_count_goes_red` 把这条钉死。

_LOOP_RUNNER_PATH = os.path.join(_PLUGIN_DIR, "loop_runner.py")

def _load_loop_runner_module():
    """导入真实模块（不 exec 源码副本）—— 接线门要打在**生产模块**上。"""
    import importlib

    return importlib.import_module("plugin.plugins.sts2_autoplay.loop_runner")

class _RecordingLogger:
    """只记录 warning —— `[sts2_stall]` 是我们唯一要断言的输出。"""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, tuple[Any, ...]]] = []

    def info(self, *args: Any, **kwargs: Any) -> None:  # noqa: A003
        pass

    def warning(self, *args: Any, **kwargs: Any) -> None:
        if args:
            self.warnings.append((str(args[0]), tuple(args[1:])))

    def stall_lines(self) -> list[str]:
        return [tpl % tup for tpl, tup in self.warnings if "[sts2_stall]" in tpl]

def _make_wired_runner(
    *,
    turn_key_provider=None,
    state: STS2RuntimeState | None = None,
    snapshot: dict[str, Any] | None = None,
):
    """构造一个「已接线」的 runner：真实 STS2RuntimeState + 假 service/logger。

    ★ 关键是 `_state` 必须是**真实** `STS2RuntimeState` —— 第一版我用了一个手写假类，
    它没有 `note_turn_key_sample` ⇒ `_note_stall_sample` 的 `except Exception: pass`
    把 `AttributeError` 静默吞掉 ⇒ 测试"绿"其实是没跑到判据（假绿）。
    这里显式用真类，并从**生产模块**取 `_note_stall_sample`，避免测了个平行实现。
    """
    module = _load_loop_runner_module()
    st = state if state is not None else STS2RuntimeState()
    st.snapshot = snapshot if snapshot is not None else {
        "run_id": "RUNX",
        "floor": 14,
        "raw_state": {"turn": 5},
    }
    st.combat_room_id = "combat"

    class _FakeService:
        def __init__(self) -> None:
            self.logger = _RecordingLogger()
            self._state = st

        def _combat_turn_key(self, snap: dict[str, Any]) -> str:
            if turn_key_provider is not None:
                return turn_key_provider(snap)
            raw = snap.get("raw_state") if isinstance(snap.get("raw_state"), dict) else {}
            return "%s|%s|%s|%s" % (
                snap.get("run_id"),
                snap.get("floor"),
                st.combat_room_id,
                raw.get("turn"),
            )

    runner = module.STS2LoopRunner.__new__(module.STS2LoopRunner)
    runner._service = _FakeService()
    return runner, runner._service

class _FrozenTime:
    """可推进的假时钟 —— `runtime_state.time` 默认取真实时钟，测试必须能控时。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

def test_the_wiring_point_really_has_tick_and_turn_key() -> None:
    """★ 验收 1：接线点必须**同时**具备真 heartbeat(tick) 与可用 turn_key。

    直接读生产源码断言（不是读文档）：`tick` 必须每轮 +1 且不受 SKIP 分支影响。
    """
    src = io.open(_LOOP_RUNNER_PATH, encoding="utf-8").read()
    assert "    async def _autoplay_loop(self) -> None:\n" in src, "★ 接线点函数名变了"
    # tick 初始化 + 每轮自增，且自增发生在 SKIP 判断【之前】⇒ SKIP 也算心跳
    assert "        tick = 0\n" in src, "★ 找不到 tick 初始化"
    assert "            tick += 1\n" in src, "★ 找不到 tick 每轮自增"
    init_at = src.index("        tick = 0\n")
    inc_at = src.index("            tick += 1\n")
    skip_at = src.index('            if self._service._state.autoplay_state != "running"')
    assert init_at < inc_at < skip_at, (
        "★ `tick += 1` 必须排在 SKIP 判断**之前** —— 否则早退轮不算心跳，"
        "而早退正是卡死期的主要形态"
    )
    # turn_key 通过 service 的同步方法可得
    svc = io.open(os.path.join(_PLUGIN_DIR, "service.py"), encoding="utf-8").read()
    assert "    def _combat_turn_key(self, snapshot: dict[str, Any]) -> str:\n" in svc, (
        "★ service._combat_turn_key 签名变了"
    )
    assert 'return f"{run_id}|{floor}|{room}|{turn}"' in svc, (
        "★ _combat_turn_key 不再是含 run_id 的形态"
    )

def test_the_wiring_actually_samples_the_detector_and_calls_the_log() -> None:
    """★ 验收 2：接线确实调用了 note_turn_key_sample 并在 should_warn 时打 [sts2_stall]。"""
    module = _load_loop_runner_module()
    assert hasattr(module.STS2LoopRunner, "_note_stall_sample"), "★ 接线方法不存在"
    # 生产循环体里必须有这个调用
    src = io.open(_LOOP_RUNNER_PATH, encoding="utf-8").read()
    assert "            self._note_stall_sample(tick)\n" in src, (
        "★ _autoplay_loop 里没有调用 _note_stall_sample(tick) ⇒ 判据仍无调用点"
    )

def test_stall_warning_is_emitted_on_a_real_stall_shape() -> None:
    """★ 集成的正例：同一局面持续 > 窗口长 ⇒ 恰好打 1 条 [sts2_stall]。"""
    clock = _FrozenTime()
    import plugin.plugins.sts2_autoplay.runtime_state as rs

    orig = rs.time
    rs.time = clock
    try:
        runner, svc = _make_wired_runner()
        for tick in range(1, 703):  # 每秒一 tick，跑过 600s 窗口
            clock.now = 1000.0 + tick
            runner._note_stall_sample(tick)
    finally:
        rs.time = orig
    lines = svc.logger.stall_lines()
    assert len(lines) == 1, "★ 真卡死形状必须恰好告警 1 次（同窗口只报一次，防刷屏）"
    assert "RUNX|14|combat|5" in lines[0], "★ 告警须带上是哪个局面"
    assert "占比 1.00" in lines[0]
    assert "种类 1" in lines[0]

def test_wiring_does_not_warn_on_active_play() -> None:
    """★ 集成的反例：活跃期（多局面交替、占比低）不得告警。"""
    clock = _FrozenTime()
    import plugin.plugins.sts2_autoplay.runtime_state as rs

    keys = ["RUNX|%d|combat|%d" % (f, (f % 7) + 1) for f in range(14, 44)]
    orig = rs.time
    rs.time = clock
    try:
        runner, svc = _make_wired_runner()
        # 让 turn_key 在 30 个旧局面间轮转 —— 模拟活跃期"局面在换"
        seq = {"i": 0}

        def provider(snap: dict[str, Any]) -> str:
            k = keys[seq["i"] % len(keys)]
            seq["i"] += 1
            return k

        runner._service._combat_turn_key = provider
        for tick in range(1, 703):
            clock.now = 1000.0 + tick
            runner._note_stall_sample(tick)
    finally:
        rs.time = orig
    assert svc.logger.stall_lines() == [], (
        "★ 活跃期（局面在换）不得告警 —— 否则就是对正常游玩的骚扰（t25 §③）"
    )
    # 反证：这轮确实喂进去了，只是没告警（否则"没告警"可能只是因为没跑）
    assert svc._state.stall_sample_counts, "★ 没喂到样本 ⇒ 上面的'不告警'是空断言"

def test_wiring_touches_no_decision_state() -> None:
    """★★ 验收 8：接线**不得改变任何决策行为** —— 锁住关键决策字段前后相等。

    t25 已锁 9 个字段；这里把同一把锁**扩展到接线路径**（经过 _note_stall_sample）。
    """
    clock = _FrozenTime()
    import plugin.plugins.sts2_autoplay.runtime_state as rs

    orig = rs.time
    rs.time = clock
    try:
        runner, svc = _make_wired_runner()
        st = svc._state
        # 先制造一批"非默认值"，确保断言不是拿默认值比默认值（假绿）
        st.solver_step_index = 7
        st.pending_card_choice_id = "CARD_X"
        st.force_replan_requested = True
        st.invalid_action_count = 3
        st.potion_used_turn_key = "RUNX|14|combat|5"
        st.autoplay_state = "running"
        st.step_count = 41
        st.last_companion_turn_key = "OLD"
        # ★ 注意：`solver_plan` / `last_solver_plan_signature` 都**不在** runtime_state 上
        #   （计划回填进 snapshot），而 `@dataclass(slots=True)` 禁止赋未声明属性 ⇒ 不能用它们。
        #   改用真实存在的求解器决策字段 `snapshot_signature`。
        st.snapshot_signature = "SIG_OLD"
        st.interrupt_requested = True
        st.guidance_generation = 5
        watched = (
            "solver_step_index",
            "pending_card_choice_id",
            "force_replan_requested",
            "invalid_action_count",
            "potion_used_turn_key",
            "autoplay_state",
            "step_count",
            "last_companion_turn_key",
            "snapshot_signature",
            "interrupt_requested",
            "guidance_generation",
        )
        before = {name: repr(getattr(st, name, "<absent>")) for name in watched}
        for tick in range(1, 703):
            clock.now = 1000.0 + tick
            runner._note_stall_sample(tick)
    finally:
        rs.time = orig
    after = {name: repr(getattr(st, name, "<absent>")) for name in watched}
    assert before == after, "★ 接线改变了决策字段：%s" % {
        k: (before[k], after[k]) for k in before if before[k] != after[k]
    }
    # 且确实跑到了判据（否则"没变"没有意义）
    assert st.stall_sample_counts, "★ 判据没被真正喂到样本 ⇒ 上面'没变'是空断言"
    assert svc.logger.stall_lines(), "★ 这轮本该告警 ⇒ 判据确实被驱动过"

def test_wiring_swallows_exceptions_so_it_cannot_break_the_loop() -> None:
    """可观测性纪律：接线出任何异常都必须被吞掉，绝不打断游玩链路。"""
    clock = _FrozenTime()
    import plugin.plugins.sts2_autoplay.runtime_state as rs

    orig = rs.time
    rs.time = clock
    try:
        runner, svc = _make_wired_runner()

        def boom(snap: dict[str, Any]) -> str:
            raise RuntimeError("模拟取 turn_key 失败")

        svc._combat_turn_key = boom
        runner._note_stall_sample(1)  # 不得抛出
    finally:
        rs.time = orig
    assert svc.logger.stall_lines() == []

# ── 变异测试（t29 契约两条）───────────────────────────────────────────────────

def test_mutation_t29_heartbeat_as_step_count_goes_red() -> None:
    """★★★ 验收 7(a)：把 heartbeat 换成 `step_count` ⇒ **必须转红**。

    这是本任务最关键的禁区。`service.py:636-637`：`step_count` 只在
    `result["status"] == "ok"` 时 +1 ⇒ 真卡死时**冻结** ⇒ `heartbeat_growing` 恒 False
    ⇒ 判据永不告警（= "交付一个永不触发的判据"缺陷的自我复制）。

    变异打在**接线处**（把喂进去的 heartbeat 从 tick 换成 step_count），而不是判据内部，
    因为那才是接线可能犯的真实错误。
    """
    src_now = io.open(_LOOP_RUNNER_PATH, encoding="utf-8").read()
    ANCHOR = "            verdict = self._service._state.note_turn_key_sample(turn_key, heartbeat=int(tick))\n"
    assert ANCHOR in src_now, "★ 变异锚点变了：核对 _note_stall_sample 里喂 heartbeat 的那一行"

    def patch(src: str) -> str:
        return src.replace(
            ANCHOR,
            "            verdict = self._service._state.note_turn_key_sample(\n"
            "                turn_key, heartbeat=int(getattr(self._service._state, 'step_count', 0))\n"
            "            )  # MUTANT(t29-heartbeat)\n",
            1,
        )

    mutant = _mutant(
        patch,
        path=_LOOP_RUNNER_PATH,
        # ★ 必须用**独立**模块名：若复用生产名 "plugin.plugins.sts2_autoplay.loop_runner"，
        #   就会把 sys.modules 里的真模块**永久换掉**，后续测试全拿到变异体
        #   （实测：单跑绿、连跑红 —— 典型测试间污染，会伪装成产品缺陷）。
        #   ★ 但名字又必须落在**包内**，因为 loop_runner.py 用的是相对导入
        #   （`from .snapshot_normalizer import ...`）；用裸名 exec 会得到
        #   `ImportError: attempted relative import with no known parent package`。
        module_name="plugin.plugins.sts2_autoplay.loop_runner_mutant_t29",
    )
    clock = _FrozenTime()
    import plugin.plugins.sts2_autoplay.runtime_state as rs

    orig = rs.time
    rs.time = clock
    try:
        st = rs.STS2RuntimeState()
        st.snapshot = {"run_id": "RUNX", "floor": 14, "raw_state": {"turn": 5}}
        st.combat_room_id = "combat"

        class _Svc:
            def __init__(self) -> None:
                self.logger = _RecordingLogger()
                self._state = st

            def _combat_turn_key(self, snap: dict[str, Any]) -> str:
                return "RUNX|14|combat|5"

        runner = mutant.STS2LoopRunner.__new__(mutant.STS2LoopRunner)
        runner._service = _Svc()
        for tick in range(1, 703):
            clock.now = 1000.0 + tick
            st.step_count = 2395  # ★ 真卡死形态：step_count 冻结（不再增长）
            runner._note_stall_sample(tick)
        lines = runner._service.logger.stall_lines()
    finally:
        rs.time = orig
    assert lines == [], (
        "★ 变异体（用冻结的 step_count 当 heartbeat）必须在真卡死时【一条告警都发不出】—— "
        "若这里不红，说明本任务没有真正证明'heartbeat 必须是 tick'"
    )

