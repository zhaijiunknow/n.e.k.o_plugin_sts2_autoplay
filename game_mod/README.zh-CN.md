# STS2 AI Agent

https://github.com/user-attachments/assets/89353468-a299-4315-9516-e520bcbfbd4b

English README: [README.md](./README.md)

`STS2 AI Agent` 是一个给《Slay the Spire 2》用的游戏 Mod + MCP Server 组合：

- `STS2AIAgent`：把游戏状态和操作暴露为本地 HTTP API
- `mcp_server`：把这套本地 API 包装成 MCP Server，方便接入支持 MCP 的 AI 客户端

更细的工具说明在 [mcp_server/README.md](./mcp_server/README.md)，如果你要搭配 agent 工作流，优先看 [skills/sts2-mcp-player/SKILL.md](./skills/sts2-mcp-player/SKILL.md)。

## 快速开始

### 1. 安装 Mod

下载并解压 release 后，把下面这些文件复制到你的游戏目录 `mods/` 下：

```text
STS2AIAgent.dll
STS2AIAgent.pck
mod_id.json
```

Steam 默认游戏目录通常是：

```text
C:\Program Files (x86)\Steam\steamapps\common\Slay the Spire 2
```

最终目录结构应当类似：

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### 2. 启动游戏并确认 Mod 生效

先正常启动一次游戏，让 Mod 随游戏一起加载。

然后在浏览器打开：

```text
http://127.0.0.1:8080/health
```

只要能看到返回结果，就说明 Mod 已经跑起来了。

### 3. 启动 MCP Server

先准备运行环境：

1. 安装 `Python 3.11+`
2. 安装 `uv`

Windows 安装 `uv`：

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS：

```bash
brew install uv
```

然后直接启动 `stdio` MCP。

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start-mcp-stdio.ps1"
```

macOS / Linux：

```bash
./scripts/start-mcp-stdio.sh
```

这就是默认推荐用法。大多数桌面 AI 客户端接 MCP，都优先用 `stdio`。

### 4. 连接你的 MCP 客户端

如果客户端支持命令式启动，把工作目录指向 `mcp_server/`，启动命令填：

```text
uv run sts2-mcp-server
```

如果你的客户端更适合连 HTTP，再启动网络版：

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\start-mcp-network.ps1"
```

macOS / Linux：

```bash
./scripts/start-mcp-network.sh
```

默认 MCP 地址：

```text
http://127.0.0.1:8765/mcp
```

## 这个项目现在能做什么

当前 `main` 分支提供的是一套可直接游玩的完整能力：

- 读取游戏状态
- 获取当前可执行动作
- 执行战斗、奖励、商店、地图、事件、休息点、宝箱、尖塔选择、Bundle 选择等操作
- 增强的战斗和运行载荷（Ascension、act/boss ID、enemy/move ID），支持 AlphaZero 训练
- `resolve_rewards` 原子动作，精确控制奖励领取
- 通过 SSE 事件减少高频轮询
- 以 `stdio` 或 HTTP 方式暴露 MCP
- 通过 Mod API 提供卡牌、遗物、敌人、药水、事件等实时元数据查询
- 支持 planner / combat 分层 handoff 流程
- 角色选择界面 `increase_ascension` / `decrease_ascension` 控制

更细的工具说明在 [mcp_server/README.md](./mcp_server/README.md)。

## 常见问题

### `http://127.0.0.1:8080/health` 打不开

优先检查这几件事：

1. 游戏是否真的已经启动
2. `STS2AIAgent.dll`、`STS2AIAgent.pck` 和 `mod_id.json` 是否都放进了游戏目录的 `mods/`
3. 文件名有没有被系统自动改名或重复
4. 你放的是 Steam 游戏目录，不是仓库目录

### MCP 能启动，但读不到游戏状态

这通常表示 `mcp_server` 启动了，但游戏里的 Mod 没连上。先确认：

1. 游戏正在运行
2. `http://127.0.0.1:8080/health` 可访问
3. MCP 仍然在连默认地址 `http://127.0.0.1:8080`

### 要不要开 debug 动作

正常使用不需要。

像 `run_console_command` 这种开发期调试工具默认关闭，发布和日常使用都建议保持关闭。

## 从源码构建

如果你不是单纯使用 release，而是要自己构建：

Windows：

```powershell
powershell -ExecutionPolicy Bypass -File ".\scripts\build-mod.ps1" -Configuration Release
```

macOS / Linux：

```bash
./scripts/build-mod.sh --configuration Release
```

更完整的环境变量、路径探测和验证流程见 [build-and-env.md](./build-and-env.md)。

## 仓库结构

- `STS2AIAgent/`：游戏 Mod 源码
- `mcp_server/`：MCP Server 源码
- `scripts/`：启动、构建、验证脚本
- `docs/`：补充文档
- `skills/`：配套 Skill

## 第三方致谢

本 Mod 的战斗求解器核心 **Vendoring 并深度适配**了以下第三方项目，特此致谢：

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)**（作者：**Torch / Torch1230**）—— 战斗路线求解器。其搜索/模拟核心被本 Mod 内嵌并修改（去 RitsuLib、反射化、移除 publicize 依赖），用于 `GET /solver/plan` 的战斗决策建议。已获作者授权使用。
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)**（作者：**hotwords123**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952)。Combat Solver 的战斗模拟引擎核（战斗状态、牌堆、RNG、Fork、历史、Mirror）部分来自其实现，并在获得 hotwords123 书面许可后进行改造。本 Mod 仅 Vendoring 引擎源码，**不加载/分发 Random Foreseer 程序集作为运行时依赖**。

> 第三方来源代码遵循**原作者许可边界**：Combat Solver 仓库无统一许可证；Random Foreseer 来源代码以 Combat Solver 的 `THIRD_PARTY_NOTICES.md` 为准。上述来源不受本仓库 AGPL-3.0-only 自动覆盖。

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).
