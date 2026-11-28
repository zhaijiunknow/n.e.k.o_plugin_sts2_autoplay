from __future__ import annotations

import asyncio
import os
from hashlib import sha1
from pathlib import Path
from random import random
from time import time
from typing import Any, Callable

from .action_engine import STS2ActionEngine
from .action_registry import STS2ActionRegistry
from .candidate_generator import STS2CandidateGenerator
from .catgirl_bridge import STS2CatgirlBridge
from .catgirl_llm import CatgirlCommentGenerator
from .catgirl_memory import CatgirlMemoryStore, _default_memory_path
from .choice_review import ChoiceReview
from .companion_evaluator import STS2CompanionEvaluator
from .encounter_memory import EncounterMemory, _default_encounter_db_path
from .heuristic_planner import (
    MAP_POLICY_WAIT,
    STS2HeuristicPlanner,
)
from .history_view import static_dir
from .instructions import normalize_guidance_instruction
from .livestream import LivestreamController
from .loop_runner import STS2LoopRunner
from .mode_controller import STS2ModeController
from .neko_interface import STS2NekoInterface
from .policy_client import PolicyAdvisor, is_policy_operation
from .preference_extractors import STS2PreferenceExtractor
from .preference_store import STS2PreferenceStore
from .runtime_state import STS2RuntimeState
from .service_danmaku import ServiceDanmakuMixin
from .service_history import ServiceHistoryMixin
from .service_host import ServiceHostMixin
from .service_settings import ServiceSettingsMixin
from .service_status import ServiceStatusMixin
from .situation_summary_engine import STS2SituationSummaryEngine
from .snapshot_normalizer import normalize_actions, normalize_snapshot
from .state_machine import STS2StateMachine, should_hold_for_orders
from .strategy_repository import STS2StrategyRepository
from .summary_context_builder import STS2SummaryContextBuilder
from .transport_client import STS2ActionRejected, STS2TransportClient, STS2TransportError

# 「当前游戏信息状态」面板：状态推送最小间隔（防刷爆 SSE 有界队列）/ 心跳间隔（空闲时也定期刷新）
STATUS_PUSH_MIN_INTERVAL = 2.0
STATUS_PUSH_HEARTBEAT = 5.0

#: `plugin.toml` 里读不到版本时的兜底标记（绝不假装知道版本）。
PLUGIN_VERSION_UNKNOWN = "unknown"


def _read_plugin_version(plugin_toml: Path | None = None) -> str:
    """读本插件**自己**的版本号，供启动行 `[sts2_code_version]` 使用。

    ★ t54 为什么必须有这个函数（缺陷出处）：这一行此前是**写死的字符串**
      `20260521_companion_eval_debug`（一个日期式的旧代号，永远不变）。于是 2026-10-26
      给玩家排障时，日志里**根本看不出他跑的是 0.7.6 / 0.7.7 / 0.7.8 中的哪一个** ——
      只能靠"日志里有没有某个标签"（`[sts2_solver_wait]` / `[sts2_mem]`）去反推，
      代价是整整一轮误判：他"更新成功"之后其实还停在旧内置副本上，而我们花了很久才确认。

    刻意**不用 tomllib**（Python 3.11+ 才有；NEKO 各发行构建自带的解释器版本不一），
    也刻意不用完整 TOML 解析：只认 `plugin.toml` 顶层第一个 `version = "..."`。
    读失败一律返回 :data:`PLUGIN_VERSION_UNKNOWN`，绝不抛（启动行不许影响启动）。
    """
    path = plugin_toml if plugin_toml is not None else Path(__file__).with_name("plugin.toml")
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != "version":
                continue
            version = value.strip().strip('"').strip("'")
            if version:
                return version
    except Exception:
        return PLUGIN_VERSION_UNKNOWN
    return PLUGIN_VERSION_UNKNOWN





class STS2AutoplayService(ServiceSettingsMixin, ServiceHistoryMixin, ServiceHostMixin, ServiceDanmakuMixin, ServiceStatusMixin):
    def __init__(self, logger: Any, status_reporter: Callable[[dict[str, Any]], None], frontend_notifier: Callable[..., Any] | None = None, *, sdk_bus: Any = None, sdk_ctx: Any = None, i18n: Any = None) -> None:
        self.logger = logger
        self._report_status = status_reporter
        self._frontend_notifier = frontend_notifier
        self._sdk_bus = sdk_bus
        self._sdk_ctx = sdk_ctx
        self._i18n = i18n
        # 猫娘 LLM 点评生成（catgirl 弹幕真内容来源）
        self._catgirl_llm = CatgirlCommentGenerator(self.logger)
        self._catgirl_llm_enabled = True
        self._catgirl_llm_task: asyncio.Task | None = None
        self._last_danmaku_at: float | None = None
        self._danmaku_sending = False
        self._recent_danmaku: dict[str, float] = {}
        # 即时句、LLM、兜底共享事实去重；不随 60 秒文本缓存过期，不包含闲聊。
        self._reported_danmaku_changes: dict[str, None] = {}
        self._last_instant_text = ""
        self._last_instant_change_key = ""
        self._danmaku_idle_observation = ""
        self._danmaku_idle_since = 0.0
        self._danmaku_idle_attempt_at: float | None = None
        self._recent_idle_lines: list[str] = []
        # 只存短期指纹，不把内部提示或聊天写入记忆。
        self._recent_host_reports: dict[str, float] = {}
        # 游戏内弹幕总开关。与 _catgirl_llm_enabled 语义不同：后者只管"点评用 LLM 还是启发式文本"，
        # 关掉它仍会走 _push_catgirl_fallback 推弹幕；本开关是唯一的真闸门（_push_catgirl 收口）。
        self._danmaku_enabled = True
        # 地图投票策略（startup 用插件配置覆盖）。默认 wait：多人局里彻底等待，不投票也不跟票，
        # 直到玩家通过对 NEKO 说话下令（sts2_set_route）。详见 heuristic_planner.MAP_POLICY_*。
        self._map_policy = MAP_POLICY_WAIT
        self._map_vote_wait_seconds = 20.0
        # 猫娘头像(base64)缓存:从 N.E.K.O 主 server card-drop 拉一次,TTL 300s
        self._avatar_b64: str | None = None
        self._avatar_at: float = 0.0
        self._client: STS2TransportClient | None = None
        # 活动实例相关（见 switch_active_instance）。默认值放在这里而不是只放 startup()：
        # 测试桩直接构造 service、不走 startup()，缺这些字段会让它们全部炸掉。
        self._host_base_url: str = ""
        self._host_client: STS2TransportClient | None = None
        self._catgirl_client: STS2TransportClient | None = None
        self._catgirl_base_url: str = ""
        self._instance_failures: int = 0
        self._cfg: dict[str, Any] = {}
        self._pending_restart_settings: dict[str, Any] = {}
        self._control_status_path: Path | None = None
        self._control_status_next = 0.0
        self._state = STS2RuntimeState()
        self._state_machine = STS2StateMachine()
        self._summary_builder = STS2SummaryContextBuilder()
        self._summary_engine = STS2SituationSummaryEngine(self._i18n)
        self._companion_evaluator = STS2CompanionEvaluator(self._i18n)
        self._catgirl_bridge = STS2CatgirlBridge(i18n=self._i18n, source_id="sts2_autoplay")
        self._preference_store = STS2PreferenceStore()
        self._preference_extractor = STS2PreferenceExtractor()
        # 猫娘跨局记忆：run 结束总结教训/偏好，落盘后注入 strategy_directives 影响下一局决策。
        self._catgirl_memory = CatgirlMemoryStore(path=_default_memory_path(), logger=self.logger)
        self._review_generation = 0
        # 局末复盘的原料：玩家亲手打的每一步与当时推荐的对照（见 choice_review）。
        self._choice_review = ChoiceReview()
        # 对端没有 /choices（旧版模组）时，隔一阵再问，别每个 tick 撞一次 404。
        self._choice_review_retry_at = 0.0
        # review_tick 正在跑：轮询、事件流、自动玩循环都会进 tick，同一拍只让一路做对照。
        self._choice_review_busy = False
        # ★ t31：怪物/事件实例知识库。**独立文件**（契约 §5.1）—— 不挂 catgirl_memory，
        #   因为后者是 cap=50 环形缓冲（实测 20 分钟内就有局被逐出），
        #   而"这只怪啃过我多少血"是**跨局累积**资产，生命周期必须长于单局。
        self._encounter_memory = EncounterMemory(
            path=_default_encounter_db_path(), logger=self.logger
        )
        # 采集开关（默认开）。关掉只影响本库的累积，不影响任何决策。
        self._encounter_memory_enabled = True
        self._strategy_repository = STS2StrategyRepository(
            logger,
            self._preference_store,
            catgirl_memory=self._catgirl_memory,
            default_strategy="defect",
        )
        self._mode_controller = STS2ModeController("program")
        self._planner = STS2HeuristicPlanner(logger)
        self._candidate_generator = STS2CandidateGenerator(self._planner)
        self._action_registry = STS2ActionRegistry()
        self._action_engine = STS2ActionEngine(self._i18n)
        self._loop_runner = STS2LoopRunner(self)
        # AlphaDefect 策略网络（默认关）。开着时整步决策先问它，给不出才回落到求解器 / 启发式。
        self._policy_advisor = PolicyAdvisor(logger)
        self._livestream = LivestreamController()
        self._policy_advisor.livestream = self._livestream
        self._planner.configure_livestream(self._livestream)
        self.neko = STS2NekoInterface(self)

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        if self._i18n is not None:
            return self._i18n.t(key, default=default, **params)
        return default.format(**params) if params and default else (default or key)

    async def startup(self, cfg: dict[str, Any]) -> dict[str, Any]:
        try:
            # ★ t54：报**真实**版本（读 plugin.toml），不再打写死的旧代号。
            #   排障第一句话就是"你跑的是哪一版"，此前这一行给的是无效信息。
            self.logger.info("[sts2_code_version] version=%s", _read_plugin_version())
        except Exception:
            pass
        self._cfg = dict(cfg)
        self._pending_restart_settings.clear()
        self._livestream.configure(self._cfg)
        self._policy_advisor.configure(self._cfg)
        # 选人：有已下载模型的角色时只在它们里面选（见 heuristic_planner）；每次进选人界面现查，
        # 所以对局之间新下载的模型不用重启插件就生效。
        self._planner.configure_preferred_characters(self._policy_advisor.playable_characters)
        # 下载前端读的进度文件。这里只写一份当前状态，**不触发任何下载**。
        self._control_status_path = Path(static_dir()) / "control_status.json"
        self._control_status_next = 0.0
        self._policy_advisor.assets.status_path = Path(static_dir()) / "policy_status.json"
        self._policy_advisor.assets.publish()
        # 两个"跟本体说话"的开关（默认开）。关掉只影响速报/复盘这两路，不影响自动玩与记录。
        self._state.host_digest_enabled = bool(self._cfg.get("host_digest_enabled", True))
        self._state.run_reflection_enabled = bool(self._cfg.get("run_reflection_enabled", True))
        # 启动就把历史快照刷一遍：快照原本只在"局末"写，于是插件刚起、还没打完一局时，
        # 历史页会显示"读不到历史" —— 而 catgirl_memory 里其实已经躺着几十局记录。
        self._write_history_view()
        # 猫娘 LLM 点评：开关（生成间隔节流已不启用，防刷屏由外层 _should_deliver_sync 兜底）
        self._catgirl_llm_enabled = bool(self._cfg.get("catgirl_llm_enabled", True))
        # 游戏内弹幕总开关（真闸门）。默认开；关闭后 _push_catgirl 直接早退，既不生成也不推。
        self._danmaku_enabled = bool(self._cfg.get("danmaku_enabled", True))
        # ★ t31：怪物/事件实例知识库采集开关。默认开。
        #   关掉只停止累积（不影响自动游玩、不影响任何决策）。
        self._encounter_memory_enabled = bool(self._cfg.get("encounter_memory_enabled", True))
        self._configure_map_policy_from_config()
        base_url = self._resolve_base_url()
        self._state.base_url = base_url
        self._apply_control_mode("program")
        self._client = self._build_client(base_url)
        # 主机实例永远是这一个（配置里的 base_url）。开房必须发给它——只有主机能拉起猫娘进程。
        self._host_base_url = base_url
        self._host_client: STS2TransportClient | None = self._client
        # 猫娘实例：coop 里被拉起的那个进程，地址由主机的 /config.coop_client_port 推导。
        # 插件接管游玩时，读状态与驱动动作都对着它。
        self._catgirl_client: STS2TransportClient | None = None
        self._catgirl_base_url = ""
        self._instance_failures = 0
        # ★ 联机房间是否开着。供 `_is_coop_active()`（弹幕优先级 coop>autoplay>陪玩）判断。
        #   与 `active_instance` 互补：后者在"接管不了猫娘"时会停在 host 上，此处仍能记住开过房。
        self._coop_room_open = False
        startup_result = {"connected": False, "companion_mode_enabled": False}

        try:
            # 启动不阻塞、不等 mod：尽力 health 一次(限时)，成功则标记 connected + 顺手禁用 mod 自身
            # LLM/弹幕；失败**不提前 return**，由后台事件循环在 mod 上线时自动重连并禁用其 LLM/弹幕。
            try:
                await asyncio.wait_for(self.health_check(), timeout=3.0)
                startup_result["connected"] = True
                if self._client is not None:
                    try:
                        await self._client.set_config(llm_enabled=False, danmaku_enabled=False)
                    except Exception as _llm_exc:
                        self.logger.debug(f"[sts2] disabling mod LLM/danmaku on connect failed (non-fatal): {_llm_exc}")
            except Exception:
                pass
            companion_enabled = bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True)))
            if companion_enabled:
                self.set_companion_mode(True, emit_status=False, push_message=False)
                startup_result["companion_mode_enabled"] = True
            else:
                self._sync_background_polling()
            if bool(self._cfg.get("autoplay_on_start", False)) and not self._state.standby:
                self.start_autoplay()
        except Exception as exc:
            self._state.transport_state = "disconnected"
            self._state.last_error = str(exc)
            self._state.consecutive_errors += 1
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
        if self._catgirl_llm_task is not None:
            self._catgirl_llm_task.cancel()
            try:
                await self._catgirl_llm_task
            except asyncio.CancelledError:
                pass
            self._catgirl_llm_task = None
        self._policy_advisor.shutdown()
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._catgirl_llm is not None:
            await self._catgirl_llm.shutdown()
        self._state.transport_state = "disconnected"
        self._state.autoplay_state = "idle"
        self._control_status_next = 0.0
        self._emit_status()

    async def health_check(self) -> dict[str, Any]:
        client = self._require_client()
        health = await client.health()
        self._state.transport_state = "connected"
        self._state.last_error = ""
        self._state.touch_success()
        # 连上 mod 就把地图等待策略推过去：插件与 mod 各有一套决策循环，策略不一致会出现
        # "插件在等、mod 照投"这种自相矛盾的局面。失败是良性的（mod 自己的默认也是 wait）。
        map_sync = await self.sync_map_policy_to_mod()
        control_sync = await self.sync_control_mode_to_mod()
        live_sync = await self.sync_livestream_to_mod()
        self._emit_status()
        message = self.t("status.connected", default="STS2-Agent 已连接: {base_url}", base_url=self._state.base_url)
        return {
            "status": "connected",
            "message": message,
            "summary": message,
            "health": health,
            "map_policy_sync": map_sync,
            "control_mode_sync": control_sync,
            "livestream_sync": live_sync,
        }

    # 正式联机流程（与 mod 自身一致）：
    #   host  → mod 的 start_coop_session 一步做完（coop_enabled 落盘 -> 关主菜单子菜单 ->
    #           open_multiplayer_menu -> start_multiplayer_host(ENet 33771) -> 拉起猫娘进程）。
    #   join  → open_multiplayer_menu（仅 currentScreen 就是 NMainMenu 时可用）-> join_multiplayer_direct。
    # 不要用调试场景那套 host_multiplayer_lobby / join_multiplayer_lobby：它们要求当前场景是
    # NMultiplayerTest，且 mod 明确记录该路径会让装了第三方 mod 的第二个游戏实例崩溃
    # （见 game_mod/nekospire/Game/NekoAutoplayDriver.cs 的 DecideMainMenu 注释）。
    COOP_SESSION_ACTION = "start_coop_session"
    COOP_MENU_ACTION = "open_multiplayer_menu"
    COOP_CLOSE_SUBMENU_ACTION = "close_main_menu_submenu"
    COOP_JOIN_ACTION = "join_multiplayer_direct"

    # ---- 活动实例：插件驱动的是"哪一个游戏进程" ------------------------------------------

    INSTANCE_HOST = "host"
    INSTANCE_CATGIRL = "catgirl"

    @staticmethod
    def _normalize_instance(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"catgirl", "coop", "client", "coop_client", "猫娘", "coop猫娘"}:
            return "catgirl"
        return "host"

    async def resolve_catgirl_base_url(self) -> str:
        """向**主机**问出猫娘进程的端口，拼出它的地址；问不到返回空串。

        `GET /config` 的 `coop_client_port` 是 mod 0.1.4 起才有的只读字段（默认 18081）。
        老版本 mod 没有这个字段时**不猜端口**——猜错会连到别的进程上去。
        """
        if self._catgirl_base_url:
            return self._catgirl_base_url
        host = self._host_client or self._client
        if host is None:
            return ""
        try:
            payload = await host.get_config()
        except Exception as exc:
            self.logger.debug(f"[sts2] reading host /config failed (non-fatal): {exc}")
            return ""
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        port = data.get("coop_client_port") if isinstance(data, dict) else None
        try:
            port_int = int(port)
        except (TypeError, ValueError):
            return ""
        if not 0 < port_int <= 65535:
            return ""
        self._catgirl_base_url = f"http://127.0.0.1:{port_int}"
        return self._catgirl_base_url

    async def switch_active_instance(self, target: str, *, reason: str = "") -> dict[str, Any]:
        """把"当前活动实例"切到主机或猫娘。切换后 `self._client` 即指向它，下游全部跟随。

        切到猫娘时会顺手给那个实例推 `coop_control_mode="hold"`：让它自己的 mod 驱动在
        除"进局流程"外的界面停手，否则两个驱动会抢着下同一个动作。进局流程（主菜单 →
        组队大厅 → 选角色）mod 仍会自己做——那正是插件够不着的部分。
        """
        wanted = self._normalize_instance(target)
        if wanted == self.INSTANCE_CATGIRL:
            if self._catgirl_client is None:
                address = await self.resolve_catgirl_base_url()
                if not address:
                    return {"switched": False, "instance": self._state.active_instance, "reason": "catgirl_address_unknown"}
                self._catgirl_client = self._build_client(address)
            self._client = self._catgirl_client
            standdown = await self._push_driver_standdown(self._catgirl_client)
            await self.sync_map_policy_to_mod()
        else:
            if self._host_client is None:
                self._host_client = self._build_client(self._host_base_url)
            self._client = self._host_client
            await self.sync_map_policy_to_mod()
            standdown = {"applied": False}
        await self.sync_livestream_to_mod()
        self._livestream.reset()
        self._state.active_instance = wanted
        self._state.catgirl_base_url = self._catgirl_base_url if wanted == self.INSTANCE_CATGIRL else ""
        self._instance_failures = 0
        self.logger.info(f"[sts2] active instance -> {wanted} ({reason or 'manual'})")
        self._emit_status()
        return {
            "switched": True,
            "instance": wanted,
            "base_url": self._state.base_url if wanted == self.INSTANCE_HOST else self._catgirl_base_url,
            "mod_standdown": standdown,
        }

    async def _push_driver_standdown(self, client: STS2TransportClient) -> dict[str, Any]:
        """让该实例上 mod 自己的驱动停手（保留进局流程）。失败是良性的：插件仍会照常驱动。"""
        try:
            await client.set_config(coop_control_mode="hold")
            return {"applied": True}
        except Exception as exc:
            self.logger.debug(f"[sts2] pushing driver stand-down failed (non-fatal): {exc}")
            return {"applied": False}

    async def adopt_coop_catgirl(self, *, wait_seconds: float = 45.0) -> dict[str, Any]:
        """开房成功后接管猫娘：等它那个进程起来，再切成活动实例。

        猫娘是 mod 拉起的**另一个游戏进程**，要冷启动十几秒，所以这里轮询等待而不是一次探测。
        等不到就停在主机实例上（单人流程不受影响），并如实说明原因。
        """
        address = await self.resolve_catgirl_base_url()
        if not address:
            return {"adopted": False, "reason": "catgirl_address_unknown"}
        probe = self._catgirl_client or self._build_client(address)
        deadline = time() + max(0.0, float(wait_seconds))
        last_error = ""
        while time() < deadline:
            try:
                await probe.health()
            except Exception as exc:
                last_error = str(exc)
                await asyncio.sleep(1.5)
                continue
            self._catgirl_client = probe
            result = await self.switch_active_instance(self.INSTANCE_CATGIRL, reason="coop_room_opened")
            return {"adopted": bool(result.get("switched")), "base_url": address, "wait_error": last_error, **result}
        return {"adopted": False, "reason": "catgirl_not_reachable", "base_url": address, "last_error": last_error}

    async def _note_transport_failure(self) -> None:
        """活动实例读不通时的兜底：连续失败够多次就把活动实例退回主机，别把插件卡在死地址上。"""
        if self._state.active_instance != self.INSTANCE_CATGIRL:
            return
        self._instance_failures += 1
        if self._instance_failures < 3:
            return
        self.logger.warning("[sts2] catgirl instance unreachable; falling back to the host instance")
        await self.switch_active_instance(self.INSTANCE_HOST, reason="catgirl_unreachable")

    async def set_instance(self, instance: str) -> dict[str, Any]:
        """显式切活动实例（host / catgirl），供玩家或排障用。"""
        result = await self.switch_active_instance(instance, reason="explicit")
        message = (
            "好，我现在对着猫娘那个进程。" if result.get("instance") == self.INSTANCE_CATGIRL
            else "好，我现在对着主机那个进程。"
        ) if result.get("switched") else "没切成功——多半是问不到猫娘进程的端口（mod 版本旧了？）。"
        return {"status": "ok" if result.get("switched") else "error", "message": message, "summary": message, **result}

    async def open_coop_room(self, host: bool = True, resume: bool = False) -> dict[str, Any]:
        if getattr(self, "_coop_opening", False):
            raise STS2TransportError("联机房间正在打开，请等待结果，不要重复提交。")
        self._coop_opening = True
        try:
            return await self._open_coop_room(host=host, resume=resume)
        finally:
            self._coop_opening = False

    async def _open_coop_room(self, host: bool = True, resume: bool = False) -> dict[str, Any]:
        """打开联机房间。

        host=True 只发一个 start_coop_session，整套编排交给 mod —— 包括拉起猫娘进程，那正是插件
        够不着的部分（LaunchCatgirlProcess 是私有的，只暴露在这一个动作后面）。插件因此不再自己拼
        动作序列，也不再自己写 coop_enabled：mod 那个动作内部会落盘。

        resume=True 只发 continue_coop_session；失败不回退新建。主机动作不会发给活动猫娘。
        host=False 是加入本机的房间，没有猫娘要拉，仍自己走 open_multiplayer_menu ->
        join_multiplayer_direct（连 host 的 127.0.0.1:33771）。
        """
        if resume and not host:
            raise STS2TransportError("继续联机存档需要在主机创建读档大厅，不能作为加入操作。")
        if self._state.autoplay_state == "running":
            raise STS2TransportError("请先暂停自动游玩，再新建或继续联机房间。")
        if host:
            # Room lifecycle is pinned to the configured host, never the active gameplay instance.
            if self._host_client is None:
                if self._host_base_url:
                    self._host_client = self._build_client(self._host_base_url)
                elif self._state.active_instance == self.INSTANCE_HOST:
                    self._host_client = self._require_client()
                else:
                    raise STS2TransportError("主机地址不可用，请检查插件的 base_url 配置。")
            client = self._host_client
            action = "continue_coop_session" if resume else self.COOP_SESSION_ACTION
            screen, available = await self._coop_probe(client)
            self._require_coop_action(
                action,
                screen=screen,
                available=available,
                key="coop.session_unavailable",
                default="{action} 不可用（screen={screen}，可用动作: {actions}）—— mod 可能没重启到含该动作的版本。",
            )
            await client.execute_action(action)
        else:
            client = self._require_client()
            action = await self._join_coop_room(client)

        self.logger.info(f"[sts2] opened co-op room ({action})")
        # ★ 房间已开 ⇒ 记进联机状态，并立刻重判弹幕（coop 优先级高于陪玩）。
        self._coop_room_open = True
        self._apply_mode_danmaku_policy()
        result = await self._coop_opened_result(client=client, host=host, action=action)
        if host:
            # 房间是交给主机开的，而**要玩的是猫娘**。主机把猫娘进程拉起来之后，插件切过去接管，
            # 并让那个实例上 mod 自己的驱动停手（否则两个驱动抢着下同一个动作）。
            # 等不到就停在主机实例上，不影响单人流程。
            result["catgirl_adoption"] = await self.adopt_coop_catgirl()
            if not result["catgirl_adoption"].get("adopted"):
                result["message"] = result["summary"] = "房间已打开，但未成功接管猫娘实例。请检查猫娘窗口和端口，不要重复建房或在主机上开启自动游玩。"
            else:
                result["message"] = result["summary"] = (
                    "联机读档大厅已打开，已切到猫娘实例。等待双方准备后继续原局。"
                    if resume else "新联机房间已打开，已切到猫娘实例。请在游戏中选择角色并准备。"
                )
        result["resume"] = resume
        return result

    async def _join_coop_room(self, client: STS2TransportClient) -> str:
        """加入方路径：必要时先回主菜单，再开多人菜单并直连 host。返回实际发出的动作名。"""
        screen, available = await self._coop_probe(client)

        # 子菜单已经开着（上一轮留下的、或 mods 子菜单）时不用再开菜单，直接发第二步。
        if self.COOP_JOIN_ACTION not in available:
            # open_multiplayer_menu 要求 currentScreen 就是 NMainMenu，主菜单上开着任何子菜单都会 409，
            # 所以先按 mod 自己的做法（NekoConfigWindow.CloseMainMenuSubmenusAsync）把子菜单收干净。
            await self._close_main_menu_submenus(client)
            screen, available = await self._coop_probe(client)
            self._require_coop_action(
                self.COOP_MENU_ACTION,
                screen=screen,
                available=available,
                key="coop.menu_unavailable",
                default="当前界面无法打开多人菜单（screen={screen}，可用动作: {actions}）。",
            )
            await client.execute_action(self.COOP_MENU_ACTION)
            screen, available = await self._coop_probe(client)

        # open_multiplayer_menu 返回 200 不代表 NMultiplayerSubmenu 真的留在栈上，所以发第二步前
        # 校验一次；否则这里会退化成 mod 那句无法定位的 "Action is not available in the current state."。
        self._require_coop_action(
            self.COOP_JOIN_ACTION,
            screen=screen,
            available=available,
            key="coop.lobby_action_unavailable",
            default="多人菜单没有打开，{action} 不可用（screen={screen}，可用动作: {actions}）。",
        )
        await client.execute_action(self.COOP_JOIN_ACTION)
        return self.COOP_JOIN_ACTION

    def _require_coop_action(
        self,
        action: str,
        *,
        screen: str,
        available: set[str],
        key: str,
        default: str,
    ) -> None:
        """动作不在 /actions/available 里就抛带 screen 与可用动作的错，而不是发出去等 mod 回 409。"""
        if action in available:
            return
        raise STS2TransportError(
            self.t(
                key,
                default=default,
                action=action,
                screen=screen,
                actions=", ".join(sorted(available)) or "无",
            )
        )

    async def _coop_opened_result(self, *, client: STS2TransportClient, host: bool, action: str) -> dict[str, Any]:
        """Read the room target, not the active catgirl; never overwrite her gameplay snapshot."""
        screen, available = await self._coop_probe(client)
        message = self.t(
            "coop.opened",
            default="已打开联机房间：{action}，当前界面 {screen}。",
            action=action,
            screen=screen,
        )
        return {
            "status": "opened",
            "host": host,
            "action": action,
            "screen": screen,
            "available_actions": sorted(available),
            "room_instance": self.INSTANCE_HOST if host else self._state.active_instance,
            "message": message,
            "summary": message,
        }

    async def _coop_probe(self, client: STS2TransportClient) -> tuple[str, set[str]]:
        """读 /actions/available：返回 (screen, 动作名集合)。"""
        payload = await client.get_available_actions()
        names = {str(item.get("type")) for item in normalize_actions(payload) if item.get("type")}
        return str(payload.get("screen") or "unknown"), names

    async def _close_main_menu_submenus(self, client: STS2TransportClient) -> None:
        """把主菜单子菜单栈收回 NMainMenu（同 NekoConfigWindow.CloseMainMenuSubmenusAsync，防嵌套）。"""
        for _ in range(6):
            _, available = await self._coop_probe(client)
            if self.COOP_CLOSE_SUBMENU_ACTION not in available:
                return
            await client.execute_action(self.COOP_CLOSE_SUBMENU_ACTION)
            await asyncio.sleep(0.15)

    def _build_client(self, base_url: str) -> STS2TransportClient:
        """按配置里的超时造一个客户端。换活动实例时也走这里，保证两边超时一致。"""
        return STS2TransportClient(
            base_url=base_url.rstrip("/"),
            connect_timeout=float(self._cfg.get("connect_timeout_seconds", 5) or 5),
            request_timeout=float(self._cfg.get("request_timeout_seconds", 15) or 15),
        )

    async def refresh_state(self, *, trigger_sync: bool = False) -> dict[str, Any]:
        previous_snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        try:
            tick_result = await self._loop_runner.tick()
        except Exception:
            # 活动实例读不通：连败若干次就退回主机，别把插件卡在一个死地址上。
            await self._note_transport_failure()
            raise
        self._instance_failures = 0
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
        # ★ t31：把本 tick 的 /state 喂给「怪物/事件实例知识库」（纯观测，见方法注释）。
        self._observe_encounter_memory()
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

    async def run_autoplay_step(self, *, force: bool = False) -> dict[str, Any]:
        """自动玩循环的一步：拿当前计划并执行。

        ``force=True`` 是"我点头了"的显式授权（``sts2_execute_planned_operation``）：它绕开
        "听安排"档位的停手闸门。自动循环调用时不传。
        """
        if self._state.standby:
            message = self.t("autoplay.standby_blocked", default="当前处于 standby 模式，不执行动作。")
            return {"status": "idle", "message": message, "summary": message}
        was_running = self._state.autoplay_state == "running"
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        if not force and self._should_hold_for_orders(snapshot):
            state_name = self._snapshot_state_name(snapshot)
            message = self.t(
                "advise.holding",
                default="现在听你安排：{screen} 这一步我先不动，你说一声我再走。",
                screen=state_name or "当前界面",
            )
            return {"status": "idle", "message": message, "summary": message, "held": True, "state_name": state_name}
        if (self._livestream.enabled and (self._livestream.key or self._livestream.scene_for(snapshot))) or self._should_rebuild_operation(snapshot):
            await self.refresh_state(trigger_sync=True)
            snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            # 强制重算的请求到此已兑现，清掉，免得以后每一步都多刷一次 /state。
            self._state.force_replan_requested = False
        if (self._state.standby or (not force and self._should_hold_for_orders(snapshot))
                or (not force and was_running and self._state.autoplay_state != "running")):
            self._livestream.reset()
            return {"status": "idle", "held": True, "summary": "已停手，等待玩家安排。"}
        if self._livestream.key and self._livestream.scene_key(snapshot) != self._livestream.key:
            self._livestream.reset()
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            # 跨屏切换（如 reward->map / 战斗结束->奖励 等）时 /state 可能尚未切到下一屏，导致
            # choose_map_node 等动作还没出现在 available_actions。短延迟后再刷一次，捕捉切换。
            await asyncio.sleep(0.6)
            await self.refresh_state(trigger_sync=True)
            snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if (self._state.standby or (not force and self._should_hold_for_orders(snapshot))
                or (not force and was_running and self._state.autoplay_state != "running")):
            self._livestream.reset()
            return {"status": "idle", "held": True, "summary": "已停手，等待玩家安排。"}
        # 玩家口径（2026-10-07）：「还有每次对战如果损血大于8就鼓励使用药水」。这条是**硬规则**：
        #   - **不依赖规划器给出动作** —— 战斗里偶尔规划不出动作时，该保命还是要保命；
        #   - 只在这一步真的能派发 `use_potion`（动作在册 = 在战斗里且手里有能用的药水）时才会 override。
        # 策略网络自己决定喝不喝药（药水是它的候选之一），这条硬规则只管求解器 / 启发式那条路。
        potion_operation = None if force or is_policy_operation(operation) else self._combat_potion_override(snapshot)
        divine_operation = None if force else self._crystal_sphere_override(snapshot)
        if potion_operation is not None:
            operation = potion_operation
        elif divine_operation is not None:
            # 占卜屏：规划器认不出这一屏（screen=EVENT 但没有 event 层），由插件直接出动作。
            operation = divine_operation
        elif operation is None:
            message = self._map_wait_reason(snapshot) or self.t("autoplay.no_planned_operation", default="当前没有可执行的规划动作。")
            # 别静默空转：持续拿不到动作就记一次日志 + 写进状态 + 告诉本体（**不改 autoplay 意图**）。
            self._note_no_operation(snapshot)
            return {"status": "idle", "message": message, "summary": message}
        if self._livestream.gate(
            snapshot, operation, force=force,
            speak=lambda text: self._push_proactive_to_host(text, kind="livestream_decision", priority=4),
        ):
            self._emit_status()
            return {"status": "idle", "held": True, "summary": "直播互动中，暂不执行选择。",
                    "livestream": self._livestream.status()}
        # 自定义事件屏（水晶球占卜）上按下标依次试，别死盯一个下标空点。
        operation = self._advance_custom_event_cursor(snapshot, operation)
        result = await self.execute_operation(operation)
        if result.get("status") == "ok":
            if self._mod_action_status(result) == "completed":
                self._livestream.reset()
            self._state.step_count += 1
            self._clear_stuck()
            self._note_step_progress(snapshot, operation)
        return result

    def _advance_custom_event_cursor(self, snapshot: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
        """**自动占卜**：自定义事件屏上把 `option_index` 换成"下一个没试过的下标"。

        水晶球那一页（`screen_progress` 非空即为这类界面）把 2 个占卜按钮 + 15 个图块都暴露成
        选项，但**不是每个下标都有用**：实测点 [3] 回 pending、局面毫无变化，点 [4] 才 completed、
        图块揭开。所以死盯一个下标（规划器默认多半就是 0）只会一直空点 —— 这正是那条
        `[sts2_stuck] 动作没生效（无进展）… repeat=8` 的来源。

        这里的做法最简单也最稳：**每次派发都往前推一格**（到尾部回绕），于是它会把所有下标扫一遍，
        图块逐个被揭开，直到 `items` 归零、这一页结束（那时屏幕换了，游标自然重置）。
        """
        if str(operation.get("action_type") or "") != "choose_event_option":
            return operation
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        event = raw.get("event") if isinstance(raw.get("event"), dict) else {}
        # 只有自定义事件屏才有 screen_progress；常规事件屏（NEventRoom）照旧听规划器的。
        if not str(event.get("screen_progress") or ""):
            return operation
        options = event.get("options") if isinstance(event.get("options"), list) else []
        count = len(options)
        if count <= 0:
            return operation
        screen_key = f"{snapshot.get('screen')}|{snapshot.get('floor')}"
        if screen_key != self._state.event_option_screen:
            self._state.event_option_screen = screen_key
            self._state.event_option_cursor = 0
        index = self._state.event_option_cursor % count
        self._state.event_option_cursor = index + 1
        kwargs = dict(operation.get("kwargs")) if isinstance(operation.get("kwargs"), dict) else {}
        kwargs["option_index"] = index
        advanced = dict(operation)
        advanced["kwargs"] = kwargs
        return advanced

    # ---- D12（t32）：mod 认不出、插件也推不动的「自定义事件屏」------------------------------
    #: `unknown_screen_type` 里出现这些**类名片段** ⇒ 该屏是游戏侧的自定义小游戏界面
    #: （水晶球/占卜/壁画一类），其交互元素**不在 `/state` 的任何层里**。
    #: ★ 用「片段」而非全名：类名带命名空间前缀（实机为
    #:   `MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere.NCrystalSphereScreen`），
    #:   前缀/后缀都可能变，片段匹配更稳。
    #: ⚠️ 但片段**必须足够独特**：我最初只写 `"CrystalSphere"`，结果把 t29 测例里的
    #:   `MegaCrit.Sts2.Core.Nodes.Screens.CrystalSphereScreen`（**旧命名空间**的另一个类）
    #:   也一起命中 ⇒ 打坏了 t29 的既有契约（全量测试抓到 2 failed）。
    #:   ⇒ 现在要求「片段 + 与屏语义一致」两段同时成立才认（见 :meth:`_crystal_sphere_screen`）。
    UNADVANCEABLE_SCREEN_MARKERS = (
        "CrystalSphere",
        "DivinationScreen",
        "ProphecyScreen",
    )
    #: ★ 与标记同现的**屏语义**片段 —— 必须同时满足，避免跨命名空间误伤。
    #: 实机为 `...Nodes.Events.Custom.CrystalSphere.NCrystalSphereScreen`
    #: （`Events` + `Custom` 都在）。这条把 `Nodes.Screens.CrystalSphereScreen` 排除在外。
    UNADVANCEABLE_SCREEN_CONTEXT = ("Events", "Custom")
    #: 这些动作**不构成推进** —— 它们在自定义事件屏上也在册，但点了局面不变/会毁局。
    #: ★ `discard_potion` 在册但语义是"丢药水"，与推进这一屏无关（实测 :2377 只按"有药水"加入册）。
    #: ★ `save_and_quit` / `return_to_main_menu` / `abandon_run` 会**毁掉当前局**，绝不能自动发。
    NON_PROGRESSING_ACTIONS = frozenset({
        "save_and_quit", "return_to_main_menu", "abandon_run", "discard_potion",
    })

    def _crystal_sphere_screen(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """判定「本 tick 是不是 D12 那块推不动的自定义事件屏」。命中则返回诊断 dict。

        ## 为什么需要这个判定（实机证据，2026-10-20 03:10:03）

        `/state` 原文（落盘 `.dsh-artifacts/_t32/live-crystal-sphere-20261020_031003.json`）：
        ```
        screen              = 'EVENT'
        unknown_screen_type = '...Custom.CrystalSphere.NCrystalSphereScreen'
        available_actions   = ['save_and_quit', 'discard_potion']   ← 无任何推进动作
        event               = null      ← ★ 连事件层都没有
        selection/modal/bundles/timeline/map/reward/rest/shop/chest/character_select = 全 null
        ```
        ⇒ 26 个顶层键里**除 run/session/agent_view 外全为 null**。

        ## 为什么 `_advance_custom_event_cursor` 救不了它

        :meth:`_advance_custom_event_cursor`（本文件 :656）对水晶球**确实**有推进逻辑，
        但它的前置条件是 `event["screen_progress"]` 非空（:672 `if not str(event.get("screen_progress") or "")`）。
        本屏 `event is None` ⇒ `screen_progress` 恒为 `''` ⇒ **游标机制被早退绕过**。
        ⇒ 「水晶球」有两个子形态：**有 event 层**（游标可推进，已被 :656 修过）与
          **无 event 层**（就是本缺陷，`event/map` 全 null，任何已知动作都推不动）。

        ## 与 D10/D11 同族

        游戏给了结构（`EVENT` 只是 mod `ResolveScreenFromStaticRoom()` 用
        `EventRoom => "EVENT"` **兜底猜出来**的屏幕名，见
        `game_mod/nekospire/Game/GameStateService.cs:736-746`），
        插件照着 `EVENT` 去找 `choose_event_option`（`heuristic_planner.py:1020-1032`），
        而游戏从没提供该动作 ⇒ 规划不出动作 ⇒ 空转。
        """
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = str(snapshot.get("screen") or raw.get("screen") or "")
        unknown = str(raw.get("unknown_screen_type") or "")
        if not unknown or not any(m in unknown for m in self.UNADVANCEABLE_SCREEN_MARKERS):
            return None
        # ★ 必须同现「屏语义」片段（`Events` + `Custom`），否则不认 ——
        #   防止把别的命名空间下的同名类（如 `Nodes.Screens.CrystalSphereScreen`）误伤。
        #   参考件：t29 的 `test_stuck_screen.py` 用的正是那个旧类名，且它**不属于**本缺陷。
        if not all(ctx in unknown for ctx in self.UNADVANCEABLE_SCREEN_CONTEXT):
            return None
        # ★ 还必须落在 EVENT 语义的屏上：本缺陷的症状就是"被兜底猜成 EVENT，却没有事件层"。
        #   其它屏（战斗/地图）即使类名带标记，也不该由本判定接管。
        if screen.upper() != "EVENT":
            return None
        # ★ 必须同时满足「事件层为空」：有 event 层时交给 :656 的游标机制（那条路已验证可用），
        #   不能把能推进的形态也拦掉（否则就是把一个能修的场景降级成放弃）。
        if isinstance(raw.get("event"), dict) and raw.get("event"):
            return None
        actions = raw.get("available_actions") if isinstance(raw.get("available_actions"), list) else []
        progressing = [
            str(a) for a in actions if str(a) not in self.NON_PROGRESSING_ACTIONS
        ]
        if progressing:
            return None
        # ★ t33：mod 补了 `crystal_sphere` 层之后，这一屏**不再是死局** ——
        #   它有专门的 `divine_crystal_sphere` 动作（在 `progressing` 里，所以上面就已 return None）。
        #   这一段是**双保险**：即使某一版 mod 只补了层的读法、还没把动作挂进册，
        #   只要 `crystal_sphere.active` 为真，也**不算"推不动"**，交由规划器/游标去处理。
        #   否则会出现最坏的自相矛盾：屏上明明能占卜，插件却在喊"卡住了"。
        cs = raw.get("crystal_sphere") if isinstance(raw.get("crystal_sphere"), dict) else {}
        if cs.get("active"):
            return None
        return {
            "screen": screen or "EVENT",
            "unknown": unknown,
            "actions": sorted(str(a) for a in actions),
        }

    def _note_unadvanceable_screen(self, snapshot: dict[str, Any], diag: dict[str, Any]) -> None:
        """D12：对推不动的自定义事件屏**停止静默空转**，并给出**可执行的降级指引**。

        ## 「至少不要卡死」的降级方案（本任务契约要求）

        ★ 诚实的边界：**该屏无法从当前 `/state` 信息自动推进**。证据：
          · `/state` 的 12 个交互层全为 `null`（无坐标/按钮/确认键可读）；
          · mod 的 `/action` 支持 54 个动作（`GameActionService.cs:99-152`），
            **没有任何一个**与水晶球交互；
          · 实测未知动作名 ⇒ HTTP 409 `invalid_action` "Action is not supported yet."
          · 唯一"看起来能推"的 `discard_potion` 只是丢药水（且需要 option_index，实测无 index 时
            HTTP 400 `invalid_request`），点它不会让这一屏结束。
          ⇒ 想**真推进**必须 mod 侧新增（见 :meth:`_unadvanceable_screen_advice`），
            那超出本任务 inScope（`game_mod/` 被明确排除）。

        ★ 因此本降级**只做三件不冒险的事**（都不动 autoplay 意图、不发任何破坏性动作）：
          1. **停止重复上报**：同一指纹只记一次 WARNING（复用既有 `stuck_*` 记账，
             不新增 runtime_state 字段 —— 该文件不在 inScope）；
          2. **把「推不动」与「没认出」讲清楚**：状态里写明是哪个屏、为什么推不动、
             以及需要 mod 补什么；
          3. **绝不自作主张毁局**：`save_and_quit`/`return_to_main_menu`/`abandon_run`
             一律不发（那是玩家的进度，实测这局已到 act1/floor23 是当晚最深的一局）。

        ★ 调用约定：由 :meth:`_note_no_operation` 的**共享闸**在「指纹稳定且已等够」之后调用
          ⇒ 本方法**不再自己计时**（那会重复实现一套时序，且第一版就是那样打坏了 t29 的测例）。
        """
        self._state.stuck_reported = True
        waited = int(max(0, time() - self._state.stuck_since))
        advice = self._unadvanceable_screen_advice(diag)
        detail = f"{diag['screen']}（未识别界面类型 {diag['unknown']}）"
        self._emit_stuck_report(
            reason=(
                f"{detail} 是游戏侧自定义小游戏界面，/state 不含任何可交互元素、"
                f"mod 也没有对应动作 ⇒ 本插件无法推进（已等 {waited} 秒）；需 mod 侧补充交互动作"
            ),
            log_line=(
                "[sts2_d12] 推不动的自定义事件屏：screen=%s unknown=%s actions=%s 已等 %s 秒"
                "（autoplay 意图保持 %s）★ 需 mod 侧新增：%s"
                % (diag["screen"], diag["unknown"], diag["actions"], waited,
                   self._state.autoplay_state, advice)
            ),
            host_text=(
                f"我卡在{detail}这个界面上了 —— 这是个游戏自带的互动小游戏（水晶球那类），"
                f"它不给我能点的东西，我也没长手去点屏幕，你手动点一下就能继续喵。"
            ),
        )

    @staticmethod
    def _unadvanceable_screen_advice(diag: dict[str, Any]) -> str:
        """给出**需要 mod 侧新增什么**（本任务契约要求的「读不到就说清要补什么」）。

        实机现状：mod 把该屏当 `EVENT` **兜底**返回（`GameStateService.cs:741-746`），
        但 `/state` 的 `event` 层是 `null` —— 于是插件拿着 `EVENT` 找不到任何事件动作。
        ⇒ mod 侧至少要补其一：
          (a) 把水晶球的**可交互元素**（每个图块/按钮的 index 或坐标 + 是否可点）暴露成
              某个层（例如 `event.screen_progress` + `event.options[]`，即已有游标机制认识的那个形状），
              这样插件 :656 的 `_advance_custom_event_cursor` 就能自动扫下标推进；
          (b) 或在 mod 内直接实现"自动完成该小游戏"的动作（如 `crystal_sphere_pick(option_index)`），
              由插件照常 `choose_event_option` 派发。
        """
        return (
            "在 GameStateService/GameActionService 里为该屏暴露可交互元素"
            "（建议复用 event.screen_progress + event.options[] 形状，插件已有游标推进逻辑）"
            "或新增专用动作；当前 :state 的 event/selection/modal 全为 null，插件无从下手"
        )

    def _note_step_progress(self, snapshot: dict[str, Any], operation: dict[str, Any]) -> None:
        """动作发出去、mod 也回 ok，但**局面指纹没变** —— 那就是这个动作其实没生效。

        2026-10-07 实测：水晶球事件屏上插件每 2-3 秒重发一次 `choose_event_option`
        （`step_count` 83→93），而金币、事件选项、局面**一字未变**，mod 每次都回 `completed`。
        "拿不到动作"那道闸（:meth:`_note_no_operation`）拦不住这种 —— 所以要有第二道闸：
        同一个动作 + 同一个局面指纹，重复 :data:`NO_PROGRESS_REPEAT_LIMIT` 次就按"卡住"上报
        （同样**不动 autoplay 意图**：玩家没喊停就不许自己停）。
        """
        action_key = f"{operation.get('action_type')}|{sorted((operation.get('kwargs') or {}).items())}"
        state_key = str(snapshot.get("state_signature") or "")
        if not state_key:
            state_key = f"{snapshot.get('screen')}|{snapshot.get('floor')}|{snapshot.get('turn')}"
        if action_key != self._state.progress_action_key or state_key != self._state.progress_signature:
            self._state.progress_action_key = action_key
            self._state.progress_signature = state_key
            self._state.progress_repeat = 1
            self._state.progress_reported = False
            return
        self._state.progress_repeat += 1
        if self._state.progress_reported or self._state.progress_repeat < self.NO_PROGRESS_REPEAT_LIMIT:
            return
        self._state.progress_reported = True
        action_type = str(operation.get("action_type") or "?")
        screen = str(snapshot.get("screen") or "unknown")
        detail = f"{screen} 上重复执行 {action_type} {self._state.progress_repeat} 次而局面没有任何变化"
        self._emit_stuck_report(
            reason=detail,
            log_line=(
                "[sts2_stuck] 动作没生效（无进展）：%s action=%s repeat=%s state=%s（autoplay 意图保持 %s）"
                % (screen, action_type, self._state.progress_repeat, state_key, self._state.autoplay_state)
            ),
            host_text=f"我卡住了：在{detail}，我推不动，你手动推一下喵。",
        )

    def _no_operation_signature(self, snapshot: dict[str, Any]) -> str:
        """「卡在哪」的指纹：界面 + 未识别的节点类型 + 可用动作集合。"""
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = str(snapshot.get("screen") or raw.get("screen") or "")
        unknown = str(raw.get("unknown_screen_type") or "")
        actions = raw.get("available_actions") if isinstance(raw.get("available_actions"), list) else []
        return f"{screen}|{unknown}|{','.join(sorted(str(action) for action in actions))}"

    def _clear_stuck(self) -> None:
        if self._state.stuck_signature or self._state.stuck_reason or self._state.stuck_reported:
            self._state.stuck_signature = ""
            self._state.stuck_since = 0.0
            self._state.stuck_reported = False
            self._state.stuck_reason = ""

    def _note_no_operation(self, snapshot: dict[str, Any]) -> None:
        """拿不到可执行动作时**别静默空转**（而且**绝不改 autoplay 意图**：玩家没喊停就不许自己停）。

        2026-10-07 实测：mod 认不出的界面（水晶球事件的"占卜"子界面）只暴露 `save_and_quit`，
        循环每 0.5 秒空转一次、`step_count` 冻在 2395 整整 **2 小时 40 分**，日志里一句都没有 ——
        玩家只能靠自己发现"它不动了"。所以这里补三件事：

        1. 同一个「卡住指纹」只记一次 WARNING（带未识别界面类型名，方便精确定位到该加哪个 switch 分支）；
        2. 写进状态（`stuck_reason`，状态查询与前端看得到）；
        3. 按「尖塔速报」开关推**一次**给本体，让它开口问玩家。

        跨屏切换时短暂拿不到动作是正常的，所以要等 :data:`NO_OPERATION_STUCK_SECONDS` 才报。
        **只在真的连着游戏时报**：游戏没开时快照是空的，"没有可执行动作"是必然的，
        那种情况已经由 ``last_error`` 表达，不该再让猫娘去问玩家"我卡住了"。
        """
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = str(snapshot.get("screen") or raw.get("screen") or "")
        if not screen or self._state.transport_state != "connected":
            self._clear_stuck()
            return
        if self._map_wait_reason(snapshot):
            # Intentional co-op waiting is not a stalled action or broken screen.
            self._clear_stuck()
            return
        # ★★ D12（t32）：先判「是不是游戏侧推不动的自定义事件屏」。
        #   命中的话走专用降级（把原因/需 mod 补什么讲清楚），而不是笼统的"没有可执行动作"。
        #
        # ⚠️ 我第一版在这里写错了，**打坏了 t29 的既有测例**（全量测试抓到的，1242 passed/2 failed）：
        #   写成 `if (now - stuck_since) >= 阈值 or 指纹不同:` ⇒ 指纹**第一次**建立时
        #   `stuck_since == 0.0` ⇒ `time() - 0` 是巨大的正数 ⇒ **立刻上报** ⇒
        #   t29 的「跨屏切换短暂拿不到动作是正常的，不许报卡住」被破坏。
        # ⇒ 改成**完全复用**下方既有闸的语义（先记账 → 再按阈值/已报判），
        #   只把"报什么"换成专用文案 ⇒ **时序行为与 t29 逐字保持一致**。
        diag = self._crystal_sphere_screen(snapshot)
        special_signature = (
            f"unadvanceable|{diag['screen']}|{diag['unknown']}|{','.join(diag['actions'])}"
            if diag is not None
            else ""
        )
        # ★ 专用形态用自己的指纹（它比通用的更具体），但**走同一个闸**。
        # ★★ t46：把求解器状态编进指纹 —— `computing→failed` 是**有意义的局面变化**
        #   （"在算"变成"等下去也不会好"），必须重新计时并重新报一次，
        #   而不是被 `stuck_reported` 永久压住。
        solver_status = str(getattr(self._state, "solver_status", "") or "")
        signature = (special_signature or self._no_operation_signature(snapshot)) + "|solver=" + solver_status
        now = time()
        if signature != self._state.stuck_signature:
            self._state.stuck_signature = signature
            self._state.stuck_since = now
            self._state.stuck_reported = False
            self._state.stuck_reason = ""
            return
        if self._state.stuck_reported or (now - self._state.stuck_since) < self.NO_OPERATION_STUCK_SECONDS:
            return
        if diag is not None:
            # ★ 命中专用屏：走专用文案（`_note_unadvanceable_screen` 自己置 stuck_reported）。
            self._note_unadvanceable_screen(snapshot, diag)
            return
        if solver_status in ("computing", "failed"):
            # ★ t46：战斗里"没动作"的真因几乎总是求解器 —— 别再笼统地说"界面里没有可执行动作"。
            #   2026-10-24 实测的误诊长这样（actions 里明明有 play_card/end_turn）：
            #     [sts2_stuck] 界面里没有可执行动作：screen=COMBAT actions=['end_turn','play_card',...]
            self._note_solver_stuck_report(solver_status, int(now - self._state.stuck_since))
            return
        self._state.stuck_reported = True
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        screen = str(snapshot.get("screen") or raw.get("screen") or "unknown")
        unknown = str(raw.get("unknown_screen_type") or "")
        actions = raw.get("available_actions") if isinstance(raw.get("available_actions"), list) else []
        waited = int(now - self._state.stuck_since)
        detail = screen + (f"（未识别界面类型 {unknown}）" if unknown else "")
        self._emit_stuck_report(
            reason=f"{detail} 界面里没有可执行动作，已等 {waited} 秒",
            log_line=(
                "[sts2_stuck] 界面里没有可执行动作：screen=%s unknown=%s actions=%s 已等 %s 秒（autoplay 意图保持 %s）"
                % (screen, unknown or "-", sorted(str(action) for action in actions), waited, self._state.autoplay_state)
            ),
            host_text=f"我卡在{detail}这个界面上了，这里没有我能做的事，你手动推一下喵。",
        )

    def _note_solver_stuck_report(self, solver_status: str, waited: int) -> None:
        """战斗里"没动作"而**原因在求解器**时专用的一份报告（t46）。

        ★ 为什么必须与通用文案分开：2026-10-24 的实机日志是这么写的 ——

              [sts2_stuck] 界面里没有可执行动作：screen=COMBAT
                actions=['end_turn', 'play_card', 'save_and_quit']

          actions 里明明有 play_card 与 end_turn，玩家照着这句话只会去怀疑游戏/自己的操作，
          而真因在另一侧（对端没有 `/solver/plan`，每次 0 ms 回 404）。**误诊比沉默更贵。**

        ★ 两种口径：
          · `computing` —— 还在算。这是**正常且被允许**的等待（无读超时），所以文案要说清
            "在等结果、不会降级"，并给出已等秒数，让玩家能判断"再等等"还是"去查"。
          · `failed`    —— 请求本身失败。等下去**永远不会好**，文案必须点名去查什么。
        """
        self._state.stuck_reported = True
        solver = self.solver_status_payload()
        waited_s = int(solver.get("wait_seconds") or 0) or waited
        if solver_status == "computing":
            self._emit_stuck_report(
                reason="求解器正在计算，已等 %d 秒（仍在等结果，不降级策略）" % waited_s,
                log_line=(
                    "[sts2_stuck] 求解器仍在计算：screen=COMBAT 已等 %s 秒"
                    "（本 tick 不发动作、不降级 —— 等不及就换策略会让本来能过的战斗过不了；"
                    "状态见 solver.status=computing）" % waited_s
                ),
                host_text="这一手我还在等求解器算完（已经 %d 秒了），算完就出牌，不会乱打喵。" % waited_s,
            )
            return
        self._emit_stuck_report(
            reason="求解器请求失败：%s（自动玩仍在等，但等下去不会好）" % (solver.get("last_error") or "未知原因"),
            log_line=(
                "[sts2_stuck] 求解器请求失败 screen=COMBAT status=failed last_error=%s"
                " ｜检查：对端是否有 /solver/plan（404=装错 mod / 装的是没有求解器的 fork）、"
                "base_url 是否指向带求解器的实例（18080 vs 38080）"
                % (solver.get("last_error") or "未知")
            ),
            host_text="我的求解器请求失败了（%s），得你看一眼 mod 有没有装对喵。" % (solver.get("last_error") or "未知原因"),
        )

    def _emit_stuck_report(self, *, reason: str, log_line: str, host_text: str) -> None:
        """把"卡住"写到三个地方：日志一次、状态一份、本体一次（按「尖塔速报」开关）。

        两道闸（拿不到动作 / 动作没生效）共用这一份出口，避免两处各写一遍还写歪。
        """
        self._state.stuck_reason = reason
        try:
            self.logger.warning(log_line)
        except Exception:
            pass
        self._emit_status()
        if not getattr(self._state, "host_digest_enabled", True):
            return
        self._push_proactive_to_host(host_text, kind="stuck_alert", priority=8)

    async def execute_operation(self, operation: dict[str, Any] | None) -> dict[str, Any]:
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        before_summary = self._summary_builder.build_snapshot_summary(snapshot) if snapshot else {}
        client = self._require_client()
        result = await self._action_engine.execute(client, snapshot, operation)
        # ★ t20/D9：派发前的药水数量（用于"派发了但没消耗"的可观测性判据）。
        potion_count_before = self._count_potions(snapshot)
        if result.get("status") == "ok":
            self._state.touch_action()
            op = result.get("operation") if isinstance(result.get("operation"), dict) else {}
            # ★★ t20/D9 修复：「派发成功」= mod 回 `completed`，**或**局面确实发生了可观测变化。
            #   原实现把「HTTP 200」当作已执行（`action_engine.execute` 无条件返回 status="ok"，
            #   而 mod 的真实 status 被埋在 `result["result"]["status"]` 里，**从未被读**）。
            #   后果：mod 回 `status="pending", stable=false`（药水被游戏侧 Cancel()、槽位未变）
            #   也被当成执行成功 ⇒ `solver_step_index` 空耗 ⇒ 步骤用尽 ⇒ 重取 ⇒
            #   求解器又给同一建议 ⇒ 无限循环。
            #   实测 run22 X9XP0L8HZWF7 循环 7347 次、卡死 5.88 小时（16:51:13 → 22:44:06）。
            #   ⇒ 下面把真实 status 解析出来，交给 `_note_action_status` 判定是否算「有效」。
            mod_status = self._mod_action_status(result)
            self._state.last_action_status = mod_status
            if op.get("action_type") == "select_deck_card":
                # 选牌光标由"这一下真的按下去了"推动。plan() 是每 tick 都跑的（事件流约 1 次/秒），
                # 若让它自己推进光标，四个候选会在几秒内被规划耗光、屏幕却一张都没选中，
                # 随后永久返回 None —— 猫娘就永久停在这一屏（玩家看到的"卡住"）。
                kwargs = op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}
                self._planner.note_selection_option_pressed(kwargs.get("option_index"))
            if op.get("action_type") == "claim_reward":
                # 同理：领奖励的记账也必须由执行推动。药水栏满时"领药水"发不出去而 mod 照旧回
                # completed，靠这张账才能跳过那一条而不是无限重领同一个下标。
                kwargs = op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}
                self._planner.note_reward_claim_dispatched(kwargs.get("option_index"))
            if op.get("action_type") == "select_character":
                # 选人界面的「随机」同样只按一次：按过就转入 embark，避免"选中后再选一次"的死循环。
                kwargs = op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}
                self._planner.note_character_select_dispatched(kwargs.get("option_index"))
            if op.get("action_type") == "choose_event_option":
                # ★缺陷 A3 修复：事件选项的"已选次数"必须由**真的派发成功**推动。
                # 可重复选择的事件（选完选项仍在、屏幕不变）上，无状态的评分器每轮算出同一个最优解
                # ⇒ 无限重复同一项。实机后果：反复选"消耗生命上限"直到 HP 变 1
                # （用户报告："脚本会一直选择消耗，直接把生命值变成了1"）。
                # 与上面选牌/领奖同一个道理：plan() 每 tick 都跑，用规划记账会凭空放大计数。
                kwargs = op.get("kwargs") if isinstance(op.get("kwargs"), dict) else {}
                self._planner.note_event_option_chosen(
                    self._summary_builder.build(snapshot, runtime_state=self._state),
                    kwargs.get("option_index"),
                )
                # ★ t31：把"我选了哪个选项"告诉实例知识库，供它跨 tick diff 出代价/收益
                #   （契约 §4.3）。**纯观测**：只记录事实，不影响本方法的任何返回值。
                self._note_encounter_event_choice(kwargs.get("option_index"))
            if op.get("action_type") == "use_potion":
                # 战斗用药水的记账："同一回合只喝一瓶"（规格「八、节流」）。只在**真的喝下去之后**记，
                # 与选牌光标/奖励领取同一个道理：规划每 tick 都跑，用规划记账会把机会白白耗光。
                self._state.potion_used_turn_key = self._combat_turn_key(snapshot)
            # 战斗整回合缓存：仅战斗步(出牌/用药/结束回合)推进 line[0].steps 索引；选牌子动作不推进。
            # ★★ t20/D9：**只在这次派发"有效"时才推进**。mod 回 pending 且局面未变 ⇒ 这一步
            #   其实什么都没做，若照样 +1 就是把计划步骤空耗掉 ⇒ 步骤用尽 ⇒ 重取 ⇒ 同一建议 ⇒
            #   死循环（实测 7347 次 / 5.88 小时）。有效性由 `_note_action_status` 判定。
            if op.get("action_type") in {"play_card", "use_potion", "end_turn"}:
                if self._note_action_status(
                    action_type=str(op.get("action_type") or ""),
                    mod_status=mod_status,
                    snapshot=snapshot,
                ):
                    self._state.solver_step_index = (self._state.solver_step_index if isinstance(self._state.solver_step_index, int) else 0) + 1
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
            # ★ t20/D9 可观测性：同一回合连续 3 次 use_potion 而药水数量未减少 ⇒ 告警。
            #   放在 refresh_state **之后**，比的是"派发前 vs 派发后"的真实槽位数量。
            if op.get("action_type") == "use_potion":
                self._note_potion_maybe_no_effect(after_snapshot, potion_count_before)
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

    # ── ★★ t20/D9：「派发成功」的有效性判据 ──────────────────────────────────────
    _VALID_MOD_STATUSES = frozenset({"completed", "ok", "success"})

    def _mod_action_status(self, result: dict[str, Any]) -> str:
        """从 `execute_operation` 的返回里取出 **mod 真实回的动作状态**。

        结构（逐层实证，见 `transport_client._request` :"data = payload.get('data')"）：
            transport `POST /action` → `{"ok":true,"data":{"action":...,"status":"pending",...}}`
            `_request` 已把 `data` 拆出来 ⇒ `action_engine.execute` 的 `result["result"]`
            就是 `{"action":..., "status":"pending","stable":false, ...}`。

        `action_engine.execute` **自己**那条 `result["status"]="ok"` 只是"动作已派发"，
        与 mod 的执行结果无关 —— 这正是原先误判的根源。
        取不到时返回 `""`（视为未知，**不**据此判失败，避免误伤）。
        """
        inner = result.get("result")
        if isinstance(inner, dict):
            raw = inner.get("status")
            if raw is not None:
                return str(raw).strip().lower()
        return ""

    def _note_event_option_locked(self, option_index: int | None) -> None:
        """★★ D11（t28）：把「被游戏锁定」的事件选项 index 转交给 planner 的**本屏禁用集**。

        ★ 为什么经 service 转交而不是直接在 planner 里读 `is_locked`：
          · planner 读 `is_locked` 属**事前预防**（字段存在才有效）；
          · 本函数属**事后校正**（错误驱动，字段缺失也有效）。
          ⇒ 两条腿正交、互为备份。见 `transport_client.STS2ActionRejected.is_locked_option_rejection`。

        ★ 传 `None` 时**什么都不做**（游戏没点名就不猜）—— 保守，避免误禁正常选项。
        """
        if option_index is None:
            return
        try:
            # ★ 用 `self._state.snapshot` —— 它是 tick 每轮写入的**当前快照**
            #   （`runtime_state.py:75`）。★ 不要用 `last_snapshot`：**该字段不存在**
            #   （实测 `hasattr(STS2RuntimeState(), 'last_snapshot') = False`），
            #   若用它，禁用集键会恒为 `"||"`（空屏键）⇒ 禁用集跨屏串味 ⇒ 新的误伤。
            snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
            self._planner.note_event_option_locked(
                self._summary_builder.build(snapshot, runtime_state=self._state),
                option_index,
            )
        except Exception as exc:  # noqa: BLE001
            # 记账失败绝不能把决策路径带崩（与 `note_event_option_chosen` 同口径）。
            self.logger.debug(f"[sts2_d11] note_event_option_locked failed (non-fatal): {exc}")

    def _observe_encounter_memory(self) -> None:
        """★★ t31：把本 tick 的 `/state` 喂给「怪物/事件实例知识库」。

        ## 为什么读 `snapshot["raw_state"]`

        实测（`_t31_snapshape.log`）：归一化后的 `snapshot` 只有 **11 个键**，
        `combat` / `event` / `map` / `run` **全部不在其中** —— `normalize_snapshot()`
        把它们**拍平**掉了。但它把**完整原始载荷**挂在 `snapshot["raw_state"]`（26 键）。
        ⇒ 采集器必须读 `raw_state`，否则拿不到 `combat.enemies` / `event.options`。
        ★ 不要改 `normalize_snapshot`（out of scope 且会影响既有决策链）。

        ## 为什么放在 `refresh_state` 的**末尾**

        `refresh_state` 是唯一的 tick 漏斗（`_autoplay_loop` / `sts2_get_status` /
        各 entry 都经它）。放在末尾是因为：此时 `snapshot` 已定稿、
        决策已经做完 ⇒ **采集不可能影响本 tick 的任何决策**（契约「纯观测」要求）。

        ## 纯观测与容错

        · 只读：把 `raw_state` 交给采集器，采集器**从不写回**（有测例锁住）。
        · 不抛：任何异常都吞掉并 `debug` 记一行 —— 采集是"锦上添花"，
          绝不能因为它让自动游玩停摆（与 `_note_*` 系列同口径）。
        · 开关：`encounter_memory_enabled`（默认开）。
        """
        if not self._encounter_memory_enabled:
            return
        try:
            raw = self._state.snapshot.get("raw_state") if isinstance(self._state.snapshot, dict) else None
            if not isinstance(raw, dict):
                return
            self._encounter_memory.observe(raw)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"[sts2_t31] observe_encounter_memory failed (non-fatal): {exc}")

    def _note_encounter_event_choice(self, option_index: Any) -> None:
        """★ t31：记下"我刚选了哪个事件选项"，供知识库跨 tick diff 出代价/收益。

        ★ 只在**真的派发**之后调（与 `note_event_option_chosen` 同一位置），
          否则"规划了但没执行"会被记成"我选了" ⇒ 代价差分失真。

        ★ `option_index` 非 int 时什么都不做（保守，与 `_note_event_option_locked` 同口径）。
        """
        if not isinstance(option_index, int):
            return
        try:
            raw = self._state.snapshot.get("raw_state") if isinstance(self._state.snapshot, dict) else None
            if not isinstance(raw, dict):
                return
            self._encounter_memory.note_event_choice(raw, option_index)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"[sts2_t31] note_encounter_event_choice failed (non-fatal): {exc}")

    def _note_action_status(
        self,
        *,
        action_type: str,
        mod_status: str,
        snapshot: dict[str, Any] | None = None,
    ) -> bool:
        """判定这次派发是否**有效**，并维护 D9 的「连续无效」计数。返回 `True` 表示有效。

        判据（★ 这是本修复的核心，刻意做窄以免误伤正常路径）：
          · `mod_status` 是 completed/ok/success        ⇒ **有效**，计数清零；
          · `mod_status` 取不到（""）                    ⇒ **有效**（未知不惩罚，保守）；
          · `mod_status == "pending"` 且**局面指纹变了** ⇒ **有效**（长动画等合法过渡）
            ⇒ 计数清零；
          · `mod_status == "pending"` 且**局面指纹未变** ⇒ **无效** ⇒ 同一指纹下计数 +1。

        「连续无效」达到 `heuristic_planner._SOLVER_DEADLOCK_RETRIES`(3) 时，调用既有破局路径
        （把 planner 的 `_solver_deadlock_count` 顶到阈值），由 planner 产出
        `source="solver_deadlock_breaker", reason="solver_deadlock_end_turn"` 的 end_turn。
        ★ 复用既有机制，不新造破局动作。

        为什么用「局面指纹未变」而不是只看 status：mod 在长动画期间也会回 pending，
        那时局面**确实在变**，不该计入（契约明确要求不误伤这类）。

        ★ 判据的精确语义（第一版写错过，被本门抓到）：
          「局面未变」= 与**上一次派发后**观察到的指纹相同 —— 包括**第一次**遇到某指纹。
          第一版把「首次遇到该指纹」也算作"局面变了、合法过渡"，于是每次 pending 都先
          `count=0` 再 `+1` ⇒ 计数**永远到不了 3** ⇒ 破局永不触发（等于没修）。
          正确做法：进入 pending 分支就**无条件** `+1`，只有 status 变为完成态、
          或指纹真的发生变化时，才清零。
        """
        from .loop_runner import _combat_deadlock_signature

        state = self._state
        # 未知 status ⇒ 不惩罚（避免把正常的旧版 mod / 非战斗动作误判为无效）。
        if not mod_status:
            state.invalid_action_signature = ""
            state.invalid_action_count = 0
            return True
        if mod_status in self._VALID_MOD_STATUSES:
            state.invalid_action_signature = ""
            state.invalid_action_count = 0
            return True

        # 至此 status 非完成态 ⇒ 只有"局面真的动了"才算合法过渡。
        # ★ 注意：这里必须与**上一次**记录的指纹比（不是与"是否为空"比）。
        signature = _combat_deadlock_signature(snapshot if isinstance(snapshot, dict) else {})
        board_changed = bool(state.invalid_action_signature) and signature != state.invalid_action_signature
        state.invalid_action_signature = signature
        if board_changed:
            # 局面确实变了 ⇒ 这是合法过渡（长动画等），不计入。
            state.invalid_action_count = 0
            return True

        state.invalid_action_count += 1
        retries = getattr(self._planner, "_SOLVER_DEADLOCK_RETRIES", 3)
        try:
            self.logger.warning(
                "[sts2_d9] 动作无效：mod 回 status=%s 且局面指纹 %s 未变"
                "（连续 %d/%d；action=%s）"
                % (mod_status, signature or "<空>", state.invalid_action_count, retries, action_type)
            )
        except Exception:
            pass
        if state.invalid_action_count >= retries:
            # ★ 复用既有破局路径：把 planner 的死锁计数顶到阈值 + 对齐指纹，让它下一 tick
            #   产出 `solver_deadlock_breaker / solver_deadlock_end_turn` 的 end_turn。
            self._planner._solver_deadlock_signature = signature
            self._planner._solver_deadlock_count = retries
            try:
                self.logger.warning(
                    "[sts2_d9] 连续 %d 次「mod 回 %s 且局面未变」⇒ 判定动作无效，"
                    "转入 end_turn 破局（source=solver_deadlock_breaker, "
                    "reason=solver_deadlock_end_turn）" % (state.invalid_action_count, mod_status)
                )
            except Exception:
                pass
            state.invalid_action_signature = ""
            state.invalid_action_count = 0
        return False

    def _note_potion_maybe_no_effect(self, snapshot: dict[str, Any], before_count: int) -> None:
        """★ t20/D9 可观测性：同一 turn_key 下连续 3 次 `use_potion` 而**药水数量未减少** ⇒ 告警。

        这是"静默"与"可观测"的分界：D9 症状就是药水被游戏侧 `Cancel()`（槽位纹丝不动）
        而插件毫无表示。即使位移判据因故没触发，这条告警也必须让人一眼看见。
        """
        state = self._state
        after_count = self._count_potions(snapshot)
        if after_count < before_count:
            state.potion_no_effect_turn_key = ""
            state.potion_no_effect_count = 0
            state.potion_no_effect_warned = False
            return
        turn_key = self._combat_turn_key(snapshot)
        if turn_key != state.potion_no_effect_turn_key:
            state.potion_no_effect_turn_key = turn_key
            state.potion_no_effect_count = 0
            state.potion_no_effect_warned = False
        state.potion_no_effect_count += 1
        if state.potion_no_effect_count >= 3 and not state.potion_no_effect_warned:
            state.potion_no_effect_warned = True
            try:
                self.logger.warning(
                    "[sts2_d9] ⚠ 同一回合（%s）连续 %d 次 use_potion 被派发但药水数量未减少"
                    "（前=%d 后=%d）⇒ 药水很可能被游戏侧取消、插件未察觉"
                    % (turn_key, state.potion_no_effect_count, before_count, after_count)
                )
            except Exception:
                pass

    @staticmethod
    def _count_potions(snapshot: dict[str, Any] | None) -> int:
        """数当前「还占着槽位的药水」数量（`None` 槽不计）。"""
        if not isinstance(snapshot, dict):
            return 0
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else {}
        potions = combat.get("potions")
        if potions is None:
            potions = raw.get("potions")
        if not isinstance(potions, list):
            return 0
        n = 0
        for item in potions:
            if isinstance(item, dict):
                if item.get("potion_id") or item.get("id") or item.get("name"):
                    n += 1
            elif item:
                n += 1
        return n

    # ---- 策略网络的下载前端（static/policy.html）用的四个入口 --------------------------------

    def _policy_assets_payload(self, message: str) -> dict[str, Any]:
        status = self._policy_advisor.assets.status()
        return {"summary": message, "policy_enabled": self._policy_advisor.enabled, **status}

    async def policy_assets_status(self) -> dict[str, Any]:
        return self._policy_assets_payload("策略网络的下载状态。")

    async def policy_prepare_runtime(self) -> dict[str, Any]:
        """运行环境（Python + PyTorch）不在就开始后台下载。由前端打开页面时调用，不在插件启动时调用。"""
        self._policy_advisor.assets.ensure_runtime()
        ready = self._policy_advisor.assets.runtime_ready()
        return self._policy_assets_payload("运行环境已就绪。" if ready else "正在下载运行环境。")

    async def policy_download_model(self, character: str) -> dict[str, Any]:
        self._policy_advisor.assets.download_model(character)
        return self._policy_assets_payload(f"正在下载 {str(character).upper()} 的模型。")

    async def policy_remove_model(self, character: str) -> dict[str, Any]:
        self._policy_advisor.assets.remove_model(character)
        return self._policy_assets_payload(f"已删除 {str(character).upper()} 的模型。")

    async def get_snapshot(self) -> dict[str, Any]:
        if not self._state.snapshot:
            await self.refresh_state(trigger_sync=True)
        snapshot = self._state.snapshot
        screen = snapshot.get("screen", "unknown") if isinstance(snapshot, dict) else "unknown"
        message = self.t("status.snapshot", default="当前快照：screen={screen}", screen=screen)
        return {"status": "ok", "message": message, "summary": message, "snapshot": snapshot}

    async def execute_planned_operation(self) -> dict[str, Any]:
        # 显式授权：玩家说了"就这个"，所以绕开听安排档位的停手闸门。
        return await self.run_autoplay_step(force=True)

    def start_autoplay(self) -> dict[str, Any]:
        if self._state.standby:
            self._state.autoplay_state = "standby"
            message = self.t("autoplay.start_blocked_standby", default="当前处于 standby 模式，无法启动自动运行。")
            return {"status": "idle", "message": message, "summary": message}
        # "让它自己玩起来" 同时也意味着退出"听安排"：否则启动了自动玩却仍然每步等发话，很反直觉。
        self._apply_control_mode("program")
        self._state.autoplay_state = "running"
        self._state.pause_reason = ""
        self._state.stop_reason = ""
        self._sync_background_polling()
        self._loop_runner.start_autoplay()
        self._apply_mode_danmaku_policy()
        self._emit_status()
        message = self.t("autoplay.started", default="已启动尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def pause_autoplay(self, reason: str = "user") -> dict[str, Any]:
        self._livestream.reset()
        self._state.autoplay_state = "paused"
        self._state.pause_reason = reason
        self._sync_background_polling()
        self._apply_mode_danmaku_policy()
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
        self._apply_mode_danmaku_policy()
        self._emit_status()
        message = self.t("autoplay.resumed", default="已恢复尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message}

    def stop_autoplay(self, reason: str = "manual") -> dict[str, Any]:
        self._livestream.reset()
        self._state.autoplay_state = "standby" if self._state.standby else "idle"
        if self._state.standby:
            # 待机期间玩家明确喊停：这条"停"要活过取消待机（否则一取消待机又自己玩起来了）。
            self._state.autoplay_state_before_standby = "idle"
        self._state.stop_reason = reason
        self._state.pause_reason = ""
        self._sync_background_polling()
        self._apply_mode_danmaku_policy()
        self._emit_status()
        message = self.t("autoplay.stopped", default="已停止尖塔自动运行。")
        return {"status": "ok", "message": message, "summary": message, "stop_reason": reason}

    def set_standby(self, standby: bool) -> dict[str, Any]:
        normalized = self._apply_control_mode("standby" if standby else "program")
        return self._mode_controller.describe(normalized) | {"mode": normalized}

    #: ════════════════════════════════════════════════════════════════════════
    #: ★★ 弹幕 ⟷ 模式 的耦合规则（冻结区，改前必读）
    #: ════════════════════════════════════════════════════════════════════════
    #:
    #: 玩家指示（2026-10-18）：
    #:   「自动游玩的时候关闭弹幕，陪玩模式下才可以开启，
    #:     然后弹幕保留出牌建议（陪玩是玩家自己玩，看不到求解器，
    #:     所以用弹幕提醒）」
    #:
    #: ⇒ 判据不是"弹幕开关"，而是**玩家在不在场**：
    #:
    #:   ┌────────────┬──────────┬────────────┬────────────────────────────┐
    #:   │ 模式       │ 谁在玩   │ 弹幕       │ 出牌建议                   │
    #:   ├────────────┼──────────┼────────────┼────────────────────────────┤
    #:   │ 自动游玩   │ 猫娘     │ **关闭**   │ —（她自己就是求解器的执行端）│
    #:   │ 陪玩模式   │ **玩家** │ **开启**   │ ★ **必须有**                │
    #:   └────────────┴──────────┴────────────┴────────────────────────────┘
    #:
    #: **为什么陪玩必须有出牌建议**：玩家自己在打，**看不到求解器**，
    #: 弹幕是唯一的告知渠道。没有它，陪玩就只是"猫娘在旁边看"，
    #: 而玩家拿不到任何决策支持。
    #:
    #: **为什么自动游玩要关**：没人在看弹幕，它只是屏幕噪声 + 白烧 LLM 调用。
    #:
    #: ★ 建议的**来源与口吻**（与战斗纯求解器改造一致）：
    #:   * 来源 = **求解器**（和自动游玩同一个权威来源，不能是第二意见）
    #:   * 口吻 = **第一人称**（"这回合我打『打击』"）
    #:     —— 弹幕是猫娘在说，不是"插件转述求解器"。
    #: ════════════════════════════════════════════════════════════════════════

    def _apply_mode_danmaku_policy(self) -> dict[str, Any]:
        """按当前模式同步弹幕开关。返回 {'changed': bool, 'enabled': bool, 'reason': str}。

        ★ 触发点：`start_autoplay` / `stop_autoplay` / `pause_autoplay` /
          `resume_autoplay` / `set_companion_mode`（另见 `set_coop_danmaku_context`）。
        ★ 只在**开与关互斥**的两种主动模式下自动改；`advise`/`standby` 不动用户的选择
          （那两种是"玩家在主导但没明确要陪玩"，擅自关掉会让人以为弹幕坏了）。

        ★★ 优先级（玩家口径 2026-10-19，**从高到低**）：

            coop（联机）  >  autoplay（自动游玩）  >  陪玩（companion）

        玩家原话：「autoplay 优先，coop」＋「coop模式>autoplay>陪玩」。

        为什么必须写明顺序（而不是各写一个 if）：这三个是**不同的轴**，
        而且**可以同时成立**——
          * `coop`  = **联机会话**（两人一起玩；见 `active_instance` / `open_coop_room`）
          * `autoplay` = 插件**替你打**
          * `陪玩`   = 插件**只点评、你自己打**
        ⇒ 老实现是「陪玩 if 在前，autoplay elif 在后」，
          于是**陪玩一开就压过 autoplay** ⇒ 自动游玩时弹幕反而开着，
          与玩家指令「自动游玩的时候关闭弹幕」**正好相反**（实测 0 条 danmaku_policy
          日志 + 93 条推送 = 弹幕确实没被关掉）。
        ⇒ 现在按上表**从高到低**判，谁高听谁的：
          * coop 且**没在**autoplay ⇒ **开**（联机时要让同伴看见猫娘点评）
          * autoplay（不论是否 coop）⇒ **关**（★ autoplay 压过陪玩，也压过 coop 的"开"）
          * 只有陪玩 ⇒ **开**
          * 都没有 ⇒ 不动（`no_policy`）

        ★ 注：`autoplay` 的语义是"没人看着屏幕"，弹幕刷出去没人读、还挤占 LLM 与日志，
          所以它一旦在跑就关；这是玩家明确要求的方向。
        """
        companion = bool(self._cfg.get("companion_mode_enabled", False))
        autoplay_running = self._state.autoplay_state in {"running", "paused"}
        # ★ coop = 联机会话中。判据取"活动实例已是 coop 里的猫娘进程"或"联机房间已开"，
        #   两者任一成立即视为联机（见 switch_active_instance / open_coop_room）。
        coop_active = self._is_coop_active()

        # ★★ 从高到低判，先命中者胜。
        if autoplay_running:
            # autoplay 最高优先（含压过 coop）⇒ 关。
            want = False
            reason = "autoplay"
        elif coop_active:
            want = True
            reason = "coop"
        elif companion:
            want = True
            reason = "companion_mode"
        else:
            return {"changed": False, "enabled": self._danmaku_enabled, "reason": "no_policy"}
        if want == self._danmaku_enabled:
            return {"changed": False, "enabled": want, "reason": reason}
        self.set_danmaku_enabled(want)
        self.logger.info(
            "[sts2_danmaku_policy] 弹幕随模式自动%s（reason=%s；优先级 coop>autoplay>陪玩）",
            "开启" if want else "关闭",
            reason,
        )
        return {"changed": True, "enabled": want, "reason": reason}

    def _is_coop_active(self) -> bool:
        """当前是否处于**联机会话**中。

        ★ 判据（任一成立即为联机）：
          1. `active_instance == "catgirl"` —— 房间开好了、插件已接管被拉起的猫娘进程
             （`open_coop_room(host=True)` 成功后 `adopt_coop_catgirl` 会切过去）；
          2. `_coop_room_open` —— 显式记录的联机房间状态（open/close 时置位）。

        ★ 保守方向：**取不准时返回 False**。
          理由：coop 分支的后果是"开弹幕"，若误判成联机就会在单人局里凭空打开弹幕；
          而 autoplay 关弹幕那一条**不依赖本函数**（它先判），所以保守不会削弱玩家指令。
        """
        if getattr(self._state, "active_instance", self.INSTANCE_HOST) == self.INSTANCE_CATGIRL:
            return True
        return bool(getattr(self, "_coop_room_open", False))

    def set_danmaku_enabled(self, enabled: bool, *, sync_mod: bool = True) -> dict[str, Any]:
        """游戏内弹幕总开关（插件侧真闸门）。

        关闭后 _push_catgirl 不再 POST /danmaku：**改走宿主点评**（`push_message`，见 t51 的
        `_push_host_commentary`）或直接静音，取决于 `host_commentary_when_danmaku_off`。
        同时把 mod 侧的 /danmaku 收口（danmaku_push_enabled）写掉并清屏，避免重连窗口期
        或其它本地调用者把弹幕漏出来。与 catgirl_llm_enabled 无关——后者只切换点评文本来源。
        """
        self._danmaku_enabled = bool(enabled)
        self._cfg["danmaku_enabled"] = self._danmaku_enabled
        # 开关变化后清掉同步去重状态，重新打开时第一条点评不会被旧指纹吃掉。
        self._state.last_sync_fingerprint = ""
        self._state.last_push_scene_key = ""
        self._state.last_push_reason = ""
        self._emit_status()
        # ★★★ t51：让 docstring 那句承诺成真 —— 把开关同步给 mod（`danmaku_push_enabled`
        #   + 关闭时清屏）。此前只有 UI 入口（neko_interface）会做，于是"模式策略自动关弹幕"
        #   只关了一半：mod 侧仍收弹幕、屏幕上的旧弹幕也不清。
        #   `sync_mod=False`：调用方自己会 `await sync_danmaku_gate_to_mod()` 并拿结果时用，
        #   避免同一次开关写两遍 mod。
        if sync_mod:
            self._schedule_mod_danmaku_gate_sync()
        message = (
            self.t("danmaku.enabled", default="已打开游戏内猫娘弹幕。")
            if self._danmaku_enabled
            else self.t("danmaku.disabled", default="已关闭游戏内猫娘弹幕（自动游玩不受影响）。")
        )
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "enabled": self._danmaku_enabled,
        }

    async def sync_danmaku_gate_to_mod(self) -> dict[str, Any]:
        """把弹幕开关写到 mod（POST /config danmaku_push_enabled），关闭时顺带清屏。

        失败是良性的：插件侧的 _push_catgirl 已经挡住了推送，这里只是纵深防御 + 立刻清掉屏幕上
        还在飘的旧弹幕。
        """
        result: dict[str, Any] = {"applied": False, "cleared": False}
        try:
            client = self._require_client()
        except Exception:
            return result
        try:
            await client.set_config(danmaku_push_enabled=self._danmaku_enabled)
            result["applied"] = True
        except Exception as exc:
            self.logger.debug(f"[sts2] sync danmaku gate to mod failed (non-fatal): {exc}")
        if not self._danmaku_enabled:
            try:
                await client.clear_danmaku()
                result["cleared"] = True
            except Exception as exc:
                self.logger.debug(f"[sts2] clear danmaku overlay failed (non-fatal): {exc}")
        return result

    def _schedule_mod_danmaku_gate_sync(self) -> None:
        """fire-and-forget：把插件侧的开关同步到 mod（`danmaku_push_enabled` + 关闭时清屏）。

        ★ 为什么要补这一步（t51）：`set_danmaku_enabled` 的 docstring 一直写着"同时把 mod 侧的
          `/danmaku` 收口也一起写掉并清屏"，但函数体里**没有**做 —— 真正写 mod 的
          `sync_danmaku_gate_to_mod()` 只被 UI 入口（`neko_interface.py`）调用。
          后果：模式策略自动关弹幕时（autoplay 启动那一下），mod 侧的收口与屏幕上残留的旧弹幕
          都不动 ⇒ 与注释承诺不一致，观感是"关了还看得见老弹幕 / 开了没反应"。
        ★ 失败一律吞掉：这是纵深防御，插件侧的 `_push_catgirl` 闸门才是主闸。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 没有运行中的循环（启动/收尾阶段）：跳过同步，等下一次出口动作
        try:
            task = loop.create_task(self.sync_danmaku_gate_to_mod())
            # fire-and-forget 必须取一次异常，否则 asyncio 会报 "never retrieved"。
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:
            pass

    async def set_route(self, *, mode: str = "safe", pick: Any = None, index: Any = None) -> dict[str, Any]:
        """对话式地图协作：定路线（safe）/ 跟你一样（mirror）/ 指定位置（pick）。

        直接调用 mod 的 choose_map_node，不依赖自动玩循环是否在跑——你说一句就立刻生效。
        重复调用就是改票（"不对不对我选错了"），只要地图还没推进。
        """
        client = self._require_client()
        raw_state = await client.get_state()
        raw_actions = await client.get_available_actions()
        snapshot = normalize_snapshot(raw_state, raw_actions)
        classification = self._state_machine.classify(snapshot)
        snapshot = {**snapshot, "classification": classification}
        summary_context = self._summary_builder.build(snapshot, runtime_state=self._state)
        strategy_context = self._strategy_repository.build_context(
            {**snapshot, "summary_context": summary_context}
        )
        preferences = (
            strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}
        )
        decision = self._planner.resolve_route(
            summary_context,
            mode=mode,
            pick=pick,
            index=index,
            preferences=preferences,
            # 把现场快照带上，"定个路线（safe）"才会走与自动选路同一套链式递推评估，
            # 而不是退回"只看眼前一步"。见 heuristic_planner.evaluate_map_route。
            snapshot=snapshot,
        )
        base = {
            "mode": decision.get("mode"),
            "node_count": decision.get("node_count", 0),
            "nodes": decision.get("nodes", []),
        }
        if not decision.get("ok"):
            message = str(decision.get("message") or "现在没办法投地图票。")
            return {"status": "error", "message": message, "summary": message, "error": decision.get("error"), **base}

        option_index = decision.get("option_index")
        await client.execute_action("choose_map_node", option_index=option_index)
        self._emit_status()
        message = str(decision.get("message") or f"已投第 {decision.get('position')} 个节点。")
        self.logger.info(
            "[sts2_set_route] mode=%s pick=%s index=%s -> option_index=%s node_type=%s",
            decision.get("mode"),
            pick,
            index,
            option_index,
            decision.get("node_type"),
        )
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "option_index": option_index,
            "position": decision.get("position"),
            "node_type": decision.get("node_type"),
            **base,
        }

    def set_companion_mode(self, enabled: bool, *, emit_status: bool = True, push_message: bool = True) -> dict[str, Any]:
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
        message = self.t("companion.enabled", default="我来陪你玩，陪玩模式开启。") if enabled else self.t("companion.disabled", default="我先歇一会儿，陪玩模式关掉了。")
        # ★ 陪玩 = 玩家自己在玩 ⇒ 弹幕要开（那是他唯一能看到出牌建议的渠道）。
        self._apply_mode_danmaku_policy()
        if emit_status:
            self._emit_status()
        if enabled:
            self._sync_background_polling()
            if push_message:
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

    def _cfg_use_event_stream(self) -> bool:
        try:
            return bool(self._cfg.get("use_event_stream", True))
        except Exception:
            return True

    def _cfg_fallback_poll_interval(self) -> float:
        try:
            return max(5.0, float(self._cfg.get("poll_fallback_seconds", 5.0) or 5.0))
        except (TypeError, ValueError):
            return 5.0

    def _cfg_action_interval(self) -> float:
        return float(self._cfg.get("action_interval_seconds", 1.5) or 1.5)

    def _cfg_memory_report_interval(self) -> float:
        """内存自检的周期（秒，t52）。`<=0` = 关掉自检。

        ★ 为什么需要它：玩家反馈"玩尖塔时 NEKO 占用越来越大"，而 NEKO 是 13 个进程
          （Electron 壳 / 主 server / 每个插件一个宿主进程 / 多个 renderer）。没有一条
          "本进程 RSS + 我方容器长度"的时间序列，就只能靠猜是哪一块在涨。
        ★ 默认 300s：5 分钟一行的量级既可看出斜率，又不会污染日志（对照：插件平时
          0.5–1.5 行/秒）。
        """
        try:
            return float(self._cfg.get("memory_report_interval_seconds", 300))
        except (TypeError, ValueError):
            return 300.0

    def _cfg_combat_replay_interval(self) -> float:
        """战斗里**按整回合快照续行**时的出牌间隔（秒，t47）。

        ★ 为什么它必须与 `action_interval_seconds` 分开：`/solver/plan` 返回的是**整回合**
          的出牌顺序（`line[0].steps`），tick 只是按 `solver_step_index` 逐步执行、不重算。
          这种 tick 的耗时与"上一次搜索花了多久"毫无关系，却被 t45 的自适应节拍按搜索耗时
          定价（顶到 3s 上限）⇒ 整回合每张牌白等 3 秒。分开之后：
            · 需要**重算**的 tick（回合开始 / 局面漂移 / 步骤用尽）→ 仍走自适应节拍（安全）；
            · 纯**回放**的 tick → 用本值（快）。
        ★ 回退值 0.5s 的依据：这是实机卡牌动画的量级；比它更快多半只会让 mod 回 `pending`
          （不推进步骤、也不作废计划，代价是退避重试）。要更快就把它调小，玩家配置有否决权。
        """
        try:
            value = float(self._cfg.get("combat_replay_interval_seconds", 0.5))
        except (TypeError, ValueError):
            return 0.5
        return value if value > 0.0 else 0.05

    def _cfg_companion_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("companion_push_probability", 0.2))
        except Exception:
            return 0.2
        return min(1.0, max(0.0, value))

    def _cfg_autoplay_push_probability(self) -> float:
        try:
            value = float(self._cfg.get("autoplay_push_probability", 0.35))
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
        previous_mode = self._state.control_mode
        self._state.control_mode = normalized
        if self._state.standby:
            if not previous_standby:
                # 记下"进待机之前想干什么"：待机是**临时停手**，取消时要回到原来的意图。
                self._state.autoplay_state_before_standby = self._state.autoplay_state
            self._state.autoplay_state = "standby"
        elif previous_standby and self._state.autoplay_state == "standby":
            # 退出待机：还回进入前的意图。以前这里写死 "idle"，而 `_autoplay_loop` 只认 "running"，
            # 结果"取消待机"会把自动游玩永久停摆（实测 step 冻住）。空记忆时按开机意图兜底。
            remembered = self._state.autoplay_state_before_standby
            if not remembered:
                remembered = "running" if bool(self._cfg.get("autoplay_on_start", False)) else "idle"
            self._state.autoplay_state = remembered
            self._state.autoplay_state_before_standby = ""
            # 待机期间轮询可能被停过，恢复意图后必须把轮询一起拉起来（幂等）。
            self._sync_background_polling()
        # 任何档位切换都要打断当前决策：否则 program<->advise 切换后，循环还会按旧档位把
        # 已经算好的那一步执行掉（切到"听安排"却仍然走了一步）。
        if previous_mode != normalized:
            self._state.interrupt_requested = True
            self._state.interrupt_reason = "mode_change"
        return normalized

    async def set_control_mode(self, mode: str) -> dict[str, Any]:
        """切换猫娘的控制档位。

        - ``program``（自己玩）：照常决策并执行。默认。
        - ``advise``（听安排）：非战斗决策界面只算不做，等玩家发话；战斗照常自动打。
        - ``standby``（停手）：连计划都不算，并且会让 mod 一起停手。
        """
        normalized = self._apply_control_mode(mode)
        sync = await self.sync_control_mode_to_mod()
        self._emit_status()
        message = {
            "program": "好，我自己玩，你不用一直盯着。",
            "advise": "好，我等你发话：每一步先算给你看，你说走我才走。战斗我会照常打。",
            "standby": "好，我彻底停手了。",
        }.get(normalized, "好，收到。")
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "mode": normalized,
            "mod_sync": sync,
        }

    async def sync_control_mode_to_mod(self) -> dict[str, Any]:
        """把档位同步给 mod，让它自己的自动玩驱动一起停手。

        只停插件等于没停：mod 有独立的 `NekoAutoplayDriver`，会照常决策并执行。
        映射：program→auto（自己玩）、advise→advise（非战斗界面停手）、
        standby→hold（所有界面停手，含战斗，回合会停住——这是该档位的预期语义）。
        失败是良性的：插件侧规划器已经停手，最坏情况是 mod 继续自己玩。
        """
        mapped = {"program": "auto", "advise": "advise", "standby": "hold"}.get(
            self._mode_controller.normalize(self._state.control_mode), "auto"
        )
        try:
            client = self._require_client()
        except Exception:
            return {"applied": False, "mode": mapped}
        try:
            await client.set_config(coop_control_mode=mapped)
            return {"applied": True, "mode": mapped}
        except Exception as exc:
            self.logger.debug(f"[sts2] sync control mode to mod failed (non-fatal): {exc}")
            return {"applied": False, "mode": mapped}

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
        if self._state.force_replan_requested:
            # 上次动作被拒 = 手里的现场/计划过期了，必须重算，别拿旧现场再撞一次。
            return True
        if self._state.interrupt_requested:
            return True
        operation = snapshot.get("agent_operation") if isinstance(snapshot.get("agent_operation"), dict) else None
        if operation is None:
            return True
        decision_epoch = int(operation.get("decision_epoch") or 0)
        return decision_epoch < self._state.guidance_generation

    @staticmethod
    def _snapshot_state_name(snapshot: dict[str, Any]) -> str:
        classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
        return str(classification.get("state_name") or "").strip().lower()

    def _should_hold_for_orders(self, snapshot: dict[str, Any]) -> bool:
        """"听安排"档位下是否该停手等玩家发话。

        战斗不在停手清单里（玩家替不了猫娘出牌），主菜单/组队大厅/选角色也不在
        （那是猫娘加入房间的必经流程）。判定细节见 state_machine.should_hold_for_orders。
        """
        if not self._mode_controller.describe(self._state.control_mode).get("advise"):
            return False
        return should_hold_for_orders(self._snapshot_state_name(snapshot), mode="advise")

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
        """记一次循环错误——**但绝不把自动玩打成终止态**。

        历史教训（这就是"猫娘卡住不动"的真因）：这里曾经写
        ``autoplay_state = "error" if autoplay_state == "running" else autoplay_state``，
        而 ``loop_runner._autoplay_loop`` 只在 ``autoplay_state == "running"`` 时才干活——
        于是**一次**被 mod 拒收的动作（例如缓存计划里的 target_index 越界）就让自动玩永久空转，
        没有任何代码把它改回 running，玩家只能看到猫娘站着不动。

        现在职责分清：
          - ``autoplay_state`` 表示**意图**（running / paused / standby / idle），只由玩家指令改变；
          - ``transport_state`` / ``last_error`` / ``consecutive_errors`` 表示**健康**。
        出错只记健康信息，循环下一轮自己重试（退避见 ``_autoplay_retry_backoff``）。
        """
        self._state.transport_state = "error"
        self._state.last_error = str(exc)
        self._state.consecutive_errors += 1
        if isinstance(exc, STS2ActionRejected) and exc.retryable:
            # 状态漂移类拒绝：被拒的那一步没有落地，而缓存计划与手里的现场都是按"旧局面"算的 ——
            # 一并作废，下一轮先强制重刷 /state 再按现场重算，通常这一步就能过。
            self._invalidate_combat_plan_cache(reason=f"rejected:{exc.code}")
            self._state.force_replan_requested = True
            self.logger.info(
                "[sts2_recover] 动作被拒，已丢弃缓存的战斗计划并继续: code=%s action=%s screen=%s",
                exc.code,
                exc.action_name,
                exc.screen,
            )
        # ★★ D11（t28）：EVENT 屏「选项被锁」——**与上面的 retryable 分支互斥且互补**。
        #
        #   实测：mod 对 locked 明确回 `retryable: false`（真响应逐字见
        #   `transport_client.STS2ActionRejected.LOCKED_OPTION_MESSAGE_MARKERS` 注释），
        #   ⇒ 上面的分支**不会**执行（全日志 `[sts2_recover] 动作被拒` = **0** 条已独立证实）。
        #   ⇒ 所以"重算"从未被触发；即便触发，EVENT 屏**没有战斗计划**可作废，
        #      重算仍是按**同一未变局面**算 ⇒ 得到**同一被锁选项** ⇒ 永久卡死。
        #
        #   ★ 处置：游戏**精确点名**了哪个 index 被锁（`details.option_index`）
        #     ⇒ 直接把它记入本屏禁用集，下一 tick 起自动改选次优**未锁**项。
        elif isinstance(exc, STS2ActionRejected) and exc.is_locked_option_rejection:
            locked_index = exc.locked_option_index
            try:
                self.logger.warning(
                    "[sts2_d11] 事件选项被游戏锁定：code=%s action=%s option_index=%s "
                    "⇒ 记入本屏禁用集，改选次优未锁项（重算不会改变结论）"
                    % (exc.code, exc.action_name, locked_index)
                )
            except Exception:
                pass
            self._note_event_option_locked(locked_index)
        self._emit_status()

    def _invalidate_combat_plan_cache(self, *, reason: str = "") -> None:
        """丢弃整回合缓存的 /solver/plan，让下一轮按现场重新取计划。

        ``solver_step_index`` 一并归零：新计划是**从当前局面**算出来的，下标自然从头开始。
        注意只归零、不清状态——被拒的那一步本来就没执行。
        """
        try:
            self._loop_runner.invalidate_combat_plan_cache()
        except Exception as exc:
            self.logger.debug(f"[sts2_recover] invalidate combat plan cache failed (non-fatal): {exc}")
        if isinstance(self._state.solver_step_index, int) and self._state.solver_step_index != 0:
            self._state.solver_step_index = 0
        if reason:
            self.logger.info("[sts2_recover] 战斗计划缓存作废 (%s)", reason)

    def _autoplay_retry_backoff(self) -> float:
        """自动玩出错后的退避：连错越多等越久，封顶 8 秒。

        与 ``_cfg_action_interval`` 同基准，保证"偶尔被拒"几乎无感（约 1.5 秒后重试），
        而 mod 真的不在时也不会每秒空转刷日志。
        """
        try:
            base = max(0.5, float(self._cfg_action_interval()))
        except (TypeError, ValueError):
            base = 1.5
        errors = max(1, int(self._state.consecutive_errors or 1))
        return min(base * (2 ** (errors - 1)), 8.0)

    def _companion_mode_active(self) -> bool:
        return bool(self._cfg.get("companion_mode_enabled", self._cfg.get("neko_commentary_enabled", True))) and bool(self._cfg.get("neko_commentary_enabled", True))

    def _should_keep_polling(self) -> bool:
        # "error" 也在名单里：即使旧版本把自动玩留在了 error 态，也**必须继续轮询** ——
        # 否则外部控制租约一过期，mod 就以为插件走了，游戏彻底没人驱动（曾是"卡住"的最后一环）。
        return bool(self._cfg.get("companion_mode_enabled", False)) or self._state.autoplay_state in {"running", "paused", "error"}

    def _sync_background_polling(self) -> None:
        if self._should_keep_polling():
            self._loop_runner.start_background()
            return
        self._loop_runner.stop_background_sync()

    # 奖励类界面属于**上一个房间**（战斗/事件打完才发奖），不另起一段路线。
    _REWARD_LIKE_SCREENS = frozenset({"REWARD", "CARD_SELECTION_REWARD", "CARDS_VIEW"})
    # 路线图最多记多少个房间（防极端情况膨胀）。
    _ROUTE_MAX_ROOMS = 80

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

    # ---- 弹幕双线参数 -------------------------------------------------------------------
    # 高风险判据：血比低于此值，或敌人这一下的意图总伤害 >= 当前生命的一半。
    HIGH_RISK_HP_RATIO = 0.5
    #: ★★★ t51：弹幕关着时，「把点评改走宿主（猫娘在聊天里说）」的节流（秒）。
    #:
    #: 玩家口径（2026-10-26）：「关闭弹幕，但是可以用 push_message 让猫娘点评一下」。
    #: 游戏内弹幕在 autoplay 期间是**故意关掉的**（2026-10-19 指令：代打时没人盯屏幕，
    #: 弹幕刷出去没收益还挤占 LLM 与日志）；但"没人看弹幕"不等于"不用说话" ——
    #: 聊天窗口里的猫娘正好是那个该说话的地方，所以换一条通道，而不是把弹幕闸门整个打开。
    #:
    #: 为什么比弹幕节流得更狠（45s vs 10s 的 LLM 间隔）：`push_message` 带
    #: `ai_behavior="respond"`，每推一次都让本体**立刻开一次口** = 一次完整 AI 回合（有 token 成本）。
    #: 45 秒 ≈ 一个回合内最多说一句，既不冷场也不会变成刷屏。
    HOST_COMMENTARY_MIN_INTERVAL_SECONDS = 45.0
    # 给本体的局势速报（秒）。**每次推送都会让本体开一次口**（ai_behavior="respond"），
    # 所以这里不能像"只喂上下文"时那么密 —— 25 秒会让它一分钟讲两三次，成了刷屏。
    HOST_DIGEST_MIN_INTERVAL_SECONDS = 75.0
    # 拿不到可执行动作持续多久才算"卡住"（要记日志 + 告诉本体）。跨屏切换时短暂拿不到动作是正常的：
    # 实测 2026-10-07 那次是 mod 认不出界面（只剩 save_and_quit），一卡就是两小时四十分。
    NO_OPERATION_STUCK_SECONDS = 20.0
    # 同一个动作 + 同一个局面指纹，重复多少次算"动作没生效"（见 service._note_step_progress）。
    # 取 8 是留足余地：正常动作（打牌/选路）总会改变局面指纹，重复到 8 次一定是不生效了。
    NO_PROGRESS_REPEAT_LIMIT = 8

    # ---- 战斗中用药水（规格 docs/shop_scoring.md 「八」，玩家口径 2026-10-07）----------------
    #
    # 玩家原话：「还有每次对战如果损血大于8就鼓励使用药水」。
    # 决策本身是纯函数（`planner.decide_combat_potion`）；这里负责三件只有服务层能做的事：
    #   1. 只在这一步真的能派发 `use_potion` 时才抢在战斗计划前面（否则白抢一步，还挡掉出牌）；
    #   2. "同一回合只喝一瓶"的记账（runtime_state.potion_used_turn_key，由**真的喝下去**推动）；
    #   3. 一句 [sts2_potion] 日志（去重），让玩家事后能核对"为什么喝/为什么没喝"。

    def _combat_turn_key(self, snapshot: dict[str, Any]) -> str:
        """"本回合"的键：`本局|层|房间|回合`。

        为什么不能只用回合号：两场战斗都从回合 1 开始，上一场喝过药的记账会把这一场也一起挡掉
        （"同一回合只喝一瓶"于是变成"同一场战斗只喝一瓶"）。层 + 房间标识（遭遇开始时的敌人组合）
        一起用就分得开了；读不到就是空串（调用方据此退化成"不记账"，也就是不拦）。
        """
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        turn = raw_state.get("turn")
        if turn is None:
            turn = combat.get("turn")
        if turn is None:
            return ""
        run_id = str(snapshot.get("run_id") or raw_state.get("run_id") or "")
        floor = self._safe_int(snapshot.get("floor")) or 0
        room = str(getattr(self._state, "combat_room_id", "") or "")
        return f"{run_id}|{floor}|{room}|{turn}"

    def _combat_potion_override(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """要不要抢在战斗计划前面先喝一瓶药水；不需要就返回 None（一切照旧）。

        只在**战斗中**、且 `use_potion` 真的在册（= 手里有能用的药水）时才算 —— 否则抢一步只会换来
        "没有合法动作"，还可能把本来要出的那张牌吞掉。注意 mod 的 `use_potion` 对
        `PotionUsage.AnyTime` 的药水**在战斗外也在册**（`IsPotionUsable`），所以必须自己带 `in_combat` 闸门。
        """
        if not bool(snapshot.get("in_combat")):
            return None
        actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        if not any(str(action.get("type") or "").strip().lower() == "use_potion" for action in actions if isinstance(action, dict)):
            return None
        turn_key = self._combat_turn_key(snapshot)
        already_used = bool(turn_key) and turn_key == str(getattr(self._state, "potion_used_turn_key", "") or "")
        try:
            decision = self._planner.decide_combat_potion(snapshot, already_used_this_turn=already_used)
        except Exception as exc:  # 规则本身出问题绝不该把自动玩拖停
            self.logger.debug(f"[sts2_potion] 用药水决策异常（忽略）: {exc}")
            return None
        self._log_potion_decision(snapshot, turn_key, decision)
        if not decision.get("use"):
            return None
        kwargs: dict[str, Any] = {"option_index": decision.get("option_index")}
        if decision.get("target_index") is not None:
            kwargs["target_index"] = decision.get("target_index")
        return {
            "action_type": "use_potion",
            "kwargs": kwargs,
            "confidence": 0.86,
            "source": "heuristic",
            "reason": "potion_lethal_save" if decision.get("lethal") else "potion_incoming_damage",
        }

    def _crystal_sphere_override(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        """占卜（水晶球）屏上代替规划器发 `divine_crystal_sphere`；不需要就返回 None。

        ## 为什么必须由插件出这一手

        `mod` 侧（t33 已改）给这一屏补了 `crystal_sphere` 状态层与 `divine_crystal_sphere` 动作，
        但**规划器认不出它**：这一屏的 `screen` 是 `EVENT`（mod 用 `ResolveScreenFromStaticRoom()`
        兜底猜的），而 `event` 层仍是 null ⇒ `heuristic_planner` 的 `state_name == "event"` 分支
        找不到 `choose_event_option` ⇒ 规划不出动作 ⇒ 空转。

        ⇒ 所以这里像 `_combat_potion_override` 一样**抢在规划器前面**出动作。

        ## 为什么不能等它自己"掉进游标"

        :meth:`_advance_custom_event_cursor` 作用在**已经规划出来的** `choose_event_option` 上，
        可这一屏根本没有那个动作 ⇒ 游标机制够不着（它的早退条件不止 `:672`，
        更根本的是上游压根没产出动作）。t33 契约里写的「若 mod 补了 event 层，
        则 :672 早退应能自然放开」——**实测并不成立**：mod 补的是 `crystal_sphere` 层，
        不是 `event.screen_progress`，所以那条路仍然不通。本方法就是补上的那一段。

        ## 揭哪一块

        mod 侧的 `crystal_sphere` 层给出两类清单：
        · `revealed_cells` —— **已经揭开**的格子（含 `item_type`：CardReward/Curse/Gold/Potion/Relic）；
        · `candidates` —— **可点候选**（当前隐藏格）的坐标清单。

        ★ 为什么必须要有 `candidates` 而不只是 `cells_hidden` 这个数字：
          占卜的核心决策就是"揭哪一块"，而**纯数量无法支撑决策** ——
          只知道"还有 37 块没揭"时，插件只能按下标瞎点。
          （这正是队长在 t33 里指出的能力缺口；与 CANON §23
          「枚举要给出**有哪些可能**，不只是**有多少**」同源。）

        ## 本方法的取格策略（**保守但可解释**）

        按 `candidates` 顺序推进、到尾回绕，每次都记住自己揭的是哪一块。理由：
        这一屏"哪块值得揭"在揭开前**信息不完全**（不知道是钱还是诅咒），
        逐个扫过一定能穷尽，比"死盯一块反复空点"稳得多（与 :656 游标同款理由）。
        ★ 诚实的边界：这是**扫格**，不是**选优** —— 现在没有依据判断"哪块更好"，
          所以不假装在优化。有了 `candidates` 的坐标后，将来要按位置启发式选格时，
          改这里一处即可。
        """
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        cs = raw.get("crystal_sphere") if isinstance(raw.get("crystal_sphere"), dict) else {}
        if not cs.get("active"):
            return None
        if cs.get("is_finished"):
            # 小游戏已结束：这一屏该走 proceed / choose_event_option，不该再揭格子。
            return None
        actions = snapshot.get("available_actions")
        if not isinstance(actions, list):
            actions = raw.get("available_actions") if isinstance(raw.get("available_actions"), list) else []
        has_divine = any(
            str(a.get("type") if isinstance(a, dict) else a).strip().lower() == "divine_crystal_sphere"
            for a in actions
        )
        if not has_divine:
            return None

        # ★ 候选格子清单（队长 ② 补上的 `candidates`）：优先用它，拿不到才退回计数。
        #   前者能说出"揭的是哪一块（x,y）"，后者只能说"第几块" —— 报告里要能读出决策依据。
        candidates = cs.get("candidates") if isinstance(cs.get("candidates"), list) else []
        hidden = cs.get("cells_hidden")
        if candidates:
            limit: int | None = len(candidates)
        elif isinstance(hidden, int) and hidden > 0:
            limit = hidden
        else:
            limit = None
        screen_key = f"divine|{snapshot.get('screen')}|{snapshot.get('floor')}"
        if screen_key != self._state.event_option_screen:
            self._state.event_option_screen = screen_key
            self._state.event_option_cursor = 0
        index = self._state.event_option_cursor
        if limit is not None:
            index %= limit
        self._state.event_option_cursor = index + 1
        coord: dict[str, Any] = {}
        if candidates and index < len(candidates):
            cell = candidates[index]
            if isinstance(cell, dict):
                coord = {"x": cell.get("x"), "y": cell.get("y")}
        try:
            self.logger.info(
                "[sts2_divine] 这一手揭第 %s 块%s（可选 %s 块，还剩 %s 次占卜）",
                index,
                f"（坐标 {coord.get('x')},{coord.get('y')}）" if coord else "",
                len(candidates) if candidates else cs.get("cells_hidden"),
                cs.get("divinations_left"),
            )
        except Exception:
            pass
        return {
            "action_type": "divine_crystal_sphere",
            "kwargs": {"option_index": index},
            "confidence": 0.8,
            "source": "heuristic",
            "reason": "crystal_sphere_divine",
        }

    def _log_potion_decision(self, snapshot: dict[str, Any], turn_key: str, decision: dict[str, Any]) -> None:
        """把这一 tick 的用药水判断用 info 级记一次（同一局面同一结论只记一次，避免刷屏）。

        规格「八」要求日志能读出"喝 / 不喝以及原因"：`[sts2_potion] 预计损血=12 > 8 → 用〈减伤药水〉(评分 88)`，
        或 `预计损血=12 但不喝（原因：已在本回合喝过）`。
        """
        text = str(decision.get("log") or "")
        if not text:
            return
        key = f"{turn_key}|{text}"
        if key == str(getattr(self._state, "potion_decision_log_key", "") or ""):
            return
        self._state.potion_decision_log_key = key
        try:
            self.logger.info("[sts2_potion] %s", text)
        except Exception:
            pass
