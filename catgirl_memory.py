# -*- coding: utf-8 -*-
"""猫娘跨局记忆：每局结束把「教训 + 偏好」沉淀到磁盘，供策略决策参考。

与 preference_store（同会话、不落盘）不同，这里是**持久化**成长记忆：
- run 结束（死亡/通关）时总结本局教训/偏好，写入 JSON，重启后仍保留。
- summarize_recent 聚合最近几局的教训/偏好（去重），注入到 strategy_directives，
  让下一局的选牌/选路线/战斗决策能参考上一局踩的坑和偏好。

总结引擎：
- 默认启发式（确定性、零成本），从 recent_decision_memory 里提炼规律；
- 若传入可用 LLM（service 的 self._catgirl_llm），叠加自然语言归纳，失败静默回退启发式。
只注入软约束（avoid / prefer），不碰 must（硬约束），避免把玩家带进坑。
"""

from __future__ import annotations

import json
import os
from time import time
from typing import Any

# 跨局记忆条数上限（避免无限膨胀）
_RUN_CAP = 50


def _default_memory_path() -> str | None:
    """解析插件数据目录下的记忆文件路径。

    优先用环境变量（NEKO 插件数据根），回退到 LOCALAPPDATA/N.E.K.O 下的插件私有子目录。
    返回 None 表示环境无法确定（此时 store 退化为仅内存、不落盘）。
    """
    base = os.environ.get("NEKO_PLUGIN_DATA_DIR") or ""
    if base:
        return os.path.join(base, "sts2_autoplay", "catgirl_memory.json")
    try:
        local = os.environ.get("LOCALAPPDATA") or ""
        if local:
            return os.path.join(local, "N.E.K.O", "sts2_autoplay", "catgirl_memory.json")
    except Exception:
        pass
    return None


class CatgirlMemoryStore:
    """猫娘跨局记忆 store：落盘读写 + 启发式/LLM 总结 + 跨局聚合。

    Args:
        path: 记忆文件路径（默认从环境解析；None 时仅内存，不落盘）。
        engine: "heuristic" | "llm"（llm 需要传入可用 `llm` 实例）。
        logger: 用于告警日志。
    """

    def __init__(self, path: str | None = None, *, engine: str = "heuristic", logger: Any = None) -> None:
        # path=None -> 纯内存（不落盘、不读盘），用于测试；生产由调用方显式传 _default_memory_path()。
        self._path = path
        self._engine = engine
        self._logger = logger
        self._runs: list[dict[str, Any]] = []
        self.load()

    @property
    def path(self) -> str | None:
        return self._path

    @property
    def runs(self) -> list[dict[str, Any]]:
        return self._runs

    def load(self) -> None:
        """从磁盘读入最近记忆；文件不存在/损坏时空载（不中断）。"""
        self._runs = []
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("runs") if isinstance(data, dict) else None
            if isinstance(records, list):
                self._runs = [r for r in records if isinstance(r, dict)]
                self._runs = self._runs[-_RUN_CAP:]
        except (OSError, ValueError):
            self._runs = []

    def save(self) -> None:
        """把当前记忆写回磁盘（幂等；path 为 None 时静默跳过）。"""
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            payload = {"runs": self._runs[:_RUN_CAP], "updated_at": time()}
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            self._log(f"catgirl_memory save failed: {self._path}")

    def add_run(self, run_id: str, *, character: str, outcome: str, floor: int, act: int,
                decision_memory: list[dict[str, Any]], llm: Any = None) -> dict[str, Any]:
        """总结一局并追加到记忆（幂等：同 run_id 的旧记录先移除再添新，避免终端屏重复触发累积）。"""
        run = {
            "run_id": str(run_id or ""),
            "character": str(character or ""),
            "ended_at": time(),
            "outcome": str(outcome or ""),
            "floor": int(floor or 0),
            "act": int(act or 0),
            "lessons": [],
            "preferences": [],
        }
        summary = self.summarize_run(
            decision_memory=decision_memory,
            run_meta=run,
            llm=llm if self._engine == "llm" else None,
        )
        run["lessons"] = summary.get("lessons", [])
        run["preferences"] = summary.get("preferences", [])
        # 同 run_id 覆盖（去重），再追加
        self._runs = [r for r in self._runs if r.get("run_id") != run.get("run_id")]
        self._runs.append(run)
        self._runs = self._runs[-_RUN_CAP:]
        self.save()
        return dict(run)

    # ---- 总结 ----

    def summarize_run(self, *, decision_memory: list[dict[str, Any]], run_meta: dict[str, Any],
                      llm: Any = None) -> dict[str, Any]:
        """提炼一局的教训 + 偏好。

        - llm 为可用实例时叠加自然语言归纳（失败/不可用静默回退启发式）。
        - 启发式：low-hp 翻车/连败 -> 教训（avoid）；反复同型选择 -> 偏好（prefer）。
        """
        lessons = self._heuristic_lessons(decision_memory, run_meta)
        preferences = self._heuristic_preferences(decision_memory)
        lessons, preferences = self._dedup(lessons), self._dedup(preferences)
        if llm is not None and self._llm_available(llm):
            llm_out = self._summarize_with_llm(llm, decision_memory, run_meta)
            if llm_out:
                lessons = self._dedup(lessons + llm_out.get("lessons", []))
                preferences = self._dedup(preferences + llm_out.get("preferences", []))
        return {"lessons": lessons, "preferences": preferences}

    def summarize_recent(self, limit: int = 5) -> dict[str, list[str]]:
        """聚合最近 N 局的教训/偏好（去重），供注入 strategy_directives。"""
        lessons: list[str] = []
        preferences: list[str] = []
        for run in self._runs[-limit:]:
            lessons.extend(str(item) for item in run.get("lessons", []) if isinstance(item, (str, int)))
            preferences.extend(str(item) for item in run.get("preferences", []) if isinstance(item, (str, int)))
        return {"lessons": self._dedup(lessons), "preferences": self._dedup(preferences)}

    # ---- 启发式规则 ----

    @staticmethod
    def _heuristic_lessons(decision_memory: list[dict[str, Any]], run_meta: dict[str, Any]) -> list[str]:
        lessons: list[str] = []
        saw_low_hp = False
        for item in decision_memory:
            if not isinstance(item, dict):
                continue
            why = str(item.get("reason") or item.get("note") or item.get("summary") or "").lower()
            hp = item.get("current_hp")
            max_hp = item.get("max_hp")
            # 生命跌到危险（<35%）的决策 -> 提示别太激进。
            # 只要本局出现过一次低血决策就记一条；跨局聚合（summarize_recent）自会去重。
            if isinstance(hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0:
                ratio = float(hp) / float(max_hp)
                if ratio < 0.35:
                    saw_low_hp = True
            if any(k in why for k in ("翻车", "失误", "暴毙", "秒杀", "战败", "贪", "上头")):
                lessons.append("这个位置容易翻车，提前留防御/留退路")
        if saw_low_hp:
            lessons.append("血量过低时别贪输出，优先保命叠甲")
        # 极端结局兜底：如果整局叠到低血但没抓到教训
        if not lessons and str(run_meta.get("outcome") or "") == "defeat":
            lessons.append("这局输得不明不白，下次决策慢一点、多留容错")
        return lessons

    @staticmethod
    def _heuristic_preferences(decision_memory: list[dict[str, Any]]) -> list[str]:
        # 同上型行动反复出现 -> 偏好；简单统计 action_type 频次
        preferences: list[str] = []
        seen = [str(item.get("action_type") or item.get("kind") or "") for item in decision_memory if isinstance(item, dict)]
        # 只保留出现 >=2 次的类型，避免把偶发当偏好
        for action_type in set(seen):
            if action_type and seen.count(action_type) >= 2:
                preferences.append(f"偏好这类选择：{action_type}")
        return preferences

    @staticmethod
    def _dedup(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            normalized = str(item).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
        return out

    # ---- LLM 可选扩展点 ----

    @staticmethod
    def _llm_available(llm: Any) -> bool:
        try:
            return bool(getattr(llm, "available", False))
        except Exception:
            return False

    def _summarize_with_llm(self, llm: Any, decision_memory: list[dict[str, Any]], run_meta: dict[str, Any]) -> dict[str, Any]:
        """LLM 自然语言归纳一局教训/偏好。失败/解析不出任何东西返回空 dict（调用方回退启发式）。"""
        try:
            memory_line = "；".join(
                f"{str(item.get('action_type') or item.get('kind') or '')}:{str(item.get('reason') or item.get('summary') or '')}"
                for item in decision_memory[:15] if isinstance(item, dict)
            ) or "（本局无关键决策记录）"
            outcome = str(run_meta.get("outcome") or "?")
            text = llm.generate(
                summary_text=f"本局结局:{outcome}。关键决策:{memory_line}",
                summary_kind="memory_summary",
                payload={},
            )
            if not text:
                return {}
            # 简单按分隔符切出 lessons / preferences 两条；LLM 可能自由发挥，取不到就都算教训
            lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
            lessons = [ln for ln in lines if ln.startswith(("教训", "lesson"))]
            preferences = [ln for ln in lines if ln.startswith(("偏好", "prefer"))]
            if not lessons and not preferences:
                lessons = lines[:1] or []
            return {"lessons": lessons, "preferences": preferences}
        except Exception:
            return {}

    def _log(self, msg: str) -> None:
        try:
            if self._logger is not None:
                self._logger.info(msg)
            else:
                print(msg)
        except Exception:
            pass


__all__ = ["CatgirlMemoryStore", "_default_memory_path"]
