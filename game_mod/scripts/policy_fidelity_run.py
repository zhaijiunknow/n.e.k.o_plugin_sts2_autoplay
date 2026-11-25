#!/usr/bin/env python3
"""Drive game_mod/PolicyFidelity with a released AlphaDefect network and report frame mismatches.

PolicyFidelity is upstream's headless engine plus a hook that makes the built nekospire.dll encode every
decision as well and compares the two frames. This script plays whole greedy runs through it (so the
compared states are the ones the network actually reaches) and prints the per-decision-kind tally.

Run it with the AlphaDefect venv's Python (it needs numpy + torch and upstream's python/ packages):

    dotnet build game_mod/nekospire/nekospire.csproj -c Release
    dotnet build game_mod/PolicyFidelity/PolicyFidelity.csproj -c Release
    D:/NekoClaw/_refs/AlphaDefect-Public/.venv/Scripts/python.exe -I -X utf8 game_mod/scripts/policy_fidelity_run.py \
        --ckpt D:/NekoClaw/_refs/AlphaDefect-Public/weights/files_v6/ironclad_ema_v24782.pt --runs 3

`-I` matters: it keeps the script's directory and the user site off sys.path.
Exit code 1 when any compared frame differs.

It also checks the player-choice log (mod `GET /choices`, read by the end-of-run review): after every step
of these runs, what the log reports must be matched by the plugin's `choice_review.match_candidate` to the
candidate that was actually taken. A step the log names as a different candidate is an error; a step it does
not report at all (leaving a room, confirming, claiming gold ...) is only counted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

GAME_MOD = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--upstream", default=r"D:\NekoClaw\_refs\AlphaDefect-Public")
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--character", default="IRONCLAD")
    ap.add_argument("--asc", type=int, default=10)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--seeds-file", default=None, help="default: <upstream>/seeds/R1.json")
    ap.add_argument("--obs-format", type=int, default=None, help="default: the checkpoint's own obs_format")
    ap.add_argument("--dotnet", default=r"C:\Program Files\dotnet\dotnet.exe")
    ap.add_argument("--dll", default=str(GAME_MOD / "PolicyFidelity" / "bin" / "Release" / "net9.0" / "PolicyFidelity.dll"))
    ap.add_argument("--max-steps", type=int, default=6000)
    a = ap.parse_args()

    upstream = Path(a.upstream)
    sys.path.insert(0, str(upstream / "python"))
    import numpy as np
    import sts2env.env as envmod
    import torch

    envmod.DOTNET = Path(a.dotnet)      # upstream hard-codes ~/.dotnet/dotnet
    from sts2env.env import Sts2Env
    from sts2env.protocol import parse
    from sts2rl.actor import MODE_RUN
    from sts2rl.catalog import Catalog
    from sts2rl.features import Ragged, collate_ragged
    from sts2rl.model import ModelConfig, PolicyValueNet, load_state, zero_new_vocab

    blob = torch.load(a.ckpt, map_location="cpu", weights_only=False)
    net = PolicyValueNet(ModelConfig(**blob["config"]["model"])).eval()
    load_state(net, blob["state"], blob.get("content_names"))
    old_n, vocab_n = blob.get("vocab_n") or 1873, Catalog().vocab_size
    if old_n < vocab_n:
        with torch.no_grad():
            zero_new_vocab(net, old_n, vocab_n)
    obs_format = a.obs_format or int(blob["obs_format"])
    seeds_file = Path(a.seeds_file) if a.seeds_file else upstream / "seeds" / "R1.json"
    seeds = json.loads(seeds_file.read_text(encoding="utf-8"))["seeds"][: a.runs]

    work = Path(tempfile.mkdtemp(prefix="policy_fidelity_"))
    summary_path = work / "summary.jsonl"
    os.environ["POLICY_FIDELITY_LOG"] = str(summary_path)
    choice_path = work / "choices.jsonl"
    os.environ["POLICY_CHOICE_LOG"] = str(choice_path)
    print(f"network v{blob['version']} obs_format={obs_format}; engine log: {work / 'engine.log'}", flush=True)
    with open(work / "engine.log", "ab") as log:
        for seed in seeds:
            env = Sts2Env(dll=Path(a.dll), labels=False, log=log)
            try:
                env._proc.send({"op": "reset", "mode": "run", "character": a.character, "ascension": a.asc, "seed": seed,
                                "labels": False, "route_summary": True, "obs_format": obs_format})
                o, steps, t0 = parse(env._proc.recv()), 0, time.time()
                while not (o.done or o.stuck or o.num_actions == 0) and steps < a.max_steps:
                    with torch.inference_mode():
                        logits, _ = net(collate_ragged(Ragged.from_obs([o], np.array([MODE_RUN]))).to("cpu"))
                    o = env.step(int(logits[0, : o.num_actions].argmax()))
                    steps += 1
                print(f"  {seed}: win={bool(o.victory) and not o.stuck} floor={int(o.floor)} steps={steps} "
                      f"({time.time() - t0:.0f}s)", flush=True)
            finally:
                env.close()

    total: dict[str, dict] = {}
    for line in summary_path.read_text(encoding="utf-8").splitlines() if summary_path.exists() else []:
        for kind, row in json.loads(line).items():
            agg = total.setdefault(kind, {"frames": 0, "equal": 0, "skipped": 0, "mismatched": 0, "samples": []})
            for key in ("frames", "equal", "skipped", "mismatched"):
                agg[key] += row[key]
            agg["samples"] += row["samples"]
    if not total:
        print("no summary written — see the engine log", file=sys.stderr)
        return 2
    print(f"{'kind':<12}{'frames':>8}{'equal':>8}{'skipped':>9}{'mismatch':>10}")
    bad = 0
    for kind, row in sorted(total.items()):
        bad += row["mismatched"]
        print(f"{kind:<12}{row['frames']:>8}{row['equal']:>8}{row['skipped']:>9}{row['mismatched']:>10}")
    for kind, row in sorted(total.items()):
        for sample in row["samples"][:6]:
            print("  " + sample)
    bad += check_choices(choice_path)
    return 1 if bad else 0


def check_choices(path: Path) -> int:
    """Judge the player-choice log with the plugin's own review code; returns the number of wrongly named steps.

    Every step is hung as a recommendation whose "recommended" candidate is the one the run took, then the
    records the log produced are fed to `ChoiceReview.resolve` exactly as the plugin does. A step answered
    with another candidate is wrong; a step never answered is silent.
    """
    if not path.exists():
        print("no choice log written", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location("choice_review", GAME_MOD.parent / "choice_review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    review = module.ChoiceReview()
    tally: dict[str, dict] = {}
    samples: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        step, records = row["step"], row["records"]
        candidates, taken = step["candidates"], step["index"]
        key = f"{step['kind']}/{candidates[taken]['kind']}"
        tally.setdefault(key, {"steps": 0, "named": 0, "wrong": 0})["steps"] += 1
        decision = "".join("_" + ch if ch.isupper() and i else ch for i, ch in enumerate(step["kind"])).upper()
        review.capture_network(
            {"decision_id": str(number), "kind": decision, "candidates": candidates,
             "probs": [1.0 / len(candidates)] * len(candidates), "choice": taken},
            context={"room": key, "floor": step["floor"]},
        )
        for answered in review.resolve(records):
            agg = tally[answered["room"]]
            if answered["same"]:
                agg["named"] += 1
            else:
                agg["wrong"] += 1
                if len(samples) < 12:
                    samples.append(f"floor {answered['floor']} {answered['room']}: took {answered['recommended']!r}, "
                                   f"log says {answered['chosen']!r}")
    print(f"\n{'choice log: decision/taken':<34}{'steps':>7}{'named':>7}{'silent':>8}{'wrong':>7}")
    for key, agg in sorted(tally.items()):
        print(f"{key:<34}{agg['steps']:>7}{agg['named']:>7}{agg['steps'] - agg['named'] - agg['wrong']:>8}{agg['wrong']:>7}")
    for sample in samples:
        print("  " + sample)
    return sum(agg["wrong"] for agg in tally.values())


if __name__ == "__main__":
    sys.exit(main())
