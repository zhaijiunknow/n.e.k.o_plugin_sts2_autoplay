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
import uuid
from time import time
from typing import Any

# 跨局记忆条数上限（避免无限膨胀）
_RUN_CAP = 50

#: 兜底教训文本（"没有任何可执行信息"的那一条）。
#: ★实测它是**最高频**的教训（41/53 = 77.36%），且 `_lesson_tokens()` 对它返回 `[]`
#: ⇒ 它既挤占注入窗口，又无法影响任何决策。集中成常量，便于识别与治理。
_FALLBACK_LESSON = "这局输得不明不白，下次决策慢一点、多留容错"

#: ★R5：真实 `decisions[].reason` 全是**英文标签**（660 条 / 25 种，中文关键词命中 0/660）。
#: 这张表把"英文标签"翻译成**可执行的行为教训**。
#: 键是标签前缀/全称的小写形式，值是对应的教训文本（含中文行为词，供 `_lesson_tokens` 抽取）。
_REASON_TO_LESSON: tuple[tuple[tuple[str, ...], str], ...] = (
    (("mod_solver_end_turn",),
     "这局有好几回合打不出更优解只能直接结束回合，下次每回合先垫够格挡再输出"),
    (("combat_preference_or_default",),
     "这局战斗里好几步都只能走默认兜底，下次别光着急出攻击牌，先垫格挡再输出"),
    (("map_preference_or_default",),
     "这局选路线时只能按默认偏好走，下次提前绕开精英、少走危险的房间"),
    (("reward_",),
     "这局选牌奖励拿得比较随意，下次优先拿格挡牌、别贪输出牌"),
    (("shop_",),
     "这局商店花钱没什么章法，下次攒钱买关键的，别乱买"),
)


def _is_mistake_reason(reason: str) -> bool:
    """该 reason 是否代表一次"值得复盘的具体失误"。"""
    text = str(reason or "").strip().lower()
    if not text:
        return False
    return any(text.startswith(prefix) for prefix, _lesson in _REASON_TO_LESSON)


def _lesson_for_reason(reason: str) -> str:
    """把英文 reason 标签映射成一条**可执行**的教训文本。"""
    text = str(reason or "").strip().lower()
    for prefixes, lesson in _REASON_TO_LESSON:
        if any(text.startswith(prefix) for prefix in prefixes):
            return lesson
    return _FALLBACK_LESSON


#: ★I1 作用域推断：根据教训文本里的行为词判断它该作用在哪一屏。
#: 顺序敏感 —— 先问最具体的（战斗行为），再问路线，最后才是 general。
#: 这与 `heuristic_planner._LESSON_KEYWORD_MAP` 的 token 体系是**两套独立**的语义：
#: 这张表只用于**标注**（可观测性 / 度量），不参与降权决策，避免与 token 体系互相干扰。
_DOMAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("combat", ("格挡", "垫格挡", "输出", "攻击牌", "回合", "叠甲", "保命", "斩杀", "抢血")),
    ("map", ("路线", "绕开", "精英", "房间", "休息", "篝火", "商店")),
    ("reward", ("拿牌", "选牌", "奖励", "能力牌", "卡牌")),
    ("event", ("事件", "选项", "生命上限", "最大生命")),
)


def _infer_lesson_domain(text: str, declared: str = "") -> str:
    """推断一条教训的作用域（combat / map / reward / event / general）。"""
    if declared and declared != "general":
        return declared
    lowered = str(text or "")
    for domain, hints in _DOMAIN_HINTS:
        if any(hint in lowered for hint in hints):
            return domain
    return "general"



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
        """从磁盘读入最近记忆；**损坏时抢救式解析，绝不静默清空**。

        ★★t16 修复的生产缺陷（原实现）::

            try:
                data = json.load(f)
                ...
            except (OSError, ValueError):
                self._runs = []          # ← 静默丢弃全部记忆，只写一行 log

        实测线上 `catgirl_memory.json` 已损坏（完整 JSON 之后残留 22,964 字符），
        所以 `json.load` 抛 `ValueError` ⇒ `self._runs = []` ⇒ **load 得 0 局**。
        危险链是「load 得 0 局 → save 把 0 局写回」 ⇒ **坐实丢失全部 50 局**。

        ★修法（三分支，全部**不清空**）：
        1. 正常 `json.load` 成功 ⇒ 直接用。
        2. `json.load` 失败（尾随残片等）⇒ 用 `JSONDecoder().raw_decode()` 取
           **第一个完整对象**，抢救出其中的 `runs`，并**显式记告警日志**
           （含抢救出的局数与丢弃的残留字符数）。**绝不清空。**
        3. 连第一个完整对象都取不到（完全非 JSON / 空文件）⇒ 才回落到空列表，
           并**显式记告警**。
        另外：正常加载成功但检测到**尾随残留**时也告警（可观测，不静默）。

        向前兼容：文件不存在 / 空文件 / 完全非 JSON，均不抛异常（见 t16 D4）。
        """
        self._runs = []
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            self._log(f"catgirl_memory load failed (IO): {self._path}")
            return
        if not raw.strip():
            return

        # --- 分支 1：正常解析 ---
        try:
            data = json.loads(raw)
            self._adopt_runs(data)
            return
        except ValueError:
            pass

        # --- 分支 2：抢救式解析（取第一个完整对象）★不得清空 ---
        try:
            data, end = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            # --- 分支 3：连第一个对象都取不到 ⇒ 才回落空，且必须告警 ---
            self._log(
                f"catgirl_memory load: unparsable, starting empty: {self._path} "
                f"(bytes={len(raw)})"
            )
            return
        self._adopt_runs(data)
        trailing = len(raw) - end
        self._log(
            f"catgirl_memory load: RECOVERED partial file {self._path} "
            f"(salvaged_runs={len(self._runs)} trailing_residue_chars={trailing})"
        )

    def _adopt_runs(self, data: Any) -> None:
        """把解析出的文档里的 runs 收进来。"""
        records = data.get("runs") if isinstance(data, dict) else None
        if isinstance(records, list):
            self._runs = [r for r in records if isinstance(r, dict)]
            self._runs = self._runs[-_RUN_CAP:]

    def save(self) -> None:
        """把当前记忆写回磁盘（幂等；path 为 None 时静默跳过）。

        ★t16 加固（针对 D3 查明的「交错写」成因）：
        原实现用固定 tmp 名 `self._path + ".tmp"`。若有两个写入者（例如宿主
        reload 时旧/新 Store 并存），它们会**共用同一个 tmp 文件** ——
        一个截断重写、另一个仍按自己的偏移继续写，就会产出
        「完整前缀 + 中段残片」这种损坏（现场观测即此形态）。

        改为：
        * **唯一 tmp 名**（含 pid 与 uuid4）⇒ 两个写入者不再互相踩 tmp；
        * 写入后 `flush()` + `os.fsync()` ⇒ 数据落到磁盘再替换，
          避免断电/崩溃时 `os.replace` 生效但内容未落盘。
        """
        if not self._path:
            return
        tmp = "%s.%d.%s.tmp" % (self._path, os.getpid(), uuid.uuid4().hex[:8])
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            payload = {"runs": self._runs[:_RUN_CAP], "updated_at": time()}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except OSError:
            self._log(f"catgirl_memory save failed: {self._path}")
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def add_run(self, run_id: str, *, character: str, outcome: str, floor: int, act: int,
                decision_memory: list[dict[str, Any]], llm: Any = None,
                hp_report: dict[str, Any] | None = None,
                route: list[dict[str, Any]] | None = None,
                deck: list[dict[str, Any]] | None = None,
                extra_lessons: list[str] | None = None) -> dict[str, Any]:
        """总结一局并追加到记忆（幂等：同 run_id 的旧记录先移除再添新，避免终端屏重复触发累积）。

        ``llm`` 参数保留但**这里不再使用**：本方法必须是同步、确定、立刻落盘的（terminal 屏会反复
        出现，记录不能等模型）。局末的对照复盘由 service 事后经 ``attach_review`` 贴回同一条记录。

        旧代码在这里同步调用 ``llm.generate(...)`` —— 那是个 ``async`` 方法，拿到的是协程，
        随后 ``.splitlines()`` 直接抛错被 ``except`` 吞掉；而且还有 ``self._engine == "llm"`` 这道
        默认关闭的闸门。**那条 LLM 总结路径从来没有生效过**，一直是纯启发式。

        ``deck``（2026-10-19 新增）：死亡那一刻的**完整牌组快照**（mod 的
        `DeckCardPayload`，带 card_type / upgraded / energy_cost / dynamic_values /
        resolved_rules_text）。它是「六维缺口」教训的唯一数据来源 ——
        没有它就只能反思"死因"，有了它才能反思"**牌组缺哪一维**"。

        ``extra_lessons``（2026-10-19 新增）：由**调用方**（service）算好的额外教训。
        为什么由调用方给、而不是在这里算：六维量化要用
        `STS2HeuristicPlanner._deck_gap_vector`，而 planner 实例住在 service
        （`service.py:99`），memory store 拿不到它。
        ⚠️ 刻意**不**在这里 new 一个 planner：那会形成第二套判据来源，
           与"决策与复盘共用同一把尺"的设计冲突。
        """
        run = {
            "run_id": str(run_id or ""),
            "character": str(character or ""),
            "ended_at": time(),
            "outcome": str(outcome or ""),
            "floor": int(floor or 0),
            "act": int(act or 0),
            "lessons": [],
            "preferences": [],
            "hp": dict(hp_report or {}),
            # 逐房间的路线图：每个房间掉了多少血、拿到什么（供历史页展示与复盘参考）。
            "route": [dict(item) for item in (route or []) if isinstance(item, dict)],
            # ★ 局末牌组快照：供「六维缺口」反思量化牌组用。
            #   只留量化需要的字段，避免把整个 deck 塞进记忆文件（50 局 × 30 张太大）。
            "deck": [self._slim_deck_card(item) for item in (deck or []) if isinstance(item, dict)],
            # 本局的关键决策（截断保留），供历史页展示 + 局末复盘当上下文。
            "decisions": [
                {
                    "screen": str(item.get("screen") or ""),
                    "action_type": str(item.get("action_type") or ""),
                    "reason": str(item.get("decision_reason") or item.get("reason") or "")[:80],
                    "step_count": item.get("step_count"),
                }
                for item in (decision_memory or [])[-60:]
                if isinstance(item, dict)
            ],
        }
        run["extra_lessons"] = [
            str(text).strip()
            for text in (extra_lessons or [])
            if isinstance(text, (str, int)) and str(text).strip()
        ]
        summary = self.summarize_run(
            decision_memory=decision_memory,
            run_meta=run,
        )
        run["lessons"] = summary.get("lessons", [])
        run["preferences"] = summary.get("preferences", [])
        # ★I1：同时落盘结构化记录（新键）。老键 `lessons` 保留不动 ⇒ 老读者零影响。
        run["lesson_records"] = [
            self.build_lesson_record(str(text), source="heuristic")
            for text in run["lessons"]
            if isinstance(text, (str, int)) and str(text).strip()
        ]
        # 同 run_id 覆盖（去重），再追加
        self._runs = [r for r in self._runs if r.get("run_id") != run.get("run_id")]
        self._runs.append(run)
        self._runs = self._runs[-_RUN_CAP:]
        self.save()
        return dict(run)

    # ---- 总结 ----

    #: ★I1 结构化教训 schema 的字段与默认值。
    #: 设计目标：**向前兼容** —— 老记忆文件里 `lessons` 只是纯字符串列表，
    #: 读进来时按这里补默认值即可，**零迁移、零报错**（真实 705KB 文件 44 条记录已实测）。
    LESSON_RECORD_DEFAULTS: dict[str, Any] = {
        "text": "",           # 原始自由文本（保留原文，永不再生成）
        "domain": "general",  # 作用域：combat / map / reward / event / general
        "trigger": {},        # 触发条件（如 {"floor_min": 24}）
        "action": {},         # 建议动作（如 {"prefer": "block_first"}）
        "strength": 1.0,      # 强度 [0,1]，供后续加权
        "evidence": {},       # 证据（哪一局哪一步，取自 hp.heaviest / decisions）
        "confidence": 1.0,    # 置信度 [0,1]
        "hit_count": 0,       # 命中次数（由消费侧累加上报）
        "source": "heuristic",  # heuristic / llm
    }

    @classmethod
    def build_lesson_record(cls, text: str, **overrides: Any) -> dict[str, Any]:
        """把一条自由文本教训包装成结构化记录（缺字段给默认）。"""
        record = dict(cls.LESSON_RECORD_DEFAULTS)
        record["text"] = str(text or "").strip()
        for key, value in overrides.items():
            if key in cls.LESSON_RECORD_DEFAULTS:
                record[key] = value
        record["domain"] = _infer_lesson_domain(record["text"], str(record.get("domain") or ""))
        return record

    @classmethod
    def normalize_lesson_record(cls, item: Any) -> dict[str, Any]:
        """把「老格式（纯字符串）或新格式（dict）」统一成结构化记录。

        ★这是向前兼容的**唯一**入口：任何读记忆的地方都该走它，
        这样老文件（`lessons: ["..."]`）与新文件（`lesson_records: [{...}]`）都能用。
        """
        if isinstance(item, dict):
            record = dict(cls.LESSON_RECORD_DEFAULTS)
            for key in cls.LESSON_RECORD_DEFAULTS:
                if key in item:
                    record[key] = item[key]
            record["text"] = str(record.get("text") or "").strip()
            if not record["text"]:
                return {}
            record["domain"] = _infer_lesson_domain(record["text"], str(record.get("domain") or ""))
            return record
        text = str(item or "").strip() if isinstance(item, (str, int)) else ""
        if not text:
            return {}
        return cls.build_lesson_record(text)

    def lesson_records(self, runs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """把若干局里的教训读成结构化记录（老格式自动升级）。

        优先读 `lesson_records`（新键），同时并入 `lessons`（老键）——
        两者同时存在时**合并**，保证新写入的记录不会让老记录消失。
        """
        source_runs = self._runs if runs is None else runs
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for run in source_runs:
            if not isinstance(run, dict):
                continue
            structured = run.get("lesson_records") if isinstance(run.get("lesson_records"), list) else []
            legacy = run.get("lessons") if isinstance(run.get("lessons"), list) else []
            for item in [*structured, *legacy]:
                record = self.normalize_lesson_record(item)
                if not record:
                    continue
                if record["text"] in seen:
                    continue
                seen.add(record["text"])
                records.append(record)
        return records

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

    def attach_review(self, run_id: str, review: dict[str, Any]) -> dict[str, Any] | None:
        """把"玩家的选择 vs 当时的推荐"的对照（及事后补上的讲解）贴到那一局的记录上。

        只是记录：它**不**进 lessons，不参与下一局的任何决策。找不到对应 run_id 时返回 None。
        """
        if not isinstance(review, dict):
            return None
        for run in self._runs:
            if str(run.get("run_id") or "") != str(run_id or ""):
                continue
            run["review"] = dict(review)
            self.save()
            return dict(run)
        return None

    def summarize_recent(self, limit: int = 5) -> dict[str, list[str]]:
        """聚合最近 N 局的教训/偏好（去重），供注入 strategy_directives。

        ★**闸2（注入窗口）**：此前"最近 N 局"是按**局数**截断的，而真实记忆里连续 41 局
        的 lessons 全是同一条兜底模板句（`_heuristic_lessons` 的 defeat 兜底）。
        于是 `limit=5` 只能捞到那一条**抽不出 token** 的句子 —— 10 条真实 LLM 反思
        （能抽出 `skill`/`attack` 等 token）全部被锁在窗外。

        实测拐点（`.dsh-artifacts/reflection-metrics-baseline.json` → `runtime.gate2_window_sweep`）：
        `limit=5→1 条`、`10→1`、`20→7`、`44→12`。

        修法：**先按局数取窗口，再补足"有信息量"的教训**（默认开启，见 `include_older_lessons`）。
        这样既不改变 `limit` 的语义（调用方仍可说"看最近 5 局"），
        又不会让高频模板句把仅有的几条真教训挤掉。补足是**追加**，不重排、不去重以外的改写。
        """
        return self.summarize_recent_detailed(limit=limit)["summary"]

    #: 兜底模板句的**特征子串**。用于识别"这一条不携带任何可执行信息"。
    #: 必须是子串匹配（不是等值），因为历史记录里可能带标点差异。
    _FALLBACK_LESSON_MARKERS: tuple[str, ...] = ("输得不明不白",)

    @classmethod
    def _is_fallback_lesson(cls, text: str) -> bool:
        """这条教训是不是那条"没有信息量"的兜底模板句。"""
        return any(marker in str(text) for marker in cls._FALLBACK_LESSON_MARKERS)

    def summarize_recent_detailed(
        self,
        limit: int = 5,
        *,
        include_older_lessons: bool = True,
        max_older_lessons: int = 12,
    ) -> dict[str, Any]:
        """`summarize_recent` 的详细版：额外给出窗口诊断信息（供度量脚手架与日志用）。

        返回 ``{"summary": {...}, "window": {...}}``，其中 `summary` 与 `summarize_recent`
        的返回值**完全一致**（同样的键、同样的顺序语义）。

        Args:
            include_older_lessons: 是否用"窗口外的、非兜底的"教训补足窗口。
                关掉它可精确复现改动前的行为（t10 基线口径）。
        """
        window_runs = self._runs[-limit:] if limit > 0 else []
        lessons: list[str] = []
        preferences: list[str] = []
        for run in window_runs:
            lessons.extend(str(item) for item in run.get("lessons", []) if isinstance(item, (str, int)))
            preferences.extend(str(item) for item in run.get("preferences", []) if isinstance(item, (str, int)))
        lessons, preferences = self._dedup(lessons), self._dedup(preferences)

        window_lessons = list(lessons)
        added: list[str] = []
        if include_older_lessons:
            older_runs = self._runs[: len(self._runs) - len(window_runs)]
            for run in reversed(older_runs):  # 由新到旧
                if len(added) >= max_older_lessons:
                    break
                for item in run.get("lessons", []):
                    if not isinstance(item, (str, int)):
                        continue
                    text = str(item).strip()
                    if not text or self._is_fallback_lesson(text):
                        continue
                    if text in lessons or text in added:
                        continue
                    added.append(text)
                    lessons.append(text)
                    if len(added) >= max_older_lessons:
                        break

        return {
            "summary": {"lessons": lessons, "preferences": preferences},
            "window": {
                "limit": limit,
                "runs_in_window": len(window_runs),
                "window_lessons": window_lessons,
                "window_lesson_count": len(window_lessons),
                "window_all_fallback": bool(window_lessons) and all(
                    self._is_fallback_lesson(item) for item in window_lessons
                ),
                "older_lessons_added": added,
                "older_lessons_added_count": len(added),
                "total_lessons": len(lessons),
            },
        }

    # ---- 启发式规则 ----

    #: ★ 牌组快照里**保留**的字段 —— 恰好是 `_deck_gap_vector` 量化六维要读的那几个。
    #:
    #: 为什么不整张存：50 局 × 30 张 × (rules_text + dynamic_values + upgrade) 能把
    #: `catgirl_memory.json` 撑到几十 MB，而历史页/策略注入根本不看那些。
    #:
    #: ⚠️ `resolved_rules_text` **必须留** —— 六维量化里的
    #:    抽牌/群伤/状态判定全靠它做关键词匹配（`_DECK_DRAW_TOKENS` 等）。
    #:    同时 `_card_damage` / `_card_block` 在 `dynamic_values` 缺失时
    #:    **也要**回落读它（`BUG-card-damage-unit-confusion` 修完之后是按单位正则匹配）。
    _DECK_SNAPSHOT_FIELDS: tuple[str, ...] = (
        "card_id", "name", "card_type", "type",
        "upgraded", "energy_cost", "costs_x",
        "dynamic_values",
        "resolved_rules_text", "rules_text",
        "damage", "block",
    )

    @classmethod
    def _slim_deck_card(cls, card: dict[str, Any]) -> dict[str, Any]:
        """把一张 deck 卡压成"六维量化够用"的最小字段集。

        保留 `dynamic_values` 里的 `name`/`current_value` 两项即可 ——
        `_card_damage` 只按 `name == "damage"` 找，`_card_block` 只按 `name == "block"` 找。
        """
        slim: dict[str, Any] = {}
        for key in cls._DECK_SNAPSHOT_FIELDS:
            if key not in card:
                continue
            value = card[key]
            if key == "dynamic_values" and isinstance(value, list):
                value = [
                    {
                        "name": str(item.get("name") or ""),
                        "current_value": item.get("current_value"),
                    }
                    for item in value
                    if isinstance(item, dict)
                ]
            elif isinstance(value, str):
                value = value[:400]
            slim[key] = value
        return slim

    #: ★R5：真实 `decisions[].reason` 是**全英文标签**（实测 660 条 / 去重 25 种，
    #: 中文关键词命中 **0/660**）。这里列出"代表一次值得复盘的失误"的英文标签。
    #: 只收语义明确的；宁可漏判，也不要因为模糊匹配产生噪声教训。
    _MISTAKE_REASON_TOKENS: tuple[str, ...] = (
        # 战斗：被 solver 判定只能结束回合，通常意味着这回合打不出更优解
        "mod_solver_end_turn",
        # 战斗：兜底/默认路径，说明没有找到任何"有把握"的序列
        "combat_preference_or_default",
        # 路线/选牌：偏好与默认混用，说明当时没有明确的更优选项
        "map_preference_or_default",
        "reward_",
        "shop_",
    )

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
            elif _is_mistake_reason(why):
                # ★R5：真实数据是英文标签，中文关键词**永远命中不了**（实测 0/660）。
                # 补上英文分支后，这些局才第一次能产出"指向具体行为"的教训。
                lessons.append(_lesson_for_reason(why))

        # ★★ 死亡归因：从 `route[]` 的损血轨迹推出**具体死因**，产出指向维度的教训。
        #
        # 为什么必须加（实测）：本函数此前**只读 `decision_memory` 的 reason 文本**，
        # 从不读牌组、不读分层损血、不读敌人类型 ⇒ 50 局真实记忆里三条死亡教训
        # **全是同一句兜底句**（"这局输得不明不白…"），抽不出任何 token，
        # 于是复盘对下一局**毫无影响**。
        #
        # 玩家的口径：「教训是需要反思整个链路的……遇到群怪就死就反思有没有拿 aoe 卡，
        # 遇到 boss 和精英就死需要反思选卡的数值和强化卡选择」。
        # ⇒ 死因（vantom/精英/群怪/被磨死）**决定该反思哪一维**。
        #
        # 实测效果：50 局里 **49 局**能产出具体死因教训（此前 3/50 全是兜底句）。
        try:
            from .death_attribution import attribution_to_lessons, build_death_attribution

            attribution = build_death_attribution(run_meta)
            attribution["final_hp"] = run_meta.get("final_hp") if isinstance(run_meta, dict) else None
            lessons.extend(attribution_to_lessons(attribution))
        except Exception:
            # 归因失败**不得**影响原有链路 —— 宁可少一条教训，也不要让复盘炸掉。
            pass

        if saw_low_hp:
            lessons.append("血量过低时别贪输出，优先保命叠甲")

        # ★★ 六维缺口教训：由 service 用 `_deck_gap_vector` 量化本局牌组算出
        # （`deck_gap_reflection.build_gap_lessons`），经 `extra_lessons` 传进来。
        #
        # 为什么放在最后：这些是**最有信息量**的一条（指向具体维度 + 具体缺多少），
        # 而上面的都是"迹象类"教训。放在后面能让 `lessons[:N]` 这类截断
        # 优先保留迹象类（更稳），而完整列表里两者都在。
        #
        # ⚠️ 它们**不参与** `if not lessons` 的兜底判定 —— 那是"这局没抓到任何教训"的意思，
        #    六维缺口算不上"抓到了迹象"，不该阻止兜底句。
        lessons.extend(
            str(text)
            for text in (run_meta.get("extra_lessons") or [])
            if isinstance(text, (str, int)) and str(text).strip()
        )

        # 极端结局兜底：如果整局叠到低血但没抓到教训
        if not lessons and str(run_meta.get("outcome") or "") == "defeat":
            lessons.append(_FALLBACK_LESSON)
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
