#!/usr/bin/env python3
"""Build nekospire (Release, non-incremental) and summarise compiler errors.

Used while re-vendoring the embedded CombatSolver engine: the adaptation work is compiler-driven
(see Solver/VENDORING.md), so what matters is the error list grouped by code and by file.

    python game_mod/scripts/build-errors.py            # summary
    python game_mod/scripts/build-errors.py --all      # every distinct error line
    python game_mod/scripts/build-errors.py CS0122     # only that code
"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1] / "nekospire"
LINE = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>CS\d+): (?P<msg>.*?)(?: \[[^\]]+\])?$")


def main() -> int:
    args = sys.argv[1:]
    show_all = "--all" in args
    codes = {a for a in args if a.startswith("CS")}
    limit = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--limit=")), 60)

    sys.stdout.reconfigure(encoding="utf-8")
    proc = subprocess.run(
        ["dotnet", "build", "nekospire.csproj", "-c", "Release", "-nologo", "-v", "q", "--no-incremental",
         "-p:GenerateFullPaths=true"],
        cwd=PROJECT_DIR, capture_output=True,
        env={**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en"},
    )
    text = proc.stdout.decode("utf-8", errors="replace")

    errors: dict[tuple[str, int, int, str], str] = {}
    for raw in text.splitlines():
        match = LINE.match(raw.strip())
        if not match:
            continue
        path = match["file"]
        marker = "nekospire\\"
        index = path.rfind(marker)
        rel = path[index + len(marker):] if index >= 0 else path
        errors[(rel, int(match["line"]), int(match["col"]), match["code"])] = match["msg"]

    print(f"errors: {len(errors)}  (exit {proc.returncode})")
    if not errors:
        if proc.returncode != 0:
            print(text[-3000:])
        return proc.returncode

    by_code = collections.Counter(key[3] for key in errors)
    by_file = collections.Counter(key[0] for key in errors)
    print("by code:", ", ".join(f"{code}={count}" for code, count in by_code.most_common()))
    print("by file:")
    for name, count in by_file.most_common(40):
        print(f"  {count:5d}  {name}")

    selected = [(key, msg) for key, msg in sorted(errors.items()) if not codes or key[3] in codes]
    if show_all or codes:
        for (rel, line, col, code), msg in selected[:limit]:
            print(f"{rel}({line},{col}) {code}: {msg}")
        if len(selected) > limit:
            print(f"... {len(selected) - limit} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
