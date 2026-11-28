from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from time import monotonic, time
from typing import Any


class ServiceStatusMixin:
    """Status APIs and throttled, non-secret static control-page snapshots."""

    #: `runtime_state.solver_status` 五态机的中文含义（写进状态包，供 UI / 排障一眼读懂）。
    SOLVER_STATUS_MEANINGS = {
        "idle": "本 tick 没取计划（不在战斗决策态）",
        "computing": "正在等求解器算完（/solver/plan 无读超时，绝不降级换策略）",
        "ready": "已拿到可用计划（in_combat=True 且 search_status 合法）",
        "waiting_phase": "mod 回了载荷但还没到玩家出牌阶段（正常，继续等）",
        "failed": "请求本身失败（404/连接断/非 200）：等下去不会好，检查 mod 与端口",
    }

    def _map_wait_reason(self, snapshot: dict[str, Any]) -> str:
        if str(snapshot.get("screen") or "").upper() != "MAP":
            return ""
        context = snapshot.get("summary_context")
        return self._planner.map_wait_reason(context) if isinstance(context, dict) else ""

    async def get_status(self) -> dict[str, Any]:
        return self._status_payload()

    def _status_payload(self) -> dict[str, Any]:
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
            "updated_at": time(),
            "summary": summary,
            "message": summary,
            "livestream": self._livestream.status(),
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
                "waiting_reason": self._map_wait_reason(snapshot),
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
            "danmaku": {
                # 弹幕总开关（唯一真闸门）。catgirl_llm_enabled 只决定点评走 LLM 还是启发式文本。
                "enabled": self._danmaku_enabled,
                "catgirl_llm_enabled": self._catgirl_llm_enabled,
                "llm_available": bool(self._catgirl_llm is not None and self._catgirl_llm.available),
            },
            "instance": {
                # 插件当前对着哪个游戏进程：host（配置里的 base_url，单人局就是它；coop 里是主机）
                # / catgirl（coop 里被拉起的猫娘进程）。见 switch_active_instance。
                "active": self._state.active_instance,
                "host_base_url": self._host_base_url or self._state.base_url,
                "catgirl_base_url": self._catgirl_base_url,
            },
            "host_digest": {"enabled": self._state.host_digest_enabled},
            "run_reflection": {"enabled": self._state.run_reflection_enabled},
            "decision_engine": self._loop_runner.decision_engine_status(),
            "solver": self.solver_status_payload(),
            "policy": self._policy_advisor.status(),
            "map_policy": {
                # wait = 彻底等玩家发话（不投票、也不跟票）；follow = 自动跟票；auto = 自己选。
                "policy": self._map_policy,
                "vote_wait_seconds": self._map_vote_wait_seconds,
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

    def solver_status_payload(self) -> dict[str, Any]:
        """暴露"求解器到底出结果了没有"—— 自动玩的等待态。

        ★ 为什么它是必须的（玩家口径 2026-10-25）：`/solver/plan` 改成**无读超时**后，
          "等多久"没有上限了（等不及就退级会换掉结果，比卡住更坏）。那就必须能从外部
          查到"它还在算 / 算完了 / 请求本身失败"，否则玩家面对的是一台"开着但不动的机器"。
        ★ `failed` 与 `computing` 必须分开：前者是 0 ms 的 404（装错 mod、端口指错），
          等一百年也不会好；后者只是慢。把两者混成一个 `in_combat=None` 正是
          2026-10-24 两次实机故障"查不出来"的原因。
        """
        try:
            state = self._state
            status = str(getattr(state, "solver_status", "idle") or "idle")
            wait_seconds = 0.0
            if status == "computing":
                started = float(getattr(state, "solver_wait_started_at", 0.0) or 0.0)
                wait_seconds = max(0.0, time() - started) if started > 0 else 0.0
            return {
                "status": status,
                "meaning": self.SOLVER_STATUS_MEANINGS.get(status, ""),
                "wait_seconds": round(wait_seconds, 1),
                "last_elapsed_seconds": round(float(getattr(state, "solver_last_elapsed_seconds", 0.0) or 0.0), 2),
                "last_search_status": str(getattr(state, "solver_last_search_status", "") or ""),
                "last_result_at": float(getattr(state, "solver_last_result_at", 0.0) or 0.0),
                "last_error": str(getattr(state, "solver_last_error", "") or ""),
                "wait_count": int(getattr(state, "solver_wait_count", 0) or 0),
                # 显式写出来：读超时被**故意**去掉，别有人再"顺手加回去"。
                "read_timeout_seconds": None,
            }
        except Exception:
            return {"status": "unknown", "meaning": "", "read_timeout_seconds": None}

    def _emit_status(self) -> None:
        self._publish_control_status()
        try:
            snap = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            # 只推轻量摘要，不塞整包 /state（snapshot 里含完整 raw_state，几十 MB；塞进状态/IPC 会触发
            # NEKO 端 ZMQ 反序列化错误 / 超时）。需要全量就单独走 refresh_state / get_snapshot。
            light_keys = ("screen", "floor", "act", "in_combat", "run_id", "character", "turn", "available_action_count", "summary_kind", "state_signature")
            self._report_status({
                "source": "sts2_autoplay",
                "transport_state": self._state.transport_state,
                "last_error": self._state.last_error,
                "snapshot": {k: snap.get(k) for k in light_keys if k in snap},
                "standby": self._state.standby,
                "autoplay_state": self._state.autoplay_state,
                "step_count": self._state.step_count,
                # 卡住时这里非空（"UNKNOWN（未识别界面类型 X）界面里没有可执行动作，已等 N 秒"）：
                # 让"它不动了"变成看得见的事实，而不是要玩家自己盯出来。
                "stuck_reason": self._state.stuck_reason,
                "sse_reconnect_count": getattr(self._state, "sse_reconnect_count", 0),
                # ★ t46：等待求解器期间，状态里必须能读出"在等/出结果了/请求失败"。
                "solver": self.solver_status_payload(),
            })
        except Exception:
            pass

    def _publish_control_status(self) -> None:
        """UI polling never creates host runs; failures must not interrupt play.

        Enable only at startup, not when a unit-test/service stub is constructed.
        Atomically replace so readers cannot see half a JSON document.
        """
        path = self._control_status_path
        now = monotonic()
        if path is None or now < self._control_status_next:
            return
        self._control_status_next = now + 2.0
        temporary = None
        try:
            full = self._status_payload()
            # No chat packets, player choices, model paths or full game snapshots.
            fields = {
                "server": ("state",),
                "instance": ("active",),
                "autoplay": ("state", "standby", "pause_reason", "stop_reason", "waiting_reason"),
                "run": ("screen", "floor", "act", "in_combat"),
                "policy": ("enabled", "runtime_ready", "decisions", "fallbacks", "characters", "last_note"),
                "companion_mode": ("enabled",),
                "danmaku": ("enabled",),
                "host_digest": ("enabled",),
                "run_reflection": ("enabled",),
                "decision_engine": ("engine", "phase", "character", "detail"),
                "livestream": ("enabled", "profile", "wait_scale", "waiting", "remaining_seconds"),
            }
            data = {section: {key: full[section][key] for key in keys if key in full[section]}
                    for section, keys in fields.items()}
            data["updated_at"] = full["updated_at"]
            content = json.dumps(data, ensure_ascii=False, allow_nan=False)
            path = Path(path)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".control-status-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            os.replace(temporary, path)
        except Exception as exc:
            self.logger.debug("[sts2_ui] control snapshot failed: %s", exc)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
