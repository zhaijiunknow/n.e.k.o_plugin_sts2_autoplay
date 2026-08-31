"""把 CombatSolver 的精确效果数据 与 我们已有数据 对照，产出缺口报告。

数据源：
- CombatSolver/coverage/classifications.json：CombatSolver 知道哪些卡/Power/怪招/遗物有战斗效果
  （status 表示它是否精确处理：SolverCompensation/EngineExact/EngineInferred = 处理了）。
- CombatSolver/src/Prediction/MonsterMoveEffects.StaticValues.cs：CombatSolver 每只怪它读的静态效果字段
  （如 Nibbit -> SliceBlock/HissStrengthGain），这对应我们的格挡/力量buff。
- 我们的：sim/monster_data.py（108只怪，伤害/格挡/buff/followup）+ cards.json/powers.json。

输出：按类别打印 CombatSolver 覆盖量 vs 我们数据量；列出"CombatSolver 有静态效果字段
但我们模型里 block=0/buff=0"的怪（提示我们的生成表可能缺格挡/buff）。
"""
from __future__ import annotations

import json
import os
import re

CS_COVERAGE = r"D:/NekoClaw/CombatSolver/coverage/classifications.json"
CS_STATIC = r"D:/NekoClaw/CombatSolver/src/Prediction/MonsterMoveEffects.StaticValues.cs"
DECOMPILED = r"D:/NekoClaw/.decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Monsters"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POWERS = os.path.join(REPO, "game_mod", "mcp_server", "data", "eng", "powers.json")
_CARDS = os.path.join(REPO, "game_mod", "mcp_server", "data", "eng", "cards.json")


def snake(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name)).upper().strip("_")


def main() -> None:
    # 1) CombatSolver classifications 覆盖
    with open(CS_COVERAGE, encoding="utf-8") as f:
        cls = json.load(f)
    cat: dict[str, int] = {}
    handled: dict[str, int] = {}
    for k, v in cls.items():
        kind = k.split("|")[0]
        cat[kind] = cat.get(kind, 0) + 1
        if v.get("status") in ("SolverCompensation", "EngineExact", "EngineInferred"):
            handled[kind] = handled.get(kind, 0) + 1
    print("=== CombatSolver coverage (classifications) ===")
    for kind in sorted(cat):
        print(f"  {kind:12}: {cat[kind]:4}  handled(compensate/exact/inferred): {handled.get(kind,0):4}")

    # 2) CombatSolver 每只怪的静态效果字段
    static_fields = {}
    with open(CS_STATIC, encoding="utf-8") as f:
        src = f.read()
    for m in re.finditer(r'\["(\w+)"\]\s*=\s*\[(.*?)\]', src):
        monster = m.group(1)
        fields = [x.strip().strip('"') for x in m.group(2).split(",") if x.strip()]
        static_fields[monster] = fields
    print(f"\n=== CombatSolver 读静态效果字段的怪: {len(static_fields)} 只 ===")

    # 3) CombatSolver 静态字段的实际值：从反编译 GetValueIfAscension 读
    #    字段名(camelCase) -> 值(ascension 档)
    field_values: dict[str, int] = {}
    for fn in os.listdir(DECOMPILED):
        if not fn.endswith(".cs"):
            continue
        with open(os.path.join(DECOMPILED, fn), encoding="utf-8") as f:
            s = f.read()
        for m in re.finditer(
            r"private\s+int\s+(\w+)\s*=>\s*AscensionHelper\.GetValueIfAscension\(AscensionLevel\.\w+,\s*(\d+),\s*(\d+)\)", s):
            field_values[m.group(1)] = int(m.group(2))  # ascension 档值

    # 4) 我们数据：用合并表（手写验证覆盖 > 自动生成），看每只怪是否已带 block/buff/status
    from sim import monster_ai
    from sim import monster_data
    merged_ids = set(monster_data.MOVE_TABLES.keys()) | set(monster_ai.MOVE_TABLES.keys())

    def merged(id_):
        t = monster_ai.table(id_)
        return t if t else monster_data.MOVE_TABLES.get(id_, {})

    # 5) 对照：CombatSolver 知道静态字段，但我们该怪仍无 block/buff → 真正缺口
    print("\n=== 缺口：CombatSolver 有静态效果，但我们合并表仍无对应 block/buff 的怪(附应填值) ===")
    gaps = []
    fix_list = {}
    for monster, fields in sorted(static_fields.items()):
        found_oks = [(fld, field_values[fld]) for fld in fields
                     if fld in field_values and field_values[fld] != 0]
        if not found_oks:
            continue
        ours = merged(snake(monster))   # StaticValues 怪名 camelCase → UPPER_SNAKE
        has_extra = any(m.block > 0 or m.buff_power or m.status_card for m in ours.values())
        if not has_extra:
            gaps.append(monster)
            fix_list[monster] = found_oks
            print(f"  {monster:12}: 应填 {found_oks[:3]}")
    print(f"\n共 {len(gaps)} 只真正缺口（手写表未覆盖）：{gaps}")
    print(f"\n共 {len(gaps)} 只因'模型无格挡/buff 但 CombatSolver 有'可能缺：{gaps[:20]}")

    # 6) 我们数据规模 vs CombatSolver 覆盖（各类别")
    try:
        from sim import effects as fx
        from sim.potion_data import POTION_TABLE
        from sim.relic_data import RELIC_TABLE
        from sim import monster_data as md
    except Exception as e:  # 非 sim 包上下文时跳过
        fx = None
        POTION_TABLE = {}
        md = None
    with open(_CARDS, encoding="utf-8") as f: cards_n = len(json.load(f))
    with open(_POWERS, encoding="utf-8") as f: powers_n = len(json.load(f))
    from sim.power_data import POWER_TABLE
    # 我们明确建模的 power_id 行为（effects.py 里处理）
    _MODELED = ["STRENGTH_POWER", "WEAK_POWER", "VULNERABLE_POWER", "DEXTERITY_POWER",
                "POISON_POWER", "INTANGIBLE_POWER", "BUFFER_POWER", "RITUAL_POWER",
                "REGEN_POWER", "PLATED_ARMOR_POWER"]
    our_powers = sum(1 for p in _MODELED if p in POWER_TABLE)
    print(f"\n=== 我们各类别覆盖 vs CombatSolver ===")
    print(f"  卡片    : 我们字段级 {cards_n} 数据 | CombatSolver 处理 {handled.get('Card',0)} 个hook")
    print(f"  药水    : 我们数据 {len(POTION_TABLE)} | CombatSolver 处理 {handled.get('Potion',0)} (use_potion 动作已接; Complex 类未知)")
    print(f"  Power   : 我们行为 {our_powers}/{len(POWER_TABLE)} 数据 | CombatSolver 处理 {handled.get('Power',0)}")
    print(f"  遗物    : 我们数据 {len(RELIC_TABLE)} (hook引擎: combat-start/turn-start/胜利治疗 + 能量/抽牌/增伤 mod) | CombatSolver 处理 {handled.get('Relic',0)}")
    print(f"  球      : 我们 {handled.get('Orb',0)} (基础4类) | CombatSolver {handled.get('Orb',0)}")
    print(f"  怪招    : 我们 {len(md.MOVE_TABLES) if md else 0} | CombatSolver 处理 {handled.get('MonsterMove',0)}")
    print(f"\n=== 药水缺口(CombatSolver 处理、我们 kind 未知/未接) ===")
    unknown = [k for k, v in POTION_TABLE.items() if v["kind"] == "unknown"]
    print(f"  共 {len(unknown)} 个药水效果 kind=unknown(需补): {unknown[:15]}")


if __name__ == "__main__":
    main()
