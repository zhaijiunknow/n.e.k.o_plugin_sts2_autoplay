from __future__ import annotations

from math import ceil
from typing import Any

from .heuristic_planner import (
    MAP_POLICY_AUTO,
    MAP_POLICY_FOLLOW,
    MAP_POLICY_WAIT,
    normalize_map_policy,
    validate_map_vote_wait_seconds,
)


class ServiceSettingsMixin:
    """Internal responsibility mixin; initialized and composed by STS2AutoplayService."""

    async def sync_livestream_to_mod(self) -> dict[str, Any]:
        try:
            result = await self._require_client().set_config(livestream_enabled=self._livestream.enabled)
            applied = result.get("livestream_enabled") is self._livestream.enabled
            return {"applied": applied, "enabled": self._livestream.enabled,
                    "reason": "" if applied else "mod 未确认直播控制开关，请更新配套模组"}
        except Exception as exc:
            self.logger.info("[sts2_live] mod sync failed: %s", exc)
            return {"applied": False, "enabled": self._livestream.enabled, "reason": str(exc)}

    def get_settings(self) -> dict[str, Any]:
        from .settings_config import FIELDS, read_settings, validate_patch

        values = {key: self._cfg.get(key, spec["default"]) for key, spec in FIELDS.items()}
        # Read effective switches, including changes made through the control page.
        values.update(policy_enabled=self._policy_advisor.enabled,
                      policy_timeout_seconds=self._policy_advisor._timeout,
                      host_commentary_min_interval_seconds=self._host_commentary_min_interval(),
                      danmaku_enabled=self._danmaku_enabled,
                      catgirl_llm_enabled=self._catgirl_llm_enabled,
                      host_digest_enabled=self._state.host_digest_enabled,
                      run_reflection_enabled=self._state.run_reflection_enabled,
                      encounter_memory_enabled=self._encounter_memory_enabled,
                      livestream_enabled=self._livestream.enabled,
                      livestream_profile=self._livestream.profile,
                      livestream_wait_scale=self._livestream.wait_scale)
        values.update(self._pending_restart_settings)
        # Preserve invalid legacy preferences even when the runtime clamps them.
        # Reading must not block fixing them, or turn an unrelated save into failure.
        for key in (FIELDS.keys() & self._cfg.keys()) - self._pending_restart_settings.keys():
            try:
                validate_patch({key: self._cfg[key]})
            except ValueError:
                values[key] = self._cfg[key]
        return {"status": "ok", **read_settings(values)}

    async def apply_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        from .settings_config import FIELDS, validate_patch

        patch = validate_patch(patch)
        # A next-start preference must not affect the current session's standby fallback.
        self._pending_restart_settings.update({key: value for key, value in patch.items() if FIELDS[key]["restart"]})
        self._cfg.update({key: value for key, value in patch.items() if not FIELDS[key]["restart"]})
        sync = {}
        if {"policy_enabled", "policy_timeout_seconds"} & patch.keys():
            self._policy_advisor.configure(self._cfg)
            self._state.force_replan_requested = True
        for key, attribute in (("catgirl_llm_enabled", "_catgirl_llm_enabled"),
                               ("encounter_memory_enabled", "_encounter_memory_enabled")):
            if key in patch:
                setattr(self, attribute, patch[key])
        if "host_digest_enabled" in patch:
            self.set_host_digest_enabled(patch["host_digest_enabled"])
        if "run_reflection_enabled" in patch:
            self.set_run_reflection_enabled(patch["run_reflection_enabled"])
        if "danmaku_enabled" in patch:
            self.set_danmaku_enabled(patch["danmaku_enabled"], sync_mod=False)
        if any(key.startswith("livestream_") for key in patch):
            self._livestream.configure(self._cfg)
            self._state.force_replan_requested = True
        # Apply all local values before waiting for a disconnected game.
        if "danmaku_enabled" in patch:
            sync["danmaku"] = await self.sync_danmaku_gate_to_mod()
        if any(key.startswith("livestream_") for key in patch):
            sync["livestream"] = await self.sync_livestream_to_mod()
        self._emit_status()
        restart = [key for key in patch if FIELDS[key]["restart"]]
        summary = self.t("settings.saved", default="已保存修改的参数；运行参数已应用。")
        if restart:
            summary += " " + self.t("settings.restart", default="启动偏好将在下次启动时读取。")
        if any(not item.get("applied") for item in sync.values()):
            summary += " " + self.t("settings.mod_pending", default="mod 尚未同步，重新连接时重试。")
        return {**self.get_settings(), "saved": True, "summary": summary,
                "restart_required": restart, "mod_sync": sync}

    async def set_livestream(self, patch: dict[str, Any]) -> dict[str, Any]:
        self._livestream.configure(patch)
        self._cfg.update(patch)
        self._state.force_replan_requested = True
        sync = await self.sync_livestream_to_mod()
        self._emit_status()
        return {"status": "ok", "summary": "已开启直播模式。" if self._livestream.enabled else "已关闭直播模式。",
                "livestream": self._livestream.status(), "mod_sync": sync}

    def set_host_digest_enabled(self, enabled: bool) -> dict[str, Any]:
        """开关「尖塔速报」：是否把局势悄悄交给本体，让它自己主动讲一句。

        关掉只影响这一路（不可见、不进记忆的主动投递）；**游戏内弹幕、自动游玩、历史记录都不受影响**。

        措辞不提"观众"：本插件是单人陪伴场景。等后续接直播插件时，听众称呼应由那边提供。
        """
        self._state.host_digest_enabled = bool(enabled)
        self._cfg["host_digest_enabled"] = bool(enabled)
        self._emit_status()
        message = "好，局势速报开着，我会自己找话说。" if enabled else "好，我不主动报局势了，等你说。"
        return {"status": "ok", "enabled": bool(enabled), "message": message, "summary": message}

    def set_run_reflection_enabled(self, enabled: bool) -> dict[str, Any]:
        """开关「尖塔复盘」：局末是否把"玩家的选择与推荐差得最多的几步"交给本体讲。

        关掉后**结构化记录照旧写**（损血归因、路线图、对照本身、历史页都能看），只是不再请模型讲解。
        """
        if not enabled and self._state.run_reflection_enabled:
            self._review_generation += 1
        self._state.run_reflection_enabled = bool(enabled)
        self._cfg["run_reflection_enabled"] = bool(enabled)
        self._emit_status()
        message = "好，打完我会讲讲你和我选得不一样的那几步。" if enabled else "好，我不讲了，只记数据。"
        return {"status": "ok", "enabled": bool(enabled), "message": message, "summary": message}

    # ---- 对话式地图协作（sts2_set_route）-----------------------------------------------

    def _configure_map_policy_from_config(self) -> None:
        # 地图投票：跟随玩家（co-op 队友先投就跟着投），以及无人投票时的等待秒数。
        # 地图投票策略：wait（默认，彻底等玩家发话）/ follow（自动跟票）/ auto（自己选）。
        # 兼容旧配置：只有 map_follow_player 布尔而没有 map_policy 时，true->follow、false->auto。
        if "map_policy" in self._cfg:
            self._map_policy = normalize_map_policy(self._cfg.get("map_policy"))
        elif "map_follow_player" in self._cfg:
            self._map_policy = MAP_POLICY_FOLLOW if self._cfg.get("map_follow_player") else MAP_POLICY_AUTO
        else:
            self._map_policy = MAP_POLICY_WAIT
        try:
            self._map_vote_wait_seconds = validate_map_vote_wait_seconds(self._cfg.get("map_vote_wait_seconds", 20))
        except (TypeError, ValueError):
            self._map_vote_wait_seconds = 20.0
        self._planner.configure_map_policy(
            policy=self._map_policy,
            vote_wait_seconds=self._map_vote_wait_seconds,
        )

    def set_map_policy(self, policy: str, *, vote_wait_seconds: float | None = None) -> dict[str, Any]:
        """切换地图投票策略。默认 wait：不投票也不跟票，等玩家发话。"""
        if vote_wait_seconds is not None:
            self._map_vote_wait_seconds = validate_map_vote_wait_seconds(vote_wait_seconds)
            self._cfg["map_vote_wait_seconds"] = self._map_vote_wait_seconds
        self._map_policy = normalize_map_policy(policy)
        self._cfg["map_policy"] = self._map_policy
        self._planner.configure_map_policy(
            policy=self._map_policy,
            vote_wait_seconds=self._map_vote_wait_seconds,
        )
        self._emit_status()
        message = {
            MAP_POLICY_WAIT: "地图我等你发话，你不说我不投。",
            MAP_POLICY_FOLLOW: f"地图我会跟你投同一个节点；联机时没人投票，我等 {self._map_vote_wait_seconds:g} 秒后自己选。",
            MAP_POLICY_AUTO: "地图我自己按稳妥的挑。",
        }[self._map_policy]
        return {"status": "ok", "message": message, "summary": message, "policy": self._map_policy,
                "vote_wait_seconds": self._map_vote_wait_seconds}

    async def sync_map_policy_to_mod(self) -> dict[str, Any]:
        """把等待策略写给 mod，让 mod 自己的自动玩循环也别抢路线。

        插件与 mod 各有一套决策循环，只改一边会出现"插件在等、mod 照投"。
        失败是良性的：插件侧 planner 在 wait 下本来就会返回 None 不投票。
        """
        try:
            client = self._require_client()
        except Exception:
            return {"applied": False, "policy": self._map_policy}
        try:
            await client.set_config(
                coop_map_policy=self._map_policy,
                # mod 接口是整数秒，向上取整，不能比插件更早抢票。
                coop_map_vote_wait_seconds=ceil(self._map_vote_wait_seconds),
            )
            return {"applied": True, "policy": self._map_policy}
        except Exception as exc:
            self.logger.debug(f"[sts2] sync map policy to mod failed (non-fatal): {exc}")
            return {"applied": False, "policy": self._map_policy}
