#!/usr/bin/env python3
"""Inference sidecar for the AlphaDefect policy (https://github.com/hmty-CHEN/AlphaDefect-Public).

The plugin runs inside the N.E.K.O host, whose Python has no PyTorch. This process owns the networks
instead. It is started by the plugin (policy_client.PolicyAdvisor) with the Python of the downloaded
runtime (policy_assets.PolicyAssets) and needs nothing else on the machine:

    <root>/python/python.exe -I -X utf8 alphadefect_server.py --root <root> --manifest <policy_manifest.json>

  <root>/site      numpy + torch (on the embedded Python's path)
  <root>/engine    vocabulary and content catalog
  <root>/models    one checkpoint per character; loaded on first use, so a model downloaded while
                   this process runs is picked up without a restart
  ./vendor         the inference subset of upstream's Python (frame parser, batch builder, network)

    GET  /health  -> {"ok": true, "characters": {"IRONCLAD": {"obs_format": 28, ...}, ...}}
    POST /infer   {"character": "IRONCLAD", "frame": "<base64 frame from the mod's GET /policy/decision>"}
                  -> {"ok": true, "choice", "logits", "probs", "value", "num_candidates", "ms"}

Frames are scored exactly as upstream's greedy evaluation does (whole-run mode, float32, CPU).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODE_RUN = 1      # upstream sts2rl.actor.MODE_RUN: whole-run mode (the only mode the live game is in)

# Bump when a client must reject an already-running sidecar from an older plugin.
CONTENT_ALIGNMENT_VERSION = 1


class ContentAlignmentError(RuntimeError):
    """Do not silently pad/truncate content columns with unknown semantics."""


def validated_content_names(net, blob: dict, spec: dict, resolve_names) -> tuple[str, ...] | None:
    """Resolve exact training feature order before upstream load_state can use its unsafe fallback.

    Released legacy checkpoints omit content_names. Their training catalog is pinned explicitly in
    our manifest, not guessed by column count (equal widths do not imply equal meanings).
    This wrapper is local; the vendored model/feature computations stay unchanged.
    """
    weight = blob['state'].get('content_proj.weight')
    current = getattr(net, 'content_names', None)
    if weight is None and current is None:
        return None
    if weight is None or current is None:
        raise ContentAlignmentError('模型内容特征与网络结构不一致，拒绝加载')
    names = blob.get('content_names')
    if names is None:
        version = spec.get('content_catalog')
        if not version:
            raise ContentAlignmentError('模型缺少内容特征名称及训练目录版本，拒绝按位置补零')
        cfg = net.cfg
        names = resolve_names(cfg.vocab_size, version, cfg.content_enchant, cfg.content_upgrade)
    if not isinstance(names, (tuple, list)) or not names or any(not isinstance(n, str) or not n for n in names):
        raise ContentAlignmentError('模型内容特征名称无效，拒绝加载')
    names = tuple(names)
    if len(names) != weight.shape[1] or len(set(names)) != len(names):
        raise ContentAlignmentError(f'模型内容特征名称与权重列数不匹配：{len(names)} / {weight.shape[1]}，拒绝加载')
    if not set(names).intersection(current):
        raise ContentAlignmentError('模型与当前目录没有同名内容特征，拒绝加载')
    return names


class Policies:
    def __init__(self, root: Path, manifest: dict) -> None:
        self.root, self.manifest = root, manifest
        self._verify_catalog(str(manifest["upstream"]["game_version"]))
        os.environ["STS2_POLICY_ROOT"] = str(root)
        os.environ["STS2_POLICY_GAME_VERSION"] = str(manifest["upstream"]["game_version"])
        sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
        import numpy as np
        import torch
        from sts2env.protocol import parse
        from sts2rl.catalog import Catalog
        from sts2rl.content import content_names
        from sts2rl.features import Ragged, collate_ragged
        from sts2rl.model import ModelConfig, PolicyValueNet, load_state, zero_new_vocab

        self._np, self._torch, self._parse = np, torch, parse
        self._ragged, self._collate = Ragged, collate_ragged
        self._build = (ModelConfig, PolicyValueNet, load_state, zero_new_vocab)
        self._vocab_n = Catalog().vocab_size
        self._content_names = content_names
        self._nets: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def _verify_catalog(self, version: str) -> None:
        dest = f"engine/catalog/{version}/catalog.json"
        spec = next((s for s in self.manifest["runtime"] if s.get("kind") == "catalog" and s.get("dest") == dest), None)
        hint = "请打开策略网络下载页补齐/修复运行环境后重试；不会按位置补零加载模型"
        if spec is None:
            raise ContentAlignmentError(f"清单没有固定内容目录 {version}；{hint}")
        try:
            data = (self.root / dest).read_bytes()
        except OSError as exc:
            raise ContentAlignmentError(f"缺少或无法读取内容目录 {version}；{hint}") from exc
        if len(data) != int(spec["size"]) or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ContentAlignmentError(f"内容目录 {version} 校验失败；{hint}")

    def available(self) -> dict[str, dict]:
        out = {}
        for character, spec in self.manifest["models"].items():
            path = self.root / "models" / spec["file"]
            if path.is_file() and path.stat().st_size == int(spec["size"]):
                loaded = self._nets.get(character)
                out[character] = {"file": spec["file"], "loaded": loaded is not None, "obs_format": loaded[1] if loaded else None}
        return out

    def obs_format(self, character: str) -> int:
        return self._load(character)[1]

    def _load(self, character: str) -> tuple:
        with self._lock:
            if character in self._nets:
                return self._nets[character]
            spec = self.manifest["models"].get(character)
            if spec is None:
                raise KeyError(f"no model is published for {character}")
            path = self.root / "models" / spec["file"]
            if not path.is_file():
                raise FileNotFoundError(f"the {character} model is not downloaded")
            torch = self._torch
            model_config, net_class, load_state, zero_new_vocab = self._build
            blob = torch.load(str(path), map_location="cpu", weights_only=False)
            if blob.get("content_names") is None and "content_proj.weight" in blob["state"]:
                version = spec.get("content_catalog")
                if not version:
                    raise ContentAlignmentError(f"{character} 模型缺少训练目录版本，拒绝加载")
                self._verify_catalog(version)
            net = net_class(model_config(**blob["config"]["model"])).eval()
            names = validated_content_names(net, blob, spec, self._content_names)
            load_state(net, blob["state"], names)
            # Same handling as upstream's evaluation: vocabulary ids added after the checkpoint get zeroed rows.
            old_n = blob.get("vocab_n") or 1873
            if old_n < self._vocab_n:
                with torch.inference_mode(False), torch.no_grad():
                    zero_new_vocab(net, old_n, self._vocab_n)
            self._nets[character] = (net, int(blob["obs_format"]), int(blob.get("version") or 0))
            return self._nets[character]

    def infer(self, character: str, frame: bytes) -> dict:
        np, torch = self._np, self._torch
        t0 = time.perf_counter()
        net, obs_format, version = self._load(character)
        obs = self._parse(frame)
        n = int(obs.num_actions)
        if n <= 0:
            raise ValueError("frame has no candidates")
        if int(obs.header[0]) != obs_format:
            raise ValueError(f"frame format {int(obs.header[0])} does not match the {character} network's obs_format {obs_format}")
        with self._lock, torch.inference_mode():
            logits, value = net(self._collate(self._ragged.from_obs([obs], np.array([MODE_RUN]))).to("cpu"))
        row = logits[0, :n].float().cpu().numpy().astype(np.float64)
        shifted = row - row.max()
        probs = np.exp(shifted) / np.exp(shifted).sum()
        return {
            "ok": True, "choice": int(np.argmax(row)), "num_candidates": n, "character": character, "version": version,
            "logits": [float(x) for x in row], "probs": [float(x) for x in probs],
            "value": float(value[0].float().cpu()), "ms": round(1000 * (time.perf_counter() - t0), 1),
        }


def make_handler(policies: Policies):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path, _, query = self.path.partition("?")
            if path != "/health":
                self._send(404, {"ok": False, "error": "unknown path"})
                return
            reply = {"ok": True, "service": "alphadefect_policy", "characters": policies.available()}
            character = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("character", "").upper()
            if character:
                # Loading tells the caller the frame format it must ask the mod for.
                try:
                    reply["obs_format"] = policies.obs_format(character)
                    reply["content_alignment_version"] = CONTENT_ALIGNMENT_VERSION
                except Exception as exc:  # noqa: BLE001
                    reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            self._send(200, reply)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/infer":
                self._send(404, {"ok": False, "error": "unknown path"})
                return
            try:
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                self._send(200, policies.infer(str(request["character"]).upper(), base64.b64decode(request["frame"])))
            except Exception as exc:  # noqa: BLE001 - report every failure to the caller, keep serving
                self._send(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="downloaded runtime directory (engine/, models/)")
    ap.add_argument("--manifest", required=True, help="policy_manifest.json")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18090)
    a = ap.parse_args()
    policies = Policies(Path(a.root), json.loads(Path(a.manifest).read_text(encoding="utf-8")))
    server = ThreadingHTTPServer((a.host, a.port), make_handler(policies))
    print(f"[alphadefect_policy] models {sorted(policies.available())} listening on http://{a.host}:{a.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
