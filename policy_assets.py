"""AlphaDefect 策略网络的本机资产：运行环境（Python + PyTorch）与各角色的模型文件。

这些东西太大，进不了插件包（PyTorch 装好约 600 MB，模型每个角色约 78 MB），由**下载前端**
（static/policy.html）按需下载到用户目录：

    %LOCALAPPDATA%/N.E.K.O/sts2_autoplay/policy_runtime/
        python/      嵌入式 Python（官方 zip，解压即用）
        site/        PyTorch、numpy 及其依赖（wheel 直接解压，不用 pip）
        engine/      词表与内容目录（来自上游仓库的固定提交）
        models/      各角色的权重文件
        downloads/   下载中的临时文件（*.part，可续传）

★ 本模块**不在插件启动时做任何下载**：只有前端调用 `ensure_runtime()` / `download_model()` 才开始。
★ 每个文件都有固定的大小与 SHA-256（policy_manifest.json），校验不过就不落盘。
★ 每个文件有多个下载地址，按顺序试；断了从 `.part` 续传。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from time import time
from typing import Any, Callable

MANIFEST_PATH = Path(__file__).resolve().parent / "policy_manifest.json"
_CHUNK = 1 << 18
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler())      # 走系统代理设置（外网下载）


def default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "N.E.K.O" / "sts2_autoplay" / "policy_runtime"


class _Job:
    """一次后台下载的进度。`state`: idle / running / done / failed。"""

    def __init__(self) -> None:
        self.state = "idle"
        self.done_bytes = 0
        self.total_bytes = 0
        self.current = ""
        self.error = ""
        self.started_at = 0.0
        self.thread: threading.Thread | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "done_bytes": self.done_bytes, "total_bytes": self.total_bytes,
            "current": self.current, "error": self.error,
        }


class PolicyAssets:
    def __init__(self, logger: Any = None, root: Path | None = None, manifest: dict[str, Any] | None = None) -> None:
        self._logger = logger
        self.root = Path(root) if root is not None else default_root()
        self.manifest = manifest if manifest is not None else json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self._lock = threading.Lock()
        #: 下载前端读的进度文件（插件的 static 目录下）。前端是静态页，读不到插件内存，所以落一份快照。
        self.status_path: Path | None = None
        self._published_at = 0.0
        self._runtime_job = _Job()
        self._model_jobs: dict[str, _Job] = {}

    # ---- 路径与就绪判定 -----------------------------------------------------------------

    @property
    def python_exe(self) -> Path:
        return self.root / "python" / "python.exe"

    @property
    def _runtime_marker(self) -> Path:
        return self.root / "runtime.json"

    def _interpreter_ready(self) -> bool:
        try:
            marker = json.loads(self._runtime_marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return marker.get("runtime_version") == self.manifest["runtime_version"] and self.python_exe.is_file()

    def _missing_catalog(self) -> list[dict[str, Any]]:
        return [
            item for item in self.manifest["runtime"]
            if item["kind"] == "catalog" and not ((self.root / item["dest"]).is_file() and (self.root / item["dest"]).stat().st_size == int(item["size"]))
        ]

    def runtime_ready(self) -> bool:
        """运行环境装完整了：完成标记的版本与清单一致、解释器在、词表与内容目录齐全。"""
        return self._interpreter_ready() and not self._missing_catalog()

    # ---- 给模组的词表 -------------------------------------------------------------------

    _MOD_DATA = ("vocab.txt", "aliases.txt", "no_downside_relics.txt")

    def mod_data_id(self) -> str:
        """本机词表的标识（vocab.txt 的 SHA-256 前 16 位），与模组 /policy/decision 的 `data_id` 同一算法。"""
        path = self.root / "engine" / "catalog" / "vocab.txt"
        return _sha256(path)[:16] if path.is_file() else ""

    def mod_data(self) -> dict[str, str]:
        """模组编码观测要用的三个文本文件（模组自己不带，见 PolicyService.InstallData）。"""
        out: dict[str, str] = {}
        for name in self._MOD_DATA:
            path = self.root / "engine" / "catalog" / name
            if path.is_file():
                out[name] = path.read_bytes().decode("utf-8")
        return out

    def model_path(self, character: str) -> Path | None:
        spec = self.manifest["models"].get(str(character or "").upper())
        return self.root / "models" / spec["file"] if spec else None

    def model_ready(self, character: str) -> bool:
        path = self.model_path(character)
        spec = self.manifest["models"].get(str(character or "").upper())
        # 大小对得上即认为可用：SHA-256 在下载落盘那一刻已经校验过，这里不每次重算 78 MB。
        return bool(path and spec and path.is_file() and path.stat().st_size == int(spec["size"]))

    def ready_characters(self) -> list[str]:
        return [c for c in self.manifest["models"] if self.model_ready(c)]

    def usable(self, character: str) -> bool:
        """这个角色现在能不能交给网络：运行环境与它的模型都在。"""
        return self.runtime_ready() and self.model_ready(character)

    def status(self) -> dict[str, Any]:
        runtime_size = sum(int(item["size"]) for item in self.manifest["runtime"])
        return {
            "root": str(self.root),
            "runtime": {
                "ready": self.runtime_ready(), "download_bytes": runtime_size,
                "version": self.manifest["runtime_version"], **self._runtime_job.as_dict(),
            },
            "models": [
                {
                    "character": character, "label": spec.get("label") or character, "file": spec["file"],
                    "size": int(spec["size"]), "ready": self.model_ready(character),
                    **self._model_jobs.get(character, _Job()).as_dict(),
                }
                for character, spec in self.manifest["models"].items()
            ],
        }

    def publish(self, *, force: bool = True) -> None:
        """把当前状态写进进度文件（下载中约每 0.7 秒一次；失败不影响下载）。"""
        if self.status_path is None or (not force and time() - self._published_at < 0.7):
            return
        self._published_at = time()
        try:
            tmp = self.status_path.with_name(self.status_path.name + ".tmp")
            tmp.write_text(json.dumps({**self.status(), "updated_at": time()}, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.status_path)
        except OSError:
            pass

    # ---- 后台任务 -----------------------------------------------------------------------

    def ensure_runtime(self) -> dict[str, Any]:
        """运行环境不在就开始后台下载安装（已在或正在装则什么都不做）。"""
        with self._lock:
            job = self._runtime_job
            if self.runtime_ready():
                job.state = "done"
            elif job.state != "running":
                self._start(job, self._install_runtime)
        self.publish()
        return self.status()

    def download_model(self, character: str) -> dict[str, Any]:
        character = str(character or "").upper()
        if character not in self.manifest["models"]:
            raise ValueError(f"没有 {character} 的模型")
        with self._lock:
            job = self._model_jobs.setdefault(character, _Job())
            if self.model_ready(character):
                job.state = "done"
            elif job.state != "running":
                self._start(job, lambda j: self._install_model(character, j))
        self.publish()
        return self.status()

    def remove_model(self, character: str) -> dict[str, Any]:
        path = self.model_path(character)
        job = self._model_jobs.get(str(character or "").upper())
        if job is not None and job.state == "running":
            raise RuntimeError("这个模型正在下载")
        if path is not None and path.is_file():
            path.unlink()
        if job is not None:
            job.state = "idle"
        self.publish()
        return self.status()

    def _start(self, job: _Job, work: Callable[[_Job], None]) -> None:
        job.state, job.error, job.done_bytes, job.total_bytes, job.started_at = "running", "", 0, 0, time()

        def run() -> None:
            try:
                work(job)
                job.state = "done"
            except Exception as exc:  # noqa: BLE001 —— 任何失败都要落到面板上
                job.state, job.error = "failed", f"{type(exc).__name__}: {exc}"
                self._log("下载失败：%s", job.error)
            self.publish()

        job.thread = threading.Thread(target=run, name="sts2-policy-download", daemon=True)
        job.thread.start()

    def _log(self, message: str, *args: Any) -> None:
        try:
            if self._logger is not None:
                self._logger.info("[sts2_policy_assets] " + message, *args)
        except Exception:  # noqa: BLE001
            pass

    # ---- 安装 ---------------------------------------------------------------------------

    def _install_runtime(self, job: _Job) -> None:
        downloads = self.root / "downloads"
        if self._interpreter_ready():
            # 解释器与 PyTorch 已在，只缺几个小的数据文件：单独补上，不重下几百 MB。
            items = self._missing_catalog()
            job.total_bytes = sum(int(item["size"]) for item in items)
            for item in items:
                job.current = item["name"]
                path = self._fetch(item, downloads / item["name"], job, job.done_bytes)
                target = self.root / item["dest"]
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, target)
            return
        items = self.manifest["runtime"]
        job.total_bytes = sum(int(item["size"]) for item in items)
        fetched: list[tuple[dict[str, Any], Path]] = []
        base = 0
        for item in items:
            job.current = item["name"]
            fetched.append((item, self._fetch(item, downloads / item["name"], job, base)))
            base += int(item["size"])
            job.done_bytes = base
        job.current = "解压"
        # 先装到旁边的目录，全部成功再换上去：装到一半失败不会留下一个"看着像装好了"的环境。
        staging = self.root / "staging"
        shutil.rmtree(staging, ignore_errors=True)
        for item, path in fetched:
            if item["kind"] == "python":
                with zipfile.ZipFile(path) as archive:
                    archive.extractall(staging / "python")
            elif item["kind"] == "wheel":
                with zipfile.ZipFile(path) as archive:
                    archive.extractall(staging / "site")
            else:
                target = staging / item["dest"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        # 嵌入式 Python 不认 PYTHONPATH，搜索路径写在 ._pth 里：自带的标准库 zip、自身目录、site。
        for pth in (staging / "python").glob("*._pth"):
            pth.write_text(f"{pth.stem.split('.')[0]}.zip\n.\n../site\n", encoding="ascii")
        self._runtime_marker.unlink(missing_ok=True)
        for name in ("python", "site", "engine"):
            shutil.rmtree(self.root / name, ignore_errors=True)
            (staging / name).rename(self.root / name)
        shutil.rmtree(staging, ignore_errors=True)
        self._runtime_marker.write_text(json.dumps({"runtime_version": self.manifest["runtime_version"], "installed_at": time()}), encoding="utf-8")
        shutil.rmtree(downloads, ignore_errors=True)
        self._log("运行环境就绪：%s", self.root)

    def _install_model(self, character: str, job: _Job) -> None:
        spec = self.manifest["models"][character]
        job.total_bytes, job.current = int(spec["size"]), spec["file"]
        part = self._fetch({"name": spec["file"], **spec}, self.root / "downloads" / spec["file"], job, 0)
        target = self.root / "models" / spec["file"]
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(part, target)
        self._log("模型就绪：%s -> %s", character, target)

    def _fetch(self, item: dict[str, Any], dest: Path, job: _Job, base: int) -> Path:
        """把一个文件下到 `dest`（校验通过才算），返回 `dest`。逐个地址试，断点续传。"""
        size, digest = int(item["size"]), str(item["sha256"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_file() and dest.stat().st_size == size and _sha256(dest) == digest:
            return dest
        part = dest.with_name(dest.name + ".part")
        errors: list[str] = []
        for url in item["urls"]:
            for _attempt in range(3):
                try:
                    have = part.stat().st_size if part.is_file() else 0
                    if have > size:
                        part.unlink()
                        have = 0
                    if have < size:
                        request = urllib.request.Request(url, headers={"User-Agent": "sts2_autoplay", **({"Range": f"bytes={have}-"} if have else {})})
                        with _OPENER.open(request, timeout=30) as response:
                            if have and response.status != 206:      # 这个地址不支持续传：从头来
                                have = 0
                            with open(part, "ab" if have else "wb") as handle:
                                while True:
                                    chunk = response.read(_CHUNK)
                                    if not chunk:
                                        break
                                    handle.write(chunk)
                                    have += len(chunk)
                                    job.done_bytes = base + min(have, size)
                                    self.publish(force=False)
                    if part.stat().st_size != size or _sha256(part) != digest:
                        part.unlink()
                        raise ValueError("大小或 SHA-256 与清单不符")
                    os.replace(part, dest)
                    return dest
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    errors.append(f"{url.split('/')[2]}: {type(exc).__name__}: {exc}")
        raise RuntimeError(f"{item['name']} 下载失败（{'; '.join(errors[-3:])}）")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["PolicyAssets", "default_root", "MANIFEST_PATH"]
