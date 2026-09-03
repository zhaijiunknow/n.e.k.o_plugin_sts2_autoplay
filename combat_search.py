"""CombatSolver 思路的战斗决策大脑（回合内前瞻搜索）。

只做"推荐"，不出牌。输入插件拿到的战斗状态（来自 mod 的 /state 快照，
snapshot.raw_state.combat），枚举当前回合内可玩的卡牌序列 + 结束回合，
模拟每个动作对局面（能量/敌人血量/格挡/威胁）的影响，用 CombatSolver 式的
评分对落点排序，返回推荐动作（打哪张、打谁，或结束回合）。

与 CombatSolver 的对应：
- CombatSolver 用跨回合 Beam 搜索；这里做"回合内"beam/greedy（能量约束下的
  出牌顺序搜索），并预留扩展到跨回合的结构（score 里已把"敌人的下一意图"算作威胁）。
- CombatSolver 的评分偏"战损/药水/胜负"；这里改成可直接从快照算的等价项：
  斩杀 > 溢出伤害、伤害/能量比、格挡抵挡即将到来的意图伤害、保留手牌。

不参与实际出牌 —— 由外层（NEKO agent / 自动玩循环）拿着推荐去调 play_card/end_turn。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 状态模型（从快照解析）
# ---------------------------------------------------------------------------

@dataclass
class Card:
    index: int
    card_id: str
    name: str
    cost: int
    damage: int = 0
    block: int = 0
    targets: list[int] = field(default_factory=list)
    target_index_space: str | None = None
    playable: bool = True
    card_type: str = ""

    @property
    def value(self) -> float:
        return max(float(self.damage), float(self.block))


@dataclass
class Enemy:
    index: int
    enemy_id: str
    hp: int
    max_hp: int
    block: int = 0
    intent_damage: int = 0
    intent_label: str | None = None
    is_attack_intent: bool = False

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class CombatState:
    player_id: str
    energy: int
    block: int
    current_hp: int
    max_hp: int
    hand: list[Card]
    enemies: list[Enemy]
    draw_count: int = 0

    def clone(self) -> "CombatState":
        return CombatState(
            player_id=self.player_id,
            energy=self.energy,
            block=self.block,
            current_hp=self.current_hp,
            max_hp=self.max_hp,
            hand=[c for c in self.hand],
            enemies=[Enemy(e.index, e.enemy_id, e.hp, e.max_hp, e.block,
                           e.intent_damage, e.intent_label, e.is_attack_intent) for e in self.enemies],
            draw_count=self.draw_count,
        )


# ---------------------------------------------------------------------------
# 解析快照的 combat 段
# ---------------------------------------------------------------------------

def parse_combat(snapshot: dict[str, Any]) -> CombatState | None:
    raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
    combat = raw.get("combat") if isinstance(raw.get("combat"), dict) else None
    if not combat:
        return None
    player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
    hand_list = combat.get("hand") if isinstance(combat.get("hand"), list) else []
    enemies_list = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []

    hand: list[Card] = []
    for card in hand_list:
        if not isinstance(card, dict):
            continue
        dmg = _lookup_dynamic(card, "Damage")
        blk = _lookup_dynamic(card, "Block")
        hand.append(Card(
            index=int(card.get("index", 0)),
            card_id=str(card.get("card_id") or ""),
            name=str(card.get("name") or card.get("card_id") or ""),
            cost=int(card.get("energy_cost") or 0),
            damage=dmg,
            block=blk,
            targets=list(card.get("valid_target_indices") or []),
            target_index_space=card.get("target_index_space"),
            playable=bool(card.get("playable", True)),
            card_type=str(card.get("card_type") or ""),
        ))

    enemies: list[Enemy] = []
    for enemy in enemies_list:
        if not isinstance(enemy, dict):
            continue
        intents = enemy.get("intents") if isinstance(enemy.get("intents"), list) else []
        total_dmg = 0
        is_attack = False
        label = None
        for int_ in intents:
            if not isinstance(int_, dict):
                continue
            if int_.get("intent_type") == "Attack":
                total_dmg += int(int_.get("total_damage") or 0)
                is_attack = True
                label = int_.get("label") or label
        enemies.append(Enemy(
            index=int(enemy.get("index", 0)),
            enemy_id=str(enemy.get("enemy_id") or ""),
            hp=int(enemy.get("current_hp") or enemy.get("hp") or 0),
            max_hp=int(enemy.get("max_hp") or 0),
            block=int(enemy.get("block") or 0),
            intent_damage=total_dmg,
            intent_label=label,
            is_attack_intent=is_attack,
        ))

    return CombatState(
        player_id=str(player.get("id") or player.get("player_id") or snapshot.get("player_id") or ""),
        energy=int(player.get("energy") or 0),
        block=int(player.get("block") or 0),
        current_hp=int(player.get("current_hp") or player.get("hp") or 0),
        max_hp=int(player.get("max_hp") or 1),
        hand=hand,
        enemies=enemies,
        draw_count=len(combat.get("draw_cards") if isinstance(combat.get("draw_cards"), list) else []),
    )


def _lookup_dynamic(card: dict[str, Any], key: str) -> int:
    for dv in card.get("dynamic_values") or []:
        if isinstance(dv, dict) and dv.get("name") == key:
            return int(dv.get("current_value") or dv.get("base_value") or 0)
    return 0


# ---------------------------------------------------------------------------
# 回合内搜索
# ---------------------------------------------------------------------------

@dataclass
class Played:
    card_index: int
    target_index: int | None
    card_name: str


@dataclass
class SearchNode:
    state: CombatState
    played: list[Played]
    damage_dealt: int = 0
    block_gained: int = 0


def apply_play(state: CombatState, card: Card, target_index: int | None) -> None:
    """In-place：打出卡片，改动能量/敌人血/格挡。damage→block 先抵消。"""
    state.energy = max(0, state.energy - card.cost)
    if card.damage and card.target_index_space == "enemies":
        _deal_damage(state, card.damage, target_index)
    if card.block:
        state.block += card.block
    # 从手牌移除（按 index 匹配）
    for i, c in enumerate(state.hand):
        if c.index == card.index:
            state.hand.pop(i)
            break


def _deal_damage(state: CombatState, amount: int, target_index: int | None) -> None:
    enemies = sorted(state.enemies, key=lambda e: e.index)
    if target_index is not None:
        target = next((e for e in enemies if e.index == target_index), None)
        if target:
            _deal_damage_to(state, target, amount)
        return
    # 无目标：打第一个活着/可打的（近似）
    for enemy in enemies:
        if enemy.alive:
            _deal_damage_to(state, enemy, amount)
            return


def _deal_damage_to(state: CombatState, enemy: Enemy, amount: int) -> None:
    remaining = amount
    if enemy.block > 0:
        absorbed = min(enemy.block, remaining)
        enemy.block -= absorbed
        remaining -= absorbed
    if remaining > 0:
        enemy.hp = max(0, enemy.hp - remaining)


def incoming_threat(state: CombatState) -> int:
    """敌人当前意图对玩家造成的伤害（下一回合）。"""
    return sum(e.intent_damage for e in state.enemies if e.alive and e.is_attack_intent)


def _state_score(state: CombatState) -> float:
    """CombatSolver 式评分：评估"落点局面"好坏，越高越好。

    核心（对应 CombatSolver 的胜负/战损/威胁）：
    - 斩杀：能把敌人清掉 → 巨大奖励（优先于一切）。
    - 血量进度：越接近清场越好（鼓励集中削弱势敌人血）。
    - 受伤：敌人当前意图伤害 - 我方格挡 = 预期掉血，掉血重罚。
    - 刻意不含"保留手牌"奖励 —— 打出去才有用，留牌不是目标。
    """
    total_enemy_hp = sum(e.hp for e in state.enemies if e.alive)
    if total_enemy_hp == 0:
        return 1e6

    lethal_threat = incoming_threat(state)
    hp_loss = max(0, lethal_threat - state.block)
    total_max = max(1, sum(e.max_hp for e in state.enemies))

    score = 0.0
    # 血量进度（0..20）：清掉越多分越高；同时按"最少剩血"倾向集中斩杀
    score += (sum(e.max_hp - e.hp for e in state.enemies if e.alive)) / total_max * 20.0
    # 掉血惩罚
    score -= hp_loss * 10.0
    # 轻微偏好：能量余额适中（不惩罚，仅作 tie-break）
    score += min(state.energy, 2) * 0.1
    return score


def search_best_sequence(state: CombatState, *, beam_width: int = 12, max_depth: int = 8) -> tuple[list[Played], float]:
    """回合内 beam 搜索：能量/深度约束下枚举"打哪张+结束回合"。"""
    from collections import deque
    results: list[tuple[list[Played], float]] = []
    queue: deque[SearchNode] = deque([SearchNode(state.clone(), [])])

    for _depth in range(max_depth):
        if not queue:
            break
        next_frontier: list[SearchNode] = []
        for node in queue:
            s = node.state
            # v1 只建模 伤害/格挡。无即时数值效应的卡（球/增益等）不进入搜索，
            # 避免推荐"看上去没用"的动作；后续扩展 buff/orb 建模再接回。
            playable = [c for c in s.hand if c.playable and c.cost <= s.energy and (c.damage or c.block)]
            if not playable:
                # 没牌可打/没能量 → 结束回合
                results.append((list(node.played), _state_score(s)))
                continue
            for card in playable:
                targets = card.targets if card.target_index_space == "enemies" else [None]
                if not targets:
                    targets = [None]
                for tgt in targets:
                    nxt = s.clone()
                    apply_play(nxt, next(c for c in nxt.hand if c.index == card.index), tgt)
                    dmg = card.damage
                    blk = card.block
                    new_node = SearchNode(nxt, node.played + [Played(card.index, tgt, card.name)],
                                          node.damage_dealt + dmg, node.block_gained + blk)
                    next_frontier.append(new_node)
            # 也可以在任意时刻结束回合
            results.append((list(node.played), _state_score(s)))
        # beam：保留分最高的 N 个继续
        next_frontier.sort(key=lambda n: _state_score(n.state), reverse=True)
        queue = deque(next_frontier[:beam_width])

    if not results:
        return [], _state_score(state.clone())
    best = max(results, key=lambda r: r[1])
    return best[0], best[1]


# ---------------------------------------------------------------------------
# 对外：给一次推荐
# ---------------------------------------------------------------------------

def recommend_action(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    state = parse_combat(snapshot)
    if state is None:
        return None
    if not state.hand:
        return {"action": "end_turn", "reason": "no_hand", "score": _state_score(state)}

    sequence, score = search_best_sequence(state)
    if not sequence:
        return {"action": "end_turn", "reason": "nothing_playable", "score": score}

    first = sequence[0]
    return {
        "action": "play_card",
        "card_index": first.card_index,
        "target_index": first.target_index,
        "card_name": first.card_name,
        "sequence": [(p.card_index, p.target_index, p.card_name) for p in sequence],
        "score": round(score, 3),
        "reason": "combat_search",
    }
