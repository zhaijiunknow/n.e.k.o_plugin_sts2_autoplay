"""战斗模拟的状态模型。

都是纯数据：一个战斗局 = 若干玩家 + 若干敌人，每方是"Combatant"（血量/格挡/Power 层数）。
卡/效果的数据来自 cards.json（见 sim/cards.py），Power 的语义来自 sim/effects.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CardInstance:
    """一张已经进入战斗的卡（由 cards.json 的字段 + 动态值实例化）。"""
    card_id: str = ""
    name: str = ""
    cost: int = 0
    card_type: str = ""          # Attack / Skill / Power
    target: str = "Self"         # Self / AnyEnemy / AllEnemies / AnyAlly ...
    damage: int = 0
    block: int = 0
    hit_count: int = 0
    cards_draw: int = 0
    energy_gain: int = 0
    hp_loss: int = 0
    powers_applied: list[tuple[str, int]] = field(default_factory=list)  # [(power_id, amount)]
    keywords: list[str] = field(default_factory=list)   # EXHAUST / RETAIN / INNATE / ETHEREAL / ETERNAL / UNPLAYABLE
    orb_action: list[tuple[str, str, int]] = field(default_factory=list)  # (channel|evoke, orb_id, times)


@dataclass
class Combatant:
    id: str = ""
    hp: int = 0
    max_hp: int = 0
    block: int = 0
    powers: dict[str, int] = field(default_factory=dict)  # power_id(lower) -> stacks

    def power(self, power_id: str) -> int:
        return self.powers.get(power_id.lower(), 0)

    def add_power(self, power_id: str, amount: int) -> None:
        key = power_id.lower()
        self.powers[key] = self.powers.get(key, 0) + amount

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class Orb:
    orb_id: str = "LIGHTNING"   # LIGHTNING / FROST / PLASMA / DARK
    passive: int = 0            # 被动值
    evoke: int = 0              # 激发值


@dataclass
class PlayerState(Combatant):
    energy: int = 0
    max_energy: int = 3
    hand: list[CardInstance] = field(default_factory=list)
    draw: list[str] = field(default_factory=list)       # card_id 队列
    discard: list[str] = field(default_factory=list)
    exhausted: list[str] = field(default_factory=list)
    orbs: list[Orb] = field(default_factory=list)       # 有序球队列，index0=front
    orb_capacity: int = 0
    focus: int = 0
    potions: list[str] = field(default_factory=list)      # 持有的药水 id（战斗内可用）
    relics: list[str] = field(default_factory=list)       # 持有的遗物 id（战斗被动）

    def clone(self) -> "PlayerState":
        return PlayerState(
            id=self.id, hp=self.hp, max_hp=self.max_hp, block=self.block,
            powers=dict(self.powers), energy=self.energy, max_energy=self.max_energy,
            hand=list(self.hand), draw=list(self.draw), discard=list(self.discard),
            exhausted=list(self.exhausted),
            orbs=list(self.orbs), orb_capacity=self.orb_capacity, focus=self.focus,
            potions=list(self.potions), relics=list(self.relics),
        )


@dataclass
class EnemyState(Combatant):
    intent_damage: int = 0      # 本回合对玩家的意图伤害（不含 debuff）
    intent_label: str | None = None
    intent_attack: bool = False
    move_id: str = ""           # 当前招（如 BUTT_MOVE），用于预测下一招
    enemy_id: str = ""

    def clone(self) -> "EnemyState":
        return EnemyState(
            id=self.id, hp=self.hp, max_hp=self.max_hp, block=self.block,
            powers=dict(self.powers), intent_damage=self.intent_damage,
            intent_label=self.intent_label, intent_attack=self.intent_attack,
            move_id=self.move_id, enemy_id=self.enemy_id,
        )


@dataclass
class BattleState:
    players: list[PlayerState] = field(default_factory=list)
    enemies: list[EnemyState] = field(default_factory=list)
    turn: int = 1
    # 提供给效果表/结算的只读上下文（哪些玩家在场、对方玩家等），跨玩家时用
    ctx: dict[str, Any] = field(default_factory=dict)

    def enemy_by_index(self, index: int) -> EnemyState | None:
        alive = sorted((e for e in self.enemies if e.alive), key=lambda e: e.id)
        return alive[index] if 0 <= index < len(alive) else None

    def total_enemy_hp(self) -> int:
        return sum(e.hp for e in self.enemies if e.alive)

    def clone(self) -> "BattleState":
        return BattleState(
            players=[p.clone() for p in self.players],
            enemies=[e.clone() for e in self.enemies],
            turn=self.turn,
            ctx=dict(self.ctx),
        )


__all__ = ["CardInstance", "Combatant", "PlayerState", "EnemyState", "BattleState"]
