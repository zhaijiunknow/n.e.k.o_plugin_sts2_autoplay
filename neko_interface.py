from __future__ import annotations

from typing import Any


class STS2NekoInterface:
    def __init__(self, service: Any) -> None:
        self._service = service

    def t(self, key: str, *, default: str = "", **params: Any) -> str:
        return self._service.t(key, default=default, **params)

    async def get_status(self) -> dict[str, Any]:
        return await self._service.get_status()

    async def get_snapshot(self) -> dict[str, Any]:
        return await self.get_readout()

    async def get_summary(self) -> dict[str, Any]:
        readout = await self.get_readout()
        return {
            "status": readout.get("status", "ok"),
            "message": readout.get("summary") or readout.get("message") or self.t("neko.summary.empty", default="当前暂无可用局势摘要。"),
            "summary": readout.get("summary") or readout.get("message") or self.t("neko.summary.empty", default="当前暂无可用局势摘要。"),
            "situation_summary": dict(readout.get("situation_summary") if isinstance(readout.get("situation_summary"), dict) else {}),
        }

    async def get_catgirl_sync(self) -> dict[str, Any]:
        readout = await self.get_readout()
        catgirl_sync = readout.get("catgirl_sync") if isinstance(readout.get("catgirl_sync"), dict) else {}
        message = self.t("neko.sync.available", default="已生成猫娘同步包。") if catgirl_sync else self.t("neko.sync.empty", default="当前暂无猫娘同步包。")
        return {
            "status": readout.get("status", "ok"),
            "message": message,
            "summary": message,
            "catgirl_sync": catgirl_sync,
        }

    async def get_readout(self) -> dict[str, Any]:
        snapshot_result = await self._service.get_snapshot()
        snapshot = snapshot_result.get("snapshot") if isinstance(snapshot_result.get("snapshot"), dict) else {}
        situation_summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        catgirl_sync = snapshot.get("catgirl_sync") if isinstance(snapshot.get("catgirl_sync"), dict) else {}
        summary_text = str(situation_summary.get("text") or "")
        message = summary_text or self.t("neko.summary.empty", default="当前暂无可用局势摘要。")
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "snapshot": snapshot,
            "situation_summary": situation_summary,
            "catgirl_sync": catgirl_sync,
        }

    async def _refresh_if_connected(self) -> bool:
        """尽力刷新一次快照；连不上 mod 就跳过。

        档位与游玩模式都是**插件本地的偏好**——玩家完全可能先设好再开游戏。若因为
        游戏没开就连带把整个切换判为失败，NEKO 会得到一个报错，而设置其实早就生效了。
        所以这里吞掉连接错误，并在返回值里如实说明 refresed 与否。
        """
        try:
            await self._service.refresh_state(trigger_sync=True)
            return True
        except Exception as exc:
            self._service.logger.debug(f"[sts2] refresh after a local setting change skipped (non-fatal): {exc}")
            return False

    async def set_control_mode(self, mode: str) -> dict[str, Any]:
        """切换猫娘的控制档位：`program` 自己玩 / `advise` 听安排 / `standby` 停手。

        `advise` 下非战斗决策界面只算不做，等玩家发话（`sts2_execute_planned_operation`
        就是"我点头了，走一步"）；战斗照常自动打。档位会同步给 mod，让它自己的驱动一起停手。
        """
        result = await self._service.set_control_mode(mode)
        result["refreshed"] = await self._refresh_if_connected()
        return result

    async def set_instance(self, instance: str = "host") -> dict[str, Any]:
        """切换插件的"活动实例"：`host`（配置里的 base_url）或 `catgirl`（coop 里的猫娘进程）。

        一般是自动的——`sts2_open_coop_room(host=True)` 成功后会自己切到猫娘。这个入口用于
        查看/覆盖：例如猫娘进程重启后地址没跟上，或你想强制回到主机侧。
        """
        result = await self._service.set_instance(instance)
        result["refreshed"] = await self._refresh_if_connected()
        return result

    async def set_play_style(self, style: str) -> dict[str, Any]:
        """切换游玩模式：`serious` 认真（默认）/ `mischievous` 捣蛋 / `silly` 笨蛋。

        不影响"控制档位"（谁来决定），只决定"决定得多不认真"：捣蛋有 20%、笨蛋有 60% 的概率
        把最优操作换成次优/最差的**合法**选择。显式授权（`sts2_execute_planned_operation`）不受影响。
        """
        result = self._service.set_play_style(style)
        result["refreshed"] = await self._refresh_if_connected()
        return result

    async def set_standby(self, standby: bool) -> dict[str, Any]:
        mode_info = self._service.set_standby(standby)
        # standby 也要同步给 mod：只停插件等于没停，mod 有自己的自动玩驱动。
        sync = await self._service.sync_control_mode_to_mod()
        await self._service.refresh_state(trigger_sync=True)
        mode_label = "standby" if self._service._state.standby else "active"
        message = self.t("neko.standby.changed", default="已切换到 {mode_label} 模式。", mode_label=mode_label)
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "standby": self._service._state.standby,
            "mode": mode_info,
            "mod_sync": sync,
        }

    async def set_companion_mode(self, enabled: bool) -> dict[str, Any]:
        result = self._service.set_companion_mode(enabled)
        if enabled:
            await self._service.refresh_state(trigger_sync=True)
        return result

    async def set_danmaku(self, enabled: bool) -> dict[str, Any]:
        """游戏内弹幕总开关（关掉后插件不再推 /danmaku，自动游玩不受影响）。

        ★ t51：`sync_mod=False` —— 这里自己 `await` 同步并把结果（`mod_gate`）回报给玩家，
          所以让 service 别再排一次 fire-and-forget 的同步（否则同一次开关写两遍 mod）。
        """
        result = self._service.set_danmaku_enabled(enabled, sync_mod=False)
        gate = await self._service.sync_danmaku_gate_to_mod()
        result["mod_gate"] = gate
        if not enabled:
            result["message"] = str(result.get("message") or "") + " 已同时通知 mod 停止渲染并清屏。"
            result["summary"] = result["message"]
        return result

    async def set_route(self, *, mode: str = "safe", pick: Any = None, index: Any = None) -> dict[str, Any]:
        """对话式地图协作：定路线 / 跟你一样 / 选第几个。

        猫娘据玩家原话选 mode：
          - "定个路线"、"你定"、"挑条稳的"        -> safe（按保守权重自己算）
          - "我选完了，来选这个"、"跟我一样"      -> mirror（投玩家当前那一票）
          - "你选中间"、"选第三个"、"选最左边"    -> pick（pick=中间/第几个/最左/最右）
        重复调用即改票，玩家说"不对不对我选错了"时直接再调一次。
        """
        payload = await self._service.set_route(mode=mode, pick=pick, index=index)
        return payload

    async def continue_saved_run(self) -> dict[str, Any]:
        """回到（继续）当前存档：主菜单上等价于点「继续」。

        真实世界里"和 AI 组队中途退出后回不到存档"最常见的成因是主菜单决策抢在玩家前面开了单人
        子菜单/新建局（见 heuristic_planner 的 main_menu 分支已改为优先 continue_run）。这个入口
        给用户一个显式、可诊断的补救动作：不可用时直接报出当前界面与可用动作，而不是静默点错。
        """
        client = self._service._require_client()
        await self._service.refresh_state()
        snapshot = self._service._state.snapshot if isinstance(self._service._state.snapshot, dict) else {}
        screen = str(snapshot.get("screen") or "unknown")
        available = {
            str(item.get("type"))
            for item in (snapshot.get("available_actions") or [])
            if isinstance(item, dict) and item.get("type")
        }
        if "continue_run" not in available:
            message = self.t(
                "neko.continue_run.unavailable",
                default="当前界面没有可继续的存档（screen={screen}，可用动作: {actions}）。请确认主菜单上确实显示了「继续」。",
                screen=screen,
                actions=", ".join(sorted(available)) or "无",
            )
            return {"status": "idle", "message": message, "summary": message, "screen": screen}
        await client.execute_action("continue_run")
        await self._service.refresh_state()
        after = self._service._state.snapshot if isinstance(self._service._state.snapshot, dict) else {}
        message = self.t(
            "neko.continue_run.done",
            default="已回到存档，当前界面 {screen}。",
            screen=after.get("screen", "unknown"),
        )
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "screen": after.get("screen", "unknown"),
            "available_actions": sorted(
                str(item.get("type"))
                for item in (after.get("available_actions") or [])
                if isinstance(item, dict) and item.get("type")
            ),
        }

    async def extract_and_upsert_preference(self, instruction: str, *, source: str = "user") -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        context = self._build_preference_context(snapshot)
        extracted = self._service._preference_extractor.extract(instruction, context=context)
        if extracted is None:
            message = self.t("neko.preference.extract_none", default="当前指令未识别为可结构化保存的偏好。")
            return {"status": "idle", "message": message, "summary": message}
        record = self._service._preference_store.upsert(
            extracted["domain"],
            extracted["key"],
            extracted["value"],
            source=source,
        )
        message = self.t(
            "neko.preference.extracted",
            default="已提取并更新偏好：{pref_domain}/{pref_key}。",
            pref_domain=extracted["domain"],
            pref_key=extracted["key"],
        )
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "record": record,
            "extracted": extracted,
        }

    async def get_strategy_context(self) -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        strategy_name = strategy_context.get("strategy_name") or "unknown"
        message = self.t("neko.strategy_context", default="当前策略上下文：{strategy_name}。", strategy_name=strategy_name)
        return {
            "status": "ok",
            "message": message,
            "summary": message,
            "strategy_context": strategy_context,
        }

    async def get_planned_operation(self) -> dict[str, Any]:
        snapshot = await self._current_snapshot()
        planned_operation = snapshot.get("planned_operation") if isinstance(snapshot.get("planned_operation"), dict) else None
        if planned_operation is None:
            message = self.t("neko.planned_operation.empty", default="当前暂无规划动作。")
            return {"status": "idle", "message": message, "summary": message}
        current = self.t("neko.planned_operation.current", default="当前规划动作：{action_type}。", action_type=planned_operation.get("action_type", "unknown"))
        return {
            "status": "ok",
            "message": current,
            "summary": current,
            "planned_operation": planned_operation,
        }

    async def execute_planned_operation(self) -> dict[str, Any]:
        return await self._service.execute_planned_operation()

    async def start_autoplay(self) -> dict[str, Any]:
        return self._service.start_autoplay()

    async def pause_autoplay(self, reason: str = "user") -> dict[str, Any]:
        return self._service.pause_autoplay(reason=reason)

    async def resume_autoplay(self) -> dict[str, Any]:
        return self._service.resume_autoplay()

    async def stop_autoplay(self, reason: str = "manual") -> dict[str, Any]:
        return self._service.stop_autoplay(reason=reason)

    async def _current_snapshot(self) -> dict[str, Any]:
        status = await self._service.get_snapshot()
        snapshot = status.get("snapshot") if isinstance(status.get("snapshot"), dict) else {}
        return snapshot

    def _build_preference_context(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return {
            "screen": snapshot.get("screen"),
            "floor": snapshot.get("floor"),
            "payload": payload,
            "event_id": payload.get("event_id"),
        }


__all__ = ["STS2NekoInterface"]
