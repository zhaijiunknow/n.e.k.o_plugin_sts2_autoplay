from __future__ import annotations

from hashlib import sha1
from time import time
from typing import Any

from .companion_evaluator import _enemy_intent_label


class ServiceHostMixin:
    """Internal responsibility mixin; initialized and composed by STS2AutoplayService."""

    def _is_host_commentary_enabled(self) -> bool:
        """弹幕关着时，是否把点评改走宿主（`push_message(ai_behavior="respond")`）。

        ★ 玩家口径（2026-10-26）：「关闭弹幕，但是可以用 push_message 让猫娘点评一下」。
        ★ 默认 **True**：与既有的「尖塔速报」（`host_digest_enabled` 默认 True）同一个取向 ——
          插件本来就会在代打时给本体发消息让它开口；这里只是把**本回合的想法**也让它说。
        ★ 还需「尖塔速报」总开关开启；它**只在弹幕关着时**生效（见 `_push_catgirl`）：弹幕开着还推宿主 = 一句话说两遍。
        """
        return self._state.host_digest_enabled and bool(self._cfg.get("host_commentary_when_danmaku_off", True))

    def _host_commentary_min_interval(self) -> float:
        """`_is_host_commentary_enabled` 那条通道的最小间隔（秒），配置可覆盖，下限 5s。"""
        try:
            value = float(self._cfg.get("host_commentary_min_interval_seconds", self.HOST_COMMENTARY_MIN_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            return self.HOST_COMMENTARY_MIN_INTERVAL_SECONDS
        return max(5.0, value)

    def _remember_host_report(self, text: str, *, kind: str, run_id: str = "") -> bool:
        """五分钟内同一情报不重交；返回 False 表示重复，不更新节流时间。"""
        now = time()
        self._recent_host_reports = {
            key: at for key, at in self._recent_host_reports.items() if now - at < 300.0
        }
        normalized = "".join(ch for ch in text if ch.isalnum())
        key = sha1(f"{kind}|{run_id}|{normalized}".encode("utf-8")).hexdigest()
        if key in self._recent_host_reports:
            self.logger.info("[sts2_host_push] duplicate report dropped kind=%s", kind)
            return False
        if len(self._recent_host_reports) >= 32:
            del self._recent_host_reports[next(iter(self._recent_host_reports))]
        self._recent_host_reports[key] = now
        return True

    def _push_host_commentary(self, text: str, *, scene_key: str = "") -> bool:
        """把一段**点评**改走宿主：`push_message` → 猫娘在聊天里用自己的口吻说。

        与 `_push_proactive_to_host` 的关系：那个是**发送出口**（visibility=[] / respond /
        ephemeral，三个开关的理由都写在它的注释里），本方法是**点评这条线的节流壳**。

        三道闸（缺一就会变成刷屏或重复）：
          ① 开关：`host_digest_enabled` 与 `host_commentary_when_danmaku_off` 均开启；
          ② 去重：同一个场景键只讲一次（`last_host_commentary_key`）；
          ③ 节流：距两条速报通道最近一次推送 ≥ `host_commentary_min_interval_seconds`（默认 45s）。
        返回 True 表示真的推了（供测试与日志核对）。
        """
        body = str(text or "").strip()
        if not body or not self._is_host_commentary_enabled():
            return False
        key = str(scene_key or body[:24])
        if key and key == getattr(self._state, "last_host_commentary_key", ""):
            return False
        now = time()
        last = max(self._state.last_host_commentary_at, self._state.last_host_digest_at)
        interval = self._host_commentary_min_interval()
        if last > 0.0 and (now - last) < interval:
            self.logger.info(
                "[sts2_catgirl_host] throttled %.1fs < %.0fs key=%s（弹幕关着，宿主点评按间隔走）",
                now - last,
                interval,
                key[:40],
            )
            return False
        if not self._remember_host_report(
            body, kind="commentary", run_id=str(self._state.snapshot.get("run_id") or "")
        ):
            return False
        self._state.last_host_commentary_key = key
        self._state.last_host_commentary_at = now
        self.logger.info(
            "[sts2_catgirl_host] pushed commentary to host（danmaku off）chars=%s key=%s",
            len(body),
            key[:40],
        )
        self._push_proactive_to_host(
            self._host_digest_prompt(self._state.snapshot, commentary=body),
            kind="autoplay_commentary",
            priority=4,
        )
        return True

    def _push_proactive_to_host(self, text: str, *, kind: str, priority: int = 5) -> None:
        """把一段内容**悄悄**交给本体，让它自己主动开口讲。

        玩家口径是：「不进记忆、不可见、本体主动讲，不然没有直播的互动味」，复盘也一样。落到
        `ctx.push_message` 的两个轴 + 一个 metadata 开关上：

        - ``visibility=[]`` —— 用户**看不到**这些 parts 本身。SDK 文档原话：空列表表示用户看不到
          内容；若同时 ``ai_behavior="respond"``，用户**只看到本体的回复**。
        - ``ai_behavior="respond"`` —— 喂进上下文**并立即触发一次 AI 回合**，所以是本体主动开口
          （``"read"`` 不会开口，那才是"没有互动味"的原因；``"blind"`` 则连上下文都不给）。
        - ``metadata["prompt_ephemeral"] = True`` —— **不进历史/记忆**。这是宿主认的临时提示开关
          （`music_pusher` 的主动推送用的是同一个）。

        另外刻意优先走 SDK 的 `ctx.push_message`：插件的 dispatcher 会把正文截到 **30 字**
        （`_host_reply_text`），战报/速报会被切碎 —— 那 30 字限制是给"状态反馈"用的。
        拿不到 SDK 上下文时退回 dispatcher；ephemeral proactive 消息同样保留完整正文。
        """
        body = str(text or "").strip()
        if not body:
            return
        meta = {"kind": kind, "prompt_ephemeral": True, "delivery_semantics": "proactive"}
        # 留痕：这条是"悄悄交给本体"的，玩家在聊天里看不到正文，所以只有日志能核对三个开关。
        # Final gate also covers delayed callbacks; livestream interaction has its own switch.
        if kind == "run_review" and not self._state.run_reflection_enabled:
            return
        if kind in {"situation_digest", "autoplay_commentary", "character_announcement", "stuck_alert"} and not self._state.host_digest_enabled:
            return
        self.logger.info(
            "[sts2_host_push] kind=%s visibility=[] ai_behavior=respond ephemeral=True chars=%s",
            kind,
            len(body),
        )
        ctx = self._sdk_ctx
        if ctx is not None and hasattr(ctx, "push_message"):
            try:
                ctx.push_message(
                    source="sts2_autoplay",
                    visibility=[],
                    ai_behavior="respond",
                    parts=[{"type": "text", "text": body}],
                    metadata=dict(meta),
                    priority=priority,
                )
                return
            except Exception as exc:
                self.logger.info("[sts2_host_push] sdk push failed (non-fatal): %s", exc)
        notifier = self._frontend_notifier
        if notifier is not None:
            try:
                notifier(
                    content=body,
                    description=body,
                    metadata=dict(meta),
                    priority=priority,
                    message_type=f"sts2_{kind}",
                    visibility=[],
                    ai_behavior="respond",
                )
            except Exception:
                pass

    def _host_digest_prompt(
        self, snapshot: dict[str, Any], *, facts: str = "", commentary: str = ""
    ) -> str:
        """两条速报通道共用事实边界；点评是建议，不是动作执行回执。"""
        if not facts:
            summary = snapshot.get("situation_summary")
            summary = summary if isinstance(summary, dict) else {}
            details = summary.get("payload")
            details = details if isinstance(details, dict) else {}
            facts = self._situation_digest_text(snapshot, {
                **details, "summary_kind": summary.get("kind"), "screen": snapshot.get("screen"),
            })
        control = "自动游玩正在运行" if self._plugin_is_driving(snapshot) else "当前未由插件自动操作"
        return (
            "《杀戮尖塔2》临时速报，非提问，不是动作执行回执。"
            "按人设用1～2句短话点评当前重点或风险，不必加喵。"
            "仅据最近观测（可能已变化），不要沿用旧局信息或重复已报内容。"
            "建议不等于已执行；不编造牌或战果，无结算依据不要保证格挡、击杀或安全。"
            "略去连接、模式、接管套话，不要反问或索要指令；未自动操作勿自称代打。"
            f"\n控制：{control}"
            f"\n最近观测：{facts or '信息不足，勿补写战况。'}"
            + (f"\n点评（仅供参考，观测优先）：{commentary}" if commentary else "")
        )

    def _situation_digest_text(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> str:
        """只报观测事实，不把意图当实际伤害，不把缺失的格挡/生命补成零。"""
        kind = str(payload.get("summary_kind") or "").lower()
        screen = str(payload.get("screen") or snapshot.get("screen") or "").lower()
        raw = snapshot.get("raw_state")
        raw = raw if isinstance(raw, dict) else {}
        run = raw.get("run")
        run = run if isinstance(run, dict) else {}
        character = str(run.get("character_name") or "")
        if not character:
            character = {
                "IRONCLAD": "铁甲战士", "SILENT": "静默猎手", "DEFECT": "故障机器人",
                "REGENT": "储君", "NECROBINDER": "亡灵契约师",
            }.get(str(run.get("character_id") or "").upper(), "")
        head = f"角色{character}；" if character else ""
        floor = snapshot.get("floor")
        if floor is not None:
            head += f"第{floor}层 "
        if kind == "combat" or screen == "combat":
            player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
            enemies = payload.get("enemies") if isinstance(payload.get("enemies"), list) else []
            bits: list[str] = []
            raw_enemies = self._raw_combat_enemies(snapshot)
            for index, enemy in enumerate(enemies[:3]):
                if not isinstance(enemy, dict):
                    continue
                name = str(enemy.get("name") or "敌人")
                raw_enemy = raw_enemies[index] if index < len(raw_enemies) else None
                intent = _enemy_intent_label(enemy, raw_enemy)
                bits.append(f"{name}({intent})" if intent else name)
            stats = ""
            if isinstance(player.get("current_hp"), (int, float)):
                stats += f"；我方生命{int(player['current_hp'])}"
                if isinstance(player.get("max_hp"), (int, float)) and player["max_hp"] > 0:
                    stats += f"/{int(player['max_hp'])}"
            if isinstance(player.get("block"), (int, float)):
                stats += f"；当前格挡{int(player['block'])}"
            turn = snapshot.get("turn")
            turn_label = f"第{turn}回合 " if turn is not None else ""
            return f"{head}{turn_label}战斗中，敌人意图：" + ("、".join(bits) if bits else "未识别") + stats
        labels = {
            "map": "地图选路", "event": "事件选择", "reward": "战利品选择",
            "card_reward": "卡牌奖励选择", "selection": "卡牌选择", "card_selection": "卡牌选择",
            "card_selection_transform": "卡牌变化选择", "card_selection_remove": "删牌选择",
            "card_selection_delet": "删牌选择", "card_selection_reward": "卡牌奖励选择",
            "card_selection_unusefull": "卡牌选择", "bundle_selection": "组合奖励选择",
            "capstone_selection": "顶点奖励选择", "relic_selection": "遗物选择",
            "shop": "商店", "shop_show": "商店", "rest": "休息处", "chest": "宝箱",
            "game_over": "本局结束", "victory": "胜利结算",
        }
        label = labels.get(screen) or labels.get(kind)
        # 标题/加载/未知屏幕没有值得播报的战况，不让本体凭空续写上一局。
        return f"{head}{label}界面" if label else ""

    def _push_host_situation_digest(self, snapshot: dict[str, Any], payload: dict[str, Any]) -> None:
        """把局势**悄悄**交给本体，让它自己主动讲一句（不可见、不进记忆）。

        两道闸门缺一不可：

        - **只在场景/回合变化时**推（`_instant_line_key`）：否则同一局面每 tick 都想开口。
        - **时间节流**：每次推送都会触发一次本体回合（`ai_behavior="respond"`），太密就是刷屏 + 烧 token。
        """
        notifier = self._frontend_notifier
        # 宿主速报与游戏内弹幕是独立通道；代打自动关闭弹幕时仍应播报。
        # 与宿主点评的共享限频及事实去重仍在下方统一生效。
        if notifier is None and self._sdk_ctx is None:
            return
        if not getattr(self._state, "host_digest_enabled", True):
            return
        try:
            key = f"{snapshot.get('run_id', '')}|{self._instant_line_key(snapshot, payload)}"
        except Exception:
            return
        if not key or key == self._state.last_host_digest_key:
            return
        now = time()
        if now - max(self._state.last_host_digest_at, self._state.last_host_commentary_at) < self.HOST_DIGEST_MIN_INTERVAL_SECONDS:
            return
        digest = self._situation_digest_text(snapshot, payload)
        if not digest:
            return
        stable_facts = self._situation_digest_text({**snapshot, "turn": None}, payload)
        if not self._remember_host_report(stable_facts, kind="digest", run_id=str(snapshot.get("run_id") or "")):
            return
        self._state.last_host_digest_key = key
        self._state.last_host_digest_at = now
        self._push_proactive_to_host(
            self._host_digest_prompt(snapshot, facts=digest),
            kind="situation_digest",
            priority=4,
        )

    def _deliver_catgirl_sync(self, snapshot: dict[str, Any]) -> None:
        # The live window owns noncombat speech; do not queue ordinary commentary over it.
        if (self._livestream.enabled and self._state.autoplay_state == "running"
                and not self._should_hold_for_orders(snapshot) and self._livestream.scene_for(snapshot)):
            return
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
        # 游戏内即时提示有自己的 6 秒节奏，不受宿主聊天概率/点评触发条件压成长期静默。
        # 静止画面不复读；长讲解和宿主速报仍走下方原有门控。
        if str(payload.get("summary_kind") or "") in {"combat", "map", "event", "reward", "selection", "shop", "rest"}:
            self._emit_instant_catgirl_line(snapshot, companion_evaluation, payload)
        self._maybe_emit_idle_catgirl(snapshot, payload)
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
        # 先做指纹/间隔去重，再进入点评 —— 防刷屏统一由 _should_deliver_sync 把关。
        # 否则点评(弹幕)不受节流控制，同一 fingerp指/场景反复刷屏。
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
        # 弹幕**双线**（解决"都到下一个场景了才刷出上一个场景的弹幕"）：
        #   ① 即时短句：完全不调 LLM，用手里的信息拼一句话立刻推 —— 弹幕才跟得上画面；
        #   ② 高风险详解：只有血低/要挨重击时才再用**本体的模型与人设**补一条讲解。
        # 实测点评生成平均 5~10 秒（主系统是推理模型，每次约 450~520 个思考 token），
        # 等它出来画面早换了；所以"及时"只能靠①，"讲得好"才靠②。场景已变的话②直接丢弃。
        self._push_host_situation_digest(snapshot, payload)
        if self._should_emit_long_commentary(snapshot, payload):
            self._maybe_emit_catgirl_llm(snapshot, companion_evaluation, payload)
        # 点评改走游戏内弹幕（上方 _maybe_emit_catgirl_llm -> mod /danmaku，文本+头像），不再经 push_message 发 NEKO。
        push_scene_key = f"{payload.get('screen')}|{payload.get('summary_kind')}|{payload.get('trigger')}"
        push_reason = str(catgirl_sync.get("reason") or "")
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

    def _push_companion_message(self) -> None:
        if not bool(self._cfg.get("companion_mode_enabled", False)):
            return
        notifier = self._frontend_notifier
        snapshot = self._state.snapshot if isinstance(self._state.snapshot, dict) else {}
        companion_evaluation = snapshot.get("companion_evaluation") if isinstance(snapshot.get("companion_evaluation"), dict) else {}
        if companion_evaluation and not bool(companion_evaluation.get("should_comment", True)):
            return
        strategy_context = snapshot.get("strategy_context") if isinstance(snapshot.get("strategy_context"), dict) else {}
        strategy_name = str(strategy_context.get("strategy_name") or companion_evaluation.get("strategy_name") or self.t("companion.current_strategy", default="我现在用的策略"))
        commentary = str(companion_evaluation.get("commentary") or "").strip()
        if not commentary:
            commentary = self.t("companion.enabled_default_commentary", default="我来了，会盯着局势随时给你提醒。")
        content = self._host_reply_text(
            self.t("companion.enabled_announcement", default="我来了。{strategy_name}：{commentary}", strategy_name=strategy_name, commentary=commentary)
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
