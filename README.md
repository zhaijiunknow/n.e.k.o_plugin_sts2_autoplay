# 插件部分（Python · NEKO 插件）

**目录**：仓库根目录（Python 包），`plugin.toml` 声明入口 `STS2AutoplayPlugin`。

本插件负责**决策与表达**：给尖塔2的猫娘陪玩提供自动出牌/走图、局势点评、事件房 LLM 打分、游戏内弹幕，以及跨局记忆沉淀。它通过 Mod 的 HTTP API（`/state`、`/events/stream`、`/action`、`/solver/plan`、`/danmaku`）与游戏交互，自身**不读取** `strategies/` md——那是策略内容，交给 `strategy_repository` 解析。

## 入口 actions（`__init__.py`）

- **状态**：`sts2_health_check`、`sts2_get_status`、`sts2_read_state`
- **自动玩**：`sts2_start_autoplay`、`sts2_pause_autoplay`、`sts2_resume_autoplay`、`sts2_stop_autoplay`、`sts2_set_standby`
- **陪玩**：`sts2_enable_companion_mode`、`sts2_disable_companion_mode`
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
| `catgirl_llm.py` | LLM 生成：`CatgirlCommentGenerator`（弹幕/点评）、`EventAdviceGenerator`（事件排序分） |
| `catgirl_memory.py` | 跨局记忆沉淀（见下节） |
| `strategy_repository.py` / `strategy_parser.py` | 策略加载：`strategies/` 解析 + `strategy_directives` 组装 |
| `dispatcher.py` | 状态反馈 / 陪玩通知的 NEKO 输出边界 |

## 决策与表达

- **决策**：地图/奖励/卡组/事件由启发式 +（事件房）LLM 评分融合决策；战斗出牌由 `/solver/plan` 权威决定，回退启发式。
- **表达**：猫娘点评 + 游戏内弹幕。
- **偏好吸收**：`sts2_apply_user_override` 把用户自然语言指点转成结构化偏好 / override。
- **跨局记忆**：见下节。

## 猫娘记忆成长（跨局教训 → 决策）

每局结束（死亡/通关，`state_machine` 判为 `screen_class=terminal`）时，插件把本局 `recent_decision_memory` 总结成「教训 + 偏好」，写入 `catgirl_memory.py` 的 `CatgirlMemoryStore`，持久化到 `<LOCALAPPDATA>/N.E.K.O/sts2_autoplay/catgirl_memory.json`。下一局 `strategy_repository.build_context()` 会把最近几局的教训并入 `strategy_directives.avoid`、偏好并入 `prefer`（软约束），从而影响选牌/路线/战斗决策。总结引擎为**启发式打底 + LLM 可选**（`service._catgirl_llm.available` 时才叠加，失败静默回退）。

## 弹幕/点评

- 猫娘点评经插件 `_maybe_emit_catgirl_llm` → `_push_catgirl` → `POST /danmaku`（文本 + 头像）进游戏内渲染；评论不再走 NEKO 宿主 `push_message`。
- 战斗时给 LLM 的 prompt 精简为「怪物血量 + 我方生命 + 当前层级」；仅 SSE `event_type == player_action_window_opened`（轮到玩家行动）才附当前回合出牌建议 line（`_build_combat_prompt`，复用 `loop_runner._solver_plan_cached`，签名命中不重复搜 `/solver/plan`）。
- 弹幕只响应三个战斗事件：`combat_started` / `available_actions_changed` / `combat_turn_changed`，其中仅 `combat_turn_changed`（回合推进）带 line。

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


# Mod 部分（C# · 游戏内 Godot Mod）

**目录**：`game_mod/nekospire/`，编译成 `nekospire.dll` 随游戏加载。默认监听 **18080**（环境变量 `STS2_API_PORT` 可改）。

本 Mod（NekoSpire）是尖塔2 的猫娘陪玩桥：内嵌 **Combat Solver** 的战斗求解脑，通过 HTTP 把游戏状态与战斗路线暴露给 NEKO 插件，并自带游戏内弹幕渲染与 co-op 自动玩的底层驱动。它只提供**可执行**能力（出牌/状态采集、求解决策、弹幕渲染、本地自动玩），**不读取** `strategies/` md——那是插件的内容。

## 主要功能

**跨回合战斗求解（`GET /solver/plan`）**：内嵌 **CombatSolver 搜索/模拟核心**，不只算眼前一回合——继续预测抽牌、洗牌、敌人行动与后续资源。它在 game 线程捕获**实时** Play 阶段状态（当前回合真实手牌），把预测路线按回合分组输出，当前回合的 `card_index` 直接落到真实手牌位置。

**co-op 与独立玩家驱动（`NekoAutoplayDriver`）**：猫娘作为独立玩家 = 双进程，各进程 Mod 驱动自己本地 player。战斗用 `/solver/plan` 的下一步；地图/奖励/选牌/事件四屏先问 LLM，LLM 不可用或返回非法动作则退回各屏启发式。每个候选都用游戏自己的 `available_actions` 把关，绝不发出非法动作；非客户端/非 co-op 进程保持安静，宿主不受打扰。

**游戏内弹幕 overlay（`POST /danmaku`）**：猫娘解说直接在游戏内渲染（内置 Godot 节点 + `ProcessFrame` 信号，非 `_Process`），可配置字号与头像；弹幕与决策 LLM 解耦，`danmaku_enabled` 独立开关。

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
| `/danmaku` | 游戏内弹幕 POST |
| `/events/stream` | SSE 场景事件流 |
| `/solver/plan` | 战斗求解决策（权威来源） |

- **战斗求解器**（`Solver/`）：内嵌 **vendored CombatSolver 0.28 引擎**，卡效推断用 **RitsuLib**，私有成员用 **GameRef 反射**（已去掉 Krafs.Publicizer）。`Game/Sim/` 是一套独立重实现的确定性结算器，仅作回退。

## 问题反馈

遇到错误路线、意外重算或执行异常，把游戏运行日志与 NEKO 运行日志发给作者邮箱（文末「联系人」一节）。

## 设置mod端口

mod默认监听端口为18080，虽然是冷门端口，但依然可能会被占用。

可以打开环境变量，在全局变量处新建变量 STS2_API_PORT ，值可以选择一些无占用端口

样例 变量名："STS2_API_PORT" 变量值："38080"

## 第三方致谢

本 Mod 的战斗求解器核心 **Vendoring 并深度适配**了以下第三方项目，特此致谢：

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)**（作者：**Torch / Torch1230**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3790899961)。战斗路线求解器。其 **搜索/模拟核心**被本 Mod 内嵌并适配，用于 `GET /solver/plan` 的战斗决策建议。已获作者授权使用。
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)**（作者：**hotwords123**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952)。Combat Solver 的战斗模拟引擎核（战斗状态、牌堆、RNG、Fork、历史、Mirror）部分来自其实现，并在获得 hotwords123 书面许可后进行改造。本 Mod 仅 Vendoring 引擎源码，**不加载/分发 Random Foreseer 程序集作为运行时依赖**。

> 第三方来源代码遵循**原作者许可边界**：Combat Solver 仓库无统一许可证；Random Foreseer 来源代码以 Combat Solver 的 `THIRD_PARTY_NOTICES.md` 为准。上述来源不受本仓库 AGPL-3.0-only 自动覆盖。

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
