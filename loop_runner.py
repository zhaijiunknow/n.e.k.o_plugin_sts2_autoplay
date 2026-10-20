from __future__ import annotations

import asyncio
import hashlib
import threading
from concurrent.futures import Future
from time import time
from typing import Any

from .decision_payload import DecisionPayload
from .instructions import instruction_summary, normalize_strategy_instruction
from .snapshot_normalizer import normalize_snapshot


class STS2LoopRunner:
    # Mod /events/stream event types that mark a real state change worth re-fetching /state for.
    _RELEVANT_EVENT_TYPES = frozenset({
        "screen_changed", "available_actions_changed", "combat_started", "combat_ended",
        "combat_turn_changed", "player_action_window_opened", "player_action_window_closed",
        "route_decision_required", "reward_decision_required", "event_state_changed", "session_started",
    })
    # 猫娘战斗弹幕只响应这三个战斗事件；仅 combat_turn_changed（回合推进）带 out 牌建议 line，
    # 其余两个（combat_started / available_actions_changed）只报基础。见 _build_combat_prompt。
    _COMBAT_DANMAKU_EVENTS = frozenset({"combat_started", "available_actions_changed", "combat_turn_changed"})
    # Coalesce a burst of SSE events into at most one /state fetch per this window.
    _EVENT_FETCH_MIN_INTERVAL_SECONDS = 0.5

    def __init__(self, service: Any) -> None:
        self._service = service
        self._poll_task: asyncio.Task[Any] | Future[Any] | None = None
        self._autoplay_task: asyncio.Task[Any] | Future[Any] | None = None
        self._shutdown = False
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._owner_thread: threading.Thread | None = None
        self._owner_thread_ready = threading.Event()
        # Cache the mod /solver/plan against the combat-state signature so the mod's search is not
        # recomputed on every tick while the combat position is unchanged. See _combat_plan_signature.
        self._solver_plan_sig: Any = None
        self._solver_plan_cached: dict[str, Any] | None = None
        # ★ t11：**取回** `_solver_plan_cached` 那一刻的局面指纹（见 tick 里 `plan_fetch_signature`）。
        #   随缓存计划一起存活；planner 用它区分「取回后局面已变（漂移 ⇒ 归 A）」与
        #   「取回后局面没变但首步仍做不了（真无解 ⇒ 归 B）」。
        self._solver_plan_fetch_signature = ""
        # 计划复用/重算的日志去重键（避免每 tick 刷屏）。
        self._plan_log_key = ""
        # 最近一次 SSE 事件类型（combat_started / player_action_window_opened 等）。随
        # /events/stream 更新，供 catgirl 战斗 prompt 判断"该不该带 line"：仅轮到玩家行动的
        # player_action_window_opened 才带，其余战斗事件只报基础（怪物血/我方血/层级）。
        self._current_event_type: str = ""
        # Scene-change event streaming (mod GET /events/stream): drive a refresh on a real change
        # instead of polling /state on a timer. See _event_loop / _RELEVANT_EVENT_TYPES.
        self._event_task: asyncio.Task[Any] | Future[Any] | None = None
        self._last_event_refresh = 0.0
        self._event_backoff = 1.0
        self._sse_connected = False
        # ★★ t14/D4（批 1）看门狗状态：判断"业务进度"是否停滞（只看 step_count，不看连接/心跳）。
        self._fallback_last_step_count = -1
        self._fallback_last_progress_at = 0.0
        self._fallback_refresh_count = 0
        # ★★ t14/D4（批 2）业务事件静默检测的起算时刻（0.0 = 已收到过业务事件）。
        self._business_silence_since = 0.0

    async def tick(self) -> dict[str, Any]:
        client = self._service._require_client()
        # ★★ D10（t26）修复：`/state` 失败时**降级**到 `/actions/available`，而不是中止整个 tick。
        #
        # 缺陷（实机，2026-10-19 23:55:51–23:59:36，35 条连续错误行）：
        #   游戏停在主菜单（非对局状态）时 mod 的 `/state` 返回 HTTP 500
        #   （`last_error='Unhandled server error. (code=internal_error)'`），
        #   而 `/actions/available` 同刻 HTTP 200 且列出 `continue_run` 等动作。
        #   修复前这里 `await client.get_state()` 直接抛 ⇒ **整个 tick 中止**
        #   ⇒ 下面那行 `/actions/available` 根本没被调用
        #   ⇒ `available_action_count` 恒 0（实测该窗口 {'0': 31, '4': 4}）
        #   ⇒ 插件把"状态查询失败"误判成"无事可做" ⇒ 从不尝试 `continue_run`
        #   ⇒ 每次重启永久卡死，8 秒一轮（= `_autoplay_retry_backoff` 封顶值）。
        #
        # 判据：`/state` 与 `/actions/available` 是**两个独立端点**，前者的失败
        #   **不蕴含**"没有动作可做"。只有后者返回空动作集才是"无事可做"。
        #   ⇒ 必须把"状态查询失败"与"语义上无动作"分开（同族：D1/D9）。
        #
        # ★ 不得静默：降级发生时打一条可观测日志（下面 `[sts2_d10]`），
        #   让"插件没动"与"状态取不到"在日志里可区分。
        raw_state: dict[str, Any] = {}
        state_error: Exception | None = None
        try:
            raw_state = await client.get_state()
        except Exception as exc:  # noqa: BLE001 — 任何 /state 失败都降级，不区分异常类型
            state_error = exc
        raw_actions = await client.get_available_actions()
        snapshot = normalize_snapshot(raw_state, raw_actions)
        if snapshot.get("state_unavailable"):
            # ★ 可观测降级：绝不静默。附动作数，直接暴露"状态挂了但动作在"这一决定性事实。
            self._service.logger.warning(
                "[sts2_d10] /state 不可用 ⇒ 降级用 /actions/available 的动作集 "
                "（state_error=%s；actions=%d；actions_preview=%s；screen=%s）",
                type(state_error).__name__ if state_error is not None else "empty_payload",
                snapshot.get("available_action_count"),
                [
                    a.get("type")
                    for a in (snapshot.get("available_actions") or [])[:6]
                    if isinstance(a, dict)
                ],
                snapshot.get("screen"),
            )
            if state_error is not None:
                # 让健康信息如实反映"状态查询失败"（既不掩盖、也不中止循环）：
                # 我们**继续**用动作集干活，健康字段仍标 error 供观测。
                self._service._mark_loop_error(state_error)
        action_registry = self._service._action_registry.build(snapshot)
        classification = self._service._state_machine.classify(snapshot)
        snapshot_with_context = {**snapshot, "classification": classification, "polled_at": time()}
        snapshot_summary = self._service._summary_builder.build_snapshot_summary(snapshot_with_context)
        previous_summary = self._service._state.latest_snapshot_summary
        continuous_delta = self._service._summary_engine.compute_delta(
            previous_summary if isinstance(previous_summary, dict) else None,
            snapshot_summary,
            source="continuous_snapshot",
        )
        self._service._state.latest_snapshot_summary = dict(snapshot_summary)
        self._service._state.remember_continuous_delta(continuous_delta)
        summary_context = self._service._summary_builder.build(
            snapshot_with_context,
            runtime_state=self._service._state,
        )
        strategy_context = self._service._strategy_repository.build_context(
            {**snapshot, "classification": classification, "summary_context": summary_context}
        )
        tactical_signals = self._service._summary_builder.build_tactical_signals(
            snapshot_with_context,
            summary_context,
            strategy_context,
        )
        mode_info = self._service._mode_controller.describe(self._service._state.control_mode)
        # Combat recommendation source: the mod's authoritative /solver/plan (vendored CombatSolver).
        # plan() is sync, so fetch here (async tick) and inject into the contexts it reads. A stale
        # search or no-in-combat payload falls back to the heuristic combat path inside the planner.
        # Cache by combat-state signature: the mod re-runs the CombatSolver search on EVERY /solver/plan
        # call, so reuse the plan while the combat state (hand/energy/enemies/potions) is unchanged.
        solver_plan = None
        # ★★ `plan_origin`（D1 修复）：本 tick 交给 planner 的计划是**复用缓存**还是**刚重取**。
        #
        # 为什么必须有这个字段：planner 的「死锁保险」要区分两种病，而它们在**局面**上
        # 逐位不可区分（只读探针实测：两者指纹相同、`_plan_step_is_executable` 返回相同、
        # 连续 3 次调用轨迹完全重合 —— 见 `.dsh-artifacts/req/R1-D1-CONTRACT.md` §0）：
        #   (A) 计划过期：缓存里的牌已不在手（搜索 ~1s 期间手牌变了）—— 运维常态，
        #       上游等价动作是 `SolverController.cs:3190-3202` 的 `RequestSearch(DeploymentDrift)`，
        #       **同回合重搜、不烧回合**。
        #   (B) 真无解：**看着最新局面重取回来**的计划，首步**仍然**做不了 —— 才该计数兜底。
        # 唯一客观差异就是「这张计划是不是本 tick 刚取回来的」⇒ 只能在**真实分支**处记录，
        # 由 planner 读。取值：
        #   "reused"    ⇐ 下面 `cached is not None` 分支（缓存复用）
        #   "refetched" ⇐ 下面真的调了 `get_combat_plan()` 且 `in_combat` 为真
        #   "none"      ⇐ 不在战斗决策态 / 取计划失败 / 未进战斗
        plan_origin = "none"
        # ★★ `plan_fetch_signature`（t11 修复）：**取回计划那一瞬间**的局面指纹。
        #
        # 为什么 `plan_origin` 单独不够（t5 复核发现、队长独立复核证实）：
        #   `plan_origin == "refetched"` 只能表达「本 tick 取过计划」，
        #   **表达不了「取回的计划与取回瞬间的局面是否一致」**。
        #   求解器搜索耗时约 1s，这期间手牌可能已经变了 ⇒ 重取回来的计划**首步就已经漂移**。
        #   实机实测：`refetched` + 手牌漂移 13 条 vs `refetched` + 真无解（药水缺失）2 条
        #   ⇒ 「refetched + 首步不可执行」**主要就是漂移**，把它判成 B 是误报，
        #   会让计数器涨到 3/3（实测 08:45:01 已达 3/3）⇒ 白烧回合。
        #   ⇒ 判 B 必须再加一条：**取回时的指纹与当前指纹相同**（即"看着最新局面取的计划
        #     现在仍然对得上，但首步还是做不了"才是真无解）。
        plan_fetch_signature = ""
        in_combat_decision = classification.get("state_name") == "combat" and mode_info.get("allows_planner")
        step_index = self._service._state.solver_step_index if isinstance(self._service._state.solver_step_index, int) else 0
        # ★★ D8 判别（t17，仅日志）：进入决策块的**真实**取值。
        #   与下面的 plan-call / plan-return 三行配对，即可彻底切开"没进块 / 进了块但返回 None /
        #   返回了操作但下游丢了"三种情形。此前两次归因错误都源于用外部重建的输入代替运行时值。
        self._hb_log(
            "tick-gate in_combat_decision=%s state_name=%s screen=%s allows_planner=%s control_mode=%s "
            "avail_count=%s solver_step_index=%s"
            % (
                in_combat_decision,
                (classification or {}).get("state_name"),
                snapshot.get("screen"),
                bool(mode_info.get("allows_planner")),
                self._service._state.control_mode,
                snapshot.get("available_action_count"),
                step_index,
            ),
        )
        if in_combat_decision:
            # ── 计划快照语义（玩家口径）──────────────────────────────────────────────
            # "算一次、做成快照、直接按快照执行；每次行动后用当前局面和快照对比，
            #   一致就继续，不一致就重新算。"
            #
            # mod 的 /solver/plan **每次调用都会重跑一遍 CombatSolver 搜索**（Short 预算是 8 秒），
            # 所以能不重算就别重算。复用快照的唯一条件是：**快照里下一步在当前局面下仍然可执行**。
            # 单看"签名没变"不够 —— 抽牌/弃牌会让手牌漂移，步进下标就会指到别的牌上。
            sig = _combat_plan_signature(snapshot)
            cached, reason = _reusable_plan(
                self._solver_plan_cached,
                self._solver_plan_sig,
                sig,
                step_index,
                snapshot,
            )
            if cached is not None:
                solver_plan = cached
                plan_origin = "reused"
                # ★ t11：**沿用这份计划被取回时**记下的指纹，不要重新采当前局面。
                #   语义：一份计划的"新鲜度基准"是它被取回那一刻的局面。
                #   若这里改成采当前指纹，"取回后局面变了"这个事实会被抹掉 ⇒
                #   planner 永远看到"未变" ⇒ refetched+漂移 的误报原样回来（t11 白修）。
                #   （该缺陷已被 test_reused_tick_keeps_the_original_fetch_signature 抓到：
                #     第一版此处漏了这一行，reused tick 传的是空串。）
                plan_fetch_signature = self._solver_plan_fetch_signature
                self._log_plan_reuse(step_index, len(_current_turn_steps(cached) or []))
            if solver_plan is None:
                try:
                    fetched = await client.get_combat_plan()
                except Exception:
                    fetched = None
                if fetched and fetched.get("in_combat"):
                    # Only cache an actionable plan; otherwise leave it empty so the next tick retries.
                    solver_plan = fetched
                    plan_origin = "refetched"
                    # ★ 记下**取回这一刻**的局面指纹（t11）。下一 tick 若 `plan_origin=="reused"`，
                    #   这份指纹会随缓存计划一起被复用（它在下面 `cached is not None` 分支里
                    #   继续沿用上一次取回时的值）——这正是我们要的语义：
                    #   「这份计划的"新鲜度基准"是它被取回时的局面」。
                    plan_fetch_signature = _combat_deadlock_signature(snapshot)
                    self._solver_plan_fetch_signature = plan_fetch_signature
                    self._solver_plan_sig = sig
                    self._solver_plan_cached = fetched
                    self._service._state.solver_step_index = 0
                    self._log_plan_refresh(reason or "首次取计划")
                else:
                    solver_plan = None
                    plan_origin = "none"
        else:
            # Not in a combat decision phase: drop the cache so re-entering combat refetches.
            # 不清空 solver_step_index：中途可能出现选牌(combat_hand_select)等非决策态。
            self._solver_plan_sig = None
            self._solver_plan_cached = None
            self._solver_plan_fetch_signature = ""
        # ★★ 把求解器计划回填进 snapshot —— 让**所有**消费者读同一份。
        #
        # 起因：`summary_context` 在 :70 就构造完了，而 solver_plan 到 :88 才取。
        # 于是 `summary_context["payload"]` 里**从来没有** solver_plan，
        # `companion_evaluator._combat_solver_suggestion` 会永远走"无计划"分支
        # ⇒ 陪玩模式的**出牌建议弹幕永远不出现**（玩家看不到该打哪张牌）。
        #
        # ★ 为什么回填 snapshot 而不是重排顺序：
        #   取计划需要 `classification`（:59）与 `mode_info`（:82）；
        #   而 summary_context（:70）夹在中间，且它的其它消费者（strategy_context、
        #   tactical_signals）都依赖它先就位。
        #   回填是**一处改动、零重排风险**，且语义正确：计划本就是当前局面的属性。
        #
        # ★ 必须同时更新已构造的两份派生数据（这是最容易漏的一步）：
        #   `snapshot_with_context` 被 `summary_context` 捕获过；
        #   而 `snapshot` 是自动决策（:133 起）读的那一份。
        #   只更新一份会出现"弹幕说的牌"与"插件实际打的牌"不一致。
        if solver_plan is not None:
            snapshot_with_context["solver_plan"] = solver_plan
            snapshot["solver_plan"] = solver_plan
            payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else None
            if payload is not None:
                payload["solver_plan"] = solver_plan
        # 若当前这步是需要"选一张牌"的动作（消耗/拉弃牌堆/生成到手/变换等），solver 已选好那张
        # （choice_card_id）。按当前步进索引取，供随后的选牌界面据此选牌。
        if in_combat_decision and solver_plan:
            pending_choice: str | None = None
            steps = _current_turn_steps(solver_plan)
            if steps:
                i = step_index if step_index < len(steps) else len(steps) - 1
                step0 = steps[i]
                if isinstance(step0, dict) and step0.get("choice_card_id"):
                    pending_choice = str(step0.get("choice_card_id"))
            self._service._state.pending_card_choice_id = pending_choice
        candidate_actions = self._service._candidate_generator.generate(
            # ★★★ t37（D1 实机复现）结构修复：**两条 planner 调用路径必须读同一份上下文**。
            #
            # 缺陷（2026-10-20 07:44–07:54，run=C6CM8CZRR9KQ，实机）：
            #   本入口（`candidate_generator.py:18` 会调 `planner.plan(context)`）与下方
            #   `planning_context`（:339 起）都调 planner，但**两个 dict 的键不同**：
            #     · 这里缺 `solver_step_index` / `pending_card_choice_id`
            #     · `planning_context` 有
            #   而 `heuristic_planner.py:1341` 正是：
            #       first_step = _line_step_at(solver_plan, context.get("solver_step_index"))
            #   ⇒ 缺键 ⇒ `context.get(...)` 返回 None ⇒ 取到**第 0 步**；
            #     有键 ⇒ 取到**第 N 步**。
            #   ⇒ **同一份 solver_plan、同一 tick、两个 first_step** ⇒
            #     一条判「可执行」（走 :1769 else 清零 stale/deadlock）、
            #     另一条判「漂移」（走 (A) 分支 +1）⇒ 计数被反复清零 ⇒
            #     上界 `LIMIT+1` 与破局 gate **永远不可达**（实测
            #     `source=solver_deadlock_breaker` = 0 次）。
            #
            # 实机铁证（原始日志逐行）：
            #   07:46:42 [sts2_plan] 复用快照 step=1/3（局面与快照一致）      ← 判"可复用"
            #   07:46:43 [sts2_plan] 复用快照 step=2/3（局面与快照一致）
            #   07:46:43 combat_follow 求解器=play_card/FLAME_BARRIER -> 执行=(过期/交接转死锁判定)
            #   07:46:44 [sts2_plan] 重新计算计划（原因：手牌里已没有可打出的 IRON_WAVE）
            #   ⇒ 同一份缓存被判出**相反**结论。
            # 只读验证：`.dsh-artifacts/_t37_pathsplit.log` —— 同一计划下
            #   缺键取到 FLAME_BARRIER、idx=1 取到 IRON_WAVE，★ 两者不同。
            #
            # ★ 修法（把约束写进**结构**，而不是留给注释）：
            #   抽成 `self._planner_context(...)` 单一工厂，两条路径共用**同一个 dict**。
            #   这样"两条路径必须一致"由构造方式保证 —— 将来任何人加键都只加一处，
            #   不可能再出现"一条有、一条没有"。同源化手法与 mod 侧 IsEventScreen 一致。
            self._planner_context(
                snapshot=snapshot,
                action_registry=action_registry,
                classification=classification,
                summary_context=summary_context,
                strategy_context=strategy_context,
                mode_info=mode_info,
                solver_plan=solver_plan,
                plan_origin=plan_origin,
                plan_fetch_signature=plan_fetch_signature,
            ),
            mode="program",
        )
        instructions = list(self._service._state.pending_guidance)
        instructions.append(
            normalize_strategy_instruction(
                str(strategy_context.get("strategy_name") or "unknown"),
                dict(strategy_context.get("strategy_constraints") if isinstance(strategy_context.get("strategy_constraints"), dict) else {}),
            )
        )
        decision_payload = DecisionPayload(
            mode=self._service._state.control_mode,
            screen_type=str(classification.get("screen_class") or "unknown"),
            state_name=str(classification.get("state_name") or snapshot.get("screen") or "unknown"),
            summary_kind=str(classification.get("summary_kind") or "general"),
            state_signature=str(self._service._state.snapshot_signature or ""),
            strategy_directives={
                "strategy_name": strategy_context.get("strategy_name"),
                "strategy_prompt": strategy_context.get("strategy_prompt"),
                "constraints": strategy_context.get("strategy_constraints"),
                **(strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}),
            },
            guidance={
                "pending": list(self._service._state.pending_guidance),
                "generation": self._service._state.guidance_generation,
                "summary": instruction_summary(instructions),
            },
            instructions=instructions,
            run_state={
                "floor": snapshot.get("floor"),
                "act": snapshot.get("act"),
                "in_combat": snapshot.get("in_combat"),
                "current_hp": snapshot_summary.get("player", {}).get("current_hp") if isinstance(snapshot_summary.get("player"), dict) else None,
                "max_hp": snapshot_summary.get("player", {}).get("max_hp") if isinstance(snapshot_summary.get("player"), dict) else None,
            },
            tactical_signals=tactical_signals,
            legal_actions=[dict(action) for action in action_registry if isinstance(action, dict)],
            candidate_actions=[dict(item) for item in candidate_actions if isinstance(item, dict)],
            policy={
                "allows_planner": bool(mode_info.get("allows_planner")),
                "allows_game_llm": bool(mode_info.get("allows_game_llm")),
                "prefers_heuristic": bool(mode_info.get("prefers_heuristic")),
                "prefers_model": bool(mode_info.get("prefers_model")),
            },
        ).as_dict()
        decision_payload["recent_decision_memory"] = list(self._service._state.recent_decision_memory)
        decision_payload["run_intent"] = dict(self._service._state.run_intent)
        summary_context["decision_payload"] = decision_payload
        strategy_context["decision_payload"] = decision_payload
        planning_context = self._planner_context(
            snapshot=snapshot,
            action_registry=action_registry,
            classification=classification,
            summary_context=summary_context,
            strategy_context=strategy_context,
            mode_info=mode_info,
            solver_plan=solver_plan,
            plan_origin=plan_origin,
            plan_fetch_signature=plan_fetch_signature,
        )
        # ★★ D8 判别（t17，仅日志，不改任何判据）：把"真实进程内的取值"打出来。
        #   动机：外部只读探针已逐一排除 9 环（planner 本身/取计划/_reusable_plan/
        #   _find_action/classify/in_combat_decision/action_registry 全部健康），
        #   但实机 400+ tick 零推进、零决策行。⇒ 必须看**运行时真实值**，
        #   而不是我从外部重建的输入（此前两次归因错误都源于"自己构造输入"）。
        #   这几个值一次打出即可判定病灶在【取计划】还是【planner 内部状态】。
        self._hb_log(
            "plan-call solver_plan=%s plan_origin=%s cached_sig=%s step_index=%s "
            "allows_planner=%s in_combat_decision=%s state_name=%s "
            "stale=%s deadlock=%s window=%s"
            % (
                "None" if solver_plan is None else "dict(len=%d)" % len(solver_plan),
                plan_origin,
                (str(self._solver_plan_sig) or "")[:12],
                step_index,
                bool(mode_info.get("allows_planner")),
                in_combat_decision,
                (classification or {}).get("state_name"),
                getattr(self._service._planner, "_solver_stale_reclaim_count", "?"),
                getattr(self._service._planner, "_solver_deadlock_count", "?"),
                getattr(self._service._planner, "_solver_out_of_window_count", "?"),
            ),
        )
        planned_operation = self._service._planner.plan(planning_context) if mode_info.get("allows_planner") else None
        # ★★ D8 判别（t17，仅日志）：plan() **之后** —— 返回的是 None 还是真操作？
        #   与上面 plan-call 那行配对，即可判定：
        #     plan-call 有 + plan-return None    ⇒ 被调用但在某早退点返回 None
        #     plan-call 有 + plan-return 有操作  ⇒ planner 正常，病灶在其下游（validate/execute）
        #     两行都没有                          ⇒ :292 没被执行（in_combat_decision 或更早）
        self._hb_log(
            "plan-return planned_operation=%s type=%s action_type=%s source=%s reason=%s"
            % (
                "None" if planned_operation is None else "obj",
                type(planned_operation).__name__,
                getattr(planned_operation, "action_type", "-"),
                getattr(planned_operation, "source", "-"),
                getattr(planned_operation, "reason", "-"),
            ),
        )
        planned_operation_dict = planned_operation.as_dict() if planned_operation is not None else None

        agent_operation = planned_operation_dict

        executable_operation = self._service._action_engine.validate({**snapshot, "action_registry": action_registry}, agent_operation)
        situation_summary = self._service._summary_engine.summarize(summary_context)
        companion_evaluation = self._service._companion_evaluator.evaluate(
            summary_context=summary_context,
            situation_summary=situation_summary,
            strategy_context=strategy_context,
            runtime_state=self._service._state,
        )
        action_frame = self._service._state.latest_action_frame if isinstance(self._service._state.latest_action_frame, dict) else {}
        if action_frame:
            companion_evaluation["action_frame"] = dict(action_frame)
        catgirl_sync = self._service._catgirl_bridge.build_sync_packet(
            {
                **snapshot,
                "classification": classification,
                "situation_summary": situation_summary,
                "companion_evaluation": companion_evaluation,
            },
            standby=self._service._state.standby,
        )
        # 带上触发这次点评的 SSE 事件类型：仅 player_action_window_opened（轮到玩家行动）时
        # catgirl 战斗 prompt 才带 line，其余战斗事件只报基础（怪物血/我方血/层级）。
        catgirl_sync["payload"]["event_type"] = self._current_event_type
        catgirl_sync["payload"]["agent_summary"] = {
            "standby": self._service._state.standby,
            "text": str(situation_summary.get("text") or ""),
            "kind": situation_summary.get("kind", "general"),
            "source": situation_summary.get("source", "snapshot"),
            "delta": dict(situation_summary.get("delta") if isinstance(situation_summary.get("delta"), dict) else {}),
            "before": dict(situation_summary.get("before") if isinstance(situation_summary.get("before"), dict) else {}),
            "after": dict(situation_summary.get("after") if isinstance(situation_summary.get("after"), dict) else {}),
            "recent_guidance": list(self._service._state.pending_guidance),
            "companion_evaluation": companion_evaluation,
        }

        return {
            "raw_state": raw_state,
            "raw_actions": raw_actions,
            "snapshot": {
                **snapshot,
                "action_registry": action_registry,
                "classification": classification,
                "summary_context": summary_context,
                "strategy_context": strategy_context,
                "mode": mode_info,
                "planned_operation": planned_operation_dict,
                "agent_operation": agent_operation,
                "executable_operation": executable_operation,
                "agent_packet": {
                    "screen": snapshot.get("screen", "unknown"),
                    "classification": classification,
                    "strategy_context": strategy_context,
                    "summary_context": summary_context,
                    "standby": self._service._state.standby,
                    "recent_guidance": list(self._service._state.pending_guidance),
                    "available_action_ids": [str(action.get("id") or "") for action in action_registry if isinstance(action, dict)],
                    "companion_evaluation": companion_evaluation,
                },
                "situation_summary": situation_summary,
                "companion_evaluation": companion_evaluation,
                "catgirl_sync": catgirl_sync,
                "polled_at": snapshot_with_context["polled_at"],
            },
        }

    def start_background(self) -> None:
        self._shutdown = False
        # 事件流模式下完全靠 /events/stream 驱动刷新（真正的变化才拉 /state），不再另起一个定时
        # 轮询循环——否则状态没变也每几秒重推一遍状态/通报，重复刷屏污染日志。轮询循环仅作为
        # use_event_stream=false（无 SSE 的客户端）的兜底。
        self._hb_log(
            "start_background use_event_stream=%s poll_task_alive=%s event_task_alive=%s"
            % (self._service._cfg_use_event_stream(),
               not self._task_done(self._poll_task), not self._task_done(self._event_task))
        )
        if not self._service._cfg_use_event_stream() and (
            self._poll_task is None or self._task_done(self._poll_task)
        ):
            self._ensure_owner_loop()
            self._poll_task = self._create_task(self._poll_loop(), name="sts2-poll-loop")
        if self._service._cfg_use_event_stream() and (
            self._event_task is None or self._task_done(self._event_task)
        ):
            self._ensure_owner_loop()
            self._event_task = self._create_task(self._event_loop(), name="sts2-event-stream")

    def start_autoplay(self) -> None:
        self._shutdown = False
        # ★ 注意：``_autoplay_task`` 的复活是本函数的传统职责；**刷新通路**
        #   （``_event_task`` / ``_poll_task``）的复活由下方 ``start_background()`` 负责。
        self._hb_log(
            "start_autoplay autoplay_task_alive=%s event_task_alive=%s poll_task_alive=%s"
            % (not self._task_done(self._autoplay_task),
               not self._task_done(self._event_task), not self._task_done(self._poll_task))
        )
        if self._autoplay_task is None or self._task_done(self._autoplay_task):
            self._ensure_owner_loop()
            self._autoplay_task = self._create_task(self._autoplay_loop(), name="sts2-autoplay-loop")
        # ★★ t14/D4 自愈：**同时**复活已死的刷新通路。
        #   历史缺陷（实测）：`_event_loop` 静默退出后，刷新通路彻底中断
        #   （`last_success_at` 不再前进、`step_count` 冻结）；而本函数只重建
        #   `_autoplay_task` ⇒ pause → start **完全无法恢复**，只能 reload 插件。
        #   `start_background()` 内已含"只在任务已死时才重建"的幂等判断
        #   （`_task_done` 检查），故此处直接复用，不会重复起任务。
        #   ★ 必须放在 `_autoplay_task` 之后：若刷新通路复活失败，
        #     autoplay 循环本身仍应存在，以便下一轮看门狗兜底。
        self.start_background()

    async def stop_background(self) -> None:
        self.stop_background_sync()

    def is_polling(self) -> bool:
        return self._poll_task is not None and not self._task_done(self._poll_task)

    def is_autoplaying(self) -> bool:
        return self._autoplay_task is not None and not self._task_done(self._autoplay_task)

    def _ensure_owner_loop(self) -> asyncio.AbstractEventLoop | None:
        loop = self._owner_loop
        thread = self._owner_thread
        if loop is not None and thread is not None and thread.is_alive() and not loop.is_closed():
            return loop

        ready = self._owner_thread_ready
        ready.clear()
        holder: dict[str, asyncio.AbstractEventLoop] = {}

        def _run_loop() -> None:
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            holder["loop"] = worker_loop
            ready.set()
            try:
                worker_loop.run_forever()
            finally:
                pending = [task for task in asyncio.all_tasks(worker_loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    worker_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                worker_loop.close()

        thread = threading.Thread(target=_run_loop, name="sts2-companion-poll", daemon=True)
        thread.start()
        if not ready.wait(timeout=2.0):
            loop = holder.get("loop")
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
            if thread.is_alive():
                thread.join(timeout=3.0)
            return None
        self._owner_loop = holder.get("loop")
        self._owner_thread = thread
        return self._owner_loop

    def _stop_owner_loop(self) -> None:
        loop = self._owner_loop
        thread = self._owner_thread
        self._owner_loop = None
        self._owner_thread = None
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)

    def _task_done(self, task: asyncio.Task[Any] | Future[Any] | None) -> bool:
        return task is None or task.done()

    async def _poll_loop(self) -> None:
        self._hb_log("poll-loop ENTER")
        n = 0
        while not self._shutdown:
            n += 1
            try:
                await self._service.refresh_state(trigger_sync=True)
            except Exception as exc:
                self._service._mark_loop_error(exc)
            if n % self._HB_EVERY == 0:
                self._hb_log("poll-loop N=%d OK" % n)
            await asyncio.sleep(self._poll_interval())
        self._hb_log("poll-loop EXIT(shutdown=True) n=%d" % n)

    # ★★ t14/D4 修复（批 1）：SSE 模式下的**周期兜底刷新**（no-progress 看门狗）。
    #
    # 缺陷（实机证据，2026-10-19）：
    #   `_event_loop` 阻塞在 `:565 async for ... subscribe_events()`（心跳实测 ENTER=4 / EXIT=0 /
    #   WAKE=0 / DROPPED=0 ⇒ 从未返回、从未重连），而 SSE 模式下 `start_background()` **不创建**
    #   `_poll_task`（见 :367-376，实测 `poll_task_alive=False`）。⇒ **没有任何周期刷新者**。
    #   于是某次 refresh 恰好拿到退化的 available_actions（如出牌动画瞬间只剩 save_and_quit）
    #   之后，快照被**永久固化**：陈旧 registry 里没有 end_turn ⇒ `action_engine.validate()`
    #   返回 None（实测 `executable_operation = null`）⇒ 动作发不出 ⇒ 局面不变化 ⇒
    #   再也不产生事件 ⇒ `_event_loop` 更不会被唤醒。**自洽死锁。**
    #   实机表现：step_count 卡在 13、turn 恒为 6、插件内部 names=['save_and_quit']
    #   而 mod 侧一直是 ['end_turn','save_and_quit']（陈旧 60s+ 不变）、
    #   `external_control.seconds_since_contact` 单调增长（无人向 mod 发请求）。
    #
    # 判据（与字节/事件无关，只看**业务进度**）：`step_count` 连续 `_FALLBACK_NO_PROGRESS_SECONDS`
    #   秒未增 ⇒ 强制 `refresh_state()`。★ 刻意**不用** state_version / 心跳 / 连接状态判活
    #   （这些都已被证明会掩盖真实停滞）。
    #
    # ★ 为什么这是安全网而不是"越权动作"：它**只刷新状态**，不发明任何动作；
    #   刷新后一切仍由既有判据决定（planner / validate / 窗口判定全部不变）。
    _FALLBACK_NO_PROGRESS_SECONDS = 20.0

    async def _maybe_fallback_refresh(self) -> None:
        """`step_count` 长时间不增 ⇒ 强制刷新一次，打破"陈旧快照 ⇒ 零推进"的自锁。"""
        try:
            state = self._service._state
            step_count = int(getattr(state, "step_count", 0) or 0)
            now = time()
            if step_count != self._fallback_last_step_count:
                # 有进展（或首次）⇒ 重新计时。★ 只在"真的动了"时清零。
                self._fallback_last_step_count = step_count
                self._fallback_last_progress_at = now
                return
            if now - self._fallback_last_progress_at < self._FALLBACK_NO_PROGRESS_SECONDS:
                return
            # ★ 到期仍未推进 ⇒ 强制刷新（并重新计时，避免每 tick 都刷）。
            self._fallback_last_progress_at = now
            self._fallback_refresh_count += 1
            self._hb_log(
                "no-progress-watchdog step_count=%s 停滞>=%.0fs ⇒ 强制 refresh_state（第 %d 次）"
                % (step_count, self._FALLBACK_NO_PROGRESS_SECONDS, self._fallback_refresh_count)
            )
            await self._service.refresh_state(trigger_sync=True)
        except Exception as exc:
            # 看门狗自身绝不拖垮主循环：记一次心跳日志后继续。
            self._hb_log("no-progress-watchdog 刷新失败: %r" % (exc,))

    def _poll_interval(self) -> float:
        # With the event stream active the poll loop is only a fallback (slow), so it never races the
        # event-driven refresh; without SSE it keeps the historical reactive cadence.
        if self._service._cfg_use_event_stream():
            return self._service._cfg_fallback_poll_interval()
        return self._service._cfg_poll_interval(active=self._service._state.autoplay_state == "running")

    # ★★ t14/D4（批 2）：**业务事件静默超时** —— 解决"心跳掩盖业务事件流死亡"。
    #
    # 缺陷（实机证据，2026-10-19）：
    #   mod 每 ~1s 发一行 `: heartbeat` 注释。它刷新了 `transport_client` 里 `read=60.0`
    #   的读超时 ⇒ **超时永不触发**；而注释行被 `continue` 丢弃、**不产出任何事件**。
    #   于是当**业务事件流**停了（插件自锁 ⇒ 局面不变 ⇒ mod 也不再发事件），
    #   连接看起来**完全健康**：`_event_loop` 永久阻塞在 `async for`，
    #   **不抛异常 ⇒ 不重连 ⇒ 不刷新**。实机心跳实测：
    #     `ENTER=4 / EXIT=0 / WAKE=0 / DROPPED=0`（从未返回、从未重连），
    #   而队长直连 mod `/events/stream` 仍是 HTTP 200、15 秒 16 行
    #   ⇒ ★ **保活信号掩盖了业务事件流的死亡**，这是本次死锁能永久持续的直接原因。
    #
    # 判据：**只有业务事件才算进度**。连接侧每产出一个业务事件就更新
    #   `client.last_business_event_at`（见 transport_client 的 SSE 循环）；
    #   这里若发现"距上次业务事件已超时"就**主动断开重连**，而不是干等。
    #   ★ 只用业务事件判活，绝不用心跳/连接状态 —— 后者正是本缺陷的来源。
    #   ★ 60s：正常推进时场景事件远密于此（战斗里每 1-3 秒若干条），
    #     同时远大于"长思考/结算动画"的正常停顿。
    _BUSINESS_EVENT_SILENCE_SECONDS = 60.0
    # 检查周期：无需每 tick 都看，10s 一次足够（且重连本身还有 backoff）。
    _BUSINESS_SILENCE_CHECK_EVERY = 10.0

    async def _business_silence_expired(self) -> bool:
        """返回 True 表示"业务事件已静默过久、需要断开重连"。

        ★ 刻意**不**看心跳/连接状态 —— 它们已被证明会掩盖真实停滞（见上）。
        """
        try:
            client = self._service._require_client()
            last = float(getattr(client, "last_business_event_at", 0.0) or 0.0)
            if last <= 0.0:
                # 本次连接还没产出过业务事件：从"连接建立时刻"起算，避免刚连上就误判。
                if not self._business_silence_since:
                    self._business_silence_since = time()
                silent = time() - self._business_silence_since
            else:
                self._business_silence_since = 0.0
                silent = time() - last
            if silent >= self._BUSINESS_EVENT_SILENCE_SECONDS:
                self._hb_log(
                    "event-loop 业务事件静默 %.0fs（>=%.0fs）⇒ 主动断开重连"
                    % (silent, self._BUSINESS_EVENT_SILENCE_SECONDS)
                )
                return True
        except Exception as exc:
            self._hb_log("业务静默检测异常: %r" % (exc,))
        return False

    async def _event_loop_body(self) -> None:
        """`_event_loop` 的单次订阅生命周期；返回即表示连接结束、需要重连。

        ★ 从 `_event_loop` 抽出来，是为了让外层能用 `asyncio.wait(timeout=...)`
        对"永久阻塞的 async for"做**业务静默检测**（心跳会一直维持连接，所以
        单靠 transport 的读超时永远等不到异常）。
        """
        client = self._service._require_client()
        if not hasattr(client, "subscribe_events"):
            # No SSE-capable transport; nothing to do.
            # ★★ 原本是静默 return（零日志、零重试）——现在必须留痕，否则
            #    "事件循环已死"会伪装成"局面没变化"。这里**仍保持** return 语义（仅加日志）。
            self._hb_log("event-loop EXIT(no subscribe_events on %s)" % type(client).__name__)
            return
        async for envelope in client.subscribe_events():
            event_type = envelope.get("type", "") if isinstance(envelope, dict) else ""
            if event_type == "stream_ready":
                # Connection (re)established: reset backoff and pull the current state once.
                self._event_backoff = 1.0
                self._business_silence_since = time()
                self._mark_sse_connected(True)
                self._hb_log("event-loop stream_ready (backoff reset)")
                await self._maybe_event_refresh(force=True)
                # 连接上 mod 后把其自身 LLM/弹幕交给本插件（mod 可能晚于插件启动），失败静默。
                try:
                    await self._service._require_client().set_config(llm_enabled=False, danmaku_enabled=False)
                except Exception:
                    pass
                continue
            if event_type in self._RELEVANT_EVENT_TYPES:
                # 战斗弹幕只认三个战斗事件（见 _COMBAT_DANMAKU_EVENTS）；非战斗事件清空，
                # 避免 _current_event_type 把上一场战斗的事件类型串进当前画面。
                self._current_event_type = event_type if event_type in self._COMBAT_DANMAKU_EVENTS else ""
                self._hb_log("event-loop EVENT=%s -> refresh" % event_type)
                await self._maybe_event_refresh(force=False)
            # heartbeat / unknown events -> ignore.

    async def _event_loop(self) -> None:
        # Subscribe to the mod's /events/stream and re-read /state on a scene change (the mod already
        # diffs its state, so these events fire only when something actually changed). Keeps reconnecting
        # with exponential backoff; the slow _poll_loop remains the safety net.
        # ★ 注意：SSE 模式下 start_background() **不会**创建 _poll_task（见 :367-376），
        #   所以本循环退出后无人再驱动 refresh_state。该事实由心跳日志暴露（t17/D8）。
        #   ★★ t14/D4 已补两道兜底：批 1 的 no-progress 强制刷新（`_maybe_fallback_refresh`）
        #      与批 2 的业务静默重连（本函数）。
        self._hb_log("event-loop ENTER")
        wakes = 0
        while not self._shutdown:
            wakes += 1
            try:
                self._service._require_client()
            except Exception as exc:
                self._hb_log("event-loop WAKE=%d require_client failed: %r" % (wakes, exc))
                await asyncio.sleep(1.0)
                continue

            if wakes % self._HB_EVERY == 0:
                self._hb_log("event-loop WAKE=%d subscribing" % wakes)

            # ★★ 把订阅包在独立任务里，以便对"永久阻塞的 async for"做超时检查。
            sub = asyncio.ensure_future(self._event_loop_body())
            try:
                while not sub.done():
                    # 每 _BUSINESS_SILENCE_CHECK_EVERY 秒醒一次看看业务事件是否已静默过久。
                    await asyncio.wait({sub}, timeout=self._BUSINESS_SILENCE_CHECK_EVERY)
                    if sub.done():
                        break
                    if await self._business_silence_expired():
                        self._hb_log("event-loop 因业务静默被强制重置（wakes=%d）" % wakes)
                        sub.cancel()
                        break
                if sub.done() and not sub.cancelled():
                    # 订阅正常/异常结束 ⇒ 取出异常（若有）以按既有路径记日志并重连。
                    exc = sub.exception()
                    if exc is not None:
                        raise exc
            except Exception as exc:
                self._service.logger.warning("SSE events stream dropped: %s", exc)
                self._hb_log("event-loop DROPPED: %r" % (exc,))
            finally:
                if not sub.done():
                    sub.cancel()
                    try:
                        await sub
                    except (asyncio.CancelledError, Exception):
                        pass
                self._mark_sse_connected(False)

            # Disconnected: backoff and retry. Do not flood while the mod is briefly unavailable.
            await asyncio.sleep(self._event_backoff)
            self._event_backoff = min(self._event_backoff * 2, 30.0)
        self._hb_log("event-loop EXIT(shutdown=True) wakes=%d" % wakes)

    async def _maybe_event_refresh(self, *, force: bool) -> None:
        now = time()
        if not force and now - self._last_event_refresh < self._EVENT_FETCH_MIN_INTERVAL_SECONDS:
            return
        self._last_event_refresh = now
        try:
            await self._service.refresh_state(trigger_sync=True)
        except Exception as exc:
            self._service._mark_loop_error(exc)

    def _mark_sse_connected(self, connected: bool) -> None:
        state = self._service._state
        # Count only a real transition (an established stream that dropped), not a clean shutdown.
        if not connected and self._sse_connected and not self._shutdown:
            state.sse_reconnect_count += 1
        self._sse_connected = connected

    async def _autoplay_loop(self) -> None:
        # ★ 诊断心跳（t17/D8，仅日志，不改任何判据）：
        #   目的 = 区分"循环死了/挂住"与"循环活着但每 tick 早退/无动作"。
        #   独立 tag [sts2_heartbeat]，绝不混进决策日志（否则破坏"决策日志空洞"判据）。
        self._hb_log("autoplay-loop ENTER")
        tick = 0
        while not self._shutdown:
            tick += 1
            if self._service._state.autoplay_state != "running" or self._service._state.standby:
                # ★ 静默早退分支：原先无任何日志，是"零输出"的一个候选成因。
                if tick % self._HB_EVERY == 0:
                    self._hb_log(
                        "autoplay-loop TICK=%d SKIP(state=%s standby=%s)"
                        % (tick, self._service._state.autoplay_state, self._service._state.standby)
                    )
                await asyncio.sleep(0.25)
                continue
            try:
                await self._service.run_autoplay_step()
            except Exception as exc:
                # 出错**不停车**：记一次错误后退避重试。autoplay_state 只表达玩家意图，
                # 任何错误都不该把它从 running 改掉（否则这个循环会永久空转，见 service._mark_loop_error）。
                self._service._mark_loop_error(exc)
                await asyncio.sleep(self._service._autoplay_retry_backoff())
                continue
            await self._maybe_fallback_refresh()
            # ★★★ t29/§13.2 空转检测接线（**纯观测**，不改任何决策行为）。
            #   为什么接在这里（三条全部实测核对过）：
            #     ① **真 heartbeat = `tick`**（:803 初始化 / :805 每轮 +1，**无论是否推进**，
            #        且 SKIP 早退分支也不会跳过它）；
            #     ② **turn_key** = `self._service._combat_turn_key(snapshot)`（同步方法，
            #        :2987 定义、:3004 返回 `run_id|floor|room|turn`）；
            #     ③ 与本文件 :836 `_hb_log()` / :834 `_HB_EVERY=20` 同源，心跳语义不走样。
            #
            #   ★★ 绝不可用 `step_count` 顶替 heartbeat：`service.py:636-637` 规定它
            #     **只在 `result["status"] == "ok"` 时 +1** ⇒ 真卡死时没有动作成功 ⇒ 冻结
            #     ⇒ `heartbeat_growing` 恒 False ⇒ 判据**永不告警**
            #     （实测 .dsh-artifacts/_t27_stepcount_fatal.log：其余各维全为 True 仍 stalled=False）。
            #     这是"交付一个永不触发的判据"缺陷的自我复制，比不接线更危险。
            #
            #   ★ turn_key 形态说明（契约要求写明）：这里用 **含 run_id** 的
            #     `_combat_turn_key()` 形态（`run_id|floor|room|turn`），**不是**日志里
            #     那 64500 条无 run_id 的 `act:floor:screen:turn` 形态（`.dsh-artifacts/
            #     _t27_turnkey_forms.log`: 竖线数 {0:64500}）。选含 run_id 的理由：
            #     turn_key 跨 run 会撞值（实测 132/175=75%，0:2:combat:1 出现在 20 个 run），
            #     含 run_id 天然按局隔离 ⇒ 「见新局面就重置窗口」才正确，
            #     不必依赖 note_turn_key_sample 的 run_id 形参去纠偏。
            self._note_stall_sample(tick)
            if tick % self._HB_EVERY == 0:
                self._hb_log(
                    "autoplay-loop TICK=%d OK step_count=%s"
                    % (tick, getattr(self._service._state, "step_count", "?"))
                )
            await asyncio.sleep(self._service._cfg_action_interval())
        self._hb_log("autoplay-loop EXIT(shutdown=True) tick=%d" % tick)

    # ★ 心跳间隔：每 N 次 tick 记一行（避免刷屏）。按 action_interval≈0.3s 估算，
    #   _HB_EVERY=20 ⇒ 约每 6s 一行，足以判定存活且不会淹没决策日志。
    _HB_EVERY = 20

    def _hb_log(self, message: str) -> None:
        """诊断心跳（仅日志）。任何异常都被吞掉，绝不影响决策链路。"""
        try:
            self._service.logger.info("[sts2_heartbeat] %s", message)
        except Exception:
            pass

    # ── ★★★ t29/§13.2 空转检测接线 ───────────────────────────────────────────────
    def _note_stall_sample(self, tick: int) -> None:
        """把一个心跳样本喂给空转判据，并在判定成立时打 `[sts2_stall]` 告警。

        **纯观测**：只读 `_combat_turn_key()`、只写 runtime_state 里 stall_* 记账字段，
        绝不改任何决策字段（有测试 `test_wiring_touches_no_decision_state` 锁住）。

        参数 ``tick`` 是 `_autoplay_loop` 的循环计数（每轮 +1，**无论是否推进**）——
        它就是本判据要的"真心跳"，见调用处注释里为何不能用 `step_count`。

        任何异常都被吞掉：这是可观测性代码，**绝不能**因为日志/记账问题影响游玩链路
        （与 `_hb_log` 同族纪律）。
        """
        try:
            snapshot = self._service._state.snapshot
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            turn_key = self._service._combat_turn_key(snapshot)
            verdict = self._service._state.note_turn_key_sample(turn_key, heartbeat=int(tick))
            if not verdict.get("should_warn"):
                return
            self._service.logger.warning(
                "[sts2_stall] ⚠ 疑似空转：%.0f 分钟内同一局面（%s）反复重算 %d 次"
                "（占比 %.2f，种类 %d，样本 %d，tick=%d，heartbeat 在涨）",
                float(verdict.get("zero_new_seconds", 0.0)) / 60.0,
                turn_key or "<empty>",
                int(verdict.get("repeats", 0)),
                float(verdict.get("top_key_share", 0.0)),
                int(verdict.get("distinct_keys", 0)),
                int(verdict.get("samples", 0)),
                int(tick),
            )
        except Exception:
            pass

    def _planner_context(
        self,
        *,
        snapshot: dict[str, Any],
        action_registry: list[Any],
        classification: Any,
        summary_context: dict[str, Any],
        strategy_context: dict[str, Any],
        mode_info: dict[str, Any],
        solver_plan: dict[str, Any] | None,
        plan_origin: str,
        plan_fetch_signature: str,
    ) -> dict[str, Any]:
        """★★★ t37：**两条 planner 调用路径共用的唯一上下文工厂**（把约束写进结构）。

        ## 为什么需要这个函数

        `tick()` 里有**两条**会调用 `planner.plan()` 的路径：

          ① `candidate_generator.generate(...)` —— `candidate_generator.py:18` 内部调 `plan()`
             （排在前面，是战斗决策的**第一条** planner 调用路径）
          ② `planning_context`（供后续 `plan()` / 决策用）

        在 t37 之前，它们**各自手写了一个 dict**，而两个 dict 的键并不一致：
        ① 缺 `solver_step_index` / `pending_card_choice_id`，② 有。

        而 `heuristic_planner.py:1341` 恰恰是：
            `first_step = _line_step_at(solver_plan, context.get("solver_step_index"))`

        ⇒ ① 缺键 ⇒ `context.get(...)` 返回 `None` ⇒ 取到 line 的**第 0 步**；
          ② 有键 ⇒ 取到**第 N 步**。
        ⇒ **同一份 solver_plan、同一 tick，两条路径算出不同的 `first_step`** ⇒
          一条判「可执行」（走 :1769 else，清零 stale/deadlock），
          另一条判「漂移」（走 (A) 分支，+1）⇒ 计数被反复清零 ⇒
          上界 `LIMIT+1` 与破局 gate **永远不可达**。

        实机铁证（`N.E.K.O_Plugin_sts2_autoplay_20261020.log`，只读）：
            07:46:42 [sts2_plan] 复用快照 step=1/3（局面与快照一致）   ← 判"可复用"
            07:46:43 [sts2_plan] 复用快照 step=2/3（局面与快照一致）
            07:46:43 combat_follow 求解器=play_card/FLAME_BARRIER -> 执行=(过期/交接转死锁判定)
            07:46:44 [sts2_plan] 重新计算计划（原因：手牌里已没有可打出的 IRON_WAVE）
        ⇒ 同一份缓存被判出**相反**结论；实测 `source=solver_deadlock_breaker` = **0** 次。

        ## 上游同构（铁律要求的对照）

        `CombatSolver/src/Runtime/SolverController.cs:2802-2815` 用 `action.IsExecutable`
        判「这一步现在还做得了吗」，判否就 `ContinuationSource = null` + 重搜；
        `ReplanCause.DeploymentDrift`（:26）与 `PlanExhausted`（:27）**分两笔账**，
        `CombatBugReportDescription.cs:113-115` 把两者都作为**跨重试累计**的证据上报。
        ⇒ 上游不存在"同一 tick 对同一计划得出两个相反判定"的形态；
        我们的缺陷**不是**上游有意为之的设计行为。

        ## 这个函数如何防止复发

        两条路径**共用同一个 dict 对象**，因此：
          · 不可能再出现"一条有某个键、另一条没有"；
          · 将来加键只需改这一处；
          · `tests/test_plan_reuse_step_consistency.py` 直接断言两条路径的键集合相等。
        （同源化手法与 mod 侧 `IsEventScreen` 一致：让"必须一致"由构造方式保证，
          而不是靠注释提醒。）
        """
        return {
            "snapshot": {**snapshot, "action_registry": action_registry},
            "classification": classification,
            "summary_context": summary_context,
            "strategy_context": strategy_context,
            "mode": mode_info,
            "solver_plan": solver_plan,
            # ★ D1：计划来源（reused / refetched / none）。planner 用它区分
            #   「计划过期 ⇒ 重取、不计死锁」与「求解器真无解 ⇒ 计数并最终兜底」。
            #   ★ 若不透传，planner 读到缺省 `"none"`（归 A）⇒ B 永远不会被判定
            #     ⇒ 死锁保险形同被摘掉（实机曾出现 `plan_origin=none` 而永远是 A）。
            "plan_origin": plan_origin,
            # ★ t11：**取回计划那一刻**的局面指纹。planner 判 B 时要求它 == 当前指纹，
            #   否则说明"取回的计划已经漂移"⇒ 归 A、立即重取、**不计数**。
            "plan_fetch_signature": plan_fetch_signature,
            # ★★★ t37 关键修复：这两个键原先**只有 planning_context 有**，
            #   candidate_generator 路径缺失 ⇒ 两条路径的 `first_step` 不同。
            #   现在由本工厂统一提供 ⇒ 两条路径必然读同一个 step。
            "pending_card_choice_id": self._service._state.pending_card_choice_id,
            "solver_step_index": self._service._state.solver_step_index,
        }

    def _log_plan_reuse(self, step_index: int, total: int) -> None:
        """按快照继续执行时留痕（同一步只记一次）—— 用来核对"到底省了多少次求解"。"""
        key = f"reuse:{step_index}/{total}"
        if key == self._plan_log_key:
            return
        self._plan_log_key = key
        try:
            self._service.logger.info("[sts2_plan] 复用快照 step=%s/%s（局面与快照一致）", step_index + 1, total)
        except Exception:
            pass

    def _log_plan_refresh(self, reason: str) -> None:
        """重新向 mod 取计划时留痕（含原因）—— 原因是"局面不一致"还是"回合变了"要看得出来。"""
        try:
            self._service.logger.info("[sts2_plan] 重新计算计划（原因：%s）", reason)
        except Exception:
            pass

    def invalidate_combat_plan_cache(self) -> None:
        """丢弃整回合缓存的 /solver/plan（下一次 tick 重新向 mod 取）。

        场景：动作被 mod 拒收（多半是缓存计划指向的敌人已经死了）。不丢的话下一轮还会拿同一个
        过期下标去撞同一堵墙。
        """
        self._solver_plan_sig = None
        self._solver_plan_cached = None
        # ★ t11：指纹与缓存计划同生共死 —— 清缓存必须一并清指纹，
        #   否则新取回的计划会错误沿用上一份的"取回时指纹"。
        self._solver_plan_fetch_signature = ""

    def _create_task(self, coro: Any, *, name: str) -> asyncio.Task[Any] | Future[Any] | None:
        try:
            current_loop = asyncio.get_running_loop()
            if self._owner_loop is None or self._owner_loop.is_closed() or not self._owner_loop.is_running():
                self._owner_loop = current_loop
            loop = self._owner_loop
            if loop is current_loop:
                task = loop.create_task(coro, name=name)
            else:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                task = asyncio.wrap_future(future)
            task.add_done_callback(lambda t: self._log_task_done(name, t))
            return task
        except RuntimeError:
            coro.close()
            return None

    def _log_task_done(self, name: str, task: asyncio.Task[Any] | Future[Any]) -> None:
        return


    def stop_background_sync(self) -> None:
        self._shutdown = True
        for task in (self._autoplay_task, self._poll_task, self._event_task):
            if task is None or task.done():
                continue
            try:
                task.cancel()
            except RuntimeError:
                # 事件循环已被关闭（shutdown 撞上 NEKO 超时强杀时会出现），忽略。
                pass
        self._autoplay_task = None
        self._poll_task = None
        self._event_task = None
        self._stop_owner_loop()


def _combat_deadlock_signature(snapshot: dict[str, Any]) -> str:
    """战斗死锁判据用的**局面指纹**（手牌 card_id 集合 / 能量 / 敌人血量）。

    ★ 为什么把它从 `heuristic_planner._combat_deadlock_signature` 提到这里：
      t11 修复需要 `loop_runner` 在**取回计划的那一瞬间**记下当时的局面指纹
      （`plan_fetch_signature`），而 planner 侧那份是实例方法、loop_runner 拿不到。
      两份实现若各写一遍，将来必然漂移 ⇒ 提取成同一个模块级纯函数，**双方共用**，
      planner 的方法改为委托给它（保持既有调用点与测试不变）。

    取材刻意与 `_combat_plan_signature` 对齐（手牌/能量/敌人血量），
    这样"计划该重取"与"死锁该计数"依据同一个局面概念，不会互相打架。
    """
    raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
    hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []

    card_ids = sorted(str(c.get("card_id") or "?") for c in hand if isinstance(c, dict))
    enemy_hp = sorted(
        str(e.get("current_hp") if e.get("current_hp") is not None else e.get("hp") or "?")
        for e in enemies
        if isinstance(e, dict)
    )
    return "|".join([
        ",".join(card_ids),
        str(player.get("energy") if player.get("energy") is not None else "?"),
        ",".join(enemy_hp),
    ])


def _combat_plan_signature(snapshot: dict[str, Any]) -> str:
    """Turn-stable fingerprint for WHOLE-TURN caching: 按 回合(run_id+turn) **与存活敌人集合** 键。

    GET /solver/plan 每次调用都会重跑 CombatSolver 搜索；插件现在"回合开始取一次、整回合复用"
    （见 solver_step_index），所以签名在回合内尽量不变，进入新回合才变化从而重查。
    注意：不能把整局战斗状态放进签名——那每出一张牌就变，起不到缓存作用。

    **但存活敌人集合必须进签名**：计划里的 target_index 是**绝对下标**。回合内死掉一只敌人后
    （史莱姆成群出现，这是常态），旧计划的下标就可能越界，于是 mod 以
    ``invalid_target: target_index is out of range`` 拒收。敌人集合变化就让缓存失效、按现场重算。
    HP 刻意排除在外：血量每出一张牌都变，放进去等于取消缓存。
    """
    raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    run_id = str(raw.get("run_id") or run.get("run_id") or run.get("runId") or run.get("id") or "")
    turn = raw.get("turn")
    if turn is None:
        combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else {}
        turn = combat.get("turn")
    return hashlib.sha256(f"{run_id}|turn:{turn}|{_alive_enemy_key(raw)}".encode("utf-8")).hexdigest()


def _alive_enemy_key(raw_state: dict[str, Any]) -> str:
    """存活敌人的 (下标, 身份) 清单——只有"谁还活着"变了才算局面换了目标空间。"""
    combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
    enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
    parts: list[str] = []
    for enemy in enemies:
        if not isinstance(enemy, dict):
            continue
        if enemy.get("is_alive") is False:
            continue
        parts.append(f"{enemy.get('index')}:{enemy.get('enemy_id') or enemy.get('move_id') or ''}")
    return ",".join(sorted(parts))


def _current_turn_steps(solver_plan: dict[str, Any]) -> list[dict[str, Any]] | None:
    line = solver_plan.get("line") if isinstance(solver_plan.get("line"), list) else None
    if not line or not isinstance(line[0], dict):
        return None
    steps = line[0].get("steps") if isinstance(line[0].get("steps"), list) else None
    return steps if isinstance(steps, list) else None


def _reusable_plan(
    cached: dict[str, Any] | None,
    cached_sig: Any,
    sig: Any,
    step_index: int,
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    """计划快照能不能继续按它执行？返回 (可复用的计划 或 None, 不能复用时的原因)。

    抽成纯函数是为了可测：这是"算一次 → 按快照执行 → 局面不一致就重算"这条口径的**全部判据**。
    """
    if not cached or cached_sig != sig:
        return None, "回合或敌人组合变了"
    steps = _current_turn_steps(cached)
    if steps is None:
        return None, "快照里没有可执行步骤"
    if step_index >= len(steps):
        return None, "快照步骤已用尽"
    ok, why = _plan_step_is_executable(steps[step_index], snapshot)
    if not ok:
        return None, why
    return cached, ""


def _explain_hand_card_state(card_id: str, hand: list[Any], player: Any) -> str:
    """★ t43：把「这一步的这张牌打不出去」拆成**两类可区分**的原因。

    旧实现只有一句话 —— `手牌里已没有可打出的 {card_id}` —— 把两种完全不同的成因压在一起：

      ① 这张卡**真的不在手牌**（已打出 / 被弃 / 被消耗）
         ⇒ 计划确实过期，丢弃重算是**正确**行为；
      ② 这张卡**在手牌里，但当前 `playable=false`**（能量不够 / 有前置条件）
         ⇒ 计划本身没错，只是**这一步暂不可执行**。

    两者的统计含义与后续修法完全不同：若 ② 占多，说明**模拟器的能量模型与实机不一致**。

    ★ 为什么必须拆（实测数据，2026-10-20）：
      当天 `[sts2_plan]` 重算 4548 次，其中 **1681 次（37.0%）** 报的就是这一句，
      而漂移 Top 全是**最基础的卡**（`DEFEND_IRONCLAD` 221 / `STRIKE_IRONCLAD` 206 /
      `SHRUG_IT_OFF` 85 / `BASH` 82）—— 最基础的打击/防御"手牌里没有"本身就反常。
      在拆开之前，这 1681 次**无法归因**，也就无法判断该不该修能量模型。

    ★ 本函数放在 `loop_runner` 而不是各写一份：`heuristic_planner._resolve_solver_card_index`
      有同一套判据（`card_id` 精确相等 + `playable`），两份实现必然漂移
      —— 这正是 `_combat_deadlock_signature` 当初被提取到这里的同一条理由。

    参数与 `/state` 的字段对应（`raw_state.combat.{hand,player}`）：
      hand[i]  = {card_id, playable, energy_cost, star_cost, costs_x, ...}
      player   = {energy, ...}

    返回值**只描述"为什么打不出"**；能打出的情况不调用本函数。
    """
    same_id = [
        c for c in (hand if isinstance(hand, list) else [])
        if isinstance(c, dict) and str(c.get("card_id") or "") == card_id
    ]
    total = len(hand) if isinstance(hand, list) else 0
    if not same_id:
        return f"手牌里已没有 {card_id}（不在手牌，共 {total} 张）"
    energy = player.get("energy") if isinstance(player, dict) else None
    # 同名多张时只列前 3 张，避免日志爆长
    detail = " ".join(
        "[idx=%s energy_cost=%s star_cost=%s costs_x=%s]" % (
            c.get("index", "?"), c.get("energy_cost", "?"),
            c.get("star_cost", "?"), bool(c.get("costs_x")),
        )
        for c in same_id[:3]
    )
    return (
        f"手牌里有 {card_id} 但当前不可打出"
        f"（playable=false，同名 {len(same_id)} 张，"
        f"当前能量={energy if energy is not None else '?'}） {detail}"
    )


def _plan_step_is_executable(step: dict[str, Any], snapshot: dict[str, Any]) -> tuple[bool, str]:
    """快照里的这一步，在**当前局面**下还能照做吗？

    这就是玩家要的"每次行动后用当前局面和快照对比"：
    一致 -> 继续按快照走；不一致 -> 丢掉快照重新算。

    为什么不能只看"回合签名没变"：抽牌/弃牌/消耗会让**手牌位置漂移**，步进下标会指到别的牌上；
    敌人死了会让目标失效。这些都不会改变回合号，但足以让快照里的下一步变成非法动作。

    判据（保守：拿不准就说"能执行"，让原有的动作层兜底）：

    - `play_card`：优先按 `card_id` 找当前手牌里**可打出**的那张（位置会漂，id 不会）；
      只有 `card_index` 时校验下标在范围内且可打出；牌需要目标时校验 `target_index` 仍在
      `valid_target_indices` 里。
    - `use_potion`：该药水仍在且 `can_use`。
    - `end_turn`：`end_turn` 仍在可用动作里（否则说明时序还没轮到我们）。
    - 其它步骤（选牌等）：交给下层的选牌界面逻辑，不在这里判。
    """
    if not isinstance(step, dict):
        return True, ""
    kind = str(step.get("kind") or step.get("action") or "").strip().lower()
    raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
    if kind == "play_card":
        hand = [card for card in (combat.get("hand") if isinstance(combat.get("hand"), list) else []) if isinstance(card, dict)]
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        card_id = str(step.get("card_id") or "")
        target = step.get("target_index")
        if card_id:
            card = next(
                (item for item in hand if str(item.get("card_id") or "") == card_id and bool(item.get("playable", True))),
                None,
            )
            if card is None:
                # ★ t43：不再用一句「已没有可打出的」掩盖两种成因。
                #   详见 `_explain_hand_card_state`：① 不在手牌 ② 在手但 playable=false。
                return False, _explain_hand_card_state(card_id, hand, player)
        else:
            index = step.get("card_index")
            if isinstance(index, int) and not isinstance(index, bool):
                if not (0 <= index < len(hand)):
                    return False, f"手牌下标 {index} 越界（现在 {len(hand)} 张）"
                if not bool(hand[index].get("playable", True)):
                    # ★ t43：这一支同样带上能量实况，与 card_id 支保持同一种诊断口径。
                    energy = player.get("energy") if isinstance(player, dict) else None
                    return False, (
                        f"手牌[{index}] 当前不可打出（playable=false，"
                        f"card_id={hand[index].get('card_id', '?')}，"
                        f"energy_cost={hand[index].get('energy_cost', '?')}，"
                        f"当前能量={energy if energy is not None else '?'}）"
                    )
                card = hand[index]
            else:
                card = None
        if card is not None and target is not None:
            valid = card.get("valid_target_indices")
            if isinstance(valid, list) and valid:
                try:
                    if int(target) not in {int(item) for item in valid}:
                        return False, f"目标 {target} 已失效"
                except (TypeError, ValueError):
                    pass
        return True, ""
    if kind == "use_potion":
        potion_id = str(step.get("card_id") or step.get("potion_id") or "")
        if not potion_id:
            return True, ""
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        usable = any(
            isinstance(potion, dict)
            and str(potion.get("potion_id") or "") == potion_id
            and bool(potion.get("can_use"))
            for potion in potions
        )
        return (True, "") if usable else (False, f"药水 {potion_id} 当前不可用")
    if kind == "end_turn":
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        available = {
            str(action.get("type") or action.get("name") or "")
            for action in actions
            if isinstance(action, dict)
        }
        return (True, "") if "end_turn" in available else (False, "现在不能结束回合")
    return True, ""


__all__ = ["STS2LoopRunner"]
