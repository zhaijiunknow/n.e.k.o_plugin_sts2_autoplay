from __future__ import annotations

import asyncio
from hashlib import sha1
from time import time
from typing import Any

from .choice_review import SOURCE_NETWORK, collect_names, review_digest
from .history_view import write_history_view


class ServiceHistoryMixin:
    """Internal responsibility mixin; initialized and composed by STS2AutoplayService."""

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
            return self.t("companion.player_operation.combat_started", default="你进新战斗了。")
        if event_type == "combat_ended":
            return self.t("companion.player_operation.combat_ended", default="你刚打完这场，局面切到结算了。")
        if event_type == "combat_turn_advanced":
            return self.t("companion.player_operation.combat_turn_advanced", default="你推进到新的战斗回合了。")
        if event_type == "player_card_or_action_committed":
            return delta_text or self.t("companion.player_operation.action_committed", default="你在战斗里走了一步。")
        if event_type == "choice_committed":
            return self.t("companion.player_operation.choice_committed", default="你刚做完一个关键选择，画面从 {previous_screen} 进到 {current_screen} 了。", previous_screen=previous_screen, current_screen=current_screen)
        if event_type == "run_progressed":
            return self.t("companion.player_operation.run_progressed", default="你推进了流程，楼层或章节变了。")
        if event_type == "screen_transition":
            return self.t("companion.player_operation.screen_transition", default="你把画面从 {previous_screen} 切到了 {current_screen}。", previous_screen=previous_screen, current_screen=current_screen)
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
        self._remember_hp_sample(snapshot)
        self._remember_route_step(snapshot)
        self._maybe_announce_character(snapshot)
        self._maybe_record_run_memory(snapshot)

    @staticmethod
    def _room_identity(screen: str, raw_state: dict[str, Any]) -> str:
        """这一屏的**稳定标识**，用于跨局复盘。

        玩家口径：「尖塔的地图是随机的，同样的层可能是不同的怪，所以（只记层数）完全没有用，
        应该是记对应房间的 id」。mod 实际能给的是：

        - 战斗：`combat.enemies[].enemy_id`（这一场的敌人组合，如 `LEAF_SLIME_M+LEAF_SLIME_S`）
        - 事件：`event.event_id`
        - 篝火/商店/宝箱：**没有 id**，只能用房间类型兜底（`room:REST` 之类）

        注意 mod 没有 encounter/room id，所以战斗用"敌人组合"当遭遇标识 —— 这已经足够区分
        "同一层遇到的第 3 层史莱姆"和"第 3 层的精英"。
        """
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        ids = sorted(
            {
                str(enemy.get("enemy_id") or "")
                for enemy in enemies
                if isinstance(enemy, dict) and enemy.get("enemy_id") and enemy.get("is_alive", True)
            }
        )
        if ids:
            return "+".join(ids)
        event = raw_state.get("event") if isinstance(raw_state.get("event"), dict) else {}
        event_id = str(event.get("event_id") or "")
        if event_id:
            return event_id
        return f"room:{screen or 'unknown'}" if screen else ""

    def _remember_hp_sample(self, snapshot: dict[str, Any]) -> None:
        """记一条生命采样，供局末做"哪里损血"的归因（HP 或界面没变就不重复记）。"""
        try:
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
            run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
            hp = player.get("current_hp")
            if not isinstance(hp, (int, float)):
                hp = run.get("current_hp")
            if not isinstance(hp, (int, float)):
                return
            max_hp = player.get("max_hp")
            if not isinstance(max_hp, (int, float)):
                max_hp = run.get("max_hp")
            run_id = str(snapshot.get("run_id") or "")
            if run_id and run_id != self._state.hp_trail_run_id:
                # 换局了：轨迹清零重新记（否则上一局的损血会算到这一局头上）。
                self._state.hp_trail_run_id = run_id
                self._state.hp_trail = []
                self._state.combat_room_id = ""
            screen = str(snapshot.get("screen") or "")
            in_combat = bool(snapshot.get("in_combat"))
            if in_combat:
                # 一场战斗的标识取**遭遇开始时**的敌人组合：战斗中敌人会死，若每帧重取，
                # 同一场战斗会被拆成好几个"房间"，归因就散了。
                if not self._state.combat_room_id:
                    self._state.combat_room_id = self._room_identity(screen, raw_state)
                room = self._state.combat_room_id
            else:
                self._state.combat_room_id = ""
                room = self._room_identity(screen, raw_state)
            sample = {
                "hp": int(hp),
                "max_hp": int(max_hp or 0),
                "screen": screen,
                "room": room,
                "floor": self._safe_int(snapshot.get("floor")) or 0,
                "step_count": int(self._state.step_count),
                "at": time(),
            }
            trail = self._state.hp_trail
            if trail and trail[-1].get("hp") == sample["hp"] and trail[-1].get("room") == sample["room"]:
                return
            trail.append(sample)
            if len(trail) > 400:
                del trail[: len(trail) - 400]
        except Exception:
            pass

    def _run_snapshot(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        """把"这一个房间的进入/离开状态"压缩成可比较的一组值（算收获用）。"""
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        hp = player.get("current_hp")
        if not isinstance(hp, (int, float)):
            hp = run.get("current_hp")
        deck = run.get("deck") if isinstance(run.get("deck"), list) else []
        relics = run.get("relics") if isinstance(run.get("relics"), list) else []
        potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        return {
            "hp": int(hp) if isinstance(hp, (int, float)) else None,
            "gold": self._safe_int(run.get("gold")) or 0,
            "deck": sorted(str(card.get("card_id") or "") for card in deck if isinstance(card, dict)),
            "relics": sorted(str(relic.get("relic_id") or relic.get("name") or "") for relic in relics if isinstance(relic, dict)),
            "potions": sorted(
                str(potion.get("potion_id") or "")
                for potion in potions
                if isinstance(potion, dict) and potion.get("potion_id")
            ),
        }

    def _remember_route_step(self, snapshot: dict[str, Any]) -> None:
        """逐房间记路线图：**每个房间掉多少血、拿到什么**。

        玩家口径：「需要路线图，对应每个房间的损血和收获」。判定与记法：

        - 房间键 = `层|界面|房间标识`；奖励类界面（REWARD / CARD_SELECTION_REWARD）不另起一段 ——
          奖励是"这个房间打完之后"发的，算进同一个房间才看得懂"这房间值不值"。
        - 损血 = 进房间时的血 - 记录过程中最低的血；收获 = 金币差 + 新增卡/遗物/药水（按 id 差集）。
        - 换房间、换局、局末时收尾。
        """
        try:
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            screen = str(snapshot.get("screen") or "")
            if not screen or screen.upper() in {"MAIN_MENU", "UNKNOWN"}:
                return
            run_id = str(snapshot.get("run_id") or "")
            if run_id and run_id != self._state.hp_trail_run_id and self._state.hp_trail_run_id:
                # 换局（hp_trail 已先一步重置）：路线图也重来。
                self._state.route_log = []
                self._state.route_room_key = ""
                self._state.route_start = {}
            current = self._run_snapshot(raw_state)
            room = self._room_identity(screen, raw_state)
            floor = self._safe_int(snapshot.get("floor")) or 0
            previous = self._state.route_last if isinstance(self._state.route_last, dict) else {}
            merging = screen.upper() in self._REWARD_LIKE_SCREENS and bool(self._state.route_room_key)
            if merging:
                # 奖励是"这个房间打完之后"发的：并入上一个房间，且**保留原房间的身份**
                # （否则这段会被记成 room:REWARD，路线图上就看不出是打哪一场拿到的）。
                key = self._state.route_room_key
                current["room"] = previous.get("room") or room
                current["screen"] = previous.get("screen") or screen
                current["floor"] = previous.get("floor", floor)
            else:
                key = f"{floor}|{screen}|{room}"
                current["room"] = room
                current["screen"] = screen
                current["floor"] = floor
            if key != self._state.route_room_key:
                self._close_route_room(reason="next_room")
                self._state.route_room_key = key
                self._state.route_start = dict(current)
            hp = current.get("hp")
            lowest = current.get("lowest_hp") if "lowest_hp" in current else previous.get("lowest_hp")
            if isinstance(hp, (int, float)):
                lowest = int(hp) if not isinstance(lowest, (int, float)) else min(int(lowest), int(hp))
            current["lowest_hp"] = lowest
            # ★★ t34 / D21 修：显式记录「这一段里到底读没读到过 hp」。
            #
            # 【为什么必须加这个标记】把本方法逐字抽出来跑（.dsh-artifacts/_t34_sim.py）证明：
            #   · hp 可读时，本方法**产生不出** lowest_hp==0（实验1：0/2）
            #   · 但 hp **读不到**时（`isinstance(hp,(int,float))` 为假），:2657 仍写
            #     `lowest_hp = None`；`_close_route_room` 的 isinstance 为假 ⇒ `hp_lost` 静默留在 0
            #   ⇒ 这个 0 与「这房间真的没掉血」的 0 **完全无法区分**
            #   ⇒ 属 CANON 口径的「无痕迹的观察缺口」—— 死因判据无法知道哪条可信。
            #
            # 【★ 为什么不是「把 hp_lost 改成别的值」】契约（t30 §1）把 `hp_lost` 定义为
            #   `max(0, hp_start - lowest_hp)`，且死因判据依赖它。**不许换定义**。
            #   ⇒ 所以只**加一个可分辨标记**，`hp_lost` 的数值一个字节都不动
            #     （`test_new_field_does_not_change_hp_lost_value` 锁住这点）。
            #
            # 【历史数据的说明】实测全库 322 条 COMBAT 的 lowest_hp==0 **全部落在
            #   28 个「整局全 0」的历史局**（10-17 ~ 10-19）；22 个 mixed 局的 447 条 COMBAT
            #   里 **0 条** 有 0 ⇒ 那些 0 是旧代码形态的产物，不是当前代码产生的。
            #   详见 .dsh-artifacts/_t34_diag.log / _t34_root.log / _t34_verdict.log。
            current["hp_seen"] = isinstance(hp, (int, float))
            current["start_hp_seen"] = current.get("start_hp_seen")
            self._state.route_last = current
            # ★ 房间起始时的可读性：开房那一刻记一次，结算时两段都要可读才算「损血已知」。
            if not self._state.route_start.get("start_hp_seen"):
                self._state.route_start["start_hp_seen"] = isinstance(
                    self._state.route_start.get("hp"), (int, float)
                )
        except Exception as exc:
            self.logger.info("[sts2_route] step failed (non-fatal): %s", exc)

    def _close_route_room(self, *, reason: str) -> None:
        """收尾当前房间，把损血与收获写进路线图。"""
        start = self._state.route_start if isinstance(self._state.route_start, dict) else {}
        last = self._state.route_last if isinstance(self._state.route_last, dict) else {}
        if not start or not last:
            return
        start_hp = start.get("hp")
        lowest = last.get("lowest_hp")
        hp_lost = 0
        if isinstance(start_hp, (int, float)) and isinstance(lowest, (int, float)):
            hp_lost = max(0, int(start_hp) - int(lowest))
        # ★★ t34 / D21：损血是否**真的量到了**。两个条件都满足才算已知：
        #   · 开房那一刻读到了 hp（否则 hp_start 为 None，`max(0, ...)` 的输入就不完整）
        #   · 本段至少有一次读到过 hp（否则 lowest 为 None）
        # ★ 注意：这里**只标注**，不动 hp_lost 的值（契约要求定义不变）。
        hp_loss_known = bool(
            isinstance(start_hp, (int, float))
            and isinstance(lowest, (int, float))
            and start.get("start_hp_seen") is not False
            and last.get("hp_seen") is not False
        )
        new_cards = sorted(set(last.get("deck") or []) - set(start.get("deck") or []))
        new_relics = sorted(set(last.get("relics") or []) - set(start.get("relics") or []))
        new_potions = sorted(set(last.get("potions") or []) - set(start.get("potions") or []))
        entry = {
            "room": str(last.get("room") or ""),
            "screen": str(last.get("screen") or ""),
            "floor": last.get("floor"),
            "hp_start": start_hp,
            "hp_end": last.get("hp"),
            "lowest_hp": lowest,
            "hp_lost": hp_lost,
            # ★ t34 / D21 新增：`hp_loss_known=False` 表示「这段的损血**不知道**」，
            #   与 `hp_lost == 0`（真的没掉血）必须可区分。
            #   消费方（死因判据 / 六维反思）可以据此决定是否采信这一条。
            "hp_loss_known": hp_loss_known,
            "gold_gained": max(0, int(last.get("gold") or 0) - int(start.get("gold") or 0)),
            "cards_gained": new_cards,
            "relics_gained": new_relics,
            "potions_gained": new_potions,
            "ended_by": reason,
        }
        # 什么都没发生的空段（例如地图上走一格）不记，免得路线图全是噪声；
        # 其它房间即使零损血零收获也保留，这样路线是完整的。
        if hp_lost == 0 and entry["gold_gained"] == 0 and not (new_cards or new_relics or new_potions):
            if str(last.get("screen") or "") == "MAP":
                return
        self._state.route_log.append(entry)
        if len(self._state.route_log) > self._ROUTE_MAX_ROOMS:
            del self._state.route_log[: len(self._state.route_log) - self._ROUTE_MAX_ROOMS]

    def _hp_loss_report(self) -> dict[str, Any]:
        """把本局生命轨迹整理成"哪里损血"的归因表（局末总结用）。"""
        trail = [s for s in self._state.hp_trail if isinstance(s, dict)]
        if not trail:
            return {}
        losses: list[dict[str, Any]] = []
        for prev, cur in zip(trail, trail[1:]):
            try:
                delta = int(cur.get("hp") or 0) - int(prev.get("hp") or 0)
            except (TypeError, ValueError):
                continue
            if delta < 0:
                losses.append(
                    {
                        # 归因按**房间标识**（敌人组合 / 事件 id），不是层数 —— 尖塔地图随机，
                        # "第 27 层"在不同局根本不是同一个房间，那样的复盘没法复用。
                        "room": str(cur.get("room") or ""),
                        "screen": str(cur.get("screen") or ""),
                        "floor": cur.get("floor"),
                        "hp_before": prev.get("hp"),
                        "hp_after": cur.get("hp"),
                        "lost": -delta,
                        "at_step": cur.get("step_count"),
                    }
                )
        hp_values = []
        for sample in trail:
            try:
                hp_values.append(int(sample.get("hp") or 0))
            except (TypeError, ValueError):
                continue
        max_hp = 0
        for sample in trail:
            try:
                max_hp = max(max_hp, int(sample.get("max_hp") or 0))
            except (TypeError, ValueError):
                continue
        return {
            "samples": len(trail),
            "max_hp": max_hp,
            "min_hp": min(hp_values) if hp_values else 0,
            "final_hp": hp_values[-1] if hp_values else 0,
            "total_lost": sum(int(item["lost"]) for item in losses),
            "loss_events": len(losses),
            "heaviest": sorted(losses, key=lambda item: -int(item["lost"]))[:3],
        }

    def _maybe_announce_character(self, snapshot: dict[str, Any]) -> None:
        """开局后**报一次**"这局我玩的是谁"，并顺手告诉本体。

        角色是游戏自带的「随机」抽的（见 heuristic_planner 的 character_select_random），
        所以实际抽到谁只能开局后从 run 里读 —— 顺便让猫娘把它说给玩家听。
        """
        try:
            run_id = str(snapshot.get("run_id") or "")
            if not run_id or run_id in {"run_unknown"}:
                return
            if self._state.announced_character_run_id == run_id:
                return
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
            name = str(run.get("character_name") or "")
            character_id = str(run.get("character_id") or "")
            display = name or character_id
            if not display:
                return
            self._state.announced_character_run_id = run_id
            self._schedule_catgirl_push(f"这局我抽到{display}喵，开打！")
            # 悄悄交给本体：不可见、不进记忆，但会让它自己主动报一句开场。
            # 归在「尖塔速报」这个开关下（它也是"跟本体说话"这一路）。
            #
            # 措辞刻意**不提"观众"**：这个插件目前是单人陪伴场景，叫观众会让猫娘对空气喊话。
            # "观众"是留给后续直播联动的（届时由直播插件提供听众人设，本插件再按场景切换措辞）。
            if getattr(self._state, "host_digest_enabled", True):
                self._push_proactive_to_host(
                    f"《杀戮尖塔2》新的一局开始了，这局随机到的角色是「{display}」。"
                    "用你自己的口吻报一句开场，一两句就够，别念稿。",
                    kind="character_announcement",
                    priority=6,
                )
            self.logger.info("[sts2_character] 本局角色=%s run_id=%s", display, run_id)
        except Exception as exc:
            self.logger.info("[sts2_character] announce failed (non-fatal): %s", exc)

    def _final_deck_snapshot(self, raw_state: dict[str, Any]) -> list[dict[str, Any]]:
        """取**局末牌组**的完整卡片（不是 id 列表）。

        为什么要单独取：`_run_snapshot`（:2054）为了"算收获"把 deck 压成了
        **排序后的 card_id 列表**（:2068）—— 那对差集够用，但**六维量化读不了**
        （它要 card_type / upgraded / energy_cost / dynamic_values / rules_text）。

        ⇒ 这里回到 `raw_state.run.deck` 原始数组（mod 的 `DeckCardPayload`，
          `GameStateService.cs:4993`），那里字段是全的。

        ⚠️ 兜底：终局屏如果没带 deck（例如 victory 屏的 payload 不同），
           就返回空列表 —— `build_gap_lessons` 对空牌组**不产教训**，
           不会编造，也不会抛错。
        """
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        deck = run.get("deck")
        if not isinstance(deck, list):
            return []
        return [dict(card) for card in deck if isinstance(card, dict)]

    def _build_gap_lessons(
        self,
        deck: list[dict[str, Any]],
        *,
        route: list[dict[str, Any]],
        floor: int,
        hp_report: dict[str, Any],
        outcome: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """局末把牌组量化成「六维缺口」教训（`deck_gap_reflection`）。

        ★ 死因与缺口**交叉**：死因决定"该重点看哪一维"，缺口决定"缺多少"。
          这正是玩家要的「遇到群怪就死就反思有没有拿 aoe 卡，
          遇到 boss 和精英就死需要反思选卡的数值和强化卡选择」。

        ★★ t38：把**同一个** `build_death_attribution()` 产物一并返回
          （``(lessons, attribution)``），供 `_maybe_record_run_memory` 落库。
          为什么返回而不是在外面再算一次：**同一个判决只能有一个来源**。
          在外面重算会让"用于决策的死因"与"落库的死因"变成两次独立求值，
          一旦 route/hp 的时点不同就会漂移 —— 这正是本模块注释里反复强调的同源纪律。

        ⚠️ 全程 try/except：**复盘绝不能因为量化失败而中断**。
           terminal 屏会反复出现，这里抛错会让整局记忆丢失。
        """
        attribution_out: dict[str, Any] | None = None
        try:
            from .death_attribution import build_death_attribution

            # 死因：用 route（此刻还没被清空）+ outcome 推。
            # ★ 复用死亡归因模块，**不另写一套判据** —— 否则"死因"会有两个互相矛盾的说法。
            attribution = build_death_attribution(
                {
                    "outcome": outcome,
                    "floor": floor,
                    "route": route,
                    "hp": dict(hp_report or {}),
                }
            )
            # ★ 即使没有牌组（下面 early-return），死因也照样要落库 ——
            #   死因是**独立于牌组**的事实，"这局怎么死的"不该因为没有 deck 快照就丢。
            attribution_out = attribution
            if not deck:
                return [], attribution_out
            from .deck_gap_reflection import build_gap_lessons

            hp_max = self._safe_int((hp_report or {}).get("max_hp")) or 0
            hp_min = self._safe_int((hp_report or {}).get("min_hp")) or 0
            lessons = build_gap_lessons(
                {"cards": deck},
                planner=self._planner,
                floor=floor,
                current_hp=hp_min,
                max_hp=hp_max,
                death_kind=str(attribution.get("kind") or ""),
                outcome=outcome,
            )
            return lessons, attribution_out
        except Exception:
            self.logger.exception("[sts2_gap] 六维缺口反思失败（不影响复盘主链路）")
            return [], attribution_out

    @staticmethod
    def _run_outcome(screen: str, raw_state: dict[str, Any]) -> str:
        """这一局是通关还是失败。

        mod 的结算屏名恒为 `GAME_OVER`（通关与阵亡同一屏），胜负写在 `game_over.is_victory`
        （取自游戏的 RunHistory.Win）。只看屏名会把通关记成 defeat —— 通关后的剧情里玩家
        会被"击杀"，血量归零不代表输。读不到 `is_victory` 时才退回按屏名判断。
        """
        game_over = raw_state.get("game_over") if isinstance(raw_state.get("game_over"), dict) else {}
        if isinstance(game_over.get("is_victory"), bool):
            return "victory" if game_over["is_victory"] else "defeat"
        return "victory" if screen == "victory" else "defeat"

    def _maybe_record_run_memory(self, snapshot: dict[str, Any]) -> None:
        """run 结束（死亡/通关）时，把本局决策痕迹总结成教训/偏好写进猫娘记忆。

        terminal 屏会反复出现在快照里，因此只在 run_id 与上次写入不同时才总结（幂等）。
        """
        try:
            classification = snapshot.get("classification") if isinstance(snapshot.get("classification"), dict) else {}
            if classification.get("screen_class") != "terminal":
                return
            screen = str(snapshot.get("screen") or "").strip().lower()
            # 仅认这三种终点屏；其余 modal 等误报 terminal 的跳过
            if screen not in {"game_over", "victory", "defeat"}:
                return
            run_id = str(snapshot.get("run_id") or "")
            if not run_id or run_id == self._state.last_memory_run_id:
                return
            self._state.last_memory_run_id = run_id
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            outcome = self._run_outcome(screen, raw_state)
            run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
            character = str(snapshot.get("character") or raw_state.get("character_id") or run.get("character_id") or "")
            floor = int(snapshot.get("floor") or 0)
            act = int(snapshot.get("act") or 0)
            # 事实部分立刻落盘（同步、确定）；对照复盘的讲解稍后异步补（见 _explain_review）。
            hp_report = self._hp_loss_report()
            # 收尾最后一个房间，把路线图一起写进本局记录。
            self._close_route_room(reason="run_end")
            route = [dict(item) for item in self._state.route_log if isinstance(item, dict)]
            # ★★ 六维缺口反思（2026-10-19）：把**局末牌组**量化成"缺哪一维"的教训。
            #
            # 为什么在这里算而不在 memory store 里算：
            #   ① planner 实例在 service（`self._planner`，:99），store 拿不到
            #   ② 死因要从 route 推，而 route 正要被清空（下面 `self._state.route_log = []`）
            # ⇒ 两个输入都只在这一刻齐备。
            deck_snapshot = self._final_deck_snapshot(raw_state)
            gap_lessons, death_attribution = self._build_gap_lessons(
                deck_snapshot,
                route=route,
                floor=floor,
                hp_report=hp_report,
                outcome=outcome,
            )
            record = self._catgirl_memory.add_run(
                run_id,
                character=character,
                outcome=outcome,
                floor=floor,
                act=act,
                decision_memory=self._state.recent_decision_memory,
                hp_report=hp_report,
                route=route,
                deck=deck_snapshot,
                extra_lessons=gap_lessons,
            )
            # ★★★ t38/§22：把死因判决**落库**（此前只现算即丢 ⇒ 死因分布无法从产物复算）。
            #   ★ 必须在这里做，而不是在 add_run 里：`catgirl_memory.py` 不在本任务 scope，
            #     且 add_run 内部 `save()` 后返回的是 `dict(run)` **副本** ⇒ 改返回值不会落盘。
            #     所以走「attach 落库」这条路：写进 `_runs` 里那条真记录，再存一次。
            self._persist_death_attribution(record, death_attribution)
            self._state.route_log = []
            self._state.route_room_key = ""
            self._state.route_start = {}
            self._state.route_last = {}
            self.logger.info(
                "[sts2_memory] recorded run end run_id=%s outcome=%s floor=%s character=%s 总损血=%s(%s次) 最低血=%s lesson_count=%s pref_count=%s",
                run_id,
                outcome,
                floor,
                character,
                hp_report.get("total_lost"),
                hp_report.get("loss_events"),
                hp_report.get("min_hp"),
                len(record.get("lessons", [])),
                len(record.get("preferences", [])),
            )
            # 对照复盘：玩家亲手打的步里，哪几步和推荐差得最多。纯自动玩的局没有可对照的步。
            review = self._choice_review.summary()
            self._choice_review.steps = []
            self._choice_review.drop_pending()
            if review["compared"]:
                self._catgirl_memory.attach_review(run_id, review)
                self.logger.info(
                    "[sts2_review] 本局玩家亲手打了 %s 步可对照，%s 步与推荐相同，挑出 %s 步讲",
                    review["compared"], review["agreed"], len(review["steps"]),
                )
            self._write_history_view()
            # 复盘开关关掉、或没有差得多的步：只留结构化记录，不请模型讲。
            if not review["steps"] or not getattr(self._state, "run_reflection_enabled", True):
                return
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._explain_review(dict(record), review, generation=self._review_generation))
                task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            except RuntimeError:
                pass
        except Exception as exc:
            self.logger.info("[sts2_memory] record run end failed (non-fatal): %s", exc)

    def _persist_death_attribution(
        self, record: dict[str, Any], attribution: dict[str, Any] | None
    ) -> None:
        """把死因判决写进记忆库里那条 run 记录，并落盘（t38）。

        ★ 为什么要"attach 再 save"而不是给 `add_run` 加参数：
          `catgirl_memory.py` **不在 t38 的 scope**（契约明确划出），
          且 `add_run` 在内部 `save()` **之后**返回 `dict(run)`（**副本**）
          ⇒ 改返回值不会进文件。所以只能写 `_runs` 里的**同一个对象**再存一次。

        ★ 无 attribution 时**什么都不写**：让该局保持"**没有这个字段**"。
          这正是契约要求的向后兼容语义 —— 旧数据（t38 之前 50 局）是 `absent`，
          与"算出来是 unknown"**不是一回事**，绝不能补一个 `kind="unknown"` 去糊上
          （那会把"系统没落库"伪装成"这局证据不足"，是 t30 里 zero vs null 的同款错误）。

        ⚠️ 全程吞异常：落库失败**不得**影响记忆主链路（对照复盘在它后面）。
        """
        if not isinstance(attribution, dict):
            return
        try:
            from .death_attribution import DEATH_RECORD_KEY, attribution_to_record

            payload = attribution_to_record(attribution)
            run_id = str((record or {}).get("run_id") or "")
            target = None
            for item in self._catgirl_memory.runs:
                if isinstance(item, dict) and str(item.get("run_id") or "") == run_id:
                    target = item
                    break
            if target is None:
                return
            target[DEATH_RECORD_KEY] = payload
            self._catgirl_memory.save()
            self.logger.info(
                "[sts2_death] 死因已落库 run_id=%s kind=%s 依据(战斗房=%s 损血=%s 最重房=%s share=%s 群怪share=%s)",
                run_id,
                payload.get("kind"),
                payload.get("combat_rooms"),
                payload.get("combat_lost"),
                payload.get("worst_room"),
                payload.get("worst_share"),
                payload.get("multi_enemy_share"),
            )
        except Exception as exc:
            self.logger.info("[sts2_death] 死因落库失败 (non-fatal): %s", exc)

    def _write_history_view(self) -> None:
        """把历史投影成插件 static 目录下的 JSON，供前端"自动游玩历史"页读取。

        前端页面是插件服务器按静态文件发的，没有直读插件内存的通道，所以走"落一份视图快照"。
        权威数据仍在 catgirl_memory.json；这里失败不影响任何决策。
        """
        try:
            path = write_history_view(self._catgirl_memory.runs)
            if path:
                self.logger.info("[sts2_history] 历史快照已更新: %s", path)
        except Exception as exc:
            self.logger.info("[sts2_history] 写历史快照失败 (non-fatal): %s", exc)

    def get_history_payload(self) -> dict[str, Any]:
        """历史页/入口用的历史数据（直接从猫娘记忆投影，不读快照文件）。"""
        try:
            from .history_view import build_history_payload

            return build_history_payload(self._catgirl_memory.runs)
        except Exception:
            return {"summary": {}, "runs": []}

    async def _explain_review(
        self, record: dict[str, Any], review: dict[str, Any], *, generation: int | None = None,
    ) -> None:
        """局末复盘：把玩家的选择与推荐差得最多的几步交给**本体**（同一模型 + 本体人设）讲讲为什么。

        讲解贴回这一局的记录（历史页可见），并推给本体 NEKO 让它用自己的口吻说。它**不**进教训、
        不影响任何决策，也不暂停自动游玩。
        """
        if generation is None:
            generation = self._review_generation
        if not self._state.run_reflection_enabled or generation != self._review_generation:
            return
        try:
            digest = review_digest(record, review)
            self.logger.info("[sts2_review] digest=%s", digest.replace("\n", " ‖ "))
            if not getattr(self._catgirl_llm, "available", False):
                self.logger.info("[sts2_review] 主系统 LLM 不可用，只留对照记录")
                return
            text = await self._catgirl_llm.generate(summary_text=digest, summary_kind="run_review", payload={})
            # A disabled/re-enabled switch must not resurrect an older queued review.
            if not self._state.run_reflection_enabled or generation != self._review_generation:
                return
            if not text:
                self.logger.info("[sts2_review] 讲解未产出，只留对照记录")
                return
            self._catgirl_memory.attach_review(str(record.get("run_id") or ""), {**review, "explanation": text})
            self.logger.info("[sts2_review] 讲解=%s", text)
            self._write_history_view()
            head = (
                f"这一局结束了（{record.get('outcome') or '?'}，第{record.get('floor') or 0}层）。"
                f"玩家亲手打的步里和你的推荐差得最多的几步，你的看法是：{text}"
                "用你自己的口吻和玩家聊聊这几步，两三句就够；是讨论不是批评。"
            )
            self._push_proactive_to_host(head, kind="run_review", priority=7)
        except Exception as exc:
            self.logger.info("[sts2_review] failed (non-fatal): %s", exc)

    def _plugin_is_driving(self, snapshot: dict[str, Any]) -> bool:
        """这一屏是不是插件自己在走（自动玩在跑、没停手、也不在"听安排"的等待清单里）。"""
        return (
            self._state.autoplay_state == "running"
            and not self._state.standby
            and not self._should_hold_for_orders(snapshot)
        )

    async def review_tick(self, client: Any, snapshot: dict[str, Any], operation: Any, situation_summary: Any) -> None:
        """对照复盘的一拍：先认领模组报来的选择，再记下这一刻的推荐。任何失败都不影响 tick。

        只在**玩家自己打**的时候记推荐（陪玩、停手、"听安排"等你发话、自动玩没在跑）；插件自己走的步
        不进对照。本局角色下载了策略网络模型时推荐来自网络，否则来自这一拍规划器算出的那一步。
        """
        if self._choice_review_busy:
            # 另一路 tick 正在做这一拍（它在等 mod 或推理旁路）：两路各读一遍 /choices 会把同一条选择
            # 认领两次，所以这一路直接让开。
            return
        self._choice_review_busy = True
        try:
            run_id = str(snapshot.get("run_id") or "")
            getter = getattr(client, "get_player_choices", None)
            if not run_id or getter is None or time() < self._choice_review_retry_at:
                return
            review = self._choice_review
            review.begin_run(run_id)
            driving = self._plugin_is_driving(snapshot)
            if driving and review.pending is None and not review.backlog:
                # 插件自己在走、也没有等回答的推荐：这一拍没有可认领的，不必问 mod。
                return
            previous = review.since
            try:
                data = await getter(previous or 0)
            except Exception as exc:
                # 只有"对端没有这条路由"（旧版模组）才长时间不再问；偶发的超时、断连隔几秒就再试，
                # 否则玩家这几分钟里的步全丢。
                missing = getattr(exc, "code", "") == "not_found"
                self._choice_review_retry_at = time() + (300.0 if missing else 5.0)
                self.logger.info(
                    "[sts2_review] 读不到玩家的选择（%s）：%s: %s",
                    "模组没有 /choices，5 分钟后再试" if missing else "5 秒后再试", type(exc).__name__, exc,
                )
                return
            choices = data.get("choices") if isinstance(data, dict) and isinstance(data.get("choices"), list) else []
            latest = data.get("latest_seq") if isinstance(data, dict) else None
            first_contact = previous is None
            if isinstance(latest, int):
                if previous is not None and latest < previous:
                    first_contact = True  # 模组重启过，序号从头来
                review.since = latest
            if first_contact:
                review.seen_seq = latest if isinstance(latest, int) else 0
            else:
                # 只认没认领过的（序号比认领过的都大）：同一条选择不回答两条推荐。
                fresh = [c for c in choices if isinstance(c, dict) and int(c.get("seq") or 0) > review.seen_seq]
                review.seen_seq = max([review.seen_seq, *(int(c.get("seq") or 0) for c in fresh)])
                for step in review.resolve(fresh, last_plugin_action_at=float(self._state.last_action_at or 0.0)):
                    self.logger.info(
                        "[sts2_review] 第%s层 %s：玩家选「%s」，推荐「%s」%s",
                        step.get("floor"), step.get("decision"), step.get("chosen"), step.get("recommended"),
                        "（相同）" if step.get("same") else "（不同%s）" % (
                            "，概率差 %.2f" % step["gap"] if step.get("source") == SOURCE_NETWORK else ""
                        ),
                    )
            if driving:
                review.drop_pending()
                return
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            summary_context = snapshot.get("summary_context") if isinstance(snapshot.get("summary_context"), dict) else {}
            payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
            screen = str(snapshot.get("screen") or "")

            def context() -> dict[str, Any]:
                return {
                    "floor": snapshot.get("floor"),
                    "act": snapshot.get("act"),
                    "room": self._room_identity(screen, raw_state),
                    "situation": str(situation_summary.get("text") or "")[:160] if isinstance(situation_summary, dict) else "",
                    "hp": payload.get("current_hp"),
                    "max_hp": payload.get("max_hp"),
                    "names": collect_names(raw_state),
                }

            character = str(snapshot.get("character") or "").upper()
            advisor = self._policy_advisor
            if character and advisor.enabled and advisor.assets.runtime_ready() and advisor.assets.model_ready(character):
                scored = await advisor.recommend(client, character=character, known_id=review.pending_key())
                if scored and scored.get("unchanged"):
                    return
                if scored:
                    review.capture_network(scored, context=context())
                    return
            if isinstance(operation, dict) and operation.get("action_type"):
                kwargs = operation.get("kwargs") if isinstance(operation.get("kwargs"), dict) else {}
                key = f"planner|{self._state.snapshot_signature}|{screen}|{operation.get('action_type')}|{sorted(kwargs.items())}"
                review.capture_planner(operation, raw_state, key=key, context=context())
        except Exception as exc:
            self.logger.info("[sts2_review] tick failed (non-fatal): %s", exc)
        finally:
            self._choice_review_busy = False
