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
    re.compile(r"\.Attack\("), re.compile(r"DealDamage|DamageCmd"),
    re.compile(r"\.GainBlock\("),
    re.compile(r"\.Heal\("),
    re.compile(r"Apply<(\w+)>"),
    re.compile(r"DrawCards\(|\.Draw\("),
    re.compile(r"\.Summon\("),
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
    # canonical 数值：DynamicVar("X", Val) / BlockVar(Val, ...) / IntVar("X", Val)
    value = 0
    m = re.search(r"(?:DynamicVar|BlockVar|IntVar)\("  # heuristic
        r"(?:\"(\w+)\",\s*)?([\d.]+)m?", src)
    if m:
        value = int(float(m.group(2)))
    # 效果种类：整文件里找效果命令（药水文件小，首个命令即效果；跳过属性/静态定义区）
    body = src
    kind = "unknown"
    for i, rx in enumerate(_COMMAND_KIND):
        mm = rx.search(body)
        if mm:
            kind = ["attack", "attack", "block", "heal", "buff", "draw", "summon"][i]
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
