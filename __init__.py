from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

# Windows 上 asyncio 默认 Proactor 事件循环与 ZeroMQ 不兼容（zmq 无法 add_reader，回退到 tornado
# selector 线程，可能让上行 recv 读到截断的 pickle -> "unpickling stack underflow"）。
# 尽早把循环策略切成 Selector，让 NEKO 插件进程的 ZMQ 上行更稳。
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except AttributeError:
        pass

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, llm_tool, neko_plugin, plugin_entry, tr
from plugin.sdk.plugin import ui as _ui

from .dispatcher import STS2Dispatcher
from .heuristic_planner import MAP_POLICIES, validate_map_vote_wait_seconds
from .service import STS2AutoplayService

JsonObject = dict[str, Any]
AsyncPayloadFactory = Callable[[], Awaitable[JsonObject]]


def _as_mapping(value: Any) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _summary_from(payload: Mapping[str, Any]) -> str:
    return str(payload.get("summary") or payload.get("message") or payload.get("content") or "")


@neko_plugin
class STS2AutoplayPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: JsonObject = {}
        self._dispatcher = STS2Dispatcher(self)
        self._service = STS2AutoplayService(
            self.logger,
            self.report_status,
            self._push_frontend_notification,
            sdk_bus=self.bus,
            sdk_ctx=self.ctx,
            i18n=self.i18n,
        )

    @lifecycle(id="startup")
    async def startup(self, **_: Any):
        cfg = _as_mapping(await self.config.dump(timeout=5.0))
        self._cfg = _as_mapping(cfg.get("sts2"))
        # 注册插件静态 UI（sts2 面板），服务地址：/plugin/sts2_autoplay/ui/
        registered = self.register_static_ui("static")
        if not registered:
            self.logger.warning("[sts2_static_ui] static UI 注册失败：插件 static/ 目录缺失")
        startup_result = _as_mapping(await self._service.startup(self._cfg))
        return Ok({"status": "ready", "result": await self._service.get_status(), "startup": startup_result})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        await self._service.shutdown()
        return Ok({"status": "shutdown"})

    # ------------------------------------------------------------------
    # Hosted surface UI context
    # ------------------------------------------------------------------
    #
    # ★★ 为什么必须有这个 provider（踩了很久才定位，记下来）：
    #
    #   hosted surface 的按钮走 `call_surface_action` → `host.get_ui_context(context_id)`。
    #   而 `host._collect_ui_actions()`（`core/host.py:1253-1261`）只收集**同时**带
    #   `@ui.action` 的 entry：
    #
    #       ui_meta = _get_ui_action_meta(handler)
    #       if not ui_meta:
    #           continue          # ← 没有 @ui.action 的 entry 直接被跳过
    #
    #   于是 actions 白名单为空，面板上**每个按钮都会 403**
    #   （"is not exposed by surface"）。
    #
    #   provider 缺失还有第二重后果：`get_ui_context` 直接报
    #   "UI context not found"，连 context 里的 state 都拿不到。
    #
    #   这条路径的对错只在 `warnings` 里留一行，UI 上看不出区别，
    #   所以它长期没被发现。参照实现见 `plugins/game_agent_minecraft/__init__.py:628`。
    #
    # ★ 返回**轻量快照**即可：surface 自己会通过 api.call 拉实时状态，
    #   这里不做 lazy-start，避免每次取 context 都阻塞等连接握手。
    @_ui.context(id="main")
    async def main_ui_context(self, **_: Any) -> JsonObject:
        """main 面板的只读上下文 provider（与 `plugin.toml` 的 panel context 对应）。"""
        try:
            status = await self._service.get_status()
        except Exception:
            self.logger.exception("[sts2_ui_context] get_status 失败，降级返回空快照")
            status = {}
        return {"status": status}

    @llm_tool(
        name="sts2_get_status",
        description=tr("tools.sts2_get_status.description", default="只读获取杀戮尖塔连接状态、当前界面和基础分类信息。"),
        parameters={"type": "object", "properties": {}},
        timeout=10.0,
    )
    async def llm_get_status(self, **_: Any) -> JsonObject:
        return await self._service.get_status()

    async def _run_entry(self, action: AsyncPayloadFactory, *, finish: bool = False):
        try:
            payload = await action()
            if finish:
                payload = dict(payload) if isinstance(payload, Mapping) else {"value": payload}
                payload.setdefault("summary", _summary_from(payload))
                return await self.finish(data=payload, delivery="passive", message=_summary_from(payload))
            return Ok(payload)
        except SdkError as error:
            self.logger.warning(f"STS2 plugin entry failed: {error}")
            return Err(str(error))
        except Exception as error:
            self.logger.exception("Unexpected STS2 plugin entry failure")
            return Err(self.i18n.t("errors.internal", default="尖塔插件内部错误: {error}", error=error))

    def _get_dispatcher(self) -> STS2Dispatcher:
        """懒加载 dispatcher：测试桩（NotificationPlugin/LiveEntryPlugin）不调用
        super().__init__()，不会设置 self._dispatcher。"""
        dispatcher = getattr(self, "_dispatcher", None)
        if dispatcher is None:
            dispatcher = STS2Dispatcher(self)
            object.__setattr__(self, "_dispatcher", dispatcher)
        return dispatcher

    def _push_silent_mode_change(self, entry_id: str, payload: JsonObject) -> None:
        """模式切换的内部留痕：只记录，不让 NEKO 看见，更不触发它说话。

        `push_status_feedback` 默认 `ai_behavior="respond"`，那会让 AI **立刻开口**一轮
        （"好，我认真打"）——玩家要的是猫娘**偷偷**换档，不是每次汇报。`visibility=[]` 已经
        让它不进聊天窗，这里再把 `ai_behavior` 放到 `"blind"`：连喂给 LLM 都不喂。
        留痕仍然有用——它是插件面板与排障能看到的那份记录。
        """
        label = str(payload.get("mode") or entry_id)
        self._get_dispatcher().push_status_feedback(label, entry_id=entry_id, ai_behavior="blind")

    def _push_frontend_notification(
        self,
        *,
        content: str,
        description: str,
        metadata: JsonObject,
        priority: int = 5,
        message_type: str = "sts2_status",
        visibility: list[str] | None = None,
        ai_behavior: str | None = None,
    ) -> None:
        self._get_dispatcher().push_frontend_notification(
            content=content,
            description=description,
            metadata=metadata,
            priority=priority,
            message_type=message_type,
            visibility=visibility,
            ai_behavior=ai_behavior,
        )

    @plugin_entry(
        id="sts2_health_check",
        name=tr("entries.sts2_health_check.name", default="看看尖塔连上没"),
        description=tr("entries.sts2_health_check.description", default="看看本地尖塔 Agent 服务现在能不能正常连上。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_health_check", label=tr("entries.sts2_health_check.name", default="sts2_health_check"), tone="default", group="status", order=10)
    async def sts2_health_check(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.health_check()
            self._get_dispatcher().push_status_feedback(
                str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.health_check.done", default="尖塔服务检查完成。")),
                entry_id="sts2_health_check",
                ai_behavior="respond",
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_get_status",
        name=tr("entries.sts2_get_status.name", default="看看现在是什么情况"),
        description=tr("entries.sts2_get_status.description", default="看看尖塔连接状态、当前界面和基础局面信息。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_get_status", label=tr("entries.sts2_get_status.name", default="sts2_get_status"), tone="default", group="status", order=20)
    async def sts2_get_status(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.get_status()
            self._get_dispatcher().push_status_feedback(
                str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.get_status.done", default="已获取尖塔状态。")),
                entry_id="sts2_get_status",
                ai_behavior="respond",
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_read_state",
        name=tr("entries.sts2_read_state.name", default="看看当前局面"),
        description=tr("entries.sts2_read_state.description", default="顺手刷新一下，并把当前快照、局势摘要和猫娘同步包一起读出来。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_read_state", label=tr("entries.sts2_read_state.name", default="sts2_read_state"), tone="default", group="status", order=30)
    async def sts2_read_state(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.get_readout()
            self._get_dispatcher().push_status_feedback(
                str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.read_state.done", default="已读取尖塔局面。")),
                entry_id="sts2_read_state",
                ai_behavior="read",
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_set_standby",
        name=tr("entries.sts2_set_standby.name", default="设置尖塔待机"),
        description=tr("entries.sts2_set_standby.description", default="切换尖塔待机模式。待机模式下停止动作执行，但保留状态整理与猫娘同步准备。"),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "standby": {"type": "boolean"},
            },
            "required": ["standby"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_standby", label=tr("entries.sts2_set_standby.name", default="sts2_set_standby"), tone="default", group="mode", order=10)
    async def sts2_set_standby(self, standby: bool, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_standby(standby))

    # ---- 策略网络的下载前端（static/policy.html）----------------------------------------------
    # 这四个入口只给那张页面用：`agent_auto=False`，不进 LLM 的工具表，也不往聊天里推任何东西。

    @plugin_entry(
        id="sts2_policy_status",
        name="策略网络：下载状态",
        description="查看策略网络的运行环境与各角色模型是否已下载。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_policy_status(self, **_: Any):
        return await self._run_entry(self._service.policy_assets_status)

    @plugin_entry(
        id="sts2_policy_prepare_runtime",
        name="策略网络：准备运行环境",
        description="运行环境（Python 与 PyTorch）不在本机时开始下载。打开下载页面时调用。",
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_policy_prepare_runtime(self, **_: Any):
        return await self._run_entry(self._service.policy_prepare_runtime)

    @plugin_entry(
        id="sts2_policy_download_model",
        name="策略网络：下载角色模型",
        description="下载某个角色的模型文件。",
        input_schema={"type": "object", "properties": {"character": {"type": "string"}}, "required": ["character"]},
        metadata={"agent_auto": False},
    )
    async def sts2_policy_download_model(self, character: str = "", **_: Any):
        return await self._run_entry(lambda: self._service.policy_download_model(character))

    @plugin_entry(
        id="sts2_policy_remove_model",
        name="策略网络：删除角色模型",
        description="删除某个角色已下载的模型文件。",
        input_schema={"type": "object", "properties": {"character": {"type": "string"}}, "required": ["character"]},
        metadata={"agent_auto": False},
    )
    async def sts2_policy_remove_model(self, character: str = "", **_: Any):
        return await self._run_entry(lambda: self._service.policy_remove_model(character))

    @plugin_entry(
        id="sts2_start_autoplay",
        name=tr("entries.sts2_start_autoplay.name", default="让它自己玩起来"),
        description=tr("entries.sts2_start_autoplay.description", default="启动后台自动运行，让尖塔自己继续往下打。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_start_autoplay", label=tr("entries.sts2_start_autoplay.name", default="让它自己玩起来"), tone="primary", group="autoplay", order=10)
    async def sts2_start_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.start_autoplay)

    @plugin_entry(
        id="sts2_pause_autoplay",
        name=tr("entries.sts2_pause_autoplay.name", default="先停一下自动玩"),
        description=tr("entries.sts2_pause_autoplay.description", default="先暂停后台自动运行，等你决定下一步。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_pause_autoplay", label=tr("entries.sts2_pause_autoplay.name", default="先停一下自动玩"), group="autoplay", order=20)
    async def sts2_pause_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.pause_autoplay)

    @plugin_entry(
        id="sts2_resume_autoplay",
        name=tr("entries.sts2_resume_autoplay.name", default="继续让它自己玩"),
        description=tr("entries.sts2_resume_autoplay.description", default="从暂停处接着自动运行。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_resume_autoplay", label=tr("entries.sts2_resume_autoplay.name", default="继续让它自己玩"), group="autoplay", order=30)
    async def sts2_resume_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.resume_autoplay)

    @plugin_entry(
        id="sts2_stop_autoplay",
        name=tr("entries.sts2_stop_autoplay.name", default="别让它自己玩了"),
        description=tr("entries.sts2_stop_autoplay.description", default="停止后台自动运行，把控制权收回来。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_stop_autoplay", label=tr("entries.sts2_stop_autoplay.name", default="别让它自己玩了"), tone="danger", group="autoplay", order=40)
    async def sts2_stop_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.stop_autoplay, finish=True)

    @plugin_entry(
        id="sts2_open_coop_room",
        name=tr("entries.sts2_open_coop_room.name", default="打开 co-op 房间"),
        description=tr("entries.sts2_open_coop_room.description", default="打开联机房间（host 建房 / 非 host 加入本机 127.0.0.1:33771），猫娘选角 + ready 由自动游玩处理。"),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"host": {"type": "boolean"}},
            "required": [],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_open_coop_room", label=tr("entries.sts2_open_coop_room.name", default="sts2_open_coop_room"), tone="default", group="coop", order=10)
    async def sts2_open_coop_room(self, host: bool = True, **_: Any):
        return await self._run_entry(lambda: self._service.open_coop_room(host=host))

    @plugin_entry(
        id="sts2_enable_companion_mode",
        name=tr("entries.sts2_enable_companion_mode.name", default="打开陪玩模式"),
        description=tr("entries.sts2_enable_companion_mode.description", default="让它开始陪你看局面，并适时给点评和提醒。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_enable_companion_mode", label=tr("entries.sts2_enable_companion_mode.name", default="sts2_enable_companion_mode"), tone="default", group="companion", order=10)
    async def sts2_enable_companion_mode(self, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_companion_mode(True))

    @plugin_entry(
        id="sts2_disable_companion_mode",
        name=tr("entries.sts2_disable_companion_mode.name", default="关掉陪玩模式"),
        description=tr("entries.sts2_disable_companion_mode.description", default="先别继续陪玩点评，只保留基础运行。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_disable_companion_mode", label=tr("entries.sts2_disable_companion_mode.name", default="sts2_disable_companion_mode"), tone="default", group="companion", order=20)
    async def sts2_disable_companion_mode(self, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_companion_mode(False))

    @plugin_entry(
        id="sts2_set_map_policy",
        name="联机地图等待设置",
        description="保存地图策略和联机跟票等待秒数；0 表示不等待，默认 20 秒。wait 无限等指令，auto 立即选路。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "policy": {"type": "string", "enum": ["wait", "follow", "auto"], "default": "follow"},
                "vote_wait_seconds": {"type": "number", "minimum": 0, "maximum": 86400, "default": 20},
            },
            "required": ["policy", "vote_wait_seconds"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_map_policy", label="联机地图等待设置", tone="default", group="route", order=20)
    async def sts2_set_map_policy(self, policy: str = "follow", vote_wait_seconds: float = 20, **_: Any):
        async def apply() -> JsonObject:
            if policy not in MAP_POLICIES:
                raise ValueError("地图策略必须是 wait、follow 或 auto")
            seconds = validate_map_vote_wait_seconds(vote_wait_seconds)
            patch = {"map_policy": policy, "map_vote_wait_seconds": seconds}
            # 先保存，失败时不悄悄改变当前行为；宿主按字段合并，不覆盖其余 sts2 配置。
            await self.config.update({"sts2": patch})
            self._cfg.update(patch)
            result = self._service.set_map_policy(policy, vote_wait_seconds=seconds)
            result["saved"] = True
            result["mod_sync"] = await self._service.sync_map_policy_to_mod()
            if not result["mod_sync"].get("applied"):
                result["summary"] += " 设置已保存；mod 尚未同步，重新连接时重试。"
            return result

        return await self._run_entry(apply)

    @plugin_entry(
        id="sts2_set_danmaku",
        name=tr("entries.sts2_set_danmaku.name", default="开关游戏内弹幕"),
        description=tr(
            "entries.sts2_set_danmaku.description",
            default="打开/关闭游戏内猫娘弹幕。关闭后插件不再向游戏推弹幕（也不生成点评、不清屏），自动游玩与状态读取不受影响。",
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
            },
            "required": ["enabled"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_danmaku", label=tr("entries.sts2_set_danmaku.name", default="sts2_set_danmaku"), tone="default", group="companion", order=30)
    async def sts2_set_danmaku(self, enabled: bool = True, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_danmaku(enabled))

    @plugin_entry(
        id="sts2_set_host_digest",
        name=tr("entries.sts2_set_host_digest.name", default="开关尖塔速报"),
        description=tr(
            "entries.sts2_set_host_digest.description",
            default=(
                "打开/关闭「尖塔速报」：开着时插件会把局势悄悄交给你（不可见、不进记忆），"
                "由你自己主动讲一句。关掉只影响这一路，游戏内弹幕与自动游玩不受影响。"
            ),
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_host_digest", label=tr("entries.sts2_set_host_digest.name", default="sts2_set_host_digest"), tone="default", group="companion", order=40)
    async def sts2_set_host_digest(self, enabled: bool = True, **_: Any):
        async def action() -> JsonObject:
            payload = self._service.set_host_digest_enabled(bool(enabled))
            self._push_silent_mode_change("sts2_set_host_digest", payload)
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_set_run_reflection",
        name=tr("entries.sts2_set_run_reflection.name", default="开关尖塔复盘"),
        description=tr(
            "entries.sts2_set_run_reflection.description",
            default=(
                "打开/关闭「尖塔复盘」：开着时，玩家亲手打过的局结束后，会把他的选择和推荐差得最多的几步"
                "交给你讲讲为什么；关掉后只记数据，损血归因/路线图/对照记录/历史页照旧可看。"
            ),
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_run_reflection", label=tr("entries.sts2_set_run_reflection.name", default="sts2_set_run_reflection"), tone="default", group="companion", order=50)
    async def sts2_set_run_reflection(self, enabled: bool = True, **_: Any):
        async def action() -> JsonObject:
            payload = self._service.set_run_reflection_enabled(bool(enabled))
            self._push_silent_mode_change("sts2_set_run_reflection", payload)
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_continue_run",
        name=tr("entries.sts2_continue_run.name", default="回到存档继续玩"),
        description=tr(
            "entries.sts2_continue_run.description",
            default="在主菜单点「继续」，回到当前存档（co-op 中途退出后回不到存档时用它）。不可用时会报出当前界面与可用动作。",
        ),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_continue_run", label=tr("entries.sts2_continue_run.name", default="sts2_continue_run"), tone="primary", group="run", order=10)
    async def sts2_continue_run(self, **_: Any):
        return await self._run_entry(self._service.neko.continue_saved_run, finish=True)

    @llm_tool(
        name="sts2_set_route",
        description=tr(
            "tools.sts2_set_route.description",
            default=(
                "在杀戮尖塔的地图选点界面按玩家的意思投票。默认策略下猫娘不会自己抢路线，只有调用本工具才会投。"
                "按玩家原话选 mode："
                "玩家让你自己定路线（“定个路线”“你来定”“挑条稳的”“你觉得走哪”）-> mode=safe；"
                "玩家已经选好、让你跟他一致（“我选完了，来选这个”“跟我一样”“跟上我”）-> mode=mirror；"
                "玩家指定了具体位置（“你选中间”“选第三个”“选最左边”“选最右边”）-> mode=pick 并把原话放进 pick。"
                "玩家说“不对不对我选错了，你选中间”时直接用 mode=pick 再调一次即可改票。"
                "不在选点界面时会返回错误说明，此时不要谎称已投票。"
            ),
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["safe", "mirror", "pick"],
                    "description": "safe=自己挑稳妥的；mirror=跟玩家当前那一票；pick=按玩家指定位置。",
                },
                "pick": {
                    "type": "string",
                    "description": "mode=pick 时玩家说的位置原话，例如“中间”“第三个”“最左边”“最右”。",
                },
            },
            "required": ["mode"],
        },
        timeout=20.0,
    )
    async def llm_set_route(self, mode: str = "safe", pick: Any = None, **_: Any) -> JsonObject:
        return await self._service.neko.set_route(mode=mode, pick=pick)

    @plugin_entry(
        id="sts2_set_route",
        name=tr("entries.sts2_set_route.name", default="定个路线/按我说的选"),
        description=tr(
            "entries.sts2_set_route.description",
            default=(
                "在选路线的界面替猫娘投票。mode=safe 让它自己挑一条稳妥的；mode=mirror 跟玩家当前那一票；"
                "mode=pick 按玩家说的位置（中间/第三个/最左/最右）投。重复调用即改票。"
            ),
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["safe", "mirror", "pick"]},
                "pick": {"type": "string"},
                "index": {"type": "integer"},
            },
            "required": ["mode"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_route", label=tr("entries.sts2_set_route.name", default="sts2_set_route"), tone="default", group="route", order=10)
    async def sts2_set_route(self, mode: str = "safe", pick: Any = None, index: Any = None, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.set_route(mode=mode, pick=pick, index=index)
            self._get_dispatcher().push_status_feedback(
                str(payload.get("summary") or payload.get("message") or "已处理地图路线指令。"),
                entry_id="sts2_set_route",
                ai_behavior="respond",
            )
            return payload

        return await self._run_entry(action)

    @llm_tool(
        name="sts2_set_control_mode",
        description=tr(
            "tools.sts2_set_control_mode.description",
            default=(
                "切换猫娘在游戏里的控制档位。按玩家原话选 mode："
                "玩家让你自己玩、不用问了（“你自己玩”“你自己看着办”“随便打”）-> mode=program；"
                "玩家要自己拿主意、让你听着（“听我安排”“你先别动”“等我说”“这步我来定”）-> mode=advise；"
                "玩家要你完全停手（“停手”“别动”“待机”）-> mode=standby。"
                "advise 下你仍然会算，玩家问“你打算怎么走”时要答得出来；"
                "他说“就这个”时用 sts2_execute_planned_operation 走一步。"
                "advise 只作用于战斗之外的决策界面（选路/奖励/选卡/事件/商店/休息等），战斗你会照常打。"
            ),
        ),
        parameters={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["program", "advise", "standby"],
                    "description": "program=自己玩；advise=听玩家安排，一步步等发话；standby=完全停手。",
                },
            },
            "required": ["mode"],
        },
        timeout=20.0,
    )
    async def llm_set_control_mode(self, mode: str = "program", **_: Any) -> JsonObject:
        result = await self._service.neko.set_control_mode(mode)
        # 工具返回值会原样回给 LLM，所以这里只给机器可读的最小结果。
        # 若带上"好，我自己玩"这类句子，猫娘多半会把它说出口——那就不是"偷偷换档"了。
        return {"ok": result.get("status") == "ok", "mode": result.get("mode") or mode}

    @plugin_entry(
        id="sts2_set_control_mode",
        name=tr("entries.sts2_set_control_mode.name", default="你自己玩/听我安排"),
        description=tr(
            "entries.sts2_set_control_mode.description",
            default=(
                "切换控制档位：program 自己玩（默认）；advise 听玩家安排——战斗之外的决策界面只算不做，"
                "等玩家发话才走一步；standby 完全停手。档位会同步给 mod。"
            ),
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["program", "advise", "standby"]}},
            "required": ["mode"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_control_mode", label=tr("entries.sts2_set_control_mode.name", default="sts2_set_control_mode"), tone="default", group="mode", order=20)
    async def sts2_set_control_mode(self, mode: str = "program", **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.set_control_mode(mode)
            self._push_silent_mode_change("sts2_set_control_mode", payload)
            return payload

        return await self._run_entry(action)

    @llm_tool(
        name="sts2_set_host_digest",
        description=tr(
            "tools.sts2_set_host_digest.description",
            default=(
                "开关「尖塔速报」：是否让插件把对局局势悄悄交给你、由你主动讲一句。"
                "玩家说「别自己报局势了」「安静点看就行」「不用主动说话」-> enabled=false；"
                "玩家说「你多讲讲局势」「直播感一点」「主动点」-> enabled=true。"
                "只影响这一路：游戏内弹幕、自动游玩、历史记录都不受影响。"
            ),
        ),
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true=开着速报；false=不主动报局势。"},
            },
            "required": ["enabled"],
        },
        timeout=20.0,
    )
    async def llm_set_host_digest(self, enabled: bool = True, **_: Any) -> JsonObject:
        result = self._service.set_host_digest_enabled(bool(enabled))
        return {"ok": result.get("status") == "ok", "enabled": bool(result.get("enabled"))}

    @llm_tool(
        name="sts2_set_run_reflection",
        description=tr(
            "tools.sts2_set_run_reflection.description",
            default=(
                "开关「尖塔复盘」：玩家亲手打过的局结束时，是否把他的选择和你的推荐差得最多的几步拿出来讲讲为什么。"
                "玩家说「别复盘了」「打完直接开下一局」「快点重开」-> enabled=false；"
                "玩家说「打完讲讲问题」「复盘一下」-> enabled=true。"
                "关掉后只记数据不讲解，损血归因/路线图/历史页照旧可看。纯自动玩的局本来就没有这一步。"
            ),
        ),
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "true=局末复盘；false=只记数据。"},
            },
            "required": ["enabled"],
        },
        timeout=20.0,
    )
    async def llm_set_run_reflection(self, enabled: bool = True, **_: Any) -> JsonObject:
        result = self._service.set_run_reflection_enabled(bool(enabled))
        return {"ok": result.get("status") == "ok", "enabled": bool(result.get("enabled"))}


    @plugin_entry(
        id="sts2_set_instance",
        name=tr("entries.sts2_set_instance.name", default="对着主机还是猫娘"),
        description=tr(
            "entries.sts2_set_instance.description",
            default=(
                "切换插件当前驱动的游戏进程：host（配置里的 base_url；单人局就是它，coop 里是主机）"
                "或 catgirl（coop 里被拉起的猫娘进程，地址由主机的 coop_client_port 推导）。"
                "一般是自动的——打开 co-op 房间成功后插件会自己切到猫娘；这个入口用于查看与强制覆盖。"
            ),
        ),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {"instance": {"type": "string", "enum": ["host", "catgirl"]}},
            "required": ["instance"],
        },
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_set_instance", label=tr("entries.sts2_set_instance.name", default="sts2_set_instance"), tone="default", group="mode", order=40)
    async def sts2_set_instance(self, instance: str = "host", **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_instance(instance))

    @plugin_entry(
        id="sts2_apply_user_override",
        name=tr("entries.sts2_apply_user_override.name", default="按我这句来调整策略"),
        description=tr("entries.sts2_apply_user_override.description", default="按当前场景理解你的这句话，并更新对应的事件或敌人级偏好。"),
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "instruction": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["instruction"],
        },
        metadata={"agent_auto": False},
    )
    async def sts2_apply_user_override(self, instruction: str, source: str = "user", **_: Any):
        return await self._run_entry(lambda: self._service.apply_user_override_safely(instruction, source=source))

    @plugin_entry(
        id="sts2_get_planned_operation",
        name=tr("entries.sts2_get_planned_operation.name", default="看看它准备怎么走"),
        description=tr("entries.sts2_get_planned_operation.description", default="看看当前局面下，它下一步打算怎么操作。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_get_planned_operation", label=tr("entries.sts2_get_planned_operation.name", default="sts2_get_planned_operation"), tone="default", group="plan", order=10)
    async def sts2_get_planned_operation(self, **_: Any):
        async def action() -> JsonObject:
            payload = await self._service.neko.get_planned_operation()
            self._get_dispatcher().push_status_feedback(
                str(payload.get("summary") or payload.get("message") or self.i18n.t("messages.get_planned_operation.done", default="已获取尖塔规划动作。")),
                entry_id="sts2_get_planned_operation",
                ai_behavior="respond",
            )
            return payload

        return await self._run_entry(action)

    @plugin_entry(
        id="sts2_execute_planned_operation",
        name=tr("entries.sts2_execute_planned_operation.name", default="按建议走一步"),
        description=tr("entries.sts2_execute_planned_operation.description", default="直接执行它当前建议的下一步动作。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    @_ui.action(id="sts2_execute_planned_operation", label=tr("entries.sts2_execute_planned_operation.name", default="sts2_execute_planned_operation"), tone="default", group="plan", order=20)
    async def sts2_execute_planned_operation(self, **_: Any):
        return await self._run_entry(self._service.neko.execute_planned_operation, finish=True)

