# -*- coding: utf-8 -*-
"""策略网络的本机资产（policy_assets）：下载、校验、安装。

用本机 HTTP 服务器代替镜像站，钉住：
1. 构造对象不下载任何东西；只有 ensure_runtime / download_model 才开始。
2. 校验不过的文件不落盘，换下一个地址；所有地址都不行则任务失败并写明原因。
3. 断了能从 .part 续传。
4. 运行环境装完才算就绪（完成标记 + 解释器 + 词表齐全），进度写进给前端读的状态文件。
5. 随包的清单本身自洽（每个文件有大小、SHA-256、至少一个 https 地址；六个角色都在）。
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
import socket
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from plugin.plugins.sts2_autoplay import policy_assets as assets_module
from plugin.plugins.sts2_autoplay.policy_assets import MANIFEST_PATH, PolicyAssets


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


FILES: dict[str, bytes] = {
    "python.zip": _zip({"python.exe": b"MZ", "python312._pth": b"python312.zip\n.\n", "python312.zip": b"x"}),
    "torch.whl": _zip({"torch/__init__.py": b"# torch", "torch/lib/big.dll": b"\x00" * 4096}),
    "vocab.txt": b"<pad>\n<unk>\nSTRIKE\n",
    "aliases.txt": b"# new old\n",
    "model.pt": bytes(range(256)) * 400,
}


class _Server:
    """`corrupt` 里的路径返回坏内容；`requests` 记下每次请求的 (路径, Range)。"""

    def __init__(self) -> None:
        self.corrupt: set[str] = set()
        self.truncate: dict[str, int] = {}
        self.disconnect: set[str] = set()
        self.ignore_range: set[str] = set()
        self.invalid_range: set[str] = set()
        self.http_errors: dict[str, int] = {}
        self.requests: list[tuple[str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                name = self.path.strip("/").split("/")[-1]
                outer.requests.append((self.path, self.headers.get("Range")))
                if self.path in outer.disconnect:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if self.path in outer.http_errors:
                    self.send_error(outer.http_errors[self.path])
                    return
                data = FILES.get(name)
                if data is None:
                    self.send_error(404)
                    return
                if self.path in outer.corrupt:
                    data = b"!" * len(data)
                start = int((self.headers.get("Range") or "bytes=0-").split("=")[1].rstrip("-"))
                if self.path in outer.ignore_range:
                    start = 0
                self.send_response(206 if start else 200)
                if start:
                    offset = start + 1 if self.path in outer.invalid_range else start
                    self.send_header("Content-Range", f"bytes {offset}-{len(data) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                body = data[start:]
                if self.path in outer.truncate:
                    body = body[:outer.truncate[self.path]]
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()


def _item(name: str, kind: str, urls: list[str], dest: str | None = None) -> dict[str, Any]:
    data = FILES[name]
    item = {"name": name, "kind": kind, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "urls": urls}
    if dest:
        item["dest"] = dest
    return item


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(assets_module, "_RETRY_DELAY", 0)


@pytest.fixture()
def server():
    instance = _Server()
    yield instance
    instance.close()


def _manifest(server: _Server, *, model_urls: list[str] | None = None) -> dict[str, Any]:
    base = server.base
    model = _item("model.pt", "model", model_urls or [f"{base}/a/model.pt"])
    return {
        "runtime_version": "test-1",
        "upstream": {"game_version": "v0.113.1"},
        "runtime": [
            _item("python.zip", "python", [f"{base}/a/python.zip"]),
            _item("torch.whl", "wheel", [f"{base}/a/torch.whl"]),
            _item("vocab.txt", "catalog", [f"{base}/a/vocab.txt"], dest="engine/catalog/vocab.txt"),
            _item("aliases.txt", "catalog", [f"{base}/a/aliases.txt"], dest="engine/catalog/aliases.txt"),
        ],
        "models": {"IRONCLAD": {"file": "model.pt", "label": "铁甲战士", **{k: model[k] for k in ("size", "sha256", "urls")}}},
    }


def _wait(assets: PolicyAssets, pick: Any, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = pick(assets.status())
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("下载任务没有结束")


def _runtime(status: dict[str, Any]) -> dict[str, Any]:
    return status["runtime"]


def _model(status: dict[str, Any]) -> dict[str, Any]:
    return status["models"][0]


@pytest.mark.unit
def test_constructing_downloads_nothing(server: _Server, tmp_path: Path) -> None:
    assets = PolicyAssets(root=tmp_path / "rt", manifest=_manifest(server))
    status = assets.status()
    assert server.requests == [] and not (tmp_path / "rt").exists()
    assert status["runtime"]["ready"] is False and status["runtime"]["state"] == "idle"
    assert [(m["character"], m["ready"], m["state"]) for m in status["models"]] == [("IRONCLAD", False, "idle")]
    assert not assets.usable("IRONCLAD") and assets.ready_characters() == []


@pytest.mark.unit
def test_runtime_install_end_to_end(server: _Server, tmp_path: Path) -> None:
    root = tmp_path / "rt"
    assets = PolicyAssets(root=root, manifest=_manifest(server))
    assets.status_path = tmp_path / "policy_status.json"
    assets.ensure_runtime()
    job = _wait(assets, _runtime)
    assert job["state"] == "done" and job["error"] == "", job
    assert assets.runtime_ready()
    assert (root / "python" / "python.exe").read_bytes() == b"MZ"
    assert (root / "site" / "torch" / "__init__.py").is_file(), "wheel 直接解压进 site/"
    assert (root / "python" / "python312._pth").read_text() == "python312.zip\n.\n../site\n", "嵌入式 Python 靠 ._pth 找到 site/"
    assert (root / "engine" / "catalog" / "vocab.txt").read_bytes() == FILES["vocab.txt"]
    assert not (root / "downloads").exists() and not (root / "staging").exists()
    assert assets.mod_data() == {"vocab.txt": FILES["vocab.txt"].decode(), "aliases.txt": FILES["aliases.txt"].decode()}
    assert assets.mod_data_id() == hashlib.sha256(FILES["vocab.txt"]).hexdigest()[:16]
    published = json.loads(assets.status_path.read_text(encoding="utf-8"))
    assert published["runtime"]["ready"] is True and published["runtime"]["state"] == "done"

    before = len(server.requests)
    assets.ensure_runtime()
    assert len(server.requests) == before, "已经装好就不再下载"


@pytest.mark.unit
def test_missing_small_file_is_fetched_without_redownloading_the_runtime(server: _Server, tmp_path: Path) -> None:
    root = tmp_path / "rt"
    assets = PolicyAssets(root=root, manifest=_manifest(server))
    assets.ensure_runtime()
    _wait(assets, _runtime)
    (root / "engine" / "catalog" / "aliases.txt").unlink()
    assert not assets.runtime_ready()
    server.requests.clear()
    assets.ensure_runtime()
    assert _wait(assets, _runtime)["state"] == "done" and assets.runtime_ready()
    assert [path for path, _ in server.requests] == ["/a/aliases.txt"]


@pytest.mark.unit
def test_corrupt_mirror_is_rejected_and_the_next_one_used(server: _Server, tmp_path: Path) -> None:
    server.corrupt.add("/bad/model.pt")
    assets = PolicyAssets(root=tmp_path / "rt", manifest=_manifest(server, model_urls=[f"{server.base}/bad/model.pt", f"{server.base}/good/model.pt"]))
    assets.download_model("ironclad")
    assert _wait(assets, _model)["state"] == "done"
    assert assets.model_ready("IRONCLAD") and assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]
    assert any(path == "/good/model.pt" for path, _ in server.requests)


@pytest.mark.unit
def test_all_mirrors_bad_fails_with_a_reason_and_leaves_no_file(server: _Server, tmp_path: Path) -> None:
    server.corrupt.add("/bad/model.pt")
    assets = PolicyAssets(root=tmp_path / "rt", manifest=_manifest(server, model_urls=[f"{server.base}/bad/model.pt", f"{server.base}/missing/nope.pt"]))
    assets.download_model("IRONCLAD")
    job = _wait(assets, _model)
    assert job["state"] == "failed" and "model.pt 下载失败" in job["error"], job
    assert not assets.model_ready("IRONCLAD") and not assets.model_path("IRONCLAD").exists()


@pytest.mark.unit
def test_download_resumes_from_the_part_file(server: _Server, tmp_path: Path) -> None:
    root = tmp_path / "rt"
    assets = PolicyAssets(root=root, manifest=_manifest(server))
    (root / "downloads").mkdir(parents=True)
    (root / "downloads" / "model.pt.part").write_bytes(FILES["model.pt"][:50_000])
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]
    assert ("/a/model.pt", "bytes=50000-") in server.requests, server.requests


@pytest.mark.unit
def test_remove_model(server: _Server, tmp_path: Path) -> None:
    assets = PolicyAssets(root=tmp_path / "rt", manifest=_manifest(server))
    assets.download_model("IRONCLAD")
    _wait(assets, _model)
    assets.remove_model("IRONCLAD")
    assert not assets.model_ready("IRONCLAD") and assets.status()["models"][0]["state"] == "idle"
    with pytest.raises(ValueError):
        assets.download_model("WATCHER")


@pytest.mark.unit
def test_shipped_manifest_is_self_consistent() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert set(manifest["models"]) == {"IRONCLAD", "SILENT", "DEFECT", "REGENT", "NECROBINDER", "FOOL"}
    entries = list(manifest["runtime"]) + list(manifest["models"].values())
    for entry in entries:
        assert int(entry["size"]) > 0 and len(entry["sha256"]) == 64, entry
        assert entry["urls"] and all(url.startswith("https://") for url in entry["urls"]), entry
    kinds = [item["kind"] for item in manifest["runtime"]]
    assert kinds.count("python") == 1 and "wheel" in kinds
    catalog = {item["dest"] for item in manifest["runtime"] if item["kind"] == "catalog"}
    version = manifest["upstream"]["game_version"]
    assert catalog == {
        "engine/catalog/vocab.txt", "engine/catalog/aliases.txt", "engine/catalog/no_downside_relics.txt",
        f"engine/catalog/{version}/catalog.json", "engine/catalog/v0.111.0/catalog.json",
    }
    commit = manifest["upstream"]["commit"]
    assert all(commit in url for item in manifest["runtime"] if item["kind"] == "catalog" for url in item["urls"]), "词表必须钉在同一个上游提交上"


@pytest.mark.unit
def test_short_response_resumes_on_next_mirror(server: _Server, tmp_path: Path) -> None:
    server.truncate["/short/model.pt"] = 12345
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server, model_urls=[
        f"{server.base}/short/model.pt", f"{server.base}/good/model.pt",
    ]))
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert server.requests == [("/short/model.pt", None), ("/good/model.pt", "bytes=12345-")]
    assert assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]


@pytest.mark.unit
def test_remote_disconnect_tries_backup_before_retry(server: _Server, tmp_path: Path) -> None:
    server.disconnect.add("/closed/model.pt")
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server, model_urls=[
        f"{server.base}/closed/model.pt", f"{server.base}/good/model.pt",
    ]))
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert [path for path, _ in server.requests] == ["/closed/model.pt", "/good/model.pt"]


@pytest.mark.unit
def test_failed_download_keeps_progress_for_user_retry(server: _Server, tmp_path: Path) -> None:
    server.truncate["/a/model.pt"] = 10000
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    assets.download_model("IRONCLAD")
    job = _wait(assets, _model)
    assert job["state"] == "failed"
    assert job["done_bytes"] == 30000
    part = tmp_path / "downloads/model.pt.part"
    assert part.read_bytes() == FILES["model.pt"][:30000]
    server.truncate.clear()
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert server.requests[-1] == ("/a/model.pt", "bytes=30000-")


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["ignore", "invalid"])
def test_resume_validates_response(server: _Server, tmp_path: Path, mode: str) -> None:
    getattr(server, "ignore_range" if mode == "ignore" else "invalid_range").add("/a/model.pt")
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server, model_urls=[
        f"{server.base}/a/model.pt", f"{server.base}/good/model.pt",
    ]))
    part = tmp_path / "downloads/model.pt.part"
    part.parent.mkdir()
    part.write_bytes(FILES["model.pt"][:10000])
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]
    if mode == "invalid":
        assert server.requests[-1] == ("/good/model.pt", "bytes=10000-")
    else:
        assert len(server.requests) == 1


@pytest.mark.unit
@pytest.mark.parametrize("code, attempts", [(404, 1), (403, 1), (429, 3), (503, 3)])
def test_bounded_http_retries(server: _Server, tmp_path: Path, code: int, attempts: int) -> None:
    server.http_errors["/a/model.pt"] = code
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    assets.download_model("IRONCLAD")
    job = _wait(assets, _model)
    assert job["state"] == "failed" and f"HTTP {code}" in job["error"]
    assert len(server.requests) == attempts


@pytest.mark.unit
def test_runtime_cleanup_preserves_other_downloads(server: _Server, tmp_path: Path) -> None:
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "model.pt.part").write_bytes(b"partial checkpoint")
    (downloads / "model.pt").write_bytes(b"completed checkpoint awaiting install")
    assets.ensure_runtime()
    assert _wait(assets, _runtime)["state"] == "done"
    assert (downloads / "model.pt.part").read_bytes() == b"partial checkpoint"
    assert (downloads / "model.pt").read_bytes() == b"completed checkpoint awaiting install"
    assert not (downloads / "python.zip").exists()


@pytest.mark.unit
def test_download_jobs_are_queued(tmp_path: Path) -> None:
    assets = PolicyAssets(root=tmp_path, manifest={})
    release = threading.Event()
    first_two = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0

    def work(job):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                first_two.set()
        assert release.wait(5)
        with lock:
            active -= 1

    jobs = [assets_module._Job() for _ in range(4)]
    try:
        for job in jobs:
            assets._start(job, work)
        assert first_two.wait(5)
        assert maximum == 2
        assert jobs[2].state == "running" and "排队" in jobs[2].message
    finally:
        release.set()
        for job in jobs:
            if job.thread:
                job.thread.join(5)
    assert maximum == 2 and all(job.state == "done" for job in jobs)


@pytest.mark.unit
def test_bad_partial_does_not_blacklist_healthy_mirror(server: _Server, tmp_path: Path) -> None:
    server.truncate["/bad/model.pt"] = 10000
    server.corrupt.add("/bad/model.pt")
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server, model_urls=[
        f"{server.base}/bad/model.pt", f"{server.base}/good/model.pt",
    ]))
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert server.requests == [
        ("/bad/model.pt", None), ("/good/model.pt", "bytes=10000-"), ("/good/model.pt", None),
    ]
    assert assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]


@pytest.mark.unit
def test_timeout_refreshes_opener_and_backs_off(server: _Server, tmp_path: Path, monkeypatch) -> None:
    build = assets_module.urllib.request.build_opener
    builds = []
    sleeps = []

    class TimeoutOpener:
        def open(self, request, timeout):
            assert timeout == 30
            raise TimeoutError("connect timeout")

    def opener(*args):
        builds.append(args)
        return TimeoutOpener() if len(builds) < 3 else build(*args)

    monkeypatch.setattr(assets_module.urllib.request, "build_opener", opener)
    monkeypatch.setattr(assets_module, "_RETRY_DELAY", 2)
    monkeypatch.setattr(assets_module, "sleep", sleeps.append)
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert sleeps == [2, 4] and len(builds) == 3


@pytest.mark.unit
def test_incomplete_read_preserves_exception_partial(server: _Server, tmp_path: Path, monkeypatch) -> None:
    build = assets_module.urllib.request.build_opener
    calls = []

    class BrokenResponse:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read1(self, size):
            raise http.client.IncompleteRead(FILES["model.pt"][:777], 1000)

    class BrokenOpener:
        def open(self, request, timeout):
            return BrokenResponse()

    def opener(*args):
        calls.append(1)
        return BrokenOpener() if len(calls) == 1 else build(*args)

    monkeypatch.setattr(assets_module.urllib.request, "build_opener", opener)
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    assets.download_model("IRONCLAD")
    assert _wait(assets, _model)["state"] == "done"
    assert server.requests == [("/a/model.pt", "bytes=777-")]
    assert assets.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]


@pytest.mark.unit
def test_catalog_added_by_upgrade_repairs_without_redownloading_runtime_or_models(server: _Server, tmp_path: Path) -> None:
    manifest = _manifest(server)
    old = PolicyAssets(root=tmp_path, manifest=manifest)
    old.ensure_runtime()
    assert _wait(old, _runtime)["state"] == "done"
    old.download_model("IRONCLAD")
    assert _wait(old, _model)["state"] == "done"
    manifest["runtime"].append(_item("aliases.txt", "catalog", [f"{server.base}/a/aliases.txt"], "engine/catalog/v0.111.0/catalog.json"))
    upgraded = PolicyAssets(root=tmp_path, manifest=manifest)
    state = upgraded.status()["runtime"]
    assert not state["ready"] and state["repair_needed"]
    assert state["missing_catalogs"] == ["engine/catalog/v0.111.0/catalog.json"]
    assert state["repair_bytes"] == len(FILES["aliases.txt"])
    assert upgraded.model_ready("IRONCLAD")
    server.requests.clear()
    upgraded.ensure_runtime()
    assert _wait(upgraded, _runtime)["state"] == "done" and upgraded.runtime_ready()
    assert server.requests == [("/a/aliases.txt", None)]
    assert upgraded.model_path("IRONCLAD").read_bytes() == FILES["model.pt"]
    assert not upgraded.status()["runtime"]["repair_needed"]


@pytest.mark.unit
def test_same_size_catalog_corruption_is_repaired_and_hash_is_cached(server: _Server, tmp_path: Path, monkeypatch) -> None:
    assets = PolicyAssets(root=tmp_path, manifest=_manifest(server))
    assets.ensure_runtime()
    assert _wait(assets, _runtime)["state"] == "done"
    original = assets_module._sha256
    hashed = []

    def counted(path):
        hashed.append(path)
        return original(path)

    monkeypatch.setattr(assets_module, "_sha256", counted)
    assert assets.runtime_ready() and assets.runtime_ready()
    assert not hashed, "unchanged catalogs must not be rehashed every autoplay tick"
    path = tmp_path / "engine/catalog/aliases.txt"
    path.write_bytes(b"x" * len(FILES["aliases.txt"]))
    assert not assets.runtime_ready()
    assert hashed == [path]
    assert not assets.runtime_ready() and hashed == [path]
    server.requests.clear()
    assets.ensure_runtime()
    assert _wait(assets, _runtime)["state"] == "done" and assets.runtime_ready()
    assert server.requests == [("/a/aliases.txt", None)]
    assert path.read_bytes() == FILES["aliases.txt"]
