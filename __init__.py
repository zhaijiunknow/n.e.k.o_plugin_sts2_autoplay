from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, llm_tool, neko_plugin, plugin_entry, tr

from .dispatcher import STS2Dispatcher
from .qt_overlay_manager import QtOverlayManager
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
        self._overlay = QtOverlayManager(
            self.logger,
            plugin_id=self.plugin_id,
            plugin_dir=self.config_dir,
        )
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
        # 浮层参数：窗口贴合 / 自定义尺寸 / 速度 / 字号 / 高度百分比（start 时生效）
        self._overlay.configure(
            window=str(self._cfg.get("qt_overlay_window", "") or ""),
            rect=str(self._cfg.get("qt_overlay_rect", "") or ""),
            speed=float(self._cfg.get("danmu_speed", 0) or 0),
            font_size=int(self._cfg.get("danmu_font_size", 0) or 0),
            height_percent=int(self._cfg.get("danmu_height_percent", 30) or 30),
        )
        # 注册插件静态 UI（弹幕浮层页），服务地址：/plugin/sts2_autoplay/ui/
        registered = self.register_static_ui("static")
        if not registered:
            self.logger.warning("[sts2_static_ui] static UI 注册失败：插件 static/ 目录缺失")
        startup_result = _as_mapping(await self._service.startup(self._cfg))
        return Ok({"status": "ready", "result": await self._service.get_status(), "startup": startup_result})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_: Any):
        await self._service.shutdown()
        try:
            self._overlay.stop()
        except Exception:
            pass
        self._cleanup_overlay_processes()
        return Ok({"status": "shutdown"})

    def _cleanup_overlay_processes(self) -> None:
        """关闭插件时终止本插件启动的 Qt 弹幕浮层子进程（qt_overlay.py）。

        匹配：命令行含 ``--url`` 指向本插件 SSE 的 ``qt_overlay.py``（按路径结尾匹配，
        避免误杀含该字符串的调用者）。
        """
        try:
            import psutil
            import shlex
        except Exception:
            return
        plugin_marker = f"{self.plugin_id}/ui-api/events"
        killed = 0
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if plugin_marker not in cmd:
                continue
            try:
                tokens = shlex.split(cmd)
            except Exception:
                tokens = []
            if not any(token.endswith("qt_overlay.py") for token in tokens):
                continue
            try:
                self.logger.info("[sts2_autoplay] 关闭插件，终止弹幕浮层进程 pid=%s", proc.pid)
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
                killed += 1
            except Exception:
                pass
        if killed:
            self.logger.info("[sts2_autoplay] 已终止 %s 个弹幕浮层进程", killed)

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
    async def sts2_set_standby(self, standby: bool, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_standby(standby))

    @plugin_entry(
        id="sts2_start_autoplay",
        name=tr("entries.sts2_start_autoplay.name", default="让它自己玩起来"),
        description=tr("entries.sts2_start_autoplay.description", default="启动后台自动运行，让尖塔自己继续往下打。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
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
    async def sts2_stop_autoplay(self, **_: Any):
        return await self._run_entry(self._service.neko.stop_autoplay, finish=True)

    @plugin_entry(
        id="sts2_enable_companion_mode",
        name=tr("entries.sts2_enable_companion_mode.name", default="打开陪玩模式"),
        description=tr("entries.sts2_enable_companion_mode.description", default="让它开始陪你看局面，并适时给点评和提醒。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
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
    async def sts2_disable_companion_mode(self, **_: Any):
        return await self._run_entry(lambda: self._service.neko.set_companion_mode(False))

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
    async def sts2_execute_planned_operation(self, **_: Any):
        return await self._run_entry(self._service.neko.execute_planned_operation, finish=True)

    @plugin_entry(
        id="sts2_overlay_status",
        name=tr("entries.sts2_overlay_status.name", default="看看弹幕浮层开着没"),
        description=tr("entries.sts2_overlay_status.description", default="查询 Qt 透明弹幕浮层是否在运行。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_overlay_status(self, **_: Any):
        # overlay 方法是同步的（返回 dict），直接 Ok 包装，不走 _run_entry 的 await
        return Ok(self._overlay.status())

    @plugin_entry(
        id="sts2_overlay_start",
        name=tr("entries.sts2_overlay_start.name", default="打开弹幕浮层"),
        description=tr("entries.sts2_overlay_start.description", default="启动 Qt 透明弹幕浮层，叠在游戏窗口上滚动显示弹幕。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_overlay_start(self, **_: Any):
        return Ok(self._overlay.start())

    @plugin_entry(
        id="sts2_overlay_stop",
        name=tr("entries.sts2_overlay_stop.name", default="关掉弹幕浮层"),
        description=tr("entries.sts2_overlay_stop.description", default="停止 Qt 透明弹幕浮层进程。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_overlay_stop(self, **_: Any):
        return Ok(self._overlay.stop())

    @plugin_entry(
        id="sts2_install_pyqt6",
        name=tr("entries.sts2_install_pyqt6.name", default="安装 PyQt6"),
        description=tr("entries.sts2_install_pyqt6.description", default="一键安装弹幕浮层所需的 PyQt6 库（联网，可能耗时）。"),
        llm_result_fields=["summary"],
        input_schema={"type": "object", "properties": {}},
        metadata={"agent_auto": False},
    )
    async def sts2_install_pyqt6(self, **_: Any):
        script = self.config_dir / "install_pyqt6.py"
        if not script.is_file():
            return Ok({"ok": False, "error": "install_pyqt6.py 不存在"})
        try:
            # 安装可能耗时，放线程池避免阻塞插件事件循环
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, str(script)],
                capture_output=True,
                timeout=600,
            )
            tail = (result.stderr or result.stdout or b"").decode("utf-8", "replace")[-300:]
            if result.returncode == 0:
                return Ok({"ok": True, "installed": True, "summary": "PyQt6 已就绪"})
            return Ok({"ok": False, "error": tail})
        except Exception as exc:
            return Ok({"ok": False, "error": str(exc)})

