"""跨回合 beam 搜索（CombatSolver 思路）。

在已验证的模拟器上（state/effects/monster_ai）向前推 N 个回合：
- 每个玩家回合：在能量约束下枚举出牌序列（beam），并允许结束回合；
- 结束回合 → 敌人按 monster_ai 预测执行/换招 → 进入下一回合（弃牌/重置能量/抽牌）；
- 反复到 horizon，按落点评分（斩杀>战损>剩余HP）beam 保留最优；
- 返回最优整条线（每回合的打法）+ 第一步的推荐动作（映射回当前手牌下标）。

只做推荐，不出牌。—— 由外层拿推荐去执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import BattleState, PlayerState
from .simulator import play_hand_index, end_turn, new_turn


@dataclass
class SearchNode:
    state: BattleState
    line: list[tuple[Any, ...]] = field(default_factory=list)   # ("PLAY", card_id, target) / ("END",)
    score: float = 0.0


def _score(state: BattleState) -> float:
    """落点评分（CombatSolver 式）：斩杀优先，然后掉血/战损，最后剩HP。"""
    total_enemy = sum(e.hp for e in state.enemies if e.alive)
    if total_enemy == 0:
        return 1e6
    player = state.players[0]
    progress = sum(e.max_hp - e.hp for e in state.enemies) / max(1, sum(e.max_hp for e in state.enemies))
    hp_lost = player.max_hp - player.hp
    return progress * 20.0 - hp_lost * 3.0 + player.block * 0.5


def _playable_moves(state: BattleState, player: PlayerState) -> list[tuple[int, int | None]]:
    """当前可打出的牌：(手牌下标, 目标下标)。只支持我们已建模范畴（伤害/格挡）的牌。"""
    moves: list[tuple[int, int | None]] = []
    for i, card in enumerate(player.hand):
        if card.cost > player.energy or card.cost < 0:
            continue
        if not (card.damage or card.block):
            continue  # v1 不推荐无即时数值的卡（球/增益等）
        tgt = (card.target or "").lower()
        if tgt in ("anyenemy", "allenemies"):
            for cur in range(len(state.enemies)):
                moves.append((i, cur))
        else:
            moves.append((i, None))
    return moves


def search(
    state: BattleState,
    *,
    horizon: int = 2,
    beam_width: int = 16,
    max_turn_actions: int = 6,
) -> SearchNode:
    """从当前（玩家回合中）局面开始，向前推 horizon 个玩家回合，返回最优线。"""
    player = state.players[0]
    # 每回合起点 = (状态, 线, 分)。第一回合从当前状态开始（能量/手牌已就位）。
    turn_nodes: list[SearchNode] = [SearchNode(state.clone(), [], _score(state.clone()))]

    for _turn in range(horizon):
        outcomes: list[SearchNode] = []
        for node in turn_nodes:
            # --- 回合内 beam：枚举出牌 → 每步保留分最高 beam 个 ---
            mid = [node]
            for _ in range(max_turn_actions):
                nextm: list[SearchNode] = []
                for m in mid:
                    for idx, tgt in _playable_moves(m.state, player):
                        c = m.state.clone()
                        hand = c.players[0].hand
                        card_id = hand[idx].card_id if idx < len(hand) else ""
                        if play_hand_index(c, c.players[0], idx, tgt):
                            nextm.append(SearchNode(c, m.line + [("PLAY", card_id, tgt)], _score(c)))
                if not nextm:
                    break
                nextm.sort(key=lambda n: n.score, reverse=True)
                mid = nextm[:beam_width]
            # --- 每个回合内状态都"结束回合" → 进入下一回合 ---
            for m in mid:
                e = m.state.clone()
                end_turn(e, e.players[0])
                new_turn(e, e.players[0])
                outcomes.append(SearchNode(e, m.line + [("END",)], _score(e)))
        outcomes.sort(key=lambda n: n.score, reverse=True)
        turn_nodes = outcomes[:beam_width]

    best = max(turn_nodes, key=lambda n: n.score)
    return best


def first_recommendation(best: SearchNode, live_state: BattleState) -> dict[str, Any] | None:
    """从最优线里取第一步，映射为当前手牌下标（供外层执行 play_card）。"""
    for step in best.line:
        if step[0] == "PLAY":
            card_id = step[1]
            target = step[2]
            # 在 live 手牌里找该 card_id 的下标
            hand = live_state.players[0].hand
            idx = next((i for i, c in enumerate(hand) if c.card_id == card_id), None)
            if idx is None:
                return {"action": "end_turn", "reason": "recommended_card_not_in_hand"}
            return {"action": "play_card", "card_index": idx, "target_index": target,
                    "card_id": card_id, "line": [(s[0], s[1] if len(s) > 1 else s[0]) for s in best.line],
                    "score": round(best.score, 3)}
    return {"action": "end_turn", "reason": "recommended_moves_exhausted"}


__all__ = ["search", "first_recommendation", "SearchNode"]
