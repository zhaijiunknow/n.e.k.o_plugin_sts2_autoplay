# 插件部分（Python · NEKO 插件）

**目录**：仓库根目录（Python 包），`plugin.toml` 声明入口 `STS2AutoplayPlugin`。

本插件负责**决策与表达**：给尖塔2的猫娘陪玩提供自动出牌/走图、局势点评、事件房 LLM 打分、游戏内弹幕，以及跨局记忆沉淀。它通过 Mod 的 HTTP API（`/state`、`/events/stream`、`/action`、`/solver/plan`、`/danmaku`）与游戏交互，自身**不读取** `strategies/` md——那是策略内容，交给 `strategy_repository` 解析。

## 入口 actions（`__init__.py`）

- **状态**：`sts2_health_check`、`sts2_get_status`、`sts2_read_state`
- **自动玩**：`sts2_start_autoplay`、`sts2_pause_autoplay`、`sts2_resume_autoplay`、`sts2_stop_autoplay`、`sts2_set_standby`
- **陪玩**：`sts2_enable_companion_mode`、`sts2_disable_companion_mode`
- **弹幕**：`sts2_set_danmaku`（`enabled=true/false`，真正的总开关，关掉同时通知 mod 停止渲染并清屏）
- **存档**：`sts2_continue_run`（回主菜单点「继续」回到存档；不可用时回报当前界面与可用动作）
- **co-op**：`sts2_open_coop_room`
- **策略**：`sts2_apply_user_override`
- **规划**：`sts2_get_planned_operation`、`sts2_execute_planned_operation`

## 核心模块（按职责）

| 模块 | 职责 |
|---|---|
| `service.py` | 总控：事件循环接线、决策/点评/记忆入口 |
| `loop_runner.py` | 轮询 + SSE 事件流 + 自动玩循环；缓存 `/solver/plan`（整回合）、事件房 LLM |
| `transport_client.py` | Mod HTTP 客户端（`/state`、`/action`、`/solver/plan`、`/danmaku`、SSE） |
| `neko_interface.py` | NEKO SDK 接口（只读/控制 SD 工具入口） |
| `heuristic_planner.py` / `companion_evaluator.py` | 决策：局面评估、出牌/选牌/路线启发式 + 点评触发器 |
| `catgirl_llm.py` | LLM 生成：`CatgirlCommentGenerator`（弹幕/点评**文案**）。只有文案，没有决策 |
| `play_style.py` | 游玩模式（认真/捣蛋/笨蛋）的档位常量、别名与"从有序候选里挑一个"的纯函数 |
| `catgirl_memory.py` | 跨局记忆沉淀（见下节） |
| `strategy_repository.py` / `strategy_parser.py` | 策略加载：`strategies/` 解析 + `strategy_directives` 组装 |
| `dispatcher.py` | 状态反馈 / 陪玩通知的 NEKO 输出边界 |

## 决策与表达

- **决策**：地图/奖励/卡组/事件全部由**启发式**决定——插件不靠 LLM 做任何决策（事件房原本的 LLM 评分融合已于 2026-10 摘除）；战斗出牌由 `/solver/plan` 权威决定，回退启发式。
- **多张选牌**：选牌屏需要选 2 张以上时，插件与猫娘驱动都按「这一屏**没按过**的下一张」推进（记住已按过的 option_index 集合，换屏即重置），能确认就优先 `confirm_selection`。以前两边都恒发 `option_index=0`，第二张永远选不上、`max_select` 到不了，界面就卡在那里。
- **其它非战斗选择**：`bundle_selection`（先选后确认）、`capstone_selection`（逐项扫，不重复按同一按钮，index 0 可能是返回键）、`cards_view`（关闭）、`modal`（先 dismiss 再 confirm）、`game_over`（回主菜单）都已覆盖，不再落进 `unknown` 后静默卡住。
- **奖励**：卡牌奖励屏优先**拿牌**（以前 `resolve_rewards` 排在前面，等于把每张奖励卡都白白 drain 掉）。
- **已知未覆盖**：出战斗外的**选遗物**屏（`NChooseARelicSelection`，如珠宝盒）mod 侧没有对应动作，需要人工点。
- **表达**：猫娘点评 + 游戏内弹幕。
- **偏好吸收**：`sts2_apply_user_override` 把用户自然语言指点转成结构化偏好 / override。
- **跨局记忆**：见下节。

## 猫娘记忆成长（跨局教训 → 决策）

每局结束（死亡/通关，`state_machine` 判为 `screen_class=terminal`）时，插件把本局 `recent_decision_memory` 总结成「教训 + 偏好」，写入 `catgirl_memory.py` 的 `CatgirlMemoryStore`，持久化到 `<LOCALAPPDATA>/N.E.K.O/sts2_autoplay/catgirl_memory.json`。下一局 `strategy_repository.build_context()` 会把最近几局的教训并入 `strategy_directives.avoid`、偏好并入 `prefer`（软约束），从而影响选牌/路线/战斗决策。总结引擎为**启发式打底 + LLM 可选**（`service._catgirl_llm.available` 时才叠加，失败静默回退）。

## 弹幕/点评

- 猫娘点评经插件 `_maybe_emit_catgirl_llm` → `_push_catgirl` → `POST /danmaku`（文本 + 头像）进游戏内渲染；评论不再走 NEKO 宿主 `push_message`。
- 战斗时给 LLM 的 prompt 精简为「怪物血量 + 我方生命 + 当前层级」；仅 SSE `event_type == player_action_window_opened`（轮到玩家行动）才附当前回合出牌建议 line（`_build_combat_prompt`，复用 `loop_runner._solver_plan_cached`，签名命中不重复搜 `/solver/plan`）。
- 弹幕只响应三个战斗事件：`combat_started` / `available_actions_changed` / `combat_turn_changed`，其中仅 `combat_turn_changed`（回合推进）带 line。

### 生成预算必须给推理模型留余量（v0.5.5 起）

`_DEFAULT_MAX_TOKENS = 1500`（原本是 **220**）。主系统默认模型 `free-agent-model` 是**推理模型**：
实测它对战斗/奖励这类需要分析的局面会把 220 个 token **全部烧在 reasoning 上**
（`completion_tokens=220`、`completion_tokens_details.reasoning_tokens=220`、`finish_reason="length"`），
最终 `message.content` 是空串 —— 于是这些场景**一次都产出不了点评**，猫娘在游戏里一句话都不说
（地图/商店/选牌偶尔成功，只是那几次思考短，赶在预算内答完了）。改成 1500 后同样两个场景
`finish_reason=stop`、content 正常（实测思考占 440~500 token，输出仍受 prompt 限在 10~25 字）。

配套两条防复发措施：

- LLM **可用但没产出**时**必须退回启发式文本**（`_catgirl_llm_async` 里原先是"什么都不推"，
  而 `_maybe_emit_catgirl_llm` 又因为 `llm_ok=True` 不会走它自己的兜底分支 —— 两条路都不出声）。
- 失败原因用 **info 级报一次**并带上 `finish_reason`（原先写在 debug 级，线上只看到
  "LLM 失败/空"，根本分不清是预算不够、被拒绝还是网络问题）。

### 怎么真正关掉弹幕（v0.3.1 起）

`catgirl_llm_enabled=false` **不是**关弹幕——它只是把 LLM 点评降级成启发式文本，弹幕照旧。要关弹幕用：

| 方式 | 说明 |
|---|---|
| `sts2_set_danmaku(enabled=false)` | 插件侧总开关：`_push_catgirl` 直接早退（不生成、不拉头像、不推），并 `POST /config {danmaku_push_enabled:false}` + `POST /danmaku/clear` 清屏 |
| `plugin.toml [sts2] danmaku_enabled = false` | 同上，随插件启动生效 |
| 游戏内 Mod 设置「允许游戏内弹幕渲染」取消勾选 | 关闭 mod 侧的 `danmaku_push_enabled`：任何本地调用者推的弹幕都不再渲染，并立即清屏 |

三个开关分工：`danmaku_enabled`（插件侧推送）、`danmaku_push_enabled`（mod 侧 overlay 渲染，唯一收口在 `DanmakuService.PushAsync`）、`catgirl_llm_enabled`（点评用 LLM 还是启发式文本）。自动游玩与状态读取都不受影响。

## 地图投票与存档（co-op 行为）

**先分清两种运行模式**，它们的地图行为**不同**：

| 模式 | 判定方式 | 地图行为 |
|---|---|---|
| **插件控制**（NEKO 已连接） | mod 收到带 `X-Neko-External-Control: 1` 的请求，且在**租约 30 秒**内 | 按 `coop_map_policy` 走（见下） |
| **mod 独自运行**（NEKO 不在） | 租约到期（插件退出/崩溃后最多 30 秒） | **完全自己跑，永不阻塞**：有人投就跟随，没人投等 `coop_map_vote_wait_seconds` 后自己选 |

插件在每个请求（含 SSE 流）上都带那个标识头，且它空闲 3 秒 / 活动 1 秒轮询一次 `/state`，所以该头天然就是心跳；插件挂掉后 mod 会自动退回独自运行，**不会卡死**。`/state` 里会回显 `external_control: {attached, client_id, seconds_since_contact, lease_seconds}` 便于诊断。

**插件控制下的 `coop_map_policy` / 插件侧 `map_policy` 三态：**

- **`wait`（默认）** —— **彻底等待**：多人局里不投票、也不自动跟票，路线完全留给玩家，直到玩家对猫娘发话。这就是"不抢路线"。单机局没有队友可等，仍会自己选，否则每张图白等一轮。
- **`follow`** —— 队友先投了就镜像跟随；没人投票时等 `map_vote_wait_seconds`（默认 20s）再自己选。
- **`auto`** —— 不等，立即按自己的偏好选。

**和猫娘说话来协作（`sts2_set_route`，同时是 `llm_tool`）：**

| 你说 | mode | 行为 |
|---|---|---|
| "定个路线"／"你来定"／"挑条稳的" | `safe` | 按保守权重自己算一个节点并投票 |
| "我选完了，来选这个"／"跟我一样" | `mirror` | 投你当前那一票 |
| "你选中间"／"选第三个"／"选最左边" | `pick` | 按你指的位置投（中间/第N个/最左/最右） |
| "不对不对我选错了，你选中间" | `pick` | **再调一次即改票** |

注意：`safe` 是**启发式**保守评分（躲精英、血低于 60% 优先休息），**不是真实战损预测**；改票必须赶在地图推进之前。旧配置兼容：只有 `map_follow_player` 的老配置按 `true`→follow、`false`→auto 解释。

- **回到存档**：主菜单有可继续存档时，插件主菜单决策**优先 `continue_run`**（以前优先 `open_character_select`，会抢在存档前面开新局）；猫娘进程则**完全不碰存档**（两个进程共用 `user://`，谁点「继续」谁就把存档吃掉），只保留重新加入房间的能力。`sts2_continue_run` 是显式的补救入口；`continue_run` 现在会先收干净主菜单子菜单（子菜单开着时「继续」按钮会被藏起来，以前会直接 409）。
- **防呆**：主菜单存在可继续存档时，`start_coop_session` / `start_multiplayer_host` 会返回 `409 active_run_present` 而不是默默另开一局（`POST /config {coop_yield_to_saved_run:false}` 可关闭这道守卫）。
- **已知限制**：游戏本体看起来没有「客户端加入进行中局」（join-in-progress）。即使主机点「继续」读回 co-op 存档，AI 队友也可能回不到那一局——这是游戏侧能力边界，不是插件能绕过的。

## 控制档位（自己玩 / 听安排 / 停手）

用 `sts2_set_control_mode` 切换，猫娘也会在你说话时自己调用它：

| 档位 | 你说的话 | 行为 |
|---|---|---|
| `program`（**默认**） | "你自己玩"、"随便打" | 照常决策并执行。与今天完全一致 |
| `advise` | "听我安排"、"你先别动"、"等我说" | **只算不做**：战斗之外的决策界面一步一步等你发话 |
| `standby` | "停手"、"待机" | 彻底停手，连计划都不算 |

**切换是悄悄发生的。** 换档不会触发猫娘开口（推送用 `ai_behavior="blind"`，连喂给 LLM 都不喂，只留一条给面板与排障看的内部记录），`llm_tool` 版也只回机器可读的最小结果（`{"ok": true, "style": ...}`）——若回一句中文，它很可能顺口念出来。所以**它不会说"好，我切到捣蛋模式了"**，换完就照常继续；你问起现在是什么档位它才回答。

它也**可以自己判断着切**，不必等你明说：你的语气里透出意思就够了（"你今天怎么这么笨" → 捣蛋或笨蛋；"给我认真点" → 认真；"这步我来定" → 听安排）。工具描述里明确授权了这种自主切换。

`advise` 的配合方式就是现成的那对入口：

- "你打算怎么走" → `sts2_get_planned_operation`，它把计划说出来
- "就这个"／"打吧" → `sts2_execute_planned_operation`，走一步（这是**显式授权**，会越过停手闸门）
- "你自己玩" → `sts2_start_autoplay`，等于切回 `program`

**停手范围有硬边界，这三条都是"会让游戏卡死"的地方：**

- **战斗永不停手** —— 玩家替不了猫娘出牌，停手会把回合永久卡住。所以 `advise` 只覆盖选路/奖励/选卡/遗物/宝箱/事件/商店/休息/双选/里程碑这些"慢决策"。
- **主菜单 / 组队大厅 / 选角色永不停手** —— 那是猫娘加入房间的必经流程（开菜单 → 连主机 → 选角色 → ready），停手就根本进不了局。`standby` 也一样。
- **认不出的界面不停手** —— 宁可没听成安排，也不能让 mod 意外卡死。

例外：`standby` 是**彻底**停手，所以它**包括战斗**——回合会停住，直到你发话。这是该档位的预期语义，不是故障。

**档位会同步给 mod**（`POST /config {coop_control_mode: auto|advise|hold}`），因为 mod 有自己的自动玩驱动：只停插件等于没停。映射为 `program→auto`、`advise→advise`、`standby→hold`。与地图策略一样，这个同步只在**插件附着**时才有意义（见上文的租约判定）；插件不在时 mod 一律自己跑。

**游戏没开也能切档（v0.7.11 起）。** 档位/游玩模式/陪玩开关都属于插件**本地偏好**，切换后只是"尽力刷新一次局面"：刷新失败返回 `refreshed: false` 并打一条 `[sts2_entry_refresh_skipped]`，设置本身照样生效。此前"停手 / 陪玩"这两个入口在 mod 连不上时会把 `Route not found` 抛到 NEKO 的入口兜底，变成一条 `Unexpected STS2 plugin entry failure` 红字（而玩家看到的却是"没生效"）。

## 游玩模式（认真 / 捣蛋 / 笨蛋）

和"控制档位"是两件不同的事：控制档位决定**谁来拍板**，游玩模式决定**拍得多不认真**。

| 档位 | 你说的话 | 行为 |
|---|---|---|
| `serious`（**默认**） | "认真打"、"好好打"、"我要通关" | 与改动前**逐字相同** |
| `mischievous` | "捣蛋"、"闹着玩"、"别太认真" | 20% 概率把最优操作换成**次优**的合法选择 |
| `silly` | "笨蛋"、"你傻啦"、"随便打打" | 60% 概率把最优操作换成**评分最差**的合法选择 |

运行时切换：`sts2_set_play_style(style=...)`；配置项 `play_style`（插件 `plugin.toml` / `config.example.toml`）。

**三条硬约束：**

1. **`serious` 零影响**——扰动层在 serious 下**直接原样返回**规划器的结果，连候选排序都不算，所以默认档不可能有回归。
2. **永远合法**——换出来的候选只来自该屏的**合法下标集合**（不可打出的牌、不可买的商品都会被排除），换完还要再通过 `action_engine` 校验；换不出合法操作就**放弃扰动、退回最优**，绝不发非法动作。
3. **覆盖全部决策屏**——战斗出牌、地图、奖励、选卡（各变体）、事件、商店、休息、宝箱、双选、里程碑都会受影响；其中事件/地图/奖励/删牌是按各屏**原有评分**排序，其余屏退化为"最优 + 其它合法下标"。

例外：**玩家亲口授权的那一步不受影响**。`sts2_execute_planned_operation`（"就这个"）是显式授权，即使当前是笨蛋档，它也照你说的走。

## 插件驱动的是哪个进程（host / catgirl）

这是 coop 里最容易搞混的一件事：**端口不区分"模式"，区分"哪个游戏进程"**。

| 端口 | 是谁 |
|---|---|
| **18080**（`HttpServer.DefaultPort`，`STS2_API_PORT` 可覆盖） | 单人局里那唯一的实例；**coop 里是主机（真人玩的那个）** |
| **18081**（`NekoConfig.coop_client_port`） | coop 里由 mod 用 `STS2_API_PORT=18081` 拉起的**猫娘进程** |

所以插件有"活动实例"的概念，`sts2_set_instance` 可以查看与覆盖：

| 值 | 含义 |
|---|---|
| `host` | 配置里的 `base_url`（默认 18080）。**开房必须发给它**——只有主机能拉起猫娘进程 |
| `catgirl` | 猫娘进程。插件驱动游玩时读状态、下动作都对着它 |

**默认是自动的**：`sts2_open_coop_room(host=True)` 成功后，插件会向主机读 `GET /config` 的 `coop_client_port`，据此推导猫娘地址，**轮询等它冷启动**（最长 45 秒），起来就切过去接管。

切过去时会顺手给那个实例推 `coop_control_mode="hold"`，让它**自己的 mod 驱动在除"进局流程"外的界面停手**——否则两个驱动会抢着下同一个动作。进局（主菜单 → 组队大厅 → 选角色 → ready）**仍由 mod 自己做**，那正是插件够不着的部分。插件若挂掉，租约到期后 mod 会自动恢复自己玩。

**读不通就退回**：活动实例是猫娘时若连续三次读不通，插件自动退回主机实例，不会卡在一个死地址上。

注意 `GET /config` 里的 `coop_client_port` 是 **mod 0.1.4** 起才有的只读字段。老版本 mod 没有它时插件**不猜端口**，会停在主机实例并如实说明——单人流程不受影响。

## 数据流

```
游戏战斗/场景变化
  → Mod (C#) 采集状态、推送 /events/stream
  → 插件 loop_runner 消费
  → heuristic_planner / companion_evaluator 出决策
  → （可选）catgirl_llm 生成弹幕/点评
  → Mod /action 执行动作 → /danmaku 渲染弹幕
  → （run 结束）catgirl_memory 总结教训/偏好 → 落盘 → 下一局注入 strategy_directives
```

## 使用教程

### 获取MOD

1. 订阅并启用前置模组 **STS2-RitsuLib**（创意工坊 `3747602295`；`mod_id.json` 建议声明 `min_version`）。
2. 订阅**猫娘尖塔 NekoSpire** [创意工坊](https://steamcommunity.com/sharedfiles/filedetails/?id=3794941932)。

### 启动插件

1. 在 NEKO 里启用猫爪 + 本插件。插件连接时会通过 `POST /config` 关掉 Mod 自己的 LLM/弹幕，把解说交给 NEKO 侧猫娘。
2. 用插件的 `sts2_start_autoplay` 等入口控制自动玩，`sts2_open_coop_room` 打开 co-op 房间。

### 更新/安装插件失败："plugin package cannot be installed safely"（v0.7.11 起）

**症状**：在 NEKO 市场里点更新，等一会儿失败；`logs\N.E.K.O_Plugin_<日期>.log` 里有：

```text
market_install_task outcome=failed ... error_code=install_failed
    message=plugin package cannot be installed safely
InstallSourceManager.mark_removed: builtin channel is locked (directory=sts2_autoplay)
delete_plugin: failed to update install source for plugin_dir=...\resources\bin\plugin\plugins\sts2_autoplay:
    ('BUILTIN_CHANNEL_LOCKED', 'builtin plugin sts2_autoplay cannot be soft-deleted')
```

**原因（实测，2026-10-26）**：部分 NEKO 发行构建把本插件**内置打包**进了
`<NEKO安装目录>\resources\bin\plugin\plugins\sts2_autoplay`。内置渠道是**硬锁**的
（`InstallSourceManager.mark_removed` 直接拒绝 `BUILTIN_CHANNEL_LOCKED`），而"从市场装新版"
等于"替换这个内置副本"——该链路在那些构建上走不通，于是市场每次都报
`PLUGIN_INSTALL_BLOCKED`。**包本身没问题**：同一份 `.neko-plugin`（sha256 校验通过）
换一个不含内置副本的构建，26 秒就装好了。

**怎么绕过去**：

1. **换 NEKO 构建**（首选）：内置插件清单里**不含** `sts2_autoplay` 的构建（如
   `N.E.K.O_0.0.0-2026102x` 之后的版本，内置 12 个插件）可以直接从市场安装/更新。
2. **手动删掉内置副本**：退出 NEKO → 删除
   `<NEKO安装目录>\resources\bin\plugin\plugins\sts2_autoplay` → 重启 NEKO → 再从市场安装。
3. 若报 `error_code=download_failed`（`category=timeout` / `connection_error`），那是
   从 GitHub Release 下载资产失败（网络/代理），换网络或挂代理重试即可 —— 与插件无关。

**怎么确认装的是哪一版**：从 v0.7.11 起，启动行会打**读自 `plugin.toml`** 的真实版本：

```text
[sts2_code_version] version=0.7.11
```

（v0.7.10 及更早这里是写死的旧代号 `20260521_companion_eval_debug`，看不出真实版本 —— 这也是
"玩家以为更新了、其实还在跑内置旧副本"当初那么难确认的原因。）


# Mod 部分（C# · 游戏内 Godot Mod）

**目录**：`game_mod/nekospire/`，编译成 `nekospire.dll` 随游戏加载。默认监听 **18080**（环境变量 `STS2_API_PORT` 可改）。

本 Mod（NekoSpire）是尖塔2 的猫娘陪玩桥：内嵌 **Combat Solver** 的战斗求解脑，通过 HTTP 把游戏状态与战斗路线暴露给 NEKO 插件，并自带游戏内弹幕渲染与 co-op 自动玩的底层驱动。它只提供**可执行**能力（出牌/状态采集、求解决策、弹幕渲染、本地自动玩），**不读取** `strategies/` md——那是插件的内容。

## 主要功能

**跨回合战斗求解（`GET /solver/plan`）**：内嵌 **CombatSolver 搜索/模拟核心**，不只算眼前一回合——继续预测抽牌、洗牌、敌人行动与后续资源。它在 game 线程捕获**实时** Play 阶段状态（当前回合真实手牌），把预测路线按回合分组输出，当前回合的 `card_index` 直接落到真实手牌位置。

**co-op 与独立玩家驱动（`NekoAutoplayDriver`）**：猫娘作为独立玩家 = 双进程，各进程 Mod 驱动自己本地 player。战斗用 `/solver/plan` 的下一步；地图/奖励/选牌/事件四屏先问 LLM，LLM 不可用或返回非法动作则退回各屏启发式。每个候选都用游戏自己的 `available_actions` 把关，绝不发出非法动作；非客户端/非 co-op 进程保持安静，宿主不受打扰。

**游戏内弹幕 overlay（`POST /danmaku`）**：猫娘解说直接在游戏内渲染（内置 Godot 节点 + `ProcessFrame` 信号，非 `_Process`），可配置字号与头像；弹幕与决策 LLM 解耦，`danmaku_enabled` 独立开关。所有来源都收口在 `DanmakuService.PushAsync`，由 `danmaku_push_enabled` 运行时门控（本地任何调用者推的弹幕都受控），`POST /danmaku/clear` 负责清屏。

**HTTP 状态桥（`/state`、`/events/stream`）**：一次性导出完整局面快照；屏幕切换 + 战斗状态变化经 SSE 实时推送，由真实游戏切换驱动，而非纯轮询。

## 独立运行：co-op 启动流程

不带 NEKO 插件、由 Mod 自配 LLM 独立运行时，猫娘作为独立玩家的 co-op 由 Mod 自己驱动。**双进程模型**：一份是 host（真人玩家手动玩），另一份是猫娘 client（自动玩）。下面以「游戏内一键开房」为主流程，另附两种启动脚本/Steam 路径。

### 方式一：游戏内一键开房（推荐 · 生产 UI）

1. **配 LLM**。主界面 → 设置 → 管理MOD → 选择 NekoSpire → 打开 NekoSpire 设置（游戏 Mod 详情页 NekoSpire 设置按钮）→ 填 `Base URL` / `API Key` / `Model`，勾「启用 LLM 决策（地图/奖励/事件/卡组由 LLM 决定）」，按需勾「启用猫娘弹幕点评」→ 保存。这是猫娘做决策与弹幕解说用的。
2. **主菜单开房**。点「开始 co-op 房间」。Mod 依次：
   - 置 `coop_enabled = true` 并存盘（`user://NekoSpire/settings.json`）；
   - 关掉主菜单子菜单后 `open_multiplayer_menu` → `start_multiplayer_host`（ENet `33771`）；
   - **拉起第二个游戏进程**（猫娘 client），环境变量 `STS2_API_PORT=<coop_client_port·默认18081>`、`STS2_ENABLE_DEBUG_ACTIONS=1`（同一 exe，**不带** `+connect_lobby`）。
3. **猫娘进程自动接棒**。它读到同一份 `settings.json`，本进程端口 == `coop_client_port` → `IsClientByPort()` 判定自己是猫娘，`NekoAutoplayDriver` 启动：主菜单 → `open_multiplayer_menu` → `join_multiplayer_direct`（连 host `127.0.0.1:33771`）→ 大厅选 **IRONCLAD**（roster 0）+ ready → 开局 → 地图互投。
4. **host 手动玩，猫娘自动跟随**。战斗用 `/solver/plan` 的下一步；地图/奖励/选牌/事件四屏先问 LLM（第 1 步配的那个），LLM 不可用或返回非法动作则退回各屏启发式；每个候选都以游戏自己的 `available_actions` 把关，绝不发非法动作。
5. **弹幕解说只在 host 进程**渲染（`NekoDanmakuDriver` 用 `IsCatgirlProcess()` 在猫娘进程跳过），所以猫娘进程专心地当队友、不刷弹幕。

### 方式二：启动器脚本 `Start-NekoCoop.ps1`（调试 / 自动化）

复用 `start-game-session.ps1` 拉起 host + client 两个实例（不同 API 端口、均 debug），再通过 HTTP `/action` 引导会话。默认进「multiplayer test」调试场景；`-ClientAutoplays` 让猫娘 client 自己 join/select/ready/vote；`-EnterCombat` 让两边投同一节点进第一场战斗并等 `COMBAT`。

```powershell
scripts\Start-NekoCoop.ps1 -ClientAutoplays -EnterCombat              # 全自动
scripts\Start-NekoCoop.ps1 -SkipHostLaunch -ClientAutoplays           # host 你已从 Steam 手动开好
```

### 双进程要点

- **谁算猫娘**：本进程 `STS2_API_PORT`（或 `STS2_COOP_PORT` 覆盖）== `NekoConfig.coop_client_port`（默认 18081）；或设了 `STS2_CONNECT_LOBBY`。其余实例一律视为玩家，保持手动、安静。
- **共享配置**：host 与猫娘是同一安装、同一 `user://NekoSpire/settings.json`，host 开的 `coop_enabled` 猫娘也读得到。
- **不劫持宿主**：运行中发现 `multiplayer_lobby.is_host` 或 `net_game_type == "host"` 时，自动玩立即禁用并告警（防线接错）。


## 依赖与兼容性

- 需要 **RitsuLib 0.5.18+**；当前适配《杀戮尖塔 2》0.111.0（net9.0 / C# 13）。
- 搜索受 Short/Deep 时间预算与分支上限约束，展示的是预算内找到的最佳路线，不承诺数学意义全局最优；**不修改游戏 RNG**。
- 卡效推断依赖 RitsuLib（消除 `unsupported` 卡模）；对游戏私有成员的访问用 **GameRef 反射**（不依赖编译期/运行时 publicize，无 Krafs.Publicizer）。

## 性能说明

`/solver/plan` 是短预算搜索（默认 Short 8s / Deep 120s；Deep 仅在 Short 无法解决时触发），求解在 worker 线程后台运行并周期性让出 CPU。自建 `Game/Sim/` 确定性结算器仅当 `SolverEnabled=false` 时作为回退；求解器不可用时自动玩退回「第一张可打出的牌」，避免卡死。

## 出错不停车（v0.5.2 起）

自动玩**不会因为一次错误就停手**，这一条是被真实的"猫娘站着不动"逼出来的：

- `autoplay_state` 只表示**玩家意图**（running / paused / standby / idle），任何错误都不改它；
  健康状态另由 `transport_state` / `last_error` / `consecutive_errors` 表达。
  曾经把错误写成 `autoplay_state="error"`，而自动玩循环只在 `running` 时才干活 —— 于是一次被拒的
  动作就让猫娘**永久空转**，玩家只看到它不动。
- **动作被拒 = 状态漂移，不是致命错**：`invalid_target`（例如整回合缓存的战斗计划里
  `target_index` 指向已经死掉的敌人）这类拒绝会自动作废缓存的 `/solver/plan`、强制重刷 `/state`
  后按现场重算，并继续下一轮；退避上限 8 秒。
- **战斗计划缓存签名含"存活敌人集合"**：回合内死一只敌人就重取计划，避免继续用过期下标。
- **发牌前最后一道兜底**：`action_engine` 会用现场 `hand[i].valid_target_indices` 校正越界的
  `target_index`（拿不到合法集就不猜，保持原样）。
- **选牌屏光标只由"真的按下去了"推动**（`note_selection_option_pressed`）。`plan()` 是每 tick 都跑的
  （事件流约 1 次/秒），若让规划自己推进光标，几个候选会在几秒内被白白耗光、屏幕却一张都没选中，
  随后永久返回 `None` —— 猫娘就永久停在那一屏。整屏按遍仍不推进时先让一次 `None`（留确认/离开的
  出口），再重置光标重按，保证不会死锁。
- **点评 LLM 只在四道闸门都过了之后才调用**：否则每次同步都白烧一次生成再丢掉结果
  （实测日志里约 1 次/秒的 `result=None` 就是这么来的，接真 LLM 时白花 token）。
- **商店不会反复开关货架**：`open_shop_inventory` 是**开关**，而开关两态的 `screen` 都叫 `SHOP`，
  只看"动作在不在册"就会 开→没值得买的就关→又看见开→再开，无限来回。现在一次进店只开一次货架
  （用只在开着时出现的 `close_shop_inventory` 判断"已经看过货了"），逛完就 `proceed` 走人。
- **选人界面会开局，不是一直重按角色**：`_selected_character_index` 取的是 `selected_character_id`
  的下标 —— 也就是**当前已经选中**的那个角色，所以 `select_character(那个下标)` 是 no-op：
  屏幕不推进 → 下一 tick 又发 → 无限重按，猫娘永远停在选人界面（死后重开一局必踩）。
  现在角色已选好就直接 `embark`。同一类错误（**幂等动作被当成推进手段反复发**）在商店也出现过，
  排查时可作为一条通用线索。

## 地图选路：链式递推（v0.5.7 起）

选路不是"只看眼前一步"，而是**从每个候选节点递归到 boss，评估整条路线**。

### 为什么之前不是

旧代码也写了"前瞻"（`_score_map_node` 里累加 `_collect_route_descendants`），但它是**假前瞻**，
两个原因叠在一起让前瞻恒等于 0：

1. 交给规划器的 `available_nodes` **没有 `children` 字段**；就算读全图 `map.nodes`，它的
   `children/parents` 是 **`{row,col}` 坐标对、不带 `node_type`**，而旧代码取的是
   `descendant.get("node_type")` —— 空串，权重 0。
2. `summary_context_builder` 读的 `game_map.future_nodes`，**mod 从来没下发过这个字段**。

### 还有一条更要命的类型不匹配

mod 下发的 `node_type` 是**游戏枚举名**（`Point.PointType.ToString()`）：
`Monster / Elite / Shop / RestSite / Treasure / Unknown / Event`（+ `Ancient`、`Boss`）。
而权重表的键是 `rest / shop / treasure / event / question / monster / elite` ——
**`RestSite` 与 `rest` 拼不上**，于是休息点权重恒为 0。再叠加 `_preferred_map_index` 过去那条
"分数必须 > 0 才采纳"的判断，结果就是：**怪物（3 分）永远压过营地（0 分），猫娘从不主动回血。**
现在用 `_MAP_NODE_TYPE_ALIASES` 做显式映射，并有单测逐一钉住这些取值。

### 评估口径

- 用 `(row, col) -> node` 索引把 `children` 坐标解成真正的图；从每个候选递归到 boss。
- **分支点取 max**：到那一层我们还会再选一次，所以是"沿途都选最好"，不是求和。
- **逐层折扣** `ROUTE_DISCOUNT_PER_ROW = 0.8`：远处的不确定性更大。
- **同类饱和** `ROUTE_REPEAT_DECAY = 0.5`：两个休息点 ≠ 两倍休息。
- **临门加权** `ROUTE_BOSS_PREP_MULTIPLIER = 2.0`：距 boss ≤ 2 层的休息/商店价值远高于中途
  （补血、把金币花掉）；金币不足 `ROUTE_BOSS_PREP_GOLD` 时临门商店不加权。
- **精英随血量缓和** `ROUTE_ELITE_RELIEF_MAX = 0.5`：满血时惩罚打对折（精英给遗物+金币，未必是坏事），
  但**不清零**；血少时用低血表，惩罚反而更重。
- **低血替换对自动选路也生效了**：过去只有玩家手点的 `sts2_set_route mode=safe` 才带
  `conditions.low_hp`，自动选路拿到的是空策略，血再低也不会整体转向休息。
- 深度：`route_policy.lookahead_depth`（缺省 `ROUTE_LOOKAHEAD_ROWS_FALLBACK = 6` 层）。
- **拿不到图就退回原来的单步评分**，行为与旧版一致（旧 mod / 非地图 payload 不会因此变差）。
- 每次决策用 info 级记一条 `[sts2_map_route] 金币=… 血比=… 候选评分=… -> 选 option_index=…`，
  同一局面只记一次 —— 选路是玩家最容易质疑的决策，必须留痕。
  （注意：本文件里的 `_debug` 是个**空函数**，所有 `[sts2_*]` 调试行其实都没有输出。）

## 同一族故障：动作"成功"但状态没变 → 无限重发

实测踩到四次，形态完全一样：**某个 `screen` 需要一个规划器不认识的中间动作，或者某个动作被 mod
接受却不产生状态变化**，于是每 tick 重发同一步，界面永不推进 —— 而插件看起来一切正常
（`autoplay_state=running`、租约 attached、`step_count` 还在涨）。认这条线可以省很多时间。

| 界面 | 病因 | 修法 |
|---|---|---|
| `SHOP` | `open_shop_inventory` 是**开关**，开关两态的 screen 都叫 SHOP，关了又开 | 一次进店只开一次货架 |
| `CHARACTER_SELECT` | 只发 `select_character`，而下标取自"当前已选中"的角色 → no-op | 角色已选好就 `embark` |
| `CHEST` | 流程是 `open_chest` → `choose_treasure_relic` → `proceed`，旧分支不认第一步 | 先开箱（实测卡 6 分钟以上）|
| `REWARD` | 药水栏满时"领药水奖励"发不出去，mod 照样回 `completed` → 重领同一个下标 | 药水排最后 + "已真的发过"的下标不再重发，都领不动就推进 |

**排查手法**：手工 `POST /action` 一次怀疑的动作，看返回里的 `state` 有没有真的变
（`/state` 的 `screen`/`selection`/`reward.rewards` 是否变化）。一变就说明"该发的是这个动作，
只是插件没发/发错了"；不变就说明动作本身在这条件下是空转。

**两条通用护栏**（都是被这些坑逼出来的）：

- 凡是"记住已经试过哪一项"的状态，**必须由"真的执行了"推动，不能由"规划了"推动** ——
  `plan()` 每 tick 都跑（事件流约 1 次/秒），执行约 2.5 秒一次，用规划推进会在几秒内把候选白白耗光。
- 候选试遍仍不推进时要有**重置/推进出口**（选牌屏重置重按、奖励屏改发 `collect_rewards_and_proceed`），
  绝不允许"规划不出动作"成为稳定终态。

## 弹幕双线：即时短句 + 高风险详解（v0.6.2 起）

**问题**：点评是等 LLM 生成完才推的，而主系统用**推理模型**。实测每条生成间隔：

| 场景 | 平均 | 最大 |
|---|---|---|
| combat | 5.0s | 39s |
| reward | 7.9s | 27s |
| map | 7.1s | 20s |
| selection | 6.2s | 23s |
| event | 9.6s | 24s |

5~10 秒足够换场景，所以玩家看到的是"上一个场景的弹幕"。**调 prompt 解决不了**，只能改架构：

1. **即时短句**（`_emit_instant_catgirl_line`）：完全不调 LLM，用手里已有的信息拼一句话立刻推。
   按 `场景|层|回合` 去重，事件一变就发。战斗里报"出牌建议 + 敌人意图 + 我方血量"
   （出牌建议只读 `_solver_plan_cached`，**绝不在这里等 HTTP**）。
2. **高风险长讲解**（`_should_emit_long_commentary`）：战斗里只在血比 < 50%、
   或敌人这一下的意图总伤害 ≥ 当前生命的一半时才再让本体生成；地图/商店/奖励等非战斗界面照旧讲
   （它们本来就被 `should_comment` + 指纹去重挡着，而且"讲解局势"主要就发生在那里）。
3. **迟到即丢弃**：生成完对比 `last_instant_line_key`，画面已换场景/回合就整条丢掉
   （日志 `讲解迟到已丢弃`），不再让上一场景的内容冒出来。

**两个容易忽略的细节**：

- 即时短句推弹幕时**不等待拉头像**（`fetch_avatar=False`）：拉头像是一次最长 3 秒的 HTTP，
  等它回来"即时"就没了；即时线只用已缓存的头像，没有就纯文本先发。
- 「讲解由谁生成」：SDK 只有单向 `push_message`，**没有读回本体回复的接口**，
  所以讲解文本由插件用**本体的模型 + 本体人设**生成（`catgirl_llm._persona_system`）；
  同时用 `push_message(ai_behavior="read")` 把**简化后的局势速报**喂给本体，让它自己也知道猫娘在打什么。

## 局末总结与反思（v0.6.1 起）

每局结束（阵亡/通关）做一次结构化总结，而不是只在终端屏上写一行计数：

- **损血归因**：每次刷新记一条生命采样（`hp_trail`，换局自动清零），局末算出总损血、
  单次掉血最多的三处（带界面/层级/血量前后）、最低血与结束血量 —— 就是"看看哪些地方损血导致失败"。
- **战报**（`_run_summary_digest`）：结局 + 角色 + 层级 + 损血归因 + 最近决策 + 已知教训，
  压成一行作为**简化提示词**。
- **反思**：由**本体的模型与人设**异步生成（不卡终端屏），落盘后**作为一条教训**参与下一局的
  `strategy_directives`，同时作为弹幕说给玩家、并用 `ai_behavior="respond"` 推给本体让它开口。
- 长文本推本体走 SDK 的 `ctx.push_message` 而**不是**插件的 dispatcher：后者会把正文截到 **30 字**
  （`_host_reply_text`），战报会被切碎。

> 顺带修掉一个**从来没生效过**的东西：`catgirl_memory.summarize_run` 里那条"LLM 自然语言归纳"是
> **同步**调用 `async def generate`，拿到协程后 `.splitlines()` 抛错被吞；外面还有
> `self._engine == "llm"` 这道默认关闭的闸门。所以它一直是纯启发式。

### 损血归因按**房间标识**，不是层数（v0.7.0 起）

玩家指出的问题：复盘说"第 27 层掉了 40 血"**没有用** —— 尖塔地图是随机的，同一层在不同局
根本不是同一个房间，这种记法无法跨局复用。

现在每条生命采样都带一个**房间标识**（`_room_identity`）：

| 房间 | 标识来源 |
|---|---|
| 战斗 | `combat.enemies[].enemy_id` 的组合，如 `LEAF_SLIME_M+LEAF_SLIME_S` |
| 事件 | `event.event_id` |
| 篝火/商店/宝箱 | mod **没有 id**，退化为 `room:REST` 这类房间类型 |

两个实现细节：

- mod **没有** encounter/room id，所以战斗用"敌人组合"当遭遇标识（足够区分"普通史莱姆群"和"精英"）。
- 一场战斗的标识在**遭遇开始时就钉住**（`_state.combat_room_id`，离开战斗清零）。战斗中敌人会死，
  若每帧重取，"同一场战斗"会被拆成好几个房间，归因就散了。

战报与历史页的"最痛的几处"现在都显示房间标识，层数只作为整体进度出现（`打到第 N 层`）。

### 路线图：逐房间的损血与收获（v0.7.1 起）

玩家口径：「需要路线图，对应每个房间的损血和收获」。每局记录里多一条按访问顺序的 `route`，
每个房间写清：**掉了多少血**（进房间的血 → 过程中最低血）、**拿到什么**（金币差 + 新增卡/遗物/药水，
按 id 差集算）。历史页把它渲染成"一行式序列 + 逐房间表格"。

两个判定要点：

- **奖励类界面并入上一个房间**：奖励是"这个房间打完之后"发的（战斗 → REWARD → 选牌 → REWARD → MAP），
  另起一段就看不出"这个房间值不值"。合并时**保留原房间的身份**，否则这段会被记成 `room:REWARD`，
  路线图上就看不出是打哪一场拿到的。
- **地图走一格不算房间**：没有损血也没有收获的 `MAP` 过渡段直接丢弃，否则路线图全是噪声。

顺带一提：`route`（路线图）与 `hp.heaviest`（最痛几处）是两套互补视角 ——
前者是"整条路怎么走的"，后者是"哪里最疼"。

### 篝火：默认优先锻造，而不是回血（v0.7.0 起）

玩家指出的问题：插件偏爱休息回血。锻造能**升级卡牌**（升数值、降费用），对战力提升很大，
应该优先考虑。

旧实现是 `_preferred_route_option(preferences) or 0` —— 要么用**路由**偏好（与篝火选项无关），
要么直接取下标 0，也就是"列表第一项"，而第一项通常就是回血，所以事实上永远在回血。

新口径（`_preferred_rest_option`）：

1. 玩家显式配过的偏好优先（`records[].value.preferred_option_index`）；
2. 血比 < `REST_HEAL_HP_RATIO`（0.6）→ 回血优先（快死了还去锻造就是送）；
3. 否则**锻造/升级优先**（关键词 `smith/upgrade/forge/锻造/升级/淬炼`）；
4. 认不出类型就取第一个**可用**选项（不看 `is_enabled=False` 的）。

类型判断读的是 `option_id` / `title` / `description` —— mod 的 `RestOptionPayload` 发的是
`option_id`，而 `summary_context_builder` 原来读 `type`/`name`，于是 `rest_option_types`
**一直是空的**（又一处形状错配）。现在也一并修了。每次决定用 info 级记一条
`[sts2_rest] 选项=[...] 血=…/… -> 选 option_index=…`，方便玩家核对"为什么又回血了"。

## 角色：每局随机，并播报抽到了谁（v0.6.2 起）

选人界面改走**游戏自带的「随机」候选**（`is_random`），而不是插件自己摇 —— 随机语义交给游戏，
开局后再从 `run` 里读出实际角色，播报一次（`[sts2_character] 本局角色=…`）并告诉本体。
「随机」只按一次（按键由**执行**推动，见上文那条通用护栏），按过就转入 `embark`。

## 自动游玩历史页（v0.6.1 起）

插件 UI 新增面板「自动游玩历史」（`static/history.html`），每局展示：结局/角色/层级、损血合计与
**最痛几处**、教训与复盘、以及**逐条选择**（步数/界面/动作/理由）。

数据通路：前端页面是插件服务器按静态文件发的（`GET /plugin/{id}/ui/<path>`），**没有直读插件内存的通道**，
所以每次局末把 `catgirl_memory.json` 投影成 `static/autoplay_history.json`（`history_view.py`），
页面 `fetch` 同一个静态目录下的它。权威数据仍是 `catgirl_memory.json`，快照只是视图缓存
（升级插件被覆盖也无所谓，下次局末会重写）。

## 多语言的一个大坑（v0.6.4 起）

奖励屏弹幕实测念出英文 `Prefer rewards that patch the weaknesses of the current deck.`。
根因不是缺译文：SDK 的 `PluginI18n.t()` 在**不传 locale** 时只按 `default_locale` 查表，
而 `default_locale` 的默认值是 **`"en"`**（`plugin/sdk/shared/i18n.py` 的 `DEFAULT_LOCALE`），
于是**凡是 `en.json` 里存在的键，无论玩家用什么语言都说英文**；只有 en 里没有的键才会退回代码里
写的中文 `default` —— 所以同一屏里会半中半英（地图那条是中文、奖励那条是英文，就是这个原因）。

插件侧拿不到运行时语区（SDK context 不暴露 locale），因此在 `plugin.toml` 里显式钉住：

```toml
[plugin.i18n]
default_locale = "zh-CN"
locales_dir = "i18n"
```

并补齐了 7 种语言缺的 `companion.suggestion.reward_default`；`tests/test_i18n_completeness.py`
守住"en 里的 `companion.*` 键在所有随包语言里都必须有译文"，防止英文再次漏到玩家眼前。

## 求解器怎么跑的 / 计划快照（v0.7.2 起）

### 现状（改动前）

- **mod 侧**：`GET /solver/plan` **每次调用都会重跑一遍 CombatSolver 搜索**（Short 预算 8 秒，
  Deep 仅在 Short 无解时才触发）。所以"调用次数"直接等于"卡顿与 CPU"。
- **插件侧**：按"整回合"缓存计划 —— 签名 = `run_id | turn | 存活敌人组合`，配一个
  `solver_step_index` 逐步推进；签名变化或步骤用尽才重查。

也就是说"算一次、按快照执行"**大体上已经存在**，但复用的判据只有"回合号 + 敌人没死"。
这不够：**抽牌/弃牌会让手牌位置漂移**，步进下标会指到别的牌上；敌人死了会让目标失效 ——
这些都不改回合号，却足以让快照里的下一步变成非法动作。

### 现在（玩家口径："算一次、做成快照、按快照执行；每次行动后和快照对比，一致继续、不一致重算"）

复用快照的**唯一判据**变成：**快照里下一步在当前局面下仍然可执行**（`_plan_step_is_executable`）：

| 步骤 | 校验 |
|---|---|
| `play_card` | 优先按 **`card_id`** 找当前手牌里可打出的那张（位置会漂、id 不会）；只有下标时校验范围与 `playable`；牌需要目标时校验 `target_index` 仍在 `valid_target_indices` 里 |
| `use_potion` | 该药水仍在且 `can_use` |
| `end_turn` | `end_turn` 仍在这回合的可用动作里 |
| 其它（选牌等） | 交给下层选牌逻辑，**不在这里反复重算** |

判据是保守的：**拿不准就说"能执行"**，让原有的动作层兜底 —— 不能因为"不认识这一步"就每 tick
重跑 8 秒的搜索。

留痕（用来看"到底省了多少次求解"）：

```
[sts2_plan] 复用快照 step=2/5（局面与快照一致）
[sts2_plan] 重新计算计划（原因：手牌里已没有可打出的 STRIKE）
```

另有一条互补的兜底：动作被 mod 拒收（`invalid_target` 之类）时也会作废快照并强制重刷
（见"出错不停车"一节）。

## 复盘期间暂停自动游玩（v0.7.2 起）

玩家口径："复盘的时候暂停自动游玩，复盘完再继续"——否则猫娘会在复盘还没讲完时就冲进主菜单开下一局。

- 局末写完记录后 `_pause_for_reflection()`（`pause_reason="run_reflection"`），复盘任务结束后
  `_resume_after_reflection()`。
- **一定要恢复**：`_reflect_run_end` 用 `finally` 恢复，连 LLM 没产出、抛异常也恢复 ——
  卡在"暂停"上等于猫娘永久停手，那比不暂停糟得多。
- **不擅自恢复**：只在"确实是我们为了复盘暂停的、且玩家没在这期间动过档位"时才恢复。
  玩家手动暂停或切到 `standby` 之后被我们偷偷恢复，比不恢复更糟。

## 两个「跟本体说话」的开关（v0.7.3 起）

| 开关 | 配置键 | 运行时动作 | 关掉之后 |
|---|---|---|---|
| **尖塔速报** | `host_digest_enabled` | `sts2_set_host_digest`（llm_tool 同名） | 不再把局势悄悄交给本体，本体也就不主动讲局势 |
| **尖塔复盘** | `run_reflection_enabled` | `sts2_set_run_reflection`（llm_tool 同名） | 局末不生成反思、不推给本体，也**不做"暂停-恢复"**那一步 |

两者都**只影响"跟本体说话"这一路**，这是刻意的：

- **游戏内弹幕**不受影响（那是 `danmaku_enabled` / `sts2_set_danmaku` 管的）；
- **自动游玩**不受影响；
- **结构化记录照旧写** —— 损血归因、路线图、历史页在复盘关掉时依然完整（复盘只多加一段
  "让模型复盘"的话）。所以复盘开关关掉后，历史页仍然能看到"哪个房间掉了多少血、拿到什么"。

两个入口都用静默推送（`ai_behavior="blind"`）：开关切换不该污染聊天，也不该被 LLM 复述。

## 架构与 HTTP 接口

- **入口** `ModEntry.cs`：依次启动 `GameThread`、`GameEventService` + `ScreenEventBridge`、`HttpServer`/`Router`、`CombatSolverRuntime`、`NekoConfig`、`NekoDanmakuDriver`、`NekoAutoplayDriver`。
- **HTTP 路由**（`Server/Router.cs`）：

| 路由 | 作用 |
|---|---|
| `/health` | 握手 / 版本 |
| `/state` | 完整局面快照 |
| `/coop/state` | co-op 读视图（每 player 行动阶段 + 手牌 + 共享敌人） |
| `/action` / `/actions/available` | 执行动作 / 可用动作 |
| `/config` / `/config/open` | 配置读写（插件连上后据此关掉 Mod 的 LLM/弹幕） |
| `/danmaku` | 游戏内弹幕 POST（受 `danmaku_push_enabled` 收口） |
| `/danmaku/clear` | 清空屏幕上的弹幕并卸掉 overlay（关弹幕时用） |
| `/events/stream` | SSE 场景事件流 |
| `/solver/plan` | 战斗求解决策（权威来源） |

- **战斗求解器**（`Solver/`）：内嵌 **vendored CombatSolver 0.50.1 引擎**（基线 `v0.28.0`），卡效推断用 **RitsuLib**，私有成员用 **GameRef 反射**（已去掉 Krafs.Publicizer）。`Game/Sim/` 是一套独立重实现的确定性结算器，仅作回退。
  - vendor 边界、适配规则、再同步流程与流水线脚本见 [`game_mod/nekospire/Solver/VENDORING.md`](game_mod/nekospire/Solver/VENDORING.md) 与 `game_mod/scripts/apply-vendor-sync.py`；只读同步报告：`game_mod\scripts\sync-vendor.ps1`。
  - **行为验证状态**：本次同步只验证到"Release 编译零错误 + SimTests 全通过"。求解器在真实对局中的行为**尚未验证**，见 `VENDORING.md` 的"验证边界"一节。

## 问题反馈

遇到错误路线、意外重算或执行异常，把游戏运行日志与 NEKO 运行日志发给作者邮箱（文末「联系人」一节）。

## 设置mod端口

mod默认监听端口为18080，虽然是冷门端口，但依然可能会被占用。

可以打开环境变量，在全局变量处新建变量 STS2_API_PORT ，值可以选择一些无占用端口

样例 变量名："STS2_API_PORT" 变量值："38080"

## 第三方致谢

本 Mod 的战斗求解器核心 **Vendoring 并深度适配**了以下第三方项目，特此致谢：

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)**（作者：**Torch / Torch1230**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3790899961)。战斗路线求解器。其 **搜索/模拟核心**被本 Mod 内嵌并适配，用于 `GET /solver/plan` 的战斗决策建议。已获作者授权使用。**上游现为 MIT 许可证**（见下）。
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)**（作者：**hotwords123**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952)。Combat Solver 的战斗模拟引擎核（战斗状态、牌堆、RNG、Fork、历史、Mirror）部分来自其实现，并在获得 hotwords123 书面许可后进行改造。本 Mod 仅 Vendoring 引擎源码，**不加载/分发 Random Foreseer 程序集作为运行时依赖**。

> **许可边界**：Combat Solver 上游采用 **MIT 许可证**，版权声明为
> `Copyright (c) 2026 Torch1230 and contributors`，以及
> `Copyright (c) 2026 hotwords123 (portions derived from Random Foreseer)`。
> MIT 允许使用、修改、再分发与再许可，**条件是保留版权声明与许可证全文**。
> 本仓库内嵌的是其搜索/模拟核心的一个子集（并做了反射适配与 Godot 去耦），
> 相关来源与同步机制见 [`game_mod/nekospire/Solver/VENDORING.md`](game_mod/nekospire/Solver/VENDORING.md)。
> 上述第三方来源代码不受本仓库 AGPL-3.0-only 自动覆盖，各自遵循原作者的 MIT 条款。

本 Mod 的MCP模块参考：

- **[STS2-Agent](https://github.com/CharTyr/STS2-Agent)**（作者：**杉茶 / CharTyr**）—— AI Agent Mod。在可视化窗口里配置模型端点、对话、按模型调整思考强度、让模型自动游玩。其具体数据来源本 MOD 并未过多修改。

本 Mod 的弹幕模块参考：

- **[danmuai](https://github.com/PEPETII/danmuai)**（作者：**timerome / PEPETII**）—— 让屏幕内容拥有自己的 AI 弹幕。看到这个仓库才诞生的加入猫娘弹幕的功能，故写致谢里。

- **[弹幕尖塔 DanmakuSpire]**（作者：**Icnsis**） —— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3779807977)。本 MOD 视觉样式刻意照着 DanmakuSpire 调，代码本身是独立写的。他的样式也很好看，故写致谢里。

**感谢开源**我嘞个缝合怪。

## 联系人

有任何问题请把游戏运行日志和NEKO运行日志发送邮件到 zhaijiunknown@outlook.com

游戏运行日志
```text
%AppData%\SlayTheSpire2\logs
```

NEKO运行日志
```text
您的用户文件夹\AppData\Local\N.E.K.O\logs
```

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).
