"""从反编译药水源码生成药水数据表（覆盖全部药水）。

读取 .decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Potions/*.cs，
抽取每个药水：id(UPPER_SNAKE)、usage(CombatOnly/AnyTime)、target_type、
canonical 数值、效果种类（由 apply 方法的主命令推断：attack/block/heal/buff/draw/summon）。
输出到 sim/potion_data.py 的 POTION_TABLE（供 sim 使用 + 对照 harness）。
"""
from __future__ import annotations

import os
import re

POTIONS_DIR = r"D:/NekoClaw/.decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Potions"
OUT = os.path.join(os.path.dirname(__file__), "potion_data.py")

_COMMAND_KIND = [
    re.compile(r"\.Attack\(|DealDamage|DamageCmd"),
    re.compile(r"\.GainBlock\("),
    re.compile(r"\.Heal\("),
    re.compile(r"Apply<(\w+)>"),
    re.compile(r"DrawCards\(|\.Draw\("),
    re.compile(r"\.Summon\("),
    re.compile(r"GainEnergy\(|EnergyVar"),
]

# 反编译源码里这些药水的效果无法被上面的命令/Var 推断（多为交互式或资源类），手动给出 kind 与 value。
# kind 语义见 sim/effects.apply_potion；utility = 对战斗评分无直接影响（进不了 search）。
# value 依据 data/eng/potions.json 的描述数字（如 "Gain 5 Max HP" -> value=5）。
_MANUAL_KINDS: dict[str, tuple[str, int]] = {
    "ASHWATER": ("hand_exhaust", 1),
    "ATTACK_POTION": ("draw", 1),
    "BLESSING_OF_THE_FORGE": ("upgrade_hand", 2),
    "COLORLESS_POTION": ("draw", 1),
    "COSMIC_CONCOCTION": ("draw", 3),
    "CUNNING_POTION": ("draw", 3),
    "DEPRECATED_POTION": ("utility", 0),
    "DISTILLED_CHAOS": ("draw", 3),
    "DROPLET_OF_PRECOGNITION": ("draw", 1),
    "ENTROPIC_BREW": ("utility", 0),
    "ESSENCE_OF_DARKNESS": ("orb:DARK", 0),
    "FRUIT_JUICE": ("max_hp", 5),
    "GAMBLERS_BREW": ("draw", 3),
    "KINGS_COURAGE": ("upgrade_hand", 2),
    "LIQUID_MEMORIES": ("draw", 1),
    "OROBIC_ACID": ("draw", 3),
    "POTION_OF_CAPACITY": ("orb_slots", 2),
    "POT_OF_GHOULS": ("draw", 2),
    "POWER_POTION": ("draw", 1),
    "SKILL_POTION": ("draw", 1),
    "SOLDIERS_STEW": ("upgrade_hand", 1),
    "STAR_POTION": ("utility", 0),
    "TOUCH_OF_INSANITY": ("utility", 0),
}

def snake(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name)).upper().strip("_")

def parse(src: str) -> dict | None:
    cls = re.search(r"class\s+(\w+)\s*:\s*PotionModel", src)
    if not cls:
        return None
    pid = snake(cls.group(1))
    usage = "CombatOnly"
    m = re.search(r"Usage\s*=>\s*PotionUsage\.(\w+)", src)
    if m:
        usage = m.group(1)
    target = "Self"
    m = re.search(r"TargetType\s*=>\s*TargetType\.(\w+)", src)
    if m:
        target = m.group(1)
    # canonical 数值：任意 Var 构造，取第一个数值（DamageVar(10m)/EnergyVar(2)/DynamicVar("X",20m)…）
    value = 0
    var_type = None
    for m in re.finditer(r"new\s+(\w+Var)\(([^)]*)\)", src):
        var_type = m.group(1)
        nums = re.findall(r"(\d+(?:\.\d+)?)m?", m.group(2))
        if nums:
            value = int(float(nums[0]))
            break
    # 效果种类：先用 Var 类型推断，再用命令确认
    kind = "unknown"
    if var_type == "DamageVar":
        kind = "attack"
    elif var_type == "BlockVar":
        kind = "block"
    elif var_type == "EnergyVar":
        kind = "energy"
    elif var_type == "HealVar":
        kind = "heal"
    else:
        for i, rx in enumerate(_COMMAND_KIND):
            mm = rx.search(src)
            if mm:
                kind = ["attack", "block", "heal", "buff", "draw", "summon", "energy"][i]
                if kind == "buff":
                    kind = f"buff:{snake(mm.group(1))}"
                break
    return {"id": pid, "usage": usage, "target": target, "value": value, "kind": kind}

def main() -> int:
    rows = []
    for fn in sorted(os.listdir(POTIONS_DIR)):
        if not fn.endswith(".cs"):
            continue
        with open(os.path.join(POTIONS_DIR, fn), encoding="utf-8") as f:
            d = parse(f.read())
        if d:
            ov = _MANUAL_KINDS.get(d["id"])
            if ov is not None:
                d["kind"], d["value"] = ov
            rows.append(d)
    print(f"parsed {len(rows)} potions")
    lines = ['"""自动生成：全部药水的效果表（由 sim/gen_potions.py 生成，勿手改）。"""',
             "POTION_TABLE = {"]
    for r in rows:
        lines.append(f"    {r['id']!r}: {{'usage': {r['usage']!r}, 'target': {r['target']!r}, "
                     f"'value': {r['value']}, 'kind': {r['kind']!r}}},")
    lines.append("}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {OUT}")
    return 0

if __name__ == "__main__":
    main()
