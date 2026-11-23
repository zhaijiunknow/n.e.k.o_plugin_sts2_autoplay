# 内嵌 CombatSolver 引擎的 vendor 机制

本目录（`game_mod/nekospire/Solver/`）里的引擎**不是整棵拷贝**，而是"**按边界取一个闭包，再叠两层本地代码**"。
这份文档说明边界怎么划、适配怎么留、以及将来怎么再同步一次。

配套文件：

| 文件 | 性质 | 作用 |
|---|---|---|
| `VENDORING-RULES.json` | **手写**（唯一事实来源） | vendor 边界、必须保留的本地文件、适配规则、完成标准 |
| `VENDORING-MANIFEST.json` | **生成**（不要手改） | 现状普查：上游提交、每个 vendor 文件的内容哈希、哪些文件带私有访问改写 |
| `game_mod/scripts/vendor-combat-solver.py` | 工具 | 读规则 → 换入上游闭包 → 把私有成员访问改写成 GameRef 反射（带命中数断言）→ 写清单 |
| `game_mod/scripts/build-errors.py` | 工具 | 非增量 Release 编译，按错误码/文件汇总 |
| `game_mod/SolverSmoke/` | 工具 | 离线冒烟：不启动游戏，用真实 `sts2.dll` 建真实战斗，驱动**构建出的** `nekospire.dll` 求解并执行计划 |

---

## 1. 上游与当前版本

- 上游仓库：<https://github.com/Torch1230/CombatSolver>
- 许可证：**MIT**（`Copyright (c) 2026 Torch1230 and contributors`；Random Foreseer 部分 `Copyright (c) 2026 hotwords123`）
- **当前内嵌：上游 1.0.0，`main@1ca3d6ce`**（同步时尚未打标签、也未发布到创意工坊；工坊版本是 0.51.9）
  **+ nekospire 自己的本地补丁**（不改上游，见第 1.1 节）
- 适配游戏：《杀戮尖塔 2》0.113.x，RitsuLib ≥ 0.6.11

### 1.0.0 是一次架构重写，不是版本递增

上游在 1.0.0 把旧引擎整体删除（`Engine/InCombat` 的 Hook 镜像、`Prediction` 补偿层、`CombatBeamSolver`，
src 净减约 10 万行），换成：

- **HotKernel**：纯值类型的战斗内核。主线程由 `HotKernel/Import/` 把一场真实战斗导入成紧凑的值状态
  （`HotRootBundle`），之后的模拟只在值拷贝上进行。
- **HotSearch**：在 HotKernel 上跑的自适应三段式搜索（`HotSearchCoordinator.Solve`）。

对本模组的直接后果：

| | 0.50.1（旧） | 1.0.0（现在） |
|---|---|---|
| 后台搜索碰不碰游戏对象 | 碰（克隆真实 Model 再模拟），所以要一批 Harmony 隔离补丁 | **不碰**，隔离补丁全部不再需要 |
| 私有成员访问散在哪 | 40 个文件、226 处 | **只在主线程根导入层**：4 个文件、72 处 |
| 与上游的差异形态 | 56 个文件被手工改编，每次同步都要三方合并 | 除私有访问改写外**零改动**，改写由脚本重放 |
| 第三方模组内容 | 部分支持 | **明确拒绝**（`VanillaRootGate` 抛 `IncompatibleGameplayModException`） |
| 多人战斗 | 引擎按玩家数处理 | 上游只建模一名玩家、多人局根本不搜索；**nekospire 的本地补丁补上了人数规则**（见 1.1） |

旧机制（基线 `v0.28.0`、三方合并、`sync-vendor.ps1`、`VENDORING-GAP-REPORT.md`）随旧引擎一并作废，
那两份文件留作历史记录。

### 1.1 本地补丁：多人战斗的本地玩家模型

上游检出（`D:\NekoClaw\CombatSolver`）**不做任何修改**。这组改动只存在于 nekospire 的内嵌副本里：
定义在 `game_mod/scripts/vendor_local_patches.py`，同步脚本在换入上游、完成私有访问改写之后重放
（5 个文件、11 处，每个锚点必须恰好命中一次）。要点：

- 内核仍只模拟**本地玩家**；队友不建模、不搜索其动作。
- 人数规则冻结进 `HotCombatRulesSnapshot`（`PlayerCount` / `MultiplayerHpScale` / `MultiplayerEnemyBlockScale`）：
  战斗中新建/孵化/复活的怪物生命、敌方获得的行动格挡按原版公式缩放。单人时全为 1，行为与文本不变。
- 两处“唯一玩家”假设改成取本地玩家（`MadScienceGrowth.CaptureRemainingCapacity`、`BattleDamageTracker`）。

**已知不精确**：全队共享的 RNG 流（洗牌、随机目标）会被队友消耗，内核只推进本地玩家那一份，所以多人下
跨回合的洗牌顺序不保证一致；随机选玩家的敌方效果按“总是命中本地玩家”处理；指向队友的多人牌没有队友
目标可选。接缝因此在多人计划的 warnings 里加 `multiplayer:teammates_not_modeled:players=N` 并把
`coverage.risk` 置真。

**取证**（一次性，2026-11）：把同样的改动临时放进上游的离线差分模糊测试（内核每一步与真实游戏逐字段对比，
60 场同种子）跑过：单人 56/60 → 56/60，失败桶逐个相同（无回归）；加一名不行动的队友 13/60 → 18/60，
测试宿主再让队友不会死后 30/60，剩余失败以共享 RNG 分歧为主。上游已恢复原样，那个带 `--teammates` 的
模糊测试入口没有保留；现在能重跑的是 `game_mod/SolverSmoke --teammates …`（见第 5 节）。

**再同步时**：锚点对不上说明上游改了这些位置——先看上游是否已经自己支持多人（那就删掉对应补丁），
否则按新代码重写锚点。

---

## 2. 三层结构

### 第 1 层：vendor 闭包（来自上游，83 个文件）

| 子树 | 文件数 | 说明 |
|---|---:|---|
| `HotKernel/` | 42 | 整棵取。`State/`、`Rules/`、`Sim/` 是纯值内核；`Import/` 是唯一读游戏对象的子层 |
| `HotSearch/` | 11 | 整棵取 |
| `Search/` | 17 | 整棵取（1.0.0 里已瘦身成对外 DTO 与策略合同：`CombatPlan`、`SearchPolicySnapshot`…） |
| `Prediction/` | 4 | 整棵取 |
| `Engine/` | 1 | 整棵取（只剩 `PredictionUnsupportedException`） |
| `Runtime/` | 8 / 39 | 只取根捕获与结果合同需要的：`CombatRootSnapshot`、`ContinuationStamp`、`LiveCombatStamp`、`BattleDamageTracker`、`SolverProgress`、`SolverDisplayNames`、`DingyDaggerDisplayNames`、`LiveEndTurnRiskEvaluator` |

命名空间保持上游原样（`CombatSolver.*`），**不改名**。

### 第 2 层：本地保留文件

| 文件 | 为什么存在 |
|---|---|
| `Engine/Common/GameRef.cs` | **反射适配层**。上游靠 Krafs.Publicizer 直接读游戏私有成员，本模组不能这么做（见约束一），全部改走这里。上游没有这个文件；它位于被整棵替换的 `Engine/` 里，脚本会先取出再放回。 |
| `SolverShellShims.cs` | 补齐被排除的外壳文件所声明、而 vendor 闭包又引用的类型：`SolverPotionPolicy` / `BossHpStrategy` / `SolverSettings`（上游 `Runtime/SolverSettings.cs`）、`SearchProgressDisplayState`（上游 `Runtime/SolverControllerSessions.cs`）；并用一条 `global using` 让改写后的文件解析到 `GameRef`。**每次同步后要与上游对应声明逐字核对。** |
| `EntryShim.cs` | 补齐被排除的上游 `Runtime/Entry.cs` 所声明的 `CombatSolver.Entry`（`Logger` / `ModId`）。 |

### 第 3 层：接缝（本模组自己的代码）

| 文件 | 作用 |
|---|---|
| `CombatSolverFacade.cs` | 主线程 `CombatRootSnapshot.Capture` → 后台 `HotSearchCoordinator.Solve` → 把 `SolverResult` 映射成 `SolverPlanPayload`（HTTP `/solver/plan` 的合同）。只给建议，**从不出手部署**。 |
| `CombatSolverRuntime.cs` | 从 `NekoComm.ModEntry` 接好引擎日志，并 `PatchAll` 本模组自己的 Harmony 补丁。 |

接缝里三处需要知道的设计决定：

1. **搜索策略照抄上游默认设置**（`SolverController.CaptureSearchPolicy` + 默认 `SolverSettingsData`：Medium 档、
   Smart 药水、进度优先的 Boss 策略、遗物策略开）。本模组只暴露一个旋钮：`NekoConfig.solver_budget_seconds`
   → `MaxHardFightThinkSeconds`（难局自适应加深的墙钟上限，引擎钳到 1..15 秒）。
2. **药水**：旧接缝有一套自己的"损血 ≤ 8 就不用药，否则按药水数量逐级放开"的循环；1.0.0 把药水放进同一个
   目标函数里定价（`PotionHpThresholds`：按档位 9 / 14 / 18 点血才值得用一瓶），所以那套循环删掉了。
3. **前置条件显式把关**：不在本地玩家出牌阶段时，`BuildPlanAsync` 直接返回 `in_combat=false` + 原因，
   不去捕获根。多人战斗照常求解（只为本地玩家规划），并在 warnings 里注明队友未建模。

---

## 3. 三条硬约束（决定了边界为什么长这样）

### 约束一：**不能用 Krafs.Publicizer**

`GameRef.cs` 头部记录了实测结论：

> Publicizer 的 `IgnoresAccessChecksTo("sts2")` 在 plain-SDK 插件模组下**不被 mod 加载器承认**（实测：直接 `ldfld` 一个私有字段仍然抛 `FieldAccessException`），RitsuLib 也不做运行时公开化。

**后果**：上游每一处直连游戏私有成员的地方，都必须改写成 `GameRef` 反射调用。1.0.0 里这些访问全部落在
主线程根导入层，共 **72 处、4 个文件**：

| 文件 | 改写 |
|---|---|
| `HotKernel/Import/HotRootBuilder.cs` | 48 处私有字段/属性 + 14 处 `GetInternalData<XPower.Data>()` |
| `HotKernel/Import/HotImportSupport.cs` | 7 处私有字段 + RNG 状态改用公开的 `Rng.ToSerializable()` |
| `HotKernel/Import/VanillaRootGate.cs` | 1 处 `GetInternalData` |
| `Search/PotionRewardOutlook.cs` | 1 处私有属性模式 |

改写**不是手工做的**：配方登记在 `vendor-combat-solver.py`（成员名 → C# 类型，类型取自真实 `sts2.dll` 元数据），
每个文件带**期望命中数**。上游新增或删除一处私有访问 ⇒ 命中数对不上 ⇒ 脚本失败。这是有意的：宁可同步时
停下来，也不要带着漏改的访问进游戏。

反射开销只发生在**每次捕获根**的时候（一次 `/solver/plan` 一次），搜索热路径不经过它。

### 约束二：**不能用 Godot.NET.Sdk**

本工程是 `Microsoft.NET.Sdk`（游戏数据目录不带 Godot 源生成器，自己声明的 `Node` 子类不会被接线）。

**后果**：凡是**声明** `Node` 子类的上游文件都不能编进来。只是**使用** Godot 类型的文件没问题
（`SolverDisplayNames.cs` 读 `Marker2D` / 节点坐标给敌人排座次，原样编入）。1.0.0 的 vendor 子树里已经没有
任何 `Node` 子类，这条约束目前只用来解释为什么 `Runtime/` 的外壳不取。

### 约束三：**工具链是 .NET 9 / C# 13**

上游 1.0.0 自己的 `LangVersion` 也是 13.0，目前没有需要降级改写的语法。

---

## 4. 再同步一次的标准流程

```powershell
# 0) 更新上游
git -C D:\NekoClaw\CombatSolver pull

# 1) 干跑：报告会写哪些文件、删哪些陈旧文件、配方是否全部命中（不动任何源码）
python game_mod\scripts\vendor-combat-solver.py

# 2) 若上游版本/提交变了：先改 VENDORING-RULES.json 的 upstream.expected_version / expected_commit，
#    并核对 SolverShellShims.cs 里每个声明与上游是否仍然逐字一致。

# 3) 执行：清空 vendor 子树 → 拷入上游闭包 → 放回 GameRef.cs → 重放私有访问改写 → 重写清单
python game_mod\scripts\vendor-combat-solver.py --apply

# 4) 编译到零错误（Solver/ 之外的 Game/、Server/ 也会被游戏更新打断，一起看）
python game_mod\scripts\build-errors.py --all
#    CS1061 / CS0117 / CS0122 指向游戏类型的私有成员 -> 在脚本的 PRIVATE_MEMBERS / INTERNAL_DATA_FIELDS
#                                                      里登记（类型以 sts2.dll 元数据为准），并更新 EXPECTED 命中数
#    CS0246 / CS0103 指向被排除的外壳类型            -> 在 SolverShellShims.cs 里补声明
#    接缝报错（CombatSolverFacade.cs）                -> 读懂上游 SolverController 的对应改动后再改

# 5) 验证
dotnet run --project game_mod\SimTests -c Release
dotnet build game_mod\nekospire\nekospire.csproj -c Release
dotnet run --project game_mod\SolverSmoke -c Release -- --character IRONCLAD --encounter NibbitsWeak --turns 4
#    插件侧 pytest 见仓库根的 AGENTS.md
```

### 完成标准

见 `VENDORING-RULES.json` 的 `definition_of_done`。

---

## 5. 验证边界

### 离线冒烟能证明什么

`game_mod/SolverSmoke/` 是一个普通 .NET 9 控制台程序。它借用上游 `OfflineSearchHarness` 的引导代码
（从上游检出目录**链接**进来，不复制）在没有 Godot 引擎的进程里把游戏核心层跑起来，开一局真实的跑局、
进一场真实的战斗，然后每个玩家回合：

1. 通过反射调用**构建出的 `nekospire.dll`** 里的 `CombatSolverFacade.BuildPlanAsync`
   —— 就是 `GET /solver/plan` 背后的那个入口，同一份 IL，没有公开化；
2. 把返回的本回合步骤按插件的口径（按 `card_id` 在手牌里找牌、按 `target_index` 取敌人）打到实机战斗上；
3. 结束回合，重复。

任何一步出错都以非零退出：门面拒绝了一个真实的出牌阶段根、`search_status` 不在插件闸门认可的集合里、
或者计划里的某一步被游戏拒绝。

所以它覆盖了编译覆盖不到的东西：**GameRef 的成员名和类型对不对、根捕获在真实对象上跑不跑得通、
搜索出来的步骤游戏认不认**。可以用 `--relics` / `--cards` / `--potions` / `--powers` 把那些"根捕获要读其私有
状态"的遗物、卡牌、能力摆上台面专门打一遍。

### 它证明不了什么（必须在游戏里看）

- **游戏进程内的加载**：真实的模组加载器、Godot 运行时、RitsuLib 的实际加载顺序。约束一那条实测结论
  （加载器不承认 Publicizer）就是只在游戏里才成立的事实，离线进程里看不出来。
- **`SolverDisplayNames` 的真实路径**：离线没有 `NCombatRoom` 节点，敌人排座次走的是无节点分支。
- **选牌类步骤**：冒烟遇到带 `choice_card_id` 的步骤会停手（需要按计划驱动选牌界面，不在冒烟范围内）。
- **路线质量与耗时**：离线只看"能不能算、算出来的能不能打"，不评价打得好不好、卡不卡。
- **真实联机的多人战斗**：冒烟的 `--teammates` 用的是游戏自带的“虚拟多人”（单机网络服务 + 多名不行动的
  队友），能验证人数规则与根捕获，验证不了真实联机下队友行动、同步与共享 RNG 的影响。

**每次同步后仍然必须由作者在游戏里实测。**

---

## 6. 历史教训（避免重犯）

1. **"命中数是断言，不是统计"。** 给私有访问写改写规则时，一定要绑定到具体文件和期望次数。内核自己的结构体
   里也有 `_base` 这样的字段名，无差别的正则会把它们也改成反射调用（1.0.0 同步时 `HotCostModifiers.cs`
   就被误伤过一次，靠"没登记的文件一律原样取"这条规则挡住）。
2. **取可信错误数必须非增量编译。** MSBuild 增量判定会跳过编译阶段，给出虚假的低错误数。
3. **C# 的错误是分阶段报的。** "找不到类型"修完之后才会出现"找不到成员"。第一轮只有十几个错误不代表快完了。
4. **游戏更新不只打断求解器。** 0.111 → 0.113 同时改了商店购买（`OnTryPurchaseWrapper` →
   `MerchantSynchronizer.PurchaseLocalEntry`）、联机加入（`JoinFlow` 构造函数）、进阶文案（多了 `GameMode` 参数）
   和大厅玩家字段。这些在 `Game/` 里，编译错误要连它们一起看。
5. **比较文件前先规范化换行符与 BOM。** 上游是 LF，本仓库工作区是 CRLF（`core.autocrlf=true`）。
6. **读 C# 源码的测试会被同步打到。** `tests/test_t45_solver_gate.py` 直接解析 `Search/CombatPlan.cs` 里的
   `SearchBoundaryReason` 枚举，要求它与插件的 `SOLVER_SEARCH_STATUSES` 逐项相等。上游增删枚举成员时
   （1.0.0 删了 `MemoryNoProgress`），插件侧 `loop_runner.py` 必须同步改。
