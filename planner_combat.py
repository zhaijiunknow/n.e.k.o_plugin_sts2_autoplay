from __future__ import annotations

import re
from typing import Any

from .planner_common import (
    _POTION_CATEGORY_LABELS,
    COMBAT_POTION_HP_LOSS_THRESHOLD,
    COMBAT_POTION_LETHAL_HEAL_SCORE,
    COMBAT_POTION_LOW_HP_HEAL_SCORE,
    COMBAT_POTION_LOW_HP_RATIO,
    COMBAT_POTION_SCORES,
    COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER,
)
from .planner_interface import PlannedOperation


class PlannerCombatMixin:
    """Internal responsibility mixin; initialized and composed by STS2HeuristicPlanner."""

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
        # ★★★ t46：求解器当前状态 + 已等秒数（由 loop_runner 的 _planner_context 透传）。
        #   等待本身不计死锁是**有意**的；但"在算"与"请求失败"必须能一眼分开。
        solver_status = str(context.get("solver_status") or "")
        solver_wait_seconds = 0.0
        try:
            solver_wait_seconds = float(context.get("solver_wait_seconds") or 0.0)
        except (TypeError, ValueError):
            solver_wait_seconds = 0.0
        solver_last_error = str(context.get("solver_last_error") or "")
        self._solver_gate_last = dict(gate)
        self._solver_gate_last_verdict = verdict

        if verdict == "solver_cannot_act":
            self._solver_gate_cannot_act_count += 1
            self._log_solver_gate(
                gate=gate,
                plan_origin=plan_origin,
                force=True,
                solver_status=solver_status,
                solver_wait_seconds=solver_wait_seconds,
                solver_last_error=solver_last_error,
            )
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
        self._log_solver_gate(
            gate=gate,
            plan_origin=plan_origin,
            force=False,
            solver_status=solver_status,
            solver_wait_seconds=solver_wait_seconds,
            solver_last_error=solver_last_error,
        )
        return None

    def _log_solver_gate(
        self,
        *,
        gate: dict[str, Any],
        plan_origin: str,
        force: bool,
        solver_status: str = "",
        solver_wait_seconds: float = 0.0,
        solver_last_error: str = "",
    ) -> None:
        """闸门留痕：一行说清「在等什么 / 三个计数各是多少 / 局面与计划是否对得上」。

        ★ 为什么必须留痕（与 t13/t23/t37 同一条纪律）：静默的等待与"插件卡死"在日志里
          无法区分 —— 这正是本项目历史上最贵的几类缺陷的共同形态。
        ★ 为什么带累计计数：本次验收要求报出「被 gate 拦下的 tick 数」，靠数日志行会漏
          （节流/去重/上限都会吃掉行）⇒ 直接把累计值写进行内，**数字自证**。
        ★★ t46：行尾补「求解器 <状态> 已等=Ns」。`/solver/plan` 不设读超时后，等待可能长达
          几分钟，这一列就是"现在到底在等谁"的答案；`failed` 与 `computing` 都在 key 里，
          ⇒ 状态一变立刻打一行，不会被 5 次节流吃掉。
        """
        solver_status = str(solver_status or "")
        key = "|".join((
            str(gate.get("verdict") or ""),
            str(gate.get("missing") or ""),
            str(gate.get("search_status") or ""),
            plan_origin,
            solver_status,
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
                "｜死锁计数 deadlock=%d/%d stale_reclaim=%d/%d（等待不计入）"
                "｜求解器 %s 已等=%.1fs%s",
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
                solver_status or "-",
                solver_wait_seconds,
                ("（最近一次失败：%s）" % solver_last_error) if solver_last_error else "",
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
