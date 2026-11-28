from __future__ import annotations

from typing import Any

from .planner_common import (
    _is_basic_card_like,
)


class PlannerLessonsMixin:
    """Internal responsibility mixin; initialized and composed by STS2HeuristicPlanner."""

    def _ranked_option_indices(self, context: dict[str, Any], operation: Any) -> list[int]:
        """按各屏**既有评分**给出的"从好到坏"顺序；没有评分可用的屏返回空列表。

        **跨局教训（strategy_directives["avoid"]）在这里生效**：排序完成后，命中教训关键词的
        选项会被降权重排。这是复盘闭环唯一的消费点 —— 没有它，`avoid` 就只是个只写不读的死字段
        （见 `.agent-teams/sts2-omnissiah-audit/reflection-audit-t2.md`）。
        """
        action_type = str(self._operation_dict(operation).get("action_type") or "").strip().lower()
        summary_context, strategy_context, _snapshot = self._context_parts(context)
        preferences = strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        state_name = str(classification.get("state_name") or "").strip().lower()

        if action_type == "choose_event_option":
            ranked = self._rank_event_options(summary_context, strategy_context)
        elif action_type == "choose_map_node":
            ranked = self._rank_map_nodes(summary_context, preferences)
        elif action_type == "choose_reward_card":
            ranked = self._rank_reward_cards(summary_context, preferences)
        elif action_type == "select_deck_card" and state_name in self._REMOVE_SELECTION_SCREENS:
            ranked = self._rank_remove_options(summary_context)
        elif action_type == "select_deck_card" and self._is_upgrade_selection(summary_context):
            # ★ 升级屏的排序必须与删牌相反（分越高越该升），否则扰动池的"第 0 位"
            #   会与 plan() 实际选出的那张不一致 —— 而扰动层的不变量正是
            #   "排序第 0 个 == 规划器原本会选的那一个"。共用删牌排序会破坏该不变量。
            ranked = self._rank_upgrade_options(summary_context)
        else:
            return []
        return self._apply_run_lessons(strategy_context, summary_context, action_type, ranked)

    def _run_memory_lessons(self, strategy_context: dict[str, Any]) -> list[str]:
        """取出跨局教训文本（`strategy_directives["avoid"]`）。

        这些文本由 `strategy_repository._inject_run_memory_directives` 从猫娘记忆注入，
        每个 tick 重算，所以此处只读不写。
        """
        directives = strategy_context.get("strategy_directives") if isinstance(strategy_context.get("strategy_directives"), dict) else {}
        raw = directives.get("avoid") if isinstance(directives.get("avoid"), list) else []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _lesson_combat_direction(
        self,
        strategy_context: dict[str, Any],
        *,
        logger: Any | None = None,
    ) -> dict[str, bool]:
        """把跨局教训翻译成战斗屏的**顺序偏好**（`prefer_defense` / `prefer_attack`）。

        返回值可直接并入既有的 `guidance_override` 通路
        （`_guidance_override_from_payload` → `:1137-1143`）。

        ★保守性（无教训时必须逐字保持现状）：
        * 无教训 / 抽不出 token / token 与战斗无关 ⇒ 返回 `{}`（**空 dict**，不是 False）。
        * 同时含 `skill` 与 `attack`（真实最高频组合）⇒ 取 **`prefer_defense`**：
          这正对应反思原意「先垫格挡**再**输出」——先保命，再输出。
          若取 `prefer_attack` 就会把「先垫格挡」读反。
        * 纯函数：不掷随机、不读时间。

        ★★t17 关键机制澄清（**必须连读，否则会做出错误推断**）：
        在本函数恒返回 `prefer_defense` 的前提下，它与生产形态下
        `:1145` 的默认兜底（`gap>0` ⇒ `prefer_defense`）**同值**。
        ⇒ 真实注入在「挨打」带内**看不见位移**，但这**不是**被吞、也**不是**接线失效，
          而是**冗余**——两者给出同一个方向。

        位移可观测的唯一带是**默认兜底不触发**的地方（`gap<=0`，玩家格挡已够）：
        ```
        gap<=0 :  无教训 -> card_index=0 ;  真实注入 -> card_index=1   ★位移=1
        gap>0  :  无教训 -> card_index=1 ;  真实注入 -> card_index=1     位移=0（冗余）
        ```
        ⇒ 因此「删掉任一闸即可覆盖」是**错的**；本函数与默认兜底是**两个独立来源**，
          同时指向防御时自然无法区分贡献。要测量教训的**净贡献**，必须
          在该函数**返回空 dict** 的带（或摘掉教训层）上做对照 —— 见 `_t17_e2e.py`。
        """
        lessons = self._run_memory_lessons(strategy_context)
        if not lessons:
            return {}
        tokens = self._lesson_tokens(lessons)
        if not tokens:
            return {}
        direction: dict[str, bool] = {}
        for token, name in self._LESSON_COMBAT_DIRECTION:
            if token in tokens:
                direction[name] = True
                break
        if direction:
            self._lesson_hit_debug("combat_direction", lessons, tokens, direction, logger=logger)
        return direction

    def lesson_trace(self, context: dict[str, Any], operation: Any = None) -> dict[str, Any]:
        """★I5 度量埋点：把"这一 tick 教训层做了什么"完整暴露出来。

        让 t_r3 的每个指标都能从**一个**调用算出，不必再猜内部状态：

        * ``lessons``      —— 注入的教训原文（`strategy_directives["avoid"]`）
        * ``tokens``       —— `_lesson_tokens()` 抽出的 token（闸3 判据）
        * ``token_lesson_ratio`` —— 有多少条教训能抽出 token（信息密度）
        * ``combat_direction``   —— 教训给出的战斗方向（I2 接线是否生效）
        * ``fallback_lessons``   —— 其中有多少条是那个无信息量的兜底模板句
        * ``penalize_indices``   —— 该屏被降权的下标（I3 区分度判据）

        **只读**：不掷随机、不写状态（`_lesson_hit_debug` 的去重集合除外，且它不影响决策）。
        `operation` 省略时只报教训侧，不报屏相关字段。
        """
        _summary_context, strategy_context, _snapshot = self._context_parts(context)
        lessons = self._run_memory_lessons(strategy_context)
        tokens = self._lesson_tokens(lessons)
        trace: dict[str, Any] = {
            "lessons": lessons,
            "lesson_count": len(lessons),
            "tokens": tokens,
            "token_count": len(tokens),
            "tokenizable_lessons": [text for text in lessons if self._lesson_tokens([text])],
            "combat_direction": self._lesson_combat_direction(strategy_context),
        }
        trace["tokenizable_lesson_count"] = len(trace["tokenizable_lessons"])
        trace["token_lesson_ratio"] = (
            round(trace["tokenizable_lesson_count"] / len(lessons), 4) if lessons else 0.0
        )
        # 兜底模板句识别（供 M1 复读率的运行期口径）。
        trace["fallback_lessons"] = [text for text in lessons if "输得不明不白" in text]
        trace["fallback_lesson_count"] = len(trace["fallback_lessons"])
        if operation is not None:
            action_type = str(self._operation_dict(operation).get("action_type") or "").strip().lower()
            digest = self._operation_dict(operation)
            try:
                ranked = self._ranked_option_indices(context, digest)
            except Exception:
                ranked = []
            trace["action_type"] = action_type
            trace["penalize_indices"] = sorted(self._indices_worth_penalizing(
                (self._context_parts(context)[0].get("payload")
                 if isinstance(self._context_parts(context)[0].get("payload"), dict) else {}),
                action_type,
                tokens,
            ))
            trace["ranked_indices"] = ranked
            trace["candidate_count"] = len(ranked)
            # I3 区分度：罚完之后还剩几个"未被罚"的候选。<=1 说明这条教训没有区分度。
            trace["discriminating_indices"] = [
                index for index in ranked if index not in set(trace["penalize_indices"])
            ]
            trace["discriminating_count"] = len(trace["discriminating_indices"])
        return trace

    def _lesson_hit_debug(
        self,
        stage: str,
        lessons: list[str],
        tokens: list[str],
        effect: Any,
        *,
        logger: Any | None = None,
    ) -> None:
        """★I4 教训命中时的可 grep 日志（沿用既有 `[sts2_*]` 前缀风格）。

        **去重键** = `stage + tokens + effect`：同一 tick 内同样的命中只打一次，
        避免每个决策步骤都刷屏把日志淹掉。去重状态放在实例上（按 planner 生命周期）。

        ★**日志失败绝不影响决策路径**：整段包在 `try/except` 里，
        且 `logger` 缺省时回落到 `self._debug`（同样可能不可用）。
        任何异常都被吞掉 —— 可观测性永远不许成为决策的失败点。
        """
        try:
            dedup_key = f"{stage}|{','.join(tokens)}|{effect!r}"
            seen = getattr(self, "_lesson_hit_seen", None)
            if not isinstance(seen, set):
                seen = set()
                self._lesson_hit_seen = seen
            if dedup_key in seen:
                return
            seen.add(dedup_key)
            message = "[sts2_lesson_hit] stage=%s tokens=%s effect=%r lessons=%d"
            args = (stage, ",".join(tokens), effect, len(lessons))
            emit = logger if callable(logger) else getattr(self, "_debug", None)
            if callable(emit):
                emit(message, *args)
        except Exception:
            # 可观测性失败绝不允许冒泡到决策路径。
            return

    def _lesson_penalty_strength(self, lessons: list[str]) -> dict[str, int]:
        """每个教训 token 的**惩罚强度** = 有多少条教训提到过它。

        ★★ 为什么需要"强度"而不是布尔命中（实测驱动，2026-10-19）：

        真实 50 局记忆的窗口（10 条教训）实测频次：

            attack  8 条     skill  7 条     elite  5 条     monster  1 条

        若只按"命中/未命中"（布尔），`elite` 与 `monster` 同档 ⇒
        排序键退化成 `(1, 原始名次)` ⇒ **保持原序 ⇒ 仍然恒等失效**。
        这正是我第一版方案 A 实现的问题：换了个写法，病没治。

        而频次是**真实的信号**：被 5 条教训反复提到的坑，
        比只被提到 1 次的不该同等对待。用频次当强度，档位就能区分开。

        语义上这也对：复盘是"多局统计"，某类死因越常出现，
        说明它越没被纠正 ⇒ 越该规避。**这本来就是玩家要的"多维反思"的最小形式。**
        """
        strength: dict[str, int] = {}
        for lesson in lessons:
            for token in self._lesson_tokens([lesson]):
                strength[token] = strength.get(token, 0) + 1
        return strength

    def _apply_run_lessons(
        self,
        strategy_context: dict[str, Any],
        summary_context: dict[str, Any],
        action_type: str,
        ranked: list[int],
    ) -> list[int]:
        """把跨局教训应用到候选排序上（稳定重排：只降权，不删除）。

        保守实现，三条硬约束：

        * **无教训时逐字返回原序** —— 没有记忆时行为与改动前完全一致。
        * **只降权不删除** —— 命中的选项仍留在候选里（只是靠后），所以扰动层拿到的仍是合法集合，
          且最坏情况退化为"顺序不同"而不会"无候选可出"。
        * **纯函数式** —— 不掷随机数、不读时间，同样的输入必得同样的输出，可确定性测试。

        ★★ 2026-10-19 方案 A（玩家选定），修掉原 `kept + demoted` 的**恒等失效**：

            旧：kept + demoted ⇒ 被罚项集中在尾部时**逐字原序**（教训完全失效）
            新：按 (惩罚强度, 原始名次) 排序 ⇒ 强度可区分，任意分布都能表达

        实测修复效果（真实 50 局注入，基线 `[1,3,0,2]`，命中 `{Monster(0), Elite(2)}`）：

            elite=5 条、monster=1 条  ⇒  强度 5 > 1
            ⇒ Monster(0) 被压到 Elite(2) 之后
            ⇒ `[1,3,0,2]` → `[1,3,2,0]`  ★ **教训终于生效**

        对照：若两者频次相同，则**保持原序是正确的** ——
        同等该罚的两项之间没有理由改变相对次序（稳定性优于随机）。
        """
        lessons = self._run_memory_lessons(strategy_context)
        if not lessons or len(ranked) < 2:
            return ranked

        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        tokens = self._lesson_tokens(lessons)
        if not tokens:
            return ranked
        penalize_indices = self._indices_worth_penalizing(payload, action_type, tokens)
        if not penalize_indices:
            # 这一屏没有该教训的着力点（或是未支持的屏）：原序返回，等于没有记忆时的行为。
            return ranked

        # ★ 强度 = "有多少条教训提到过这个 token"（见 `_lesson_penalty_strength`）。
        #   必须**逐 token** 求命中集，才能把强度落到具体选项上。
        strength = self._lesson_penalty_strength(lessons)
        penalties: dict[int, int] = {}
        for token, weight in strength.items():
            for index in self._indices_worth_penalizing(payload, action_type, [token]):
                penalties[index] = penalties.get(index, 0) + weight * self._LESSON_PENALTY_PER_TOKEN

        # 未被罚的项 penalty = 0；排序键 (penalty, 原始名次) ⇒ 稳定、单调、可表达任意强度。
        order = {index: position for position, index in enumerate(ranked)}
        return sorted(ranked, key=lambda index: (penalties.get(index, 0), order.get(index, 0)))

    def _lesson_tokens(self, lessons: list[str]) -> list[str]:
        """把教训文本压成一小组可匹配的关键词。

        教训是整句中文（例如「下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出」），
        直接拿整句去匹配选项描述必然匹配不上，所以抽取其中的**行为关键词**。
        未命中任何关键词的教训会被忽略 —— 宁可没影响，也不要用模糊匹配乱改决策。
        """
        tokens: list[str] = []
        for lesson in lessons:
            text = lesson.lower()
            for token, keywords in self._LESSON_KEYWORD_MAP:
                if token in tokens:
                    continue
                if any(keyword in text for keyword in keywords):
                    tokens.append(token)
        return tokens

    def _indices_worth_penalizing(
        self,
        payload: dict[str, Any],
        action_type: str,
        tokens: list[str],
    ) -> set[int]:
        """哪些选项应当因为跨局教训被降权。

        处理三种语义明确的屏：**路线**（规避某个节点类型）、**选牌奖励**（规避某类卡）、
        **事件选项**（规避某类事件后果）。
        其余屏返回空集 —— 匹配不准就别动，避免把复盘变成噪声。

        ★事件屏是**缺陷 C** 的核心：此前它不在返回值内，于是复盘教训**永远作用不到事件屏**，
        这正是用户说的「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。
        """
        targets: set[int] = set()
        if not tokens:
            return targets
        if action_type == "choose_map_node":
            nodes = payload.get("travelable_nodes")
            if not isinstance(nodes, list) or not nodes:
                nodes = payload.get("available_nodes")
            for position, node in enumerate(nodes if isinstance(nodes, list) else []):
                if not isinstance(node, dict):
                    continue
                raw = node.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                # **必须复用**既有别名表：mod 真实下发的是 `RestSite`/`Treasure` 这类枚举名，
                # 而教训 token 是规范短键（`rest`/`treasure`）。只 lower() 不做归一会永远匹配不上——
                # 这正是"短键能重排、真实对局却毫无反应"的根因（见 `_map_node_weight_key`）。
                node_type = self._map_node_weight_key(node)
                if node_type and node_type in tokens:
                    targets.add(index)
        elif action_type == "choose_reward_card":
            cards = payload.get("reward_cards") if isinstance(payload.get("reward_cards"), list) else []
            if not cards:
                cards = payload.get("cards") if isinstance(payload.get("cards"), list) else []
            # ★I3 有区分度的奖励选牌着力点 —— 修掉「全罚等于没罚 / 偏罚」两种退化。
            #
            # 病因：真实最高频的教训（9/10 条）抽出 `["skill","attack"]`，而奖励屏常给出
            # attack/skill/power 三选一 ⇒ 前两张**全被罚**，只剩 power 没被罚。
            # 实测（t10 `g4_degeneracy`）：区分度 3/3 → **1/3**，且**恒定**把玩家推向 power，
            # 与反思本意（「先垫格挡再输出」是**顺序**）毫无关系。
            # `_apply_run_lessons:4467` 的 `kept + demoted if kept else ranked` 对此无能为力：
            # 只要还剩 1 个未罚项，它就会把其余全部推到后面 —— 这是**偏罚**，比 no-op 更危险。
            #
            # 修法：**只有当惩罚集不至于把候选压成单一选择时才应用**。
            # 判据是"留下 ≥2 个未被罚的候选"：低于此数就说明这次匹配**没有区分度**
            # （罚掉了几乎所有选项 ⇒ 剩下的那一个是运气，不是判断），于是本屏放弃降权、返回原序。
            # 保守性优先：宁可不动，也不要用一条压平语义的教训去改选牌。
            candidates: list[int] = []
            matched: set[int] = set()
            for position, card in enumerate(cards):
                if not isinstance(card, dict):
                    continue
                raw = card.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                candidates.append(index)
                card_type = str(card.get("card_type") or "").strip().lower()
                rarity = str(card.get("rarity") or "").strip().lower()
                if "curse" in tokens and rarity == "curse":
                    matched.add(index)
                # ★ 六维缺口的「牌组质量」着力点（2026-10-19）：
                #   「这局基础牌和诅咒太多，下局我想早点升级关键牌、删掉废牌」
                #   ⇒ `quality` token ⇒ 把**会拉低牌组质量**的奖励降权
                #     （诅咒牌 + 已持有的基础牌再拿一张）。
                #
                # ★ 为什么这是**正确方向**：`quality` 缺口的定义就是
                #   "基础牌没升级 + 诅咒太多"（`_deck_gap_vector` 的 gap_quality），
                #   所以"别再拿基础牌/诅咒"正是它该产生的行为。
                elif "quality" in tokens and (rarity == "curse" or _is_basic_card_like(card)):
                    matched.add(index)
                elif card_type and card_type in tokens:
                    matched.add(index)
            if len(candidates) - len(matched) >= 2:
                targets |= matched
        elif action_type == "choose_event_option":
            # ★缺陷 C：让复盘教训能作用于**事件屏**。
            # 此前事件屏不在本函数覆盖范围内 ⇒ `avoid` 对事件决策**零影响**
            # （inquisitor t12 实测：有无 avoid 的 plan() 逐字相同）。
            # 玩家口径：「我建立复盘机制就是为了避免这个问题，但脚本已经这样很多次了，没有改善」。
            #
            # 判据与路线/选牌同源：教训 token 命中选项文本所表达的**后果类型**。
            # 复用既有的 `_LESSON_KEYWORD_MAP` 语义（curse/shop/rest/...），
            # 但事件选项的文案是自然语言（"失去 5 点生命上限，获得 100 金币"），
            # 因此这里按**关键词共现**匹配，而不是等值比较。
            options = payload.get("event_options") if isinstance(payload.get("event_options"), list) else []
            for position, option in enumerate(options):
                if not isinstance(option, dict):
                    continue
                raw = option.get("index")
                index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                text = self._event_option_text(option).lower()
                if not text:
                    continue
                # 教训 token -> 事件选项文本的后果关键词
                for token in tokens:
                    keywords = self._EVENT_CONSEQUENCE_KEYWORDS.get(token)
                    if not keywords:
                        continue
                    if any(keyword in text for keyword in keywords):
                        targets.add(index)
                        break
        elif action_type == "choose_shop_item":
            # ★★ 让复盘教训能作用于**商店屏**（此前完全无着力点）。
            #
            # 病因：本函数没有 `choose_shop_item` 分支 ⇒ `avoid` 对商店决策**零影响**，
            # 教训学不到"这局缺格挡"就永远改不了"该不该买格挡牌"。
            # 已由 `test_shop_screen_has_no_lesson_fulcrum` 钉住该缺口。
            #
            # ★ 与其它屏的**本质差别**：路线/选牌屏是"**规避**某类选项"（降权），
            #   而商店的教训是"**别买**某类东西" —— 语义仍是降权，故复用同一机制。
            #
            # 判据：教训 token 映射到**牌组维度**，再看这件商品补的是不是那一维。
            #   例：「这局栽在精英手里」→ `elite` token → 不对应具体维度 ⇒ **不动商店**
            #       （商店里没有"精英"这类商品，硬匹配只是噪声）
            #   「早点补格挡浓度」→ `skill` token → 格挡维 ⇒ 把"纯输出"类商品降权
            #
            # ⚠️ 刻意**保守**：只在 token 明确指向某一维时才动（`_LESSON_TOKEN_TO_SHOP_DIM`），
            #   其余 token 一律不动 —— 商店是花金币的地方，误降权等于白花/不花，
            #   代价比路线屏更高（与全局"匹配不准就别动"一致）。
            dims = self._lesson_shop_dims(tokens)
            if dims:
                for position, item in enumerate(self._shop_payload_items(payload)):
                    if not isinstance(item, dict):
                        continue
                    raw = item.get("index")
                    index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
                    # 这件商品补的维度（卡走 `_card_effect_dims`，遗物/药水走类别）
                    item_dims = self._shop_item_dims(item)
                    # ★ 降权的是"**没在补**该维度"的商品 —— 教训说缺格挡，
                    #   那买攻击牌就该被压后（把额度留给格挡牌）。
                    if item_dims and not (item_dims & dims):
                        targets.add(index)
        return targets

    def _lesson_shop_dims(self, tokens: list[str]) -> set[str]:
        """教训 token 指向的**商店维度**集合（空集 = 本屏不该动）。"""
        dims: set[str] = set()
        for token in tokens:
            for dim in self._LESSON_TOKEN_TO_SHOP_DIM.get(token, ()):
                dims.add(dim)
        return dims

    def _shop_payload_items(self, payload: dict[str, Any]) -> list[Any]:
        """商店屏的可买项（卡/遗物/药水），按 mod 下发的顺序拼成一个候选序列。

        ★ 顺序必须与 `_shop_affordable_items` 一致（卡 → 遗物 → 药水），
          否则 `index` 会与真实的商品下标错位。
        """
        out: list[Any] = []
        for key in ("shop_cards", "shop_relics", "shop_potions"):
            items = payload.get(key)
            if isinstance(items, list):
                out.extend(items)
        return out

    def _shop_item_dims(self, item: dict[str, Any]) -> set[str]:
        """这件商店商品补的是哪些**牌组维度**（读不出就返回空集 ⇒ 不动它）。"""
        # 卡牌：直接复用既有映射（攻击→输出、格挡→格挡、抽牌→抽牌…）
        if str(item.get("card_id") or "") or str(item.get("card_type") or ""):
            return set(self._card_effect_dims(item))
        # 遗物/药水：走类别映射
        category = self._potion_category(item)
        return set(self._POTION_CATEGORY_TO_SHOP_DIM.get(category, ()))

    def _rank_map_nodes(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> list[int]:
        """与 _preferred_map_index 同口径：分数高的在前，同分时下标小的在前。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes")
        if not isinstance(nodes, list) or not nodes:
            nodes = payload.get("available_nodes")
        nodes = nodes if isinstance(nodes, list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        route_policy = self._route_policy(preferences)

        scored: list[tuple[int, int]] = []
        for position, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            raw = node.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_map_node(node, route_policy, hp_ratio, gold), index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]

    def _rank_reward_cards(self, summary_context: dict[str, Any], preferences: dict[str, Any]) -> list[int]:
        """与 _preferred_reward_option 同口径（含 guidance 调整）：分数高的在前，同分时下标小的在前。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        reward_cards = self._reward_candidates(summary_context)
        deck = payload.get("deck") if isinstance(payload.get("deck"), dict) else {}
        archetype_tags = [str(tag).strip().lower() for tag in self._infer_archetype_tags(deck)] if isinstance(deck, dict) else []
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        deck_policy = self._reward_deck_policy(records)
        guidance_override = self._guidance_override_from_payload(summary_context)

        scored: list[tuple[int, int]] = []
        for position, card in enumerate(reward_cards if isinstance(reward_cards, list) else []):
            if not isinstance(card, dict):
                continue
            score = self._score_reward_card(card, deck_policy, archetype_tags)
            if guidance_override.get("prefer_defense"):
                if self._card_block(card) > 0:
                    score += 18
                if self._reward_dynamic_value(card, "HpLoss") > 0:
                    score -= 12
            if guidance_override.get("prefer_attack"):
                if self._card_damage(card) > 0:
                    score += 14
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((score, index))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]

    def _rank_remove_options(self, summary_context: dict[str, Any]) -> list[int]:
        """与 `_preferred_remove_option` **同口径**：最该删的在前。

        ★ 修一个真实缺陷（2026-10-17）：本函数原为
        `scored.sort(key=lambda item: (-item[0], -item[1]))`，即**分数高者在前**，
        与 `_preferred_remove_option` 的 `min((score, -index))`
        （**分数低者优先删**）**方向相反**，但 docstring 却写"同口径"。

        后果：`_score_remove_card` 对诅咒 = −100（最低 ⇒ 最该删），
        而排序把它放**最后** ⇒ `ordered_candidates` 的扰动候选里，
        `items[1]`（mischievous 档位会选它）指向的是**最不该删的卡**（如能量卡 +70）。
        实测：`[诅咒, 打击, 储能]` ⇒ preferred=0(诅咒) 正确，但 rank=[2,1,0]
        ⇒ 扰动时优先删掉储能卡。

        现在改为与 preferred 同口径（`(score, -index)` 升序），
        ⇒ 扰动候选顺序变为"次该删 → 再次该删"，与决策对齐。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        scored: list[tuple[int, int]] = []
        for position, card in enumerate(selection_cards):
            if not isinstance(card, dict) or self._is_unremovable_card(card):
                continue
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_remove_card(card), index))
        # 与 `_preferred_remove_option` 完全同序：分数低者在前（最该删），
        # 同分时下标**大**者在前（与 preferred 的 `-card_index` 取 min 等价）。
        scored.sort(key=lambda item: (item[0], -item[1]))
        return [index for _score, index in scored]

    def _rank_upgrade_options(self, summary_context: dict[str, Any]) -> list[int]:
        """与 `_preferred_upgrade_option` **同口径**：最该升的在前。

        ★ 刻意与 `_rank_remove_options` 方向相反 —— 升级要"最有价值"，
        删牌要"最没价值"。第 0 个必须等于规划器真正会选的那张，
        否则扰动层的"不扰动 == 取第 0 个"不变量会被破坏。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selection_cards = payload.get("selection_cards") if isinstance(payload.get("selection_cards"), list) else []
        scored: list[tuple[int, int]] = []
        for position, card in enumerate(selection_cards):
            if not isinstance(card, dict) or self._item_blocked(card):
                continue
            raw = card.get("index")
            index = int(raw) if isinstance(raw, int) and not isinstance(raw, bool) else position
            scored.append((self._score_upgrade_card(card), index))
        # 与 `_preferred_upgrade_option` 的 `max((score, -index))` 等价：
        # 分数**高**者在前，同分时下标**小**者在前。
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _score, index in scored]
