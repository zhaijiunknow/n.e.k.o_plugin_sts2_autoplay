#!/usr/bin/env python3
"""Watch a live autoplay session and file a guild-board task when it stalls.

Polls the mod (GET /state) and the plugin log. "Stalled" = the game state signature (screen, floor, hp,
turn, hand, gold) has not changed for --stall-seconds while the mod is reachable. On a stall it saves the
evidence (state, /actions/available, /policy/decision, recent [sts2_policy] / [sts2_stuck] log lines) under
.dsh-artifacts/policy-live/ and appends one pending task to the guild board (tasks.json of --team), in the
format dsh-tool-guild reads. One task per distinct stall signature.

    python game_mod/scripts/policy_live_monitor.py --team sts2-policy
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / ".dsh-artifacts" / "policy-live"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(base: str, path: str) -> dict | None:
    try:
        with OPENER.open(base + path, timeout=8) as response:
            return json.loads(response.read().decode("utf-8")).get("data")
    except Exception:  # noqa: BLE001 - unreachable mod is a state, not an error
        return None


def signature(state: dict) -> str:
    run, combat = state.get("run") or {}, state.get("combat") or {}
    hand = [c.get("card_id") for c in combat.get("hand") or [] if isinstance(c, dict)]
    enemies = [e.get("current_hp") for e in combat.get("enemies") or [] if isinstance(e, dict)]
    return json.dumps([state.get("screen"), run.get("floor"), run.get("current_hp"), run.get("gold"),
                       state.get("turn") or combat.get("turn"), hand, enemies, len(run.get("deck") or [])])


def log_tail(pattern: str, keep: int = 60) -> list[str]:
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    if not files:
        return []
    with open(files[-1], "rb") as handle:
        handle.seek(max(0, os.path.getsize(files[-1]) - 400_000))
        lines = handle.read().decode("utf-8", errors="replace").splitlines()
    return [line[:400] for line in lines if "[sts2_policy]" in line or "[sts2_stuck]" in line or "Traceback" in line][-keep:]


def publish(team_dir: Path, subject: str, description: str, files: list[str]) -> str:
    team_dir.mkdir(parents=True, exist_ok=True)
    board = team_dir / "tasks.json"
    db = json.loads(board.read_text(encoding="utf-8")) if board.exists() else {"tasks": []}
    task = {
        "id": "T-" + str(len(db["tasks"]) + 1).zfill(3), "subject": subject, "description": description,
        "status": "pending", "owner": None, "blockedBy": [], "files": files, "claimAttempts": 0, "gateRejections": 0,
        "acceptance": [
            "python game_mod/scripts/build-errors.py",
            'cd tests && D:/NekoClaw/N.E.K.O/.venv/Scripts/python.exe -m pytest -p neko_repo_shim -p no:cacheprovider '
            '--rootdir="D:/NekoClaw/n.e.k.o_plugin_sts2_autoplay/tests" -q -W ignore test_policy_client.py',
        ],
    }
    db["tasks"].append(task)
    board.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return task["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:18080")
    ap.add_argument("--team", default="sts2-policy")
    ap.add_argument("--interval", type=float, default=10)
    ap.add_argument("--stall-seconds", type=float, default=150)
    ap.add_argument("--log-glob", default=os.path.expandvars(r"%LOCALAPPDATA%\N.E.K.O\logs\plugin\N.E.K.O_Plugin_sts2_autoplay_*.log"))
    a = ap.parse_args()
    team_dir = Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "teams" / a.team
    OUT.mkdir(parents=True, exist_ok=True)
    last_sig, since, reported = "", time.time(), set()

    def note(message: str) -> None:
        line = f"{dt.datetime.now():%H:%M:%S} {message}"
        print(line, flush=True)
        with open(OUT / "monitor.log", "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    note(f"monitor started; board {team_dir / 'tasks.json'}")
    while True:
        state = get(a.base, "/state")
        if state is None:
            note("mod unreachable (game closed or /state failing)")
            last_sig, since = "", time.time()
            time.sleep(a.interval * 3)
            continue
        sig, run = signature(state), state.get("run") or {}
        if sig != last_sig:
            if json.loads(sig)[:2] != (json.loads(last_sig)[:2] if last_sig else None):
                note(f"screen={state.get('screen')} floor={run.get('floor')} char={run.get('character_id')} hp={run.get('current_hp')}/{run.get('max_hp')}")
            last_sig, since = sig, time.time()
        elif time.time() - since >= a.stall_seconds and sig not in reported:
            reported.add(sig)
            stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
            evidence = OUT / f"stall-{stamp}"
            evidence.mkdir(parents=True, exist_ok=True)
            decision = get(a.base, "/policy/decision?obs_format=28") or {}
            decision.pop("frame", None)
            actions = get(a.base, "/actions/available") or {}
            lines = log_tail(a.log_glob)
            (evidence / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
            (evidence / "policy_decision.json").write_text(json.dumps(decision, ensure_ascii=False, indent=1), encoding="utf-8")
            (evidence / "actions.json").write_text(json.dumps(actions, ensure_ascii=False, indent=1), encoding="utf-8")
            (evidence / "plugin_log_tail.txt").write_text("\n".join(lines), encoding="utf-8")
            names = [x.get("name") for x in actions.get("actions") or []]
            subject = f"autoplay stalled on {state.get('screen')} (floor {run.get('floor')}, {run.get('character_id')})"
            description = (
                f"Live autoplay made no progress for {int(a.stall_seconds)}s.\n"
                f"screen={state.get('screen')} unknown_screen_type={state.get('unknown_screen_type')} floor={run.get('floor')} "
                f"character={run.get('character_id')} hp={run.get('current_hp')}/{run.get('max_hp')}\n"
                f"available actions: {names}\n"
                f"/policy/decision: available={decision.get('available')} kind={decision.get('decision_kind')} wait={decision.get('wait')} "
                f"reason={decision.get('reason')} forced_index={decision.get('forced_index')} "
                f"candidates={[(c.get('label'), c.get('executable'), c.get('action')) for c in (decision.get('candidates') or [])][:12]}\n"
                f"last plugin log lines:\n" + "\n".join(lines[-12:]) + "\n"
                f"Evidence: {evidence}\n"
                "Context: AlphaDefect policy seam (AGENTS.md, section 'AlphaDefect policy seam'). Mod side: "
                "game_mod/nekospire/Policy/PolicyService.cs; plugin side: policy_client.py + loop_runner.tick. "
                "Find why no action is produced or why the produced action does not change the state, fix it, "
                "and say in the report what was verified and what was not."
            )
            task_id = publish(team_dir, subject, description, ["game_mod/nekospire/Policy/PolicyService.cs", "policy_client.py", "loop_runner.py"])
            note(f"STALL -> published {task_id}: {subject} (evidence {evidence.name})")
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
