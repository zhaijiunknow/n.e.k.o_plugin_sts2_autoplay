from __future__ import annotations

import asyncio
import os
from collections import deque
from hashlib import sha1
from random import random
from time import time
from typing import Any, Callable

from .action_engine import STS2ActionEngine
from .action_registry import STS2ActionRegistry
from .candidate_generator import STS2CandidateGenerator
from .catgirl_bridge import STS2CatgirlBridge
from .catgirl_llm import CatgirlCommentGenerator, EventAdviceGenerator
from .companion_evaluator import STS2CompanionEvaluator
from .danmu_bridge import STS2DanmuBridge
from .danmu_events import DanmuEventTracker
from .danmu_spire import _load_rules, _truncated_normal, burst_profile, match_events, pick_rule_burst
from .danmu_text import build_viewer_danmu, pick_ambient_bucket
from .heuristic_planner import STS2HeuristicPlanner
from .instructions import normalize_guidance_instruction
from .loop_runner import STS2LoopRunner
from .mode_controller import STS2ModeController
from .neko_interface import STS2NekoInterface
from .preference_extractors import STS2PreferenceExtractor
from .preference_store import STS2PreferenceStore
from .runtime_state import STS2RuntimeState
from .situation_summary_engine import STS2SituationSummaryEngine
from .state_machine import STS2StateMachine
from .strategy_repository import STS2StrategyRepository
from .summary_context_builder import STS2SummaryContextBuilder
from .transport_client import STS2TransportClient

# 「当前游戏信息状态」面板：状态推送最小间隔（防刷爆 SSE 有界队列）/ 心跳间隔（空闲时也定期刷新）
STATUS_PUSH_MIN_INTERVAL = 2.0
STATUS_PUSH_HEARTBEAT = 5.0

# 弹幕：每次触发事件的发射条数按正态分布抽样（中值 10，不做密度/最大值门控）
_DANMU_EMIT_MEAN = 10
_DANMU_EMIT_DEVIATION = 3
_DANMU_EMIT_MIN = 1
_DANMU_EMIT_MAX = 20


class STS2AutoplayService:
    def __init__(self, logger: Any, status_reporter: Callable[[dict[str, Any]], None], frontend_notifier: Callable[..., Any] | None = None, *, sdk_bus: Any = None, sdk_ctx: Any = None, i18n: Any = None, danmu_bridge: Any = None) -> None:
        self.logger = logger
        self._report_status = status_reporter
        self._frontend_notifier = frontend_notifier
        self._sdk_bus = sdk_bus
        self._sdk_ctx = sdk_ctx
        self._i18n = i18n
        self._danmu_bridge = danmu_bridge if danmu_bridge is not None else STS2DanmuBridge(self.logger)
        self._last_danmu_payload: dict[str, Any] | None = None
        self._danmu_seen: deque[str] = deque(maxlen=6)
        # 快照 diff 事件流：每次 refresh 出事件 → 规则弹幕（独立于 should_sync 闸门）
        self._danmu_tracker = DanmuEventTracker(self.logger)
        self._danmu_density = 100  # 词条密度（50-200%），configure 可覆盖
        self._danmu_burst_target = 10  # 发射目标条数（区域容量×填满比例×2），configure 覆盖
        self._scrolling_delay_scale = 0.5  # 横向弹幕延迟缩放（顶部弹幕不缩放），configure 覆盖
        self._danmu_ambient_enabled = True  # 场景氛围弹幕（战斗/奖励/商店等进入时填充），configure 覆盖
        # 「当前游戏信息状态」面板：触发计数 + 状态推送节流状态
        self._trigger_counts: dict[str, int] = {}
        self._last_run_id: str = ""
        self._last_status_sig: str = ""
        self._last_status_push_at: float = 0.0
        self._trigger_names: list[str] = list(_load_rules().keys())
        # 猫娘 LLM 点评生成（catgirl 弹幕真内容来源），节流状态
        self._catgirl_llm = CatgirlCommentGenerator(self.logger)
        self._catgirl_llm_enabled = True
        self._catgirl_llm_inflight = False
        self._last_catgirl_llm_at = 0.0
        self._catgirl_llm_interval = 10.0
        # 事件房 LLM 建议（与 heuristic 融合），默认开
        self._event_advice = EventAdviceGenerator(self.logger)
        self._event_llm_enabled = True
        self._event_llm_weight = 0.5
        self._client: STS2TransportClient | None = None
        self._cfg: dict[str, Any] = {}
        self._state = STS2RuntimeState()
        self._state_machine = STS2StateMachine()
        self._summary_builder = STS2SummaryContextBuilder()
        self._summary_engine = STS2SituationSummaryEngine(self._i18n)
        self._companion_evaluator = STS2CompanionEvaluator(self._i18n)
        self._catgirl_bridge = STS2CatgirlBridge(i18n=self._i18n, source_id="sts2_autoplay")
        self._preference_store = STS2PreferenceStore()
        self._preference_extractor = STS2PreferenceExtractor()
        self._strategy_repository = STS2StrategyRepository(
            logger,
            self._preference_store,
            default_strategy="defect",
        )
        self._mode_controller = STS2ModeController("program")
        self._planner = STS2HeuristicPlanner(logger)
        self._candidate_generator = STS2CandidateGenerator(self._planner)
        self._action_registry = STS2ActionRegistry()
        self._action_engine = STS2ActionEngine(self._i18n)
        self._loop_runner = STS2LoopRunner(self)
        self.neko = STS2NekoInterface(self)

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    async def startup(self, cfg: dict[str, Any]) -> dict[str, Any]:
        try:
            self.logger.info("[sts2_code_version] 20260521_companion_eval_debug")
        except Exception:
            pass
        self._cfg = dict(cfg)
        if self._danmu_bridge is not None:
            self._danmu_bridge.enabled = bool(self._cfg.get("danmu_overlay_enabled", True))
            self._danmu_bridge.dedup_enabled = bool(self._cfg.get("danmu_dedup_enabled", False))
            top_mode = str(self._cfg.get("danmu_top_mode", "standard") or "standard").strip().lower()
            if top_mode in ("none", "standard", "all"):
                self._danmu_bridge.top_mode = top_mode
        if self._danmu_tracker is not None and "danmu_multiplayer_enabled" in self._cfg:
            # 配置指定多人模式（否则 tracker 从快照自动检测）
            self._danmu_tracker.set_multiplayer(bool(self._cfg.get("danmu_multiplayer_enabled", False)))
        # 词条密度（50-200%，对齐 mod），默认 100%
        try:
            self._danmu_density = max(50, min(200, int(self._cfg.get("danmu_density", 100) or 100)))
        except (TypeError, ValueError):
            self._danmu_density = 100
        # 弹幕密度 = 区域容量 × 填满比例 × 2（延迟补偿）
        # 容量 = 轨道数（区域高度/行高）× 每轨条数（窗口宽/平均弹幕宽）
        try:
            win_h = max(200, int(self._cfg.get("danmu_window_height", 1080) or 1080))
            win_w = max(200, int(self._cfg.get("danmu_window_width", 1920) or 1920))
            zone_pct = max(1, min(100, int(self._cfg.get("danmu_height_percent", 30) or 30)))
            font_size = max(1, int(self._cfg.get("danmu_font_size", 20) or 20))
            fill = max(0.05, min(1.0, float(self._cfg.get("danmu_zone_fill", 0.5) or 0.5)))
        except (TypeError, ValueError):
            win_h, win_w, zone_pct, font_size, fill = 1080, 1920, 30, 20, 0.5
        zone_h = max(0.0, win_h * (zone_pct / 100.0) - 130)
        line_h = max(30.0, float(font_size) * 1.6)
        lanes = max(1, min(20, int(zone_h / line_h)))
        avg_w = max(80.0, float(font_size) * 15.0)
        per_lane = max(1, int(win_w / avg_w))
        capacity = lanes * per_lane
        # 发射目标 = 容量 × 填满比例 × 2（弹幕延迟导致同刻在屏约一半，需双倍发射）
        self._danmu_burst_target = max(1, int(capacity * fill * 2))
        # 横向弹幕延迟缩放（0-1，越小横向出现越快；顶部弹幕不受影响）
        try:
            # 横向弹幕延迟缩放（0-3）：<1 更快更密，>1 更慢更稀；顶部弹幕不缩放
            self._scrolling_delay_scale = max(0.0, min(3.0, float(self._cfg.get("danmu_scrolling_delay_scale", 1.0) or 1.0)))
        except (TypeError, ValueError):
            self._scrolling_delay_scale = 0.5
        # 场景氛围弹幕开关
        self._danmu_ambient_enabled = bool(self._cfg.get("danmu_ambient_enabled", True))
        # 猫娘 LLM 点评：开关 + 最小生成间隔（节流）
        self._catgirl_llm_enabled = bool(self._cfg.get("catgirl_llm_enabled", True))
        try:
            self._catgirl_llm_interval = max(2.0, float(self._cfg.get("catgirl_llm_min_interval_seconds", 10.0) or 10.0))
        except (TypeError, ValueError):
            self._catgirl_llm_interval = 10.0
        # 事件房 LLM 建议：开关 + 融合权重（0=纯 heuristic，1=纯 LLM）
        self._event_llm_enabled = bool(self._cfg.get("event_llm_enabled", True))
        try:
            self._event_llm_weight = max(0.0, min(1.0, float(self._cfg.get("event_llm_weight", 0.5) or 0.5)))
        except (TypeError, ValueError):
            self._event_llm_weight = 0.5
        base_url = self._resolve_base_url()
        self._state.base_url = base_url
        self._apply_control_mode("program")
        self._client = STS2TransportClient(
            base_url=base_url,
            connect_timeout=float(self._cfg.get("connect_timeout_seconds", 5) or 5),
            request_timeout=float(self._cfg.get("request_timeout_seconds", 15) or 15),
        )
        startup_result = {"connected": False, "companion_mode_enabled": False}

        try:
            await self.health_check()
            companion_enabled = bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True)))
            if companion_enabled:
                self.set_companion_mode(True)
                await self.refresh_state(trigger_sync=True)
                startup_result["companion_mode_enabled"] = True
            else:
                self._sync_background_polling()
                await self.refresh_state(trigger_sync=True)
            startup_result["connected"] = True
            if bool(self._cfg.get("autoplay_on_start", False)) and not self._state.standby:
                self.start_autoplay()
        except Exception as exc:
            self._state.transport_state = "disconnected"
            self._state.last_error = str(exc)
            self._state.consecutive_errors += 1
            self._emit_status()
            return startup_result
        return startup_result

    def _resolve_base_url(self) -> str:
        env_base_url = str(os.environ.get("STS2_API_BASE_URL") or "").strip()
        if env_base_url:
            return env_base_url.rstrip("/")

        env_port = str(os.environ.get("STS2_API_PORT") or "").strip()
        if env_port:
            try:
                port = int(env_port)
            except ValueError:
                port = 0
            if 0 < port <= 65535:
                return f"http://127.0.0.1:{port}"

        configured = str(self._cfg.get("base_url") or self._state.base_url).strip()
        return configured.rstrip("/") if configured else self._state.base_url.rstrip("/")

    async def shutdown(self) -> None:
        await self._loop_runner.stop_background()
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._catgirl_llm is not None:
            await self._catgirl_llm.shutdown()
        if self._event_advice is not None:
            await self._event_advice.shutdown()
        self._state.transport_state = "disconnected"
        self._state.autoplay_state = "idle"
        self._emit_status()

    async def health_check(self) -> dict[str, Any]:
        client = self._require_client()
        health = await client.health()
        self._state.transport_state = "connected"
        self._state.last_error = ""
        self._state.touch_success()
        self._emit_status()
        message = self.t("status.connected", default="STS2-Agent 已连接: {base_url}", base_url=self._state.base_url)
        return {"status": "connected", "message": message, "summary": message, "health": health}

    async def refresh_state(self, *, trigger_sync: bool = False) -> dict[str, Any]:
        previous_snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        tick_result = await self._loop_runner.tick()
        self._state.raw_state = tick_result["raw_state"]
        self._state.raw_actions = tick_result["raw_actions"]
        self._state.snapshot = tick_result["snapshot"]
        player_operation_observation = self._observe_companion_player_operation(previous_snapshot, self._state.snapshot)
        if player_operation_observation:
            self._state.latest_player_operation_observation = dict(player_operation_observation)
            self._state.remember_companion_player_op(player_operation_observation)
        else:
            self._state.latest_player_operation_observation = {}
        self._cfg["character_strategy"] = self._strategy_repository.strategy_for_snapshot(self._state.snapshot)
        self._state.transport_state = "connected"
        self._state.last_error = ""
        self._state.touch_poll()
        self._state.touch_success()
        self._remember_snapshot_metadata()
        self._update_run_intent()
        # 快照 diff 事件流弹幕：每次 tick 都跑，不受 trigger_sync / should_sync 闸门限制
        self._emit_danmu_events()
        if trigger_sync:
            self._deliver_catgirl_sync(self._state.snapshot)
        self._emit_status()
        message = self.t("status.refreshed", default="已刷新状态，screen={screen}", screen=self._state.snapshot.get("screen", "unknown"))
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "snapshot": self._state.snapshot,
        }

    def _rebuild_companion_snapshot(self, snapshot: dict[str, Any]) -> None:
        if not isinstance(snapshot, dict):
            return
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else self._state_machine.classify(snapshot)
        snapshot["classification"] = classification
        summary_context = self._summary_builder.build(snapshot, runtime_state=self._state)
        snapshot["summary_context"] = summary_context
        situation_summary = self._summary_engine.summarize(summary_context)
        snapshot["situation_summary"] = situation_summary
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        companion_evaluation = self._companion_evaluator.evaluate(
            summary_context=summary_context,
            situation_summary=situation_summary,
            strategy_context=strategy_context,
            runtime_state=self._state,
        )
        action_frame = self._state.latest_action_frame if isinstance(self._state.latest_action_frame, dict) else {}
        if action_frame:
            companion_evaluation["action_frame"] = dict(action_frame)
        snapshot["companion_evaluation"] = companion_evaluation
        try:
            self.logger.info(
                "[sts2_companion_eval] trigger=%s should_comment=%s turn_key=%s scene_key=%s eval_key=%s summary_kind=%s",
                companion_evaluation.get("trigger"),
                companion_evaluation.get("should_comment"),
                companion_evaluation.get("turn_key"),
                companion_evaluation.get("scene_key"),
                companion_evaluation.get("evaluation_key"),
                companion_evaluation.get("summary_kind"),
            )
        except Exception:
            pass
        snapshot["catgirl_sync"] = self._catgirl_bridge.build_sync_packet(snapshot, standby=self._state.standby)

    async def run_autoplay_step(self) -> dict[str, Any]:
        if self._state.standby:
            message = self.t("autoplay.standby_blocked", default="当前处于 standby 模式，不执行动作。")
            return {"status": "idle", "message": message, "summary": message}
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        if self._should_rebuild_operation(snapshot):
            await self.refresh_state(trigger_sync=True)
            snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            message = self.t("autoplay.no_planned_operation", default="当前没有可执行的规划动作。")
            return {"status": "idle", "message": message, "summary": message}
        result = await self.execute_operation(operation)
        if result.get("status") == "ok":
            self._state.step_count += 1
        return result


    async def execute_operation(self, operation: dict[str, Any] | None) -> dict[str, Any]:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        before_summary = self._summary_builder.build_snapshot_summary(snapshot) if snapshot else {}
        client = self._require_client()
        result = await self._action_engine.execute(client, snapshot, operation)
        if result.get("status") == "ok":
            self._state.touch_action()
            op = result.get("operation") if isinstance(result.get("operation"), dict) else {}
            self._state.last_decision_source = str(op.get("source") or "")
            self._state.last_decision_reason = str(op.get("reason") or "")
            self._consume_guidance(op)
            self._remember_plugin_action_marker(op, snapshot)
            action_seed = {
                "action_type": str(op.get("action_type") or ""),
                "action_kwargs": dict(op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}),
                "decision_source": self._state.last_decision_source,
                "decision_reason": self._state.last_decision_reason,
                "created_at": time(),
            }
            self._state.latest_action_frame = dict(action_seed)
            await self.refresh_state(trigger_sync=True)
            after_snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            after_summary = self._summary_builder.build_snapshot_summary(after_snapshot) if after_snapshot else {}
            action_delta = self._summary_engine.compute_delta(before_summary, after_summary, source="action_paired")
            action_frame = {
                "before": dict(before_summary),
                "after": dict(after_summary),
                "delta": dict(action_delta),
                **action_seed,
                "step_count": self._state.step_count + 1,
            }
            self._state.remember_action_frame(action_frame)
            self._state.remember_decision_memory(
                {
                    "screen": str(after_snapshot.get("screen") or before_summary.get("screen") or "unknown"),
                    "action_type": action_frame["action_type"],
                    "decision_reason": action_frame["decision_reason"],
                    "decision_source": action_frame["decision_source"],
                    "delta": dict(action_delta),
                    "step_count": action_frame["step_count"],
                }
            )
            summary_context = self._summary_builder.build(after_snapshot, runtime_state=self._state)
            self._state.snapshot["summary_context"] = summary_context
            self._state.snapshot["situation_summary"] = self._summary_engine.summarize(summary_context)
        return result

    async def get_status(self) -> dict[str, Any]:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        mode = snapshot.get("mode") if isinstance(snapshot.get("mode"), dict) else self._mode_controller.describe(self._state.control_mode)
        planned_operation = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else None
        agent_operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        executable_operation = snapshot.get("executable_operation") if isinstance(snapshot.get("executable_operation"), dict) else None
        agent_packet = snapshot.get("agent_packet") if isinstance(snapshot.get("agent_packet"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        summary = (
            f"transport={self._state.transport_state} | "
            f"screen={snapshot.get('screen', 'unknown')} | "
            f"class={classification.get('screen_class', 'unknown')} | "
            f"mode={mode.get('mode', 'unknown')} | "
            f"autoplay={self._state.autoplay_state}"
        )
        return {
            "summary": summary,
            "message": summary,
            "server": {
                "state": self._state.transport_state,
                "base_url": self._state.base_url,
                "last_error": self._state.last_error,
                "last_success_at": self._state.last_success_at,
                "last_poll_at": self._state.last_poll_at,
                "consecutive_errors": self._state.consecutive_errors,
            },
            "run": {
                "screen": snapshot.get("screen", "unknown"),
                "floor": snapshot.get("floor", 0),
                "act": snapshot.get("act", 0),
                "in_combat": snapshot.get("in_combat", False),
                "step_count": self._state.step_count,
                "snapshot_signature": self._state.snapshot_signature,
            },
            "autoplay": {
                "state": self._state.autoplay_state,
                "standby": self._state.standby,
                "control_mode": self._state.control_mode,
                "pause_reason": self._state.pause_reason,
                "stop_reason": self._state.stop_reason,
                "is_polling": self._loop_runner.is_polling(),
                "is_autoplaying": self._loop_runner.is_autoplaying(),
                "last_action_at": self._state.last_action_at,
                "guidance_generation": self._state.guidance_generation,
                "pending_guidance": list(self._state.pending_guidance),
                "interrupt_requested": self._state.interrupt_requested,
                "interrupt_reason": self._state.interrupt_reason,
            },
            "companion_mode": {
                "enabled": bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True))),
                "reporting_enabled": bool(self._cfg.get("neko_reporting_enabled", True)),
                "commentary_enabled": bool(self._cfg.get("neko_commentary_enabled", True)),
                "commentary_probability": float(self._cfg.get("neko_commentary_probability", 0.65) or 0.65),
                "critical_commentary_always": bool(self._cfg.get("neko_critical_commentary_always", True)),
                "latest_player_operation_observation": dict(self._state.latest_player_operation_observation),
            },
            "classification": classification,
            "summary_context": summary_context,
            "strategy_context": strategy_context,
            "mode": mode,
            "planned_operation": planned_operation,
            "agent_operation": agent_operation,
            "executable_operation": executable_operation,
            "agent_packet": agent_packet,
            "situation_summary": situation_summary,
            "catgirl_sync": catgirl_sync,
            "preference_domains": list(self._preference_store.export_all().keys()),
            "recent_deliveries": list(self._state.recent_deliveries),
            "latest_sync_packet": dict(self._state.latest_sync_packet),
        }

    async def get_snapshot(self) -> dict[str, Any]:
        if not self._state.snapshot:
            await self.refresh_state(trigger_sync=True)
        snapshot = self._state.snapshot
        screen = snapshot.get("screen", "unknown") if isinstance(snapshot, dict) else "unknown"
        message = self.t("status.snapshot", default="当前快照：screen={screen}", screen=screen)
        return {"status": "ok", "message": message, "summary": message, "snapshot": snapshot}

    async def execute_planned_operation(self) -> dict[str, Any]:
        return await self.run_autoplay_step()

    def start_autoplay(self) -> dict[str, Any]:
        if self._state.standby:
            self._state.autoplay_state = "standby"
            message = self.t("autoplay.start_blocked_standby", default="当前处于 standby 模式，无法启动自动运行。")
            return {"status": "idle", "message": message, "summary": message}
        self._state.autoplay_state = "running"
        self._state.pause_reason = ""
        self._state.stop_reason = ""
        self._sync_background_polling()
        self._loop_runner.start_autoplay()
        self._emit_status()
        message = self.t("autoplay.started", default="已启动尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def pause_autoplay(self, reason: str = "user") -> dict[str, Any]:
        self._state.autoplay_state = "paused"
        self._state.pause_reason = reason
        self._sync_background_polling()
        self._emit_status()
        message = self.t("autoplay.paused", default="已暂停尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message, "pause_reason": reason}

    def resume_autoplay(self) -> dict[str, Any]:
        if self._state.standby:
            message = self.t("autoplay.resume_blocked_standby", default="当前处于 standby 模式，不能恢复自动运行。")
            return {"status": "idle", "message": message, "summary": message}
        self._state.autoplay_state = "running"
        self._state.pause_reason = ""
        self._sync_background_polling()
        self._loop_runner.start_autoplay()
        self._emit_status()
        message = self.t("autoplay.resumed", default="已恢复尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def stop_autoplay(self, reason: str = "manual") -> dict[str, Any]:
        self._state.autoplay_state = "standby" if self._state.standby else "idle"
        self._state.stop_reason = reason
        self._state.pause_reason = ""
        self._sync_background_polling()
        self._emit_status()
        message = self.t("autoplay.stopped", default="已停止尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message, "stop_reason": reason}

    def set_standby(self, standby: bool) -> dict[str, Any]:
        normalized = self._apply_control_mode("standby" if standby else "program")
        return self._mode_controller.describe(normalized) | {"mode": normalized}

    def set_companion_mode(self, enabled: bool) -> dict[str, Any]:
        if not enabled and self._loop_runner.is_polling() and not self._loop_runner.is_autoplaying() and hasattr(self.logger, "info"):
            try:
                self.logger.info("[sts2_companion] disabling companion while polling active; keeping state refresh path quiet during teardown")
            except Exception:
                pass
        self._cfg["companion_mode_enabled"] = bool(enabled)
        self._cfg["neko_reporting_enabled"] = bool(enabled)
        self._cfg["neko_commentary_enabled"] = bool(enabled)
        if not enabled:
            self._state.latest_player_operation_observation = {}
            self._state.last_companion_scene_key = ""
            self._state.last_companion_turn_key = ""
            self._state.last_companion_evaluation_key = ""
            self._state.last_companion_combat_comment_key = ""
            self._state.last_companion_player_op_fingerprint = ""
            self._state.latest_sync_packet = {}
        message = self.t("companion.enabled", default="已开启陪玩模式。") if enabled else self.t("companion.disabled", default="已关闭陪玩模式。")
        self._emit_status()
        if enabled:
            self._sync_background_polling()
            self._push_companion_message()
        else:
            self._sync_background_polling()
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "enabled": bool(enabled),
            "reporting_enabled": bool(self._cfg.get("neko_reporting_enabled", False)),
            "commentary_enabled": bool(self._cfg.get("neko_commentary_enabled", False)),
        }

    async def apply_user_override_safely(self, instruction: str, *, source: str = "user") -> dict[str, Any]:
        was_running = self._state.autoplay_state == "running"
        was_paused = self._state.autoplay_state == "paused"
        pause_result: dict[str, Any] | None = None
        if was_running:
            pause_result = self.pause_autoplay(reason="apply_user_override")
            if pause_result.get("status") != "ok":
                return pause_result
        result = await self.neko.extract_and_upsert_preference(instruction, source=source)
        status = str(result.get("status") or "")
        if status != "ok":
            if was_running:
                message = str(result.get("message") or result.get("summary") or "策略更新失败。")
                result["message"] = message + " 自动游玩已暂停，请确认后再手动恢复。"
                result["summary"] = result["message"]
                result["autoplay_paused"] = True
            return result
        if was_running:
            message = str(result.get("message") or result.get("summary") or "策略已更新。")
            result["message"] = message + " 自动游玩已先暂停；如果要继续，请手动恢复自动游玩。"
            result["summary"] = result["message"]
            result["autoplay_paused"] = True
            result["pause_reason"] = pause_result.get("pause_reason") if isinstance(pause_result, dict) else "apply_user_override"
            return result
        if was_paused:
            message = str(result.get("message") or result.get("summary") or "策略已更新。")
            result["message"] = message + " 当前自动游玩仍处于暂停状态。"
            result["summary"] = result["message"]
            result["autoplay_paused"] = True
        return result

    def _require_client(self) -> STS2TransportClient:
        if self._client is None:
            raise RuntimeError(self.t("errors.client_not_started", default="STS2 client 未启动"))
        return self._client

    def _cfg_poll_interval(self, *, active: bool) -> float:
        key = "poll_interval_active_seconds" if active else "poll_interval_idle_seconds"
        return float(self._cfg.get(key, 1 if active else 3) or (1 if active else 3))

    def _cfg_action_interval(self) -> float:
        return float(self._cfg.get("action_interval_seconds", 1.5) or 1.5)

    def _cfg_companion_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("companion_push_probability", 0.2) or 0.2)
        except Exception:
            return 0.2
        return min(1.0, max(0.0, value))

    def _cfg_autoplay_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("autoplay_push_probability", 0.35) or 0.35)
        except Exception:
            return 0.35
        return min(1.0, max(0.0, value))

    def _should_allow_push_by_probability(self, *, companion_mode: bool) -> bool:
        probability = self._cfg_companion_push_probability() if companion_mode else self._cfg_autoplay_push_probability()
        if probability >= 1.0:
            return True
        if probability <= 0.0:
            return False
        return random() < probability

    def _apply_control_mode(self, mode: str) -> str:
        normalized = self._mode_controller.normalize(mode)
        previous_standby = self._state.standby
        self._state.control_mode = normalized
        if self._state.standby:
            self._state.autoplay_state = "standby"
        elif previous_standby and self._state.autoplay_state == "standby":
            self._state.autoplay_state = "idle"
        if previous_standby != self._state.standby:
            self._state.interrupt_requested = True
            self._state.interrupt_reason = "mode_change"
        return normalized

    def _queue_guidance(self, content: str, *, source: str = "neko") -> dict[str, Any]:
        self._state.guidance_generation += 1
        guidance = normalize_guidance_instruction(
            content,
            source=source,
            guidance_type="soft_guidance",
        )
        guidance["id"] = f"guidance-{self._state.guidance_generation}"
        guidance["generation"] = self._state.guidance_generation
        guidance["origin"]["generation"] = self._state.guidance_generation
        self._state.pending_guidance.append(guidance)
        self._state.pending_guidance = self._state.pending_guidance[-20:]
        self._state.interrupt_requested = True
        self._state.interrupt_reason = "guidance"
        return guidance

    def _consume_guidance(self, operation: dict[str, Any] | None) -> None:
        if not isinstance(operation, dict):
            return
        consumed_generation = int(operation.get("consumed_guidance_generation") or 0)
        consumed_ids = {
            str(item)
            for item in (operation.get("consumed_guidance_ids") if isinstance(operation.get("consumed_guidance_ids"), list) else [])
            if str(item)
        }
        if consumed_generation <= 0 and not consumed_ids:
            return
        self._state.last_consumed_guidance_generation = max(self._state.last_consumed_guidance_generation, consumed_generation)
        self._state.pending_guidance = [
            item for item in self._state.pending_guidance
            if int(item.get("generation") or 0) > self._state.last_consumed_guidance_generation and str(item.get("id") or "") not in consumed_ids
        ]
        self._state.interrupt_requested = False
        self._state.interrupt_reason = ""

    def _should_rebuild_operation(self, snapshot: dict[str, Any]) -> bool:
        if self._state.interrupt_requested:
            return True
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            return True
        decision_epoch = int(operation.get("decision_epoch") or 0)
        return decision_epoch < self._state.guidance_generation

    def _should_deliver_sync(self, catgirl_sync: dict[str, Any]) -> bool:
        fingerprint = str(catgirl_sync.get("fingerprint") or "")
        min_interval = float(catgirl_sync.get("min_interval_seconds") or 0.0)
        force = bool(catgirl_sync.get("force"))
        payload = catgirl_sync.get("payload") if isinstance(catgirl_sync.get("payload"), dict) else {}
        screen = str(payload.get("screen") or "")
        summary_kind = str(payload.get("summary_kind") or "")
        if force:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: force=true fingerprint=%s screen=%s summary_kind=%s",
                    fingerprint,
                    screen,
                    summary_kind,
                )
            except Exception:
                pass
            return True
        if self._state.interrupt_requested:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: interrupt_requested=true reason=%s fingerprint=%s",
                    self._state.interrupt_reason,
                    fingerprint,
                )
            except Exception:
                pass
            return True
        if fingerprint and fingerprint == self._state.last_sync_fingerprint:
            now = time()
            elapsed = now - self._state.last_sync_at if self._state.last_sync_at else None
            if self._state.last_sync_at and elapsed < min_interval:
                self._state.sync_repeat_count += 1
                try:
                    self.logger.info(
                        "[sts2_push_debug] should_deliver_sync deny: duplicate fingerprint=%s elapsed=%.3f min_interval=%.3f repeat_count=%s last_screen=%s last_summary_kind=%s",
                        fingerprint,
                        elapsed,
                        min_interval,
                        self._state.sync_repeat_count,
                        self._state.last_sync_screen,
                        self._state.last_sync_summary_kind,
                    )
                except Exception:
                    pass
                return False
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: duplicate fingerprint outside interval fingerprint=%s elapsed=%s min_interval=%.3f",
                    fingerprint,
                    f"{elapsed:.3f}" if elapsed is not None else "none",
                    min_interval,
                )
            except Exception:
                pass
        if screen != self._state.last_sync_screen or summary_kind != self._state.last_sync_summary_kind:
            try:
                self.logger.info(
                    "[sts2_push_debug] should_deliver_sync allow: scene_changed fingerprint=%s screen=%s->%s summary_kind=%s->%s",
                    fingerprint,
                    self._state.last_sync_screen,
                    screen,
                    self._state.last_sync_summary_kind,
                    summary_kind,
                )
            except Exception:
                pass
            return True
        try:
            self.logger.info(
                "[sts2_push_debug] should_deliver_sync allow: default fingerprint=%s screen=%s summary_kind=%s last_fingerprint=%s",
                fingerprint,
                screen,
                summary_kind,
                self._state.last_sync_fingerprint,
            )
        except Exception:
            pass
        return True

    def _mark_loop_error(self, exc: Exception) -> None:
        self._state.transport_state = "error"
        self._state.last_error = str(exc)
        self._state.consecutive_errors += 1
        self._state.autoplay_state = "error" if self._state.autoplay_state == "running" else self._state.autoplay_state
        self._emit_status()

    def _companion_mode_active(self) -> bool:
        return bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True))) and bool(self._cfg.get("neko_commentary_enabled", True))

    def _should_keep_polling(self) -> bool:
        return bool(self._cfg.get("companion_mode_enabled", False)) or self._state.autoplay_state in {"running", "paused"}

    def _sync_background_polling(self) -> None:
        if self._should_keep_polling():
            self._loop_runner.start_background()
            return
        self._loop_runner.stop_background_sync()

    def _remember_plugin_action_marker(self, operation: dict[str, Any], snapshot: dict[str, Any]) -> None:
        action_type = str(operation.get("action_type") or "")
        if not action_type:
            self._state.last_plugin_action_fingerprint = ""
            return
        screen = str(snapshot.get("screen") or "unknown")
        floor = snapshot.get("floor")
        act = snapshot.get("act")
        turn = self._snapshot_turn(snapshot)
        action_kwargs = operation.get("kwargs") if isinstance(operation.get("kwargs"), dict) else {}
        payload = f"{action_type}|{screen}|{floor}|{act}|{turn}|{sorted(action_kwargs.items())}"
        self._state.last_plugin_action_fingerprint = sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _observe_companion_player_operation(self, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> dict[str, Any] | None:
        if not self._companion_mode_active():
            return None
        if not isinstance(previous_snapshot, dict) or not previous_snapshot:
            return None
        if not isinstance(current_snapshot, dict) or not current_snapshot:
            return None

        previous_summary = self._summary_builder.build_snapshot_summary(previous_snapshot)
        current_summary = self._summary_builder.build_snapshot_summary(current_snapshot)
        delta = self._summary_engine.compute_delta(previous_summary, current_summary, source="player_operation")
        event_type = self._classify_player_operation(previous_snapshot, current_snapshot, previous_summary, current_summary, delta)
        if not event_type:
            return None

        observation = self._build_player_operation_observation(
            event_type=event_type,
            previous_snapshot=previous_snapshot,
            current_snapshot=current_snapshot,
            previous_summary=previous_summary,
            current_summary=current_summary,
            delta=delta,
        )
        if observation is None:
            return None
        if self._is_recent_plugin_action(observation):
            return None
        if not self._should_emit_player_operation(observation):
            return None
        return observation

    def _classify_player_operation(
        self,
        previous_snapshot: dict[str, Any],
        current_snapshot: dict[str, Any],
        previous_summary: dict[str, Any],
        current_summary: dict[str, Any],
        delta: dict[str, Any],
    ) -> str:
        previous_screen = str(previous_snapshot.get("screen") or "unknown")
        current_screen = str(current_snapshot.get("screen") or "unknown")
        previous_floor = self._safe_int(previous_snapshot.get("floor") if previous_snapshot.get("floor") is not None else previous_summary.get("floor"))
        current_floor = self._safe_int(current_snapshot.get("floor") if current_snapshot.get("floor") is not None else current_summary.get("floor"))
        previous_act = self._safe_int(previous_snapshot.get("act") if previous_snapshot.get("act") is not None else previous_summary.get("act"))
        current_act = self._safe_int(current_snapshot.get("act") if current_snapshot.get("act") is not None else current_summary.get("act"))
        previous_turn = self._safe_int(previous_summary.get("turn"))
        current_turn = self._safe_int(current_summary.get("turn"))
        previous_in_combat = bool(previous_summary.get("in_combat"))
        current_in_combat = bool(current_summary.get("in_combat"))

        if current_floor > previous_floor or current_act > previous_act:
            return "run_progressed"
        if not previous_in_combat and current_in_combat:
            return "combat_started"
        if previous_in_combat and not current_in_combat:
            return "combat_ended"
        if previous_screen != current_screen:
            target_class = str(current_snapshot.get("classification", {}).get("screen_class") if isinstance(current_snapshot.get("classification"), dict) else "")
            if target_class in {"reward", "selection", "shop", "rest"} or current_screen in {"event", "map", "shop", "rest", "reward"}:
                return "choice_committed"
            return "screen_transition"
        if current_in_combat and current_turn > previous_turn:
            return "combat_turn_advanced"
        if current_in_combat and self._combat_state_changed(delta):
            return "player_card_or_action_committed"
        if self._choice_state_changed(previous_snapshot, current_snapshot):
            return "choice_committed"
        return ""

    def _build_player_operation_observation(
        self,
        *,
        event_type: str,
        previous_snapshot: dict[str, Any],
        current_snapshot: dict[str, Any],
        previous_summary: dict[str, Any],
        current_summary: dict[str, Any],
        delta: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_screen = str(current_snapshot.get("screen") or "unknown")
        floor = self._safe_int(current_snapshot.get("floor") if current_snapshot.get("floor") is not None else current_summary.get("floor"))
        act = self._safe_int(current_snapshot.get("act") if current_snapshot.get("act") is not None else current_summary.get("act"))
        turn = self._safe_int(current_summary.get("turn"))
        scene_key = f"{act}:{floor}:{current_screen}:{turn if event_type.startswith('combat') else 0}"
        summary = self._render_player_operation_summary(event_type, previous_snapshot, current_snapshot, delta)
        if not summary:
            return None
        delta_text = str(delta.get("text") or "").strip()
        fingerprint_payload = f"{event_type}|{current_screen}|{floor}|{act}|{turn}|{summary}|{delta_text}"
        fingerprint = sha1(fingerprint_payload.encode("utf-8")).hexdigest()[:16]
        return {
            "event_type": event_type,
            "screen": current_screen,
            "floor": floor,
            "act": act,
            "turn": turn,
            "scene_key": scene_key,
            "summary": summary,
            "delta_text": delta_text,
            "fingerprint": fingerprint,
            "captured_at": time(),
            "should_comment": True,
            "source": "state_observer",
        }

    def _render_player_operation_summary(self, event_type: str, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any], delta: dict[str, Any]) -> str:
        previous_screen = str(previous_snapshot.get("screen") or "unknown")
        current_screen = str(current_snapshot.get("screen") or "unknown")
        delta_text = str(delta.get("text") or "").strip()
        if event_type == "combat_started":
            return self.t("companion.player_operation.combat_started", default="玩家进入了新的战斗。")
        if event_type == "combat_ended":
            return self.t("companion.player_operation.combat_ended", default="玩家刚结束这场战斗，局面已经切到后续结算。")
        if event_type == "combat_turn_advanced":
            return self.t("companion.player_operation.combat_turn_advanced", default="玩家已经推进到新的战斗回合。")
        if event_type == "player_card_or_action_committed":
            return delta_text or self.t("companion.player_operation.action_committed", default="玩家刚在战斗中完成了一步操作。")
        if event_type == "choice_committed":
            return self.t("companion.player_operation.choice_committed", default="玩家刚完成了一个关键选择，画面从 {previous_screen} 进入 {current_screen}。", previous_screen=previous_screen, current_screen=current_screen)
        if event_type == "run_progressed":
            return self.t("companion.player_operation.run_progressed", default="玩家推进了当前流程，楼层或章节发生了变化。")
        if event_type == "screen_transition":
            return self.t("companion.player_operation.screen_transition", default="玩家把画面从 {previous_screen} 切换到了 {current_screen}。", previous_screen=previous_screen, current_screen=current_screen)
        return delta_text

    def _is_recent_plugin_action(self, observation: dict[str, Any]) -> bool:
        if not self._state.last_action_at:
            return False
        if time() - self._state.last_action_at > 2.5:
            return False
        last_action_frame = self._state.latest_action_frame if isinstance(self._state.latest_action_frame, dict) else {}
        before = last_action_frame.get("before") if isinstance(last_action_frame.get("before"), dict) else {}
        after = last_action_frame.get("after") if isinstance(last_action_frame.get("after"), dict) else {}
        action_type = str(last_action_frame.get("action_type") or "")
        screen = str(observation.get("screen") or "")
        turn = self._safe_int(observation.get("turn"))
        observation_event = str(observation.get("event_type") or "")
        if observation_event == "player_card_or_action_committed" and action_type == "play_card":
            return False
        if screen and screen in {str(before.get("screen") or ""), str(after.get("screen") or "")}:
            if turn and turn in {self._safe_int(before.get("turn")), self._safe_int(after.get("turn"))}:
                return True
            if observation_event in {"combat_ended", "choice_committed", "screen_transition"}:
                return True
        return False

    def _should_emit_player_operation(self, observation: dict[str, Any]) -> bool:
        fingerprint = str(observation.get("fingerprint") or "")
        if not fingerprint:
            return False
        if fingerprint == self._state.last_companion_player_op_fingerprint:
            min_interval = 0.0 if str(observation.get("event_type") or "") in {"combat_ended", "choice_committed", "run_progressed"} else 4.0
            if self._state.last_companion_player_op_at and time() - self._state.last_companion_player_op_at < min_interval:
                return False
        return True

    def _combat_state_changed(self, delta: dict[str, Any]) -> bool:
        player_changes = delta.get("player_changes") if isinstance(delta.get("player_changes"), dict) else {}
        enemy_changes = delta.get("enemy_changes") if isinstance(delta.get("enemy_changes"), dict) else {}
        hand_changes = delta.get("hand_changes") if isinstance(delta.get("hand_changes"), dict) else {}
        return any(
            [
                self._safe_int(player_changes.get("energy_delta")) != 0,
                self._safe_int(player_changes.get("block_delta")) != 0,
                self._safe_int(enemy_changes.get("enemy_total_hp_delta")) != 0,
                self._safe_int(hand_changes.get("hand_count_delta")) != 0,
                bool(hand_changes.get("left_cards")),
            ]
        )

    def _choice_state_changed(self, previous_snapshot: dict[str, Any], current_snapshot: dict[str, Any]) -> bool:
        previous_actions = previous_snapshot.get("available_actions") if isinstance(previous_snapshot.get("available_actions"), list) else []
        current_actions = current_snapshot.get("available_actions") if isinstance(current_snapshot.get("available_actions"), list) else []
        previous_names = {str(action.get("type") or "") for action in previous_actions if isinstance(action, dict)}
        current_names = {str(action.get("type") or "") for action in current_actions if isinstance(action, dict)}
        if previous_names == current_names:
            return False
        interesting = {"choose_reward_card", "claim_reward", "choose_event_option", "choose_map_node", "choose_rest_option", "buy_card", "buy_relic", "buy_potion"}
        return bool(previous_names & interesting or current_names & interesting)

    def _snapshot_turn(self, snapshot: dict[str, Any]) -> int:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        return self._safe_int(raw_state.get("turn") if raw_state.get("turn") is not None else combat.get("turn"))

    def _safe_int(self, value: Any) -> int:
        try:
            if value is None:
                return 0
            return int(value)
        except Exception:
            return 0

    def _update_run_intent(self) -> None:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        hp = payload.get("current_hp")
        max_hp = payload.get("max_hp")
        hp_ratio = 0.0
        try:
            if max_hp not in (None, 0):
                hp_ratio = float(hp or 0) / float(max_hp)
        except Exception:
            hp_ratio = 0.0
        run_intent = {
            "strategy_name": strategy_context.get("strategy_name"),
            "screen": snapshot.get("screen"),
            "screen_class": snapshot.get("classification", {}).get("screen_class") if isinstance(snapshot.get("classification"), dict) else None,
            "hp_ratio": hp_ratio,
            "risk_posture": "preserve_hp" if hp_ratio < 0.45 else "growth",
            "floor": snapshot.get("floor"),
            "act": snapshot.get("act"),
        }
        self._state.run_intent = run_intent

    def _remember_snapshot_metadata(self) -> None:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        payload = f"{snapshot.get('screen')}|{snapshot.get('floor')}|{snapshot.get('act')}|{snapshot.get('in_combat')}|{snapshot.get('available_action_count')}"
        self._state.snapshot_signature = sha1(payload.encode("utf-8")).hexdigest()[:12]
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        self._state.last_summary = str(situation_summary.get("text") or "")
        planned = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else {}
        self._state.last_planner_type = str(planned.get("source") or "")
        agent_operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else {}
        self._state.last_decision_source = str(agent_operation.get("source") or self._state.last_decision_source)
        self._state.last_decision_reason = str(agent_operation.get("reason") or self._state.last_decision_reason)
        self._state.last_decision_generation = int(agent_operation.get("decision_epoch") or self._state.last_decision_generation)

    def _emit_danmu_events(self) -> None:
        """快照 diff 事件流：feed → 规则匹配 → 按强度分批推送弹幕。

        事件流独立于 chat 点评节奏（should_sync），每次快照都跑。
        发射机制对齐 mod：按规则强度抽 2-12 条（保证 1 条角色弹幕）、
        按强度置顶概率（narration 才置顶）、延迟分布分批推出。
        """
        if self._danmu_bridge is None or not self._danmu_bridge.enabled:
            return
        try:
            events = self._danmu_tracker.feed(self._state.raw_state, self._state.snapshot)
            if events:
                # 氛围弹幕：场景进入（战斗/奖励/商店/火堆/事件）时填充当前屏幕词条
                if self._danmu_ambient_enabled:
                    self._emit_ambient(events)
                hits = match_events(events, self._danmu_tracker)
                # 触发计数：run 生命周期清零 + 逐 hit 累加（供「当前游戏信息状态」面板）
                self._update_trigger_counts(events, hits)
                for hit in hits:
                    try:
                        # 弹幕数量：正态分布抽样（中值 10），不做密度/最大值门控；顶部概率仍按强度。
                        # 不做延迟：全部立即推，前端按轨道互相避让（pickLane 全满丢弃）。
                        _, top_prob = burst_profile(hit.trigger, self._danmu_density)
                        emit_count = int(round(_truncated_normal(_DANMU_EMIT_MEAN, _DANMU_EMIT_DEVIATION, _DANMU_EMIT_MIN, _DANMU_EMIT_MAX)))
                        emit_count = max(_DANMU_EMIT_MIN, emit_count)
                        burst = pick_rule_burst(hit.trigger, hit.context, variant=hit.variant, count=emit_count)
                    except Exception:
                        burst = []
                        top_prob = 0.0
                    for phrase in burst:
                        placement = self._danmu_placement(phrase["style"], top_prob)
                        self._danmu_bridge.push_text(
                            phrase["text"],
                            style=phrase["style"],
                            placement=placement,
                        )
                # 事件订阅生成：弹幕事件触发 → 猫娘 LLM 也生成一条点评（仅受启用/可用/节流限制）
                self._maybe_emit_catgirl_llm_event()
            scene_changed = any(getattr(e, "type", "") == "scene_changed" for e in events)
            # 状态推送：场景切换时强制立即推（消除切换延迟），否则按节流/心跳
            self._maybe_push_game_status(force=scene_changed)
        except Exception as exc:  # 事件流失败不阻断主链路
            try:
                self.logger.debug("[sts2_danmu] 事件流弹幕失败: %s", exc)
            except Exception:
                pass

    def _update_trigger_counts(self, events: list[Any], hits: list[Any]) -> None:
        """触发计数：run 生命周期清零 + 逐 hit 累加（供「当前游戏信息状态」面板）。

        清零信号：run_started / run_ended 事件，或 raw_state 的 run_id 变化。
        （tracker 在 run_id 直接切换时不发 run_started，见 danmu_events._diff，
        因此这里同时用 run_id 变化兜底。）
        """
        etypes = [getattr(e, "type", "") for e in events]
        run_id = self._current_run_id(self._state.raw_state)
        if run_id != self._last_run_id or any(t in ("run_started", "run_ended") for t in etypes):
            self._trigger_counts = {}
        self._last_run_id = run_id
        for hit in hits:
            name = str(getattr(hit, "trigger", "") or "")
            if name:
                self._trigger_counts[name] = self._trigger_counts.get(name, 0) + 1

    @staticmethod
    def _current_run_id(raw_state: dict[str, Any]) -> str:
        """从 raw_state 提取 run_id（镜像 danmu_events._extract 的取法）。"""
        if not isinstance(raw_state, dict):
            return ""
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        return str(raw_state.get("run_id") or run.get("run_id") or "")

    def _build_game_status_data(self) -> dict[str, Any]:
        """组「当前游戏信息状态」载荷（game 信息 + 57 个条件名 + 触发计数）。"""
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        payload = situation_summary.get("payload") if isinstance(situation_summary.get("payload"), dict) else {}
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        # 金币真实来源在原始快照 run.gold（situation_summary.payload.player 只有 hp/block，没有 gold）
        raw_state = self._state.raw_state if isinstance(self._state.raw_state, dict) else {}
        run_raw = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        gold = run_raw.get("gold") if run_raw.get("gold") is not None else raw_state.get("gold")
        mode = snapshot.get("mode") if isinstance(snapshot.get("mode"), dict) else self._mode_controller.describe(self._state.control_mode)
        game = {
            "screen": str(snapshot.get("screen") or "unknown"),
            "screen_class": str(classification.get("screen_class") or "unknown"),
            "floor": snapshot.get("floor", 0),
            "act": snapshot.get("act", 0),
            "in_combat": bool(snapshot.get("in_combat", False)),
            "hp": player.get("current_hp"),
            "max_hp": player.get("max_hp"),
            "gold": gold,
            "turn": snapshot.get("turn") if snapshot.get("turn") is not None else payload.get("turn"),
            "summary_kind": str(situation_summary.get("kind") or ""),
            "strategy_name": str(strategy_context.get("strategy_name") or ""),
            "autoplay_state": str(self._state.autoplay_state),
            "transport_state": str(self._state.transport_state),
            "mode": dict(mode),
            "standby": bool(self._state.standby),
            "last_error": str(self._state.last_error or ""),
            "step_count": int(self._state.step_count),
        }
        # 弹幕条件判定的全部参数（_extract 特征 + tracker 内部计数），供监控面板调试
        try:
            params = self._danmu_tracker.features(self._state.raw_state, self._state.snapshot)
        except Exception:
            params = {}
        return {
            "game": game,
            "params": params,
            "trigger_names": list(self._trigger_names),
            "triggers": dict(self._trigger_counts),
        }

    def _status_signature(self, data: dict[str, Any]) -> str:
        """状态变化签名：参与展示的字段 + 触发计数 + 判定参数标量计数。用于节流判断。"""
        g = data.get("game", {})
        keys = (
            "screen", "screen_class", "floor", "act", "in_combat", "hp", "max_hp",
            "gold", "turn", "summary_kind", "strategy_name", "autoplay_state",
            "transport_state", "standby", "last_error",
        )
        parts = [str(g.get(k, "")) for k in keys]
        counts = ",".join(f"{k}={v}" for k, v in sorted((data.get("triggers") or {}).items()))
        # 判定参数中的标量计数变化也触发刷新（大 dict 如 deck_counts 交给心跳覆盖）
        params = data.get("params", {})
        param_keys = (
            "combat_turn_plays", "shop_enter_gold", "no_damage_streak", "upgrade_streak",
            "potion_used_in_combat", "combat_turn", "combat_is_first_turn",
            "combat_damage_count", "idle_ticks", "elite_combat", "big_deck_triggered",
            "multiplayer",
        )
        param_parts = [f"{k}={params.get(k)}" for k in param_keys]
        return "|".join(parts + [counts] + param_parts)

    def _maybe_push_game_status(self, *, force: bool = False) -> None:
        """节流推送一次 game_status 事件到 SSE（供「当前游戏信息状态」面板）。

        推送条件：状态签名变化 **或** 心跳到期（5s）；并且距上次推送 ≥ 最小间隔（2s）。
        force=True（如场景切换）时跳过最小间隔立即推，消除切换延迟。
        """
        if self._danmu_bridge is None or not self._danmu_bridge.enabled:
            return
        data = self._build_game_status_data()
        sig = self._status_signature(data)
        now = time()
        changed = sig != self._last_status_sig
        heartbeat_due = (now - self._last_status_push_at) >= STATUS_PUSH_HEARTBEAT
        min_ok = (now - self._last_status_push_at) >= STATUS_PUSH_MIN_INTERVAL
        if not changed and not heartbeat_due and not force:
            return
        if not min_ok and not force:
            return  # 变了但刚推过：不更新 sig，下一 tick 仍视为 changed → 2s 后补推
        self._last_status_sig = sig
        self._last_status_push_at = now
        self._danmu_bridge.push_status(data=data)

    @staticmethod
    def _danmu_placement(style: str, top_prob: float) -> str:
        """按强度顶部概率决定 placement（对齐 mod：只 narration 置顶）。"""
        if style == "catgirl":
            return "scrolling"
        if top_prob > 0 and random() < top_prob:
            return "top"
        return "scrolling"

    # 场景进入事件 → 氛围分桶（词库对应场景，抽中性词条）
    _AMBIENT_BUCKET = {
        "combat_started": "combat",
        "reward_opened": "reward",
        "shop_opened": "shop",
        "rest_opened": "rest",
        "event_opened": "event",
    }

    def _emit_ambient(self, events: list[Any]) -> None:
        """场景进入时推一条当前屏幕的中性氛围弹幕（词库分桶，避开特定场面词条）。"""
        if self._danmu_bridge is None or not self._danmu_bridge.enabled:
            return
        for ev in events:
            bucket = self._AMBIENT_BUCKET.get(getattr(ev, "type", ""))
            if not bucket:
                continue
            try:
                text = pick_ambient_bucket(bucket)
            except Exception:
                text = None
            if text:
                self._danmu_bridge.push_text(text, style="narration")

    def _maybe_emit_catgirl_llm(self, snapshot: dict[str, Any], companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> None:
        """catgirl 轨道：猫娘 LLM 生成点评（优先）或启发式 primary_message 兜底。

        - 启用 + LLM 可用 + 非生成中 + 距上次生成 ≥ 间隔 → 调度 LLM 生成（生成后推弹幕）
        - 否则推启发式文本（保持即时性，避免无弹幕）
        """
        kind = str(payload.get("summary_kind") or "")
        if not bool(companion_evaluation.get("should_comment", True)):
            self.logger.info("[sts2_catgirl_llm] skip=should_comment_false kind=%s", kind)
            return
        enabled = self._catgirl_llm_enabled
        llm_ok = self._catgirl_llm is not None and self._catgirl_llm.available
        if enabled and llm_ok:
            now = time()
            throttle_ok = (now - self._last_catgirl_llm_at) >= self._catgirl_llm_interval
            if not self._catgirl_llm_inflight and throttle_ok:
                self.logger.info("[sts2_catgirl_llm] schedule kind=%s screen=%s", kind, payload.get("screen"))
                self._catgirl_llm_inflight = True
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    self._catgirl_llm_inflight = False
                    self._push_catgirl_fallback(companion_evaluation, payload)
                    return
                loop.create_task(self._catgirl_llm_async(snapshot, payload))
                return
            self.logger.info("[sts2_catgirl_llm] skip inflight=%s throttle_ok=%s kind=%s", self._catgirl_llm_inflight, throttle_ok, kind)
            return
        self.logger.info("[sts2_catgirl_llm] fallback enabled=%s llm_ok=%s kind=%s", enabled, llm_ok, kind)
        self._push_catgirl_fallback(companion_evaluation, payload)

    def _maybe_emit_catgirl_llm_event(self) -> None:
        """事件订阅生成：弹幕事件触发时猫娘 LLM 生成一条点评。

        不受 should_comment 去重门控，仅受启用/可用/inflight/节流限制；
        快照有局面摘要才生成（避免无上下文乱说）。
        """
        if not self._catgirl_llm_enabled or self._catgirl_llm is None or not self._catgirl_llm.available:
            return
        now = time()
        if self._catgirl_llm_inflight or (now - self._last_catgirl_llm_at) < self._catgirl_llm_interval:
            return
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        situation = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        summary_text = str(situation.get("text") or "").strip()
        if not summary_text:
            return
        self.logger.info("[sts2_catgirl_llm] event schedule kind=%s", situation.get("kind"))
        self._catgirl_llm_inflight = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._catgirl_llm_inflight = False
            return
        payload: dict[str, Any] = {
            "message": summary_text,
            "summary_kind": str(situation.get("kind") or ""),
            "screen": snapshot.get("screen"),
        }
        loop.create_task(self._catgirl_llm_async(snapshot, payload))

    async def _catgirl_llm_async(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> None:
        """异步生成猫娘点评并推 catgirl 弹幕；失败静默（兜底留给下一次）。"""
        kind = str(payload.get("summary_kind") or "")
        try:
            situation = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
            summary_text = str(payload.get("message") or situation.get("text") or "").strip()
            self.logger.info("[sts2_catgirl_llm] generate kind=%s text=%s", kind, summary_text[:80])
            text = await self._catgirl_llm.generate(
                summary_text=summary_text,
                summary_kind=kind,
                payload=payload,
            )
            if text:
                self.logger.info("[sts2_catgirl_llm] result=%s", text)
            else:
                self.logger.info("[sts2_catgirl_llm] result=None (LLM 失败/空) kind=%s", kind)
            if text and self._danmu_bridge is not None and self._danmu_bridge.enabled:
                self._danmu_bridge.push_text(text, style="catgirl")
                self.logger.info("[sts2_catgirl_llm] pushed catgirl danmu")
            elif text:
                self.logger.info("[sts2_catgirl_llm] bridge disabled, not pushed")
        except Exception as exc:
            self.logger.info("[sts2_catgirl_llm] exception %s kind=%s", exc, kind)
        finally:
            self._catgirl_llm_inflight = False
            self._last_catgirl_llm_at = time()

    def _push_catgirl_fallback(self, companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> None:
        """兜底：推启发式 primary_message（原 catgirl 轨道文本来源）。"""
        if self._danmu_bridge is None or not self._danmu_bridge.enabled:
            return
        catgirl_text = str(
            companion_evaluation.get("primary_message") or payload.get("message") or ""
        ).strip()
        if catgirl_text:
            self._danmu_bridge.push_text(catgirl_text, style="catgirl")

    def _deliver_catgirl_sync(self, snapshot: dict[str, Any]) -> None:
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        payload = catgirl_sync.get("payload") if isinstance(catgirl_sync.get("payload"), dict) else {}
        companion_evaluation = payload.get("companion_evaluation") if isinstance(payload.get("companion_evaluation"), dict) else {}
        player_operation_observation = payload.get("player_operation_observation") if isinstance(payload.get("player_operation_observation"), dict) else {}
        try:
            self.logger.info(
                "[sts2_push_debug] deliver_catgirl_sync should_sync=%s should_comment=%s force=%s fingerprint=%s last_fingerprint=%s min_interval=%s payload_keys=%s summary_kind=%s sync_priority=%s companion_trigger=%s player_op=%s queue_only=%s ai_behavior=%s message_len=%s",
                catgirl_sync.get("should_sync"),
                companion_evaluation.get("should_comment") if companion_evaluation else catgirl_sync.get("should_comment"),
                catgirl_sync.get("force"),
                catgirl_sync.get("fingerprint"),
                self._state.last_sync_fingerprint,
                catgirl_sync.get("min_interval_seconds"),
                sorted(payload.keys()) if isinstance(payload, dict) else [],
                payload.get("summary_kind") if isinstance(payload, dict) else None,
                payload.get("sync_priority") if isinstance(payload, dict) else None,
                payload.get("companion_trigger") if isinstance(payload, dict) else None,
                player_operation_observation.get("event_type") if player_operation_observation else None,
                payload.get("queue_only") if isinstance(payload, dict) else None,
                payload.get("ai_behavior") if isinstance(payload, dict) else None,
                len(str(payload.get("message") or payload.get("summary") or "")),
            )
        except Exception:
            pass
        if not payload:
            return
        self._state.latest_sync_packet = dict(payload)
        notifier = self._frontend_notifier
        if not bool(catgirl_sync.get("should_sync")):
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=should_sync_false turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: should_sync false screen=%s summary_kind=%s sync_priority=%s",
                    payload.get("screen"),
                    payload.get("summary_kind"),
                    payload.get("sync_priority"),
                )
            except Exception:
                pass
            return
        # W-DANMU-001：陪玩点评推入弹幕 SSE 流（web / Qt 浮层）。
        # 放在 should_sync 闸门之后、should_comment 之前：弹幕不受 chat 点评节奏限制，
        # 每次可同步事件都推。双轨：
        #   1) catgirl 角色弹幕：猫娘说的点评（primary_message，带头像）
        #   2) narration 旁白弹幕：观众视角社区词条（见 danmu_text.py）
        if self._danmu_bridge is not None and self._danmu_bridge.enabled:
            # catgirl 轨道：猫娘 LLM 生成点评（优先）或启发式 primary_message 兜底
            self._maybe_emit_catgirl_llm(snapshot, companion_evaluation, payload)
            signature = self._danmu_screen_signature(payload)
            seen_before = bool(
                signature
                and signature in self._danmu_seen
                and (not self._danmu_seen or self._danmu_seen[-1] != signature)
            )
            danmu_payload = self._enrich_danmu_payload(payload)
            viewer = build_viewer_danmu(danmu_payload, self._last_danmu_payload, seen_before=seen_before)
            if viewer:
                self._danmu_bridge.push_text(viewer["text"], style=viewer["style"])
            self._last_danmu_payload = dict(payload)
            if signature:
                self._danmu_seen.append(signature)
        if companion_evaluation:
            if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] companion_eval trigger=%s should_comment=%s turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("trigger"),
                        companion_evaluation.get("should_comment"),
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_gate] should_comment=%s turn_key=%s last_turn_key=%s scene_key=%s eval_key=%s commentary=%s",
                        companion_evaluation.get("should_comment"),
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                        companion_evaluation.get("scene_key"),
                        companion_evaluation.get("evaluation_key"),
                        str(companion_evaluation.get("commentary") or "")[:120],
                    )
                except Exception:
                    pass
            if not bool(companion_evaluation.get("should_comment", True)):
                if str(companion_evaluation.get("trigger") or "") == "combat_turn":
                    try:
                        self.logger.info(
                            "[sts2_combat_turn_path] return=should_comment_false turn_key=%s last_turn_key=%s eval_key=%s",
                            companion_evaluation.get("turn_key"),
                            self._state.last_companion_turn_key,
                            companion_evaluation.get("evaluation_key"),
                        )
                    except Exception:
                        pass
                try:
                    self.logger.info(
                        "[sts2_push_debug] deliver_catgirl_sync skipped: companion should_comment false trigger=%s turn_key=%s scene_key=%s evaluation_key=%s commentary_len=%s",
                        companion_evaluation.get("trigger"),
                        companion_evaluation.get("turn_key"),
                        companion_evaluation.get("scene_key"),
                        companion_evaluation.get("evaluation_key"),
                        len(str(companion_evaluation.get("commentary") or "")),
                    )
                except Exception:
                    pass
                return
        if not self._should_deliver_sync(catgirl_sync):
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=should_deliver_sync_false turn_key=%s last_turn_key=%s fingerprint=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                        catgirl_sync.get("fingerprint"),
                    )
                except Exception:
                    pass
            print("[sts2_companion_sync:skip] reason=dedup_or_interval_gate")
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: _should_deliver_sync false fingerprint=%s last_fingerprint=%s last_sync_at=%s repeat_count=%s",
                    catgirl_sync.get("fingerprint"),
                    self._state.last_sync_fingerprint,
                    self._state.last_sync_at,
                    self._state.sync_repeat_count,
                )
            except Exception:
                pass
            return
        if not bool(catgirl_sync.get("force")) and not self._should_allow_push_by_probability(companion_mode=self._companion_mode_active()):
            try:
                self.logger.info(
                    "[sts2_push_debug] deliver_catgirl_sync skipped: probability_gate companion_mode=%s autoplay_state=%s companion_probability=%.3f autoplay_probability=%.3f",
                    self._companion_mode_active(),
                    self._state.autoplay_state,
                    self._cfg_companion_push_probability(),
                    self._cfg_autoplay_push_probability(),
                )
            except Exception:
                pass
            return
        if notifier is None:
            if str(companion_evaluation.get("trigger") or payload.get("trigger") or "") == "combat_turn":
                try:
                    self.logger.info(
                        "[sts2_combat_turn_path] return=frontend_notifier_missing turn_key=%s last_turn_key=%s",
                        companion_evaluation.get("turn_key"),
                        self._state.last_companion_turn_key,
                    )
                except Exception:
                    pass
            print("[sts2_companion_sync:skip] reason=frontend_notifier_missing")
            try:
                self.logger.info("[sts2_push_debug] deliver_catgirl_sync skipped: frontend_notifier missing")
            except Exception:
                pass
            return
        ai_behavior = str(payload.get("ai_behavior") or "respond")
        if self._companion_mode_active() and ai_behavior == "read":
            ai_behavior = "respond"
        push_scene_key = f"{payload.get('screen')}|{payload.get('summary_kind')}|{payload.get('trigger')}"
        push_reason = str(catgirl_sync.get("reason") or "")
        notifier(
            content=self._host_reply_text(str(payload.get("message") or payload.get("summary") or self.t("sync.default", default="尖塔局势已同步。"))),
            description="STS2 catgirl sync",
            metadata={
                "kind": "catgirl_sync",
                "screen": payload.get("screen"),
                "summary_kind": payload.get("summary_kind"),
                "trigger": payload.get("trigger"),
                "strategy": dict(payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {}),
                "player_operation": dict(payload.get("player_operation") if isinstance(payload.get("player_operation"), dict) else {}),
                "player": dict(payload.get("player") if isinstance(payload.get("player"), dict) else {}),
                "enemies": list(payload.get("enemies") if isinstance(payload.get("enemies"), list) else []),
                "cards": list(payload.get("cards") if isinstance(payload.get("cards"), list) else []),
            },
            priority=4,
            message_type="sts2_catgirl_sync",
            visibility=[],
            ai_behavior=ai_behavior,
        )
        trigger = str(companion_evaluation.get("trigger") or payload.get("trigger") or "")
        turn_key = str(companion_evaluation.get("turn_key") or "")
        scene_key = str(companion_evaluation.get("scene_key") or "")
        evaluation_key = str(companion_evaluation.get("evaluation_key") or "")
        if trigger == "combat_turn":
            try:
                self.logger.info(
                    "[sts2_combat_turn_commit] before trigger=%s turn_key=%s last_turn_key=%s eval_key=%s",
                    trigger,
                    turn_key,
                    self._state.last_companion_turn_key,
                    evaluation_key,
                )
            except Exception:
                pass
        if trigger == "combat_turn" and turn_key:
            self._state.last_companion_turn_key = turn_key
            self._state.last_companion_combat_comment_key = turn_key
            self._state.last_companion_scene_key = ""
            self._state.last_companion_evaluation_key = evaluation_key
            try:
                self.logger.info(
                    "[sts2_combat_turn_commit] after trigger=%s turn_key=%s last_turn_key=%s eval_key=%s",
                    trigger,
                    turn_key,
                    self._state.last_companion_turn_key,
                    self._state.last_companion_evaluation_key,
                )
            except Exception:
                pass
        elif trigger == "scene_entry" and scene_key:
            self._state.last_companion_scene_key = scene_key
            self._state.last_companion_evaluation_key = evaluation_key
        self._state.touch_sync()
        self._state.last_sync_fingerprint = str(catgirl_sync.get("fingerprint") or "")
        self._state.last_sync_screen = str(payload.get("screen") or "")
        self._state.last_sync_summary_kind = str(payload.get("summary_kind") or "")
        self._state.last_sync_reason = str(catgirl_sync.get("reason") or "")
        self._state.last_push_scene_key = push_scene_key
        self._state.last_push_reason = push_reason
        self._state.last_push_step_count = self._state.step_count
        self._state.last_push_at = time()
        self._state.sync_repeat_count = 0
        self._state.remember_delivery(
            {
                "kind": "catgirl_sync",
                "screen": payload.get("screen"),
                "summary_kind": payload.get("summary_kind"),
                "synced_at": time(),
            }
        )

    def _host_reply_text(self, text: str, *, limit: int = 30) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    def _danmu_screen_signature(self, payload: dict[str, Any]) -> str:
        """弹幕屏幕签名：战斗=screen+敌人名，其它=screen+summary_kind。用于重遇检测。"""
        if not isinstance(payload, dict):
            return ""
        screen = str(payload.get("screen") or "unknown")
        kind = str(payload.get("summary_kind") or "")
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        if screen.upper() in ("COMBAT", "BATTLE") or kind == "combat":
            names = ",".join(sorted(str(e.get("name") or "") for e in enemies if isinstance(e, dict)))
            return f"combat:{names}"
        return f"{screen}:{kind}"

    def _enrich_danmu_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """给弹幕生成器补充原始屏幕数据（奖励选牌/商店货品），供规则检测。"""
        enriched = dict(payload)
        screen = str(payload.get("screen") or "").upper()
        raw = self._state.raw_state if isinstance(self._state.raw_state, dict) else {}
        if screen in ("REWARD", "REWARDS", "SELECTION") and isinstance(raw.get("reward"), dict):
            enriched["_offers"] = dict(raw["reward"])
        elif screen in ("REWARD", "REWARDS", "SELECTION") and isinstance(raw.get("selection"), dict):
            enriched["_offers"] = dict(raw["selection"])
        if screen in ("SHOP", "STORE") and isinstance(raw.get("shop"), dict):
            enriched["_shop"] = dict(raw["shop"])
        return enriched

    def _push_companion_message(self) -> None:
        if not bool(self._cfg.get("companion_mode_enabled", False)):
            return
        notifier = self._frontend_notifier
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        companion_evaluation = snapshot.get("companion_evaluation") if isinstance(snapshot.get("companion_evaluation"), dict) else {}
        if companion_evaluation and not bool(companion_evaluation.get("should_comment", True)):
            return
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        strategy_name = str(strategy_context.get("strategy_name") or companion_evaluation.get("strategy_name") or self.t("companion.current_strategy", default="当前策略"))
        commentary = str(companion_evaluation.get("commentary") or "").strip()
        if not commentary:
            commentary = self.t("companion.enabled_default_commentary", default="陪玩已开启，我会给出简短建议。")
        content = self._host_reply_text(
            self.t("companion.enabled_announcement", default="陪玩模式已开启。{strategy_name}：{commentary}", strategy_name=strategy_name, commentary=commentary)
        )
        if notifier is not None:
            notifier(
                content=content,
                description="STS2 companion mode enabled",
                metadata={
                    "kind": "companion_mode_enabled",
                    "delivery_semantics": "passive",
                    "strategy_name": strategy_name,
                    "companion_evaluation": companion_evaluation,
                },
                priority=5,
                message_type="sts2_companion_mode_enabled",
                visibility=["chat"],
                ai_behavior="respond",
            )

    def _emit_status(self) -> None:
        try:
            self._report_status({
                "source": "sts2_autoplay",
                "transport_state": self._state.transport_state,
                "last_error": self._state.last_error,
                "snapshot": self._state.snapshot,
                "standby": self._state.standby,
                "autoplay_state": self._state.autoplay_state,
                "step_count": self._state.step_count,
            })
        except Exception:
            pass
