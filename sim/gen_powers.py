"""从反编译 Power 源码生成 Power 行为签名表（覆盖全部 Power）。

读取 .decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Powers/*.cs，
抽取每个 Power：id(UPPER_SNAKE)、type(Buff/Debuff)、stack_type、行为 hook(override 方法名)。
输出到 sim/power_data.py 的 POWER_TABLE（供对照 harness 判断我们建模范围）。
"""
from __future__ import annotations

import os
import re

POWERS_DIR = r"D:/NekoClaw/.decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Powers"
OUT = os.path.join(os.path.dirname(__file__), "power_data.py")

def snake(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"(?<!^)(?=[A-Z])", "_", name)).upper().strip("_")

def parse(src: str) -> dict | None:
    cls = re.search(r"class\s+(\w+)\s*:\s*PowerModel", src)
    if not cls:
        return None
    pid = snake(cls.group(1))
    ptype = "Buff"
    m = re.search(r"PowerType\s*=>\s*PowerType\.(\w+)", src)
    if m:
        ptype = m.group(1)
    stack = "Counter"
    m = re.search(r"PowerStackType\s*=>\s*PowerStackType\.(\w+)", src)
    if m:
        stack = m.group(1)
    hooks = []
    for m in re.finditer(r"override\s+(?:async\s+)?(?:decimal|int|bool|void|Task)\s+(\w+)\(", src):
        hooks.append(m.group(1))
    return {"id": pid, "type": ptype, "stack": stack, "hooks": hooks}

def main() -> int:
    rows = []
    for fn in sorted(os.listdir(POWERS_DIR)):
        if not fn.endswith(".cs"):
            continue
        with open(os.path.join(POWERS_DIR, fn), encoding="utf-8") as f:
            d = parse(f.read())
        if d:
            rows.append(d)
    print(f"parsed {len(rows)} powers")
    lines = ['"""自动生成：全部 Power 的行为签名表（由 sim/gen_powers.py 生成，勿手改）。"""',
             "POWER_TABLE = {"]
    for r in rows:
        lines.append(f"    {r['id']!r}: {{'type': {r['type']!r}, 'stack': {r['stack']!r}, "
                     f"'hooks': {r['hooks']!r}}},")
    lines.append("}")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {OUT}")
    return 0

if __name__ == "__main__":
    main()
