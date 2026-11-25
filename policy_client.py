"""AlphaDefect 策略网络的插件侧接线（https://github.com/hmty-CHEN/AlphaDefect-Public）。

一步决策的三段路：

    mod  GET /policy/decision   当前界面的候选列表 + 观测帧（帧与上游引擎逐字节相同，
                                见 game_mod/PolicyFidelity）
    旁路 POST /infer            网络给每个候选打分（policy_sidecar/alphadefect_server.py，
                                独立进程：N.E.K.O 的 Python 里没有 PyTorch）
    mod  POST /action policy_act 执行被选中的候选（mod 自己把候选翻译成游戏动作）

运行环境（Python + PyTorch）与各角色的模型由下载前端按需下载（policy_assets.PolicyAssets），
这里**不触发任何下载**，也没有任何路径配置：本局角色的模型在、运行环境在，就用网络；否则回落。

★ 任何一段不成立都**返回 None 并写明原因**，调用方（loop_runner.tick）照旧走
  CombatSolver / 启发式那条路。这里不猜、不重试、不替网络补动作：
    · 运行环境没装 / 本局角色没有下载模型 / 旁路没起 → None
    · mod 说这一屏没有适配（available=false）     → None
    · 网络选中的候选在 mod 里没有对应动作         → None
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any

from .policy_assets import MANIFEST_PATH, PolicyAssets

POLICY_SOURCE = "alphadefect_policy"
POLICY_ACTION_TYPE = "policy_act"

#: 合成的动作注册项：`policy_act` 不在 mod 的 /actions/available 里（那张表描述的是界面上的
#: 动作，其它消费者按它判断"这一屏还有没有能推进的动作"），由 tick 在选用策略动作时补进注册表。
POLICY_REGISTRY_ENTRY: dict[str, Any] = {
    "id": POLICY_ACTION_TYPE,
    "type": POLICY_ACTION_TYPE,
    "category": "policy",
    "label": POLICY_ACTION_TYPE,
    "raw": {},
    "default_kwargs": {},
    "requires_index": True,
    "requires_target": False,
}

_SIDECAR_SCRIPT = Path(__file__).resolve().parent / "policy_sidecar" / "alphadefect_server.py"


def is_policy_operation(operation: Any) -> bool:
    return isinstance(operation, dict) and operation.get("source") == POLICY_SOURCE


def is_policy_hold(operation: Any) -> bool:
    """「这一刻归网络管，但现在还动不了」（敌方回合、上一个动作还在结算）：等，不要交给后备。"""
    return isinstance(operation, dict) and bool(operation.get("policy_hold"))


class PolicyAdvisor:
    #: 旁路不可用时，隔多久再探一次（秒）。期间每个 tick 直接回落，不反复等连接超时。
    SIDECAR_RETRY_SECONDS = 15.0
    #: 把词表推给模组后，隔多久才允许再推一次（秒）：推了仍对不上时不要每个 tick 都推。
    DATA_PUSH_RETRY_SECONDS = 30.0

    def __init__(self, logger: Any, assets: PolicyAssets | None = None) -> None:
        self._logger = logger
        self.assets = assets if assets is not None else PolicyAssets(logger)
        self.enabled = True
        self._sidecar_url = "http://127.0.0.1:18090"
        self._timeout = 20.0
        #: 各角色网络要的观测格式（问旁路得到；旁路重启后重问）。
        self._formats: dict[str, int] = {}
        self._next_probe_at = 0.0
        self._next_data_push_at = 0.0
        self._process: subprocess.Popen[bytes] | None = None
        self._last_note = ""
        self.last_decision: dict[str, Any] = {}
        self.decisions = 0
        self.fallbacks = 0

    def configure(self, cfg: dict[str, Any]) -> None:
        self.enabled = bool(cfg.get("policy_enabled", True))
        self._sidecar_url = str(cfg.get("policy_sidecar_url") or self._sidecar_url).rstrip("/")
        try:
            self._timeout = max(1.0, float(cfg.get("policy_timeout_seconds", 20) or 20))
        except (TypeError, ValueError):
            self._timeout = 20.0
        self._formats = {}
        self._next_probe_at = 0.0

    def playable_characters(self) -> list[str]:
        """现在能交给网络的角色（运行环境在、模型已下载）；关着或一个都没有时为空（选人照旧随机）。"""
        if not self.enabled or not self.assets.runtime_ready():
            return []
        return self.assets.ready_characters()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "sidecar_url": self._sidecar_url,
            "runtime_ready": self.assets.runtime_ready(),
            "characters": self.assets.ready_characters(),
            "decisions": self.decisions,
            "fallbacks": self.fallbacks,
            "last_note": self._last_note,
            "last_decision": dict(self.last_decision),
        }

    def shutdown(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()

    # ---- 一步决策 -----------------------------------------------------------------------

    async def decide(self, client: Any, *, character: str, map_policy: str = "auto") -> dict[str, Any] | None:
        """给当前界面一个操作（`agent_operation` 形状），或 None（回落到原有逻辑）。

        `character` 是本局的角色（来自 /state）；空串 = 不在对局里（主菜单、选人），不归网络管。
        """
        scored = await self._score(client, character=character, map_policy=map_policy, fail=self._fallback)
        if scored is None or scored.get("policy_hold"):
            return scored
        kind, candidates, choice = scored["kind"], scored["candidates"], scored["choice"]
        probability, forced = scored["probability"], scored["forced"]
        picked = candidates[choice]
        label = f"{kind}: {picked.get('label')}"
        if not picked.get("executable"):
            return self._fallback(f"网络选了「{label}」，但 mod 没有对应的动作")

        self.decisions += 1
        self.last_decision = {
            "kind": kind, "label": str(picked.get("label") or ""), "choice": choice, "candidates": len(candidates),
            "probability": probability, "value": scored["value"], "forced": forced,
            "multiplayer": scored["multiplayer"], "action": picked.get("action"), "character": scored["character"],
        }
        self._note(
            "选择 %s（候选 %d 个%s%s）" % (
                label, len(candidates),
                "" if probability is None else "，概率 %.2f" % probability,
                "，无需网络" if forced else "",
            ),
            key=f"{scored['decision_id']}:{choice}",
        )
        return {
            "action_type": POLICY_ACTION_TYPE,
            "kwargs": {"option_index": choice, "command": scored["decision_id"]},
            "confidence": 1.0 if probability is None else probability,
            "source": POLICY_SOURCE,
            "reason": label,
            "action_id": POLICY_ACTION_TYPE,
            "category": "policy",
            "candidate_actions": [str(c.get("label") or "") for c in candidates if isinstance(c, dict)][:12],
            "policy": dict(self.last_decision),
        }

    async def recommend(self, client: Any, *, character: str, known_id: str = "") -> dict[str, Any] | None:
        """玩家自己打的这一步，网络会怎么选：只打分，不执行、不计数（局末复盘用，见 choice_review）。

        返回 `_score` 的结果（含每个候选的概率）；这一屏网络给不出、或只有一个候选时返回 None。
        `known_id` 是上次已经打过分的决策：界面没变时返回 ``{"decision_id":…, "unchanged": True}``，不重复推理。
        """
        scored = await self._score(client, character=character, map_policy="auto", fail=self._no_recommendation, known_id=known_id)
        if scored is None or scored.get("policy_hold") or scored.get("forced"):
            return None
        return scored

    async def _score(self, client: Any, *, character: str, map_policy: str, fail: Any, known_id: str = "") -> dict[str, Any] | None:
        """问 mod 要当前界面的决策，再让网络给每个候选打分。

        返回 ``{decision_id, kind, candidates, choice, probs, probability, value, forced, multiplayer, character}``，
        或 ``{"policy_hold": True}``（归网络管但现在动不了），或 ``fail(原因)`` 的返回值（给不出）。
        """
        character = str(character or "").upper()
        if not self.enabled or not character:
            return None
        if not self.assets.runtime_ready():
            return fail("策略网络的运行环境还没下载（在插件面板的「策略网络」页下载）")
        if not self.assets.model_ready(character):
            return fail(f"没有下载 {character} 的模型")
        obs_format = await self._obs_format(character, fail)
        if obs_format is None:
            return None
        try:
            decision = await client.get_policy_decision(obs_format)
        except Exception as exc:  # noqa: BLE001 —— 取不到决策就回落，原因写进日志
            return fail(f"mod /policy/decision 失败：{type(exc).__name__}: {exc}")
        if isinstance(decision, dict) and decision.get("data_id") != self.assets.mod_data_id():
            # 模组没有词表，或它的词表与网络的不是同一份（编号对不上）：把本机这份交给它，下一轮再问。
            return await self._push_mod_data(client, decision.get("data_id"), fail)
        if not isinstance(decision, dict) or not decision.get("available"):
            reason = decision.get("reason") if isinstance(decision, dict) else "响应不是对象"
            if isinstance(decision, dict) and decision.get("wait"):
                # 不是"没有适配"，是"还没轮到"：此刻让求解器 / 启发式接手，等于替网络出了这一步。
                self._note(f"等待：{reason}", key=f"wait:{reason}")
                return {"policy_hold": True, "reason": str(reason or "")}
            return fail(f"mod 不给这一屏出决策：{reason}")
        kind = str(decision.get("decision_kind") or "")
        decision_id = str(decision.get("decision_id") or "")
        candidates = decision.get("candidates") if isinstance(decision.get("candidates"), list) else []
        if str(decision.get("character") or "").upper() != character:
            return fail(f"/state 说这一局是 {character}，mod 的决策是 {decision.get('character')} 的")
        # 多人局的路线归玩家：map_policy 不是 auto 时地图照旧走原有的等待 / 跟票逻辑。
        if kind == "MAP" and decision.get("multiplayer") and map_policy != "auto":
            return fail(f"多人局地图按 map_policy={map_policy} 处理，不交给网络")
        if known_id and decision_id == known_id:
            return {"decision_id": decision_id, "unchanged": True}

        forced = decision.get("forced_index")
        probability: float | None = None
        value: float | None = None
        probs: list[float] = []
        if isinstance(forced, int):
            choice = forced
        else:
            frame = decision.get("frame")
            if not isinstance(frame, str) or not frame:
                return fail("mod 的决策里没有观测帧")
            try:
                scored = await self._sidecar_infer(character, frame)
            except Exception as exc:  # noqa: BLE001
                self._formats = {}
                self._next_probe_at = monotonic() + self.SIDECAR_RETRY_SECONDS
                return fail(f"推理旁路失败：{type(exc).__name__}: {exc}")
            if not scored.get("ok") or int(scored.get("num_candidates", -1)) != len(candidates):
                return fail(f"推理旁路没有给出可用结果：{scored.get('error') or scored}")
            choice = int(scored["choice"])
            probs = [float(p) for p in scored.get("probs")] if isinstance(scored.get("probs"), list) else []
            probability = probs[choice] if 0 <= choice < len(probs) else None
            value = float(scored["value"]) if isinstance(scored.get("value"), (int, float)) else None
        if not 0 <= choice < len(candidates) or not isinstance(candidates[choice], dict):
            return fail(f"选中的候选下标 {choice} 不在候选列表里（共 {len(candidates)} 个）")
        return {
            "decision_id": decision_id, "kind": kind, "candidates": candidates, "choice": choice, "probs": probs,
            "probability": probability, "value": value, "forced": isinstance(forced, int),
            "multiplayer": bool(decision.get("multiplayer")), "character": character,
        }

    def _fallback(self, why: str) -> None:
        self.fallbacks += 1
        self._note("回落到原有逻辑：" + why, key="fallback:" + why)
        return None

    def _no_recommendation(self, why: str) -> None:
        """`recommend` 给不出打分时的去处：玩家自己在打，没有什么"回落"，不计入 fallbacks，只留一句原因。"""
        self._note("对照复盘这一步拿不到网络的推荐：" + why, key="no-recommendation:" + why)
        return None

    def _note(self, message: str, *, key: str) -> None:
        """同一句话只记一次（tick 每秒都跑，局面不变时不刷屏）。"""
        if key == self._last_note:
            return
        self._last_note = key
        try:
            self._logger.info("[sts2_policy] %s", message)
        except Exception:  # noqa: BLE001
            pass

    # ---- 模组的词表 ---------------------------------------------------------------------

    async def _push_mod_data(self, client: Any, mod_data_id: Any, fail: Any) -> None:
        mine = self.assets.mod_data_id()
        if monotonic() < self._next_data_push_at:
            return fail(f"模组的词表（{mod_data_id}）与本机的（{mine}）不一致，刚交过一次，稍后再试")
        self._next_data_push_at = monotonic() + self.DATA_PUSH_RETRY_SECONDS
        try:
            result = await client.push_policy_data(self.assets.mod_data())
        except Exception as exc:  # noqa: BLE001
            return fail(f"把词表交给模组失败：{type(exc).__name__}: {exc}")
        now = result.get("data_id") if isinstance(result, dict) else None
        return fail(f"已把词表交给模组（{mod_data_id} -> {now}），下一步起生效")

    # ---- 推理旁路 -----------------------------------------------------------------------

    async def _obs_format(self, character: str, fail: Any) -> int | None:
        """这个角色的网络要的观测格式；旁路没起就拉起它（本轮回落），起不来返回 None。

        `fail` 是"这一步给不出"的去处：自动玩是 `_fallback`（计数），对照复盘是 `_no_recommendation`（不计数）。
        """
        if character in self._formats:
            return self._formats[character]
        if monotonic() < self._next_probe_at:
            return None
        self._next_probe_at = monotonic() + self.SIDECAR_RETRY_SECONDS
        try:
            info = await self._sidecar_health(character)
        except Exception as exc:  # noqa: BLE001
            started = self._start_sidecar()
            fail(
                f"推理旁路 {self._sidecar_url} 连不上（{type(exc).__name__}）"
                + ("，已启动旁路进程，稍后再试" if started else "，也没能启动它")
            )
            return None
        if not isinstance(info, dict) or not info.get("ok") or not isinstance(info.get("obs_format"), int):
            fail(f"推理旁路加载 {character} 的模型失败：{info.get('error') if isinstance(info, dict) else info}")
            return None
        self._formats[character] = int(info["obs_format"])
        self._next_probe_at = 0.0
        self._note("推理旁路就绪：%s，观测格式 %s" % (character, info["obs_format"]), key=f"sidecar-ready:{character}")
        return self._formats[character]

    def _start_sidecar(self) -> bool:
        if self._process is not None and self._process.poll() is None:
            return True
        port = self._sidecar_url.rsplit(":", 1)[-1]
        command = [
            str(self.assets.python_exe), "-I", "-X", "utf8", str(_SIDECAR_SCRIPT),
            "--root", str(self.assets.root), "--manifest", str(MANIFEST_PATH), "--port", port,
        ]
        try:
            self._process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
                env={k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")},
            )
        except OSError as exc:
            self._note(f"启动推理旁路失败：{exc}", key=f"sidecar-start:{exc}")
            return False
        return True

    async def _sidecar_health(self, character: str) -> dict[str, Any]:
        # 第一次问某个角色时旁路要把它的模型读进内存（几秒），所以超时比普通探活长。
        response = await self._http().get(f"{self._sidecar_url}/health?character={character}", timeout=30.0)
        return response.json()

    async def _sidecar_infer(self, character: str, frame: str) -> dict[str, Any]:
        response = await asyncio.wait_for(
            self._http().post(self._sidecar_url + "/infer", json={"character": character, "frame": frame}, timeout=self._timeout),
            timeout=self._timeout + 1.0,
        )
        return response.json()

    @staticmethod
    def _http() -> Any:
        # 与 mod 请求同一个宿主 client：不经系统代理（见 transport_client._internal_client）。
        from .transport_client import _internal_client

        return _internal_client()


__all__ = [
    "PolicyAdvisor", "POLICY_SOURCE", "POLICY_ACTION_TYPE", "POLICY_REGISTRY_ENTRY",
    "is_policy_operation", "is_policy_hold",
]
