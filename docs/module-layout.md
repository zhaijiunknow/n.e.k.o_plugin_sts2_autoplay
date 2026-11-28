# 手写代码模块布局

本次拆分保留 `STS2AutoplayService`、`STS2HeuristicPlanner` 及 Mod 两个静态服务类的调用入口。
Python 使用显式 mixin 组合；C# 使用同一命名空间下的 `partial` 类。不是复制一份旧实现再加转发，
也没有引入动态代码加载。未修改上游 vendored 引擎/编码器。

## Python 服务

| 文件 | 职责 |
| --- | --- |
| `service.py` | 服务初始化/生命周期、执行与恢复、状态协调，组合其余内部 mixin |
| `service_settings.py` | 参数读取/应用、直播/地图配置；读取兼容旧错误值，写入仍严格校验 |
| `service_history.py` | 玩家选择、路线/血量记录、局末历史与复盘 |
| `service_host.py` | 宿主速报、聊天点评、发送与去重 |
| `service_danmaku.py` | 即时/静止画面/LLM 弹幕、生成与发送节奏 |
| `service_status.py` | 状态接口、求解器状态、最小化静态控制页快照 |

各 mixin 使用由服务构造函数初始化的共享状态，不应单独实例化。修改方法后，测试应在其所属模块
mock `time`、`monotonic`、历史写入等依赖，而不是在 `service.py` 中补一个不起作用的同名对象。

## Python 规划器

| 文件 | 职责 |
| --- | --- |
| `heuristic_planner.py` | 公共入口、计划分发与状态协调、跨领域规则；兼容导出旧常量/工具函数 |
| `planner_common.py` | 公共常量、正规化与无状态工具函数 |
| `planner_combat.py` | 战斗/出牌与目标选择 |
| `planner_choices.py` | 选牌、奖励/事件选择、牌组评估 |
| `planner_map.py` | 地图路径、联机等待/跟随策略 |
| `planner_shop.py` | 商店购买/移除评估 |
| `planner_lessons.py` | 历史教训匹配与评分 |

领域模块不要反向导入 facade；公共依赖放在 `planner_common.py`，否则容易形成循环导入。
新的领域功能优先加到对应模块，而不是继续堆入主文件。

## Mod 游戏桥接

目录均为 `game_mod/nekospire/Game/`：

- `GameStateService.cs` 保留状态组装入口和核心辅助逻辑；对应 partial 文件：
  `AvailableActions`、`Combat`、`AgentView`、`Rooms`、`Cards`、`ScreenActions`、`UiLookup`。
- `GameActionService.cs` 保留动作分发与通用处理；对应 partial 文件：
  `Combat`、`Menus`、`MenuTransitions`、`Map`、`Rewards`、`Events`、`Shop`。
- 两组 `*.Payloads.cs` 单独存放 DTO，不是另一个静态服务实例。
- 方法仍共享原类的私有成员；HTTP 路由、动作名字和数据结构未因拆分改名。
- 解析源码的测试需合并相关 partial 文件；不能假定方法全部在无后缀的主文件中。

## 前端生成文件与本地资源

- `static/control_status.json`：后台状态更新时最多每 2 秒原子写入一次，字段白名单，
  不含完整游戏状态/聊天包。前端每 5 秒读取，过期 45 秒后显示未知；隐藏页不轮询。
  首次/手动读取和实际控制操作仍使用宿主入口；快照不替代控制 API。
- `static/local-art/`：用户本地提取的可选美术，不提交、不打包；无图片时保留可操作的文字卡片。
- 两者均有 Git 忽略与插件打包排除规则，不能只依赖 `.gitignore` 控制分发。

验证命令和环境要求见根目录 `AGENTS.md`。真实 Godot 界面、网络下载与实际游玩仍需单独联调。
