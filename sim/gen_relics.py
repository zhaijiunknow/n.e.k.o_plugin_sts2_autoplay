"""从反编译遗物源码生成遗物数据表（覆盖战斗相关遗物）。

读取 .decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Relics/*.cs，
抽取每个遗物：id(UPPER_SNAKE)、canonical 数值(Var)、效果类型(var_kind)、行为 hook(override 方法名)。
输出到 sim/relic_data.py 的 RELIC_TABLE（供 sim 建模 + 对照 harness）。
"""
from __future__ import annotations

import os
import re

RELICS_DIR = r"D:/NekoClaw/.decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Relics"
OUT = os.path.join(os.path.dirname(__file__), "relic_data.py")

def snake(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name)).upper().strip("_")

def parse(src: str) -> dict | None:
    cls = re.search(r"class\s+(\w+)\s*:\s*RelicModel", src)
    if not cls:
        return None
    rid = snake(cls.group(1))
    # canonical 数值 + var 类型
    value, var_kind = 0, ""
    for m in re.finditer(r"new\s+(\w+Var)\(([^)]*)\)", src):
        var_kind = m.group(1)
        nums = re.findall(r"(\d+(?:\.\d+)?)m?", m.group(2))
        if nums:
            value = int(float(nums[0]))
        break
    # 行为 hook：override 方法名（ModifyMaxEnergy/AfterCombatVictory/AfterSideTurnStart...）
    hooks = []
    for m in re.finditer(r"override\s+(?:async\s+)?(?:Task|decimal|int|bool|void|Creature)\s+(\w+)\(", src):
        hooks.append(m.group(1))
    # 有的效果在方法体用 GainBlock/Heal/Apply<X>（turn-start 类）
    return {"id": rid, "value": value, "var_type": var_kind, "hooks": hooks}

def main() -> int:
    rows = []
    for fn in sorted(os.listdir(RELICS_DIR)):
        if not fn.endswith(".cs"):
            continue
        with open(os.path.join(RELICS_DIR, fn), encoding="utf-8") as f:
            d = parse(f.read())
        if d and d["hooks"]:
            rows.append(d)
    print(f"parsed {len(rows)} relics with hooks")
    lines = ['"""自动生成：战斗相关遗物的效果表（由 sim/gen_relics.py 生成，勿手改）。"""',
             "RELIC_TABLE = {"]
    for r in rows:
        lines.append(f"    {r['id']!r}: {{'value': {r['value']}, 'var_type': {r['var_type']!r}, "
                     f"'hooks': {r['hooks']!r}}},")
    lines.append("}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {OUT}")
    return 0

if __name__ == "__main__":
    main()
