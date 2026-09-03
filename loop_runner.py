from __future__ import annotations

import asyncio
import hashlib
import json
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
        # Cache the event-room LLM advice against the event-state signature (see _event_plan_signature)
        # so the LLM is not re-consulted every tick while the event options are unchanged.
        self._event_llm_sig: Any = None
        self._event_llm_cached: dict[int, float] | None = None
        # Scene-change event streaming (mod GET /events/stream): drive a refresh on a real change
        # instead of polling /state on a timer. See _event_loop / _RELEVANT_EVENT_TYPES.
        self._event_task: asyncio.Task[Any] | Future[Any] | None = None
        self._last_event_refresh = 0.0
        self._event_backoff = 1.0
        self._sse_connected = False

    async def tick(self) -> dict[str, Any]:
        client = self._service._require_client()
        raw_state = await client.get_state()
        raw_actions = await client.get_available_actions()
        snapshot = normalize_snapshot(raw_state, raw_actions)
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
        in_combat_decision = classification.get("state_name") == "combat" and mode_info.get("allows_planner")
        step_index = self._service._state.solver_step_index if isinstance(self._service._state.solver_step_index, int) else 0
        if in_combat_decision:
            sig = _combat_plan_signature(snapshot)
            if sig == self._solver_plan_sig and self._solver_plan_cached:
                solver_plan = self._solver_plan_cached
                # 当前回合的步骤已含最后一个 end_turn 且被取完 -> 视为进入新回合，触发重查。
                steps = _current_turn_steps(solver_plan)
                if steps is None or step_index >= len(steps):
                    solver_plan = None
            else:
                solver_plan = None
            if solver_plan is None:
                try:
                    fetched = await client.get_combat_plan()
                except Exception:
                    fetched = None
                if fetched and fetched.get("in_combat"):
                    # Only cache an actionable plan; otherwise leave it empty so the next tick retries.
                    solver_plan = fetched
                    self._solver_plan_sig = sig
                    self._solver_plan_cached = fetched
                    self._service._state.solver_step_index = 0
                else:
                    solver_plan = None
        else:
            # Not in a combat decision phase: drop the cache so re-entering combat refetches.
            # 不清空 solver_step_index：中途可能出现选牌(combat_hand_select)等非决策态。
            self._solver_plan_sig = None
            self._solver_plan_cached = None
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
        # Event-room LLM advice (per-option scores) for heuristic fusion. plan() is sync and the LLM
        # is async, so fetch here (async tick) and inject into the contexts it reads. Cache by
        # event-state signature so the LLM is not re-consulted while the event options are unchanged.
        event_llm_scores = None
        event_llm_weight = float(getattr(self._service, "_event_llm_weight", 0.5) or 0.5)
        in_event_decision = (
            bool(getattr(self._service, "_event_llm_enabled", True))
            and classification.get("state_name") == "event"
            and mode_info.get("allows_planner")
        )
        if in_event_decision:
            esig = _event_plan_signature(summary_context)
            if esig == self._event_llm_sig:
                event_llm_scores = self._event_llm_cached
            else:
                try:
                    payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
                    event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
                    player = snapshot_summary.get("player") if isinstance(snapshot_summary.get("player"), dict) else {}
                    run_context = {
                        "character_name": player.get("character_name"),
                        "current_hp": player.get("current_hp") if player.get("current_hp") is not None else payload.get("current_hp"),
                        "max_hp": player.get("max_hp") if player.get("max_hp") is not None else payload.get("max_hp"),
                        "gold": payload.get("gold"),
                        "deck": payload.get("deck"),
                        "relics": payload.get("relics"),
                    }
                    fetched = await self._service._event_advice.score_event_options(
                        options=event_options,
                        run_context=run_context,
                        strategy_context=strategy_context,
                    )
                except Exception:
                    fetched = None
                if fetched:
                    event_llm_scores = fetched
                    self._event_llm_sig = esig
                    self._event_llm_cached = fetched
                else:
                    event_llm_scores = None
        else:
            # Not in an event decision phase: drop the cache so re-entering an event refetches.
            self._event_llm_sig = None
            self._event_llm_cached = None
        candidate_actions = self._service._candidate_generator.generate(
            {
                "snapshot": {**snapshot, "action_registry": action_registry},
                "classification": classification,
                "summary_context": summary_context,
                "strategy_context": strategy_context,
                "solver_plan": solver_plan,
                "event_llm_scores": event_llm_scores,
                "event_llm_weight": event_llm_weight,
            },
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
        planning_context = {
            "snapshot": {**snapshot, "action_registry": action_registry},
            "classification": classification,
            "summary_context": summary_context,
            "strategy_context": strategy_context,
            "mode": mode_info,
            "solver_plan": solver_plan,
            "event_llm_scores": event_llm_scores,
            "event_llm_weight": event_llm_weight,
            "pending_card_choice_id": self._service._state.pending_card_choice_id,
            "solver_step_index": self._service._state.solver_step_index,
        }
        planned_operation = self._service._planner.plan(planning_context) if mode_info.get("allows_planner") else None
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
        if self._autoplay_task is None or self._task_done(self._autoplay_task):
            self._ensure_owner_loop()
            self._autoplay_task = self._create_task(self._autoplay_loop(), name="sts2-autoplay-loop")

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
        while not self._shutdown:
            try:
                await self._service.refresh_state(trigger_sync=True)
            except Exception as exc:
                self._service._mark_loop_error(exc)
            await asyncio.sleep(self._poll_interval())

    def _poll_interval(self) -> float:
        # With the event stream active the poll loop is only a fallback (slow), so it never races the
        # event-driven refresh; without SSE it keeps the historical reactive cadence.
        if self._service._cfg_use_event_stream():
            return self._service._cfg_fallback_poll_interval()
        return self._service._cfg_poll_interval(active=self._service._state.autoplay_state == "running")

    async def _event_loop(self) -> None:
        # Subscribe to the mod's /events/stream and re-read /state on a scene change (the mod already
        # diffs its state, so these events fire only when something actually changed). Keeps reconnecting
        # with exponential backoff; the slow _poll_loop remains the safety net.
        while not self._shutdown:
            try:
                client = self._service._require_client()
                if not hasattr(client, "subscribe_events"):
                    # No SSE-capable transport; nothing to do.
                    return
            except Exception:
                await asyncio.sleep(1.0)
                continue

            try:
                async for envelope in client.subscribe_events():
                    event_type = envelope.get("type", "") if isinstance(envelope, dict) else ""
                    if event_type == "stream_ready":
                        # Connection (re)established: reset backoff and pull the current state once.
                        self._event_backoff = 1.0
                        self._mark_sse_connected(True)
                        await self._maybe_event_refresh(force=True)
                        # 连接上 mod 后把其自身 LLM/弹幕交给本插件（mod 可能晚于插件启动），失败静默。
                        try:
                            await self._service._require_client().set_config(llm_enabled=False, danmaku_enabled=False)
                        except Exception:
                            pass
                        continue
                    if event_type in self._RELEVANT_EVENT_TYPES:
                        await self._maybe_event_refresh(force=False)
                    # heartbeat / unknown events -> ignore.
            except Exception as exc:
                self._service.logger.warning("SSE events stream dropped: %s", exc)
            finally:
                self._mark_sse_connected(False)

            # Disconnected: backoff and retry. Do not flood while the mod is briefly unavailable.
            await asyncio.sleep(self._event_backoff)
            self._event_backoff = min(self._event_backoff * 2, 30.0)

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
        while not self._shutdown:
            if self._service._state.autoplay_state != "running" or self._service._state.standby:
                await asyncio.sleep(0.25)
                continue
            try:
                await self._service.run_autoplay_step()
            except Exception as exc:
                self._service._mark_loop_error(exc)
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(self._service._cfg_action_interval())

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
            task.cancel()
        self._autoplay_task = None
        self._poll_task = None
        self._event_task = None
        self._stop_owner_loop()


def _event_plan_signature(summary_context: dict[str, Any]) -> str:
    """Stable fingerprint of the event-room state an LLM advice call depends on.

    Reuse the cached event_llm_scores while the event options + player resources are unchanged, so a
    repeated tick does not re-consult the LLM for the same event position.
    """
    payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
    frozen = json.dumps(
        {
            "event_options": payload.get("event_options"),
            "current_hp": payload.get("current_hp"),
            "max_hp": payload.get("max_hp"),
            "gold": payload.get("gold"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(frozen.encode("utf-8")).hexdigest()


def _combat_plan_signature(snapshot: dict[str, Any]) -> str:
    """Turn-stable fingerprint for WHOLE-TURN caching: 只按 回合(run_id+turn) 键。

    GET /solver/plan 每次调用都会重跑 CombatSolver 搜索；插件现在"回合开始取一次、整回合复用"
    （见 solver_step_index），所以签名在回合内不变，进入新回合才变化从而重查。
    注意：不能把整局战斗状态放进签名——那每出一张牌就变，起不到缓存作用。
    """
    raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
    run_id = str(raw.get("run_id") or run.get("run_id") or run.get("runId") or run.get("id") or "")
    turn = raw.get("turn")
    if turn is None:
        combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else {}
        turn = combat.get("turn")
    return hashlib.sha256(f"{run_id}|turn:{turn}".encode("utf-8")).hexdigest()


def _current_turn_steps(solver_plan: dict[str, Any]) -> list[dict[str, Any]] | None:
    line = solver_plan.get("line") if isinstance(solver_plan.get("line"), list) else None
    if not line or not isinstance(line[0], dict):
        return None
    steps = line[0].get("steps") if isinstance(line[0].get("steps"), list) else None
    return steps if isinstance(steps, list) else None


__all__ = ["STS2LoopRunner"]
