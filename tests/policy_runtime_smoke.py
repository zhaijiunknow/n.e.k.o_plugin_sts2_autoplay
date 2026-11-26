#!/usr/bin/env python3
"""Offline smoke test with the DISTRIBUTED Python, catalogs and checkpoints, not upstream's checkout.

Run with <runtime>/python/python.exe -I -X utf8 tests/policy_runtime_smoke.py --root <runtime>.
Use --expect-missing-legacy on an old installation to verify fail-closed behavior without altering it.
For an isolated repaired data directory, --manifest may reference original checkpoints by absolute path.
No HTTP requests, game access, downloads or writes to the runtime are made.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parents[1] / "policy_manifest.json")
    ap.add_argument("--expect-missing-legacy", action="store_true")
    ap.add_argument("--frames", type=Path, help="Optional directory containing <CHARACTER>/frame-*.bin")
    args = ap.parse_args()
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "policy_sidecar"))
    from alphadefect_server import ContentAlignmentError, Policies, validated_content_names

    manifest = json.loads(args.manifest.read_text(encoding="utf8"))
    policies = Policies(args.root, manifest)
    from sts2rl.content import remap_columns

    checked, rejected, frames = [], [], 0
    for character, spec in manifest["models"].items():
        path = args.root / "models" / spec["file"]
        if not path.is_file():
            continue
        legacy = spec["content_catalog"] == "v0.111.0"
        if args.expect_missing_legacy and legacy:
            try:
                policies.obs_format(character)
            except ContentAlignmentError as exc:
                assert "缺少或无法读取内容目录 v0.111.0" in str(exc), str(exc)
                assert character not in policies._nets
                rejected.append(character)
                print(f"PASS {character}: missing legacy catalog rejected, not cached")
                continue
            raise AssertionError(f"{character}: missing catalog was silently accepted")
        net, fmt, _ = policies._load(character)
        blob = policies._torch.load(str(path), map_location="cpu", weights_only=False)
        names = validated_content_names(net, blob, spec, policies._content_names)
        expected, hits = remap_columns(blob["state"]["content_proj.weight"], names, net.content_names)
        assert policies._torch.equal(net.state_dict()["content_proj.weight"], expected), character
        checked.append(character)
        print(f"PASS {character}: format={fmt}, content columns {len(names)} -> {len(net.content_names)}, matched={hits}")
        if args.frames:
            for frame in sorted((args.frames / character).glob("frame-*.bin")):
                data = frame.read_bytes()
                result = policies.infer(character, data)
                assert result["ok"] and 0 <= result["choice"] < result["num_candidates"]
                assert all(policies._np.isfinite(result["logits"]))
                assert abs(sum(result["probs"]) - 1) < 1e-8
                obs = policies._parse(data)
                print(f"  {frame.name}: {obs.labels[result['choice']]}, p={result['probs'][result['choice']]:.6f}")
                frames += 1
    assert checked or rejected, "no checkpoints tested"
    if args.expect_missing_legacy:
        assert rejected, "no legacy checkpoint tested"
    if args.frames:
        assert frames, "no frames tested"
    print(json.dumps({"loaded": checked, "rejected": rejected, "frames": frames}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
