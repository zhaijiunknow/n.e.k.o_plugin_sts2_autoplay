"""从反编译怪物源码自动生成 move 表（覆盖全部怪物）。

读取 .decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Monsters/*.cs，
解析 GenerateMoveStateMachine：抽取每只怪的招式循环（FollowUp）+ 伤害/格挡/buff（ascension 档）。
输出到 sim/monster_data.py 的 MOVE_TABLES（供 sim/monster_ai.py 使用）。

局限（诚实）：RandomBranchState 的不确定性、隐式状态机、跨多个文件的怪，会用启发式近似；
状态卡/召唤/多目标等作为非攻击意图（damage 0）标记。准确性仍需差分验证兜底。
"""
from __future__ import annotations

import os
import re
import sys

MONSTERS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                            "NekoClaw", ".decompiled", "sts2-v0.111.0",
                            "MegaCrit.Sts2.Core.Models.Monsters") if False else \
    r"D:/NekoClaw/.decompiled/sts2-v0.111.0/MegaCrit.Sts2.Core.Models.Monsters"
OUT_PATH = os.path.join(os.path.dirname(__file__), "monster_data.py")

_RE_CLASS = re.compile(r"class\s+(\w+)\s*:\s*MonsterModel")
_RE_DAMAGE = re.compile(
    r"private\s+int\s+(\w+)\s*=>\s*AscensionHelper\.GetValueIfAscension\(AscensionLevel\.\w+,\s*(\d+),\s*(\d+)\)")
_RE_MOVE = re.compile(r"(\w+)\s*=\s*new MoveState\(\"([A-Za-z0-9_]+)\",\s*\w+,\s*((?:[^()]|\([^()]*\))+)\)")
_RE_FOLLOW = re.compile(r"(\w+)\.FollowUpState\s*=\s*(?:\(MoveState\))?\s*(\w+);")
_RE_GAINBLOCK = re.compile(r"\.GainBlock\([^,]+,\s*(\w+),")
_RE_APPLYPOWER = re.compile(r"Apply<(\w+)>\([^,]+,\s*(\w+),")


def _snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()
    return re.sub(r"_+", "_", s).strip("_")


def _intent_damage(intents: str, props: dict[str, int]) -> int:
    m = re.search(r"SingleAttackIntent\((\w+)\)", intents)
    if m:
        return props.get(m.group(1), 0)
    m = re.search(r"MultiAttackIntent\((\w+)", intents)
    if m:
        return props.get(m.group(1), 0)
    return 0


def _intent_hits(intents: str) -> int:
    m = re.search(r"MultiAttackIntent\((\w+),?\s*(\d+)?\)", intents)
    return int(m.group(2)) if m and m.group(2) else 1


def parse_monster(src: str) -> dict | None:
    cls = _RE_CLASS.search(src)
    if not cls:
        return None
    enemy_id = _snake(cls.group(1))
    props = {name: int(asc) for name, asc, _x in _RE_DAMAGE.findall(src)}

    # 招式：var name -> (move_id, intents_str)
    moves: dict[str, tuple[str, str]] = {}
    for var, move_id, intents in _RE_MOVE.findall(src):
        moves[var] = (move_id, intents)
    if not moves:
        return None

    # followup：var -> var
    follow: dict[str, str] = {}
    for a, b in _RE_FOLLOW.findall(src):
        if a in moves and b in moves:
            follow[a] = b

    # block / buff 从招式方法体提取（GainBlock / Apply<Power>）
    method_bodies = re.findall(r"private\s+async\s+Task\s+\w+\([^)]*\)\s*\{(.*?)\n\t\}", src, re.S)
    blocks_by_method = {}
    buffs_by_method = {}
    for body in method_bodies:
        for m in _RE_GAINBLOCK.finditer(body):
            blocks_by_method.setdefault(body, []).append(props.get(m.group(1), 0))
        for m in _RE_APPLYPOWER.finditer(body):
            potype = _snake(m.group(1))
            buffs_by_method.setdefault(body, []).append((potype, props.get(m.group(2), 0)))

    out_moves: dict[str, dict] = {}
    for var, (move_id, intents) in moves.items():
        entry = {
            "move_id": move_id,
            "damage": _intent_damage(intents, props),
            "hits": _intent_hits(intents),
            "followup": moves.get(follow.get(var, ""), (None,))[0],
        }
        # block/buff 用 body 级启发式（GainBlock / Apply 首次）
        pb = blocks_by_method.get(intents, [])
        if pb:
            entry["block"] = pb[0]
        if re.search(r"DefendIntent\(\)", intents):
            entry.setdefault("block", 0)
        out_moves[move_id] = entry
    return {"enemy_id": enemy_id, "moves": out_moves}


def main() -> int:
    results = []
    for fn in sorted(os.listdir(MONSTERS_DIR)):
        if not fn.endswith(".cs"):
            continue
        with open(os.path.join(MONSTERS_DIR, fn), encoding="utf-8") as f:
            data = parse_monster(f.read())
        if data:
            results.append(data)
    print(f"parsed {len(results)} monsters")
    # 写出 python 数据模块
    lines = ['"""自动生成：全部怪物的 move 表（由 sim/gen_monsters.py 生成，勿手改）。"""',
             "from .monster_ai import EnemyMove", "MOVE_TABLES = {"]
    for r in results:
        lines.append(f"    {r['enemy_id']!r}: {{")
        for mid, m in r["moves"].items():
            follow = m.get("followup") or ""
            lines.append(f"        {mid!r}: EnemyMove({mid!r}, damage={m.get('damage', 0)}, "
                         f"block={m.get('block', 0)}, hits={m.get('hits', 1)}, "
                         f"followup={follow!r}),")
        lines.append("    },")
    lines.append("}")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
