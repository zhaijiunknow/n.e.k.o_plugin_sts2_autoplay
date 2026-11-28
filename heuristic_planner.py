from __future__ import annotations

import random
import time
from typing import Any

from .planner_choices import PlannerChoicesMixin
from .planner_combat import PlannerCombatMixin
from .planner_common import (
    _ARCHETYPE_DRAW_RE as _ARCHETYPE_DRAW_RE,
)
from .planner_common import (
    _DECK_AOE_TOKENS as _DECK_AOE_TOKENS,
)
from .planner_common import (
    _DECK_ATTACK_TYPE_TOKENS as _DECK_ATTACK_TYPE_TOKENS,
)
from .planner_common import (
    _DECK_BASIC_CARD_TOKENS as _DECK_BASIC_CARD_TOKENS,
)
from .planner_common import (
    _DECK_CURSE_TYPE_TOKENS as _DECK_CURSE_TYPE_TOKENS,
)
from .planner_common import (
    _DECK_DRAW_TOKENS as _DECK_DRAW_TOKENS,
)
from .planner_common import (
    _DECK_STATUS_TOKENS as _DECK_STATUS_TOKENS,
)
from .planner_common import (
    _MAP_NODE_TYPE_ALIASES as _MAP_NODE_TYPE_ALIASES,
)
from .planner_common import (
    _MAP_PICK_CN_DIGITS as _MAP_PICK_CN_DIGITS,
)
from .planner_common import (
    _MAP_PICK_FIRST_TOKENS as _MAP_PICK_FIRST_TOKENS,
)
from .planner_common import (
    _MAP_PICK_LAST_TOKENS as _MAP_PICK_LAST_TOKENS,
)
from .planner_common import (
    _MAP_PICK_MIDDLE_TOKENS as _MAP_PICK_MIDDLE_TOKENS,
)
from .planner_common import (
    _POTION_AOE_TOKENS as _POTION_AOE_TOKENS,
)
from .planner_common import (
    _POTION_BURST_TOKENS as _POTION_BURST_TOKENS,
)
from .planner_common import (
    _POTION_CATEGORY_LABELS as _POTION_CATEGORY_LABELS,
)
from .planner_common import (
    _POTION_CATEGORY_PRIORITY as _POTION_CATEGORY_PRIORITY,
)
from .planner_common import (
    _POTION_CLEANSE_TOKENS as _POTION_CLEANSE_TOKENS,
)
from .planner_common import (
    _POTION_DEFENSE_TOKENS as _POTION_DEFENSE_TOKENS,
)
from .planner_common import (
    _POTION_DRAW_TOKENS as _POTION_DRAW_TOKENS,
)
from .planner_common import (
    _POTION_HEAL_TOKENS as _POTION_HEAL_TOKENS,
)
from .planner_common import (
    _RELIC_COMBAT_START_TOKENS as _RELIC_COMBAT_START_TOKENS,
)
from .planner_common import (
    _RELIC_ECON_TOKENS as _RELIC_ECON_TOKENS,
)
from .planner_common import (
    _RELIC_EFFECT_DIM_TOKENS as _RELIC_EFFECT_DIM_TOKENS,
)
from .planner_common import (
    _RELIC_HARSH_TOKENS as _RELIC_HARSH_TOKENS,
)
from .planner_common import (
    _RELIC_STRONG_TOKENS as _RELIC_STRONG_TOKENS,
)
from .planner_common import (
    _RELIC_SURVIVAL_TOKENS as _RELIC_SURVIVAL_TOKENS,
)
from .planner_common import (
    _REST_HEAL_TOKENS as _REST_HEAL_TOKENS,
)
from .planner_common import (
    _REST_SMITH_TOKENS as _REST_SMITH_TOKENS,
)
from .planner_common import (
    COMBAT_POTION_ELITE_THRESHOLD_MULTIPLIER as COMBAT_POTION_ELITE_THRESHOLD_MULTIPLIER,
)
from .planner_common import (
    COMBAT_POTION_HP_LOSS_THRESHOLD as COMBAT_POTION_HP_LOSS_THRESHOLD,
)
from .planner_common import (
    COMBAT_POTION_LETHAL_HEAL_SCORE as COMBAT_POTION_LETHAL_HEAL_SCORE,
)
from .planner_common import (
    COMBAT_POTION_LOW_HP_HEAL_SCORE as COMBAT_POTION_LOW_HP_HEAL_SCORE,
)
from .planner_common import (
    COMBAT_POTION_LOW_HP_RATIO as COMBAT_POTION_LOW_HP_RATIO,
)
from .planner_common import (
    COMBAT_POTION_SCORES as COMBAT_POTION_SCORES,
)
from .planner_common import (
    COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER as COMBAT_POTION_TRASH_THRESHOLD_MULTIPLIER,
)
from .planner_common import (
    MAP_POLICIES as MAP_POLICIES,
)
from .planner_common import (
    MAP_POLICY_AUTO as MAP_POLICY_AUTO,
)
from .planner_common import (
    MAP_POLICY_FOLLOW as MAP_POLICY_FOLLOW,
)
from .planner_common import (
    MAP_POLICY_WAIT as MAP_POLICY_WAIT,
)
from .planner_common import (
    REST_HEAL_HP_RATIO as REST_HEAL_HP_RATIO,
)
from .planner_common import (
    ROUTE_BOSS_PREP_GOLD as ROUTE_BOSS_PREP_GOLD,
)
from .planner_common import (
    ROUTE_BOSS_PREP_MULTIPLIER as ROUTE_BOSS_PREP_MULTIPLIER,
)
from .planner_common import (
    ROUTE_BOSS_PREP_ROWS as ROUTE_BOSS_PREP_ROWS,
)
from .planner_common import (
    ROUTE_DISCOUNT_PER_ROW as ROUTE_DISCOUNT_PER_ROW,
)
from .planner_common import (
    ROUTE_ELITE_RELIEF_MAX as ROUTE_ELITE_RELIEF_MAX,
)
from .planner_common import (
    ROUTE_LOOKAHEAD_ROWS_FALLBACK as ROUTE_LOOKAHEAD_ROWS_FALLBACK,
)
from .planner_common import (
    ROUTE_REPEAT_DECAY as ROUTE_REPEAT_DECAY,
)
from .planner_common import (
    SAFE_MAP_HP_RATIO_BELOW as SAFE_MAP_HP_RATIO_BELOW,
)
from .planner_common import (
    SAFE_MAP_LOW_HP_WEIGHTS as SAFE_MAP_LOW_HP_WEIGHTS,
)
from .planner_common import (
    SAFE_MAP_WEIGHTS as SAFE_MAP_WEIGHTS,
)
from .planner_common import (
    SHOP_AOE_TARGET_UNITS as SHOP_AOE_TARGET_UNITS,
)
from .planner_common import (
    SHOP_CARD_FIT_SCALE as SHOP_CARD_FIT_SCALE,
)
from .planner_common import (
    SHOP_CARDS_PER_TURN as SHOP_CARDS_PER_TURN,
)
from .planner_common import (
    SHOP_COST_PENALTY_HALF_BONUS as SHOP_COST_PENALTY_HALF_BONUS,
)
from .planner_common import (
    SHOP_COST_PENALTY_SCALE as SHOP_COST_PENALTY_SCALE,
)
from .planner_common import (
    SHOP_COVERAGE_MATURE_RATIO as SHOP_COVERAGE_MATURE_RATIO,
)
from .planner_common import (
    SHOP_DIM_AOE as SHOP_DIM_AOE,
)
from .planner_common import (
    SHOP_DIM_DEFENSE as SHOP_DIM_DEFENSE,
)
from .planner_common import (
    SHOP_DIM_DRAW as SHOP_DIM_DRAW,
)
from .planner_common import (
    SHOP_DIM_ENERGY as SHOP_DIM_ENERGY,
)
from .planner_common import (
    SHOP_DIM_LABELS as SHOP_DIM_LABELS,
)
from .planner_common import (
    SHOP_DIM_OUTPUT as SHOP_DIM_OUTPUT,
)
from .planner_common import (
    SHOP_DIM_QUALITY as SHOP_DIM_QUALITY,
)
from .planner_common import (
    SHOP_DIMENSIONS as SHOP_DIMENSIONS,
)
from .planner_common import (
    SHOP_DRAW_CARD_CREDIT as SHOP_DRAW_CARD_CREDIT,
)
from .planner_common import (
    SHOP_DRAW_SIZE_BASE as SHOP_DRAW_SIZE_BASE,
)
from .planner_common import (
    SHOP_DRAW_SIZE_SPAN as SHOP_DRAW_SIZE_SPAN,
)
from .planner_common import (
    SHOP_ELITE_POTION_BOOST as SHOP_ELITE_POTION_BOOST,
)
from .planner_common import (
    SHOP_ELITE_POTION_NEEDS as SHOP_ELITE_POTION_NEEDS,
)
from .planner_common import (
    SHOP_ELITE_THRESHOLD_DELTA as SHOP_ELITE_THRESHOLD_DELTA,
)
from .planner_common import (
    SHOP_GOLD_FLOOR as SHOP_GOLD_FLOOR,
)
from .planner_common import (
    SHOP_GOLD_POOR as SHOP_GOLD_POOR,
)
from .planner_common import (
    SHOP_GOLD_RICH as SHOP_GOLD_RICH,
)
from .planner_common import (
    SHOP_MATURE_THRESHOLD_DELTA as SHOP_MATURE_THRESHOLD_DELTA,
)
from .planner_common import (
    SHOP_POTION_BASELINE_FIT as SHOP_POTION_BASELINE_FIT,
)
from .planner_common import (
    SHOP_POTION_COVERED_FIT as SHOP_POTION_COVERED_FIT,
)
from .planner_common import (
    SHOP_POTION_NEEDED_FIT as SHOP_POTION_NEEDED_FIT,
)
from .planner_common import (
    SHOP_POTION_PRICE_FALLBACK as SHOP_POTION_PRICE_FALLBACK,
)
from .planner_common import (
    SHOP_QUALITY_BASIC_TARGET as SHOP_QUALITY_BASIC_TARGET,
)
from .planner_common import (
    SHOP_QUALITY_CURSE_TARGET as SHOP_QUALITY_CURSE_TARGET,
)
from .planner_common import (
    SHOP_RELIC_CONDITION_PENALTY as SHOP_RELIC_CONDITION_PENALTY,
)
from .planner_common import (
    SHOP_RELIC_KEYWORD_BONUS as SHOP_RELIC_KEYWORD_BONUS,
)
from .planner_common import (
    SHOP_RELIC_KEYWORD_BONUS_CAP as SHOP_RELIC_KEYWORD_BONUS_CAP,
)
from .planner_common import (
    SHOP_RELIC_RARITY_BONUS as SHOP_RELIC_RARITY_BONUS,
)
from .planner_common import (
    SHOP_SCORE_THRESHOLD as SHOP_SCORE_THRESHOLD,
)
from .planner_common import (
    SHOP_STATUS_CARD_CREDIT as SHOP_STATUS_CARD_CREDIT,
)
from .planner_common import (
    SHOP_TARGET_BLOCK_BASE as SHOP_TARGET_BLOCK_BASE,
)
from .planner_common import (
    SHOP_TARGET_BLOCK_PER_FLOOR as SHOP_TARGET_BLOCK_PER_FLOOR,
)
from .planner_common import (
    SHOP_TARGET_DAMAGE_BASE as SHOP_TARGET_DAMAGE_BASE,
)
from .planner_common import (
    SHOP_TARGET_DAMAGE_PER_FLOOR as SHOP_TARGET_DAMAGE_PER_FLOOR,
)
from .planner_common import (
    SHOP_THRESHOLD_MAX as SHOP_THRESHOLD_MAX,
)
from .planner_common import (
    SHOP_THRESHOLD_MIN as SHOP_THRESHOLD_MIN,
)
from .planner_common import (
    SHOP_THRESHOLD_POOR_DELTA as SHOP_THRESHOLD_POOR_DELTA,
)
from .planner_common import (
    SHOP_THRESHOLD_RICH_DELTA as SHOP_THRESHOLD_RICH_DELTA,
)
from .planner_common import (
    SHOP_UNKNOWN_RELIC_GAP_RATIO as SHOP_UNKNOWN_RELIC_GAP_RATIO,
)
from .planner_common import (
    _first_line_step as _first_line_step,
)
from .planner_common import (
    _is_basic_card_like as _is_basic_card_like,
)
from .planner_common import (
    _line_step_at as _line_step_at,
)
from .planner_common import (
    normalize_map_policy as normalize_map_policy,
)
from .planner_common import (
    parse_map_pick as parse_map_pick,
)
from .planner_common import (
    validate_map_vote_wait_seconds as validate_map_vote_wait_seconds,
)
from .planner_interface import PlannedOperation
from .planner_lessons import PlannerLessonsMixin
from .planner_map import PlannerMapMixin
from .planner_shop import PlannerShopMixin


class STS2HeuristicPlanner(PlannerCombatMixin, PlannerChoicesMixin, PlannerMapMixin, PlannerShopMixin, PlannerLessonsMixin):
    #: 跨局教训 → 可匹配选项的语义关键词。
    #:
    #: 复盘的产物是**整句中文**（如「下次血量低于20的时候别头铁硬刚，先垫格挡喝回血药再输出」），
    #: 整句拿去匹配选项描述必然匹配不上，所以把它压成下面这小组**语义 token**，再由
    #: ``_indices_worth_penalizing`` 落到具体屏（目前只落地「路线」与「选牌」两屏 —— 匹配不准的
    #: 屏宁可不动，避免把复盘变成噪声）。
    #:
    #: 键是 token（同时也是地图 `node.type` / 卡牌 `card_type` 的小写值），值是该 token 的
    #: 中文触发词。新增玩法时在这里加一行即可，不要在 `_lesson_tokens` 里堆 if。
    _LESSON_KEYWORD_MAP: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("elite", ("精英",)),
        ("monster", ("小怪", "打怪", "普通敌人")),
        ("rest", ("休息", "睡觉", "回血点", "篝火")),
        ("shop", ("商店", "买东西", "攒钱", "金币")),
        ("treasure", ("宝箱", "宝藏", "开箱")),
        ("curse", ("诅咒", "别拿咒")),
        # ★生命上限是**永久**损失（`max_hp` 不可恢复），用户报告的核心痛点：
        # 「有一些事件是可以消耗生命值上限的并且可以重复选择，这个时候脚本会一直选择消耗，
        #   直接把生命值变成了1」。没有这一条时，"别再扣生命上限"这类复盘**抽不出任何 token**
        # ⇒ 教训完全无法生效。
        #
        # ⚠️ 为什么不需要（也没有）"只在事件屏生效"的过滤：**结构上不可能泄漏**。
        # 实测确认各屏的匹配口径：
        #   * 路线屏 —— 只把节点 `type` 经 `_map_node_weight_key` 归一后与 token **等值**比较，
        #     取值域是 `rest`/`shop`/`elite`/... ，永远不等于 `max_hp`；
        #   * 选牌屏 —— 比较 `card_type`/`rarity` 与 token 等值，取值域是
        #     `attack`/`skill`/`power`/`curse`/... ，同样不可能等于 `max_hp`；
        #   * 其余屏 —— `_indices_worth_penalizing` 没有分支。
        # 我一度加过一个 `_EVENT_ONLY_TOKENS` 过滤，但**变异测试证明它不可观测**
        # （删掉它全部测试照样通过）⇒ 那是"无法被测试保护的死代码"，故删除。
        # 若将来给路线/选牌屏加上"按文案模糊匹配"的能力，才需要重新引入过滤。
        #
        # ⚠️ **刻意不加**普通掉血 token（`hp`）：实测真实反思「先垫格挡喝回血药再输出」
        # 含「回血」二字，会给纯**战斗**建议抽出 `hp`，从而破坏
        # `test_real_lesson_demotes_matching_node` 钉住的"战斗建议不得改动路线"契约。
        # 扣上限这一条已经完整覆盖用户报告，收益明确、风险为零。
        ("max_hp", ("生命上限", "最大生命", "扣上限", "命上限")),
        ("skill", ("格挡", "垫格挡", "防御", "保命", "保血")),
        ("attack", ("输出", "伤害", "抢血", "斩杀")),
        ("power", ("能力牌", "攒力量")),
        # ★★ 六维缺口的「群伤」与「牌组质量」（2026-10-19 补）。
        #
        # 为什么必须补：`deck_gap_reflection` 会产出「这局几乎没有群伤牌，
        # 下局我想早点拿 AOE」这类教训，但**关键词表里没有对应项** ⇒
        # `_lesson_tokens` 抽不出任何 token ⇒ 教训**完全无法生效**。
        # 实测（补之前）：死因 aoe 的六维教训只抽出 `attack`/`skill`，
        # AOE 那一维的信号**整个丢失** —— 这正是"说了等于没说"的老毛病。
        ("aoe", ("群伤", "群攻", "群怪", "多敌")),
        # ── 牌组质量 ──────────────────────────────────────────────────────
        # ⚠️ **刻意不收「升级」二字**：实测它会**误伤路线类教训** ——
        #    `"上次在篝火休息浪费了升级机会，以后别选休息"` 讲的是**休息点**，
        #    但含「升级」⇒ 会额外抽出 `quality` ⇒ 破坏
        #    `test_route_lesson_changes_route_order` 钉住的"路线教训只影响路线"契约。
        #    这是我自己补 token 时踩的坑（测试立刻抓到）。
        # ⇒ 只收**指向牌**的搭配词，不收裸「升级」。质量教训的模板用「升级关键牌」
        #    「删牌」，这里的 `关键牌`/`升级关键牌` 都能命中。
        ("quality", ("升级关键牌", "精简牌组", "删牌", "废牌", "基础牌")),
    )

    def __init__(self, logger: Any | None = None) -> None:
        self._logger = logger
        # ★ 战斗决策留痕的计数（**每场战斗**上限，不是每局/每实例）。见 _combat_log / _COMBAT_LOG_MAX。
        #   t39：原注释写「每局限流」，而实现是「每实例一次性累计」—— 两者不一致，
        #   且计数**永无重置点** ⇒ 打满上限后本实例永久静默。现改为随**战斗身份**重置。
        self._combat_log_count = 0
        self._combat_log_last_key = ""
        self._combat_log_last_at = 0.0
        # ★ t39：战斗身份 + 封顶告警只报一次的标志。
        #   身份 = (屏幕|层|是否战斗) 三元的拼装串，沿用 service.py:3217 的公式形态。
        #   ★ 为什么必须由身份驱动而不是时间驱动：时间驱动的重置会让**长战斗**
        #     仍然被封顶，而长战斗恰恰是最需要留痕的那种局面（boss 战动辄几十回合）。
        self._combat_log_battle_identity = ""
        self._combat_log_cap_warned = False
        # ★ 战斗纯求解器的**死锁保险**状态（见 _combat_solver_only_outcome）。
        #   语义：同一局面指纹下，求解器**看着最新局面**仍连续给不出可执行动作的次数。
        #   指纹变化即清零（新局面 = 旧计数无意义）。
        self._solver_deadlock_signature = ""
        self._solver_deadlock_count = 0
        # ★★ D1：与死锁计数**必须分开**的第二个计数器（见 _combat_solver_only_outcome）。
        #   语义：同一局面指纹下，「计划过期（建议的牌已不在手）」连续发生的次数。
        #   ★ 为什么不只是"加个 if 直接 return None"：反复过期本身也是异常
        #     （上游 SolverController.cs:175-177 把 DeploymentDrift 计入 UnexpectedReplanCount，
        #      当作"计划失效"的缺陷信号），若完全不计上界，一旦某局面每 tick 都过期
        #      就会**永久重取、卡死整局**。故给上界：达 _SOLVER_STALE_RECLAIM_LIMIT 次
        #      即**转入死锁计数**，最迟第 LIMIT+RETRIES 次仍会发 end_turn 脱离。
        self._solver_stale_reclaim_count = 0
        # ★ t13（D3）：非玩家行动窗口的**独立**计数（见 `_SOLVER_OUT_OF_WINDOW_LIMIT`）。
        #   与 `_solver_deadlock_count` 严格分开：窗口没开时不是"求解器无解"，
        #   把它计进 N/3 会让 N 越过阈值（实机 5/3、7/3）且永远等不到兜底。
        self._solver_out_of_window_count = 0
        # ══════════════════════════════════════════════════════════════════════════
        # ★★★ t45：**准入闸门**的记账（见 `_combat_solver_gate_blocked`）
        # ══════════════════════════════════════════════════════════════════════════
        #   三个计数**互不掩盖**（t37 契约第 3 条的同一精神：任一 tick 结束后，
        #   都要能从日志读出「闸门放行了几次 / 等了几次 / 判了几次真无解」）：
        #     allowed_count      —— 过闸门（= 本 tick 真的可能发动作）
        #     wait_count         —— `waiting_solver`：**不计数死锁、不发任何动作**
        #     cannot_act_count   —— `solver_cannot_act`：真无解，**走既有上界**
        #   ★ 与 `_solver_stale_reclaim_count` 的关系：后者是 t23 遗留的"事后补救"计数，
        #     只在闸门放行后、执行期才失败时仍可能 +1；`waiting_solver` 路径
        #     **绝不**动它 —— 这正是「上界不得被等待消耗掉」的落地方式。
        self._solver_gate_allowed_count = 0
        self._solver_gate_wait_count = 0
        self._solver_gate_cannot_act_count = 0
        # 最近一次闸门结论（供 service 侧观测与测试断言）。
        self._solver_gate_last: dict[str, Any] = {}
        # 等待态日志的限流状态：`为什么在等` 的键变了就立刻打一行；
        # 同因连续等待则每 _SOLVER_GATE_LOG_EVERY 次打一行（行内带**累计**计数，
        # 于是"被闸门拦下的 tick 数"可以逐字从日志读出来，不靠估算）。
        self._solver_gate_log_key = ""
        self._solver_gate_blocked_since_log = 0
        # 上一次闸门结论（用于"恢复放行"只在**跃迁**时留痕，避免每 tick 重复一行）。
        self._solver_gate_last_verdict = ""
        # 地图投票策略，由 service.startup 用插件配置覆盖：
        #   wait（默认）——多人局里彻底等待：不投票、也不自动跟票，直到玩家通过对 NEKO 说话下令
        #                 （见 sts2_set_route）。单机局没有队友可等，仍按自己的偏好选，否则会卡死。
        #   follow     ——玩家已投票就镜像跟随；无人投票满 map_vote_wait_seconds 后自己选。
        #   auto       ——不等，立即按自己的偏好选。
        self._map_policy = MAP_POLICY_WAIT
        self._map_vote_wait_seconds = 20.0
        self._map_wait_key = ""
        self._map_wait_started_at = 0.0
        # 选牌屏的推进游标：签名 + 这一屏已按过的 option_index 集合。同一屏上永远不重复按同一张卡，
        # 否则需要选 2 张的界面会一直重按第 0 张、max_select 永远到不了（"选两张卡卡住"）。
        # 注意 _selection_pressed 只由**真的按键**推进（note_selection_option_pressed），
        # 不由规划推进——plan() 每 tick 都跑，用规划推进会把候选白白耗光并导致永久卡屏。
        self._selection_cursor_key = ""
        self._selection_pressed: set[int] = set()
        # 同一屏连续"候选按遍仍返回 None"的次数，达到 _SELECTION_EXHAUSTED_RETRIES 就重置光标重按，
        # 保证这一屏永远不会陷入"再也规划不出动作"的死锁。
        self._selection_exhausted = 0
        # ★事件屏"同一选项选过几次"的记忆（缺陷 A3）。
        # 与 _selection_pressed 同范式：以**屏签名**为键，换屏即重置；
        # 只由**真的派发成功**推进（note_event_option_chosen），不在 plan() 里累加。
        # 作用：可重复选择的事件（选项选完仍在）不会把同一项选到死。
        self._event_memory_key = ""
        self._event_choice_counts: dict[int, int] = {}
        # 商店"这次进店看过货了没"：货架开过一次、又关掉之后**不能再开**。
        # mod 的 open_shop_inventory 是**开关**，而开关两态的 screen 都叫 SHOP，
        # 只看 open_shop_inventory 在不在 available_actions 里就会开->关->开无限来回（玩家看到的死循环）。
        self._shop_visit_key = ""
        self._shop_viewed = False
        # 商店统一评分的日志去重键：plan() 每 tick 都跑一次（约 1 次/秒），不去重就是刷屏。
        # 键里带"最高分/门槛/决定"，所以调参后参数一变就会重新记一行。
        self._shop_log_key = ""
        # 最近一次路线决定留下的"前方有没有精英"：**商店界面拿不到地图**（mod 的 map payload 只在
        # 地图屏下发，BuildMapPayload 一离开地图就返回 null），所以只能在选路那一刻把结论记下来，
        # 进店后照着用（规格「路线/精英信息：evaluate_map_route 那一套已经在算精英压力，复用它」）。
        self._route_elite_ahead = False
        self._last_route_node_type = ""
        # 最近一次记录过的地图路线评分（同一局面只记一次，防止每 tick 刷屏）。
        self._last_map_route_log_key = ""
        self._last_reward_route_log_key = ""
        #: ★ 最近一次选卡评估的留痕（供 `_log_reward_route` 输出，便于线上核对）
        self._last_reward_scored: list[tuple[int, float]] = []
        self._last_reward_archetype_tags: list[str] = []
        self._last_reward_bias: set[str] = set()
        self._last_reward_lesson_signals: dict[str, int] = {}
        # 最近一次记录过的事件屏评分（同上，事件屏此前完全没有决策日志）。
        self._last_event_route_log_key = ""
        # 最近一次记录过的变换屏评分（同上，变换屏此前同样是决策日志盲区）。
        self._last_transform_route_log_key = ""
        # 奖励领取游标：当前奖励清单的签名 + 已经**真的领过**的下标。
        # 药水栏满时"领药水奖励"发不下去，而 mod 照样回 completed —— 不记账就会无限重领同一个。
        self._reward_claim_key = ""
        self._reward_claim_tried: set[int] = set()
        # ★★ D11（t28）：本屏**已确认不可选**的事件选项下标（被游戏锁定 / 被拒）。
        #   ★ 与 `_reward_claim_*` 完全同构：**按屏键隔离**，换屏自动清空，不跨屏累积。
        #   实机缺陷（2026-10-20，ENDLESS_CONVEYOR）：options[0] `is_locked:true`（金币18不足），
        #   插件却给它 +10 分、给未锁的 options[1] 0 分 ⇒ 每轮选被锁项 ⇒ 99 次被拒 ⇒ 永久卡死。
        self._event_locked_set_key_cached = ""
        self._event_locked_set_cached: set[int] = set()
        # 选人界面：「随机」只按一次（按键由执行推动），按过就转入开局。
        self._character_pick_key = ""
        self._character_pick_done = False
        self._character_choice_log_key = ""
        self._rest_choice_log_key = ""
        # 同类的"别重复按同一个按钮"状态：bundle 选过没、capstone 扫到第几个。
        self._bundle_signature = ""
        self._bundle_chosen = False
        self._capstone_signature = ""
        self._capstone_last_option = -1

    _MAX_CAPSTONE_SWEEP = 8
    # 选牌屏：候选按遍后允许返回 None 的次数，超过就重置光标重按（防死锁，见 _next_selection_option）。
    _SELECTION_EXHAUSTED_RETRIES = 2

    def configure_livestream(self, controller: Any) -> None:
        self._livestream = controller

    def configure_preferred_characters(self, provider: Any) -> None:
        """选人界面只在这些角色里选。`provider()` 返回角色 id 列表（策略网络已下载模型的角色）；
        返回空表 = 照旧用游戏自带的「随机」。每次进选人界面现查，所以新下载的模型不用重启就生效。
        """
        self._preferred_characters = provider

    def _preferred_character_index(self, summary_context: dict[str, Any]) -> int | None:
        """这次选人要选的角色的下标：在「有模型且没锁」的角色里随机定一个，定了就不变，直到离开选人界面。"""
        provider = getattr(self, "_preferred_characters", None)
        wanted = {str(c).upper() for c in (provider() if callable(provider) else [])}
        indices: dict[str, int] = {}
        for character in self._character_list(summary_context):
            character_id = str(character.get("character_id") or "").upper()
            index = self._safe_int(character.get("index"))
            if character_id in wanted and index is not None and not bool(character.get("is_locked")):
                indices[character_id] = index
        if not indices:
            self._character_pick_target = ""
            return None
        if getattr(self, "_character_pick_target", "") not in indices:
            self._character_pick_target = getattr(self, "_character_rng", random).choice(sorted(indices))
        return indices[self._character_pick_target]

    def _preferred_character_selected(self, summary_context: dict[str, Any]) -> bool:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        target = getattr(self, "_character_pick_target", "")
        return bool(target) and str(payload.get("selected_character_id") or "").upper() == target

    def configure_map_policy(self, *, policy: str, vote_wait_seconds: float) -> None:
        self._map_policy = normalize_map_policy(policy)
        self._map_vote_wait_seconds = validate_map_vote_wait_seconds(vote_wait_seconds)
        self._map_wait_key = ""
        self._map_wait_started_at = 0.0

    def _log_reward_route(
        self,
        scored: list[tuple[int, float]],
        chosen: int | None,
        *,
        skipped: bool,
        archetype_tags: list[str] | None = None,
        bias: set[str] | None = None,
        lesson_signals: dict[str, int] | None = None,
    ) -> None:
        """把选卡/跳牌的评估结果用 info 级记一次。

        ★ 为什么必须单独写（与 `_log_map_route` 同因）：
        `_debug()` 在本文件里是**空函数**（`L456-457 return`），
        所有 `[sts2_reward_*]` / `[sts2_archetype]` 调试行**其实都没输出**。
        于是线上完全无法核对"这次到底为什么选/跳这张牌"——
        而"选卡质量"正是用户点名的核心（诅咒卡污染、垃圾卡进卡组）。
        选路已有 `_log_map_route` 留痕，选卡同样必须留痕。
        """
        if self._logger is None:
            return
        try:
            key = "%s|%s|%s|%s" % (
                chosen,
                skipped,
                ",".join(sorted(bias or set())),
                "|".join("%s:%s" % (i, s) for i, s in scored),
            )
            if key == self._last_reward_route_log_key:
                return
            self._last_reward_route_log_key = key
            self._logger.info(
                "[sts2_reward_route] 流派=%s 成型=%s 教训=%s 候选评分=%s -> %s",
                archetype_tags or [],
                sorted(bias or set()),
                lesson_signals or {},
                ", ".join("%s=%s" % (i, s) for i, s in scored),
                "跳牌（全候选不值得拿）" if skipped else "选 option_index=%s" % chosen,
            )
        except Exception:
            pass

    def _debug(self, message: str, *args: Any) -> None:
        return

    # ── ★ 战斗决策留痕（2026-10-18）────────────────────────────────────────────
    # 为什么必须单独写：`_debug` 在上方是个**空函数**，所有 `[sts2_*]` 战斗调试行
    # 其实一个字都没输出。战斗是玩家最容易质疑的决策（"求解器算得对，为什么不照打？"），
    # 而线上**完全没有留痕** ⇒ 既无法核对、也无法统计"到底按不按求解器打"。
    # 这与 `_log_map_route`（:507）当初补留痕的理由完全相同，但战斗这条最重要的
    # 路径一直漏着。这里补上，并且**显式对比"求解器建议"与"最终执行"**。
    #
    # ★★ 限流器的坑（2026-10-18 实机踩到，自查修）：
    #   第一版写成"每实例最多 _COMBAT_LOG_MAX 条"，而计数**永不重置** ⇒
    #   打满 60 条后**永久静默**。实测：插件 11:47:25 启动，11:47–11:49 打出
    #   56 条 follow + 4 条 fallback = 60 条，**之后整局再也不输出**
    #   （现象与"留痕没生效"难以区分，害我又排查了一轮）。
    #   ⇒ 改成**按"局面+动作"去重 + 每秒窗口限流**：
    #     同一个 (屏幕|层|回合|求解器建议|实际执行) 只记一次 —— 这既压掉了
    #     最要命的重复刷屏（实测同一局面重复 8~20 次），又**不会**因为总量到顶
    #     而再也看不到新决策。只有完全不同局面才产生新行，长局也能看全。
    #
    # ★★ 同一个坑的**第二次**踩（2026-10-20，t39）：上面那段注释说"改成去重+限流"，
    #   但**总量封顶这一层并没有被改掉** —— 200 这个阈值与 `:603` 的 return
    #   仍是同一个**永不重置**的全局计数。⇒ 只是把阈值从 60 推迟到 200，
    #   **不是修好了**。实机证据：10:59 同时段 Combat Solver 侧 ROUTE_ACTION
    #   3532 条 / 45 个 traceId，而 `[sts2_combat_follow]` **0 条** ⇒
    #   45 条建议全部"无可配对"，跨 mod 对照失效。
    #   ⇒ 现在把封顶语义改为**每场战斗**：计数与去重键随**战斗身份**归零
    #     （身份 = 屏幕|层|是否战斗，见 `_battle_identity`）。
    #   ★ 并且触及上限时**必须留一行告警**（见 `_combat_log`）：静默正是这个
    #     缺陷能潜伏两轮而无人察觉的原因。
    _COMBAT_LOG_MAX = 200  # 每场战斗的留痕上限（达到后打一行告警，换战斗即重置）
    _COMBAT_LOG_MIN_INTERVAL = 0.25  # 秒；同键去重之外再加一道时间闸

    def _battle_identity(self, snapshot: dict[str, Any] | None, classification: dict[str, Any] | None = None) -> str:
        """战斗身份 —— 留痕计数的重置依据。

        ★ 沿用 `service.py:3217` 的公式形态 `f"{screen}|{floor}|{act}|{in_combat}|..."`，
          但只用 planner 内确实拿得到的三个分量（`plan()` 的局部量 `snapshot` /
          `classification`），避免为了凑格式而去读 planner 不该读的状态。

        ★ 为什么是这三个分量：屏幕变了 / 层变了 / 进战斗状态变了，都意味着
          "这是另一场战斗"。反之同一场战斗内这三者恒定 —— 于是计数在其中累计、
          换战斗时归零。**不引入时间分量**（时间驱动会让长战斗仍被封顶）。
        """
        snap = snapshot if isinstance(snapshot, dict) else {}
        cls = classification if isinstance(classification, dict) else {}
        screen = str(snap.get("screen") or cls.get("state_name") or "")
        floor = str(snap.get("floor") if snap.get("floor") is not None else "")
        in_combat = cls.get("in_combat")
        if in_combat is None:
            in_combat = snap.get("in_combat")
        return f"{screen}|{floor}|{in_combat}"

    def _combat_log(
        self,
        message: str,
        *args: Any,
        dedup_key: str = "",
        battle_identity: str | None = None,
    ) -> None:
        """战斗决策的 info 级留痕。

        ★ 去重键优先（同一局面/同一决策只记一次），去重键为空时退回纯限流。
        ★ 总量封顶是**每场战斗**的：传入 `battle_identity` 且与上次不同 ⇒
          计数、去重键、告警标志全部归零，新战斗重新获得完整留痕预算
          （见 `_COMBAT_LOG_MAX` 上方的说明）。
        ★ 触及上限时**留一行告警**，绝不静默 return —— 静默正是本缺陷
          能潜伏两轮而无人察觉的原因。
        """
        if self._logger is None:
            return
        try:
            # ── ① 战斗身份变化 ⇒ 归零（必须在其余判定**之前**）──────────────
            identity = battle_identity
            if identity is None:
                identity = self._combat_log_battle_identity or ""
            if identity != self._combat_log_battle_identity:
                self._combat_log_battle_identity = identity
                self._combat_log_count = 0
                self._combat_log_last_key = ""   # 新战斗的首条不得被旧战斗的键吞掉
                self._combat_log_cap_warned = False

            now = time.time()
            if dedup_key:
                if dedup_key == self._combat_log_last_key:
                    return
                self._combat_log_last_key = dedup_key
            # ── ② 每场战斗上限：达上限 ⇒ 打一行告警（只一次），然后丢弃正文 ──
            if self._combat_log_count >= self._COMBAT_LOG_MAX:
                if not self._combat_log_cap_warned:
                    self._combat_log_cap_warned = True
                    self._logger.info(
                        "[sts2_combat_log_cap] 本场战斗留痕已达上限 计数=%s 上限=%s "
                        "战斗身份=%s —— 本场后续战斗留痕将不再输出（换战斗后自动恢复）",
                        self._combat_log_count,
                        self._COMBAT_LOG_MAX,
                        self._combat_log_battle_identity or "(未提供)",
                    )
                return
            if now - self._combat_log_last_at < self._COMBAT_LOG_MIN_INTERVAL:
                return
            self._combat_log_count += 1
            self._combat_log_last_at = now
            self._logger.info(message, *args)
        except Exception:
            pass

    def _combat_log_solver_vs_executed(
        self,
        *,
        solver_kind: Any,
        solver_card_id: Any,
        solver_target: Any,
        executed_action: str,
        executed_kwargs: dict[str, Any],
        matched: bool,
        fallback_reason: str = "",
        hand: list[Any] | None = None,
        snapshot: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
    ) -> None:
        """★ 核心留痕：**求解器建议了什么 / 实际执行了什么 / 是否一致**。

        这是回答"战斗时到底按不按求解器打"的唯一可靠依据。格式固定，便于后续
        用 grep / 正则批量统计"按求解器打的比例"：

            [sts2_combat_follow] 求解器=use_potion/EXPLOSIVE_AMPOULE -> 执行=use_potion{...}
                                 一致=是
            [sts2_combat_follow] 求解器=play_card/STRIKE_IRONCLAD -> 执行=heuristic{...}
                                 一致=否(手牌里没有这张卡)

        `matched=False` 时**必须**带 `fallback_reason` —— 静默降级是这次排查里
        最难发现的坑（`if effective_card_index is not None` 分支没有 else 日志）。

        ★ t39：新增 `snapshot` / `classification`（均可选）两个入参，仅用于推导
          **战斗身份**（见 `_battle_identity`），使每场战斗的留痕预算独立。
          它们**不影响**上面这个被消费的输出格式。
        """
        names = []
        if isinstance(hand, list):
            for card in hand:
                if isinstance(card, dict):
                    names.append(str(card.get("name") or card.get("card_id") or "?"))
        hand_key = ",".join(names[:9])
        # ★ t44：把 `turn` 与 `run` 追加到行尾，使**跨 mod 按回合对齐**成为可能。
        #
        #   为什么必须加（实测，2026-10-20）：做「插件实打 vs 上游 CombatSolver 路线」对照时，
        #   上游一条路线覆盖 turn T..T+8（35~46 个动作），而它在 turn T 开始时搜索、耗时中位
        #   2.5 秒，我们出牌间隔中位 1.0 秒 ⇒ 搜索期间我们已打掉 turn T 的前 1~3 张。
        #   只有 turn T 的开头被污染，**turn T+1 之后完全可比**
        #   ⇒ 正确的对齐单位是**回合**，不是整条路线。
        #   但本行原本不带回合信息，只能靠 `[sts2_combat_turn_*]` 的 `turn_key` 反推，
        #   而 `turn_key` 主要来自点评/推送路径，纯 autoplay 下会漏 ⇒ 实测出现
        #   「turn 4 我们 6 张 = 上游 3 张逐字重复两遍」这种"相邻两回合被合并"的结果。
        #   ★ 更早的教训（`loop_runner.py:862` 已记录）：「turn_key 跨 run 会撞值
        #     （实测 132/175=75%，0:2:combat:1 出现在 20 个 run）」
        #     ⇒ 除了 turn **必须同时带 run**，否则同名回合跨局合并（我实测踩了两次：
        #       先只用 turn → "turn 2 出 207 张"；再加 (act,floor,turn) → "turn 2 出 24 张"）。
        #
        #   ★ 兼容性：新字段追加在 `手牌=%s` **之后**，既有解析器的正则
        #     （锚 `一致=是|否` 与 `手牌=[...]`）逐字不受影响。
        #   取值沿用 `loop_runner.py:1112-1118` 的既有惯例，不另造路径。
        raw_state = (
            snapshot.get("raw_state")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("raw_state"), dict)
            else {}
        )
        run_obj = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        turn_val = raw_state.get("turn")
        if turn_val is None:
            combat_obj = (
                raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            )
            turn_val = combat_obj.get("turn")
        run_val = (
            raw_state.get("run_id")
            or run_obj.get("run_id")
            or run_obj.get("runId")
            or run_obj.get("id")
            or ""
        )
        # ★ 去重键 = 求解器建议 + 实际执行 + 手牌 + 原因。
        #   见 `_combat_log` 上方关于「限流器永久静默」的说明：这里按**内容**去重，
        #   于是同一局面反复重试（实测同一动作重复 8~20 次）只留一行，
        #   而局面一变就会立刻出新行 —— 长局也能看全，不再有"打满就瞎"。
        #   ★ t44 刻意**不**把 turn/run 加进去：那会改变限流密度（每回合强制出新行），
        #     而本次目标只是"能读出回合"，不是改留痕密度。
        self._combat_log(
            "[sts2_combat_follow] 求解器=%s/%s(target=%s) -> 执行=%s%s 一致=%s%s 手牌=%s turn=%s run=%s",
            solver_kind,
            solver_card_id,
            solver_target,
            executed_action,
            ("(" + ",".join(f"{k}={v}" for k, v in executed_kwargs.items()) + ")")
            if executed_kwargs
            else "",
            "是" if matched else "否",
            ("  原因=" + fallback_reason) if fallback_reason else "",
            names[:9],
            turn_val if turn_val is not None else "?",
            run_val if run_val else "?",
            dedup_key="|".join(
                (
                    str(solver_kind),
                    str(solver_card_id),
                    str(solver_target),
                    str(executed_action),
                    ",".join(f"{k}={v}" for k, v in sorted(executed_kwargs.items())),
                    "1" if matched else "0",
                    str(fallback_reason),
                    hand_key,
                )
            ),
            # ★ t39：传战斗身份，使每场战斗的留痕预算独立（否则又变成永不重置）。
            battle_identity=self._battle_identity(snapshot, classification),
        )

    def _log_combat_heuristic(
        self,
        reason: str,
        hand: list[Any] | None = None,
        *,
        snapshot: dict[str, Any] | None = None,
        classification: dict[str, Any] | None = None,
    ) -> None:
        """求解器**没参与**（或没匹配上）而走启发式时的留痕。"""
        names = []
        if isinstance(hand, list):
            for card in hand:
                if isinstance(card, dict):
                    names.append(str(card.get("name") or card.get("card_id") or "?"))
        self._combat_log(
            "[sts2_combat_fallback] 走启发式 原因=%s 手牌=%s",
            reason,
            names[:9],
            dedup_key="fallback|" + str(reason) + "|" + ",".join(names[:9]),
            # ★ t39：同上，传战斗身份。
            battle_identity=self._battle_identity(snapshot, classification),
        )

    def _log_map_route(self, scored: list[tuple[int, float]], chosen: int, *, hp_ratio: float, gold: int) -> None:
        """把这一 tick 的路线评估结果用 info 级记一次（同一局面只记一次，避免刷屏）。

        为什么要单独写：`_debug` 在本文件里是个**空函数**，所有 [sts2_*] 调试行其实都没输出，
        线上没法核对"到底按什么选的"。选路是玩家最容易质疑的决策，必须留痕。
        """
        if self._logger is None:
            return
        try:
            key = "|".join(f"{index}:{score:.2f}" for index, score in scored)
            if key == self._last_map_route_log_key:
                return
            self._last_map_route_log_key = key
            self._logger.info(
                "[sts2_map_route] 金币=%s 血比=%.2f 候选评分=%s -> 选 option_index=%s",
                gold,
                hp_ratio,
                ", ".join(f"{index}={score:.2f}" for index, score in scored),
                chosen,
            )
        except Exception:
            pass

    @staticmethod
    def _available_actions_signature(available_actions: list[dict[str, Any]]) -> str:
        return ",".join(sorted(str(action.get("type") or "") for action in available_actions if isinstance(action, dict)))

    def _next_capstone_option(self, available_actions: list[dict[str, Any]]) -> int | None:
        """capstone 选项游标：逐项往前，扫过 _MAX_CAPSTONE_SWEEP 项后返回 None（等界面自己变）。"""
        signature = self._available_actions_signature(available_actions)
        if signature != self._capstone_signature:
            self._capstone_signature = signature
            self._capstone_last_option = -1
        option = self._capstone_last_option + 1
        if option >= self._MAX_CAPSTONE_SWEEP:
            return None
        self._capstone_last_option = option
        return option

    def plan(self, context: dict[str, Any]) -> PlannedOperation | None:
        classification = context.get("classification") if isinstance(context.get("classification"), dict) else {}
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        strategy_context = context.get("strategy_context") if isinstance(context.get("strategy_context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        state_name = str(classification.get("state_name") or snapshot.get("screen") or "").strip().lower()
        available_actions = snapshot.get("available_actions") if isinstance(snapshot.get("available_actions"), list) else []
        preferences = strategy_context.get("preferences") if isinstance(strategy_context.get("preferences"), dict) else {}

        if state_name != "character_select":
            # 离开选人界面就把这次的选人记账清掉：下一局重新定角色、重新按一次。
            self._character_pick_target = ""
            self._character_pick_done = False

        if state_name == "main_menu":
            # 主菜单优先级：**先「继续」**。原来 open_character_select 排在前面，而主菜单只要单人按钮可用
            # 就会广告 open_character_select（GameStateService.CanOpenCharacterSelect），于是插件会去点
            # 「单人/新建局」，抢在存档前面 —— 这正是「和 AI 组队中途退出后回不到存档」的成因。
            # 只有在没有可继续存档时才允许开新局/看时间线。
            action = (
                self._find_action(available_actions, "continue_run")
                or self._find_action(available_actions, "open_character_select")
                or self._find_action(available_actions, "open_timeline")
            )
            if action is None:
                # 子菜单压着主菜单时「继续」按钮会被藏起来（CanContinueRun 要求 SubmenusOpen == false），
                # 先收干净子菜单，下一 tick 就能看到 continue_run。
                action = self._find_action(available_actions, "close_main_menu_submenu")
                if action is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="main_menu_close_submenu")
                action = self._find_action(available_actions, "choose_timeline_epoch")
            if action is not None:
                kwargs = self._main_menu_kwargs(action, summary_context)
                reason = "main_menu_continue_run" if str(action.get("type") or "").lower() == "continue_run" else "main_menu_default"
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.95, source="heuristic", reason=reason)

        if state_name == "multiplayer_lobby":
            # co-op 大厅：选角 -> ready。猫娘侧由 mod 的 NekoAutoplayDriver 驱动，这里是插件驱动的那一份。
            action = self._find_action(available_actions, "ready_multiplayer_lobby")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="multiplayer_lobby_ready")
            action = self._find_action(available_actions, "select_character")
            if action is not None:
                selected_index = self._selected_character_index(summary_context)
                kwargs = {"option_index": selected_index} if selected_index is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.85, source="heuristic", reason="multiplayer_lobby_select_character")
            action = self._find_action(available_actions, "join_multiplayer_lobby")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="multiplayer_lobby_join")

        if state_name == "character_select":
            action = self._find_action(available_actions, "select_character")
            embark_action = self._find_action(available_actions, "embark")
            # 玩家口径：**每局角色随机**。用游戏自带的「随机」候选（is_random），而不是插件自己摇 ——
            # 那样连"随机"这个语义都交给游戏，公告时再读它实际抽到谁（见 service._maybe_announce_character）。
            #
            # 老毛病提醒：`_selected_character_index` 取的是"当前已选中"的角色下标，所以再发一次
            # select_character(那个下标) 是 no-op —— 屏幕不推进、下一 tick 又发，无限重按。
            # 这里靠"按过一次就不再按"（记账由执行推动，见 note_character_select_dispatched）保证推进。
            signature = self._character_list_signature(summary_context)
            if signature != self._character_pick_key:
                self._character_pick_key = signature
                self._character_pick_done = False
            random_index = self._random_character_index(summary_context)
            already_random = self._selected_character_is_random(summary_context)
            # 一个网络只会打一个角色：有已下载的模型时，在那些角色里选，不用游戏的「随机」
            # （见 configure_preferred_characters）；一个都没有时走下面的老路。
            preferred_index = self._preferred_character_index(summary_context)
            if preferred_index is not None:
                if action is not None and not self._preferred_character_selected(summary_context) and not self._character_pick_done:
                    self._log_character_choice("preferred", preferred_index)
                    return PlannedOperation(
                        action_type=action["type"],
                        kwargs={"option_index": preferred_index},
                        confidence=0.9,
                        source="heuristic",
                        reason="character_select_preferred",
                    )
            elif action is not None and random_index is not None and not already_random and not self._character_pick_done:
                self._log_character_choice("random", random_index)
                return PlannedOperation(
                    action_type=action["type"],
                    kwargs={"option_index": random_index},
                    confidence=0.9,
                    source="heuristic",
                    reason="character_select_random",
                )
            if embark_action is not None:
                # 角色已定（随机项或上一次玩的）就该开局。
                self._log_character_choice("embark", None)
                return PlannedOperation(action_type=embark_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="character_select_embark")
            if action is not None:
                selected_index = self._selected_character_index(summary_context)
                kwargs = {"option_index": selected_index} if selected_index is not None else {}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.9, source="heuristic", reason="character_select_default")

        if state_name == "event":
            action = self._find_action(available_actions, "choose_event_option")
            if action is not None:
                # 事件房是纯启发式的：插件不再有任何 LLM 决策（2026-10 摘除 EventAdviceGenerator）。
                preferred_option = self._preferred_event_option(summary_context, preferences, strategy_context)
                self._debug(
                    "[sts2_event_plan] options=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("event_options") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                kwargs = {"option_index": preferred_option if preferred_option is not None else 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.8, source="heuristic", reason="event_preference_or_default")

        if state_name == "map":
            action = self._find_action(available_actions, "choose_map_node")
            if action is not None:
                decision = self._map_decision(summary_context, preferences, snapshot)
                if decision is None:
                    # 多人局可能在等指令、等首票或已投票等队友，均不重复提交。
                    # 下一 tick 重读投票；follow 模式仍可跟随队友改票。
                    self._debug("[sts2_map_plan] policy=%s wait=%s", self._map_policy, self.map_wait_reason(summary_context))
                    return None
                node_index, reason = decision
                self._debug(
                    "[sts2_map_plan] policy=%s nodes=%s chosen=%s reason=%s payload=%s",
                    self._map_policy,
                    summary_context.get("payload", {}).get("travelable_nodes") if isinstance(summary_context.get("payload"), dict) else [],
                    node_index,
                    reason,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                return PlannedOperation(
                    action_type=action["type"],
                    kwargs={"option_index": node_index},
                    confidence=0.95 if reason == "map_follow_player_vote" else 0.82,
                    source="heuristic",
                    reason=reason,
                )

        if state_name in {"reward", "card_selection_reward", "card_selection"}:
            # 两段是**分开**的，动作清单天然区分：
            #   领奖励段：claim_reward 在册（另有 resolve_rewards / collect_rewards_and_proceed）
            #   选卡段  ：领出一张卡奖励后 claim_reward 消失，换成 choose_reward_card / skip_reward_cards
            card_action = self._find_action(available_actions, "choose_reward_card")
            reward_candidates = self._reward_candidates(summary_context)
            if card_action is not None and reward_candidates:
                # 选卡段：`_preferred_reward_option` 对「全候选都不值得拿」会返回 None
                # （见 :1548 `if best_candidate[0] < 0`——最佳卡得分为正才选）。
                # ★旧代码拿到 None 仍照选：`_reward_kwargs(..., None)` 退回"第一个选项"
                #   ⇒ 无条件选牌 ⇒ 诅咒卡/垃圾卡被收进卡组，卡组质量单调下降。
                #   这也是「变化卡事件会选到诅咒」的直接机制。
                # ⇒ 现在改为：None 表示"一张都不该拿"，优先跳牌（skip_reward_cards），
                #   没有跳牌动作时才退回原行为（保持向前兼容，不改变无 skip 动作的场景）。
                preferred_option = self._preferred_reward_option(summary_context, preferences, strategy_context)
                if preferred_option is None:
                    skip_action = self._find_action(available_actions, "skip_reward_cards")
                    if skip_action is not None:
                        self._debug("[sts2_reward_skip] 全候选不值得拿 -> 跳牌（避免污染卡组）")
                        self._log_reward_route(
                            self._last_reward_scored,
                            None,
                            skipped=True,
                            archetype_tags=self._last_reward_archetype_tags,
                            bias=self._last_reward_bias,
                            lesson_signals=self._last_reward_lesson_signals,
                        )
                        return PlannedOperation(
                            action_type=skip_action["type"],
                            kwargs={},
                            confidence=0.7,
                            source="heuristic",
                            reason="reward_skip_low_value",
                        )
                self._log_reward_route(
                    self._last_reward_scored,
                    preferred_option,
                    skipped=False,
                    archetype_tags=self._last_reward_archetype_tags,
                    bias=self._last_reward_bias,
                    lesson_signals=self._last_reward_lesson_signals,
                )
                kwargs = self._reward_kwargs(summary_context, card_action, preferred_option)
                return PlannedOperation(action_type=card_action["type"], kwargs=kwargs, confidence=0.72, source="heuristic", reason="reward_pick_card")
            claim_action = self._find_action(available_actions, "claim_reward")
            if claim_action is not None:
                # 逐个领；**药水排最后**，且"已经真的发过的下标"不再重发。
                # 药水栏满时领药水发不出去，而 mod 照样回 completed -> 奖励清单不变 ->
                # 旧代码每 tick 重发同一个 claim_reward(0)（正好是药水），无限重领（实测卡 5 分钟以上）。
                claim_index = self._next_reward_claim_index(summary_context)
                if claim_index is None and not self._reward_claimable_list(summary_context):
                    # payload 里没有 mod 的 rewards[] 形状（旧口径 / 别的来源）：退回原来的偏好下标，
                    # 不要因为"读不到新形状"就把动作整个吞掉。
                    preferred_option = self._preferred_reward_option(summary_context, preferences, strategy_context)
                    claim_index = preferred_option if preferred_option is not None else 0
                if claim_index is not None:
                    return PlannedOperation(
                        action_type=claim_action["type"],
                        kwargs={"option_index": claim_index},
                        confidence=0.72,
                        source="heuristic",
                        reason="reward_claim",
                    )
            # 奖励领完 / 都领不动了 -> 推进到下一屏。
            proceed = (self._find_action(available_actions, "collect_rewards_and_proceed")
                       or self._find_action(available_actions, "resolve_rewards")
                       or self._find_action(available_actions, "skip_reward_cards")
                       or self._find_action(available_actions, "proceed"))
            if proceed is not None:
                return PlannedOperation(action_type=proceed["type"], kwargs={}, confidence=0.85, source="heuristic", reason="reward_proceed_to_map")

        # plain "card_selection" 覆盖战斗内选牌（如「坚毅」烧牌 / combat_hand_select）与菜单卡牌选择；
        # reward 类走上面的 reward 分支（那里找 choose_reward_card/claim_reward，这里没有才落到 select_deck_card）。
        if state_name in {"card_selection", "card_selection_unusefull", "card_selection_delet", "card_selection_transform", "card_selection_remove"}:
            # 1) 能确认就先确认。老代码把 select_deck_card 排在 confirm_selection 前面，于是永远不确认；
            #    需要选 2 张的界面（min_select=2）在没选够时 confirm 不可用，就会一直重按同一张卡而卡死。
            confirm_action = self._find_action(available_actions, "confirm_selection")
            if confirm_action is not None and self._selection_can_confirm(summary_context):
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.95, source="heuristic", reason="selection_confirm")
            action = self._find_action(available_actions, "select_deck_card")
            if action is not None:
                preferred_option = self._next_selection_option(summary_context, preferences, context)
                self._debug(
                    "[sts2_remove_plan] cards=%s chosen=%s payload=%s",
                    summary_context.get("payload", {}).get("selection_cards") if isinstance(summary_context.get("payload"), dict) else [],
                    preferred_option,
                    summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {},
                )
                # 这一屏的候选都按过了就不再发 select_deck_card（发一个没有 option_index 的选牌动作只会
                # 让动作引擎去猜/报错），落到下面的确认或离开分支。
                if preferred_option is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={"option_index": preferred_option}, confidence=0.7, source="heuristic", reason="remove_preference_or_default")
            # 选牌屏没有可用的选牌动作时，先尝试确认，其次离开（关闭牌视图 / 继续）。
            if confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="selection_confirm")
            for fallback in ("close_cards_view", "proceed"):
                fallback_action = self._find_action(available_actions, fallback)
                if fallback_action is not None:
                    return PlannedOperation(action_type=fallback_action["type"], kwargs={}, confidence=0.8, source="heuristic", reason=f"selection_{fallback}")

        # 商店：**统一评分池**（规格 docs/shop_scoring.md）。卡 / 遗物 / 药水 / 删牌进同一个池子比总分，
        # 取最高分；全部低于门槛就一分不花，把钱留到下一个商店。
        #
        # 玩家口径（2026-10-07）：「商店好像很喜欢买遗物？这里最好评估一下那些是真正需要的。
        # 如果没有需要的就留到下一个商店」「买评分最高的，都不高就不买」。老实现是**死板的类别优先**
        # （遗物 → 高价值卡 → 药水 → 删牌 → 其他卡），遗物只要买得起就买、reason 却写着
        # `shop_high_value_relic` —— 这就是"商店很喜欢买遗物"的根因，也把买药冲精英的钱先花掉了。
        if state_name in {"shop", "shop_show"}:
            # 一次进店只开一次货架。open_shop_inventory 是**开关**：开 -> 没值得买的就关 ->
            # 下一 tick 又看见 open_shop_inventory 就又开，于是 open/close 无限来回（玩家报的"商店卡住"）。
            # "已经看过货"用 close_shop_inventory 是否在册判定——它只在货架**开着**时出现。
            visit_key = self._shop_visit_signature(summary_context)
            if visit_key != self._shop_visit_key:
                self._shop_visit_key = visit_key
                self._shop_viewed = False
            if self._find_action(available_actions, "close_shop_inventory") is not None:
                self._shop_viewed = True
            if not self._shop_viewed:
                open_action = self._find_action(available_actions, "open_shop_inventory")
                if open_action is not None:
                    return PlannedOperation(action_type="open_shop_inventory", kwargs={}, confidence=0.72, source="heuristic", reason="shop_open_inventory")

            decision = self._shop_preferred_purchase(summary_context, preferences, snapshot, available_actions)
            if decision is not None and decision.get("action_type"):
                return PlannedOperation(
                    action_type=str(decision["action_type"]),
                    kwargs=dict(decision.get("kwargs") or {}),
                    confidence=float(decision.get("confidence") or 0.7),
                    source="heuristic",
                    reason="shop_scored_buy",
                )

            # 一分不花：日志里已经写了"最高分 vs 门槛"，这里把 reason 也带上，方便事后查。
            reason = str((decision or {}).get("reason") or "shop_close")
            if self._find_action(available_actions, "close_shop_inventory"):
                return PlannedOperation(action_type="close_shop_inventory", kwargs={}, confidence=0.8, source="heuristic", reason=reason)
            if self._find_action(available_actions, "proceed"):
                return PlannedOperation(
                    action_type="proceed",
                    kwargs={},
                    confidence=0.8,
                    source="heuristic",
                    reason=reason if reason == "shop_score_below_threshold" else "shop_done",
                )
            return None

        if state_name == "rest":
            action = self._find_action(available_actions, "choose_rest_option")
            if action is not None:
                preferred_option = self._preferred_rest_option(summary_context, preferences)
                if preferred_option is not None:
                    self._log_rest_choice(summary_context, preferred_option)
                    return PlannedOperation(
                        action_type=action["type"],
                        kwargs={"option_index": preferred_option},
                        confidence=0.78,
                        source="heuristic",
                        reason="rest_option_preference",
                    )
                kwargs = {"option_index": 0}
                return PlannedOperation(action_type=action["type"], kwargs=kwargs, confidence=0.74, source="heuristic", reason="rest_preference_or_default")
            # 休息点无可挑选项（rest.options 空 / 已休息）-> 直接 proceed 离开，继续到地图。
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type="proceed", kwargs={}, confidence=0.8, source="heuristic", reason="rest_proceed")

        if state_name == "chest":
            # 宝箱是**两步**：mod 的流程是 open_chest -> choose_treasure_relic -> proceed。
            # 开箱**前**只有 open_chest 在册（choose_treasure_relic / proceed 都不在册，
            # chest.relic_options 还是空的），而旧分支只找后两个 —— 规划不出任何动作，
            # 猫娘永久停在宝箱屏（实测 floor 10 卡了 6 分钟以上，step_count 一直是 0）。
            # 顺序：能领遗物就先领（避免"已开箱但没领"再按开箱键），其次开箱，最后离开。
            relic_action = self._find_action(available_actions, "choose_treasure_relic")
            if relic_action is not None:
                return PlannedOperation(action_type="choose_treasure_relic", kwargs={"option_index": 0}, confidence=0.85, source="heuristic", reason="chest_claim_relic")
            open_action = self._find_action(available_actions, "open_chest")
            if open_action is not None:
                return PlannedOperation(action_type=open_action["type"], kwargs={}, confidence=0.85, source="heuristic", reason="chest_open")
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="chest_default")

        if state_name == "bundle_selection":
            # 先确认再重选：choose_bundle 选中后仍然留在 available_actions 里，老顺序会不停重选 bundle 0
            # （每次阻塞到界面关闭为止），confirm_bundle 永远够不到。
            confirm_action = self._find_action(available_actions, "confirm_bundle")
            choose_action = self._find_action(available_actions, "choose_bundle")
            signature = self._available_actions_signature(available_actions)
            if signature != self._bundle_signature:
                self._bundle_signature = signature
                self._bundle_chosen = False
            if self._bundle_chosen and confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.95, source="heuristic", reason="bundle_confirm")
            if choose_action is not None:
                self._bundle_chosen = True
                return PlannedOperation(action_type=choose_action["type"], kwargs={"option_index": 0}, confidence=0.8, source="heuristic", reason="bundle_choose")
            if confirm_action is not None:
                return PlannedOperation(action_type=confirm_action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="bundle_confirm")
            action = self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.75, source="heuristic", reason="bundle_proceed")

        if state_name == "capstone_selection":
            # 逐项往前扫，绝不重复按同一个按钮：index 0 可能是返回/关闭，一直按它永远离不开这个栈。
            action = self._find_action(available_actions, "choose_capstone_option")
            if action is not None:
                option = self._next_capstone_option(available_actions)
                if option is not None:
                    return PlannedOperation(action_type=action["type"], kwargs={"option_index": option}, confidence=0.7, source="heuristic", reason="capstone_option")
            for fallback in ("proceed", "dismiss_modal", "close_main_menu_submenu"):
                fallback_action = self._find_action(available_actions, fallback)
                if fallback_action is not None:
                    return PlannedOperation(action_type=fallback_action["type"], kwargs={}, confidence=0.8, source="heuristic", reason=f"capstone_{fallback}")

        if state_name == "cards_view":
            action = self._find_action(available_actions, "close_cards_view") or self._find_action(available_actions, "proceed")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.85, source="heuristic", reason="cards_view_close")

        if state_name == "modal":
            # 先 dismiss 再 confirm：confirm 可能落在"放弃本局"这类危险按钮上。
            action = self._find_action(available_actions, "dismiss_modal") or self._find_action(available_actions, "confirm_modal")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.8, source="heuristic", reason="modal_dismiss")

        if state_name in {"game_over", "victory", "defeat"}:
            action = self._find_action(available_actions, "return_to_main_menu")
            if action is not None:
                return PlannedOperation(action_type=action["type"], kwargs={}, confidence=0.9, source="heuristic", reason="game_over_return_to_main_menu")

        if state_name == "combat":
            # ════════════════════════════════════════════════════════════════════
            # ★★ 战斗 = 纯求解器（PURE SOLVER）—— 冻结区，改前必读
            # ════════════════════════════════════════════════════════════════════
            #
            # 玩家指示（2026-10-18）：
            #   「战斗完全交给求解器。战斗以外的才需要启发式评分。」
            #   「求解器是穷举所有可能性，这一步启发完全没有意义，只需要按照路线走
            #     就一定能成功，启发了会导致更多变量导致失败，得不偿失」
            #
            # ⇒ 战斗内**不允许**启发式介入。理由是设计性的，不是性能性的：
            #   * 求解器已穷举所有可能性 ⇒ 启发式在一个已有最优解的空间里注入噪声；
            #   * 启发式不完美 ⇒ 它的介入**引入额外变量**，让结果不可归因；
            #   * 纯服从 ⇒ 可复现、可归因。
            #
            # 求解器无可用建议时的语义（玩家选定 **A**）：
            #   **丢弃缓存计划、立即重算求解器**，而不是降级启发式、也不是乱发动作。
            #   重算由 `loop_runner.tick` 在下一 tick 完成（它会重新取 /solver/plan）；
            #   本函数此时返回 None ⇒ 本 tick 不发任何动作 ⇒ **局面不变** ⇒ 下一 tick
            #   拿到新计划继续。这与"发一个错动作"有本质区别。
            #
            # ★ 死锁保险（为什么必须有）：
            #   若求解器对某局面**持续**给不出可执行建议（例如它一直建议一张打不出的牌），
            #   纯重算会表现为**永久卡住** —— 比启发式降级更糟。
            #   故：同一局面指纹连续重算 `_SOLVER_DEADLOCK_RETRIES` 次仍无解 ⇒
            #   **发 end_turn 打破僵局**并留痕。这是"不发动作"与"卡死"的分界线。
            #
            # ⚠️ 不要"顺手"把启发式加回来当兜底：那正是本次要移除的东西。
            #    战斗内的 `_plan_combat_turn_sequence` / `_preferred_combat_action` /
            #    `_combat_kwargs` 已删除（备份见 `.dsh-artifacts/deleted/`）。
            # ════════════════════════════════════════════════════════════════════
            solver_plan = context.get("solver_plan") if isinstance(context.get("solver_plan"), dict) else None
            # ★ 留痕要用到手牌快照（"执行了什么"要能对上具体牌）。
            _combat_hand = (
                snapshot.get("raw_state", {}).get("combat", {}).get("hand")
                if isinstance(snapshot.get("raw_state", {}).get("combat", {}).get("hand"), list)
                else []
            )
            # ★ 求解器为何没被采用（供统一留痕，避免"静默降级"）。
            solver_skip_reason = ""
            # ★ `first_step` 必须在**函数作用域**里先声明：求解器分支若未进入
            #   （无计划/未在战斗），留痕仍要引用它来记录"求解器本来建议什么"。
            first_step: dict[str, Any] | None = None
            if solver_plan is not None:
                first_step = _line_step_at(solver_plan, context.get("solver_step_index"))
            # ════════════════════════════════════════════════════════════════════
            # ★★★ t45：战斗出牌的**准入门槛（HARD GATE）** —— 必须排在**最前**。
            # ════════════════════════════════════════════════════════════════════
            #
            # 玩家指示（本次任务）：
            #   「严格等待求解器算出结果后才执行，禁止抢跑、禁止启发式兜底出牌。」
            #
            # 为什么位置必须在**这里**（而不是继续沿用下面那些事后判据）：
            #   缺陷形态是「只要 `line[0].steps[0]` 存在就出牌，出牌之后才发现计划过期，
            #   再进入补救流程」—— 那是**补偿**，不是准入。旧判据
            #   （`is_plan_absent` / `is_true_deadlock` / `is_signature_unchanged_since_fetch`）
            #   全都在 `_combat_solver_only_outcome` 里，**在出牌动作已经产生之后**才被求值
            #   ⇒ 对"这一次该不该出手"毫无约束力。
            #
            # 四条件（详见 `loop_runner.solver_play_gate` 的 docstring 与
            # `SOLVER_SEARCH_STATUSES` 上方那段"依据"长注释）：
            #   C1 `solver_plan` 是 dict 且 `in_combat is True`
            #   C2 `search_status` ∈ MapStatus 值域（= 搜索已算出结果）
            #   C3 `line` 非空且首步**在当前局面可执行**
            #   C4 这份计划确实基于当前局面（`plan_fetch_signature` / 整回合快照续行）
            #
            # ★ 不满足时本 tick 的语义（**玩家口径，逐字**）：
            #   「不发任何出牌动作、不发 end_turn、不落回启发式，只返回"继续等待"」。
            #   ⇒ 下面 `_combat_solver_gate_blocked` 在 `waiting_solver` 分支只写日志并 return None。
            #   ★ 唯一例外是 `solver_cannot_act`（求解器已给出**完整**结果、首步仍不可执行、
            #     且取回后指纹未变）—— 那才是真无解，交给既有上界（`_SOLVER_DEADLOCK_RETRIES`）
            #     在耗尽后发 end_turn 破局。没有这个例外，求解器对一个"建议了一张打不出的牌"
            #     的局面会永久静默 ⇒ 卡死，同样违反本次任务。
            # ★ 惰性导入（与 `_combat_solver_only_outcome` 里的同族处理一致）：
            #   `loop_runner` 侧也在模块级引用本模块（service 侧），用函数内导入把这个方向的
            #   依赖彻底推迟到"真的进入战斗分支"，万无一失地避免循环导入。
            from .loop_runner import GATE_ACT, solver_play_gate

            _gate = solver_play_gate(
                solver_plan=solver_plan,
                snapshot=snapshot,
                first_step=first_step,
                plan_origin=str(context.get("plan_origin") or "none"),
                plan_fetch_signature=str(context.get("plan_fetch_signature") or ""),
                current_signature=self._combat_deadlock_signature(snapshot),
            )
            if _gate["verdict"] != GATE_ACT:
                return self._combat_solver_gate_blocked(
                    gate=_gate,
                    snapshot=snapshot,
                    available_actions=available_actions,
                    solver_plan=solver_plan,
                    first_step=first_step,
                    hand=_combat_hand,
                    classification=classification,
                    context=context,
                )
            self._solver_gate_allowed_count += 1
            # ★ 闸门恢复（上一 tick 在等/判无解，这一 tick 放行）⇒ 打一行**带精确累计值**的收口行。
            #   为什么需要它：等待期的日志是节流的（同因每 N 次一行），单看那些行读不出"这一轮
            #   到底被拦了几次"。恢复行在**边界**上把三个计数逐字写出来 ⇒
            #   「被 gate 拦下的 tick 数 / 放行数」可精确核对，不靠数日志行。
            #   ★ 只在**状态跃迁**时打（上一 tick 是拦下的），否则每个放行 tick 都会重复一行。
            if self._solver_gate_last_verdict in {"waiting_solver", "solver_cannot_act"}:
                self._log_solver_gate_resume(gate=_gate)
            self._solver_gate_last_verdict = "act"
            self._log_solver_gate_act(gate=_gate)
            if not solver_plan or not solver_plan.get("in_combat"):
                solver_skip_reason = "无求解器计划（未在战斗/取计划失败）"
            if solver_plan and solver_plan.get("in_combat"):
                # 整回合缓存模式：按 solver_step_index 取当前回合第几步（循环逐个执行，不重查）。
                first_step = _line_step_at(solver_plan, context.get("solver_step_index"))
                solver_action = first_step.get("kind") if isinstance(first_step, dict) else None
                solver_card_index = first_step.get("card_index") if isinstance(first_step, dict) else None
                solver_card_id = first_step.get("card_id") if isinstance(first_step, dict) else None
                solver_target = first_step.get("target_index") if isinstance(first_step, dict) else None
                if not solver_action:
                    solver_skip_reason = "求解器计划的当前步为空（line 无 kind）"
                if solver_action == "play_card":
                    # 用 card_id 重新匹配实机手牌（mod 的 NekoAutoplayDriver 同样如此）：solver 给的是
                    # /solver/plan 捕获瞬间的位置卡号；捕获与执行之间手牌可能漂移，直接抽样会打在别的卡上。
                    effective_card_index, resolve_reason = self._resolve_solver_card_index(
                        snapshot, solver_card_id, solver_card_index
                    )
                    if effective_card_index is not None:
                        kw = {"card_index": effective_card_index}
                        if solver_target is not None:
                            kw["target_index"] = solver_target
                        self._combat_log_solver_vs_executed(
                            solver_kind=solver_action,
                            solver_card_id=solver_card_id,
                            solver_target=solver_target,
                            executed_action="play_card",
                            executed_kwargs=kw,
                            matched=True,
                            hand=_combat_hand,
                            # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                            snapshot=snapshot,
                            classification=classification,
                        )
                        return PlannedOperation(
                            action_type="play_card",
                            kwargs=kw,
                            confidence=0.9,
                            source="mod_solver",
                            reason="mod_combat_search",
                        )
                    # ★ 实机手牌里没有这张卡 ⇒ 计划已过期（漂移）。
                    #   纯求解器语义：**不发启发式动作**，丢弃本计划、让下一 tick 重算。
                    #   实测根因（`.dsh-artifacts/ROOTCAUSE-solver-adherence.md`）：
                    #   求解器搜索要 ~1 秒，期间上一步动作已让手牌变化 ⇒ 计划天然过期。
                    # ★ t43：改用 `_resolve_solver_card_index` 带回的**可区分原因**，
                    #   不再一律写成"手牌漂移"。两种成因（不在手牌 / 在手但 playable=false）
                    #   在统计与后续修法上完全不同，混在一起会让 1681 次重算无法归因。
                    solver_skip_reason = (
                        resolve_reason
                        or f"求解器建议 {solver_card_id} 但实机手牌里没有这张卡（手牌漂移）"
                    )
                if solver_action == "use_potion":
                    potion_index = self._solver_potion_option_index(snapshot, solver_card_id)
                    if potion_index is not None:
                        kw = {"option_index": potion_index}
                        if solver_target is not None:
                            kw["target_index"] = solver_target
                        self._combat_log_solver_vs_executed(
                            solver_kind=solver_action,
                            solver_card_id=solver_card_id,
                            solver_target=solver_target,
                            executed_action="use_potion",
                            executed_kwargs=kw,
                            matched=True,
                            hand=_combat_hand,
                            # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                            snapshot=snapshot,
                            classification=classification,
                        )
                        return PlannedOperation(
                            action_type="use_potion",
                            kwargs=kw,
                            confidence=0.9,
                            source="mod_solver",
                            reason="mod_solver_potion",
                        )
                    solver_skip_reason = f"求解器建议用药水 {solver_card_id} 但实机找不到对应药水"
                if solver_action == "end_turn":
                    self._combat_log_solver_vs_executed(
                        solver_kind=solver_action,
                        solver_card_id=solver_card_id,
                        solver_target=solver_target,
                        executed_action="end_turn",
                        executed_kwargs={},
                        matched=True,
                        hand=_combat_hand,
                        # ★ t39：传战斗身份，使每场战斗的留痕预算独立。
                        snapshot=snapshot,
                        classification=classification,
                    )
                    return PlannedOperation(
                        action_type="end_turn",
                        kwargs={},
                        confidence=0.9,
                        source="mod_solver",
                        reason="mod_solver_end_turn",
                    )
                if solver_action and not solver_skip_reason:
                    solver_skip_reason = f"求解器给了未知动作 kind={solver_action!r}"
            # ════════════════════════════════════════════════════════════════════
            # 求解器没给出可执行动作 ⇒ **不降级启发式**，交给下一 tick 重算（语义 A）。
            # 只保留死锁保险：同一局面连续无解 N 次就发 end_turn 打破僵局。
            # ════════════════════════════════════════════════════════════════════
            return self._combat_solver_only_outcome(
                snapshot=snapshot,
                available_actions=available_actions,
                solver_plan=solver_plan,
                first_step=first_step,
                solver_skip_reason=solver_skip_reason or "求解器未给出可执行首步",
                hand=_combat_hand,
                # ★ D1：本 tick 的计划究竟是"复用缓存"还是"刚重取" —— 由 loop_runner 的真实分支
                #   透传（见 loop_runner.py 的 `plan_origin`），**绝不在这里猜**。
                #   这是区分「计划过期（A，应重取不计死锁）」与「求解器真无解（B，应计数兜底）」
                #   的**唯一**客观事实（两者在局面上逐位不可区分，见 R1-D1-CONTRACT.md §0）。
                plan_origin=str(context.get("plan_origin") or "none"),
                # ★ t11：**取回计划那一刻**的局面指纹。用于识别"重取回来的计划已经漂移"
                #   （搜索 ~1s 期间手牌变了）⇒ 应归 A，而不是误判成 B 并推高死锁计数。
                plan_fetch_signature=str(context.get("plan_fetch_signature") or ""),
                # ★ t39：透传 classification，供留痕推导**战斗身份**（每场战斗独立预算）。
                classification=classification,
            )
        return None

    #: 同一局面连续无解多少次后，发 end_turn 打破僵局。
    #: 取值权衡：太小 ⇒ 求解器偶发超时就白扔回合；太大 ⇒ 玩家看到明显卡住。
    #: 3 次 × 每 tick ~1s ≈ 3 秒可见停顿，仍能自愈。
    #: ★ D1 修复后它**只**统计「求解器看着最新局面仍给不出可执行动作」（= B），
    #:   不再统计「计划过期（牌已不在手）」（= A）。见 `_combat_solver_only_outcome`。
    _SOLVER_DEADLOCK_RETRIES = 3
    # ★★ t13（D3）：非玩家行动窗口的**独立**计数上限。
    #   窗口没开时我们既不能发 end_turn（它不可用）也不该累加 N/3（那不是"求解器无解"），
    #   但必须有一个**可观测**的上限出口，否则窗口永不开时插件永久静默。
    #   达上限时记一条显式告警（含"局面疑似卡住"），并**回绕**计数 ⇒ 日志不会出现越界数字。
    _SOLVER_OUT_OF_WINDOW_LIMIT = 5

    #: ★ D1 新增：同一局面指纹下，「计划过期（漂移）⇒ 立即重取」连续发生多少次后，
    #: 就当**真死锁**处理（转入死锁计数，从而最终发 end_turn 脱离）。
    #:
    #: 为什么必须有这个上界（而不是无限重取）：
    #:   上游 `SolverController.cs:175-177` 用 `UnexpectedReplanCount` 把
    #:   `StateMismatch + DeploymentDrift` 上报为**缺陷信号** —— 即上游把「计划失效」
    #:   当 bug 证据，而不是正常运维事件。反复过期说明我们的计划快照与实机持续不一致，
    #:   若此时**只**重取、不设上界，一旦某局面每 tick 都过期就会永久卡住整局。
    #:
    #: 上界算术（可断言，与契约 §1.3 INV-3 / §5 的形式化陈述一致）：LIMIT=5、RETRIES=3 ⇒
    #:   第 1–5 次 A：不计数、不发动作；第 6/7/8 次 A：转入死锁计数 1/2/3
    #:   ⇒ **第 8 次发 end_turn**。⇒ 任一固定指纹下调用次数 ≤ 8，不可能无限重取。
    #:
    #: ★ 与契约 §1.2 伪代码的一处**有意偏离**（必须记录）：伪代码把过期计数在转入 B 时
    #:   **归零**（`count = 0`），但那样每次 A 只加 1 个 deadlock，而 deadlock 需 RETRIES
    #:   次才触发 end_turn ⇒ 实际要 `LIMIT + LIMIT × RETRIES = 20` 次才脱离，
    #:   **违反 §5 明确要求的「S ≤ 8」上界**。§1.2 与 §5 在这里不可兼得；
    #:   本实现取 §5 的**安全属性**（防无限重取卡死整局），令计数单调递增不归零。
    _SOLVER_STALE_RECLAIM_LIMIT = 5

    # ★★ D11（t28）：被锁事件选项的**哨兵分** —— 保证它排在任何未锁选项之后。
    #
    #   取 −1000 的**上界论证**（必须能断言，不能拍脑袋）：
    #     `_score_event_option` 内任一**单项**正向加分 ≤ +70（卡牌档最高），
    #     而各正向规则虽独立相加，其可命中集合有限（金币/遗物/卡牌/删牌/回血/加上限），
    #     实机观察到的最高合理总分为 **+50** 量级（遗物14+金币10+卡牌8+回血18）。
    #     ⇒ 1000 远大于任何可能总分的绝对值 ⇒ 被锁项恒为最低。
    #   ★ 且 `_normalize_percentile` 只做**相对**归一（把分数映射成 0–100 百分位），
    #     哨兵值不参与绝对量纲，故不影响未锁项之间的相对序。
    #   ★ 用哨兵而非 −60 的理由：若只给被锁项 −60，而某未锁项得 +50，
    #     则 50 > −60 ⇒ **仍会选被锁的** ⇒ 修不好（这正是实机 {0:10, 1:0} 的放大版）。
    _EVENT_LOCKED_OPTION_SCORE = -1000

    # ★★ D11（t28）：EVENT 屏「同一选项被拒且局面未变」连续多少次 ⇒ 写入本屏禁用集。
    #
    #   N 的取值依据（与 D9 同**口径**但更快，理由必须可核）：
    #     · D9 用 `_SOLVER_DEADLOCK_RETRIES = 3` —— 那是**战斗屏**：动作多、局面每回合大变，
    #       且"待确认"可能只是长动画 ⇒ 需要 3 次才敢断定无效。
    #     · EVENT 屏不同：一屏只有 2–3 个选项；一次**成功**选择会立刻改变局面；
    #       而**被锁**是游戏**逐字告知**的确定事实（`is_locked:true` / `"option is locked"`），
    #       不是概率性抖动 ⇒ "试探多次"没有信息价值。
    #   ⇒ N 取 **2**：既不像 N=1 那样可能被一次瞬时网络抖动误禁，
    #     也不像 N=3 那样在被锁项上多空烧一整轮（实机每轮 ≈ 2–8 秒）。
    #   ★ 但若**已拿到明确错误文本/字段**（`is_locked` 或 `option is locked` 或
    #     `details.option_index`），则**立即**禁用（不等 N 次）—— 见 service 侧实现。
    _EVENT_LOCKED_OPTION_RETRIES = 2

    def note_event_option_locked(self, summary_context: dict[str, Any], option_index: Any) -> None:
        """服务层在**游戏明确告知某选项被锁**之后调用：把该 index 记入**本屏禁用集**。

        ★★ D11（t28）的失效检测入口。两种触发（后者更精确，见 service 侧）：
          ① 准备阶段读到 `/state` 的 `option.is_locked == true`（**预防**）；
          ② 派发后被游戏拒：`code=invalid_target` + `message="...option is locked."`
             + `details.option_index=<n>`（**错误驱动的精确信号**）。

        ★ 为什么需要禁用集（而不是只靠打分层读 `is_locked`）：
          · 打分层读 `is_locked` 只在**该字段存在**时有效（旧 mod / 字段缺失就无效）；
          · 而**错误驱动**路径不依赖字段存在性 —— 游戏逐字告诉我们哪个 index 被锁。
          ⇒ 两条腿互为备份：字段缺失时仍有兜底。

        ★ 按屏隔离（见 `_event_locked_set_key`）：换屏 / 换事件自动清空，不跨局累积。
        """
        index = self._safe_int(option_index)
        if index is None:
            return
        key = self._event_locked_set_key(summary_context)
        if key != self._event_locked_set_key_cached:
            self._event_locked_set_cached = set()
            self._event_locked_set_key_cached = key
        if index not in self._event_locked_set_cached:
            self._event_locked_set_cached.add(index)
            if self._logger is not None:
                try:
                    self._logger.warning(
                        "[sts2_d11] 选项被判为已锁 ⇒ 加入本屏禁用集 index=%s（本屏禁用集=%s；"
                        "重算不会改变结论，将改选次优未锁项）"
                        % (index, sorted(self._event_locked_set_cached))
                    )
                except Exception:
                    pass

    def _event_locked_set_key(self, summary_context: dict[str, Any]) -> str:
        """本屏禁用集的隔离键：`screen|floor|event_id`（换其一即视为新屏 ⇒ 自动清空）。

        ★ 为什么必须隔离：禁用集若跨屏累积，会在后续**同一个 index 合法**的事件上
          错误地跳过它 ⇒ 变成新的误伤。用屏 + 事件 id 做键最短且足够。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        screen = str(payload.get("screen") or payload.get("_screen") or "")
        floor = str(payload.get("floor") or "")
        event_id = str(payload.get("event_id") or "")
        return "%s|%s|%s" % (screen, floor, event_id)

    #: ★★ t45：等待态日志的节流步长。
    #:   语义：`为什么在等` 的键（= verdict|missing|search_status|plan_origin）一变就立刻打一行；
    #:   同因连续等待则每 N 次打一行，行内带**累计**计数 ⇒「被闸门拦下的 tick 数」可逐字读出。
    #:   N 取 5：tick ≈ 1.5s ⇒ 同因等待时约每 7.5s 一行，既不刷屏也不会让日志出现死寂
    #:   （静默正是本项目历史上多个缺陷最难查的原因）。
    _SOLVER_GATE_LOG_EVERY = 5

    def _find_action(self, actions: list[dict[str, Any]], action_name: str) -> dict[str, Any] | None:
        target = str(action_name or "").strip().lower()
        for action in actions:
            if not isinstance(action, dict):
                continue
            raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
            candidate = str(action.get("type") or raw.get("name") or raw.get("action") or "").strip().lower()
            if candidate == target:
                return action
        return None

    def _solver_potion_option_index(self, snapshot: dict[str, Any], potion_id: str | None) -> int | None:
        """用 potion_id 回查 snapshot.raw_state.run.potions[] 的真实下标（含空槽），且要求 can_use。

        mod /solver/plan 的 use_potion 给的是 potion_id；插件的 use_potion 动作要 option_index，
        所以必须回查完整 potions 列表（不能用压缩后的占用槽下标）。
        """
        if not potion_id:
            return None
        raw = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        run = raw.get("run") if isinstance(raw.get("run"), dict) else {}
        for p in run.get("potions") or []:
            if not isinstance(p, dict):
                continue
            if str(p.get("potion_id") or "") != potion_id:
                continue
            if not p.get("can_use"):
                return None
            if isinstance(p.get("index"), int):
                return p["index"]
        return None

    def _selected_character_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selected_character_id = payload.get("selected_character_id")
        if not selected_character_id:
            return None
        raw_state = payload.get("character_select") if isinstance(payload.get("character_select"), dict) else {}
        characters = raw_state.get("characters") if isinstance(raw_state.get("characters"), list) else []
        for character in characters:
            if not isinstance(character, dict):
                continue
            if character.get("character_id") == selected_character_id:
                try:
                    return int(character.get("index"))
                except Exception:
                    return None
        return None

    def _main_menu_kwargs(self, action: dict[str, Any], summary_context: dict[str, Any]) -> dict[str, Any]:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type != "choose_timeline_epoch":
            return {}
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        timeline = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else {}
        slots = timeline.get("slots") if isinstance(timeline.get("slots"), list) else []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if not bool(slot.get("is_actionable")):
                continue
            try:
                return {"option_index": int(slot.get("index"))}
            except Exception:
                continue
        return {"option_index": 0}

    def _first_available_map_index(self, summary_context: dict[str, Any]) -> int | None:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        nodes = payload.get("travelable_nodes") if isinstance(payload.get("travelable_nodes"), list) else []
        for index, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            if "index" in node:
                try:
                    return int(node.get("index"))
                except Exception:
                    continue
            return index
        return None

    #: 复盘教训 → 选卡倾向 的关键词表。
    #:
    #: ★ 背景（2026-10-17）：用户指出「强化/删除哪张卡片、卡牌流派的抉择」是败因，
    #:   但此前跨局教训**只**经 `_apply_run_lessons` 影响通用排序，
    #:   **完全不参与 `_score_reward_card`** ⇒ 复盘对「选哪张卡」零影响。
    #:   这里补上通道：把教训文本里的**方向性措辞**映射为选卡加权。
    #:
    #: ★★ 关键设计（否则会误判）：教训里往往**同时**提到两个方向
    #:   （如「输出不够，每回合都在垫格挡」同时含"输出"与"格挡"），
    #:   单纯"出现关键词就加权"会给两边都加分 ⇒ 净效果为 0 或错向。
    #:   ⇒ 必须区分**褒贬**：正向词（要多做）与负向词（别再这样）。
    _REWARD_LESSON_POSITIVE: dict[str, tuple[str, ...]] = {
        "defense": ("多垫", "先垫", "垫够", "留容错", "加格挡", "多格挡", "厚一点", "稳一点", "别头铁"),
        "attack": ("输出不够", "伤害不足", "打不死", "多输出", "加攻击", "斩杀不足", "节奏太慢"),
        "draw": ("抽牌不够", "过牌少", "卡手", "手牌少", "多抽"),
        "energy": ("能量不够", "费用不够", "卡能", "多来点能量"),
        "strength": ("力量流", "缺力量", "多力量", "易伤不够"),
        "orb_focus": ("电球不够", "充能不足", "多聚焦"),
    }
    _REWARD_LESSON_NEGATIVE: dict[str, tuple[str, ...]] = {
        "defense": ("光垫格挡", "只垫格挡", "一直在垫", "垫太多", "纯防御打不动"),
        "attack": ("光输出", "只输出", "硬刚", "头铁", "无脑攻击", "贪输出"),
        "draw": ("抽太多", "过牌过头"),
        "energy": ("能量溢出", "费用多余"),
        "strength": ("力量没用",),
        "orb_focus": ("电球没用",),
    }

    # ---- 商店购买策略（规格 docs/shop_scoring.md）------------------------------------------
    #
    # `_score_reward_card` 的原始分越高越好（命中 deck 策略 +40、archetype +20、分支 +35~50，
    # 核心卡常 ≥40）。老代码里：
    #   `_SHOP_HIGH_VALUE_SCORE = 25` —— "原始分 25 以上就算高价值卡，直接买"；
    #   `_SHOP_ANY_CARD_SCORE = 1`    —— 前面都没得买时，1 分以上的卡也凑合买。
    # 这两个常量现在**并入统一池子**：它们不再是"买/不买"的开关，而是"原始分 -> 补缺分"的标尺
    # （见 `_score_shop_card`）：25 分折成补缺分约 0.42，扣掉代价惩罚后**大致落在门槛附近**，
    # 于是老口径（25 分以上就买）只对"便宜或真缺得厉害的卡"继续成立 —— 这正是玩家要的收紧。
    _SHOP_HIGH_VALUE_SCORE = 25
    _SHOP_ANY_CARD_SCORE = 1
    #: 「补缺分 = 1.0」对应的原始分。取 60 ≈ 2.4 × 老的"高价值线"（`_SHOP_HIGH_VALUE_SCORE` = 25），
    #: 让老口径的边界大致落在门槛上，于是老口径（25 分以上就买）在新口径里只对"便宜或真缺得厉害的卡"成立。
    _SHOP_CARD_FIT_SCALE = SHOP_CARD_FIT_SCALE
    #: 前方有精英时，从选路那一步起向前看几层找精英（规格：「复用它判断"前方有精英"」）。
    _SHOP_ELITE_LOOKAHEAD_ROWS = 4

    def _resolve_solver_card_index(
        self,
        snapshot: dict[str, Any] | None,
        card_id: str | None,
        fallback_index: int | None,
    ) -> tuple[int | None, str]:
        """把 /solver/plan 推荐的卡（card_id）重新映射到实机手牌位置，避免位置索引漂移。

        与 mod 的 NekoAutoplayDriver.DecideCombatAsync 一致：优先按 card_id 精确匹配且可打出；
        匹配不到再退回 solver 给的位置索引（仅当该位置卡可打出）；都不行返回 (None, reason)。

        ★★ 2026-10-18 修（`_test_stale_index.py` 复现的**真实缺陷**）：
          旧实现只要 `fallback_index` 落在手牌范围内**且那张牌可打出**就直接返回它，
          **不管它是不是求解器建议的那张**。后果：
            * 求解器在 T0 建议 hand[0]=打击；执行时手牌漂移，hand[0] 已变成"痛击"；
            * `card_id` 匹配不到 ⇒ 回退 `fallback_index=0` ⇒ **打出"痛击"**；
            * 返回值非 None ⇒ 上层记为 `source="mod_solver"`、`一致=是`。
          ⇒ 也就是**「看起来按求解器打了，其实打的是另一张牌」**，
            而且完全无痕 —— 这正是"求解器算得对却没照打"最隐蔽的成因。
          修法：区分"mod 没给 card_id"（可安全回退位置）与"给了 card_id 但当前手牌里
          没有它"（**必须放弃**，交给启发式重算）。判据是 `card_id` 是否为空，
          而不是"能不能找到一个可打出的位置"。

        ★★ t43（2026-10-20）：返回值从 `int | None` 改为 `(index, reason)`。
          旧版匹配不到只返回 None，调用方只能写一句
          「求解器建议 X 但实机手牌里没有这张卡（手牌漂移）」，把两种成因压在一起：
            ① 卡**真的不在手牌**（已打出/弃掉/消耗）⇒ 计划过期，重算正确；
            ② 卡**在手牌里但 `playable=false`**（能量不够等）⇒ 计划没错，只是暂不可执行。
          实测当天 `[sts2_plan]` 重算 4548 次里 **1681 次（37%）** 报这一句而无法归因，
          且漂移 Top 全是最基础的打击/防御 ⇒ 必须先拆开才能判断是不是模拟器能量模型的问题。
          `reason` 为空串 ⇒ 成功取到 index。原因文本由 `loop_runner._explain_hand_card_state`
          统一生成（两处共用同一实现，避免再次漂移）。
        """
        hand: Any = []
        player: Any = {}
        if isinstance(snapshot, dict):
            raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
            combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
            hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
            player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        # ★ 惰性导入（模块级导入会与 loop_runner 形成循环依赖；本文件已有同样先例，见 :1746/:2067）
        from .loop_runner import _explain_hand_card_state

        if card_id:
            for i, card in enumerate(hand):
                if isinstance(card, dict) and str(card.get("card_id") or "") == str(card_id) and bool(card.get("playable", True)):
                    return i, ""
            # ★ mod 明确给了 card_id，但当前手牌里找不到（或不可打出）⇒ 位置索引已不可信，
            #   绝不能拿陈旧下标去蒙一张牌。交给启发式按当前局面重算。
            return None, _explain_hand_card_state(str(card_id), hand, player)
        # mod 没给 card_id（旧版 /solver/plan 或该步本来就只有位置）：位置索引是唯一线索，
        # 只要落点可打出就沿用。
        if fallback_index is not None and 0 <= fallback_index < len(hand):
            card = hand[fallback_index]
            if isinstance(card, dict) and bool(card.get("playable", True)):
                return fallback_index, ""
            energy = player.get("energy") if isinstance(player, dict) else None
            return None, (
                f"求解器给的手牌下标 {fallback_index} 当前不可打出（playable=false，"
                f"energy_cost={card.get('energy_cost', '?') if isinstance(card, dict) else '?'}，"
                f"当前能量={energy if energy is not None else '?'}）"
            )
        return None, ""

    def _shop_visit_signature(self, summary_context: dict[str, Any]) -> str:
        """这一次逛的是哪家店：换店（楼层变 / 货品集合变）就重新算"没看过"。

        只看楼层不够——同一层的两次商店（罕见但可能）会互相顶掉；带上货品身份最稳。
        """
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        ids: list[str] = []
        for field in ("shop_cards", "shop_relics", "shop_potions"):
            items = payload.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                ids.append(str(item.get("card_id") or item.get("relic_id") or item.get("potion_id") or item.get("index")))
        return f"{payload.get('floor')}|" + "|".join(ids)

    #: 事件屏上"同一选项每多选一次"的递减惩罚（归一化后的百分点）。
    #:
    #: 归一化参考（`_normalize_percentile` 把整数分压到 0–100）：
    #:   扣上限(−60) vs 离开(0) 的差距 → 归一化后约 100 点
    #:   诅咒+遗物(−26) vs 离开(0) → 约 43 点
    #: 取 **40** 的含义：**第二次**选同一项就足以让"扣上限"输给"离开"，
    #: 从而在可重复选择的事件上主动止损；第一次选完全不受影响（不改变正常决策）。
    _EVENT_REPEAT_PENALTY_PER_TIME = 40.0

    #: `selection.kind` 里表示"**变换**卡牌"的取值（来自 mod 的 `BuildSelectionPayload`，
    #: `GameStateService.cs:4250` `NDeckTransformSelectScreen => "deck_transform_select"`）。
    #:
    #: ★为什么必须区分：删牌屏与变换屏的**最优选择语义相反** ——
    #:   * 删牌屏：选诅咒 = **移除**它 ⇒ 最优（`_score_remove_card` 给诅咒 −100）
    #:   * 变换屏：选诅咒 = **变成另一张诅咒** ⇒ 最劣
    #: 游戏侧源码证实诅咒变换是零期望：`CardFactory.GetFilteredTransformationOptions` 里
    #: `if ((uint)(rarity - 8) > 1u)` 对 `Curse`(=9) 为**假**（9−8=1，不 >1）⇒ 跳过
    #: "只保留 Common/Uncommon/Rare" 的稀有度约束 ⇒ 候选池放宽到整个牌池，**可含其它诅咒**。
    #:
    #: 历史缺陷：`card_selection_transform` 与 `card_selection_remove` 在 `:839` 被放进**同一分支**，
    #: 于是变换屏复用了删牌启发式，**每轮都主动选诅咒**。实机采样里
    #: `CARD_SELECTION_TRANSFORM` 出现过 6 次（527 份样本）。
    _TRANSFORM_SELECTION_KINDS = frozenset({"deck_transform_select"})

    #: 变换屏的**屏幕名**（与 `_TRANSFORM_SELECTION_KINDS` 取并集，见 `_is_transform_selection`）。
    #:
    #: 实机证据：日志里 `screen=CARD_SELECTION_TRANSFORM` 真实出现过（527 份样本中 6 次）。
    #: 只认 `selection_kind` 时若那个字段缺失，判定会静默失效 ⇒ 变换屏重新开始选诅咒。
    _TRANSFORM_SCREEN_NAMES = frozenset({"card_selection_transform"})

    #: 变换屏上"诅咒/状态牌"的**变换优先级分**。
    #:
    #: 取值依据 `_score_remove_card` 的标尺（分越低越该被移出/换掉）：
    #:   诅咒 −100 / 状态 −90 / 打击 −60 / 防御 −50 / 普通牌 +10 / 好牌 +60~70
    #:
    #: 定在 **−55** 的含义：**排在"打击"(−60)之后、"防御"(−50)之前** ——
    #:   * 即：换掉一张基础打击比换掉诅咒**更值得**（收益确定：基础牌换掉大概率变强）
    #:   * 而诅咒仍排在防御与所有"有价值牌"之前 ⇒ 有诅咒时**不会**去换好牌
    #:
    #: 为什么**不**沿用删牌屏的 −100：删牌是"移出牌组"，选诅咒**一定能**移除它；
    #: 而变换诅咒的期望**接近零** —— 游戏侧 `CardFactory.GetFilteredTransformationOptions` 对
    #: `Curse`(=9) 跳过稀有度过滤（`9−8=1` 不 `>1`），候选池含其它诅咒 ⇒ 可能换回另一张诅咒。
    #: 所以变换屏**不该把诅咒当首选目标**。
    _TRANSFORM_CURSE_SCORE = -55

    def _guidance_override(self, context: dict[str, Any]) -> dict[str, bool]:
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        return self._guidance_override_from_payload(summary_context)

    def _guidance_override_from_payload(self, summary_context: dict[str, Any]) -> dict[str, bool]:
        decision_payload = summary_context.get("decision_payload") if isinstance(summary_context.get("decision_payload"), dict) else {}
        instructions = decision_payload.get("instructions") if isinstance(decision_payload.get("instructions"), list) else []
        guidance_lines = [
            str(item.get("content") or "")
            for item in instructions
            if isinstance(item, dict) and str(item.get("source") or "") == "neko_guidance"
        ]
        merged = " ".join(guidance_lines).lower()
        if not merged:
            guidance = decision_payload.get("guidance") if isinstance(decision_payload.get("guidance"), dict) else {}
            pending = guidance.get("pending") if isinstance(guidance.get("pending"), list) else []
            merged = " ".join(str(item.get("content") or "") for item in pending if isinstance(item, dict)).lower()
        if not merged:
            return {}
        prefer_defense = any(token in merged for token in ["先防", "优先防", "保血", "保命", "别贪", "不要贪", "求稳", "稳一点"])
        prefer_attack = any(token in merged for token in ["优先输出", "抢伤害", "压血", "收掉", "收尾", "斩杀"])
        return {
            "prefer_defense": prefer_defense,
            "prefer_attack": prefer_attack,
            "avoid_greed": any(token in merged for token in ["别贪", "不要贪"]),
            "prefer_lethal": any(token in merged for token in ["斩杀", "收掉", "收尾"]),
            "conservative": prefer_defense or any(token in merged for token in ["求稳", "稳一点"]),
        }

    @staticmethod
    def _character_list(summary_context: dict[str, Any]) -> list[dict[str, Any]]:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        raw = payload.get("character_select") if isinstance(payload.get("character_select"), dict) else {}
        chars = raw.get("characters") if isinstance(raw.get("characters"), list) else []
        return [c for c in chars if isinstance(c, dict)]

    def _character_list_signature(self, summary_context: dict[str, Any]) -> str:
        return "|".join(
            f"{c.get('index')}:{c.get('character_id')}" for c in self._character_list(summary_context)
        )

    def _random_character_index(self, summary_context: dict[str, Any]) -> int | None:
        """游戏自带的「随机」候选的下标（跳过锁着的）。"""
        for character in self._character_list(summary_context):
            if not bool(character.get("is_random")) or bool(character.get("is_locked")):
                continue
            index = self._safe_int(character.get("index"))
            if index is not None:
                return index
        return None

    def _selected_character_is_random(self, summary_context: dict[str, Any]) -> bool:
        payload = summary_context.get("payload") if isinstance(summary_context.get("payload"), dict) else {}
        selected = str(payload.get("selected_character_id") or "")
        if not selected:
            return False
        for character in self._character_list(summary_context):
            if str(character.get("character_id") or "") == selected:
                return bool(character.get("is_random"))
        return False

    def _log_character_choice(self, decision: str, option_index: Any) -> None:
        """把选人界面的决定用 info 级记一次（同一决定只记一次）。

        为什么要留痕：`_debug` 在本文件里是**空函数**，玩家问"角色真随机了吗"时，
        日志里必须能看出"按了随机的那个候选"，而不是只能靠猜。
        """
        if self._logger is None:
            return
        key = f"{decision}:{option_index}"
        if key == self._character_choice_log_key:
            return
        self._character_choice_log_key = key
        try:
            self._logger.info("[sts2_character_select] decision=%s option_index=%s", decision, option_index)
        except Exception:
            pass

    def note_character_select_dispatched(self, option_index: Any) -> None:
        """服务层在**真的发出了** select_character 之后调用（用于"随机"只按一次）。"""
        if self._safe_int(option_index) is None:
            return
        self._character_pick_done = True

    #: ★ 流派标签 → 该流派"成型"所需的卡牌特征（用于把 deck 自身判成某流派）。
    #:
    #: 背景（缺陷 E）：`_reward_deck_policy` 只认 `instruction_type == "deck_policy"` 的记录，
    #: 而全仓库**没有任何地方产出**这种记录（唯一 `upsert` 点由用户自然语言指令驱动，
    #: `_DOMAINS` 里连 `deck_policy` 域都没有）⇒ `prefer_tags/avoid_tags/archetype_bias/branches`
    #: **恒为空** ⇒ `_score_reward_card` 里所有 ±35/±40/±50 的流派加权**永不触发**。
    #: 这正是用户点名的「卡牌流派的抉择」——系统里**根本没有流派机制**。
    #:
    #: 修法：不再依赖"外部指令"，而是**从当前卡组自身推断流派**
    #: （`_infer_archetype_tags` 已能识别中文卡组），
    #: 再用它生成一份"内部 deck_policy"，让既有的加权分支真正跑起来。
    _ARCHETYPE_TAG_KEYWORDS: dict[str, tuple[str, ...]] = {
        "defense": ("block", "格挡", "护盾", "护甲", "defend", "bodyguard", "护卫", "守墓人"),
        "attack": ("damage", "伤害", "attack", "strike", "打击"),
        "draw": ("draw", "抽牌", "skim", "思虑", "过牌"),
        "energy": ("energy", "能量", "储能", "turbo"),
        "strength": ("strength", "力量", "易伤", "vulnerable", "虚弱", "weak"),
        "orb_focus": ("orb", "focus", "电球", "充能", "聚焦", "宝珠"),
    }

    #: 流派"成型"阈值：该标签的卡达到这么多张，就认为卡组在走这个流派。
    _ARCHETYPE_COMMIT_COUNT = 3

    def _first_record_option(self, preferences: dict[str, Any]) -> int | None:
        records = preferences.get("records") if isinstance(preferences.get("records"), list) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            value = record.get("value") if isinstance(record.get("value"), dict) else {}
            preferred = value.get("preferred_option_index")
            normalized = self._safe_int(preferred)
            if normalized is not None:
                return normalized
        return None

    def _safe_int(self, value: Any) -> int | None:
        try:
            if value is None:
                return None
            return int(value)
        except Exception:
            return None

    # ---- 各屏候选的排序（按既有评分，从好到坏）----------------------------------------------
    #
    # 评分一律复用各屏原来的 _score_* 函数，不另写一套口径——排序的第 0 个必须就是规划器原本会选的
    # 那一个。现在的使用者是 `lesson_trace`（教训层的度量）；原先的另一个使用者「游玩模式」已删除。

    #: 任一为 False 就认为该项不可选。
    _UNSELECTABLE_FLAGS = ("playable", "enabled", "is_enabled", "affordable", "can_afford")

    #: 用 _score_remove_card 排名的选牌屏（"移除/删牌"）。其它选牌屏（升级/变换/附魔）没有对应评分，
    #: 走通用枚举。
    _REMOVE_SELECTION_SCREENS = {"card_selection_remove", "card_selection_delet"}

    @classmethod
    def _item_blocked(cls, item: dict[str, Any]) -> bool:
        return any(flag in item and item.get(flag) is False for flag in cls._UNSELECTABLE_FLAGS)

    @staticmethod
    def _operation_dict(operation: Any) -> dict[str, Any]:
        if isinstance(operation, dict):
            return operation
        if hasattr(operation, "as_dict"):
            try:
                return dict(operation.as_dict())
            except Exception:
                return {}
        return {}

    @staticmethod
    def _context_parts(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        summary_context = context.get("summary_context") if isinstance(context.get("summary_context"), dict) else {}
        strategy_context = context.get("strategy_context") if isinstance(context.get("strategy_context"), dict) else {}
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        return summary_context, strategy_context, snapshot

    #: ★I2 战斗屏着力点：哪些教训 token 会改变**战斗出牌顺序**，以及怎么改。
    #:
    #: 背景（t10 基线实测）：`_indices_worth_penalizing` 对 `play_card` 返回空集，
    #: 而 32/43 的死亡发生在 COMBAT。但**天真的补法（给 play_card 加降权分支）是错的**：
    #:   * `_apply_run_lessons` 只被 `_ranked_option_indices`（候选排序，不是 `plan()` 的决策）调用，
    #:     `plan()` 的战斗路径在 solver 命中时于 :1049 直接 return，从不经过它
    #:     ⇒ 只改 `_indices_worth_penalizing` 对真实战斗决策的位移为 **0**（t1 N1，已复现）。
    #:   * `avoid` 语义是「哪张牌别出」，而战斗教训的真实语义是「**先垫格挡再输出**」——
    #:     这是**顺序**，不是**排除**。用降权表达顺序会把语义压平（t2 F2 / t1 §3.5）。
    #: 所以正确接线是：把教训转成**顺序偏好**，接进既有的战斗决策链
    #: `_plan_combat_turn_sequence:3125` / `_preferred_combat_card_index:3046`。
    #:
    #: ⚠️ 防双重计分（t1 Q2 规则 R2/R3）：
    #:   * 只在**没有任何既有指引**（solver 未命中、guidance 未给方向）时才补默认方向；
    #:   * 绝不改写 solver 已给出的第一步；
    #:   * 与 guidance 冲突时 **guidance 优先**（玩家显式指令 > 跨局教训）。
    _LESSON_COMBAT_DIRECTION: tuple[tuple[str, str], ...] = (
        # token -> 方向。顺序即优先级：先保命，再输出。
        #
        # ★t17 调查结论（**保留原语义**，不要"修"成极性分歧）：
        # 真实 10 条注入教训里有 8 条含 `['skill','attack']`（另 2 条是兜底句/纯 skill），
        # 因此这里**恒**返回 `prefer_defense`。我一度以为这是"极性锁定"缺陷，
        # 实测后确认它**语义上是对的**：这批真实反思原文写的就是
        # 「先垫够格挡再输出」「先把格挡叠够再还手」——**首步确实该防御**。
        # 所以恒取 `prefer_defense` 不是 bug，是**正确地读懂了反思**。
        #
        # 真正的机制在别处：生产形态下 `gap>0`（挨打）时，:1145 的默认兜底
        # **也会**给出 `prefer_defense` ⇒ 两者**同值** ⇒ 真实注入在该带内
        # **不可见**（冗余，非被吞）。位移只能出现在默认兜底**不触发**的带
        # （`gap<=0`，即玩家格挡已够）—— 实测那里位移 = 1。详见 `_lesson_combat_direction` 文档。
        ("skill", "prefer_defense"),
        ("attack", "prefer_attack"),
    )

    #: 每命中一条教训 token，被罚项的"降权档位"加多少。
    #:
    #: ★★ 方案 A（2026-10-19，玩家选定的修法）：把"降权"实现成**可比较的排序键**，
    #: 而不是"移到末尾"。
    #:
    #: 病因（`.dsh-artifacts/BUG-lesson-demotion-identity.md`）：
    #: 原实现是 `kept + demoted`（两个列表各自保持原相对顺序再拼接）。
    #: 当被罚集合恰好是"候选尾部连续若干项"时，拼接结果**与原序逐字相同** ⇒
    #: **恒等变换、教训完全失效**。而真实数据正落在这个失效区：
    #:     基线 = [1, 3, 0, 2]，被罚 = {0, 2}（本来就在末尾）
    #:     kept=[1,3] + demoted=[0,2] = [1,3,0,2]  ← 没变
    #:
    #: 为什么"移到末尾"这个语义本身就不对：
    #:   它无法表达"已经在末尾了，不该再动"与"该降 3 位"的区别。
    #:   一个恒等于尾部的映射，对被罚项的相对次序**没有任何信息**。
    #:
    #: 修法：给每个候选算一个**惩罚档位** `penalty`（0 = 未被罚），
    #: 然后按 `(penalty, 原始名次)` 排序。这样：
    #:   * 惩罚为 0 的项保持原相对顺序（等价于"无教训时逐字原序"）；
    #:   * 被罚项按其**惩罚强度**分档，强的更靠后；
    #:   * **已在末尾的被罚项仍是恒等** —— 但这次是**正确的恒等**：
    #:     它已经在最低档了，没有更低的地方可去（这是语义正确，不是缺陷）。
    #:
    #: ⚠️ 与旧实现的**可观测差异**（本修法的价值所在）：
    #:   旧实现在"被罚项分散"时才有效，在"被罚项集中尾部"时恒等；
    #:   新实现在**两种情况下都有效** —— 分散时能把中间项压到尾部，
    #:   集中时至少能把"更该罚的"排到"较该罚的"后面（旧实现做不到）。
    _LESSON_PENALTY_PER_TOKEN = 1

    #: 教训 token -> 商店商品的**牌组维度**。
    #:
    #: 只收"能明确对应到某一维"的 token。`elite`/`boss`/`monster`/`rest`/`treasure`
    #: 等**路线/敌人**类 token 刻意不收 —— 商店里没有对应的商品，
    #: 硬匹配只会变成噪声（与 `_EVENT_CONSEQUENCE_KEYWORDS` 的保守口径一致）。
    _LESSON_TOKEN_TO_SHOP_DIM: dict[str, tuple[str, ...]] = {
        # 「先垫格挡」「血量低优先保命叠甲」-> 缺格挡/生存
        "skill": ("defense",),
        # 「输出不够」「斩杀不了」-> 缺输出
        "attack": ("output",),
        # ★ 六维缺口新增（2026-10-19）：
        #   「几乎没有群伤牌，下局想早点拿群伤」-> 缺群伤
        #   ⇒ 商店里**没在补 aoe** 的商品被压后。
        "aoe": ("aoe",),
        # ⚠️ `quality` **刻意不映射到商店维度**。
        #
        # 我一度写了 `"quality": ("quality",)`，但实测它会造成**全罚**：
        #   * `_shop_item_dims` 只给商品标 output/defense/draw/energy/aoe，
        #     **永远不会**标 `quality`（质量不是"某张牌补的维度"，而是"牌组整体状态"）
        #   * 于是 `item_dims and not (item_dims & {"quality"})` 对**每一件**商品恒为真
        #   * 实测：`_indices_worth_penalizing({shop_cards:[打击,防御]}, "choose_shop_item", ["quality"])`
        #     -> `[0, 1]`（两件全罚）
        #
        # 全罚在加权排序里是**无操作**（所有项同时 +1 权重，相对序不变），
        # 但它会让"降权集"这个中间量失真，且一旦将来排序改成非线性就会变成真 bug。
        # ⇒ 撤掉。`quality` 的着力点在**奖励屏**（`_is_basic_card_like`，
        #   语义明确："别再拿基础牌/诅咒"），不在商店。
        #
        # 这正是全局口径「匹配不准就别动，避免把复盘变成噪声」的应用。
    }

    #: 药水/遗物**类别** -> 牌组维度。
    #:
    #: 卡牌有 `_card_effect_dims` 可直接映射，药水/遗物没有 ⇒ 需要这张表。
    #: 键取自 `_potion_category` 的返回值（aoe/defense/heal/cleanse/burst/draw/other）。
    #: `burst` 映射到 `output`（单体爆发就是输出）；`heal`/`cleanse` 不对应六维中的任何一维
    #: （六维里没有"续航"）⇒ 刻意留空，表示"这类商品在维度判据下**不动它**"。
    _POTION_CATEGORY_TO_SHOP_DIM: dict[str, tuple[str, ...]] = {
        "defense": ("defense",),
        "burst": ("output",),
        "aoe": ("aoe",),
        "draw": ("draw",),
        # heal / cleanse / other：六维里没有对应项 ⇒ 不参与降权
    }

    #: 教训 token -> **事件选项文案**里表示该后果的关键词（缺陷 C 用）。
    #:
    #: 与 `_LESSON_KEYWORD_MAP`（面向路线/选牌屏的简短词）不同：事件选项是自然语言长句，
    #: 且中英混排，所以要单列一张表。刻意保守 —— 只收"语义明确、误判代价高"的几类，
    #: 匹配不准就不动（与 `_indices_worth_penalizing` 的整体口径一致）。
    _EVENT_CONSEQUENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
        # 诅咒：永恒话题，且游戏侧已证变换期望近零（见 `_TRANSFORM_CURSE_SCORE` 注释）
        "curse": ("诅咒", "curse"),
        # ★生命**上限**损失：用户报告的核心痛点，优先级高于普通掉血
        "max_hp": ("生命上限", "最大生命", "上限", "max hp", "maximum health", "max. hp"),
        # 生命：掉血（一次性、可恢复）—— 用户报告的核心痛点
        "hp": ("失去生命", "掉血", "失去", "lose hp", "lose ", "生命值", "血量"),
        "skill": ("格挡", "防御", "护盾", "block"),
        "attack": ("伤害", "攻击", "damage", "attack"),
        # 金币：复盘里"攒钱"类教训应当能影响"花钱/得钱"的选项
        "shop": ("金币", "gold", "购买", "buy", "花费", "spend"),
        "rest": ("回复", "回血", "heal", "休息", "生命"),
        "treasure": ("遗物", "relic", "宝箱", "chest"),
        "elite": ("精英", "elite"),
    }
