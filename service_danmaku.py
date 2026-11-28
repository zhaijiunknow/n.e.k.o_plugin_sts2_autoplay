from __future__ import annotations

import asyncio
import json
import os
from hashlib import sha1
from time import monotonic, time
from typing import Any

from .companion_evaluator import _enemy_intent_label
from .loop_runner import _combat_plan_signature, _current_turn_steps, _reusable_plan

# 游戏内弹幕独立于宿主聊天：只发新鲜短句，不补播积压消息。
DANMAKU_MIN_INTERVAL = 6.0
DANMAKU_MAX_CHARS = 32
DANMAKU_GENERATION_TIMEOUT = 8.0
DANMAKU_IDLE_INTERVAL = 25.0



class ServiceDanmakuMixin:
    """Internal responsibility mixin; initialized and composed by STS2AutoplayService."""

    def _raw_combat_enemies(self, snapshot: dict[str, Any]) -> list[Any]:
        """`raw_state.combat.enemies`（mod 的权威来源），用来取结构化的 `intents[]`。

        payload 里的 `enemies[].intent` 只有招式 id（念出来是 `STICKY_SHOT`），结构化意图
        （`intent_type` + 伤害）只在 raw_state 里。两边按下标对应：payload 就是按 raw 的顺序
        截前 3 个生成的（`catgirl_bridge`），所以尾部多出来的敌人不影响对齐。
        """
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies")
        return list(enemies) if isinstance(enemies, list) else []

    def _instant_line_key(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> str:
        screen = str(payload.get("screen") or snapshot.get("screen") or "")
        raw = snapshot.get("raw_state") or {}
        run = raw.get("run") or {}
        run_id = snapshot.get("run_id") or raw.get("run_id") or run.get("run_id") or run.get("id") or ""
        scope = f"{run_id}|{snapshot.get('act')}|" if run_id else ""
        return f"{scope}{screen}|{snapshot.get('floor')}|{snapshot.get('turn')}"

    def _schedule_catgirl_push(self, text: str, *, fetch_avatar: bool = True, change_key: str = "") -> None:
        """把一条文本异步推到游戏内弹幕（fire-and-forget，异常自取）。

        ``fetch_avatar=False`` 用于**即时短句**：拉头像是一次 HTTP（最长 3 秒超时），
        等它回来"即时"就没了 —— 所以即时线只用已缓存的头像，没有就纯文本先发。
        """
        if not text or not self._danmaku_enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            task = loop.create_task(self._push_change_catgirl(
                text, self._commentary_observation_key(self._state.snapshot), change_key, fetch_avatar=fetch_avatar
            ))
            def finished(t):
                try:
                    sent = False if t.cancelled() else t.result()
                except Exception:
                    sent = False
                # 调度不等于播报成功；限流/过时/网络失败不能永久吃掉本次即时句。
                if (not sent and change_key and self._last_instant_change_key == change_key
                        and change_key not in self._reported_danmaku_changes):
                    self._state.last_instant_line_key = ""
                    self._last_instant_text = ""
            task.add_done_callback(finished)
        except Exception:
            pass

    def _emit_instant_catgirl_line(self, snapshot: dict[str, Any], companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> None:
        """**即时短句**：不调 LLM，用手里已有的信息拼一句立刻推。

        这是"弹幕跟不上画面"的解药：点评生成平均要 5~10 秒，等它出来早就换场景了；
        即时短句只取当前观测，统一出口至少间隔 6 秒；有新信息才说，不排队补播。
        """
        try:
            key = self._instant_line_key(snapshot, payload)
        except Exception:
            return
        if not self._danmaku_enabled or not key or not self._danmaku_ready():
            return
        change_key = self._danmaku_change_key(snapshot, payload)
        if change_key in self._reported_danmaku_changes:
            return
        text = self._compact_danmaku(self._instant_line_text(snapshot, companion_evaluation, payload))
        if not text or (key == self._state.last_instant_line_key and text == self._last_instant_text):
            return
        self._state.last_instant_line_key = key
        self._last_instant_text = text
        self._last_instant_change_key = change_key
        # 即时线不等待拉头像（那是一次最长 3 秒的 HTTP），只用缓存里的。
        self._schedule_catgirl_push(text, fetch_avatar=False, change_key=change_key)
        self.logger.info("[sts2_instant_line] key=%s text=%s", key, text[:60])

    def _instant_line_text(self, snapshot: dict[str, Any], companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> str:
        """短句内容：战斗报局势 + 出牌建议（**只读缓存计划**，绝不在这里等 HTTP）；其余界面用启发式文本。"""
        kind = str(payload.get("summary_kind") or "")
        if kind != "combat":
            return str(companion_evaluation.get("primary_message") or payload.get("message") or "").strip()
        bits: list[str] = []
        try:
            plan_line = self._current_commentary_plan_line(snapshot)
        except Exception:
            plan_line = ""
        if plan_line:
            # 弹幕只说下一步，不把整轮出牌列表铺满屏幕；详解仍用完整剩余计划。
            return "可以先" + plan_line.split("；", 1)[0] + "喵。"
        enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        raw_enemies = self._raw_combat_enemies(snapshot)
        enemy_bits: list[tuple[float, str]] = []
        for index, enemy in enumerate(enemies[:3]):
            if not isinstance(enemy, dict):
                continue
            name = str(enemy.get("name") or "敌人")
            # 说人话的意图（`攻击 9`），而不是 mod 的招式 id（`STICKY_SHOT`）——见 `_enemy_intent_label`。
            raw_enemy = raw_enemies[index] if index < len(raw_enemies) else None
            intent = _enemy_intent_label(enemy, raw_enemy)
            intents = raw_enemy.get("intents") if isinstance(raw_enemy, dict) else []
            damage = sum(
                float(item["total_damage"]) for item in (intents if isinstance(intents, list) else [])
                if isinstance(item, dict) and isinstance(item.get("total_damage"), (int, float))
            )
            enemy_bits.append((damage, f"{name}/{intent}" if intent else name))
        if enemy_bits:
            # 只挑伤害意图最高的一只，避免把三只敌人和整轮计划塞成横跨屏幕的清单。
            bits.append("敌:" + max(enemy_bits, key=lambda item: item[0])[1])
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        if isinstance(player.get("current_hp"), (int, float)):
            hp = str(int(player["current_hp"]))
            if isinstance(player.get("max_hp"), (int, float)) and player["max_hp"] > 0:
                hp += f"/{int(player['max_hp'])}"
            bits.append("我" + hp)
        if not bits:
            return ""
        return "｜".join(bits) + " 喵。"

    def _should_emit_long_commentary(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> bool:
        """要不要再让**本体**生成一条长讲解（即时短句之外的第二条线）。

        用户口径："先做高风险，其余短句"。落到实现上是：

        - **战斗**：只在 `_is_high_risk_combat` 为真时讲（血低 / 这一下要挨掉半条命）。
          战斗回合密集，每回合都让推理模型讲一遍既慢又刷屏。
        - **非战斗**（地图/商店/奖励/事件/选牌/营地）：照旧讲。这些界面本来就被
          `should_comment` + 指纹去重挡着，频率很低；而且"讲解局势"主要就发生在这里
          （选哪条路、买什么、拿哪张牌），砍掉的话猫娘就"完全不会讲解了"。
        """
        if str(payload.get("summary_kind") or "") == "combat":
            return self._is_high_risk_combat(snapshot, payload)
        return True

    def _is_high_risk_combat(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> bool:
        """要不要额外出长讲解。用户口径："先做高风险，其余短句"。

        判据：血比 < HIGH_RISK_HP_RATIO，或敌人这一下的意图总伤害 >= 当前生命的一半
        （意图伤害从 raw_state.combat.enemies[].intents[].total_damage 取，这是 mod 的权威来源）。
        """
        if str(payload.get("summary_kind") or "") != "combat":
            return False
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        hp_value = player.get("current_hp")
        max_value = player.get("max_hp")
        if not isinstance(hp_value, (int, float)):
            # 连血量都不知道 -> 判不出风险，那就**讲**。玩家要的是"猫娘会讲解局势"，
            # 这种"拿不准"的场合静默反而更糟（实测 payload 里 player 一直都在，所以线上很少走这里）。
            return True
        ratio = 1.0
        try:
            if float(max_value or 0) > 0:
                ratio = float(hp_value) / float(max_value)
        except (TypeError, ValueError):
            ratio = 1.0
        if ratio < self.HIGH_RISK_HP_RATIO:
            return True
        try:
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            incoming = 0.0
            for enemy in combat.get("enemies") if isinstance(combat.get("enemies"), list) else []:
                if not isinstance(enemy, dict):
                    continue
                for intent in enemy.get("intents") if isinstance(enemy.get("intents"), list) else []:
                    if isinstance(intent, dict):
                        incoming += float(intent.get("total_damage") or 0)
            current_hp = float(hp_value or 0)
            return current_hp > 0 and incoming >= current_hp * 0.5
        except (TypeError, ValueError):
            return False

    def _maybe_emit_catgirl_llm(self, snapshot: dict[str, Any], companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> None:
        """catgirl 轨道：猫娘 LLM 生成点评（优先）或启发式 primary_message 兜底。

        - 启用 + LLM 可用 + 非生成中 + 距上次生成 ≥ 间隔 → 调度 LLM 生成（生成后推弹幕）
        - 否则推启发式文本（保持即时性，避免无弹幕）
        """
        kind = str(payload.get("summary_kind") or "")
        # 弹幕总闸门：关掉时连 LLM 生成与头像 HTTP 都不做（省一次 N.E.K.O. 主 server 请求）。
        # _catgirl_llm_enabled=false 只是把 LLM 点评降级为启发式文本，不是关弹幕——见 danmaku_enabled。
        # ★★★ t51：但"弹幕关着 + 开了宿主点评"是**要说话**的组合 ⇒ 必须继续生成文本，
        #   否则聊天里那句也无从谈起（`_push_catgirl` 会把它改走 push_message）。
        if not self._danmaku_enabled and not self._is_host_commentary_enabled():
            self.logger.info("[sts2_catgirl_llm] suppressed danmaku disabled kind=%s screen=%s", kind, payload.get("screen"))
            return
        if not self._danmaku_enabled:
            self.logger.info(
                "[sts2_catgirl_llm] danmaku off ⇒ 改走宿主点评 kind=%s screen=%s", kind, payload.get("screen")
            )
        if self._danmaku_enabled and self._danmaku_change_key(snapshot, payload) in self._reported_danmaku_changes:
            return
        # 生成只允许一个在途请求；所有来源最终共用 _push_catgirl 的发送间隔和短句上限。
        enabled = self._catgirl_llm_enabled
        llm_ok = self._catgirl_llm is not None and self._catgirl_llm.available
        if enabled and llm_ok:
            if self._catgirl_llm_task is not None and not self._catgirl_llm_task.done():
                return
            self.logger.info("[sts2_catgirl_llm] schedule kind=%s screen=%s", kind, payload.get("screen"))
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self._push_catgirl_fallback(companion_evaluation, payload)
                return
            self._catgirl_llm_task = loop.create_task(self._catgirl_llm_async(snapshot, payload, companion_evaluation, self._instant_line_key(snapshot, payload)))
            return
        self.logger.info("[sts2_catgirl_llm] fallback enabled=%s llm_ok=%s kind=%s", enabled, llm_ok, kind)
        self._push_catgirl_fallback(companion_evaluation, payload)

    def _idle_danmaku_allowed(self, snapshot: dict[str, Any]) -> bool:
        # 闲聊只进游戏弹幕，不因弹幕关闭转成宿主聊天；直播讲解拥有场景时也不抢话。
        return self._danmaku_enabled and self._client is not None and self._state.transport_state == "connected" and not (
            self._livestream.enabled and self._state.autoplay_state == "running"
            and not self._should_hold_for_orders(snapshot) and self._livestream.scene_for(snapshot)
        )

    def _maybe_emit_idle_catgirl(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> None:
        """静止观测且一段时间没有弹幕时，补一句陪伴闲聊，而不是重播旧战报。"""
        kind = str(payload.get("summary_kind") or "")
        if not self._idle_danmaku_allowed(snapshot) or kind not in {
            "combat", "map", "event", "reward", "selection", "shop", "rest",
        }:
            self._danmaku_idle_observation = ""
            return
        now = monotonic()
        key = self._commentary_observation_key(snapshot)
        if key != self._danmaku_idle_observation:
            self._danmaku_idle_observation = key
            self._danmaku_idle_since = now
            return
        last_activity = max(
            self._danmaku_idle_since,
            self._last_danmaku_at if self._last_danmaku_at is not None else self._danmaku_idle_since,
            self._danmaku_idle_attempt_at if self._danmaku_idle_attempt_at is not None else self._danmaku_idle_since,
        )
        if now - last_activity < DANMAKU_IDLE_INTERVAL or not self._danmaku_ready():
            return
        if self._catgirl_llm_task is not None and not self._catgirl_llm_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # 与事件点评共用一个生成槽；失败也等下一周期，不在每个 tick 重试。
        self._danmaku_idle_attempt_at = now
        self._catgirl_llm_task = loop.create_task(self._idle_catgirl_async(snapshot, payload, key))

    async def _idle_danmaku_tick(self) -> None:
        """SSE 没有变化事件时也检查陪伴闲聊；到期先取新状态，不对旧快照直接发言。"""
        snapshot = self._state.snapshot or {}
        sync = snapshot.get("catgirl_sync") or {}
        payload = sync.get("payload") or {}
        if not self._idle_danmaku_allowed(snapshot) or payload.get("summary_kind") not in {
            "combat", "map", "event", "reward", "selection", "shop", "rest",
        }:
            self._danmaku_idle_observation = ""
            return
        key = self._commentary_observation_key(snapshot)
        if key != self._danmaku_idle_observation:
            self._danmaku_idle_observation = key
            self._danmaku_idle_since = monotonic()
            return
        last_activity = max(
            self._danmaku_idle_since,
            self._last_danmaku_at if self._last_danmaku_at is not None else self._danmaku_idle_since,
            self._danmaku_idle_attempt_at if self._danmaku_idle_attempt_at is not None else self._danmaku_idle_since,
        )
        if monotonic() - last_activity < DANMAKU_IDLE_INTERVAL or not self._danmaku_ready():
            return
        if self._catgirl_llm_task is not None and not self._catgirl_llm_task.done():
            return
        # refresh_state 的同步出口统一处理场景变更、断线、即时点评和闲聊的优先级。
        await self.refresh_state(trigger_sync=True)

    def _idle_catgirl_fallback(self, kind: str) -> str:
        scene_lines = {
            "combat": "这回合我陪你慢慢看喵。",
            "map": "分岔路总让我想多看一眼喵。",
            "event": "遇到这种事，我也想多琢磨一会儿。",
            "reward": "挑奖励这会儿，我还挺期待的喵。",
            "selection": "我也想把这些牌再看一遍喵。",
            "shop": "逛商店就忍不住想多看两眼喵。",
            "rest": "在这里歇一会儿也挺舒服喵。",
        }
        choices = (
            scene_lines.get(kind, "我还在这儿陪着喵。"),
            "我陪着呢，慢慢来就好喵。",
            "我先伸个懒腰，耳朵还竖着喵。",
            "不赶时间，我也想歇口气喵。",
            "我安静待一会儿，可没溜走喵。",
        )
        return next(line for line in choices if line not in self._recent_idle_lines)

    async def _idle_catgirl_async(self, snapshot: dict[str, Any], payload: dict[str, Any], key: str) -> None:
        if not self._idle_danmaku_allowed(snapshot) or self._commentary_observation_changed(key):
            return
        progress = (self._state.step_count, self._state.solver_step_index)
        last_sent = self._last_danmaku_at
        text = None
        if self._catgirl_llm_enabled and self._catgirl_llm is not None and self._catgirl_llm.available:
            try:
                text = await asyncio.wait_for(self._catgirl_llm.generate(
                    summary_text=self._situation_digest_text(snapshot, payload),
                    summary_kind="idle",
                    payload={"recent_idle_lines": list(self._recent_idle_lines)},
                ), timeout=DANMAKU_GENERATION_TIMEOUT)
            except Exception as exc:
                self.logger.info("[sts2_danmaku_idle] generation unavailable: %s", exc)
        if (not self._idle_danmaku_allowed(self._state.snapshot or snapshot)
                or self._commentary_observation_changed(key)
                or progress != (self._state.step_count, self._state.solver_step_index)
                or self._last_danmaku_at != last_sent):
            return
        text = self._compact_danmaku(text or "")
        normalized = "".join(c for c in text if c.isalnum())
        if not normalized or any(normalized == "".join(c for c in line if c.isalnum()) for line in self._recent_idle_lines):
            text = self._idle_catgirl_fallback(str(payload.get("summary_kind") or ""))
        # 最近三句只供短期避重，不写入长期记忆；发送仍走统一限频/去重出口。
        self._recent_idle_lines = (self._recent_idle_lines + [text])[-3:]
        await self._push_current_catgirl(text, key, fetch_avatar=False)

    @staticmethod
    def _main_server_port() -> int:
        """解析 N.E.K.O 主 server 端口（环境变量优先，默认 48911）。"""
        for key in ("NEKO_MAIN_SERVER_PORT", "MAIN_SERVER_PORT"):
            raw = os.environ.get(key)
            if raw:
                try:
                    port = int(raw)
                    if 0 < port <= 65535:
                        return port
                except ValueError:
                    pass
        return 48911

    def _cached_avatar_base64(self) -> str | None:
        """只用**已缓存**的头像（TTL 内）。即时短句用它，避免为头像等一次 HTTP 而拖慢弹幕。"""
        try:
            if self._avatar_b64 is not None and (time() - self._avatar_at) < 300:
                return self._avatar_b64
        except Exception:
            pass
        return None

    async def _catgirl_avatar_base64(self) -> str | None:
        """取猫娘头像 base64（N.E.K.O 主 server card-drop），TTL 300s 缓存；失败返回上次缓存或 None。"""
        now = time()
        if self._avatar_b64 is not None and now - self._avatar_at < 300:
            return self._avatar_b64
        try:
            url = f"http://127.0.0.1:{self._main_server_port()}/api/card-drop/active-character?include_avatar=true"
            # ★ 与 transport_client 同一口径：走宿主共享的 internal client
            #   （proxy=None + trust_env=False），因为这也是**本机 127.0.0.1** 请求 ——
            #   修复前用裸 httpx.AsyncClient，会被系统代理（实测 FlClash 127.0.0.1:7890）劫持成 502，
            #   头像就永远取不到（静默降级成 None，只表现为"弹幕没头像"，很难查）。
            #   ★ 不得 async with / aclose()：生命周期归宿主。timeout 逐请求传。
            from utils.internal_http_client import get_internal_http_client

            client = get_internal_http_client()
            resp = await client.get(url, timeout=3.0)
            payload = resp.json()
            # 48911 card-drop 顶层直接返回 {name, dataUrl}；头像在 dataUrl 字段（可能带 data:...;base64, 前缀），
            # 不是 data 包装里的 avatar。Mod 端 DecodeAvatar.StripDataPrefix 会去掉前缀，直接传即可。
            avatar = payload.get("dataUrl") if isinstance(payload, dict) else None
            if avatar:
                self._avatar_b64 = str(avatar)
                self._avatar_at = now
        except Exception:
            return self._avatar_b64
        return self._avatar_b64

    @staticmethod
    def _compact_danmaku(text: str) -> str:
        # 一次只展示一句；长度约束放在出口，不能仅靠 LLM 遵守提示词。
        clean = " ".join(str(text or "").split()).strip()
        for index, char in enumerate(clean):
            if char in "。！？!?" and index > 0:
                clean = clean[:index + 1]
                break
        if len(clean) > DANMAKU_MAX_CHARS:
            clean = clean[:DANMAKU_MAX_CHARS - 1].rstrip("，、；｜ ") + "…"
        return clean

    def _danmaku_ready(self) -> bool:
        return not self._danmaku_sending and (
            self._last_danmaku_at is None
            or monotonic() - self._last_danmaku_at >= DANMAKU_MIN_INTERVAL
        )

    async def _push_catgirl(self, text: str, *, fetch_avatar: bool = True) -> bool:
        """推一条猫娘点评到游戏内弹幕（mod /danmaku），带可选头像。

        这是插件侧唯一的弹幕出口（LLM 分支与启发式兜底都汇到这里），所以 danmaku_enabled 的闸门
        放在这一处即可覆盖全部来源。

        ``fetch_avatar=False``：只用**已缓存**的头像，绝不为它等 HTTP —— 即时短句靠这个保住延迟。

        ★★★ t51：弹幕关着时**不静音，换通道**。

            玩家口径（2026-10-26）：「关闭弹幕，但是可以用 push_message 让猫娘点评一下」。
            游戏内弹幕在 autoplay 期间是**故意关的**（2026-10-19 指令：代打时没人盯屏幕，
            弹幕刷出去没收益还挤占 LLM 与日志）；但"没人盯弹幕"不等于"不用说话"——
            这一支把同一段点评交回宿主（`push_message` + `ai_behavior="respond"`），
            由猫娘在聊天窗口用自己的口吻说。

            ★ 只在**弹幕关着**时走这条路：开着弹幕还推宿主 = 同一句点评说两遍。
            ★ 也正因为这一支不需要头像，`return` 必须在"取头像"之前（否则为一条
              发不出去的弹幕白等一次最长 3 秒的 HTTP）。
        """
        if not self._danmaku_enabled:
            self._push_host_commentary(
                text,
                scene_key=str(getattr(self._state, "last_push_scene_key", "") or ""),
            )
            return False
        client = self._client
        if client is None or not text:
            return False
        text = self._compact_danmaku(text)
        if not text or not self._danmaku_ready():
            return False
        now = monotonic()
        self._recent_danmaku = {key: at for key, at in self._recent_danmaku.items() if now - at < 60.0}
        run_id = str(self._state.snapshot.get("run_id") or "")
        fingerprint = run_id + "|" + "".join(c for c in text if c.isalnum())
        if fingerprint in self._recent_danmaku:
            return False
        # 在第一个 await 前占位；即时句、LLM 和兜底共用出口，不并发等待后一起刷屏。
        self._danmaku_sending = True
        observation_key = self._commentary_observation_key(self._state.snapshot)
        try:
            avatar = await self._catgirl_avatar_base64() if fetch_avatar else self._cached_avatar_base64()
            if self._commentary_observation_changed(observation_key) or not self._danmaku_enabled:
                return False
            self._last_danmaku_at = monotonic()
            await asyncio.wait_for(client.push_danmaku(text, style="catgirl", avatar=avatar), timeout=2.0)
            self._recent_danmaku[fingerprint] = monotonic()
            self.logger.info("[sts2_catgirl_llm] pushed catgirl danmu to mod chars=%s", len(text))
            return True
        except Exception as exc:
            self.logger.info("[sts2_catgirl_llm] push failed: %s", exc)
            return False
        finally:
            self._danmaku_sending = False

    def _danmaku_change_key(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> str:
        """按游戏事实而非触发来源/文案标记一次变化；与更严格的迟到检查分开。"""
        summary = snapshot.get("situation_summary") or {}
        facts = summary.get("payload")
        if not isinstance(facts, dict) or not facts:
            # 旧快照/精简调用也只取事实，不能把 message、trigger 或上次动作 delta 当新局势。
            factual_fields = {
                "player", "enemies", "hand", "current_hp", "max_hp", "gold", "deck", "relics", "potions",
                "event", "options", "map", "available_nodes", "travelable_nodes", "reward", "reward_cards",
                "shop", "shop_cards", "rest", "selection", "selection_cards", "selected_count",
                "min_select", "max_select", "required_count", "can_confirm",
            }
            facts = {k: v for k, v in payload.items() if k in factual_fields}
            raw = snapshot.get("raw_state") or {}
            facts = {**facts, "scene": {k: raw[k] for k in (
                "combat", "event", "map", "reward", "shop", "rest", "selection",
            ) if k in raw}}
        # 可用操作在动画中短暂消失/恢复，不是新的战况；时钟/版本号也不是。
        def stable(value):
            if isinstance(value, dict):
                return {k: stable(v) for k, v in value.items() if k not in {
                    "available_actions", "captured_at", "polled_at", "state_version", "request_id",
                }}
            if isinstance(value, list):
                return [stable(v) for v in value]
            return value
        recommendation = self._current_commentary_plan_line(snapshot).split("；", 1)[0] if payload.get("summary_kind") == "combat" else ""
        identity = [self._instant_line_key(snapshot, payload), stable(facts), recommendation]
        return sha1(json.dumps(identity, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()

    async def _push_change_catgirl(self, text: str, observation_key: str, change_key: str, *, fetch_avatar: bool = True) -> bool:
        """所有局势点评共享一次播报名额；闲聊与宿主聊天不使用这个账本。"""
        if self._commentary_observation_changed(observation_key):
            return False
        if self._danmaku_enabled and change_key and change_key in self._reported_danmaku_changes:
            return False
        sent = await self._push_catgirl(text) if fetch_avatar else await self._push_catgirl(text, fetch_avatar=False)
        if sent and change_key:
            self._reported_danmaku_changes[change_key] = None
            # 有界指纹窗口：不存文本，不因长时间游玩无限增长。
            while len(self._reported_danmaku_changes) > 256:
                self._reported_danmaku_changes.pop(next(iter(self._reported_danmaku_changes)))
        return bool(sent)

    @staticmethod
    def _commentary_observation_key(snapshot: dict[str, Any]) -> str:
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        summary = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        facts = [snapshot.get("run_id"), snapshot.get("screen"), snapshot.get("floor"),
                 snapshot.get("turn"), raw.get("combat"), summary.get("payload")]
        return sha1(json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()

    def _commentary_observation_changed(self, key: str) -> bool:
        return bool(self._state.snapshot) and key != self._commentary_observation_key(self._state.snapshot)

    async def _push_current_catgirl(self, text: str, observation_key: str, *, fetch_avatar: bool = True) -> None:
        if not self._commentary_observation_changed(observation_key):
            await self._push_catgirl(text, fetch_avatar=fetch_avatar)

    async def _catgirl_llm_async(self, snapshot: dict[str, Any], payload: dict[str, Any], companion_evaluation: dict[str, Any] | None = None, scene_key: str = "") -> None:
        """异步生成猫娘点评并推 catgirl 弹幕；**LLM 没产出就退回启发式文本**（不能噤声）。

        战斗时给 LLM 的 prompt 精简为三样：已核验的剩余计划 + 怪物行动意图 + 我方生命，
        其余界面保持原 summary_text（太长会导致 LLM 抓不住重点、输出变唠叨）。
        """
        kind = str(payload.get("summary_kind") or "")
        change_key = self._danmaku_change_key(snapshot, payload)
        if self._danmaku_enabled and change_key in self._reported_danmaku_changes:
            return
        observation_key = self._commentary_observation_key(snapshot)
        step_index = self._state.solver_step_index
        step_count = self._state.step_count
        if self._commentary_observation_changed(observation_key):
            return
        try:
            situation = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
            summary_text = str(payload.get("message") or situation.get("text") or "").strip()
            if kind == "combat":
                summary_text = await self._build_combat_prompt(snapshot, payload)
            self.logger.info("[sts2_catgirl_llm] generate kind=%s text=%s", kind, summary_text[:80])
            try:
                text = await asyncio.wait_for(self._catgirl_llm.generate(
                    summary_text=summary_text,
                    summary_kind=kind,
                    payload=payload,
                ), timeout=DANMAKU_GENERATION_TIMEOUT)
            except Exception as exc:
                self.logger.info("[sts2_catgirl_llm] generation unavailable kind=%s error=%s", kind, exc)
                text = None
            # 迟到丢弃：生成花了几秒，若画面已经换了场景/回合，这条讲解就是"上一个场景的弹幕"
            # —— 玩家抱怨的正是这个。即时短句（_emit_instant_catgirl_line）已经把该说的说过了。
            stale = (
                self._commentary_observation_changed(observation_key)
                or self._state.step_count != step_count
                or self._state.solver_step_index != step_index
                # 关闭弹幕后即时场景键不再更新，不能用旧键拦截宿主点评。
                # 真实局势和执行进度是否变化仍由上面三项检查。
                or (
                    self._danmaku_enabled
                    and bool(scene_key)
                    and bool(self._state.last_instant_line_key)
                    and self._state.last_instant_line_key != scene_key
                )
            )
            if text:
                if stale:
                    self.logger.info(
                        "[sts2_catgirl_llm] 讲解迟到已丢弃 kind=%s %s -> %s",
                        kind,
                        scene_key,
                        self._state.last_instant_line_key,
                    )
                    return
                self.logger.info("[sts2_catgirl_llm] result=%s", text)
                await self._push_change_catgirl(text, observation_key, change_key)
                return
            # LLM **可用但这次没产出**：必须退回启发式文本。
            # 旧代码这里什么都不推，而 _maybe_emit_catgirl_llm 又因为 llm_ok=True 不会走它自己的
            # 兜底分支 —— 于是猫娘在游戏里彻底噤声。实测：战斗场景 208 次调用全部 result=None，
            # 一条弹幕都没有，而商店/地图/奖励场景正常出声。
            if stale:
                self.logger.info("[sts2_catgirl_llm] 兜底也迟到已丢弃 kind=%s", kind)
                return
            self.logger.info("[sts2_catgirl_llm] result=None kind=%s -> 退回启发式文本", kind)
            self._push_catgirl_fallback(dict(companion_evaluation or {}), payload)
        except Exception as exc:
            self.logger.info("[sts2_catgirl_llm] exception %s kind=%s", exc, kind)

    async def _build_combat_prompt(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> str:
        """点评只读已核验的剩余计划，绝不为了说话另跑一次求解器搜索。"""
        steps_text = self._current_commentary_plan_line(snapshot)
        # 怪物血量+意图 + 我方生命 + 当前层级。敌人带当前血量：payload["enemies"] 只有 name/intent，
        # 血量要从 snapshot.situation_summary.payload.enemies 取。
        enemies_pool = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
        situation = snapshot.get("situation_summary") if isinstance(snapshot.get("situation_summary"), dict) else {}
        sum_payload = situation.get("payload") if isinstance(situation.get("payload"), dict) else {}
        full_enemies = sum_payload.get("enemies") if isinstance(sum_payload.get("enemies"), list) else []
        enemy_bits = []
        for idx in range(min(len(enemies_pool), 3)):
            base_e = enemies_pool[idx]
            if not isinstance(base_e, dict):
                continue
            name = str(base_e.get("name") or "敌人")
            intent = str(base_e.get("intent") or "")
            full = full_enemies[idx] if idx < len(full_enemies) and isinstance(full_enemies[idx], dict) else {}
            chp = full.get("current_hp") if full.get("current_hp") is not None else base_e.get("hp")
            mhp = full.get("max_hp")
            hp_text = ""
            if isinstance(chp, (int, float)):
                hp_text = f"{int(chp)}/{int(mhp or 0)}"
            enemy_bits.append(f"{name}({hp_text}，意图:{intent})" if hp_text else (f"{name}(意图:{intent})" if intent else name))
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        hp = ""
        if isinstance(player.get("current_hp"), (int, float)):
            hp = f" 我方生命{int(player['current_hp'])}/{int(player.get('max_hp') or 0)}"
        floor = ""
        try:
            fl = self._safe_int(snapshot.get("floor"))
            if fl:
                floor = f" 层级{fl}"
        except Exception:
            floor = ""
        enemies_text = " 敌人:" + "、".join(enemy_bits) if enemy_bits else ""
        base = steps_text or ""
        return f"本轮剩余建议: {base or '暂无可核验的出牌计划，不提供具体出牌建议。'}{enemies_text}{hp}{floor}".strip()

    def _current_commentary_plan_line(self, snapshot: dict[str, Any]) -> str:
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
        combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else {}
        run_id = str(raw.get("run_id") or run.get("run_id") or run.get("runId") or run.get("id") or "")
        turn = raw.get("turn") if raw.get("turn") is not None else combat.get("turn")
        # 求解器缓存签名取自 raw_state；缺少标识/与归一化快照矛盾时不猜旧计划仍然有效。
        if not run_id or turn is None:
            return ""
        if snapshot.get("run_id") and str(snapshot["run_id"]) != run_id:
            return ""
        if snapshot.get("turn") is not None and str(snapshot["turn"]) != str(turn):
            return ""
        runner = self._loop_runner
        if getattr(runner, "_decision_engine", {}).get("engine") == "policy":
            return ""  # 网络在决策时不能拿旧求解器计划冒充它的想法。
        index = self._state.solver_step_index
        plan, _ = _reusable_plan(
            getattr(runner, "_solver_plan_cached", None),
            getattr(runner, "_solver_plan_sig", None),
            _combat_plan_signature(snapshot), index, snapshot,
        )
        return self._format_plan_line(plan, start_index=index)

    @staticmethod
    def _format_plan_line(plan: dict[str, Any] | None, *, start_index: int = 0) -> str:
        """把 /solver/plan 的当前回合 line（line[0].steps）拼成一句人类可读指令。

        每个动作一步：打<卡名>[→目标N] / 用药水<名> / 结束回合。取不到返回 ""。
        """
        if not plan:
            return ""
        try:
            steps = _current_turn_steps(plan)
        except Exception:
            return ""
        if not steps:
            return ""
        parts: list[str] = []
        for s in steps[max(0, start_index):]:
            if not isinstance(s, dict):
                continue
            k = str(s.get("kind") or "")
            if k == "play_card":
                name = str(s.get("card_name") or s.get("card_id") or "打牌")
                target = ""
                ti = s.get("target_index")
                if isinstance(ti, int):
                    target = f"→{ti + 1}"
                parts.append(f"打{name}{target}")
            elif k == "use_potion":
                parts.append(f"用药水{str(s.get('card_name') or s.get('card_id') or '')}")
            elif k == "end_turn":
                parts.append("结束回合")
        return "；".join(parts)

    def _push_catgirl_fallback(self, companion_evaluation: dict[str, Any], payload: dict[str, Any]) -> None:
        """兜底：推启发式 primary_message 到游戏内弹幕（原 catgirl 轨道文本来源）。"""
        client = self._client
        if client is None:
            return
        catgirl_text = str(
            companion_evaluation.get("primary_message") or payload.get("message") or ""
        ).strip()
        if catgirl_text:
            try:
                task = asyncio.get_running_loop().create_task(self._push_change_catgirl(
                    catgirl_text, self._commentary_observation_key(self._state.snapshot),
                    self._danmaku_change_key(self._state.snapshot, payload),
                ))
                # Fire-and-forget: retrieve any exception so asyncio doesn't warn about an unretrieved task error.
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except Exception:
                self.logger.info("[sts2_catgirl_llm] fallback push failed")
