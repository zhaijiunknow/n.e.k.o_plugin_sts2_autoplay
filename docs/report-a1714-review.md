# A1714 报告核查与修复（2026-11-27）

核查对象：用户提供的 `sts2_autoplay_图一乐评分-A1714.pdf`，以及本次工作区源码。
报告是问题线索，不把评分或未验证推测当成事实。未部署、未发布，也未修改真实存档。

## 已确认并修复

### 1. Mod HTTP 浏览器隔离

- 路由前集中校验 Host（回环地址且端口匹配监听端口）。
- 拒绝带 Origin 或 Sec-Fetch-Site 的浏览器请求，包括 `Origin: null`、本机网页；
  不开放 CORS。前端仍通过 N.E.K.O 宿主调用插件，插件的 Python HTTP 客户端不带这些头。
- 所有 POST 必须为 `application/json`，允许 charset 参数，不接受 text/plain、表单或 jsonp。
- HTTP `/config` 不再允许写入 `llm_base_url` / `llm_api_key`；即便值为 null 也明确拒绝。
  校验在修改任何配置之前完成。游戏内设置窗口仍可编辑这些字段。
- 这不是对本机程序的身份认证，不能防御已能在本机执行代码的恶意进程。
- API key 的本地文件存储格式没有改成加密；配置和损坏备份都应作为私密文件对待。

生产逻辑：`game_mod/nekospire/Server/LocalRequestPolicy.cs`、`ConfigWriteRequest.cs`、`Router.cs`。

### 2. 地图协作：修复接收端而非重复添加发送端

报告所说“Python 没有同步等待时间”在当前代码中已不成立：
`service.sync_map_policy_to_mod()` 已在连接、实例切换及设置保存后同步秒数，并向上取整。
真正缺失的是 Mod 端没有接收 `coop_map_policy`，未知字段被静默忽略。

现在 Mod 接收 `wait/follow/auto`：

- wait：不自行投票或跟票，等待插件指令。
- follow：先跟玩家；无投票时按等待秒数决定何时自行选择。
- auto：不走跟票等待；独立 Mod 原有的自动选路路径保留。
- 参数非法时，在任何赋值前拒绝；秒数合法区间为 0..86400，0 是有效值。
- 插件请求头续约 15 秒；没有活跃插件时使用原来的 standalone follow/auto 规则，
  不把插件 wait 写入共享设置文件，避免插件退出后 Mod 永久等指令。
- wait/follow 不能被独立 Mod 的 LLM 路径绕过；auto 仍可使用原有 LLM。

游戏屏幕交互尚未实机验证，离线检查不替代联机游玩验证。

### 3. 配置与历史数据保护

- `NekoConfig` 读取损坏 JSON 时先生成唯一 `.corrupt-*.bak`，再允许保存默认值。
- 备份失败或读取发生 IO 错误时禁写，不让下次自动配置同步覆盖原件。
- 配置和历史都增加 schema_version=1；无版本文件按旧版本兼容读取，正常保存时才升级。
- 未知/非法版本禁写；支持版本中的额外字段保留，不因序列化丢失。
- 历史完全不可读时也禁写；仍保留原有“完整 JSON 后跟残片”的抢救能力。
- Mod 保存使用唯一临时文件、落盘刷新、原子替换，减少并行写同一个临时文件的风险。

### 4. 测试、残留与可运行性

- i18n 检查只扫描插件根模块，不再把本地备份或工具当作用户文案代码。
- 修复外部控制请求头测试的裸模块导入，使其能通过工作区 shim 运行。
- 移除未被读取的 `post_action_delay_seconds` 默认配置及烟测伪配置。
- `mode_controller.py` 仍控制 program/advise/standby，不是可删除的废代码，保留。
- 复盘度量脚本仍被测试使用：从 `.dsh-artifacts/` 移至 `tools/reflection_metrics.py`，同步测试路径。
- 两份旧 live_entry_smoke JSON 输出移至本地 `dist/report-review-20261127/` 归档，
  后续运行产物不入库；tmp/ 不入库也不打入插件包。
- 新增 `tools/test-working-tree.ps1`，从仓库根即可运行工作区测试。
- 补齐现有直播入口的烟测清单；明确跳过会保存设置的直播开关，避免实机烟测改动用户偏好。
- 新增不依赖游戏安装的 `game_mod/SafetyTests`，直接编译生产 HTTP/配置逻辑；
  新 CI workflow 在 push/PR 上执行这些检查。没有声称原市场 CI 已运行全套测试。

## 验证方式

```powershell
# 完整插件回归（测试工作区，不是部署副本）
./tools/test-working-tree.ps1
# 定向测试
./tools/test-working-tree.ps1 -TestArgs @('-q', 'test_memory_schema.py')
# 不依赖游戏安装的 C# 安全与存储检查
dotnet run --project game_mod/SafetyTests -c Release
# 游戏 Mod 编译（依赖本地游戏和 RitsuLib）
dotnet build game_mod/nekospire/nekospire.csproj -c Release --no-restore
```

## 本地验证结果

- Mod Release 编译：0 错误，9 条空引用警告；没有改动对应的求解器/编码器生成代码。
- C# SafetyTests：62 项通过，覆盖请求头、敏感配置、地图策略分流、schema、损坏备份及 IO 失败保护。
- Python 定向回归：108 项通过，覆盖历史、i18n、地图等待、请求头和路由接线。
- 额外集成回归：复盘度量脚本、控制模式与游戏 API 兼容补丁共 86 项通过。
- 新增 PowerShell 入口：使用它运行 schema/路由接线测试，19 项通过。
- 修改涉及的 Python 文件通过 Ruff；`git diff --check` 无空白错误。
- 完整 Python 回归：1601 passed、2 failed、720 warnings，用时 1520.46 秒。
  - 地图接线断言：测试进程在 14:59 启动，加载了 15:00 修订前的断言；读取到修订后源码时不匹配。
    当前断言同时检查 wait/follow 的 LLM 绕过和 auto 的保留路径，已重跑通过。
  - 入口清单：工作区已有 `sts2_set_livestream`，旧断言仍要求 26 个入口。
    已补齐精确入口集合和重复 ID 检查，并把该持久化设置入口列入烟测跳过清单。
- 上述失败项及相关入口、直播、历史 schema 回归重新运行：100 passed、35 warnings，38.35 秒。
  最终测试清单修订后没有再重跑整套；不能把首次整套结果说成零失败。
- 远程 CI 尚未触发，以上均为本地结果。

## 本轮未处理

- heuristic_planner/service 全面拆模块：属于独立架构重构，避免与当前未提交的 UI、直播改动混做。
- README 全面精简、巨大历史夹具缩减：本轮只加核查记录，不改写历史事实或降低断言覆盖。
- 完整插件测试上 CI、完整 Mod 编译上 CI：宿主 SDK 和游戏依赖仍需要专门配置。
- 本机 HTTP 调用者鉴权、API key 加密存储尚未实现。
- 没有进行游戏内联机实测、发布或本地部署。
