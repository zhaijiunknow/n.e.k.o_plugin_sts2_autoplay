from __future__ import annotations

from time import monotonic
from typing import Any

from .planner_common import (
    _MAP_NODE_TYPE_ALIASES,
    MAP_POLICY_FOLLOW,
    MAP_POLICY_WAIT,
    ROUTE_BOSS_PREP_GOLD,
    ROUTE_BOSS_PREP_MULTIPLIER,
    ROUTE_BOSS_PREP_ROWS,
    ROUTE_DISCOUNT_PER_ROW,
    ROUTE_ELITE_RELIEF_MAX,
    ROUTE_LOOKAHEAD_ROWS_FALLBACK,
    ROUTE_REPEAT_DECAY,
    SAFE_MAP_HP_RATIO_BELOW,
    SAFE_MAP_LOW_HP_WEIGHTS,
    SAFE_MAP_WEIGHTS,
    parse_map_pick,
)


class PlannerMapMixin:
    """Internal responsibility mixin; initialized and composed by STS2HeuristicPlanner."""

    # ---- 地图跟随玩家投票 ----------------------------------------------------------------

    def _other_player_vote_index(self, summary_context: dict[str, Any]) -> int | None:
        """别人已投节点位置索引（没人投 / 只有自己投 -> None）。summary_context_builder 已算好。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        index = payload.get("other_player_vote_index")
        if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
            return index
        return None

    @staticmethod
    def _is_multiplayer_map(summary_context: dict[str, Any]) -> bool:
        """是否多人局。单机地图不该等玩家投票（等也等不到，只会拖慢自动玩）。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        return bool(payload.get("is_multiplayer"))

    @staticmethod
    def _map_wait_identity(summary_context: dict[str, Any]) -> str:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        current = payload.get("current_node") if isinstance(payload.get("current_node"), dict) else {}
        nodes = payload.get("available_nodes") if isinstance(payload.get("available_nodes"), list) else []
        return f"{payload.get('floor')}|{current.get('row')},{current.get('col')}|{len(nodes)}"

    def _local_map_vote_position(self, summary_context: dict[str, Any]) -> int | None:
        """Only count a vote for a currently available node, not a stale previous-floor vote."""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        vote = payload.get("local_vote")
        for position, node in enumerate(self._map_nodes(summary_context)):
            if node.get("has_local_vote"):
                return position
            if (isinstance(vote, dict) and vote.get("row") is not None and vote.get("col") is not None
                    and node.get("row") == vote["row"] and node.get("col") == vote["col"]):
                return position
        return None

    def map_wait_reason(self, summary_context: dict[str, Any]) -> str:
        """Read-only explanation: observing status must not start/reset the vote timer."""
        if not self._is_multiplayer_map(summary_context) or not self._map_nodes(summary_context):
            return ""
        if self._map_policy == MAP_POLICY_WAIT:
            return "联机地图：等待玩家下达选路指令；此模式不会自动跟票，也不会超时选路。"
        local = self._local_map_vote_position(summary_context)
        other = self._other_player_vote_index(summary_context)
        if local is not None and (self._map_policy != MAP_POLICY_FOLLOW or other is None or other == local):
            return "联机地图：已投票，等待队友确认或地图切换，不重复提交。"
        if (self._map_policy == MAP_POLICY_FOLLOW and other is None
                and self._map_wait_key == self._map_wait_identity(summary_context)
                and self._map_wait_started_at > 0
                and monotonic() - self._map_wait_started_at < self._map_vote_wait_seconds):
            return f"联机地图：等待队友先投票，无人投票满 {self._map_vote_wait_seconds:g} 秒后自行选路。"
        return ""

    def _map_wait_pending(self, summary_context: dict[str, Any]) -> bool:
        """没人投票时是否还要继续等玩家。

        以地图身份（层级 + 当前节点 + 候选节点数）为键：换图/换层就重新开始计时，不会把上一张图的
        等待时间带过来。等满 _map_vote_wait_seconds 后返回 False，让插件自己选，避免整局卡死。
        """
        if self._map_vote_wait_seconds <= 0:
            return False
        key = self._map_wait_identity(summary_context)
        now = monotonic()
        if key != self._map_wait_key or self._map_wait_started_at <= 0:
            self._map_wait_key = key
            self._map_wait_started_at = now
            return True
        return (now - self._map_wait_started_at) < self._map_vote_wait_seconds

    # ---- 对话式地图协作（sts2_set_route）--------------------------------------------------

    @staticmethod
    def _map_nodes(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        """候选节点列表。所谓"位置"就是这个列表的下标，与 mod 侧 available_nodes 的顺序一致。"""
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes")
        if not isinstance(nodes, list) or not nodes:
            nodes = payload.get("available_nodes")
        return [node for node in (nodes if isinstance(nodes, list) else []) if isinstance(node, dict)]

    @staticmethod
    def _map_option_index(nodes: list[dict[str, Any]], position: int) -> int:
        """位置 -> choose_map_node 的 option_index。mod 侧就是用这个下标取 available_nodes。"""
        node = nodes[position] if 0 <= position < len(nodes) and isinstance(nodes[position], dict) else {}
        raw = node.get("index")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else position

    @staticmethod
    def _map_node_type(node: dict[str, Any]) -> str:
        return str(node.get("type") or node.get("node_type") or "?").strip() or "?"

    def _map_decision(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> tuple[int, str] | None:
        """按 map_policy 决定这一 tick 要不要投票、投哪个；返回 None 表示什么都不做。

        wait 在多人局里永远返回 None：不投票、也不跟票，路线完全留给玩家。等玩家发话之后才动，
        那三种说法走 sts2_set_route（safe/mirror/pick），不经过这里。单机局没有队友可等，
        仍然自己选，否则每张图都会白等一轮。
        """
        multiplayer = self._is_multiplayer_map(summary_context)
        policy = self._map_policy
        if policy == MAP_POLICY_WAIT and multiplayer:
            return None
        local_vote = self._local_map_vote_position(summary_context) if multiplayer else None
        if policy == MAP_POLICY_FOLLOW:
            follow_index = self._other_player_vote_index(summary_context)
            if follow_index is not None:
                if local_vote == follow_index:
                    return None
                return self._map_option_index(self._map_nodes(summary_context), follow_index), "map_follow_player_vote"
            if local_vote is not None:
                return None
            if multiplayer and self._map_wait_pending(summary_context):
                return None
        elif local_vote is not None:
            return None
        return self._map_own_pick(summary_context, preferences, snapshot)

    def _map_own_pick(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> tuple[int, str]:
        node_index = self._preferred_map_index(summary_context, preferences, snapshot)
        return (node_index if isinstance(node_index, int) else 0), "map_preference_or_default"

    def _safe_route_preferences(self, preferences: dict[str, Any] | None) -> dict[str, Any]:
        """"定个路线"用的路由权重：内置保守权重打底，玩家自己配过的权重覆盖其上。"""
        configured = self._route_policy(preferences or {})
        weights = dict(SAFE_MAP_WEIGHTS)
        user_weights = configured.get("weights") if isinstance(configured.get("weights"), dict) else {}
        for key, value in user_weights.items():
            try:
                weights[str(key).strip().lower()] = int(value)
            except (TypeError, ValueError):
                continue
        policy = {
            "instruction_type": "route_policy",
            "weights": weights,
            "conditions": {
                "low_hp": {
                    "hp_ratio_below": SAFE_MAP_HP_RATIO_BELOW,
                    "weights": dict(SAFE_MAP_LOW_HP_WEIGHTS),
                }
            },
            "lookahead_depth": configured.get("lookahead_depth") or 4,
        }
        return {"records": [{"value": policy}]}

    def resolve_route(
        self,
        summary_context: dict[str, Any],
        *,
        mode: str,
        pick: Any = None,
        index: Any = None,
        preferences: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把"定个路线 / 跟你一样 / 选中间"解析成一个具体的 choose_map_node 选项。

        message 是给猫娘直接说出口的中文；option_index 为 None 时调用方不应执行动作。
        """
        mode_text = str(mode or "").strip().lower()
        if mode_text not in {"safe", "mirror", "pick"}:
            mode_text = "safe"
        nodes = self._map_nodes(summary_context)
        catalog = [
            {
                "position": position + 1,
                "option_index": self._map_option_index(nodes, position),
                "node_type": self._map_node_type(node),
            }
            for position, node in enumerate(nodes)
        ]
        result: dict[str, Any] = {
            "ok": False,
            "mode": mode_text,
            "option_index": None,
            "node_type": None,
            "position": None,
            "node_count": len(nodes),
            "nodes": catalog,
            "message": "",
            "error": None,
        }
        if not nodes:
            result["error"] = "not_on_map"
            result["message"] = "现在不是选路线的界面，没办法投地图票。"
            return result

        position: int | None = None
        if mode_text == "mirror":
            mirror = self._other_player_vote_index(summary_context)
            if mirror is None:
                result["error"] = "no_player_vote"
                result["message"] = "你还没在地图上投票呢，我先等着；你选好之后说一声，我就跟你投同一个。"
                return result
            if not 0 <= mirror < len(nodes):
                result["error"] = "no_player_vote"
                result["message"] = "我读到的你的票不在当前候选里，等我刷新一下再说。"
                return result
            position = mirror
        elif mode_text == "pick":
            if isinstance(index, int) and not isinstance(index, bool):
                if 0 <= index < len(nodes):
                    position = index
                else:
                    result["error"] = "index_out_of_range"
                    result["message"] = f"下标 {index} 越界了，现在只有 {len(nodes)} 个可选节点。"
                    return result
            else:
                parsed = parse_map_pick(pick, len(nodes))
                if parsed is None:
                    result["error"] = "pick_unparsed"
                    result["message"] = (
                        f"我没听清要选哪个；说“中间”“第几个”“最左”“最右”都行，现在有 {len(nodes)} 个候选。"
                    )
                    return result
                position = parsed
        else:
            chosen = self._preferred_map_index(summary_context, self._safe_route_preferences(preferences), snapshot)
            if isinstance(chosen, int):
                position = chosen if 0 <= chosen < len(nodes) else None
                if position is None:
                    for position, item in enumerate(catalog):
                        if item["option_index"] == chosen:
                            break
                    else:
                        position = None

        if position is None:
            result["error"] = "no_safe_route"
            result["message"] = "这张图上我没找到更稳妥的节点，等你定我再跟着投。"
            return result

        node_type = self._map_node_type(nodes[position])
        result.update(
            ok=True,
            option_index=self._map_option_index(nodes, position),
            node_type=node_type,
            position=position + 1,
        )
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        if mode_text == "mirror":
            result["message"] = f"好，我跟你投第 {position + 1} 个（{node_type}）。"
        elif mode_text == "pick":
            result["message"] = f"好，我投第 {position + 1} 个（{node_type}）。"
        else:
            result["message"] = self._safe_route_message(payload, position, node_type)
        return result

    def _safe_route_message(self, payload: dict[str, Any], position: int, node_type: str) -> str:
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        if hp_ratio and hp_ratio < SAFE_MAP_HP_RATIO_BELOW:
            return f"我投第 {position + 1} 个（{node_type}）：血量只剩 {int(hp_ratio * 100)}%，先挑稳一点的。"
        return f"我投第 {position + 1} 个（{node_type}）：这条看着最稳妥。"

    def _preferred_route_option(self, preferences: dict[str, Any]) -> int | None:
        preferred = self._preferred_option_index(preferences)
        if preferred is not None:
            return preferred
        return self._first_record_option(preferences)

    def _route_elite_lookahead(self, graph: dict[str, Any], start: dict[str, Any]) -> bool:
        """从 `start` 这个节点往下看 `_SHOP_ELITE_LOOKAHEAD_ROWS` 层，路上有没有精英/Boss。

        复用 `_map_graph_index` / `_map_child_keys`（`evaluate_map_route` 那一套用的同一份图），
        所以坐标语义与链式递推完全一致：children 是 `{row,col}`，row 越大越靠近 boss。
        """
        index = self._map_graph_index(graph)
        if not index:
            return False
        row = self._safe_int(start.get("row"))
        col = self._safe_int(start.get("col"))
        if row is None or col is None:
            return False
        frontier: list[tuple[int, int]] = [(row, col)]
        seen: set[tuple[int, int]] = set()
        for _ in range(max(1, self._SHOP_ELITE_LOOKAHEAD_ROWS)):
            nxt: list[tuple[int, int]] = []
            for key in frontier:
                if key in seen:
                    continue
                seen.add(key)
                node = index.get(key)
                if node is None:
                    continue
                if self._map_node_weight_key(node) in {"elite", "boss"}:
                    return True
                nxt.extend(self._map_graph_child_keys_checked(index, node))
            frontier = nxt
            if not frontier:
                break
        return False

    def _map_graph_child_keys_checked(self, index: dict[tuple[int, int], dict[str, Any]], node: dict[str, Any]) -> list[tuple[int, int]]:
        return [key for key in self._map_child_keys(node) if key in index]

    def _note_route_choice(self, node: dict[str, Any] | None, graph: dict[str, Any] | None = None) -> None:
        """记下"我选的这条路前面有没有精英"。

        商店界面拿不到地图（见 `_shop_elite_ahead`），所以结论必须在**选路那一刻**算好存起来。
        同一个值还供战斗用药水判断"这是不是精英/Boss 战"（§八节流）。
        """
        node_type = self._map_node_weight_key(node) if isinstance(node, dict) else ""
        self._last_route_node_type = node_type
        if node_type in {"elite", "boss"}:
            self._route_elite_ahead = True
            return
        self._route_elite_ahead = self._route_elite_lookahead(graph, node) if (graph and isinstance(node, dict)) else False

    def _preferred_map_index(self, summary_context: dict[str, Any], preferences: dict[str, Any], snapshot: dict[str, Any] | None = None) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        travelable_nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        current_hp = self._safe_int(payload.get("current_hp")) or 0
        max_hp = self._safe_int(payload.get("max_hp")) or 0
        gold = self._safe_int(payload.get("gold")) or 0
        hp_ratio = (current_hp / max_hp) if max_hp > 0 else 0.0
        route_policy = self._route_policy(preferences)
        # 优先走**链式递推**：拿到整幕 DAG 就对每个候选评估"递归到 boss 的整条路线"。
        # 拿不到图（旧 mod / payload 里没有 map.nodes）才退回单步评分，行为与旧版一致。
        graph = self._map_graph_from_snapshot(snapshot)
        if graph:
            scored: list[tuple[int, float]] = []
            best_route: tuple[float, int] | None = None
            nodes_by_index: dict[int, dict[str, Any]] = {}
            for position, node in enumerate(travelable_nodes):
                if not isinstance(node, dict):
                    continue
                route_score = self.evaluate_map_route(
                    graph,
                    node,
                    route_policy=route_policy,
                    hp_ratio=hp_ratio,
                    gold=gold,
                )
                if route_score is None:
                    best_route = None
                    break
                node_index = self._safe_int(node.get("index"))
                if node_index is None:
                    node_index = position
                scored.append((node_index, route_score))
                nodes_by_index[node_index] = node
                candidate = (-route_score, node_index)
                if best_route is None or candidate < best_route:
                    best_route = candidate
            if best_route is not None:
                self._log_map_route(scored, best_route[1], hp_ratio=hp_ratio, gold=gold)
                # 顺手记下"这条路前面有没有精英/是不是精英房"：商店里拿不到地图（见 _shop_elite_ahead），
                # 战斗里也只有节点类型能告诉我们这是不是精英/Boss 战（见 in_elite_or_boss_combat）。
                self._note_route_choice(nodes_by_index.get(best_route[1]), graph)
                # 有图就一律取评估最优：**"每条路都不好"正是最需要认真挑的时候**，
                # 这时退回"随手取最左边"等于把评估结果扔了。
                return best_route[1]
        best_candidate: tuple[int, int] | None = None
        best_node: dict[str, Any] | None = None
        for index, node in enumerate(travelable_nodes):
            if not isinstance(node, dict):
                continue
            score = self._score_map_node(node, route_policy, hp_ratio, gold)
            node_index = self._safe_int(node.get("index"))
            if node_index is None:
                node_index = index
            candidate = (-score, node_index)
            if best_candidate is None or candidate < best_candidate:
                best_candidate = candidate
                best_node = node
        if best_candidate is not None and best_candidate[0] < 0:
            self._note_route_choice(best_node, graph)
            return best_candidate[1]
        preferred = self._first_record_option(preferences)
        chosen = preferred if preferred is not None else self._first_available_map_index(summary_context)
        # 拿不到图也要记账（至少知道"选的是不是精英房"）；此时算不出"前方有没有精英"。
        for index, node in enumerate(travelable_nodes):
            if not isinstance(node, dict):
                continue
            node_index = self._safe_int(node.get("index"))
            if node_index is None:
                node_index = index
            if node_index == chosen:
                self._note_route_choice(node, graph)
                break
        return chosen

    def _score_map_node(self, node: dict[str, Any], route_policy: dict[str, Any], hp_ratio: float, gold: int) -> int:
        node_type = self._map_node_weight_key(node)
        weights = self._route_effective_weights(route_policy, hp_ratio, gold)
        return int(weights.get(node_type, 0))

    def _route_effective_weights(self, route_policy: dict[str, Any], hp_ratio: float, gold: int) -> dict[str, float]:
        """把"内置打底 -> 玩家配置 -> 低血替换"整理成一张明确的权重表。

        优先级：内置保守表 < 玩家的 route_policy.weights；血量低于阈值时整张表换成低血表
        （玩家配了 conditions.low_hp.weights 就用玩家的，否则用内置 SAFE_MAP_LOW_HP_WEIGHTS）。

        注意：低血替换现在对**自动选路**也生效了。以前只有玩家手点的"定个路线 safe"会走
        `_safe_route_preferences`（带 conditions），自动选路拿到的 route_policy 是空表，于是
        `_score_map_node` 内部的兜底里只有商店/精英两处硬编码的血量判断 —— 血再低也不会整体转向休息。
        """
        base: dict[str, float] = {}
        user_weights = route_policy.get("weights") if isinstance(route_policy.get("weights"), dict) else {}
        if user_weights:
            for key, value in user_weights.items():
                try:
                    base[str(key).strip().lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        else:
            # 没配过就用与历史行为一致的内置值（这样"未配置"时选路口径不变）。
            base = {
                "rest": 12.0,
                "shop": 8.0 if gold >= ROUTE_BOSS_PREP_GOLD else 4.0,
                "elite": -10.0 if hp_ratio < SAFE_MAP_HP_RATIO_BELOW else -4.0,
                "monster": 3.0,
                "event": 4.0,
                "treasure": 5.0,
                "question": 4.0,
            }
        conditions = route_policy.get("conditions") if isinstance(route_policy.get("conditions"), dict) else {}
        low_hp = conditions.get("low_hp") if isinstance(conditions.get("low_hp"), dict) else {}
        try:
            threshold = float(low_hp.get("hp_ratio_below", SAFE_MAP_HP_RATIO_BELOW))
        except (TypeError, ValueError):
            threshold = SAFE_MAP_HP_RATIO_BELOW
        if hp_ratio < threshold:
            low_table = low_hp.get("weights") if isinstance(low_hp.get("weights"), dict) else SAFE_MAP_LOW_HP_WEIGHTS
            for key, value in low_table.items():
                try:
                    base[str(key).strip().lower()] = float(value)
                except (TypeError, ValueError):
                    continue
        return base

    # ---- 全图 DAG 的读取与链式递推 ---------------------------------------------------------

    def _map_graph_from_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        """从 snapshot 里取出整幕地图 DAG（mod /state 的 map.nodes）。

        为什么从 snapshot 取而不是塞进 summary_context.payload：那张图有几十个节点，
        payload 会跟着 sync 包推给 NEKO，之前已经因为"往状态里塞大对象"踩过 ZMQ 超时。
        图只在规划器内部用，就地取最省事。
        """
        if not isinstance(snapshot, dict):
            return {}
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        game_map = raw_state.get("map") if isinstance(raw_state.get("map"), dict) else {}
        nodes = [node for node in (game_map.get("nodes") if isinstance(game_map.get("nodes"), list) else []) if isinstance(node, dict)]
        if not nodes:
            return {}
        return {"nodes": nodes, "boss_node": game_map.get("boss_node"), "rows": game_map.get("rows")}

    def _map_graph_index(self, graph: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
        """(row, col) -> 节点。mod 的 children/parents 是坐标对，必须靠这张索引才能解出类型。"""
        index: dict[tuple[int, int], dict[str, Any]] = {}
        for node in graph.get("nodes") if isinstance(graph.get("nodes"), list) else []:
            if not isinstance(node, dict):
                continue
            row = self._safe_int(node.get("row"))
            col = self._safe_int(node.get("col"))
            if row is None or col is None:
                continue
            index[(row, col)] = node
        return index

    def _map_boss_row(self, graph: dict[str, Any], index: dict[tuple[int, int], dict[str, Any]]) -> int | None:
        boss = graph.get("boss_node") if isinstance(graph.get("boss_node"), dict) else {}
        row = self._safe_int(boss.get("row"))
        if row is not None:
            return row
        rows = [r for (r, _c), node in index.items() if bool(node.get("is_boss"))]
        return max(rows) if rows else None

    def _map_child_keys(self, node: dict[str, Any]) -> list[tuple[int, int]]:
        """节点的子节点坐标列表（mod 给的是 children: [{row, col}, ...]）。"""
        keys: list[tuple[int, int]] = []
        for child in node.get("children") if isinstance(node.get("children"), list) else []:
            if not isinstance(child, dict):
                continue
            row = self._safe_int(child.get("row"))
            col = self._safe_int(child.get("col"))
            if row is None or col is None:
                continue
            keys.append((row, col))
        return keys

    def evaluate_map_route(
        self,
        graph: dict[str, Any],
        start: dict[str, Any],
        *,
        route_policy: dict[str, Any] | None = None,
        hp_ratio: float = 1.0,
        gold: int = 0,
        lookahead_rows: int | None = None,
    ) -> float | None:
        """从 `start` 这个候选节点出发、递归到 boss 的**整条路线**评分。

        - 分支点取 **max**：到那一层我们还会再选一次，所以是"沿途都选最好"而不是求和。
        - 每远一层乘 `ROUTE_DISCOUNT_PER_ROW`：远处的不确定性更大。
        - 同类节点按出现次数递减（`ROUTE_REPEAT_DECAY`）：两个休息点不等于两倍休息。
        - 临门（距 boss <= ROUTE_BOSS_PREP_ROWS 层）的休息/商店加权：补血、把金币花掉。
        - 精英按血量缓和：血越满，精英那点机会成本越低（精英给遗物+金币，未必是坏事）。

        返回 None 表示拿不到图（旧 mod / 非地图界面），调用方应退回单步评分。
        """
        index = self._map_graph_index(graph)
        if not index:
            return None
        row = self._safe_int(start.get("row"))
        col = self._safe_int(start.get("col"))
        if row is None or col is None or (row, col) not in index:
            return None
        policy = route_policy if isinstance(route_policy, dict) else {}
        weights = self._route_effective_weights(policy, hp_ratio, gold)
        try:
            depth = int(lookahead_rows if lookahead_rows is not None else (policy.get("lookahead_depth") or ROUTE_LOOKAHEAD_ROWS_FALLBACK))
        except (TypeError, ValueError):
            depth = ROUTE_LOOKAHEAD_ROWS_FALLBACK
        boss_row = self._map_boss_row(graph, index)
        return self._route_value(
            index,
            (row, col),
            weights=weights,
            hp_ratio=hp_ratio,
            gold=gold,
            boss_row=boss_row,
            depth=max(1, depth),
            counters={},
            path=set(),
        )

    def _route_value(
        self,
        index: dict[tuple[int, int], dict[str, Any]],
        key: tuple[int, int],
        *,
        weights: dict[str, float],
        hp_ratio: float,
        gold: int,
        boss_row: int | None,
        depth: int,
        counters: dict[str, int],
        path: set[tuple[int, int]],
    ) -> float:
        node = index.get(key)
        if node is None or depth < 0 or key in path:
            return 0.0
        node_type = self._map_node_weight_key(node)
        value = float(weights.get(node_type, 0.0))

        # 同类饱和：第 k 次出现按 ROUTE_REPEAT_DECAY^(k-1) 递减。
        seen = counters.get(node_type, 0)
        value *= ROUTE_REPEAT_DECAY ** seen

        # 精英缓和：血越满，精英那点机会成本越低。
        if node_type == "elite" and value < 0:
            relief = min(1.0, max(0.0, hp_ratio)) * ROUTE_ELITE_RELIEF_MAX
            value *= (1.0 - relief)

        # 临门一脚：紧邻 boss 的休息/商店价值远高于中途。
        if node_type in {"rest", "shop"} and self._is_boss_prep_row(node, boss_row):
            if node_type != "shop" or gold >= ROUTE_BOSS_PREP_GOLD:
                value *= ROUTE_BOSS_PREP_MULTIPLIER

        counters[node_type] = seen + 1
        path.add(key)
        best_child = 0.0
        if depth > 0:
            for child_key in self._map_child_keys(node):
                best_child = max(
                    best_child,
                    self._route_value(
                        index,
                        child_key,
                        weights=weights,
                        hp_ratio=hp_ratio,
                        gold=gold,
                        boss_row=boss_row,
                        depth=depth - 1,
                        counters=counters,
                        path=path,
                    ),
                )
        path.discard(key)
        counters[node_type] = seen
        return value + ROUTE_DISCOUNT_PER_ROW * best_child

    @staticmethod
    def _map_node_weight_key(node: dict[str, Any]) -> str:
        """node_type -> 权重表的键。认不出就原样返回（玩家仍可为原始名配权重）。"""
        raw = str(node.get("type") or node.get("node_type") or "").strip().lower()
        if not raw:
            return ""
        return _MAP_NODE_TYPE_ALIASES.get(raw, raw)

    @staticmethod
    def _is_boss_prep_row(node: dict[str, Any], boss_row: int | None) -> bool:
        if boss_row is None:
            return False
        own_row = node.get("row")
        if not isinstance(own_row, int) or isinstance(own_row, bool):
            try:
                own_row = int(own_row)
            except (TypeError, ValueError):
                return False
        return 0 <= (boss_row - own_row) <= ROUTE_BOSS_PREP_ROWS
