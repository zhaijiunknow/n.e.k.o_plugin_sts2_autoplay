from __future__ import annotations

from typing import Any

from .planner_common import (
    _DECK_AOE_TOKENS,
    _DECK_ATTACK_TYPE_TOKENS,
    _DECK_BASIC_CARD_TOKENS,
    _DECK_CURSE_TYPE_TOKENS,
    _DECK_DRAW_TOKENS,
    _DECK_STATUS_TOKENS,
    _POTION_AOE_TOKENS,
    _POTION_BURST_TOKENS,
    _POTION_CATEGORY_LABELS,
    _POTION_CATEGORY_PRIORITY,
    _POTION_CLEANSE_TOKENS,
    _POTION_DEFENSE_TOKENS,
    _POTION_DRAW_TOKENS,
    _POTION_HEAL_TOKENS,
    _RELIC_EFFECT_DIM_TOKENS,
    _RELIC_HARSH_TOKENS,
    _RELIC_STRONG_TOKENS,
    _RELIC_SURVIVAL_TOKENS,
    SHOP_AOE_TARGET_UNITS,
    SHOP_CARDS_PER_TURN,
    SHOP_COST_PENALTY_HALF_BONUS,
    SHOP_COST_PENALTY_SCALE,
    SHOP_COVERAGE_MATURE_RATIO,
    SHOP_DIM_AOE,
    SHOP_DIM_DEFENSE,
    SHOP_DIM_DRAW,
    SHOP_DIM_ENERGY,
    SHOP_DIM_LABELS,
    SHOP_DIM_OUTPUT,
    SHOP_DIM_QUALITY,
    SHOP_DIMENSIONS,
    SHOP_DRAW_CARD_CREDIT,
    SHOP_DRAW_SIZE_BASE,
    SHOP_DRAW_SIZE_SPAN,
    SHOP_ELITE_POTION_BOOST,
    SHOP_ELITE_POTION_NEEDS,
    SHOP_ELITE_THRESHOLD_DELTA,
    SHOP_GOLD_FLOOR,
    SHOP_GOLD_POOR,
    SHOP_GOLD_RICH,
    SHOP_MATURE_THRESHOLD_DELTA,
    SHOP_POTION_BASELINE_FIT,
    SHOP_POTION_COVERED_FIT,
    SHOP_POTION_NEEDED_FIT,
    SHOP_POTION_PRICE_FALLBACK,
    SHOP_QUALITY_BASIC_TARGET,
    SHOP_QUALITY_CURSE_TARGET,
    SHOP_RELIC_CONDITION_PENALTY,
    SHOP_RELIC_KEYWORD_BONUS,
    SHOP_RELIC_KEYWORD_BONUS_CAP,
    SHOP_RELIC_RARITY_BONUS,
    SHOP_SCORE_THRESHOLD,
    SHOP_STATUS_CARD_CREDIT,
    SHOP_TARGET_BLOCK_BASE,
    SHOP_TARGET_BLOCK_PER_FLOOR,
    SHOP_TARGET_DAMAGE_BASE,
    SHOP_TARGET_DAMAGE_PER_FLOOR,
    SHOP_THRESHOLD_MAX,
    SHOP_THRESHOLD_MIN,
    SHOP_THRESHOLD_POOR_DELTA,
    SHOP_THRESHOLD_RICH_DELTA,
    SHOP_UNKNOWN_RELIC_GAP_RATIO,
)


class PlannerShopMixin:
    """Internal responsibility mixin; initialized and composed by STS2HeuristicPlanner."""

    def _shop_affordable_items(self, summary_context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}

        def affordable(items: Any) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get("is_stocked", True) and item.get("enough_gold", True):
                    out.append(item)
            return out

        return (
            affordable(payload.get("shop_cards")),
            affordable(payload.get("shop_relics")),
            affordable(payload.get("shop_potions")),
        )

    # ---- 商店：统一评分池（牌组量化 × 覆盖度 × 商品量化）----------------------------------
    #
    # 规格 docs/shop_scoring.md。三个老入口（_preferred_shop_relic_index / _preferred_shop_potion_index
    # / _shop_best_card_index）在这里**收敛成一个** `_shop_preferred_purchase()`：卡 / 遗物 / 药水 /
    # 删牌进同一个池子比总分，取最高分；全部低于门槛就一分不花。
    # `_shop_best_card_index` 仍然保留（选牌屏/候选排序还在用它），但商店采购不再走它。

    def _shop_preferred_purchase(
        self,
        summary_context: dict[str, Any],
        preferences: dict[str, Any],
        snapshot: dict[str, Any] | None = None,
        available_actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """商店买什么：把可买的每一项打分，买**最高分**的；都不够高就什么都不买。

        算法（规格式伪代码）：

            可买分 = 补缺分(牌组缺口 − 遗物覆盖) × 情境加权 − 代价惩罚 − 重复惩罚
            药水分 = 药水缺口 × 精英加权 − 代价惩罚（栏位满则不评）
            遗物预算 = 金币 − 药水预留 − 底钱（不够就不买遗物）

        返回 ``None`` 表示"没什么可看的"（调用方照旧关门/推进）；返回带 ``action_type`` 的 dict 表示
        买它；返回只有 ``reason`` 的 dict 表示**评估过了但一分不花**（reason 会写进 PlannedOperation，
        玩家能在 last_decision_reason 里看到是 `shop_score_below_threshold`）。
        """
        run = self._shop_run_data(summary_context, snapshot)
        gold = run["gold"]
        floor = run["floor"]
        gaps = self._deck_gap_vector(run["deck"], current_hp=run["current_hp"], max_hp=run["max_hp"], floor=floor)
        coverage = self._relic_coverage(run["relics"])
        residual = {dim: max(0.0, gaps.get(dim, 0.0) - coverage.get(dim, 0.0)) for dim in SHOP_DIMENSIONS}
        elite_ahead = self._shop_elite_ahead(summary_context, snapshot)
        policy = self._shop_policy(preferences)
        base_threshold = self._shop_threshold(gold=gold, elite_ahead=elite_ahead, gaps=gaps, residual=residual, policy=policy)

        cards, relics, potions = self._shop_affordable_items(summary_context)
        occupied, slots = self._shop_potion_slots(run["potions"], potions)
        slots_full = slots > 0 and occupied >= slots
        owned_potion_categories = {self._potion_category(item) for item in run["potions"] if isinstance(item, dict) and item.get("potion_id")}
        needed_categories = set(SHOP_ELITE_POTION_NEEDS) if elite_ahead else {"defense", "burst", "aoe", "heal", "cleanse"}
        missing_categories = needed_categories - owned_potion_categories
        potion_boost = float(policy.get("elite_potion_boost", SHOP_ELITE_POTION_BOOST)) if elite_ahead else 1.0

        # 买遗物之前先扣掉"留给药水的钱"和"下一个商店的底钱"（规格「三、4」）。
        gold_floor = int(policy.get("gold_floor", SHOP_GOLD_FLOOR) or 0)
        cheapest_potion = min([self._safe_int(item.get("price")) or 0 for item in potions if isinstance(item, dict)] or [SHOP_POTION_PRICE_FALLBACK])
        potion_reserve = self._shop_potion_reserve(policy, elite_ahead=elite_ahead, empty_slots=max(0, slots - occupied), potion_price=cheapest_potion)
        if not potions and "potion_reserve_gold" not in policy:
            # 这家店**没有药水可买**：预留就没有意义（钱花不出去，下一次能不能碰到药水也不知道）。
            # 不为买不到的东西压着一笔钱，否则会白白错过大遗物。
            potion_reserve = 0
        relic_budget = gold - potion_reserve - gold_floor

        allowed = None
        if isinstance(available_actions, list):
            allowed = {str(action.get("type") or "").strip().lower() for action in available_actions if isinstance(action, dict)}
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(run["deck"])]

        entries: list[dict[str, Any]] = []
        skipped: list[str] = []

        def action_ok(action_type: str) -> bool:
            return allowed is None or action_type in allowed

        for relic in relics:
            index = self._safe_int(relic.get("index"))
            if index is None:
                continue
            if not action_ok("buy_relic"):
                continue
            if self._shop_relic_is_duplicate(relic, run["relics"]):
                # 重复惩罚：同名 / 同 relic_id **直接排除**（规格「二、通用部分」）。
                skipped.append(f"遗物「{self._shop_item_name(relic)}」→ 不买(原因=与已有遗物重复)")
                continue
            price = self._safe_int(relic.get("price")) or 0
            score, detail = self._score_shop_relic(relic, residual, gold=gold)
            entry = {
                "kind": "relic",
                "action_type": "buy_relic",
                "option_index": index,
                "name": self._shop_item_name(relic),
                "price": price,
                "score": score,
                "detail": detail,
                "blocked": "",
            }
            if price > relic_budget:
                # 规格「三、4」：遗物预算 = 金币 − 药水预留 − 底钱。不够就不买遗物。
                entry["blocked"] = (
                    f"留给药水 {potion_reserve} + 下一个商店的底钱 {gold_floor}：遗物预算={relic_budget} < 价格 {price}"
                )
            entries.append(entry)

        for card in cards:
            index = self._safe_int(card.get("index"))
            if index is None or not action_ok("buy_card"):
                continue
            price = self._safe_int(card.get("price")) or 0
            score, detail = self._score_shop_card(card, residual, gold=gold, deck_policy=deck_policy, archetype_tags=archetype_tags, deck=run["deck"])
            entries.append(
                {
                    "kind": "card",
                    "action_type": "buy_card",
                    "option_index": index,
                    "name": self._shop_item_name(card),
                    "price": price,
                    "score": score,
                    "detail": detail,
                    "blocked": "",
                }
            )

        if not slots_full:
            for potion in potions:
                index = self._safe_int(potion.get("index"))
                if index is None or not action_ok("buy_potion"):
                    continue
                price = self._safe_int(potion.get("price")) or 0
                score, detail = self._score_shop_potion(
                    potion,
                    missing_categories=missing_categories,
                    owned_categories=owned_potion_categories,
                    boost=potion_boost,
                    gold=gold,
                )
                entries.append(
                    {
                        "kind": "potion",
                        "action_type": "buy_potion",
                        "option_index": index,
                        "name": self._shop_item_name(potion),
                        "price": price,
                        "score": score,
                        "detail": detail,
                        "blocked": "",
                    }
                )
        else:
            skipped.append(f"药水栏位已满（{occupied}/{slots}）→ 整类不评")

        removal = self._shop_card_removal(summary_context)
        if removal is not None and action_ok("remove_card_at_shop"):
            price = self._safe_int(removal.get("price")) or 0
            score, detail = self._score_shop_removal(residual, gold=gold)
            entries.append(
                {
                    "kind": "remove",
                    "action_type": "remove_card_at_shop",
                    "option_index": None,
                    "name": "删牌服务",
                    "price": price,
                    "score": score,
                    "detail": detail,
                    "blocked": "",
                }
            )

        best = None
        # 代价惩罚统一在这里扣（规格：`分数 = 补缺分 × 情境加权 − 代价惩罚`）：每一件都按
        # `价格 / 当前金币` 扣，花掉一半以上的钱再重罚一次。放在一处算，免得四种商品各写一遍还写歪。
        for entry in entries:
            entry["penalty"] = self._shop_cost_penalty(int(entry.get("price") or 0), gold)
            entry["score"] = entry["score"] - entry["penalty"]
        for entry in entries:
            if entry["blocked"]:
                continue
            if best is None or entry["score"] > best["score"]:
                best = entry
        best_blocked = None
        for entry in entries:
            if not entry["blocked"]:
                continue
            if best_blocked is None or entry["score"] > best_blocked["score"]:
                best_blocked = entry

        live = getattr(self, "_livestream", None)
        if (live is not None and live.enabled and live.profile != "focused"
                and not records and not self._guidance_override_from_payload(summary_context)
                and best is not None and best["kind"] in {"card", "relic"}
                and best["score"] >= self._entry_threshold(
                    base_threshold, entry=best, elite_ahead=elite_ahead, policy=policy)):
            eligible = [(i, entry["score"]) for i, entry in enumerate(entries)
                        if not entry["blocked"] and entry["kind"] == best["kind"]
                        and entry["score"] >= self._entry_threshold(
                            base_threshold, entry=entry, elite_ahead=elite_ahead, policy=policy)]
            picked = live.pick_ranked(eligible, key=[self._shop_visit_signature(summary_context), entries])
            if picked is not None:
                best = entries[picked]

        threshold = self._entry_threshold(base_threshold, entry=best, elite_ahead=elite_ahead, policy=policy)
        decision: dict[str, Any] = {"action_type": None, "score": None, "threshold": threshold, "reason": "shop_close"}
        if best is None:
            if best_blocked is not None:
                decision.update(reason="shop_score_below_threshold", blocked=best_blocked["blocked"], score=best_blocked["score"])
            reason = str(decision["reason"])
        elif best["score"] >= threshold:
            decision.update(
                action_type=str(best["action_type"]),
                kwargs={"option_index": best["option_index"]} if best["option_index"] is not None else {},
                score=best["score"],
                kind=best["kind"],
                name=best["name"],
                confidence={"relic": 0.72, "card": 0.7, "potion": 0.66, "remove": 0.7}.get(str(best["kind"]), 0.7),
                reason="shop_scored_buy",
            )
            reason = "shop_scored_buy"
        else:
            decision.update(reason="shop_score_below_threshold", score=best["score"])
            reason = "shop_score_below_threshold"

        self._log_shop_decision(
            summary_context,
            entries=entries,
            skipped=skipped,
            best=best,
            best_blocked=best_blocked,
            threshold=threshold,
            base_threshold=base_threshold,
            policy=policy,
            gold=gold,
            elite_ahead=elite_ahead,
            relic_budget=relic_budget,
            potion_reserve=potion_reserve,
            missing_categories=missing_categories,
            reason=reason,
        )
        return decision

    def _shop_run_data(self, summary_context: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """商店决策要用的局面数据：金币 / 血量 / 层数 / 牌组 / 已有遗物 / 已有药水。

        为什么不能只读 `summary_context.payload`：`_build_shop_context` 里那份 `deck` 取的是
        `raw_state["deck"]`，而 mod 的 /state **没有顶层 deck** —— 牌组在 `run.deck[]` 里、已有遗物在
        `run.relics[]`（只有它带 description）、药水栏位在 `run.potions[]`（只有它带 occupied）。
        所以这里一律以 `raw_state.run.*` 为权威来源；payload 里那几项（gold/hp/floor）优先，因为它们
        已经在别的分支里用过、口径一致。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}

        def first_int(*values: Any) -> int:
            for value in values:
                parsed = self._safe_int(value)
                if parsed is not None:
                    return parsed
            return 0

        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        if not (deck.get("cards") if isinstance(deck.get("cards"), list) else []):
            deck = {"cards": run.get("deck")} if isinstance(run.get("deck"), list) else {}
        relics = payload.get("relics") if isinstance(payload.get("relics"), list) else []
        if not relics:
            relics = run.get("relics") if isinstance(run.get("relics"), list) else []
        potions = payload.get("potions") if isinstance(payload.get("potions"), list) else []
        if not potions:
            potions = run.get("potions") if isinstance(run.get("potions"), list) else []
        return {
            "gold": first_int(payload.get("gold"), run.get("gold")),
            "current_hp": first_int(payload.get("current_hp"), run.get("current_hp")),
            "max_hp": first_int(payload.get("max_hp"), run.get("max_hp")),
            "floor": first_int(payload.get("floor"), run.get("floor"), snapshot.get("floor") if isinstance(snapshot, dict) else None),
            "deck": deck if isinstance(deck, dict) else {},
            "relics": [item for item in relics if isinstance(item, dict)],
            "potions": [item for item in potions if isinstance(item, dict)],
        }

    @staticmethod
    def _clamp01(value: float) -> float:
        return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)

    @staticmethod
    def _shop_item_name(item: dict[str, Any]) -> str:
        for field in ("name", "card_id", "relic_id", "potion_id"):
            text = str(item.get(field) or "").strip()
            if text:
                return text
        return "?"

    @staticmethod
    def _item_text(item: dict[str, Any]) -> str:
        return " ".join(
            str(item.get(field) or "")
            for field in ("name", "card_id", "relic_id", "potion_id", "description", "rules_text", "resolved_rules_text", "effect", "usage", "card_type", "type")
        ).lower()

    # ---- 牌组量化（缺口向量）--------------------------------------------------------------

    def _deck_gap_vector(
        self,
        deck: dict[str, Any],
        *,
        current_hp: int = 0,
        max_hp: int = 0,
        floor: int = 0,
    ) -> dict[str, float]:
        """牌组量化 → 缺口向量（六维 0..1，越大越缺）。规格「一、牌组量化」那张表。

        六个维度：输出 / 格挡生存 / 抽牌循环 / 费用曲线 / 状态群伤 / 牌组质量。
        **全 0 表示"什么都不缺"**（那就别买）。量法都只用 mod 已给的 deck[] 字段
        （card_type / upgraded / energy_cost / dynamic_values / resolved_rules_text），不额外猜。

        为什么按"每回合能打出多少"而不是"牌组总伤害"：牌组越大总伤害越高，但一回合只抽 5 张、只打
        3 张 —— 用总量算会让"堆了一堆打击"的胖牌组显得很强。所以取**平均每张牌的数值 × 每回合出牌数**。
        """
        cards = [
            card
            for card in (deck.get("cards") if isinstance(deck, dict) and isinstance(deck.get("cards"), list) else [])
            if isinstance(card, dict)
        ]
        if not cards:
            # 读不到牌组就按"没缺口"处理：宁可这一家不买，也不要凭猜乱花钱（玩家的原话就是这个）。
            return {dim: 0.0 for dim in SHOP_DIMENSIONS}
        size = len(cards)
        damage_total = 0.0
        block_total = 0.0
        draw_cards = 0
        aoe_cards = 0
        status_cards = 0
        zero_cost = 0
        cost_sum = 0.0
        basic_unupgraded = 0
        curses = 0
        for card in cards:
            text = self._item_text(card)
            card_type = str(card.get("card_type") or card.get("type") or "").lower()
            damage_total += self._deck_card_damage(card)
            block_total += self._card_block(card)
            if any(token in text for token in _DECK_DRAW_TOKENS):
                draw_cards += 1
            if any(token in text for token in _DECK_AOE_TOKENS):
                aoe_cards += 1
            if any(token in text for token in _DECK_STATUS_TOKENS):
                status_cards += 1
            cost = self._card_energy_cost(card)
            cost_sum += cost
            if cost <= 0:
                zero_cost += 1
            name_text = f"{card.get('name') or ''} {card.get('card_id') or ''}".lower()
            if not bool(card.get("upgraded")) and any(token in name_text for token in _DECK_BASIC_CARD_TOKENS):
                basic_unupgraded += 1
            if any(token in card_type for token in _DECK_CURSE_TYPE_TOKENS):
                curses += 1

        per_turn_damage = (damage_total / size) * SHOP_CARDS_PER_TURN
        per_turn_block = (block_total / size) * SHOP_CARDS_PER_TURN
        target_damage = SHOP_TARGET_DAMAGE_BASE + SHOP_TARGET_DAMAGE_PER_FLOOR * max(0, floor)
        target_block = SHOP_TARGET_BLOCK_BASE + SHOP_TARGET_BLOCK_PER_FLOOR * max(0, floor)
        gap_output = self._clamp01((target_damage - per_turn_damage) / target_damage)
        gap_defense = self._clamp01((target_block - per_turn_block) / target_block)
        if max_hp > 0 and current_hp > 0 and (current_hp / max_hp) < 0.6:
            # 血量低且格挡薄 → 缺口高（规格那张表的判据）。
            gap_defense = min(1.0, gap_defense + 0.2)

        # 牌组 > 14 张才开始嫌循环不动；有抽牌手段就按张数抹平（每 2 张抽牌牌抹掉一整档）。
        gap_draw = self._clamp01((size - SHOP_DRAW_SIZE_BASE) / SHOP_DRAW_SIZE_SPAN)
        gap_draw *= 1.0 - min(1.0, draw_cards / SHOP_DRAW_CARD_CREDIT)

        avg_cost = cost_sum / size
        zero_share = zero_cost / size
        gap_energy = self._clamp01((avg_cost - 1.2) / 0.8)
        gap_energy *= 1.0 - 0.5 * min(1.0, zero_share / 0.25)

        # 状态/群伤：群伤算 1 张、单体状态算半张；这个维度封顶 0.7（规格说这种情境是"缺口中"）。
        status_units = aoe_cards + status_cards * SHOP_STATUS_CARD_CREDIT
        gap_aoe = 0.7 * self._clamp01(1.0 - status_units / SHOP_AOE_TARGET_UNITS)

        gap_quality = self._clamp01(
            0.5 * min(1.0, basic_unupgraded / SHOP_QUALITY_BASIC_TARGET)
            + 0.5 * min(1.0, curses / SHOP_QUALITY_CURSE_TARGET)
        )
        return {
            SHOP_DIM_OUTPUT: round(gap_output, 4),
            SHOP_DIM_DEFENSE: round(gap_defense, 4),
            SHOP_DIM_DRAW: round(gap_draw, 4),
            SHOP_DIM_ENERGY: round(gap_energy, 4),
            SHOP_DIM_AOE: round(gap_aoe, 4),
            SHOP_DIM_QUALITY: round(gap_quality, 4),
        }

    def _deck_card_damage(self, card: dict[str, Any]) -> float:
        """牌组里一张牌"一回合能打出多少伤害"（含多段：Damage × Repeat/hit_count）。"""
        damage = self._card_damage(card)
        if damage <= 0:
            return 0.0
        repeat = self._reward_dynamic_value(card, "Repeat")
        hits = repeat or self._safe_int(card.get("hit_count")) or 1
        return float(damage * max(1, hits))

    # ---- 已有遗物 / 已有药水的覆盖度 -------------------------------------------------------

    def _relic_effect_dims(self, relic: dict[str, Any]) -> set[str]:
        """遗物 → 它覆盖哪几维缺口（规格「一之二」的类别表：输出/防御/能量/抽牌/生存/经济/条件）。

        生存/回复归到「格挡/生存」这一维（牌组量化里它就叫这个名字）；经济类**没有**对应的缺口维度，
        所以不进这个集合（钱够时"加金币"不补任何缺口 —— 这也是玩家说的"评估一下那些是真正需要的"）。
        """
        text = self._item_text(relic)
        dims: set[str] = set()
        for dim, tokens in _RELIC_EFFECT_DIM_TOKENS.items():
            if any(token in text for token in tokens):
                dims.add(dim)
        if any(token in text for token in _RELIC_SURVIVAL_TOKENS):
            dims.add(SHOP_DIM_DEFENSE)
        return dims

    def _relic_coverage(self, relics: list[dict[str, Any]]) -> dict[str, float]:
        """已有遗物 → 覆盖度向量：同类累加但**边际递减** `覆盖度 = 1 − 0.6^n`（规格「一之二」）。

        为什么要边际递减：手里已经有"力量+2"时，再买一件加力量的遗物几乎没有边际收益 ——
        `1-0.6^1 = 0.4`（第 1 件几乎补满 40% 缺口）、`1-0.6^2 = 0.64`（第 2 件只补 24%）、
        第 3 件起基本无价值（0.784）。一件遗物横跨多类别就按类别**分摊**（半个计入每一类）。
        """
        counts: dict[str, float] = {dim: 0.0 for dim in SHOP_DIMENSIONS}
        for relic in relics:
            if not isinstance(relic, dict):
                continue
            dims = self._relic_effect_dims(relic)
            if not dims:
                continue
            share = 1.0 / len(dims)
            for dim in dims:
                counts[dim] += share
        return {dim: (0.0 if n <= 0 else round(1.0 - 0.6 ** n, 4)) for dim, n in counts.items()}

    def _shop_relic_is_duplicate(self, relic: dict[str, Any], owned: list[dict[str, Any]]) -> bool:
        """这把遗物是不是已经有一件了（同 relic_id 或同名）—— 重复惩罚是"直接排除"，不是降分。"""
        relic_id = str(relic.get("relic_id") or "").strip().lower()
        name = str(relic.get("name") or "").strip().lower()
        for item in owned:
            if not isinstance(item, dict):
                continue
            if relic_id and relic_id == str(item.get("relic_id") or "").strip().lower():
                return True
            if name and name == str(item.get("name") or "").strip().lower():
                return True
        return False

    def _potion_category(self, item: dict[str, Any]) -> str:
        """药水 → 用途类别（单体爆发 / 群伤 / 减伤 / 回血 / 清状态 / 抽牌 / 其它）。

        判定顺序是刻意的：`EXPLOSIVE_POTION`（对所有敌人造成伤害）必须落到"群伤"，不能被"伤害"先判成
        "单体爆发"；`BLOCK_POTION` 先于"回血"（它只给格挡）。
        """
        text = self._item_text(item)
        for tokens, category in (
            (_POTION_AOE_TOKENS, "aoe"),
            (_POTION_DEFENSE_TOKENS, "defense"),
            (_POTION_HEAL_TOKENS, "heal"),
            (_POTION_CLEANSE_TOKENS, "cleanse"),
            (_POTION_BURST_TOKENS, "burst"),
            (_POTION_DRAW_TOKENS, "draw"),
        ):
            if any(token in text for token in tokens):
                return category
        return "other"

    def _shop_potion_slots(self, run_potions: list[dict[str, Any]], shop_potions: list[dict[str, Any]]) -> tuple[int, int]:
        """(已占用, 总栏位)。

        规格里写的是 `shop.potions[].occupied`，但 mod 的 ShopPotionPayload **没有这个字段**
        （只有 index/potion_id/name/rarity/usage/price/is_stocked/enough_gold —— 而且栏位满时它直接把
        `enough_gold` 算成 false）。所以以 `run.potions[]`（每格都带 occupied/potion_id）为准，
        shop 那份只在 run 读不到时兜底；两边都读不到就返回 (0, 0) = "未知"，此时**不**因此拦下药水。
        """
        slots = [item for item in run_potions if isinstance(item, dict)]
        if slots:
            occupied = sum(1 for item in slots if item.get("occupied", bool(item.get("potion_id"))))
            return occupied, len(slots)
        shop_slots = [item for item in shop_potions if isinstance(item, dict) and "occupied" in item]
        if shop_slots:
            return sum(1 for item in shop_slots if item.get("occupied")), len(shop_slots)
        return 0, 0

    # ---- 情境与门槛 -----------------------------------------------------------------------

    def _shop_policy(self, preferences: dict[str, Any]) -> dict[str, Any]:
        """玩家自己配过的商店策略（`instruction_type == "shop_policy"`）。

        与 `route_policy` / `deck_policy` 一个路子：没配过就用模块常量的保守默认。
        可配键就是规格「五、配置项」那几个：score_threshold / elite_potion_boost /
        potion_reserve_gold / gold_floor（外加战斗用药水的 potion_hp_loss_threshold）。
        """
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            if str(value.get("instruction_type") or "") != "shop_policy":
                continue
            return value
        return {}

    def _shop_elite_ahead(self, summary_context: dict[str, Any], snapshot: dict[str, Any] | None = None) -> bool:
        """前方有没有精英（决定药水加权、门槛、遗物预算）。

        玩家口径：「买两瓶药可以冲精英」。麻烦在于**商店界面拿不到地图**：mod 的 `BuildMapPayload`
        一离开地图屏就返回 null，所以进店时 `raw_state.map` 是空的。于是这里按三级取：
        1. payload 里显式给了 `elite_ahead` / `route_elite_ahead`（策略/测试注入口）；
        2. 快照里恰有地图（选路那一刻、或以后 mod 改了行为）→ 现算；
        3. 否则用**选路时记下的结论**（`_note_route_choice`，由 evaluate_map_route 那一套算出来的）。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        for field in ("elite_ahead", "route_elite_ahead"):
            flag = payload.get(field)
            if isinstance(flag, bool):
                return flag
        graph = self._map_graph_from_snapshot(snapshot)
        if graph:
            raw_state = snapshot.get("raw_state") if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict) else {}
            game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
            current = game_map.get("current_node") if isinstance(game_map.get("current_node"), dict) else None
            if current is not None:
                return self._route_elite_lookahead(graph, current)
        return bool(self._route_elite_ahead)

    def _shop_threshold(
        self,
        *,
        gold: int,
        elite_ahead: bool,
        gaps: dict[str, float],
        residual: dict[str, float],
        policy: dict[str, Any],
    ) -> float:
        """统一门槛（随情境浮动，规格「三、3」）。

        - 前方有精英 → 药水门槛下调（只在药水那一项上减，见 `_entry_threshold`）；
        - 金币很多（>300）→ 门槛略降，避免"攒着不花"；
        - 金币很少（<100）→ 门槛升高，优先留钱；
        - **遗物覆盖度整体很高（构筑已成型）→ 门槛上调**，更倾向留钱到下一个商店。
        """
        try:
            threshold = float(policy.get("score_threshold", SHOP_SCORE_THRESHOLD))
        except (TypeError, ValueError):
            threshold = SHOP_SCORE_THRESHOLD
        if gold > SHOP_GOLD_RICH:
            threshold += SHOP_THRESHOLD_RICH_DELTA
        elif gold < SHOP_GOLD_POOR:
            threshold += SHOP_THRESHOLD_POOR_DELTA
        total_gap = sum(gaps.values())
        total_residual = sum(residual.values())
        if total_gap > 0 and (total_gap - total_residual) / total_gap >= SHOP_COVERAGE_MATURE_RATIO:
            threshold += SHOP_MATURE_THRESHOLD_DELTA
        return max(SHOP_THRESHOLD_MIN, min(SHOP_THRESHOLD_MAX, threshold))

    def _entry_threshold(self, base_threshold: float, *, entry: dict[str, Any] | None, elite_ahead: bool, policy: dict[str, Any]) -> float:
        """单项门槛：只有药水会在"前方有精英"时下调（规格「三、3」第一句）。"""
        if not elite_ahead or entry is None or entry.get("kind") != "potion":
            return base_threshold
        try:
            delta = float(policy.get("elite_threshold_delta", SHOP_ELITE_THRESHOLD_DELTA))
        except (TypeError, ValueError):
            delta = SHOP_ELITE_THRESHOLD_DELTA
        return max(SHOP_THRESHOLD_MIN, base_threshold + delta)

    def _shop_potion_reserve(self, policy: dict[str, Any], *, elite_ahead: bool, empty_slots: int, potion_price: int) -> int:
        """买遗物前要给"药水"预留多少钱（规格「五」：默认 冲精英时=补满栏位的钱）。"""
        configured = policy.get("potion_reserve_gold")
        parsed = self._safe_int(configured)
        if parsed is not None:
            return max(0, parsed)
        if not elite_ahead:
            return 0
        return max(0, empty_slots) * max(0, potion_price)

    @staticmethod
    def _shop_cost_penalty(price: int, gold: int) -> float:
        """代价惩罚：`价格 / 当前金币`（花掉一半以上的钱要重罚）。"""
        if gold <= 0:
            return SHOP_COST_PENALTY_SCALE * 2 + SHOP_COST_PENALTY_HALF_BONUS
        ratio = max(0.0, float(price) / float(gold))
        penalty = ratio * SHOP_COST_PENALTY_SCALE
        if ratio >= 0.5:
            penalty += SHOP_COST_PENALTY_HALF_BONUS
        return penalty

    # ---- 单件商品的补缺分 -----------------------------------------------------------------

    def _score_shop_relic(self, relic: dict[str, Any], residual: dict[str, float], *, gold: int) -> tuple[float, str]:
        """遗物：与缺口的匹配度 + 稀有度 + 关键词 − 条件苛刻降权（规格「二、分类细则」）。

        读不出效果类别时按"最缺那一维 × 0.5"折算：mod 的商店遗物 payload 只有
        `index/relic_id/name/rarity/price/is_stocked/enough_gold`（**没有 description**，只有已有遗物
        `run.relics[]` 才带 description），所以中文名字/英文 id 认不出来是常态。这个先验让"牌组真的
        缺东西"时还能买遗物，牌组没事时（缺口全 0）一律不买 —— 正是玩家要的那条线。
        折算只看**效果维度**（输出/格挡/抽牌/费用/群伤），不算"牌组质量"——遗物基本不会帮你升级或删牌。
        """
        text = self._item_text(relic)
        dims = self._relic_effect_dims(relic)
        if dims:
            matched = max(residual.get(dim, 0.0) for dim in dims)
            detail = "补缺=" + " ".join(f"{SHOP_DIM_LABELS[dim]}{residual.get(dim, 0.0):.2f}" for dim in sorted(dims))
        else:
            effect_gaps = [residual.get(dim, 0.0) for dim in SHOP_DIMENSIONS if dim != SHOP_DIM_QUALITY]
            matched = SHOP_UNKNOWN_RELIC_GAP_RATIO * max(effect_gaps or [0.0])
            detail = f"补缺={matched:.2f}(读不出效果类别→最缺维度×{SHOP_UNKNOWN_RELIC_GAP_RATIO})"
        rarity = str(relic.get("rarity") or "").strip().lower()
        rarity_bonus = SHOP_RELIC_RARITY_BONUS.get(rarity, 6.0)
        keyword_hits = sum(1 for token in _RELIC_STRONG_TOKENS if token in text)
        keyword_bonus = min(SHOP_RELIC_KEYWORD_BONUS_CAP, SHOP_RELIC_KEYWORD_BONUS * keyword_hits)
        harsh = any(token in text for token in _RELIC_HARSH_TOKENS)
        score = matched * 100.0 + rarity_bonus + keyword_bonus - (SHOP_RELIC_CONDITION_PENALTY if harsh else 0.0)
        detail += " 稀有%s=%+.0f" % (rarity or "?", rarity_bonus)
        if keyword_bonus:
            detail += " 关键词=%+.0f" % keyword_bonus
        if harsh:
            detail += " 条件苛刻=%+.0f" % (-SHOP_RELIC_CONDITION_PENALTY)
        return score, detail

    def _score_shop_card(
        self,
        card: dict[str, Any],
        residual: dict[str, float],
        *,
        gold: int,
        deck_policy: dict[str, Any],
        archetype_tags: list[str],
        deck: dict[str, Any],
    ) -> tuple[float, str]:
        """卡牌：沿用 `_score_reward_card` 的评分，接进统一池子（规格「二、分类细则」）。

        折算方式：原始分 / `_SHOP_CARD_FIT_SCALE`(60) = 补缺分，再乘"这张卡命中的那一维有多缺"
        （`0.25 + 0.75 × 缺口`）。没命中的牌（诅咒/状态，或读不出效果）缺口算 0 → 至多 0.25 倍，
        于是**牌组没缺口就不买卡**（规格「三、5」），而老口径"25 分以上就买"在便宜/真缺时仍然成立。
        """
        raw = float(self._score_reward_card(card, deck_policy, archetype_tags))
        # `_SHOP_ANY_CARD_SCORE` 的老语义是"原始分 1 以上就凑合买"；新口径里它成了**入库下限**：
        # 连 1 分都不到的卡（读不出效果、策略也不认）补缺分为 0，于是只剩代价惩罚 → 永远低于门槛。
        if raw < self._SHOP_ANY_CARD_SCORE:
            fit = 0.0
        else:
            fit = self._clamp01(raw / self._SHOP_CARD_FIT_SCALE) if self._SHOP_CARD_FIT_SCALE > 0 else 0.0
        dims = self._card_effect_dims(card)
        gap_fit = max([residual.get(dim, 0.0) for dim in dims], default=0.0)
        score = fit * (0.25 + 0.75 * gap_fit) * 100.0
        if dims:
            dim_label = "/".join(SHOP_DIM_LABELS[dim] for dim in sorted(dims))
            gap_detail = f"最高{gap_fit:.2f}({dim_label})"
        else:
            gap_detail = "未命中维度0.00"
        detail = f"补缺={gap_detail} 原始分={raw:.0f}"
        if self._is_duplicate_heavy_card(card, deck):
            # 卡牌与牌组里已有同名过多：降分（规格「二、通用部分」的重复惩罚）。
            score *= 0.5
            detail += " 同名过多×0.5"
        return score, detail

    def _card_effect_dims(self, card: dict[str, Any]) -> set[str]:
        """这张卡补的是哪几维（攻击→输出、格挡→格挡、抽牌→抽牌、能量→费用、群伤/状态→群伤/状态）。

        诅咒/状态牌**不补**任何维度（它们只会把牌组拖坏）——所以永远不该在商店里买。
        """
        text = self._item_text(card)
        card_type = str(card.get("card_type") or card.get("type") or "").lower()
        if any(token in card_type for token in _DECK_CURSE_TYPE_TOKENS):
            return set()
        dims: set[str] = set()
        if any(token in (card_type + " " + text) for token in _DECK_ATTACK_TYPE_TOKENS) or self._deck_card_damage(card) > 0:
            dims.add(SHOP_DIM_OUTPUT)
        if self._card_block(card) > 0 or any(token in text for token in ("block", "格挡", "护盾")):
            dims.add(SHOP_DIM_DEFENSE)
        if any(token in text for token in _DECK_DRAW_TOKENS):
            dims.add(SHOP_DIM_DRAW)
        if self._reward_dynamic_value(card, "Energy") > 0 or any(token in text for token in ("能量", "energy")):
            dims.add(SHOP_DIM_ENERGY)
        if any(token in text for token in _DECK_AOE_TOKENS) or any(token in text for token in _DECK_STATUS_TOKENS):
            dims.add(SHOP_DIM_AOE)
        return dims

    def _is_duplicate_heavy_card(self, card: dict[str, Any], deck: dict[str, Any]) -> bool:
        """牌组里同名牌是否已经很多（>=3 张）：重复惩罚（规格「卡牌与牌组里已有同名过多降分」）。"""
        name = str(card.get("name") or card.get("card_id") or "").strip().lower()
        if not name:
            return False
        cards = deck.get("cards") if isinstance(deck.get("cards"), list) else []
        same = sum(
            1
            for item in cards
            if isinstance(item, dict) and str(item.get("name") or item.get("card_id") or "").strip().lower() == name
        )
        return same >= 3

    def _score_shop_potion(
        self,
        potion: dict[str, Any],
        *,
        missing_categories: set[str],
        owned_categories: set[str],
        boost: float,
        gold: int,
    ) -> tuple[float, str]:
        """药水：按"还缺哪几类"评分（规格「一之二」「二」）。

        - 命中"冲精英还缺的那一类" → 高分；**已经有的那一类** → 低分（栏位有限，重复买同一类不值）；
        - 前方有精英时整体加权上调（这就是"买两瓶药冲精英"）；
        - 栏位满**整类不评**（调用方在 `_shop_preferred_purchase` 里就跳过了）。
        """
        category = self._potion_category(potion)
        priority = _POTION_CATEGORY_PRIORITY.get(category, 0.3)
        if category in missing_categories:
            fit = SHOP_POTION_NEEDED_FIT * priority
            note = "还缺这一类"
        elif category in owned_categories:
            fit = SHOP_POTION_COVERED_FIT * priority
            note = "同类已有"
        else:
            fit = SHOP_POTION_BASELINE_FIT.get(category, 0.2)
            note = "非冲精英必需品"
        score = fit * 100.0 * max(0.1, boost)
        return score, f"用途={_POTION_CATEGORY_LABELS.get(category, category)}({note}) 补缺={fit:.2f} 情境={boost:.2f}"

    def _score_shop_removal(self, residual: dict[str, float], *, gold: int) -> tuple[float, str]:
        """删牌服务：分数来自"牌组质量"缺口（未升级的打击/防御、诅咒/状态牌的张数）。"""
        gap = residual.get(SHOP_DIM_QUALITY, 0.0)
        return gap * 100.0, f"补缺=质量{gap:.2f}"

    # ---- 日志 -----------------------------------------------------------------------------

    def _log_shop_decision(
        self,
        summary_context: dict[str, Any],
        *,
        entries: list[dict[str, Any]],
        skipped: list[str],
        best: dict[str, Any] | None,
        best_blocked: dict[str, Any] | None,
        threshold: float,
        base_threshold: float,
        policy: dict[str, Any],
        gold: int,
        elite_ahead: bool,
        relic_budget: int,
        potion_reserve: int,
        missing_categories: set[str],
        reason: str,
    ) -> None:
        """每件商品一行 + 一句结论（规格「四、日志与可观测」）。

        玩家口径要求"日志要说实话"：老实现给的 reason 是 `shop_high_value_relic`，而它其实只判断了
        "买得起"。这里把每项的分数构成、门槛、以及"没买时最高分是多少"都写清楚，方便事后调参。
        `_debug` 在本文件里是空函数，所以这些行必须走 info 级。
        """
        if self._logger is None:
            return
        kind_labels = {"relic": "遗物", "card": "卡牌", "potion": "药水", "remove": "删牌"}
        lines: list[str] = []
        for entry in entries:
            penalty = float(entry.get("penalty") or 0.0)
            own_threshold = self._entry_threshold(base_threshold, entry=entry, elite_ahead=elite_ahead, policy=policy)
            if entry["blocked"]:
                verdict = f"不买({entry['blocked']})"
            elif best is not None and entry is best and reason == "shop_scored_buy":
                verdict = "买"
            elif entry["score"] >= own_threshold:
                # 够门槛但不是最高分：**如实说**。（以前这里会被写成"低于门槛"，那是假日志。）
                verdict = f"不买(够门槛 {own_threshold:.1f}，但最高分是 {best['score']:.1f} 的{kind_labels.get(str(best['kind']), best['kind'])})"
            else:
                verdict = f"不买(低于门槛 {own_threshold:.1f})"
            lines.append(
                f"[sts2_shop] item={kind_labels.get(str(entry['kind']), str(entry['kind']))}「{entry['name']}」"
                f" score={entry['score']:.1f} ({entry['detail']} 价={entry['price']} 代价={-penalty:.1f}) → {verdict}"
            )
        lines.extend(f"[sts2_shop] {line}" for line in skipped)
        if best is not None and reason == "shop_scored_buy":
            lines.append(
                f"[sts2_shop] 最高分={best['score']:.1f}（{kind_labels.get(str(best['kind']), best['kind'])}「{best['name']}」）"
                f" ≥ 门槛 {threshold:.1f} → 买"
            )
        elif best is not None:
            lines.append(
                f"[sts2_shop] 最高分={best['score']:.1f}（{kind_labels.get(str(best['kind']), best['kind'])}「{best['name']}」）"
                f" < 门槛 {threshold:.1f} → 一分不花，钱留到下一个商店"
            )
        elif best_blocked is not None:
            lines.append(
                f"[sts2_shop] 最高分={best_blocked['score']:.1f} 但被预算拦下（{best_blocked['blocked']}）"
                f"：留给药水 {potion_reserve} + 底钱，遗物预算={relic_budget} → 一分不花"
            )
        else:
            lines.append("[sts2_shop] 没有可买项 → 一分不花")
        lines.append(
            f"[sts2_shop] 金币={gold} 门槛={threshold:.1f}(基础 {base_threshold:.1f}) 前方有精英={elite_ahead} "
            f"还缺药水类别={sorted(missing_categories) or '-'} 决定={reason}"
        )
        key = f"{self._shop_visit_signature(summary_context)}|" + "\n".join(lines)
        if key == self._shop_log_key:
            return
        self._shop_log_key = key
        try:
            for line in lines:
                self._logger.info(line)
        except Exception:
            pass

    def _shop_card_removal(self, summary_context: dict[str, Any]) -> dict[str, Any] | None:
        """删牌服务（`shop.card_removal`）：只有牌组质量缺口高才值得买。

        注意 `remove_card_at_shop` 动作**不接 option_index**；下一步的 `card_selection_remove` 屏由
        现有的 `_preferred_remove_option` 挑诅咒→打击/防御。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        shop = payload.get("shop") if isinstance(payload.get("shop"), dict) else {}
        removal = shop.get("card_removal") if isinstance(shop.get("card_removal"), dict) else None
        if removal is None:
            # 兜底：payload 里也可能把删牌服务铺平在顶层（`_build_shop_context` 一直在铺平别的字段）。
            flat = payload.get("card_removal")
            removal = flat if isinstance(flat, dict) else None
        if removal is None:
            return None
        if removal.get("used") is True or removal.get("available") is False:
            return None
        return removal

    def _shop_best_card_index(self, summary_context: dict[str, Any], preferences: dict[str, Any], min_score: int) -> int | None:
        """按 `_score_reward_card` 原始分取最好的卡（选牌屏/候选排序仍在用）。

        商店采购**不再**走这个方法：那边走统一评分池 `_shop_preferred_purchase`。
        """
        cards, _relics, _potions = self._shop_affordable_items(summary_context)
        if not cards:
            return None
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        best: tuple[int, int] | None = None
        for card in cards:
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            if score < min_score:
                continue
            index = self._safe_int(card.get("index"))
            if index is None:
                index = self._safe_int(card.get("card_index"))
            if index is None:
                continue
            if best is None or score > best[0]:
                best = (score, index)
        return best[1] if best else None
