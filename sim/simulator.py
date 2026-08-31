"""用效果引擎驱动一个回合；并提供面向差分测试的便捷入口。"""
from __future__ import annotations

from .cards import card as get_card
from .effects import apply_card, draw_cards, tick_turn_end
from .monster_ai import predict_next, table
from .state import BattleState, CardInstance, EnemyState, PlayerState


def start_turn(state: BattleState, player: PlayerState, *, draw: int = 5) -> None:
    player.energy = player.max_energy
    draw_cards(state, player, draw)


def new_turn(state: BattleState, player: PlayerState, *, draw: int = 5) -> None:
    """回合结束进入下一回合：弃掉手中牌 → 重置能量 → 抽满手牌。"""
    player.discard.extend(c.card_id for c in player.hand)
    player.hand = []
    player.energy = player.max_energy
    draw_cards(state, player, draw)


def play_hand_index(
    state: BattleState,
    player: PlayerState,
    hand_index: int,
    target: int | None = None,
) -> bool:
    """从手牌按下标打出一张。成功返回 True。"""
    if hand_index < 0 or hand_index >= len(player.hand):
        return False
    card = player.hand[hand_index]
    if card.cost > player.energy:
        return False
    apply_card(state, player, card, target)
    player.hand.pop(hand_index)
    return True


def end_turn(state: BattleState, player: PlayerState) -> None:
    """回合末：tick 玩家 Power（中毒），敌人按当前意图行动，然后用 follow-up 表预测下一招。"""
    tick_turn_end(state, player)
    for enemy in state.enemies:
        if not enemy.alive:
            continue
        # 敌人执行当前意图：伤害 + 本招自带的格挡/buff（如 SLICE 的 DefendIntent、HISS 的力量）
        cur = table(enemy.enemy_id).get(enemy.move_id) if enemy.enemy_id else None
        if enemy.intent_attack and enemy.intent_damage and state.players:
            target = _primary_target(state, player)
            remaining = enemy.intent_damage
            if target.block > 0:
                absorbed = min(target.block, remaining)
                target.block -= absorbed
                remaining -= absorbed
            if remaining > 0:
                target.hp = max(0, target.hp - remaining)
        if cur is not None:
            if cur.block:
                enemy.block += cur.block
            if cur.buff_power:
                enemy.add_power(cur.buff_power, cur.buff_amount)
        # 预测下一招（monster_ai.followup 循环）
        nxt = predict_next(enemy.enemy_id, enemy.move_id)
        enemy.move_id = nxt.move_id
        enemy.intent_damage = nxt.damage
        enemy.intent_label = nxt.move_id
        enemy.intent_attack = nxt.damage > 0
    state.turn += 1


def _primary_target(state: BattleState, player: PlayerState) -> PlayerState:
    # v1 单玩家：就是 player。co-op 时按敌人意图目标/受击玩家扩展。
    return player


def build_battle(
    *,
    player_hp: int = 75,
    max_hp: int = 75,
    energy: int = 3,
    enemies: list[tuple[str, int]] | None = None,
    enemy_block: int = 0,
    intent_damage: int = 0,
    intent_attack: bool = False,
) -> BattleState:
    """快速建一个战斗局（默认 1 玩家 vs 敌人）。"""
    enemy_list: list[EnemyState] = []
    for i, (eid, hp) in enumerate(enemies or []):
        enemy_list.append(EnemyState(
            id=str(i), hp=hp, max_hp=hp, block=enemy_block,
            intent_damage=intent_damage, intent_label="attack" if intent_attack else None,
            intent_attack=intent_attack,
        ))
    player = PlayerState(id="p0", hp=player_hp, max_hp=max_hp, energy=energy, max_energy=energy)
    return BattleState(players=[player], enemies=enemy_list, turn=1)


def from_cards(card_ids: list[str]) -> list[CardInstance]:
    """"按 id 实例化手牌（从 cards.json）。"""
    out: list[CardInstance] = []
    for cid in card_ids:
        c = get_card(cid)
        if c is not None:
            out.append(c)
    return out


__all__ = ["start_turn", "play_hand_index", "end_turn", "build_battle", "from_cards"]
