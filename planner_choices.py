from __future__ import annotations

import re
from typing import Any

from .planner_common import (
    _ARCHETYPE_DRAW_RE,
    _REST_HEAL_TOKENS,
    _REST_SMITH_TOKENS,
    REST_HEAL_HP_RATIO,
)


class PlannerChoicesMixin:
    """Internal responsibility mixin; initialized and composed by STS2HeuristicPlanner."""

    def _event_screen_signature(self, summary_context: dict[str, Any]) -> str:
        """事件屏签名：用于判断"是否还是同一个事件屏"。

        刻意**不含** hp/gold —— 它们会在同一屏内因已执行的选项而变化，
        若纳入签名会让"已选次数"在每次选完后重置，记忆就失效了。
        只取"哪些选项"（文案+下标），这才是一屏的身份。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        parts: list[str] = []
        for index, option in enumerate(options):
            if not isinstance(option, dict):
                continue
            option_index = self._safe_int(option.get("index"))
            if option_index is None:
                option_index = index
            parts.append(f"{option_index}:{self._event_option_text(option)[:40]}")
        return "|".join(parts)

    def _note_event_option_chosen(self, summary_context: dict[str, Any], option_index: int) -> None:
        """记录"某个事件选项**已被真正派发**过几次"。

        ★修复缺陷 A3：`_rank_event_options` 原本是**无状态**的 —— 只读当前 hp/gold，
        完全不知道"这个选项我已经选过"。于是在**可重复选择**的事件上（每次选完屏幕不变、
        选项还在），它每轮都算出同一个最优解 ⇒ **无限重复**，直到 HP 变 1。

        与 `note_selection_option_pressed` 同范式：**只在真正派发成功后**调用
        （由 service 在动作成功后回调），不在 `plan()` 里累加 —— 因为 `plan()` 每 tick
        都被调用（约 1 次/秒），而实际派发约 2.5 秒一次，在 plan 里累加会凭空放大计数。
        """
        signature = self._event_screen_signature(summary_context)
        if signature != self._event_memory_key:
            self._event_memory_key = signature
            self._event_choice_counts = {}
        self._event_choice_counts[option_index] = self._event_choice_counts.get(option_index, 0) + 1

    def _event_repeat_penalty(self, summary_context: dict[str, Any], option_index: int) -> float:
        """同一事件屏上"该选项已选过 N 次"的**递减惩罚**。

        ★★ 这里有一个**必须避免的陷阱**（我在实现时踩到并已修正）：

        若对所有选项一视同仁地累加惩罚，会把**无害选项**（如"离开"）也一起压下去 ——
        实测后果：反复选"离开"4 次后，`离开` 被压到 `扣上限` 之下，**最优解反而翻回了扣上限的选项**
        （复现：第 1 轮 `[1,0]` 正确，第 4 轮 `[0,1]` 反转）。这会让修复**比不修更糟**。

        正确做法：**只惩罚"有害选项"的重复**。判据是该选项本身是否已被评分为负 ——
        "离开"这类中性选项（分数 0）**永不惩罚**，因此不会再出现"好选项被累积惩罚压过头"。

        于是语义变成：**同一屏上反复选一个"有害"选项，惩罚递增；选无害选项不累积。**
        这既不改变一次性事件的正常决策（第一次选零惩罚），又能在可重复选择的事件上止损。
        """
        signature = self._event_screen_signature(summary_context)
        if signature != self._event_memory_key:
            return 0.0
        times = self._event_choice_counts.get(option_index, 0)
        if times <= 0:
            return 0.0
        return float(self._EVENT_REPEAT_PENALTY_PER_TIME * times)

    def _preferred_event_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        # 1) 硬覆盖：玩家用自然语言钉死了某个选项，优先级高于任何评分。
        pinned = self._preferred_option_index(preferences)
        if pinned is not None:
            return pinned

        # 2) 纯启发式打分（事件房已无 LLM 评分融合，见 _rank_event_options）。
        ranked = self._rank_event_options(summary_context, strategy_context)
        return ranked[0] if ranked else None

    def _rank_event_options(
        self,
        summary_context: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> list[int]:
        """事件选项按启发式分数**从好到坏**排序（返回选项下标）。

        与 ``_preferred_event_option`` 的评分口径完全一致，唯一差别是不取最高分而是保留整条顺序，
        第 0 个就是原来的最优。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        guidance_override = self._guidance_override_from_payload(summary_context)

        hei_scores: dict[int, int] = {}
        indexed: list[tuple[int, dict[str, Any]]] = []
        # ★★ D11（t28）：本屏禁用集（按 `screen|floor|event_id` 隔离）——只影响**本屏**。
        locked_key = self._event_locked_set_key(summary_context)
        if locked_key != self._event_locked_set_key_cached:
            self._event_locked_set_cached = set()
            self._event_locked_set_key_cached = locked_key
        banned = self._event_locked_set_cached
        locked_by_field: list[int] = []
        seen_indices: set[int] = set()
        for index, option in enumerate(event_options):
            if not isinstance(option, dict):
                continue
            option_index = self._safe_int(option.get("index"))
            if option_index is None:
                option_index = index
            seen_indices.add(option_index)
            # ★ 预防：字段直接说被锁 ⇒ 记入禁用集（跨 tick 保持，见 `note_event_option_locked`）。
            if self._event_option_is_locked(option):
                locked_by_field.append(option_index)
                banned.add(option_index)
            # ★★ 关键修正（t28 自测发现，实机 01:19:16 暴露 4 次误报）：
            #   `/state` 的 `is_locked` 是**权威且更新**的数据源；禁用集只是**错误驱动的补充**。
            #   ⇒ 若本 tick 的数据**明确**说明该 index 未被锁（字段为 False），
            #     则禁用集里的旧记录**已过期**，必须移除 —— 否则会误禁一个合法选项。
            #
            #   为什么必须这样（实机证据）：ENDLESS_CONVEYOR 是**多页事件**
            #     （`text_key` 含 `pages.ALL.options.LOCKED` /
            #       `pages.GRAB_SOMETHING_OFF_THE_BELT.options.LEAVE`），
            #   换页后 `screen|floor|event_id` **三者都不变** ⇒ 隔离键**无法区分页**
            #   ⇒ 上一页 index=0 被锁的禁用记录**串到下一页**，而下一页 index=0 合法
            #   ⇒ 实测 01:19:16 连打 4 条「本屏所有选项均在禁用集内 [0]」**误报**。
            #   ★ 修法不是继续加大隔离键（页 id 从 `/state` 拿不到），
            #     而是**让权威数据压制补充记录** —— 数据说没锁，就不该再禁。
            elif option.get("is_locked") is False:
                banned.discard(option_index)
            # ★ 运行时禁用集里的项**不参与打分**（但仍返回分数，供日志显示为何被排除）。
            hei_scores[option_index] = self._score_event_option(option, hp_ratio, gold)
            indexed.append((option_index, option))
        # ★ 已不在本屏出现的 index ⇒ 禁用记录无意义，清掉（防止跨屏累积）。
        banned &= seen_indices
        self._event_locked_set_cached = banned
        hei_norm = self._normalize_percentile(hei_scores)

        # ★★ 让【跨局复盘教训】作用于事件选择（用户点名的缺陷 A/B 所在屏）。
        #   此前事件屏只读 `guidance_override`（payload 里的临时指示）与启发分，
        #   **完全不读跨局教训** ⇒ 复盘学到"别再吃永久掉上限的选项"也无法阻止重犯。
        #   `strategy_directives` 在 `strategy_context`（兄弟键），故由调用方传入。
        #   ★ 教训的**唯一真相来源**是 `_event_final_scores` 内部（打分核心）；
        #     这里只为日志再取一次信号，**不参与打分**，避免出现第二个真相来源。
        lesson_signals = self._reward_lesson_signals(strategy_context)

        final_scores = self._event_final_scores(
            summary_context,
            strategy_context,
            hei_scores=hei_scores,
            hei_norm=hei_norm,
            indexed=indexed,
            guidance_override=guidance_override,
        )
        # 与 _preferred_event_option 同口径：分数高的在前，同分时下标小的在前。
        scored = sorted(final_scores.items(), key=lambda item: (-item[1], item[0]))
        # ★★ D11（t28）：把**已确认不可选**的下标排到**最后**。
        #
        #   ★ 为什么是"排到最后"而不是"直接删除"：
        #     若某屏**所有**选项都被判为锁定（数据异常 / 字段误读），直接删除会得到**空候选**
        #     ⇒ `_preferred_event_option` 返回 None ⇒ 插件**不发任何动作** ⇒ 反而制造新的卡死。
        #     排到最后可保证：只要还有任一未锁项就选它；全都锁时仍有一个（最差的）可发
        #     —— 此时"发出去被拒"至少留下**可观测**痕迹，而不是静默无动作。
        #   ★ 这是安全属性：**绝不因本修复把候选集变空**。
        banned_order = [ix for ix, _ in scored if ix in banned]
        free_order = [ix for ix, _ in scored if ix not in banned]
        ordered = free_order + banned_order
        if banned_order and not free_order:
            if self._logger is not None:
                try:
                    self._logger.warning(
                        "[sts2_d11] 本屏**所有**选项均在禁用集内 %s ⇒ 无未锁可选，"
                        "仍发出最差分项以避免静默无动作（请人工介入）" % (sorted(banned),)
                    )
                except Exception:
                    pass
        self._log_event_route(hei_scores, scored, ordered, event_options, hp_ratio, gold, lesson_signals)
        return ordered

    def _event_final_scores(
        self,
        summary_context: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
        *,
        hei_scores: dict[int, int] | None = None,
        hei_norm: dict[int, float] | None = None,
        indexed: list[tuple[int, dict[str, Any]]] | None = None,
        guidance_override: dict[str, Any] | None = None,
    ) -> dict[int, float]:
        """事件选项的**最终分**（排序前的唯一真相来源）。

        ★ 抽成独立函数的原因：把管线（启发分 → 归一化 → 教训 → 重复惩罚 → 最终分）
          集中在一处，`_rank_event_options` 与测试**共用同一实现**，
          避免"测试复刻了一份打分逻辑"导致测试通过而线上行为不同。

        教训（`strategy_context`）的三处作用：
          - `self_damage` 教训 ⇒ **永久扣生命上限**的选项 −45，普通掉血 −18（缺陷 A）
          - 诅咒教训 ⇒ 拿诅咒/塞牌的选项 −30（缺陷 B）
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        event_options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        if guidance_override is None:
            guidance_override = self._guidance_override_from_payload(summary_context)
        if indexed is None:
            indexed = []
            for index, option in enumerate(event_options):
                if not isinstance(option, dict):
                    continue
                option_index = self._safe_int(option.get("index"))
                indexed.append((option_index if option_index is not None else index, option))
        if hei_scores is None:
            hei_scores = {
                option_index: self._score_event_option(option, hp_ratio, gold)
                for option_index, option in indexed
            }
        if hei_norm is None:
            hei_norm = self._normalize_percentile(hei_scores)

        lesson_signals = self._reward_lesson_signals(strategy_context)
        lesson_text = self._lesson_raw_text(strategy_context)
        wants_curse_avoid = ("curse" in lesson_signals) or any(
            token in lesson_text for token in ("诅咒", "curse")
        )

        out: dict[int, float] = {}
        for option_index, option in indexed:
            final = hei_norm.get(option_index, 50.0)
            text = self._event_option_text(option)
            if guidance_override.get("prefer_defense") and self._option_loses_hp(text):
                final -= 15.0
            if guidance_override.get("prefer_attack") and any(tok in text for tok in ["relic", "遗物", "gold", "金币", "card", "卡牌"]):
                final += 10.0
            # ★ 缺陷 A（用户点名）：**永久扣生命上限**的选项，在教训提到过血上限/自损时重罚。
            #   实际风险是「可重复选择 ⇒ 被反复选到把上限烧到很低」，故罚分要压过启发分。
            if lesson_signals.get("self_damage"):
                if self._option_loses_max_hp(text):
                    final -= 45.0
                elif self._option_loses_hp(text):
                    final -= 18.0
            # ★ 缺陷 B（用户点名）：教训里出现过"诅咒"相关教训时，拿诅咒/塞牌的选项降权。
            if wants_curse_avoid and any(
                tok in text for tok in ("诅咒", "curse", "放入一张", "塞入", "变为")
            ):
                final -= 30.0
            # ★缺陷 A3：同一屏上重复选"**有害**"选项要递减，否则可重复选择的事件会被选到死。
            #   ★只对原始评分为负的选项施加 —— 否则会把"离开"这类中性选项一起压下去，
            #   导致反复选"离开"后惩罚累积、最优解反而翻回有害选项（实测过这个反转）。
            if hei_scores.get(option_index, 0) < 0:
                final -= self._event_repeat_penalty(summary_context, option_index)
            out[option_index] = final
        return out

    def _log_event_route(
        self,
        hei_scores: dict[int, int],
        scored: list[tuple[float, int]],
        ordered: list[int],
        event_options: list[Any],
        hp_ratio: float,
        gold: int,
        lesson_signals: dict[str, int] | None = None,
    ) -> None:
        """把事件屏决策写进日志（与 `[sts2_map_route]` 同风格）。

        ★为什么要加：此前**事件屏决策完全不记日志** —— 地图有 `[sts2_map_route]`、
        战斗有 `[sts2_combat_turn_gate]`，唯独事件屏什么都没有。
        这让我无法实机验证 A1/A3/C 的任何修复（只能靠单测），
        也让玩家遇到"反复扣上限"时拿不到任何可诊断的痕迹。

        去重键取"原始分 + 最终序"，同一屏静止时不刷屏（与 `_last_map_route_log_key` 一致）。
        """
        if self._logger is None:
            return
        key = "|".join("%d:%d:%.2f" % (index, hei_scores.get(index, 0), final)
                       for final, index in scored)
        key += "|%s" % sorted((lesson_signals or {}).items())
        if key == self._last_event_route_log_key:
            return
        self._last_event_route_log_key = key
        texts = {}
        for index, option in enumerate(event_options):
            if isinstance(option, dict):
                option_index = self._safe_int(option.get("index"))
                texts[option_index if option_index is not None else index] = self._event_option_text(option)
        # 截断长度取 60：实测 18 时会把"失去6点生命上限"这类**关键区别**截掉
        # （只能看到"失去[red]6[/red]点生"），无法从日志判断是掉血还是永久扣上限 ——
        # 而那恰恰是缺陷 A 的判定核心。日志是实机验证的唯一证据来源，必须能看清。
        detail = " / ".join("%d:%s" % (index, texts.get(index, "")[:60]) for index in ordered)
        # ★ 标出哪些选项是"永久扣生命上限"（缺陷 A 的判定核心），便于实机核对。
        maxhp_flags = [index for index in ordered if self._option_loses_max_hp(texts.get(index, ""))]
        try:
            self._logger.info(
                "[sts2_event_route] 金币=%s 血比=%.2f 教训=%s 启发分=%s 重复惩罚=%s 扣上限选项=%s -> 选 option_index=%s | %s",
                gold,
                hp_ratio,
                lesson_signals or {},
                {index: hei_scores.get(index, 0) for index in ordered},
                {index: round(self._event_repeat_penalty({"payload": {
                    "event_options": event_options}}, index), 1) for index in ordered},
                maxhp_flags,
                ordered[0] if ordered else None,
                detail,
            )
        except Exception:
            # 日志绝不能影响决策路径。
            pass

    @staticmethod
    def _normalize_percentile(scores: dict[int, int]) -> dict[int, float]:
        """把一组整数分 min-max 归一化到 0-100（单值/全等分 → 50），供与 LLM 0-100 分融合。"""
        if not scores:
            return {}
        values = [float(v) for v in scores.values()]
        lo, hi = min(values), max(values)
        if hi <= lo:
            return {k: 50.0 for k in scores}
        return {k: 100.0 * (float(v) - lo) / (hi - lo) for k, v in scores.items()}

    @staticmethod
    def _strip_rich_text(text: str) -> str:
        """剥掉游戏文案里的 BBCode 式富文本标签，**再**做语义匹配。

        ★★这是实机抓出来的严重缺陷（A2 的隐性失效）：
        游戏下发的选项文案**带标签且标签正好插在关键词与数字之间**：
            ``失去[red]8[/red]点生命。获得1瓶随机药水。``
        而 `_option_loses_hp` 的正则是 ``失去\\s*\\d*\\s*点?\\s*(生命|血量)`` ——
        ``失去`` 与 ``8`` 之间夹着 ``[red]`` ⇒ **匹配失败** ⇒ 掉血惩罚完全丢失。

        实机后果（EVENT#3，血比 0.73）：
        ```
        启发分={0: 10, 1: 10} -> 选 0 | 0:获得藏宝图。 / 1:失去[red]8[/red]点生命。…
        ```
        两个选项**同分**（本该是 −8 vs 10 的明确差距），最后靠 index 平手规则才选中安全项
        —— **是运气，不是启发式在起作用**。

        同类失效还会波及：`获得[green]2[/green]点最大生命值`、
        `支付[red]150[/red][gold]金币[/gold]`（金币判定用的是裸子串，侥幸没坏）。
        ⇒ 所有事件文案匹配**统一先过这一层**，而不是逐个正则去容忍标签。
        """
        t = str(text or "")
        # 去掉 [xxx] / [/xxx]；游戏里也有 [img]res://...[/img] 这类跨段标签。
        return re.sub(r"\[/?[A-Za-z][^\]]*\]", "", t)

    @staticmethod
    def _option_loses_max_hp(text: str) -> bool:
        """事件选项是否**永久削减生命上限**（`max_hp`）。

        与 `_option_loses_hp`（一次性掉血、可被治疗回来）区分开：上限损失**不可恢复**，
        会永久降低整局容错空间，因此必须是**更高量级**的惩罚。

        实机证据：三个 run 里 `PVV8N7MJ4VGY` 的 `max_hp` 从 80 被扣到 54（−26）且此后不再恢复。

        匹配要点（中英双语，且不能被"失去生命"先命中而漏掉）：
        * 中文：「失去 5 点生命上限」「最大生命 -5」
        * 英文：`lose 5 max hp` / `maximum hp -5`（容忍点号与空格）

        ★★必须判定**方向**（实机发现的严重缺陷，我第一版漏了）：
        只匹配"提到了最大生命"是**错的** —— 实机真实事件里出现过
        「获得 11 点最大生命值。」（**增益**），旧实现把它也判成损失并罚 −60，
        于是脚本**主动放弃了 +11 最大生命**，与修复意图完全相反。
        ⇒ 必须同时要求"损失语义"与"没有增益语义"：
          * 有 `失去`/`lose`/`-` 等损失词 ⇒ 是损失
          * 有 `获得`/`gain`/`+`/`提升`/`增加` 等增益词 ⇒ **不是**损失
        「最大生命 -5」用负号表达，故负号也算损失信号。

        ★富文本标签必须先剥掉（见 `_strip_rich_text`）。
        """
        t = PlannerChoicesMixin._strip_rich_text(text).lower()
        # 是否"提到了最大生命"这个概念
        mentions = (
            re.search(r"(生命|血量|最大)\s*(值)?\s*(上限|最大值)", t) is not None
            or re.search(r"最大生命", t) is not None
            or re.search(r"(max|maximum)\s*\.?\s*(hp|health)", t) is not None
        )
        if not mentions:
            return False
        # ★增益语义优先排除（实机：「获得 11 点最大生命值。」）
        #   「提高」「变成」等也要算增益 —— 例如「你的生命上限提高了」。
        # 损失语义：扣/失去/减少/负号
        loss = re.search(r"(失去|扣|减少|降低|lose|reduce|-\s*\d)", t) is not None
        # ★增益与损失都有时，**以损失为准**：`"Lose 5 Max HP, gain a relic"` 必须判为损失
        #   （先扣永久上限再给遗物，净收益远不足以抵消）。
#   注：此处**不需要**额外判断「有增益就放行」——
        #   那种情况下 `loss` 本身已是 False，`return loss` 结果完全相同
#   （实测确认「有增益就放行」与 return loss 等价，属冗余代码已删除；
        #   故本函数只计算 loss，不再保留 gain 变量。）
        return loss

    @staticmethod
    def _option_loses_hp(text: str) -> bool:
        """事件选项是否掉血（健壮匹配，容忍 "lose 7 HP"/"Lose HP"/中文表述）。

        **不含**生命上限损失 —— 那是 `_option_loses_max_hp` 的语义，量级不同。

        历史缺陷（本轮修正）：原实现用字面量 ``"失去生命"`` 匹配中文，于是
        ``"失去 10 点生命"``（中间有数字与量词）**匹配不到** —— 实测确认该缺陷在
        ``HEAD`` 版本即存在，非本次引入。中文里"失去 N 点生命"是常见形态，故改用正则容忍
        数字与量词：``失去\\s*\\d*\\s*点?\\s*(生命|血量)``。

        ★★富文本标签（实机二次修正）：即便容忍了数字，游戏真实文案是
        ``失去[red]8[/red]点生命`` —— 标签夹在 **动词与数字之间**，正则仍然失败。
        ⇒ 统一在 `_strip_rich_text` 里先剥标签（见该方法说明，附实机后果）。
        """
        t = PlannerChoicesMixin._strip_rich_text(text).lower()
        # 上限损失走专门路径，避免被这里的"失去生命"宽泛匹配吞掉而失去区分度。
        if PlannerChoicesMixin._option_loses_max_hp(t):
            return False
        return (
            "失去生命" in t                      # 无数字的最短形态，保留以兼容旧行为
            or re.search(r"失去\s*\d*\s*点?\s*(生命|血量)", t) is not None
            or "掉血" in t
            or "lose hp" in t
            or "lose health" in t
            or re.search(r"lose\s+\d+\s*(hp|health)", t) is not None
        )

    def _preferred_option_index(self, preferences: dict[str, Any]) -> int | None:
        record = preferences.get("record") if isinstance(preferences.get("record"), dict) else None
        if isinstance(record, dict):
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for item in records:
            if not isinstance(item, dict):
                continue
            value = item.get("value") if isinstance(item.get("value"), dict) else {}
            preferred = self._safe_int(value.get("preferred_option_index"))
            if preferred is not None:
                return preferred
        return None

    def _preferred_reward_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        reward_cards = self._reward_candidates(summary_context)
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        # ★ deck 在两个位置出现：新口径在 `payload` 顶层，旧口径在 `payload["deck"]`。
        #   只读后者会漏掉实机形状 ⇒ 流派永远推断为空。
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        if not deck.get("cards") and isinstance(payload.get("run"), dict):
            run_deck = payload["run"].get("deck")
            if isinstance(run_deck, list):
                deck = {"cards": run_deck}
            elif isinstance(run_deck, dict):
                deck = run_deck
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        # ★★ 缺陷 E 修复：外部 `deck_policy` 恒空（全仓库无人产出该记录）⇒
        #   `_score_reward_card` 里所有流派加权（archetype_bias +20 等）永不触发。
        #   这里用**卡组自身**推断的流派补位，让「卡牌流派的抉择」真正存在。
        #   外部指令若存在则合并（不覆盖），保持向前兼容。
        internal_policy = self._archetype_policy_from_deck(deck, archetype_tags)
        if internal_policy:
            merged_bias = set(deck_policy.get("archetype_bias") or set()) | set(internal_policy.get("archetype_bias") or set())
            deck_policy = dict(deck_policy)
            deck_policy["archetype_bias"] = merged_bias
            self._debug(
                "[sts2_archetype] 卡组流派=%s -> archetype_bias=%s"
                % (archetype_tags, sorted(merged_bias))
            )
        guidance_override = self._guidance_override_from_payload(summary_context)
        # ★★ 让【跨局复盘教训】真正作用于选卡质量。
        #   此前教训只经 `_apply_run_lessons` 进入通用排序（`ordered_candidates` 的扰动候选池），
        #   **完全不参与 `_score_reward_card`** ⇒ 复盘无论学到什么都不改变「选哪张卡」。
        #   这是用户点名的「强化/删除哪张卡片」缺口的直接原因。
        #   ★ 注意：`strategy_directives` 在 `strategy_context` 里，**不在** `summary_context` 里
        #     （`service.py:549-557` 把两者作为兄弟键放进 snapshot）
        #     ⇒ 必须显式传入，否则运行时读到的是不存在的键（恒空 = 白改）。
        lesson_signals = self._reward_lesson_signals(strategy_context)
        # ★ 留痕：把这次评估的输入与打分记下来，供 `_log_reward_route` 输出。
        #   （`_debug()` 是空函数，不留痕线上就无法核对选卡依据。）
        self._last_reward_archetype_tags = list(archetype_tags)
        self._last_reward_bias = set(deck_policy.get("archetype_bias") or set())
        self._last_reward_lesson_signals = dict(lesson_signals)
        self._last_reward_scored = []
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(reward_cards if isinstance(reward_cards, list) else []):
            if not isinstance(card, dict):
                continue
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            score += self._reward_lesson_bonus(card, lesson_signals, archetype_tags)
            if guidance_override.get("prefer_defense"):
                if self._card_block(card) > 0:
                    score += 18
                if self._reward_dynamic_value(card, "HpLoss") > 0:
                    score -= 12
            if guidance_override.get("prefer_attack"):
                if self._card_damage(card) > 0:
                    score += 14
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            self._last_reward_scored.append((card_index, round(score, 2)))
            candidate = (-score, card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None and best_candidate[0] < 0:
            live = getattr(self, "_livestream", None)
            if live is not None and live.enabled and live.profile != "focused" and not records and not guidance_override:
                return live.pick_ranked(self._last_reward_scored, key=reward_cards)
            return best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _lesson_raw_text(self, source: dict[str, Any] | None) -> str:
        """取跨局教训的**原始文本**（小写拼接），供粗粒度关键词判据使用。

        与 `_reward_lesson_signals` 的分工：那个产出**方向信号**（选卡/删牌用），
        这个产出**原文**（事件屏的"诅咒"类判据用，不需要方向极性）。
        """
        source = source if isinstance(source, dict) else {}
        directives = source.get("strategy_directives")
        if not isinstance(directives, dict):
            directives = source if isinstance(source.get("avoid"), list) else {}
        lessons = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        return " ".join(str(item) for item in lessons if str(item).strip()).lower()

    def _reward_lesson_signals(self, source: dict[str, Any] | None) -> dict[str, int]:
        """从跨局教训里抽出「该多拿什么卡 / 该少拿什么卡」的方向信号。

        返回 {方向: 净权重}，正=多拿、负=少拿。教训为空 ⇒ {}（调用方加权为 0，行为不变）。

        ★ 入参可以是 `strategy_context`（含 `strategy_directives`，运行时形状）
          或直接是含 `avoid` 的 directives 字典 —— 两者都接受，
          避免调用方传错层导致**恒空**（本轮踩过一次：传 summary_context 恒返回 {}）。
        """
        source = source if isinstance(source, dict) else {}
        directives = source.get("strategy_directives")
        if not isinstance(directives, dict):
            # 兼容：直接把 directives 传进来（含顶层 avoid）
            directives = source if isinstance(source.get("avoid"), list) else {}
        lessons = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        text = " ".join(str(item) for item in lessons if str(item).strip()).lower()
        if not text:
            return {}
        signals: dict[str, int] = {}
        for direction, keywords in self._REWARD_LESSON_POSITIVE.items():
            if any(str(k).lower() in text for k in keywords):
                signals[direction] = signals.get(direction, 0) + 1
        for direction, keywords in self._REWARD_LESSON_NEGATIVE.items():
            if any(str(k).lower() in text for k in keywords):
                signals[direction] = signals.get(direction, 0) - 1
        # 自损/血上限担忧（独立轴：对"有自损的卡"降权）
        if any(k in text for k in ("自损", "失去生命", "扣血", "血上限", "生命上限", "烧血")):
            signals["self_damage"] = 1
        # ★★ 诅咒担忧（独立轴，用户点名的缺陷 B）：
        #   「涉及变化卡的事件或遗物要避开诅咒，因为选诅咒只会变成另一种诅咒，
        #     对提升卡牌质量的期望太低」。
        #   ⇒ 教训里出现"诅咒"时置位，供**事件屏**（拿诅咒的选项）与**选卡屏**
        #     （诅咒卡）降权。此前 `curse` 从不被产出 ⇒ 事件屏那条判据是死的。
        if any(k in text for k in ("诅咒", "curse", "污染卡组", "变废", "污染牌库")):
            signals["curse"] = 1
        return {k: v for k, v in signals.items() if v != 0}

    def _reward_lesson_bonus(self, card: dict[str, Any], signals: dict[str, int], archetype_tags: list[str]) -> int:
        """按复盘信号给单张候选卡加权。

        ★ 保守设计（避免噪声压过 `_score_reward_card` 的既有判据）：
          * 每方向每级 +18，最多两级（36）—— 低于"诅咒 −60"与"状态牌 −40"，
            不会把垃圾卡救回来，也不会压过流派/数值的主判据。
          * 负向方向（教训说"别光垫格挡"）对称扣分。
          * 教训为空 ⇒ 返回 0（保证零教训时行为与改动前**完全一致**）。
        """
        if not signals:
            return 0
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        # 各方向的"这张卡属不属于该方向"判据（复用 _score_reward_card 的关键词口径）
        card_is: dict[str, bool] = {
            "defense": any(k in text for k in ("block", "格挡", "护盾", "护甲", "defend")),
            "attack": any(k in text for k in ("damage", "伤害", "攻击", "attack", "strike")),
            "draw": bool(_ARCHETYPE_DRAW_RE.search(text)) or any(k in text for k in ("draw", "skim")),
            "energy": any(k in text for k in ("energy", "能量", "储能")),
            "strength": any(k in text for k in ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")),
            "orb_focus": any(k in text for k in ("orb", "focus", "电球", "充能", "聚焦", "宝珠")),
        }
        bonus = 0
        for direction, weight in signals.items():
            if direction == "self_damage":
                continue
            if card_is.get(direction):
                bonus += 18 * max(-2, min(2, weight))
        if signals.get("self_damage", 0) > 0 and self._reward_dynamic_value(card, "HpLoss") > 0:
            bonus -= 20
        return bonus

    def _reward_claim_signature(self, summary_context: dict[str, Any]) -> str:
        rewards = self._reward_claimable_list(summary_context)
        return "|".join(f"{item.get('index')}:{item.get('reward_type')}" for item in rewards)

    @staticmethod
    def _reward_claimable_list(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        rewards = reward.get("rewards") if isinstance(reward.get("rewards"), list) else []
        return [item for item in rewards if isinstance(item, dict) and bool(item.get("claimable", True))]

    def note_reward_claim_dispatched(self, option_index: Any) -> None:
        """服务层在**真的把 claim_reward 发出去**之后调用。

        "已经试过"必须由执行推动，不能由规划推动 —— 规划每 tick 都跑（事件流约 1 次/秒），
        而执行约 2.5 秒一次，用规划推进会把候选在几秒内白白耗光（选牌光标踩过这个坑）。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._reward_claim_tried.add(index)

    def _next_reward_claim_index(self, summary_context: dict[str, Any]) -> int | None:
        """该领哪一个奖励；都领不动就返回 None，交给调用方去推进界面。

        为什么需要这张账：**药水栏满时"领药水奖励"发不出去，而 mod 照样回 completed** ——
        奖励清单不变、`step_count` 照涨，于是插件每 tick 重发同一个 `claim_reward(0)`（药水），
        无限重领（实测 floor 13 的奖励屏卡了 5 分钟以上）。药水排到最后，领过的下标不再重发。
        """
        rewards = self._reward_claimable_list(summary_context)
        if not rewards:
            return None
        signature = self._reward_claim_signature(summary_context)
        if signature != self._reward_claim_key:
            self._reward_claim_key = signature
            self._reward_claim_tried = set()

        def rank(item: dict[str, Any]) -> tuple[int, int]:
            # 药水最容易"领不动"（栏位满），排到最后；其余按下标顺序。
            is_potion = 1 if str(item.get("reward_type") or "").strip().lower() == "potion" else 0
            index = self._safe_int(item.get("index"))
            return (is_potion, index if index is not None else 0)

        for item in sorted(rewards, key=rank):
            index = self._safe_int(item.get("index"))
            if index is None or index in self._reward_claim_tried:
                continue
            return index
        return None

    def _reward_candidates(self, summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward_cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
        if reward_cards:
            return reward_cards
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if selection_cards:
            return selection_cards
        reward = payload.get("reward") if isinstance(payload.get("reward"), dict) else {}
        card_options = reward.get("card_options") if isinstance(reward.get("card_options"), list) else []
        if card_options:
            return card_options
        return []

    def _preferred_remove_option(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        strategy_context: dict[str, Any] | None = None,
    ) -> int | None:
        selection_cards = summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else []
        # ★ 让【跨局复盘教训】作用于「该删哪张」。
        #   此前删牌屏只经 `_ranked_option_indices → _apply_run_lessons` 影响
        #   **扰动候选顺序**，不改 `preferred` 本身 ⇒ 复盘学到的取向
        #   无法改变真正删掉的那张（用户点名「删除哪张卡片」很影响战斗）。
        #
        #   ★★ 符号方向（我第一次写反了，实测抓到）：本函数取 `candidate` 的 **min**，
        #      即 `_score_remove_card` 分数**越低越该删**（诅咒 −100 最该删）。
        #      ⇒ "保护某张卡"必须**加上**保护量（抬高分数 ⇒ 更不该删），
        #        绝不能减。写成 `score -= protection` 会让被保护的卡变成**最先被删**的
        #        （实测：防御卡 eff=−8 反而胜出 ⇒ 正好反了）。
        lesson_signals = self._reward_lesson_signals(strategy_context if strategy_context is not None else summary_context)
        best_candidate: tuple[int, int] | None = None
        for index, card in enumerate(selection_cards if isinstance(selection_cards, list) else []):
            if not isinstance(card, dict):
                continue
            if self._is_unremovable_card(card):
                continue
            score = self._score_remove_card(card)
            if lesson_signals:
                # 符合"要多拿"方向的卡 ⇒ **抬高**其"该删程度"分数 ⇒ 更不该删
                score += self._remove_lesson_protection(card, lesson_signals)
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            candidate = (score, -card_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
        if best_candidate is not None:
            return -best_candidate[1]
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _remove_lesson_protection(self, card: dict[str, Any], signals: dict[str, int]) -> int:
        """复盘教训对"该删哪张"的保护量（正数 = 越不该删 ⇒ 调用方**加**到删牌分上）。

        ★ 与 `_reward_lesson_bonus` 的关系：那个用于**选卡**（符合方向 ⇒ 加分拿），
          这个用于**删牌**（符合方向 ⇒ 抬高删牌分 ⇒ 更不该删）。语义相反、方向一致。
        ★ 保守：与选卡同量级（每方向每级 18），不足以把诅咒（−100）救成"不该删"。
        """
        if not signals:
            return 0
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        card_is: dict[str, bool] = {
            "defense": any(k in text for k in ("block", "格挡", "护盾", "护甲", "defend")),
            "attack": any(k in text for k in ("damage", "伤害", "攻击", "attack", "strike")),
            "draw": bool(_ARCHETYPE_DRAW_RE.search(text)) or any(k in text for k in ("draw", "skim")),
            "energy": any(k in text for k in ("energy", "能量", "储能")),
            "strength": any(k in text for k in ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")),
            "orb_focus": any(k in text for k in ("orb", "focus", "电球", "充能", "聚焦", "宝珠")),
        }
        protection = 0
        for direction, weight in signals.items():
            if direction == "self_damage":
                continue
            if card_is.get(direction):
                protection += 18 * max(-2, min(2, weight))
        return protection

    def _selection_can_confirm(self, summary_context: dict[str, Any]) -> bool:
        """选牌屏是否已经满足可确认条件。

        mod 只对战斗内手牌选择填 can_confirm（BuildSelectionPayload 取自 combat-hand 元数据），牌库/网格
        选择屏恒为 false。所以这里保守判断：can_confirm 为真 → 可确认；否则不主动确认（select_deck_card
        自己会在选够后点确认按钮），避免在没选够时白点一次。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return bool(payload.get("selection_can_confirm"))

    def _next_selection_option(self, summary_context: dict[str, Any], preferences: dict[str, Any], context: dict[str, Any]) -> int | None:
        """选牌屏要按的 option_index：多选时逐张推进，绝不重复按同一张。

        先按偏好（/solver/plan 的 choice_card_id -> 删牌启发式）；那张在这一屏已经按过就换下一张没按过的。
        全部按过仍无法推进时返回 None，交给调用方走确认/离开分支。已按过的集合以"选牌屏签名"为键，
        换屏或候选集合变化即重置（新的一屏从干净状态开始）。

        **只在"真的按了"之后才推进光标**（见 note_selection_option_pressed，由 service 在成功派发
        select_deck_card 后调用）。历史教训：这里曾经在**规划时**就 add 进 _selection_pressed，而
        plan() 是**每 tick 都被调用一次**的（事件流约 1 次/秒），自动玩步进却约 2.5 秒一次 ——
        于是四个候选在几秒内被"规划"白白耗光，屏幕上一个都没选中（selected_count 仍为 0），
        此后 plan() 永远返回 None，猫娘就**永久停在这一屏**不动了（玩家看到的"卡住"）。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if not cards:
            return self._preferred_choice_option(summary_context, preferences, context)

        signature = "|".join(
            str(card.get("card_id") or card.get("index")) for card in cards if isinstance(card, dict)
        )
        if signature != self._selection_cursor_key:
            self._selection_cursor_key = signature
            self._selection_pressed = set()
            self._selection_exhausted = 0

        count = len(cards)
        preferred = self._preferred_choice_option(summary_context, preferences, context)
        if preferred is not None and 0 <= preferred < count and preferred not in self._selection_pressed:
            return preferred
        for index in range(count):
            if index not in self._selection_pressed:
                return index
        # 这一屏的候选都按过了。
        self._selection_exhausted += 1
        if self._selection_exhausted >= self._SELECTION_EXHAUSTED_RETRIES:
            # 按遍了整屏却没换过屏（多半是一次都没真正落地）。此时若一直返回 None，这一屏就再也
            # 出不了动作 = 死锁；宁可重头再按一遍，也不让猫娘永久停手。
            self._debug(
                "[sts2_selection] 候选按遍仍未推进，重置光标重按 (key=%s pressed=%s)",
                signature,
                sorted(self._selection_pressed),
            )
            self._selection_pressed = set()
            self._selection_exhausted = 0
            return preferred if (preferred is not None and 0 <= preferred < count) else 0
        return None

    def note_selection_option_pressed(self, option_index: Any) -> None:
        """服务层在**真的把 select_deck_card 发出去**之后调用：把该下标记进"已按过"。

        规划与执行不是一一对应的（每 tick 都会规划，但只有少数 tick 会真正执行），
        所以光标必须由"执行"推动，而不是由"规划"推动。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._selection_pressed.add(index)

    def note_event_option_chosen(self, summary_context: dict[str, Any], option_index: Any) -> None:
        """服务层在**真的把 choose_event_option 发出去**之后调用：给该选项的"已选次数"加一。

        ★缺陷 A3 的修复入口。`_rank_event_options` 原本无状态，在可重复选择的事件上
        每轮算出同一个最优解 ⇒ 无限重复（实机后果：反复选"消耗生命上限"直到 HP 变 1）。

        与 `note_selection_option_pressed` 同样的理由：规划与执行不是一一对应
        （`plan()` 每 tick 都跑，约 1 次/秒；实际派发约 2.5 秒一次），
        所以计数必须由"执行"推动，不能由"规划"推动 —— 否则计数会被凭空放大。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        self._note_event_option_chosen(summary_context if isinstance(summary_context, dict) else {}, index)

    def _selection_kind(self, summary_context: dict[str, Any]) -> str:
        """取 mod 给出的选牌屏细分类型（`payload.selection_kind`），取不到返回空串。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return str(payload.get("selection_kind") or "").strip().lower()

    def _state_name(self, summary_context: dict[str, Any]) -> str:
        """取**屏幕名**（`payload.state_name`）。

        ★为什么变换屏判定必须同时看它（实测发现的静默失效风险）：
        真实对局里变换屏的屏幕名是 `card_selection_transform`（实机日志里
        `screen=CARD_SELECTION_TRANSFORM` 确实出现过），而 `selection_kind` 取自
        mod 的 `selection.kind`。两者是**独立**来源，一旦 `selection_kind` 缺失/改名，
        只靠它的判定会**静默失效** —— 变换屏会退回去复用删牌启发式，
        也就是**每轮主动选诅咒**（正是缺陷 B 本身），而且不报任何错。

        我实测过这个场景（`.dsh-artifacts/probe_b_statename.py`）：
        只给 `state_name`、不给 `selection_kind` 时，旧实现 `is_transform=False` ⇒ 选诅咒。
        ⇒ 两个信号取**并集**，任一命中即认定为变换屏。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw = payload.get("state_name")
        if not raw:
            classification = summary_context.get("classification")
            if isinstance(classification, dict):
                raw = classification.get("state_name")
        return str(raw or "").strip().lower()

    def _is_transform_selection(self, summary_context: dict[str, Any]) -> bool:
        """是否**变换**屏。两个独立信号取并集（任一命中即可），避免静默失效。"""
        if self._selection_kind(summary_context) in self._TRANSFORM_SELECTION_KINDS:
            return True
        return self._state_name(summary_context) in self._TRANSFORM_SCREEN_NAMES

    def _preferred_transform_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """**变换**屏该选哪张牌：优先把手上**已有的诅咒/废牌**换掉，但**绝不主动新增诅咒**。

        与删牌屏的区别（这是本方法存在的全部理由）：
        * 删牌屏要"移出牌组" ⇒ 选最该走的牌（诅咒最优先）。
        * 变换屏要"换成别的牌" ⇒ 选**最没价值、且换掉后不会更差**的牌。
          诅咒虽然是废牌，但变换它**期望接近零**（游戏侧豁免了稀有度过滤，可能换回另一张诅咒），
          因此**诅咒的优先级低于"普通低价值牌"** —— 换掉一张废攻击牌的收益更确定。

        排序逻辑（分越低越该被变换）：
          1. 不可移除的牌（`_is_unremovable_card`）直接排除
          2. 诅咒 / 状态牌：给**中性偏低**分（可换，但不是首选）—— 避免"变诅咒"的零期望赌注
          3. 其它牌：沿用 `_score_remove_card`（分越低越是废牌）
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        best: tuple[tuple[int, int], int] | None = None
        scored_cards: list[tuple[int, str, bool]] = []
        for index, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            if self._is_unremovable_card(card):
                continue
            card_index = self._safe_int(card.get("index"))
            if card_index is None:
                card_index = index
            rarity = str(card.get("rarity") or "").strip().lower()
            card_type = str(card.get("card_type") or "").strip().lower()
            is_curse = rarity in {"curse"} or card_type in {"curse", "status"}
            if is_curse:
                # 可换，但不优先：变换诅咒的期望接近零（可能换回另一张诅咒）。
                # 用一个固定值把它排在"有明确价值的牌"之后、"完全没得选"之前。
                score = self._TRANSFORM_CURSE_SCORE
            else:
                score = self._score_remove_card(card)
            scored_cards.append((score, str(card.get("name") or card_index), is_curse))
            candidate = (score, -card_index)
            if best is None or candidate < best[0]:
                best = (candidate, card_index)
        self._log_transform_route(scored_cards, best[1] if best is not None else None)
        if best is not None:
            return best[1]
        return self._preferred_remove_option(summary_context, preferences)

    def _log_transform_route(
        self,
        scored_cards: list[tuple[int, str, bool]],
        chosen: int | None,
    ) -> None:
        """把变换屏决策写进日志（与 `[sts2_event_route]` / `[sts2_map_route]` 同风格）。

        ★为什么要加：变换屏此前**没有任何专属决策日志** ——
        事件屏我已补（`_log_event_route`），但变换屏仍是盲区。
        而缺陷 B 恰恰在变换屏上，没有日志就无法实机验证"是否还在选诅咒"
        （`CARD_SELECTION_TRANSFORM` 在历史日志里有 67 次真实出现，却查不到任何决策痕迹）。

        去重键取"候选与分数快照 + 最终选择"，同一屏静止时不刷屏。
        """
        if self._logger is None:
            return
        key = "|".join("%d:%s:%d" % (score, name, 1 if curse else 0)
                       for score, name, curse in scored_cards) + "#%s" % chosen
        if key == self._last_transform_route_log_key:
            return
        self._last_transform_route_log_key = key
        detail = " / ".join(
            "%s=%d%s" % (name[:16], score, "(诅咒)" if curse else "")
            for score, name, curse in sorted(scored_cards)
        )
        try:
            self._logger.info(
                "[sts2_transform_route] 候选=%d 选择=%s | %s",
                len(scored_cards),
                chosen,
                detail,
            )
        except Exception:
            # 日志绝不能影响决策路径。
            pass

    def _is_upgrade_selection(self, summary_context: dict[str, Any]) -> bool:
        """本屏是不是"升级卡牌"屏。

        判据优先级（**先看 mod 明确下发的 kind，再看名称兜底**）：
          1. `payload["selection_kind"]` —— mod `GameStateService.cs:4249` 对
             `NDeckUpgradeSelectScreen` 下发 `"deck_upgrade_select"`，最可靠；
          2. `payload["selection"]["kind"]` 原始结构（同上，双保险）；
          3. 卡牌名/描述里出现"升级"字样（兜底，避免 mod 改名后彻底失效）。
        ★ 绝不把"删牌屏"误判成升级屏：`card_selection_remove/delet` 先排除。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        kind = str(payload.get("selection_kind") or "").strip().lower()
        if not kind:
            sel = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
            kind = str(sel.get("kind") or "").strip().lower()
        if kind:
            # 删牌/变换/附魔各有自己的语义，明确排除。
            if kind in {"deck_card_select", "deck_transform_select", "deck_enchant_select",
                        "choose_card_select", "combat_hand_select"}:
                return False
            if "upgrade" in kind or "smith" in kind:
                return True
            # kind 明确但非升级 ⇒ 不猜。
            return False
        # kind 缺失（旧版 mod / 其它屏）：用提示语兜底。
        prompt = ""
        sel = payload.get("selection") if isinstance(payload.get("selection"), dict) else {}
        prompt = str(sel.get("prompt") or "").strip().lower()
        return any(token in prompt for token in ("升级", "upgrade", "smith", "锻造"))

    def _preferred_upgrade_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """升级屏选哪张卡：**分数越高越该升**（与删牌刚好相反）。

        不是升级屏时返回 None，让调用方继续走删牌/变换分支。
        """
        if not self._is_upgrade_selection(summary_context):
            return None
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        if not cards:
            return None
        best: tuple[int, int] | None = None
        for position, card in enumerate(cards):
            if not isinstance(card, dict):
                continue
            if self._item_blocked(card):
                continue
            score = self._score_upgrade_card(card)
            index = self._safe_int(card.get("index"))
            if index is None:
                index = position
            # 取 **max**（分越高越该升）；同分时取较小 index 以保证确定性。
            candidate = (score, -index)
            if best is None or candidate > best:
                best = candidate
        if best is None:
            return None
        return -best[1]

    def _preferred_choice_option(self, summary_context: dict[str, Any], preferences: dict[str, Any], context: dict[str, Any]) -> int | None:
        """选牌（消耗/拉弃牌堆/生成到手/变换等，弹的是人选牌界面）：优先用 /solver/plan 的 choice_card_id
        （solver 已优化选哪张），命中不到再走删牌启发式。避免卡死在选牌上。

        ★变换屏（`selection.kind == "deck_transform_select"`）**不走删牌启发式** ——
        两者最优语义相反，详见 `_preferred_transform_option` 的说明。
        """
        pending = context.get("pending_card_choice_id") if isinstance(context, dict) else None
        if pending:
            payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
            cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
            for i, card in enumerate(cards):
                if isinstance(card, dict) and str(card.get("card_id") or "") == str(pending):
                    idx = self._safe_int(card.get("index"))
                    return idx if idx is not None else i
        if self._is_transform_selection(summary_context):
            return self._preferred_transform_option(summary_context, preferences)
        # ★★ 升级屏必须**先于**删牌启发式分流（2026-10-18 实机缺陷）：
        #   升级屏与删牌屏的**最优方向相反**，共用 _preferred_remove_option 会
        #   变成"优先升级最该删的卡"（实测：优先看打击 7/13，打击+ 出现 35 次）。
        #   判据用 mod 下发的 `selection.kind`（GameStateService.cs:4249
        #   `NDeckUpgradeSelectScreen => "deck_upgrade_select"`），
        #   经 summary_context_builder._selection_counts 落到 payload["selection_kind"]。
        upgrade_option = self._preferred_upgrade_option(summary_context, preferences)
        if upgrade_option is not None:
            return upgrade_option
        # ★ 显式传 `strategy_context`：跨局教训（`strategy_directives.avoid`）在它里面，
        #   不在 `summary_context` 里（`service.py:549-557` 把两者作为兄弟键放进 snapshot）。
        #   不传 ⇒ 教训通道恒空 ⇒ 复盘学到的取向无法影响「该删哪张」。
        return self._preferred_remove_option(
            summary_context,
            preferences,
            context.get("strategy_context") if isinstance(context, dict) else None,
        )

    def _log_rest_choice(self, summary_context: dict[str, Any], chosen: int) -> None:
        """把篝火的选择用 info 级记一次（同一决定只记一次）。

        玩家会直接质问"为什么又回血不锻造"，所以这里必须留痕：选项 id 列表 + 血量 + 选中的下标。
        """
        if self._logger is None:
            return
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        rest = payload.get("rest") if isinstance(payload.get("rest"), dict) else {}
        options = rest.get("options") if isinstance(rest.get("options"), list) else []
        ids = [str(item.get("option_id") or item.get("title") or "?") for item in options if isinstance(item, dict)]
        key = f"{chosen}|{'/'.join(ids)}"
        if key == self._rest_choice_log_key:
            return
        self._rest_choice_log_key = key
        try:
            self._logger.info(
                "[sts2_rest] 选项=%s 血=%s/%s -> 选 option_index=%s",
                ids,
                payload.get("current_hp"),
                payload.get("max_hp"),
                chosen,
            )
        except Exception:
            pass

    def _preferred_rest_option(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> int | None:
        """篝火选哪一项：**默认优先锻造（升级卡牌）**，血低才回血。

        玩家口径：「插件偏爱休息回血，而不是锻造卡牌。锻造可以升级卡牌、提升数值、降低费用，
        对战力提升很大，是需要优先思考的」。原来的实现是

            preferred_option = self._preferred_route_option(preferences) or 0

        —— 要么用**路由**偏好（跟篝火选项没关系），要么直接取下标 0，也就是"列表第一项"，
        而列表第一项通常就是回血。所以插件事实上永远在回血。

        现在的口径：

        1. 玩家显式配过的偏好优先（他说话算数）；
        2. 血比 < `REST_HEAL_HP_RATIO` → 回血优先（快死了还去锻造就是送）；
        3. 否则**锻造/升级优先**（升数值、降费用，战力涨得比几十点血值）；
        4. 认不出类型就取第一个**可用**选项。

        选项类型靠 `option_id` / `title` / `description` 的关键词判断 —— mod 发的是 `option_id`
        （见 GameStateService 的 RestOptionPayload），不是 `type`/`name`。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        rest = payload.get("rest") if isinstance(payload.get("rest"), dict) else {}
        options = [item for item in (rest.get("options") if isinstance(rest.get("options"), list) else []) if isinstance(item, dict)]
        if not options:
            return None

        def option_index(position: int, item: dict[str, Any]) -> int:
            index = self._safe_int(item.get("index"))
            return index if index is not None else position

        # 1) 玩家显式偏好（route/rest preference）—— 只要落在范围内就听他的。
        explicit = self._preferred_route_option(preferences)
        if explicit is not None and any(option_index(position, item) == explicit for position, item in enumerate(options)):
            return explicit

        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 1.0

        def kind(item: dict[str, Any]) -> str:
            text = " ".join(
                str(item.get(key) or "") for key in ("option_id", "title", "description", "name", "type")
            ).lower()
            if any(token in text for token in _REST_SMITH_TOKENS):
                return "smith"
            if any(token in text for token in _REST_HEAL_TOKENS):
                return "heal"
            return "other"

        usable = [(position, item) for position, item in enumerate(options) if bool(item.get("is_enabled", True))]
        pool = usable or list(enumerate(options))
        wanted = "heal" if hp_ratio < REST_HEAL_HP_RATIO else "smith"
        for position, item in pool:
            if kind(item) == wanted:
                return option_index(position, item)
        for position, item in pool:
            if kind(item) == "other":
                return option_index(position, item)
        return option_index(*pool[0])

    def _reward_kwargs(self, summary_context: dict[str, Any], action: dict[str, Any], preferred_option: int | None) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "claim_reward":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        if action_type == "choose_reward_card":
            return {"option_index": preferred_option if preferred_option is not None else 0}
        return {}

    def _is_unremovable_card(self, card: dict[str, Any]) -> bool:
        if bool(card.get("unremovable")) or bool(card.get("cannot_remove")):
            return True
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
            ]
        ).lower()
        return "eternal" in text or "永恒" in text

    def _score_remove_card(self, card: dict[str, Any]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
                str(card.get("rarity") or ""),
            ]
        ).lower()
        if any(token in text for token in ["curse", "诅咒"]):
            return -100
        if any(token in text for token in ["status", "状态"]):
            return -90
        if any(token in text for token in ["strike", "打击"]):
            return -60
        if any(token in text for token in ["defend", "防御"]):
            return -50
        if any(token in text for token in ["draw", "skim", "coolheaded", "抽牌", "过牌"]):
            return 60
        if any(token in text for token in ["energy", "turbo", "double energy", "能量"]):
            return 70
        if any(token in text for token in ["focus", "orb", "电球", "充能"]):
            return 65
        return 10

    def _route_policy(self, preferences: dict[str, Any]) -> dict[str, Any]:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "route_policy":
                continue
            return value
        return {}

    def _score_event_option(self, option: dict[str, Any], hp_ratio: float, gold: int) -> int:
        # ★★ D11（t28）：**被锁选项绝对不可选** —— 必须在一切加分之前短路返回。
        #
        #   实机缺陷（2026-10-20 00:24:24 起，run 6W6FGS6W4TPS / ENDLESS_CONVEYOR / floor=14）：
        #     `/state` 的 `event.options` 逐字带了 `is_locked`：
        #       options[0] {title:破产了, is_locked:true }   ← 金币 18 不足支付
        #       options[1] {title:离开,   is_locked:false}
        #     但本函数只经 `_event_option_text()` 做**文本子串**匹配，而 `is_locked` 是**布尔字段**、
        #     不在文本里 ⇒ 结构上不可能被感知 ⇒ 给被锁项 **+10**、给可选项 **0**：
        #       被锁项文本「你还能吃得下更多，但是兜里没金币了。」命中 `"金币"` ⇒ +10（见下方 :4356 规则）
        #       ★ 即：**因为提到了金币，就给一个付不起钱的选项加分**。
        #     然后游戏 99 次回 `invalid_target: The selected event option is locked.`，
        #     而该错误在 EVENT 屏上"重算"仍得同一结论 ⇒ 永久卡死。
        #
        #   ★ 为什么必须**短路**而不是"减分"：当前可选选项的合理最高分约
        #     遗物14 + 金币10 + 卡牌8 + 回血18 ≈ 50；若只给被锁项 −60，则 50 > −60
        #     ⇒ 仍会选被锁的 ⇒ 修不好。只有让被锁项**低于任何可能总分**才能保证绝不选中。
        #
        #   ★ 为什么是 −1000：任一单项正向加分 ≤ +70；即便所有正向规则全部命中，
        #     总和也远小于 1000。且 `_normalize_percentile` 只做**相对**归一，
        #     哨兵值保证被锁项恒排末位。
        #
        #   ★ 不误伤（三条）：
        #     ① 全部选项未锁 ⇒ 本分支永不进入 ⇒ 打分**逐位不变**；
        #     ② 字段**缺失**（旧 mod / 测试替身）⇒ `is_locked=None` ⇒ 视为**未锁**（保守）；
        #     ③ 只认**真值**（True 或 "true"/"1" 这类显式真），不把 0/""/None 当已锁。
        if self._event_option_is_locked(option):
            return self._EVENT_LOCKED_OPTION_SCORE
        text = self._event_option_text(option)
        score = 0
        # ★生命上限损失：**永久不可恢复**，单独且**最重**地惩罚。
        #   必须先于普通掉血判断，否则会被 `"hp" in text` 之类的宽泛匹配吞掉。
        #   实机教训：`"失去 5 点生命上限，获得 100 金币"` 原先得 **+10**（金币 +10 盖过一切），
        #   于是脚本每轮都选它，把 max_hp 从 80 一路扣到 54；可重复选择的事件上会一直选到 HP 变 1。
        #   惩罚量级取 60 —— 高于任何单项正向收益（遗物 +14、删牌 +12、金币 +10），
        #   使"任何数量的即时收益都不足以换一次永久上限损失"。
        if self._option_loses_max_hp(text):
            score -= 60
        # ★普通掉血：必须用 `_option_loses_hp`（它已判方向），**不能**用下面的裸子串匹配。
        #   实机抓到的缺陷：原实现是 `any(token in text for token in ["lose hp", "hp", ...])`，
        #   而裸 `"hp"` 子串会命中 `"Gain 5 Max HP"` ⇒ 把**加最大生命**也罚了 −8。
        #   中文侧同理（"获得11点最大生命值"里的"生命"）。
        if self._option_loses_hp(text):
            # ★量级必须**高于**普通战利品，否则"掉血换药水"会被奖励盖过。
            #   实机（EVENT#3，血比 0.73）：「失去8点生命。获得1瓶随机药水。」
            #   若掉血只罚 8，而药水给 +14 ⇒ 净 **+6** ⇒ 会**主动选掉血**，
            #   与"低血时不拿命换物资"的意图相反。
            #   故提高到 24/12：任一常见战利品（药水14 / 金币10 / 卡牌8）都无法单独抵消。
            score -= 24 if hp_ratio < 0.6 else 12
        # ★"掉血换物资"必须**净负**：即使两种战利品同时给（药水14 + 金币10 = 24），
        #   在血比 ≥0.6 时 24 − 12 = **+12** 仍是正分 ⇒ 会主动拿命换。
        #   实测确认过这个漏洞（血比0.73: 掉血8+药水14 → 净 +2）。
        #   这里再加一道**乘法抑制**：掉血时把所有正向收益减半 ——
        #   在低血时干脆全抹掉（拿命换物资是最该避免的），高血时也只拿一半好处。
        #   ★必须在所有加分规则**之后**执行，所以这里只记标记，末尾统一处理。
        loses_hp = self._option_loses_hp(text) or self._option_loses_max_hp(text)
        # ★加上限（与扣上限相反方向）：永久提升容错空间，必须给**正分**。
        #   实机：「获得[blue]11[/blue]点最大生命值。」—— 修复方向判定后不再被罚，
        #   但此前也没有任何规则给它**加分** ⇒ 落到 0 分，与"什么都不发生"等价。
        #   量级取 +16：低于扣上限的 −60（不对称是故意的：永久损失比永久收益更该避），
        #   但高于单次掉血、与遗物(14)同档。
        if self._option_gains_max_hp(text):
            score += 16
        if any(token in text for token in ["gain gold", "gold", "获得金币", "金币"]):
            score += 10 if gold < 150 else 4
        # ★遗物/道具：游戏文案里不叫"遗物"，而是**具体道具名**（如"藏宝图"）。
        #   实机：「获得[gold]藏宝图[/gold]。」原先得 **0 分** —— 一个遗物级道具
        #   与"什么都不给"同分。除字面词外，加入两个高频形态：
        #   * 「获得X」+ 无其它类别词 ⇒ 视为道具（保守：这类事件选项几乎都是给东西）
        #   * 药水/宝物类关键词
        if any(token in text for token in ["relic", "遗物", "宝物", "宝图", "药水", "potion"]):
            score += 14
        # ★卡牌：实机文案是"稀有牌"/"普通牌"/"卡牌包"，**不含**字面"卡牌"二字
        #   （「获得一张随机[gold]稀有牌[/gold]。」原先得 0 分）。
        #   故补齐"X牌"这一族（稀有牌/罕见牌/普通牌/诅咒牌/升级牌…）。
        if any(token in text for token in ["card", "卡牌", "获得卡", "稀有牌", "普通牌", "罕见牌", "牌包"]):
            score += 8
        if any(token in text for token in ["remove", "删除", "移除"]):
            score += 12
        # ★回血：量随血量而定 —— 低血时是救命的，满血时几乎无价值。
        #   实机：「回复[green]10[/green]点生命。」原先得 **0 分**
        #   （血比 0.81 时它确实价值不高，但 0 分让它与"什么都没发生"等价；
        #   血比 0.30 时它却依然是 0 分 —— 这才是真问题）。
        if re.search(r"(回复|恢复|治疗|heal|restore)", text) and re.search(r"(生命|血量|hp|health)", text):
            score += 18 if hp_ratio < 0.5 else (8 if hp_ratio < 0.8 else 2)
        if any(token in text for token in ["curse", "诅咒", "愧疚", "笨拙", "晕眩", "伤口"]):
            # ★诅咒在"卡牌质量"维度是**零/负期望**：诅咒变换时被游戏豁免了稀有度过滤
            #   （`CardFactory.GetFilteredTransformationOptions`：`(uint)(rarity-8) > 1u` 对
            #   Curse(9) 为假 ⇒ 跳过"只保留 Common/Uncommon/Rare"的约束），
            #   候选池可含其它诅咒 ⇒「诅咒只会变成另一种诅咒」。
            #   因此惩罚必须**高于遗物(+14)与卡牌(+8)之和**，不能被"有得有失"的选项抵消。
            #   ★补入具体诅咒卡名：实机文案写的是"愧疚"/"笨拙"，不写"诅咒"二字
            #   （「将1张[red]笨拙[/red]」「并将1张[red]愧疚[/red]」原先都**逃过惩罚**）。
            score -= 40
        # ★★"掉血换物资"必须**净负**（最后一道闸门）。
        #   为什么需要它：上面各条正向规则是**独立相加**的，所以"给得多"可以盖过掉血：
        #   实测（血比 0.73）「失去8点生命。获得1瓶随机药水。」= −12 +14 = **+2** ⇒
        #   仍会**主动拿命换药水**。同理若同时给金币与卡牌，净分更高。
        #   ⇒ 掉血/扣上限的选项，其正向收益一律**减半并再减 1**，保证**恒为负**；
        #   血比 <0.6 时**全部抹掉**（低血时拿命换物资是本项目最该避免的决策）。
        #   实测：只减半时血比 0.73 的「掉血8+药水14」= −12 +7 = **+1** 仍为正 ⇒
        #   故 `//2 - 1` 把上界钉死在 **≤ −1**（"绝不主动拿命换"是硬约束，不是偏好）。
        #   注意：惩罚项（诅咒 −40）保留原样，只压缩**收益**。
        if loses_hp and score > 0:
            score = 0 if hp_ratio < 0.6 else min(score // 2 - 1, -1)
        return score

    @staticmethod
    def _event_option_is_locked(option: dict[str, Any]) -> bool:
        """事件选项是否被游戏**锁定**（`/state` 的 `event.options[].is_locked`）。

        ★ 实机字段（2026-10-20，ENDLESS_CONVEYOR，逐字取自 `/state`）：
            {"index": 0, "title": "破产了", "is_locked": true,  ...}   ← 金币不足支付
            {"index": 1, "title": "离开",   "is_locked": false, ...}
          字段名确为 `is_locked`（实测 `option` 键 =
          `['index','text_key','title','description','is_locked','is_proceed',
            'will_kill_player','has_relic_preview']`）。

        ★ 判据做**窄**（保守），避免误伤：
          · 只认真布尔 `True`，以及显式真值串（"true"/"1"/"yes"/"locked"）；
          · 字段缺失 / `None` / `False` / `0` / `""` ⇒ **未锁**。
          ⇒ 旧版 mod 或测试替身不带该字段时，行为与修复前**完全一致**。
        """
        if not isinstance(option, dict):
            return False
        raw = option.get("is_locked")
        if raw is True:
            return True
        if isinstance(raw, str):
            return raw.strip().lower() in {"true", "1", "yes", "locked"}
        return False

    @staticmethod
    def _option_gains_max_hp(text: str) -> bool:
        """事件选项是否**永久提升生命上限**（`_option_loses_max_hp` 的反方向）。

        与"回复生命"区分：回复是即时治疗（可被消耗掉），提升上限是永久容错空间。

        实机依据：「获得[blue]11[/blue]点最大生命值。」/「获得[green]2[/green]点最大生命值。」
        —— 两者在修复方向判定后都变成 **0 分**，即"给了永久上限提升"与"什么都没发生"同分。
        """
        t = PlannerChoicesMixin._strip_rich_text(text).lower()
        mentions = (
            re.search(r"(生命|血量|最大)\s*(值)?\s*(上限|最大值)", t) is not None
            or re.search(r"最大生命", t) is not None
            or re.search(r"(max|maximum)\s*\.?\s*(hp|health)", t) is not None
        )
        if not mentions:
            return False
        gain = re.search(r"(获得|得到|增加|提升|提高|gain|increase|\+\s*\d)", t) is not None
        loss = re.search(r"(失去|扣|减少|降低|lose|reduce|-\s*\d)", t) is not None
        # 有得有失时以**损失**为准（与 `_option_loses_max_hp` 保持同一口径）。
        return gain and not loss

    def _event_option_text(self, option: dict[str, Any]) -> str:
        """事件选项的归一化文本：拼接三个来源字段 + 转小写 + **剥富文本标签**。

        ★这是事件文案匹配的**唯一入口**，因此富文本剥离放在这里 ——
        所有下游判断（`_option_loses_max_hp` / `_option_loses_hp` / 金币/遗物/诅咒加分）
        都会自动拿到干净文本，不必各自容忍标签。

        实机原因见 `_strip_rich_text`：游戏文案是 ``失去[red]8[/red]点生命``，
        标签夹在动词与数字之间会让 `失去\\s*\\d*` 这类正则整体失效。
        """
        return PlannerChoicesMixin._strip_rich_text(
            " ".join(
                [
                    str(option.get("label") or ""),
                    str(option.get("text") or ""),
                    str(option.get("description") or ""),
                ]
            )
        ).lower()

    def _score_reward_card(self, card: dict[str, Any], deck_policy: dict[str, set[str] | list[dict[str, Any]]], archetype_tags: list[str]) -> int:
        text = " ".join(
            [
                str(card.get("name") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
        ).lower()
        energy_gain = self._reward_dynamic_value(card, "Energy")
        score = 0
        tag_keywords = {
            "draw": ["draw", "抽牌", "skim", "coolheaded"],
            "defense": ["block", "护盾", "格挡", "defend", "charge battery"],
            "energy": ["energy", "能量", "turbo", "double energy"],
            "orb_focus": ["orb", "focus", "电球", "充能", "defragment", "frost"],
            "expensive_attack": ["2 cost", "3 cost", "x cost", "meteor", "heavy blade"],
            "attack": ["damage", "伤害", "attack", "strike"],
            "strength": ["strength", "力量"],
            "self_damage_attack": ["失去", "lose", "hp loss", "hemokinesis", "御血术"],
            "slow_block_only": ["block", "护盾", "格挡"],
        }
        for tag in deck_policy.get("prefer_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 40
        for tag in deck_policy.get("avoid_tags", set()):
            if any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score -= 40
        for tag in deck_policy.get("archetype_bias", set()):
            if tag in archetype_tags and any(keyword in text for keyword in tag_keywords.get(tag, [tag])):
                score += 20

        score += self._base_reward_score(text, energy_gain)
        # 数值打分：用 dynamic_values 的实际量级（伤害×连击/格挡/抽牌/能量/易伤），按费用与自损扣减。
        score += self._reward_value_score(card)

        branches = deck_policy.get("branches") if isinstance(deck_policy.get("branches"), list) else []
        matched_branch = self._matched_reward_branch(branches, archetype_tags, text)
        if matched_branch is not None:
            prefer_tags = matched_branch.get("prefer_tags") if isinstance(matched_branch.get("prefer_tags"), list) else []
            avoid_tags = matched_branch.get("avoid_tags") if isinstance(matched_branch.get("avoid_tags"), list) else []
            prefer_cards = matched_branch.get("prefer_cards") if isinstance(matched_branch.get("prefer_cards"), list) else []
            avoid_cards = matched_branch.get("avoid_cards") if isinstance(matched_branch.get("avoid_cards"), list) else []
            for tag in prefer_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score += 35
            for tag in avoid_tags:
                if any(keyword in text for keyword in tag_keywords.get(str(tag).strip().lower(), [str(tag).strip().lower()])):
                    score -= 35
            if any(str(card_name).strip().lower() in text for card_name in prefer_cards):
                score += 50
            if any(str(card_name).strip().lower() in text for card_name in avoid_cards):
                score -= 50
        return score

    def _archetype_policy_from_deck(self, deck: dict[str, Any], archetype_tags: list[str]) -> dict[str, Any]:
        """从**卡组自身**生成流派策略（替代恒空的外部 `deck_policy`）。

        ★ 这是缺陷 E 的修复：让「卡牌流派的抉择」真正存在。
        规则（保守、可解释）：
          * 已经在走某流派（该流派卡 ≥ `_ARCHETYPE_COMMIT_COUNT` 张）
            ⇒ `archetype_bias` 收录它 ⇒ 后续同流派卡 +20；
          * 该流派**成型后**，明显**偏离**它的卡被轻微降权（`avoid_tags`），
            避免"什么都要"把卡组摊薄（这是 Roguelike 卡组构建的核心取舍）；
          * 卡组仍很小时（< 阈值）不做任何偏好 ⇒ 早期保持开放，不锁死流派。

        返回空字典即表示"无流派偏好"（行为与改动前一致）。
        """
        if not archetype_tags:
            return {}
        cards = deck.get("cards") if isinstance(deck, dict) else None
        cards = cards if isinstance(cards, list) else []
        if len(cards) < self._ARCHETYPE_COMMIT_COUNT:
            return {}
        # 统计各流派在卡组里的实际张数（用同一套关键词表，口径与 _infer_archetype_tags 对齐）
        counts: dict[str, int] = {}
        for card in cards:
            if not isinstance(card, dict):
                continue
            text = " ".join(
                str(card.get(field) or "")
                for field in ("name", "card_id", "rules_text", "resolved_rules_text", "description", "effect", "card_type")
            ).lower()
            for tag, keywords in self._ARCHETYPE_TAG_KEYWORDS.items():
                if any(keyword in text for keyword in keywords):
                    counts[tag] = counts.get(tag, 0) + 1
        committed = {tag for tag, n in counts.items() if n >= self._ARCHETYPE_COMMIT_COUNT}
        if not committed:
            return {}
        return {
            "archetype_bias": committed,
            # 成型流派之外的"攻击/防御"倾向：若已专注防御流，则少拿纯攻击，反之亦然。
            "committed_counts": counts,
        }

    def _reward_deck_policy(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        prefer_tags: set[str] = set()
        avoid_tags: set[str] = set()
        archetype_bias: set[str] = set()
        branches: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "deck_policy":
                continue
            prefer_tags.update(str(tag).strip().lower() for tag in (value.get("prefer_tags") if isinstance(value.get("prefer_tags"), list) else []) if str(tag).strip())
            avoid_tags.update(str(tag).strip().lower() for tag in (value.get("avoid_tags") if isinstance(value.get("avoid_tags"), list) else []) if str(tag).strip())
            archetype_bias.update(str(tag).strip().lower() for tag in (value.get("archetype_bias") if isinstance(value.get("archetype_bias"), list) else []) if str(tag).strip())
            if isinstance(value.get("branches"), list):
                branches.extend(item for item in value.get("branches") if isinstance(item, dict))
        return {
            "prefer_tags": prefer_tags,
            "avoid_tags": avoid_tags,
            "archetype_bias": archetype_bias,
            "branches": branches,
        }

    def _base_reward_score(self, text: str, energy_gain: int) -> int:
        score = 0
        # ★ 负面卡惩罚（原先【完全缺失】）：`_base_reward_score` 与 `_reward_value_score`
        #   都只对正向关键词加分，于是「诅咒」与「防御」同为 0 分，
        #   平分时还会被 index 次序选中 ⇒ 诅咒/状态牌被收进卡组。
        #   这是「变化卡事件会选到诅咒」的第二个机制（第一个见 :838 跳牌判定被丢弃）。
        #   ⇒ 明确扣分，使"一张都不该拿"的判定能真正成立（配合 reward_skip_low_value）。
        if any(token in text for token in
               ["curse", "诅咒", "无法打出", "unplayable", "不能被打出", "无法被移除"]):
            score -= 60
        if any(token in text for token in
               ["wound", "伤口", "dazed", "晕眩", "slimed", "黏液", "burn", "灼伤", "void", "虚空"]):
            score -= 40
        if any(token in text for token in ["block", "护盾", "格挡"]):
            score += 20
        if any(token in text for token in ["draw", "抽牌"]):
            score += 18
        if any(token in text for token in ["damage", "伤害", "attack", "攻击"]):
            score += 12
        if any(token in text for token in ["strength", "力量"]):
            score += 10
        if energy_gain > 0 or any(token in text for token in ["获得能量", "gain energy", "恢复能量", "refund energy"]):
            score += 16 + (energy_gain * 4)
        return score

    def _reward_value_score(self, card: dict[str, Any]) -> float:
        """基于卡牌 dynamic_values 的实际量级打分（补充关键词打分的盲区：不区分 4 伤和 9 伤）。

        伤害×连击、格挡、抽牌、能量、易伤/虚弱均按量加分；自损(HpLoss)与费用扣分。
        """
        dmg = self._reward_dynamic_value(card, "Damage")
        blk = self._reward_dynamic_value(card, "Block")
        draw = self._reward_dynamic_value(card, "Cards")
        energy = self._reward_dynamic_value(card, "Energy")
        vuln = self._reward_dynamic_value(card, "VulnerablePower")
        weak = self._reward_dynamic_value(card, "WeakPower")
        hp_loss = self._reward_dynamic_value(card, "HpLoss")
        # 多段攻击：Repeat 动态值（如 3伤×3）优先，其次 hit_count。
        repeat = self._reward_dynamic_value(card, "Repeat")
        hits = max(1, repeat or self._safe_int(card.get("hit_count")) or 1)
        cost = self._safe_int(card.get("energy_cost")) or 0
        star_cost = self._safe_int(card.get("star_cost")) or 0
        return (
            dmg * hits * 1.0
            + blk * 0.8
            + draw * 6.0
            + energy * 8.0
            + vuln * 3.0
            + weak * 2.0
            - hp_loss * 2.5
            - cost * 2.0
            - star_cost * 1.0
        )

    def _reward_dynamic_value(self, card: dict[str, Any], field_name: str) -> int:
        for item in card.get("dynamic_values") if isinstance(card.get("dynamic_values"), list) else []:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip().lower() == field_name.strip().lower():
                return self._safe_int(item.get("current_value")) or 0
        return 0

    def _matched_reward_branch(self, branches: list[dict[str, Any]], archetype_tags: list[str], text: str) -> dict[str, Any] | None:
        for branch in branches:
            when_has = branch.get("when_has") if isinstance(branch.get("when_has"), list) else []
            if when_has and not any(str(token).strip().lower() in text for token in when_has):
                continue
            branch_archetypes = [str(tag).strip().lower() for tag in (branch.get("archetype_bias") if isinstance(branch.get("archetype_bias"), list) else []) if str(tag).strip()]
            if branch_archetypes and not any(tag in archetype_tags for tag in branch_archetypes):
                continue
            return branch
        return None

    def _infer_archetype_tags(self, deck: dict[str, Any]) -> list[str]:
        """从卡组推断流派标签。

        ★ 修两个真实缺陷（2026-10-17，实机校准）：
        1. **关键词表严重英文偏置**：实机卡名是中文（游戏跑中文），
           原表 `orb/zap/coolheaded/...` 对「打击/护卫/出击/守墓人」**全部不命中**
           ⇒ 实测真实 12 张卡组只推断出 `['defense','energy']`，
             且 `defense` 还只是误撞到「防」字（命中"防御"）。
           ⇒ 补齐中文关键词。
        2. **只读 `name`/`card_id`，完全不看效果文本**：
           `规则文本/rules_text/description` 里才有「造成N点伤害」这类可判据信息。
           实战中卡名往往猜不出效果（如「守墓人」「负能量脉冲」）
           ⇒ 改为 name + card_id + 全部效果文本一起做 haystack。
        """
        cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        haystack: list[str] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            parts = [
                str(card.get("name") or ""),
                str(card.get("card_id") or ""),
                str(card.get("rules_text") or ""),
                str(card.get("resolved_rules_text") or ""),
                str(card.get("description") or ""),
                str(card.get("effect") or ""),
                str(card.get("card_type") or card.get("type") or ""),
            ]
            haystack.append(" ".join(parts).lower())
        # ★ 中文关键词要选**不会被数量词插断**的短词：
        #   实测真实文本是「抽2张牌」（数字插在"抽"和"牌"之间）⇒ 关键词"抽牌"不命中。
        #   故一律用单词素（抽/牌/格挡…）或容忍插入的形式。
        tags: list[str] = []
        if any(keyword in text for text in haystack for keyword in
               ("orb", "zap", "coolheaded", "ball lightning", "focus", "电球", "充能", "聚焦", "宝珠")):
            tags.append("orb_focus")
        if any(keyword in text for text in haystack for keyword in
               ("block", "defend", "charge battery", "格挡", "护盾", "防护", "防御", "护甲")):
            tags.append("defense")
        # ★ "抽N张牌"（数字插入）与"抽牌堆"（draw pile）必须区分：
        #   用正则而非子串。`抽[0-9一二三四五六七八九十]+张牌` 才是真抽牌。
        if any(
            _ARCHETYPE_DRAW_RE.search(text)
            or any(k in text for k in ("draw", "skim", "coolheaded"))
            for text in haystack
        ):
            tags.append("draw")
        if any(keyword in text for text in haystack for keyword in
               ("energy", "turbo", "double energy", "能量", "储能")):
            tags.append("energy")
        if any(keyword in text for text in haystack for keyword in
               ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak")):
            tags.append("strength")
        # ★ 补 `attack` 标签（原实现**从不产出**它）：
        #   后果实测 —— 纯攻击卡组 `_infer_archetype_tags` 返回 `[]`
        #   ⇒ `_archetype_policy_from_deck` 拿不到流派 ⇒ 攻击卡组**永远没有流派偏好**，
        #     而防御卡组有 ⇒ 系统性地把玩家往防御推。
        if any(keyword in text for text in haystack for keyword in
               ("damage", "strike", "造成", "点伤害", "攻击")):
            tags.append("attack")
        return tags

    def _score_upgrade_card(self, card: dict[str, Any]) -> int:
        """升级屏：一张牌"多值得被升级"。**分数越高越该升。**

        ★★ 为什么必须单独写这一个函数（2026-10-18 实机缺陷）：
          升级屏此前**复用了删牌评分** `_score_remove_card`（该函数给"打击"−60 =
          "最该删的卡"），而 `_preferred_remove_option` 取的是 **min** ⇒
          于是升级屏恰好选中**最该删的那张** ⇒ 实测「可以优先看：打击」7/13 次，
          `打击+` 在日志里出现 35 次。**删牌与升级的最优方向正好相反**：
            删牌：选**最没价值**的（分越低越该删）
            升级：选**最有价值**的（分越高越该升）
          ⇒ 两者**绝不能共用**同一个评分函数。旧行为是语义反转，不是调参问题。

        判据（尽量只用 mod 已下发的字段，不猜）：
          * 已升级过（`upgraded` 为真）→ 返回极低分（不能再升，排除）
          * 诅咒牌 → 极低分（升它们没有意义）
          * 基础打击/防御 → 低分（升一次收益极小，是"浪费升级"的典型）
          * 高费牌 → 加分（升级降费收益大，费用越高收益越大）
          * 抽牌/能量/充能/能力等引擎牌 → 加分（长期价值高）

        ★★ 实机自查修掉的三个缺陷（2026-10-18，`_selfaudit_upgrade.py` 复现）：
          1. **不能照抄删牌的关键词**。删牌用 `['status','状态']` 判"该删"是合理的
             （状态牌确实该删），但**升级**若也这么判，就会把效果文本里含"状态"
             一词的**好卡**（如「巨像：使敌人陷入易伤状态」「痛击：施加易伤状态」）
             全打成 −500 而排到最后。实机样本已复现：巨像/痛击/头槌 均被误判。
             ⇒ 这里**只认真正的诅咒牌**（`curse`/`诅咒`），**不看** status/状态。
          2. **"打击/防御" 不能用裸子串**。`"双重打击"`、`"剑柄打击"`、`"灰烬打击"`
             都含"打击"，但它们是好卡；一律 −60 会**把好卡当基础牌降权**。
             ⇒ 改成**名称精确匹配**基础牌名（打击/防御/Defend/Strike + 可选 "+"）。
          3. **"能量/充能" 也不该裸匹配效果文本**。`"消耗 1 点能量"` 这种
             普通效果会被当成引擎牌加分。⇒ 只在**名称**里判引擎属性，不扫描述。
        """
        if bool(card.get("upgraded")):
            # 已经升过，不能再升。给最低分让它排在最后（而不是直接删掉，
            # 以便所有卡都升过时仍能选出一张、不发生死锁）。
            return -1000
        name = str(card.get("name") or "").strip()
        name_l = name.lower()
        # 只认真诅咒。★ 不看 status/状态 —— 效果文本里的"状态"是正常机制词。
        text_for_curse = " ".join([
            name,
            str(card.get("card_type") or card.get("type") or ""),
            str(card.get("rarity") or ""),
        ]).lower()
        if "curse" in text_for_curse or "诅咒" in text_for_curse:
            return -500
        # ★ 基础牌判定用**名称精确匹配**，避免 "双重打击"/"剑柄打击" 被误伤。
        base_names = {"打击", "防御", "strike", "defend", "defend_ironclad",
                      "strike_ironclad", "打击+", "防御+"}
        is_base = name_l in base_names or name_l.rstrip("+").strip() in base_names
        score = -60 if is_base and ("打击" in name or "strike" in name_l) else (
            -45 if is_base else 20)
        # 引擎类：★ 只看**名称**，不扫描述（避免"消耗1点能量"被当成引擎牌）。
        if any(token in name_l for token in ["draw", "skim", "coolheaded", "抽牌", "过牌"]):
            score += 40
        if any(token in name_l for token in ["energy", "turbo", "能量"]):
            score += 50
        if any(token in name_l for token in ["focus", "orb", "电球", "充能"]):
            score += 45
        # ★降费收益：费用越高，升级减费赚得越多。读 mod 已给的 energy_cost。
        cost = self._safe_int(card.get("energy_cost"))
        if cost is None:
            cost = self._safe_int(card.get("cost"))
        if isinstance(cost, int) and cost >= 2:
            score += 15 * min(cost, 3)
        # 稀有度：越稀有通常越值得投资。
        rarity = str(card.get("rarity") or "").lower()
        if any(token in rarity for token in ["rare", "稀有"]):
            score += 25
        elif any(token in rarity for token in ["uncommon", "罕见"]):
            score += 12
        return score
