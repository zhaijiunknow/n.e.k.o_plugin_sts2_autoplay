"""怪物出招预测：读懂敌人"下一回合出什么"。

数据来源（每只怪一张表）：
- move 效果（伤害/格挡/buff）从 monsters.json 的 damage_values + 反编译 MoveState 取。
- followup 循环从反编译 GenerateMoveStateMachine 的 FollowUpState 取。

原理：敌人 `INIT_MOVE` 只决定第一招；之后按 FollowUpState 循环出招。
给定当前招，预测下一招 = followup[current]，从而让模拟器能连续推多个回合。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EnemyMove:
    move_id: str
    damage: int = 0
    block: int = 0
    hits: int = 1                   # 多段攻击次数
    buff_power: str | None = None   # power_id,施放给自己
    buff_amount: int = 0
    status_card: str | None = None  # 往玩家手牌塞的状态卡（如黏糊）
    status_amount: int = 0
    followup: str | None = None     # 下一招 move_id


# move 表：enemy_id -> {move_id: EnemyMove}
# 数值取 ascension 档（DeadlyEnemies 伤害 / ToughEnemies 格挡），对应我们的 asc 10 局。
# 注：带 RandomBranch 的怪（LeafSlimeS / TwigSlimeM）这里取"攻击↔状态"的确定性循环近似；随机不在 v1 建模。
MOVE_TABLES: dict[str, dict[str, EnemyMove]] = {
    "NIBBIT": {
        "BUTT_MOVE": EnemyMove("BUTT_MOVE", damage=13, followup="SLICE_MOVE"),
        "SLICE_MOVE": EnemyMove("SLICE_MOVE", damage=7, block=6, followup="HISS_MOVE"),
        "HISS_MOVE": EnemyMove("HISS_MOVE", buff_power="STRENGTH_POWER", buff_amount=3, followup="BUTT_MOVE"),
    },
    "TWIG_SLIME_S": {  # 一直 TACKLE(5)
        "TACKLE_MOVE": EnemyMove("TACKLE_MOVE", damage=5, followup="TACKLE_MOVE"),
    },
    "LEAF_SLIME_S": {  # TACKLE(4) ↔ GOOP(状态卡)
        "TACKLE_MOVE": EnemyMove("TACKLE_MOVE", damage=4, followup="GOOP_MOVE"),
        "GOOP_MOVE": EnemyMove("GOOP_MOVE", status_card="STATUS", status_amount=1, followup="TACKLE_MOVE"),
    },
    "TWIG_SLIME_M": {  # STICKY(状态卡) ↔ POKEY_POUNCE(12)
        "STICKY_SHOT_MOVE": EnemyMove("STICKY_SHOT_MOVE", status_card="STATUS", status_amount=1, followup="POKEY_POUNCE_MOVE"),
        "POKEY_POUNCE_MOVE": EnemyMove("POKEY_POUNCE_MOVE", damage=12, followup="STICKY_SHOT_MOVE"),
    },
    "LEAF_SLIME_M": {  # STICKY(状态卡2) ↔ CLUMP(9)
        "CLUMP_SHOT_MOVE": EnemyMove("CLUMP_SHOT_MOVE", damage=9, followup="STICKY_SHOT_MOVE"),
        "STICKY_SHOT_MOVE": EnemyMove("STICKY_SHOT_MOVE", status_card="STATUS", status_amount=2, followup="CLUMP_SHOT_MOVE"),
    },
}


_GEN_CACHE: dict[str, dict[str, EnemyMove]] | None = None


def table(enemy_id: str) -> dict[str, EnemyMove]:
    """先查手写（已验证、含格挡/正确 followup），再用自动生成表兜底（覆盖面，但格挡等是启发式）。"""
    key = enemy_id.upper()
    if key in MOVE_TABLES:
        return MOVE_TABLES[key]
    global _GEN_CACHE
    if _GEN_CACHE is None:
        from . import monster_data  # 延迟导入，避免循环
        _GEN_CACHE = monster_data.MOVE_TABLES
    return _GEN_CACHE.get(key, {})


def predict_next(enemy_id: str, current_move_id: str | None) -> EnemyMove:
    """给定敌人当前招，返回下一招（followup）。查不到则回退：不追加伤害的"无招"。"""
    t = table(enemy_id)
    if not current_move_id or current_move_id not in t:
        return EnemyMove(move_id=current_move_id or "")
    nxt = t[current_move_id].followup
    return t.get(nxt, EnemyMove(move_id=nxt or ""))


def intent_of(enemy_id: str, move: EnemyMove) -> dict[str, Any]:
    """把一招转成 mod /state 里的 intent 形状（供比对 + 模拟器使用）。"""
    out: dict[str, Any] = {"move_id": move.move_id, "damage": move.damage, "block": move.block}
    if move.buff_power:
        out["buff"] = {"power_id": move.buff_power, "amount": move.buff_amount}
    return out


__all__ = ["EnemyMove", "MOVE_TABLES", "predict_next", "intent_of"]
