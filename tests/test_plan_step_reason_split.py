# -*- coding: utf-8 -*-
"""t43 —— 把「计划这一步打不出去」的原因拆成可区分的两类。

## 缺陷（队长 2026-10-20 实测）

`[sts2_plan]` 当天重算 **4548** 次，按原因分布：

    回合或敌人组合变了（正常）        1766   38.8%
    ★ 手牌里已没有可打出的 X         1681   37.0%   ← 这一句把两种成因压在一起
    现在不能结束回合                  665   14.6%
    快照步骤已用尽                    343    7.5%
    药水不可用                        ~40

漂移 Top 全是**最基础的卡**：`DEFEND_IRONCLAD` 221 / `STRIKE_IRONCLAD` 206 /
`SHRUG_IT_OFF` 85 / `BASH` 82 —— 最基础的打击/防御"手牌里没有"本身就反常。

**两种成因在代码里是同一条路径**（`card_id` 精确相等 **且** `playable` 为真）：

    loop_runner.py::_plan_step_is_executable
    heuristic_planner.py::_resolve_solver_card_index

    card = next((it for it in hand
                 if it["card_id"] == cid and it["playable"]), None)
    if card is None:
        → 「手牌里已没有可打出的 X」

- ① 卡**真的不在手牌**（已打出/弃掉/消耗）⇒ 计划过期，重算**正确**
- ② 卡**在手牌里但 `playable=False`**（能量不够等）⇒ 计划没错，只是**这一步暂不可执行**

② 若占多，说明**模拟器的能量模型与实机不一致** —— 那才是要修的东西。
所以在拆开之前，这 1681 次**无法归因**，本文件就是把它钉住。

## 契约
1. 两个 site 都必须能区分 ①/②，且 reason **可 grep 区分**。
2. ② 必须带能量实况（`energy_cost` / 当前能量）；拿不到写 `?`，**不许编数字**。
3. 原因文本由**同一实现**生成（`loop_runner._explain_hand_card_state`），
   不允许两处各写一份 —— 这是 `_combat_deadlock_signature` 当初被提取的同一条理由。
4. `[sts2_combat_follow]` 的成功行格式**原样不变**（跨 mod 对照器依赖）。
"""
from __future__ import annotations

import re
import unittest

from plugin.plugins.sts2_autoplay.heuristic_planner import STS2HeuristicPlanner
from plugin.plugins.sts2_autoplay.loop_runner import (
    _explain_hand_card_state,
    _plan_step_is_executable,
)


def _snapshot(hand, energy=3, actions=None):
    return {
        "raw_state": {"combat": {"hand": hand, "player": {"energy": energy}}},
        "available_actions": actions or [],
    }


def _card(card_id, playable=True, index=0, energy_cost=2, star_cost=0, costs_x=False):
    return {
        "index": index,
        "card_id": card_id,
        "playable": playable,
        "energy_cost": energy_cost,
        "star_cost": star_cost,
        "costs_x": costs_x,
    }


class PlanStepReasonSplitTest(unittest.TestCase):
    """两个 site 的 ①/② 区分 + 共享实现 + 格式不变。"""

    # ── ① 卡真的不在手牌 ───────────────────────────────────────────────
    def test_loop_runner_absent_card_reason_says_not_in_hand(self) -> None:
        step = {"kind": "play_card", "card_id": "STRIKE_IRONCLAD"}
        ok, why = _plan_step_is_executable(step, _snapshot([_card("BASH")]))
        self.assertFalse(ok)
        self.assertIn("不在手牌", why)
        self.assertIn("STRIKE_IRONCLAD", why)
        # ★ 正向对照：不能把 ② 的话术用在 ① 上
        self.assertNotIn("playable=false", why)

    # ── ② 卡在手牌里但 playable=False ──────────────────────────────────
    def test_loop_runner_unplayable_card_reason_says_in_hand_but_not_playable(self) -> None:
        step = {"kind": "play_card", "card_id": "BASH"}
        snap = _snapshot([_card("BASH", playable=False, energy_cost=2)], energy=0)
        ok, why = _plan_step_is_executable(step, snap)
        self.assertFalse(ok)
        self.assertIn("不可打出", why)
        self.assertIn("playable=false", why)
        # ★ 必须带能量实况，且数字要来自输入（energy_cost=2 / 当前能量=0）
        self.assertIn("energy_cost=2", why)
        self.assertIn("当前能量=0", why)
        # ★ 正向对照：不能把 ① 的话术用在 ② 上
        self.assertNotIn("不在手牌", why)

    def test_index_branch_unplayable_also_carries_energy(self) -> None:
        step = {"kind": "play_card", "card_index": 0}
        snap = _snapshot([_card("BASH", playable=False, energy_cost=3)], energy=1)
        ok, why = _plan_step_is_executable(step, snap)
        self.assertFalse(ok)
        self.assertIn("不可打出", why)
        self.assertIn("energy_cost=3", why)
        self.assertIn("当前能量=1", why)

    # ── 阳性对照：可打出的卡必须放行，且不产生 reason ───────────────────
    def test_playable_card_passes_with_empty_reason(self) -> None:
        step = {"kind": "play_card", "card_id": "BASH"}
        ok, why = _plan_step_is_executable(step, _snapshot([_card("BASH")]))
        self.assertTrue(ok)
        self.assertEqual(why, "")

        planner = STS2HeuristicPlanner()
        index, reason = planner._resolve_solver_card_index(
            _snapshot([_card("BASH", index=2)]), "BASH", None
        )
        self.assertEqual(index, 0)  # 返回枚举位置，不是卡自带的 index 字段
        self.assertEqual(reason, "")

    # ── planner 侧：与 loop_runner 同口径 ──────────────────────────────
    def test_planner_side_distinguishes_both_causes(self) -> None:
        planner = STS2HeuristicPlanner()

        absent_idx, absent_reason = planner._resolve_solver_card_index(
            _snapshot([_card("BASH")]), "STRIKE_IRONCLAD", None
        )
        self.assertIsNone(absent_idx)
        self.assertIn("不在手牌", absent_reason)

        unplay_idx, unplay_reason = planner._resolve_solver_card_index(
            _snapshot([_card("BASH", playable=False, energy_cost=2)], energy=0),
            "BASH",
            None,
        )
        self.assertIsNone(unplay_idx)
        self.assertIn("不可打出", unplay_reason)
        self.assertIn("当前能量=0", unplay_reason)

        # ★ 两条 reason 必须**不同** —— 这正是本任务的唯一目的
        self.assertNotEqual(absent_reason, unplay_reason)

    # ── 共享实现：两边对同一输入必须给出**逐字相同**的文本 ──────────────
    def test_both_sites_use_one_shared_implementation(self) -> None:
        hand = [_card("BASH", playable=False, index=1, energy_cost=2)]
        player = {"energy": 0}
        shared = _explain_hand_card_state("BASH", hand, player)

        planner = STS2HeuristicPlanner()
        _, planner_reason = planner._resolve_solver_card_index(
            _snapshot(hand, energy=0), "BASH", None
        )
        self.assertEqual(planner_reason, shared)

        _, loop_reason = _plan_step_is_executable(
            {"kind": "play_card", "card_id": "BASH"}, _snapshot(hand, energy=0)
        )
        self.assertEqual(loop_reason, shared)

    # ── 拿不到能量时不许编数字 ─────────────────────────────────────────
    def test_missing_energy_is_question_mark_not_invented_number(self) -> None:
        hand = [_card("BASH", playable=False)]
        why = _explain_hand_card_state("BASH", hand, {})  # player 里没有 energy
        self.assertIn("当前能量=?", why)
        # 不允许出现任何伪造的具体能量值
        self.assertNotRegex(why, r"当前能量=\d")

    # ── 被消费的格式不得改变 ───────────────────────────────────────────
    def test_combat_follow_success_line_format_unchanged(self) -> None:
        """`[sts2_combat_follow]` 成功行格式是跨 mod 对照器的输入，必须原样。

        形态（实测样例）：
            求解器=play_card/STRIKE_IRONCLAD(target=0) -> 执行=play_card(card_index=1,target_index=0) 一致=是 手牌=['打击', ...]
        ★ 细节：成功行 `执行=` 后面**没有外层括号**（直接跟 kwargs 的 `(`）；
          失败行才有 `执行=(中文说明)` 的外层括号。对照器依赖这个区别。
        ★ t44 之后行尾多了 ` turn=N run=X`，但它追加在 `手牌=` **之后**，
          所以下面这套正则（锚 `一致=是|否`）**逐字不受影响**。
        """
        success_line = (
            "求解器=play_card/STRIKE_IRONCLAD(target=0) -> "
            "执行=play_card(card_index=1,target_index=0) 一致=是 手牌=['打击', '防御'] "
            "turn=4 run=U94BBX05GV0P"
        )
        failure_line = (
            "求解器=play_card/BASH(target=0) -> 执行=(不发动作，等玩家窗口) 一致=否 "
            "turn=4 run=U94BBX05GV0P"
        )
        pat = re.compile(
            r"求解器=(?P<kind>\S+?)/(?P<card>\S*?)\(target=(?P<t>[^)]*)\)"
            r"\s*->\s*执行=(?P<exec>.+?)\s+一致=(?P<mt>\S+)"
        )
        m = pat.search(success_line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("card"), "STRIKE_IRONCLAD")
        self.assertTrue(m.group("exec").startswith("play_card("))
        self.assertFalse(m.group("exec").startswith("("))  # 成功行无外层括号
        self.assertEqual(m.group("mt"), "是")

        m2 = pat.search(failure_line)
        self.assertIsNotNone(m2)
        self.assertTrue(m2.group("exec").startswith("("))  # 失败行有外层括号
        self.assertEqual(m2.group("mt"), "否")

    def test_combat_follow_line_carries_turn_and_run(self) -> None:
        """★ t44：`[sts2_combat_follow]` 必须带 `turn=` 与 `run=`，供按回合对齐。

        为什么：跨 mod 对照的正确单位是**回合**（上游路线覆盖 turn T..T+8，
        只有 turn T 的开头被搜索窗口污染）。没有 turn 就只能靠 `turn_key` 反推，
        而它在纯 autoplay 下会漏 ⇒ 回合被合并。且 `turn_key` 跨 run 会撞值
        （`loop_runner.py:862` 记录：实测 132/175=75%），所以**必须同时带 run**。
        """
        captured: list[str] = []

        class _Cap:
            def info(self, message, *args):
                try:
                    captured.append(message % args if args else message)
                except Exception:
                    captured.append(message)

            def warning(self, *a, **k):
                pass

            def debug(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

        planner = STS2HeuristicPlanner(logger=_Cap())
        snapshot = {
            "raw_state": {
                "run_id": "U94BBX05GV0P",
                "combat": {"turn": 7, "hand": [_card("BASH")], "player": {"energy": 3}},
            }
        }
        planner._combat_log_solver_vs_executed(
            solver_kind="play_card",
            solver_card_id="BASH",
            solver_target=None,
            executed_action="play_card",
            executed_kwargs={"card_index": 0},
            matched=True,
            hand=[_card("BASH")],
            snapshot=snapshot,
        )
        self.assertTrue(captured, "没有捕获到任何留痕行")
        line = captured[-1]
        self.assertIn("turn=7", line)
        self.assertIn("run=U94BBX05GV0P", line)
        # 旧字段仍在，且新字段在【手牌=... 之后】
        self.assertIn("一致=是", line)
        self.assertLess(line.index("手牌="), line.index("turn="))

        # 取不到时写 `?`，不编造
        planner2 = STS2HeuristicPlanner(logger=_Cap())
        captured.clear()
        planner2._combat_log_solver_vs_executed(
            solver_kind="play_card",
            solver_card_id="BASH",
            solver_target=None,
            executed_action="play_card",
            executed_kwargs={"card_index": 0},
            matched=True,
            hand=[_card("BASH")],
            snapshot={"raw_state": {"combat": {"hand": [_card("BASH")]}}},
        )
        self.assertIn("turn=?", captured[-1])
        self.assertIn("run=?", captured[-1])

    # ── 反向门：旧文案不得再出现在「在手但不可打」这条路径上 ─────────────
    def test_old_ambiguous_wording_is_gone(self) -> None:
        ok, why = _plan_step_is_executable(
            {"kind": "play_card", "card_id": "BASH"},
            _snapshot([_card("BASH", playable=False)], energy=0),
        )
        self.assertFalse(ok)
        self.assertNotIn("手牌里已没有可打出的", why)


if __name__ == "__main__":
    unittest.main()
