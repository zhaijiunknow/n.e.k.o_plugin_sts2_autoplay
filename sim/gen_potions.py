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
