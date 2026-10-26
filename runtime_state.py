from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any

# ★★★ t25/§13.2 空转检测（**纯可观测性，不改变任何决策行为**）
#
# 为什么需要它（实机证据，2026-10-19，见 CANON §13）：
#   卡死窗口 16:40:26 → 23:47:11 内，**全部现有"活性判据"都是健康的** ——
#   step_count 2977 行在涨、TICK 483、heartbeat 10796、[sts2_plan] 2090，
#   而 `record run end` = 0、`floor` 变化 = 0 ⇒ 整整 7.27 小时零推进、零落盘，
#   吃掉约 20 局有效样本（≈当前样本的 94%）。根因：现有判据全是【心跳量】
#   （"循环跑过多少 tick"），而心跳量只证活、不证推进。
#
# 本判据用【领域量】测"同一局面被反复重算"，而不用"新 turn_key 计数"当必要条件。
#
# ★★★ 2026-10-20 第二次修正（队长发现跨 run 撞值，实测复核 132/175 = 75%）：
#   turn_key 的格式是 `act:floor:screen:turn`，**不含 run_id**
#   （实测：含 turn_key 行 40,323 ；含 run_id 行 18,776 ；【同行 = 0】）。
#   ⇒ 换局后 floor=5/turn=1 会重新出现同样的 key（实测 0:2:combat:1 出现在 20 个 run）。
#   ⇒ 用"新 key == 0"当必要条件有歧义：按全局首见计 ⇒ 同 floor 反复换局会**假卡死**；
#     按窗内首见计 ⇒ 换局后旧 key **假新** ⇒ 漏报。
#   ⇒ 因此【彻底不用"新 key"当判据】，真正必要的判据是"同一局面反复重算"本身。
#   ★ 该修正的实测收益（.dsh-artifacts/_t25_recompute_final.log，745 个 10 分钟窗）：
#       老判据（无新 key + 心跳涨）判空转 = 549 窗（其中 334 窗是活跃期误报）
#       新判据（占比>=0.9 + 时长足 + 样本足 + 心跳涨）= 215 窗（占比恒 1.00、种类恒 1）
#     ⇒ 消除 334 个误报，且**真卡死窗一个不漏**（215 窗全部落在 19:39~23:13 真卡死区间）。
#
# ★★ 2026-10-20 第三次修正（队长在 :429 发现"形参恒真"，我已独立复现）：**删净该维**。
#   中间过渡版曾保留 `require_no_new_key` 形参 + `condition_no_new_key = condition_duration`，
#   但 duration 本就是必要项 ⇒ `stalled and condition_no_new_key` **恒等于 stalled**
#   ⇒ 形参在任何输入下都不改变结果（实测 require=False/True 均为 True）。
#   ⇒ 按 §11.7 同族处理：**不留"看起来实现了、实则恒真"的形参**（比没有更危险）。
#   注意：`new_key` 字段**仍然保留**，但它现在只承担一件事 ——
#   见新局面时重置窗口（这是正确的失败方向：有推进就不该判空转）。
#   它**不再出现在 `conditions` 里**，因为它不是判据的一部分。
#
# ★ 判据共【四条必要条件】，每条都在 `note_turn_key_sample` 里算成**具名量**并放进返回值的
#   `conditions`，可逐条核对：
#   ① dominance        —— 窗内最高频 turn_key 占比 >= STALL_DOMINANCE_RATIO（默认 0.9）
#                          ★ 这就是空转的定义"同一局面反复重算"的字面读数；
#                          ★ 它**不依赖跨 run 语义**（不需要知道那局面是新是旧）。
#   ② duration         —— 窗口已持续 >= STALL_WINDOW_SECONDS（默认 10 分钟）
#                          （必须独立存在：只看瞬时占比时，**刚开窗单一 key 天然占比 1.00**
#                            ⇒ 会立刻误报；实测第 1 个 tick 后 18 秒就触发过）。
#   ③ enough_samples   —— 窗内样本数 >= STALL_MIN_SAMPLES（样本不足不判，宁漏报不误报）；
#   ④ heartbeat_growing—— 【且】heartbeat 在涨。
#   缺 ④ 就与【进程死亡】不可区分：进程死了 heartbeat 不涨、局面也不动，
#   那不是空转，是"通道没了"（§9.42 同族）。所以 heartbeat 不涨时**不得**告警。
#
# ★ 为什么 ① 的判别力成立（实测，.dsh-artifacts/_t25_separation.log）：
#        活跃期零新增窗口：窗内去重种类 2~37，最高频 key 占比 <= 0.875
#        真卡死窗口      ：种类 1，占比恒 1.00（16:52~23:49）
#   ⇒ 两族在占比轴上【零重叠】，边界差 0.125。
STALL_WINDOW_SECONDS = 600.0
STALL_DOMINANCE_RATIO = 0.9
# 触发判据所需的最少 turn_key 采样行数。低于它说明样本不足以判定，
# 宁可漏报也不误报（例如刚 reload 完、日志刚滚动）。
STALL_MIN_SAMPLES = 20


@dataclass(slots=True)
class STS2RuntimeState:
    base_url: str = "http://127.0.0.1:18080"
    # 当前活动实例：插件驱动的是"哪一个游戏进程"。
    #   "host"    —— 配置里的 base_url（默认 18080）。单人局就是它；coop 里它是主机。
    #   "catgirl" —— coop 里被 mod 拉起的猫娘进程（地址由主机 /config.coop_client_port 推导）。
    # 见 service.switch_active_instance / adopt_coop_catgirl。
    active_instance: str = "host"
    catgirl_base_url: str = ""
    transport_state: str = "disconnected"
    last_error: str = ""
    snapshot: dict[str, Any] = field(default_factory=dict)
    raw_state: dict[str, Any] = field(default_factory=dict)
    raw_actions: dict[str, Any] = field(default_factory=dict)
    control_mode: str = "program"
    # 游玩模式：serious（默认，零影响）/ mischievous / silly。见 play_style.py。
    play_style: str = "serious"
    autoplay_state: str = "idle"
    # 进 standby（待机）之前的**意图**，退出待机时还回去。
    # 以前这里没有记忆，退出待机直接写死 `idle` —— 而自动玩循环只认 `running`，
    # 于是"取消待机"等于把自动游玩永久停摆（2026-10-07 实测 step 冻住不再增长）。
    autoplay_state_before_standby: str = ""
    # 「卡住」检测：拿不到可执行动作时**别每 0.5 秒静默空转**。
    # 2026-10-07 实测空转了 2 小时 40 分、日志里一个字都没有（水晶球事件的占卜子界面，mod 只暴露
    # save_and_quit）。`stuck_signature` 变了就重新计时，`stuck_reported` 保证同一处只报一次。
    stuck_signature: str = ""
    stuck_since: float = 0.0
    stuck_reported: bool = False
    stuck_reason: str = ""
    # 「无进展」检测：动作发出去了、mod 也回 ok，但**局面指纹没变** = 这个动作其实没生效。
    # 2026-10-07 实测：水晶球事件屏上插件每 2-3 秒重发一次 choose_event_option（step 83→93），
    # 金币/选项/局面一字未变。这种情况"拿不到动作"那道闸拦不住，得有第二道闸。
    progress_action_key: str = ""
    progress_signature: str = ""
    progress_repeat: int = 0
    progress_reported: bool = False
    # 自动占卜/揭图块的游标：自定义事件屏（水晶球）上**每次派发都推进下标**，扫遍所有图块。
    # 2026-10-07 实测：死盯一个下标会空点（点 [3] 无变化、点 [4] 才 completed）。
    event_option_cursor: int = 0
    event_option_screen: str = ""
    stop_reason: str = ""
    pause_reason: str = ""
    step_count: int = 0
    consecutive_errors: int = 0
    # Incremented each time the scene-change event stream (GET /events/stream) drops/reconnects.
    sse_reconnect_count: int = 0
    last_poll_at: float = 0.0
    last_action_at: float = 0.0
    last_sync_at: float = 0.0
    last_success_at: float = 0.0
    last_summary: str = ""
    last_decision_source: str = ""
    last_decision_reason: str = ""
    last_planner_type: str = ""
    # /solver/plan 当前一步若是需要"选一张牌"的动作（消耗/拉弃牌堆/生成到手/变换等），solver 选定的
    # 那张 card_id；在随后出现的选牌界面（combat_hand_select 等）据此选牌，避免卡住或用启发式乱猜。
    # 无选择动作为 None。承载的是"下一步选择"的选中牌，不限于消耗。
    pending_card_choice_id: str | None = None
    # 战斗自动玩：当前回合缓存的 /solver/plan line[0].steps 里下一个要执行的步骤下标。
    # 回合开始取一次计划后整回合复用；每执行一个战斗步（play_card/use_potion/end_turn）自增，
    # 选牌子动作（select_deck_card）不步进；接近回合末尾/耗尽后重查下一回合并归零。
    solver_step_index: int = 0
    # ★★★ t46：「等待求解器」必须是**可观测的状态**，不能靠日志里那个 `in_combat=None` 去猜。
    #
    # 背景（两起实机故障，同一句话"自动玩开着但一张牌不出"）：
    #   ① 装错 mod（对端没有 /solver/plan 路由）⇒ 每次 0 ms 回 404；
    #   ② 求解器预算 120 s + 一轮 120 s 补充审计，而客户端 15 s 读超时 ⇒ 每次都超时。
    #   两起在旧实现里都退化成同一个日志形态：`in_combat=None` + 闸门 `waiting_solver`，
    #   而"等待"按设计不计死锁/不计过期、也不降级 ⇒ 静默等到玩家自己发现。
    #
    # 现在的口径（玩家要求）：
    #   · `/solver/plan` **不设读超时** —— 求解器算多久就等多久，绝不因为等不及而退级策略
    #     （退级会让"本来能过的战斗过不了"，这是比卡住更坏的错）。
    #   · 于是必须有下面这几个字段，把"还在算 / 算完了 / 请求本身失败了"三件事分开：
    #       computing    —— 请求在飞，正在等（唯一允许无声等待的状态）
    #       ready        —— 拿到可用计划（in_combat=True 且 search_status 合法）
    #       waiting_phase—— 拿到了载荷，但 mod 说还没到玩家出牌阶段（正常，继续等）
    #       failed       —— 请求本身失败（404/连接断/非 200）：等下去**永远不会好**，必须喊出来
    #       idle         —— 本 tick 没取（不在战斗决策态）
    solver_status: str = "idle"
    # 本次等待的起点（time()）；`computing` 期间用它算"已等 N 秒"。
    solver_wait_started_at: float = 0.0
    # 最近一次等待的**总耗时**（秒，结束后定格；computing 期间为 0）。
    solver_last_elapsed_seconds: float = 0.0
    # 最近一次成功拿到计划的时刻（time()），用于"这份结果有多新"。
    solver_last_result_at: float = 0.0
    # 最近一次请求失败的可诊断文本（`类型: 消息`），成功即清空。
    solver_last_error: str = ""
    # 计划自报的 search_status 原值（complete / budget_exceeded / boundary_*）。
    solver_last_search_status: str = ""
    # 累计"进入等待"的次数（单飞合并会让它小于 HTTP 请求数）：日志里自报，别靠数行。
    solver_wait_count: int = 0
    # ★★ t20/D9 修复：「派发成功」已被收紧为「mod 回 completed，或局面确实变了」。
    #   还有效的派发在 mod 回 `status="pending"` 且局面指纹未变时**不再推进**
    #   `solver_step_index`（原实现无条件 +1 ⇒ 步骤被空耗 ⇒ 步骤用尽 ⇒ 重取 ⇒
    #   求解器又给同一建议 ⇒ 无限循环。实测 run22 X9XP0L8HZWF7 循环 7347 次 / 5.88 小时）。
    #   下面三个字段实现「同一死锁指纹下连续 N 次 pending 且未变 ⇒ 转 end_turn 破局」：
    #     invalid_action_signature —— 上一次「pending 且局面未变」时的局面指纹；
    #     invalid_action_count     —— 同一指纹下连续发生次数（指纹一变即清零）；
    #     last_action_status       —— 最近一次派发的 mod 侧 status（可观测性）。
    invalid_action_signature: str = ""
    invalid_action_count: int = 0
    last_action_status: str = ""
    # ★ t20/D9 可观测性：同一 turn_key 下连续几次 `use_potion` 被派发而药水数量未减少。
    #   连续 3 次 ⇒ 发一条明确告警（不静默）。药水数量变化时清零。
    potion_no_effect_turn_key: str = ""
    potion_no_effect_count: int = 0
    potion_no_effect_warned: bool = False
    # 战斗中用药水的记账（规格 docs/shop_scoring.md 「八」，玩家口径 2026-10-07：
    # 「还有每次对战如果损血大于8就鼓励使用药水」）：
    #   potion_used_turn_key —— 本回合是否已经喝过一瓶（"同一回合只喝一瓶"，避免连灌）。
    #     键是"本局|层|房间|回合"：只看回合号不够，两场战斗的回合 1 会互相顶掉。
    #   potion_decision_log_key —— 上一句 [sts2_potion] 决策日志的去重键。自动玩每 2~3 秒一步、
    #     规划每秒一次，不去重就是刷屏（"不喝"的那些行尤其烦）。
    potion_used_turn_key: str = ""
    potion_decision_log_key: str = ""
    latest_sync_packet: dict[str, Any] = field(default_factory=dict)
    latest_report_packet: dict[str, Any] = field(default_factory=dict)
    latest_snapshot_summary: dict[str, Any] = field(default_factory=dict)
    latest_continuous_delta: dict[str, Any] = field(default_factory=dict)
    latest_action_frame: dict[str, Any] = field(default_factory=dict)
    latest_player_operation_observation: dict[str, Any] = field(default_factory=dict)
    recent_action_frames: list[dict[str, Any]] = field(default_factory=list)
    recent_continuous_deltas: list[dict[str, Any]] = field(default_factory=list)
    recent_decision_memory: list[dict[str, Any]] = field(default_factory=list)
    recent_companion_player_ops: list[dict[str, Any]] = field(default_factory=list)
    run_intent: dict[str, Any] = field(default_factory=dict)
    recent_deliveries: list[dict[str, Any]] = field(default_factory=list)
    last_companion_turn_key: str = ""
    last_companion_scene_key: str = ""
    last_companion_evaluation_key: str = ""
    last_companion_combat_comment_key: str = ""
    last_companion_player_op_fingerprint: str = ""
    last_companion_player_op_at: float = 0.0
    last_plugin_action_fingerprint: str = ""
    snapshot_signature: str = ""
    pending_guidance: list[dict[str, Any]] = field(default_factory=list)
    guidance_generation: int = 0
    last_consumed_guidance_generation: int = 0
    last_decision_generation: int = 0
    interrupt_requested: bool = False
    interrupt_reason: str = ""
    # 上一次自动玩动作被 mod 拒收（状态漂移类）时置位：说明手里的现场/计划已经过期，
    # 下一轮必须先强制 refresh_state 重算再动手。用完即清（见 service.run_autoplay_step）。
    force_replan_requested: bool = False
    last_sync_fingerprint: str = ""
    last_sync_screen: str = ""
    last_sync_summary_kind: str = ""
    last_sync_reason: str = ""
    last_push_scene_key: str = ""
    last_push_reason: str = ""
    last_push_step_count: int = -1
    last_push_at: float = 0.0
    sync_repeat_count: int = 0
    # 弹幕双线的记账：即时短句按"场景|层|回合"去重；给本体的局势摘要按时间节流。
    last_instant_line_key: str = ""
    last_host_digest_at: float = 0.0
    # 最近一次交给本体的局势速报对应的场景键（同一局面只讲一次）。
    last_host_digest_key: str = ""
    # ★★★ t51：「弹幕关着 ⇒ 点评改走宿主（猫娘在聊天里说）」的记账。
    #   与上面的 `last_host_digest_*` **刻意分开**：速报是"局势播报"，点评是"这一手的想法"，
    #   两者各自节流，不能互相顶掉（否则一边讲了另一边就哑）。
    #   为什么必须节流：`push_message(ai_behavior="respond")` 每推一次都会让本体**立刻开一次口**
    #   （一次完整 AI 回合，有 token 成本）—— 弹幕是"免费刷屏"，聊天不是。
    last_host_commentary_at: float = 0.0
    last_host_commentary_key: str = ""
    # 本局生命轨迹（每次刷新追加一条），局末总结时用来做"哪里损血"的归因。
    hp_trail: list[dict[str, Any]] = field(default_factory=list)
    hp_trail_run_id: str = ""
    # 当前这场战斗的"房间标识"（遭遇开始时的敌人组合）。战斗结束清零。
    combat_room_id: str = ""
    # 本局**路线图**：按访问顺序记每个房间的损血与收获。
    #   route_room_key  当前房间的键（层|界面|房间标识），换房间时收尾上一段
    #   route_start     进入房间时的基线（血量/金币/牌组/遗物/药水），用来算"收获"
    #   route_last      该房间的最新观测值
    route_log: list[dict[str, Any]] = field(default_factory=list)
    route_room_key: str = ""
    route_start: dict[str, Any] = field(default_factory=dict)
    route_last: dict[str, Any] = field(default_factory=dict)
    # 本局已经公告过的角色（开局后报一次"我玩的谁"）。
    announced_character_run_id: str = ""
    # 本局已记录的历史条目 id（避免同一局重复写历史）。
    last_history_run_id: str = ""
    # 是否正因"局末复盘"而暂停自动游玩（复盘完要恢复；玩家自己动过就不擅自恢复）。
    reflection_paused: bool = False
    # 两个"跟本体说话"的开关（玩家口径：给尖塔速报和尖塔复盘各一个开关）。
    #   host_digest_enabled     尖塔速报：把局势悄悄交给本体，让它自己主动讲一句
    #   run_reflection_enabled  尖塔复盘：局末生成反思并推给本体
    # 关掉只影响"跟本体说话"这一路；结构化记录（损血归因/路线图/历史页）照旧写。
    host_digest_enabled: bool = True
    run_reflection_enabled: bool = True
    # 上次已把哪一局写入猫娘记忆（避免 terminal 屏反复触发时重复总结同一局）。
    last_memory_run_id: str = ""

    # ── ★★★ t25/§13.2 空转检测：全局累积的【新 turn_key】记账 ────────────────────
    # 只记观测，不参与任何决策。
    #
    # ★★★ 2026-10-20 修正（队长发现，实测复核 132/175 = 75% 跨 run 撞值）：
    #   turn_key 的格式是 `act:floor:screen:turn`，**不含 run_id**
    #   （实测：含 turn_key 行 40,323 ；含 run_id 行 18,776 ；【同行 = 0】）。
    #   ⇒ 换局后 floor=5/turn=1 会**重新出现同样的 key**（实测 0:2:combat:1 出现在 20 个 run）。
    #   ⇒ 用【全局首见集合】判"新 key"有歧义：
    #       · 同一 floor 反复换局 ⇒ 旧 key 永不"新" ⇒ **假卡死**；
    #       · 若改成"窗内首次出现" ⇒ 换局后旧 key 又"假新" ⇒ **漏报**。
    #   ⇒ 因此本判据**不把"新 key"当作必要条件**（见下面 `condition_dominance` 是必需项）。
    #     "新 key"降级为【可选加强条件】，且必须带 run 边界才有意义（见 run_id 字段）。
    #
    #   ★ 为什么"占比"这一维不依赖跨 run 语义：它就是空转的定义
    #     「同一局面反复重算」的**字面读数** —— 10 分钟窗内只有一个 turn_key 出现过
    #     且占满全部样本。这不需要知道那局面是"新"还是"旧"。
    #     实测：真卡死窗（16:52~23:49）种类恒 1、占比恒 1.00；
    #           活跃期零新增窗（12:28~16:51）种类 2~37、占比 0.05~0.875
    #           ⇒ 边界差 0.125，两族零重叠。
    #
    #   stall_seen_turn_keys    —— 见过的 turn_key（喂了 run_id 时圈定在**同 run 内**）。
    #                              ★ 只用于"见新局面 ⇒ 重置窗口"，**不参与**判据。
    #   last_new_turn_key_at    —— 最近一次出现**新** turn_key 的时刻（纯记账）。
    #   stall_window_started_at —— 当前窗口的起点（见新局面时重置）。
    #   last_stall_heartbeat    —— 上一 tick 的 heartbeat 计数，用来判"heartbeat 是否在涨"。
    #   stall_reported          —— 当前窗口内是否已经告警（同一窗口只报一次，不刷屏）。
    stall_seen_turn_keys: set[str] = field(default_factory=set)
    stall_last_new_turn_key_at: float = 0.0
    stall_window_started_at: float = 0.0
    stall_last_heartbeat: int = -1
    stall_heartbeat_growth_count: int = 0
    stall_reported: bool = False
    stall_reported_window_seconds: float = 0.0
    stall_top_key_share: float = 0.0
    stall_distinct_keys: int = 0
    stall_new_key_count: int = 0
    # 窗口起点的"绝对秒"（= 首次采样的 now）。占比判据只用本地窗口，
    # 不依赖任何跨 run 语义。
    stall_window_anchor: float = 0.0
    # ★ run 边界：turn_key 不含 run_id，所以必须**另行**喂进来才能做同 run 判定。
    #   实测 run_id 出现在 "Plugin sts2_autoplay status updated" 行（STATUS_UPDATE，
    #   18,611 行）上，与 turn_key **从不同行**出现 ⇒ 调用方需要把两者分别喂进来。
    #   本字段只用于"新 key"这条**加强**条件；占比维不读它。
    stall_run_id: str = ""
    # 滑动窗内部记账：key -> 最近一次出现时刻；key -> 窗内出现次数。
    stall_sample_at: dict[str, float] = field(default_factory=dict)
    stall_sample_counts: dict[str, int] = field(default_factory=dict)

    @property
    def standby(self) -> bool:
        return self.control_mode == "standby"

    @property
    def guidance_queue_size(self) -> int:
        return len(self.pending_guidance)

    def touch_poll(self) -> None:
        self.last_poll_at = time()

    def touch_action(self) -> None:
        self.last_action_at = time()

    def touch_sync(self) -> None:
        self.last_sync_at = time()

    def touch_success(self) -> None:
        self.last_success_at = time()
        self.consecutive_errors = 0

    def remember_delivery(self, delivery: dict[str, Any]) -> None:
        self.recent_deliveries.append(dict(delivery))
        self.recent_deliveries = self.recent_deliveries[-20:]

    def remember_action_frame(self, frame: dict[str, Any]) -> None:
        self.latest_action_frame = dict(frame)
        self.recent_action_frames.append(dict(frame))
        self.recent_action_frames = self.recent_action_frames[-20:]

    def remember_continuous_delta(self, delta: dict[str, Any]) -> None:
        self.latest_continuous_delta = dict(delta)
        self.recent_continuous_deltas.append(dict(delta))
        self.recent_continuous_deltas = self.recent_continuous_deltas[-20:]

    def remember_decision_memory(self, item: dict[str, Any]) -> None:
        self.recent_decision_memory.append(dict(item))
        self.recent_decision_memory = self.recent_decision_memory[-20:]

    def remember_companion_player_op(self, observation: dict[str, Any]) -> None:
        self.latest_player_operation_observation = dict(observation)
        self.last_companion_player_op_fingerprint = str(observation.get("fingerprint") or "")
        self.last_companion_player_op_at = time()
        self.recent_companion_player_ops.append(dict(observation))
        self.recent_companion_player_ops = self.recent_companion_player_ops[-20:]

    def note_companion_player_op_comment(self, fingerprint: str) -> None:
        self.last_companion_player_op_fingerprint = str(fingerprint or "")
        self.last_companion_player_op_at = time()

    # ── ★★★ t25/§13.2 空转检测 ───────────────────────────────────────────────────
    #
    # 观测口径（全部只读，返回值只用于决定"要不要打一条告警日志"）：
    #   ① 【必要】窗内最高频 turn_key 占比 >= STALL_DOMINANCE_RATIO（同一局面反复重算）；
    #   ② 【必要】窗内样本数 >= STALL_MIN_SAMPLES（样本不足不判，宁漏报不误报）；
    #   ③ 【必要】heartbeat 在涨；
    #   ④ 【可选加强】同 run 内无新 turn_key。
    #
    # ★ 为什么 ③ 必须【同时】要 heartbeat 在涨：否则与「进程死亡」不可区分。
    #   进程死了 heartbeat 不涨、局面也不动 —— 那是通道失效（§9.42），不是空转。
    #
    # ★ 为什么 ① 是**必要项**、而 ④ 只是加强项（2026-10-20 修正）：
    #   turn_key 格式 `act:floor:screen:turn` **不含 run_id** ⇒ 跨 run 会撞值
    #   （实测 132/175 = 75% 撞值；0:2:combat:1 出现在 20 个 run）。
    #   于是"新 key == 0"这一步有歧义：按全局首见计 ⇒ 同 floor 反复换局会**假卡死**；
    #   按窗内首见计 ⇒ 换局后旧 key 会**假新** ⇒ 漏报。
    #   ⇒ 收紧为：**只要 ①（占比）+ ② + ③ 成立就判空转**，不看"新不新"。
    #     因为 ① 就是"同一局面反复重算"的字面读数，**不需要知道那局面是新是旧**。
    #     ① 的实测判别力：真卡死窗 种类=1、占比=1.00；活跃期零新增窗 种类 2~37、占比≤0.875
    #     ⇒ 边界差 0.125、零重叠 ⇒ 不依赖跨 run 语义也能完全分开。
    #   ⇒ "无新 key"【已按 §11.7 删净】（见下面判据处的第三次修正说明）。
    def note_turn_key_sample(
        self,
        turn_key: str,
        *,
        heartbeat: int | None = None,
        now: float | None = None,
        dominance_ratio: float = STALL_DOMINANCE_RATIO,
        window_seconds: float = STALL_WINDOW_SECONDS,
        min_samples: int = STALL_MIN_SAMPLES,
        run_id: str = "",
    ) -> dict[str, Any]:
        """记一条 turn_key 采样，并返回本 tick 的空转判定（**纯观测**，不改任何决策状态）。

        参数：
          ``run_id`` —— 当前 run（可选）。turn_key 不含 run_id、跨 run 会撞值（实测 75%），
                        所以必须另行喂进来才能把"已见集合"圈在**同 run 内**。
                        不喂 ⇒ 退化为跨 run 全局集合（换局后旧 key 永不"新"，窗口不再被重置）。

        返回 dict 字段：
          ``new_key``          —— 本次是否见到新的 turn_key（★ 只用于"窗口是否被重置"的记账，
                                   **不参与** stalled 判定）；
          ``stalled``          —— 本次是否判为「空转」；
          ``zero_new_seconds`` —— 当前窗口已持续多少秒；
          ``heartbeat_growing``—— heartbeat 是否在涨（False ⇒ 即使窗口满了也【不】判空转）；
          ``top_key_share``    —— 窗口内最高频 turn_key 的占比（★ 核心判据量）；
          ``distinct_keys``    —— 窗口内出现过的不同 turn_key 数；
          ``repeats``          —— 窗口内最高频 key 的出现次数（告警文案用）；
          ``should_warn``      —— 是否应当在本 tick 打告警（同一窗口只 True 一次）；
          ``conditions``       —— 各分项明细，可逐条核对。
        """
        t = time() if now is None else float(now)
        key = str(turn_key or "")

        # ★ run 变化 ⇒ "已见集合"必须重置：否则同一 floor 换局后旧 key 永不"新"，
        #   会假卡死（这正是 75% 撞值带来的歧义）。只在调用方**确实**喂了 run_id 时做。
        if run_id and run_id != self.stall_run_id:
            self.stall_run_id = str(run_id)
            self.stall_seen_turn_keys.clear()

        new_key = False
        if key and key not in self.stall_seen_turn_keys:
            self.stall_seen_turn_keys.add(key)
            self.stall_new_key_count += 1
            new_key = True
            # 见到新局面 ⇒ 窗口重置。
            self.stall_last_new_turn_key_at = t
            self.stall_window_started_at = t
            self.stall_reported = False
            self.stall_sample_at.clear()
            self.stall_sample_counts.clear()
        elif self.stall_window_started_at <= 0.0:
            # 首个采样（即使不是新 key）：把窗口起点定在这里，避免用 0.0 算出天文数字时长。
            self.stall_window_started_at = t
            self.stall_last_new_turn_key_at = t

        # heartbeat 涨落：None 表示本 tick 没拿到心跳数（不参与判定）。
        heartbeat_growing = False
        if heartbeat is not None:
            hb = int(heartbeat)
            if self.stall_last_heartbeat >= 0 and hb > self.stall_last_heartbeat:
                self.stall_heartbeat_growth_count += 1
                heartbeat_growing = True
            self.stall_last_heartbeat = hb

        # 滑动窗：只保留 window_seconds 内的采样，用来算「最高频 key 占比」。
        # （纯记账。判据只看"新增 key 数"与"heartbeat 涨落"之外的这一维，
        #   它把"跑图期大量旧局面交替"与"真卡死：同一局面反复重算"分开。）
        samples = self.stall_sample_at
        counts = self.stall_sample_counts
        cutoff = t - float(window_seconds)
        for sample_key in [k for k, ts in samples.items() if ts <= cutoff]:
            samples.pop(sample_key, None)
            counts.pop(sample_key, None)
        if key:
            counts[key] = counts.get(key, 0) + 1
            samples[key] = t
        total = sum(counts.values())
        top_share = (max(counts.values()) / total) if total else 0.0
        distinct = len(counts)

        zero_new_seconds = max(0.0, t - self.stall_window_started_at)
        # ★ 判据的分项各自算成**具名量**再合取 —— 每一条都能在代码里逐条查到：
        #     condition_dominance   ← 【必要】窗内最高频 key 占比 >= 0.9
        #                              （"同一局面反复重算"的字面读数，不依赖跨 run 语义）
        #     condition_duration    ← 【必要】窗口已持续 >= window_seconds
        #                              ★ 它必须独立存在：
        #                                "占比高"若只看瞬时，则**刚开窗时单一 key 天然占比 1.00**
        #                                ⇒ 会立刻误报（实测第 1 个 tick 后 18 秒就触发）。
        #                                所以"时长"是【占比维的配套必要条件】，不是谁的附属品。
        #     condition_enough      ← 【必要】样本数够（不足不判，宁漏报不误报）
        #     condition_heartbeat   ← 【必要】heartbeat 在涨（否则与「进程死亡」不可区分）
        #
        # ★ 2026-10-20 第三次修正（队长在 :429 发现，我已独立复现）：**删净"无新 key"维**。
        #   原实现写了 `condition_no_new_key = condition_duration`，于是：
        #       require_no_new_key=True ⇒ stalled and condition_no_new_key
        #                               ≡ stalled and condition_duration
        #       而 duration 在下面已是必要项 ⇒ 该合取**恒为真** ⇒ 形参【完全无区分力】。
        #   实测（require=False vs True 在真卡死形状下）：stalled 均为 True，无差别。
        #   ⇒ 按 §11.7 同族处理：**不留"看起来实现了、实则恒真"的形参**（比没有更危险，
        #     会让人以为有这层保护）。
        #   ⇒ 语义上也确实多余：本条判据的定义是"同一局面反复重算"，
        #     它**不需要知道那个局面是新还是旧**（占比维不依赖跨 run 语义，见文件头）。
        condition_dominance = top_share >= float(dominance_ratio)
        condition_duration = zero_new_seconds >= float(window_seconds)
        condition_enough = total >= int(min_samples)
        condition_heartbeat = heartbeat_growing

        stalled = bool(
            condition_dominance and condition_duration and condition_enough and condition_heartbeat
        )

        # 分项明细：供日志/测试逐条核对（哪一条不满足一眼可见）。
        conditions = {
            "duration": condition_duration,
            "dominance": condition_dominance,
            "enough_samples": condition_enough,
            "heartbeat_growing": condition_heartbeat,
        }

        self.stall_top_key_share = top_share
        self.stall_distinct_keys = distinct
        top_repeats = max(counts.values()) if counts else 0

        should_warn = False
        if stalled and not self.stall_reported:
            self.stall_reported = True
            self.stall_reported_window_seconds = zero_new_seconds
            should_warn = True
        if not stalled:
            # 判据不再成立（局面动了 / 样本不足 / 心跳停）⇒ 复位告警标志，下次能再报。
            self.stall_reported = False

        return {
            "new_key": new_key,
            "stalled": stalled,
            "should_warn": should_warn,
            "zero_new_seconds": zero_new_seconds,
            "heartbeat_growing": heartbeat_growing,
            "top_key_share": top_share,
            "distinct_keys": distinct,
            "repeats": top_repeats,
            "samples": total,
            "window_seconds": float(window_seconds),
            "run_id": self.stall_run_id,
            "conditions": conditions,
        }


__all__ = [
    "STS2RuntimeState",
    "STALL_WINDOW_SECONDS",
    "STALL_DOMINANCE_RATIO",
    "STALL_MIN_SAMPLES",
]
