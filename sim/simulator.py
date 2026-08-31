"""用效果引擎驱动一个回合；并提供面向差分测试的便捷入口。"""
from __future__ import annotations

from .cards import card as get_card
from .effects import apply_card, apply_potion, draw_cards, tick_turn_end, process_orbs_turn_end
from .monster_ai import predict_next, table
from .state import BattleState, CardInstance, EnemyState, PlayerState


def start_turn(state: BattleState, player: PlayerState, *, draw: int = 5) -> None:
    player.energy = player.max_energy
    draw_cards(state, player, draw)


def new_turn(state: BattleState, player: PlayerState, *, draw: int = 5) -> None:
    """回合结束进入下一回合：RETAIN 留手、ETHEREAL 在手则消耗、其余弃掉 → 重置能量 → 抽满。"""
    kept: list[CardInstance] = []
    for card in player.hand:
        kws = {k.upper() for k in (card.keywords or [])}
        if "RETAIN" in kws:
            kept.append(card)
        elif "ETHEREAL" in kws:
            player.exhausted.append(card.card_id)
        else:
            player.discard.append(card.card_id)
    player.hand = kept
    player.energy = player.max_energy
    draw_cards(state, player, draw)


def start_combat(state: BattleState, player: PlayerState, *, deck: list[CardInstance]) -> None:
    """开局：INNATE 卡直接进初始手牌，其余进抽牌堆。"""
    innate = [c for c in deck if "INNATE" in {k.upper() for k in (c.keywords or [])}]
    player.hand = innate
    player.draw = [c.card_id for c in deck if c not in innate]
    player.energy = player.max_energy


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


def use_potion(state: BattleState, player: PlayerState, potion_index: int, target: int | None = None) -> bool:
    """按玩家药水清单下标用一瓶。成功返回 True。"""
    if potion_index < 0 or potion_index >= len(player.potions):
        return False
    pid = player.potions[potion_index]
    return apply_potion(state, player, pid, target)


def end_turn(state: BattleState, player: PlayerState) -> None:
    """回合末：tick 玩家 Power（中毒），敌人按当前意图行动，然后用 follow-up 表预测下一招。"""
    tick_turn_end(state, player)
    process_orbs_turn_end(state, player)   # 玩家回合末：球被动+激发前端
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
            if cur.status_card and cur.status_amount:
                for _ in range(cur.status_amount):
                    player.hand.append(CardInstance(card_id="STATUS", name="状态卡",
                                                    cost=1, keywords=["UNPLAYABLE"]))
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
