# 快速开始



## 使用教程

### 获取MOD



### 安装游戏 Mod




### 设置mod端口

mod默认监听端口为18080，虽然是冷门端口，但依然可能会被占用。

可以打开环境变量，在全局变量处新建变量 STS2_API_PORT ，值可以选择一些无占用端口

样例 变量名："STS2_API_PORT" 变量值："38080"

Ps：环境变量可以通过一下方式快速打开：

- 按下 Win + R 组合键，打开“运行”对话框。

- 输入以下命令并按回车：

```
rundll32 sysdm.cpl,EditEnvironmentVariables
```

### 启动游戏并确认接口

先正常启动游戏，让 Mod 随游戏加载。

第一次加载 mod 后如果游戏出现一次异常退出，重新启动游戏即可。

在加载mod后，在NEKO中，启用猫爪，开启插件，进入插件面板，手动启动杀戮尖塔插件

如果你在打开插件或初始化插件的同时，刚好也在《杀戮尖塔 2》里进行操作，插件第一次回应可能会比平时慢一拍，属于正常现象。等当前局面同步完成后，后续响应通常会恢复正常。

### 运行依赖：RitsuLib

本 Mod 的战斗求解器**依赖第三方共享框架库 [STS2-RitsuLib](https://steamcommunity.com/sharedfiles/filedetails/?id=3747602295)**（卡效 IL 推断等）。请确保：

- Steam 创意工坊订阅了 **STS2-RitsuLib**（自动下载到 `steamapps/workshop/content/2868840/3747602295/`）；
- 游戏 `mods/` 目录里有能解析到的 `STS2-RitsuLib.dll`（本 Mod 通常靠它解析；部署脚本 `build-mod.ps1` 不会自动拷贝，若缺失请手动放置，否则 mod 会报 `ReflectionTypeLoadException`）；
- `mod_id.json` / `mod_manifest.json` 的 `dependencies` 建议声明为**新式对象**（带 `min_version`，如 `[{"id":"STS2-RitsuLib","min_version":"0.5.18"}]`）以避开旧式依赖的迁移提示。

### 重新部署

改代码后需重打并拷入游戏。**务必先退出游戏**（`neko_comm.dll` 被游戏进程占用时 `Copy-Item` 会失败）。可用仓库脚本：

```
game_mod/scripts/build-mod.ps1 -Configuration Release -GameRoot "D:/Steam/steamapps/common/Slay the Spire 2" -GodotExe <路径>\Godot_v4.5.1-stable_win64_console.exe
```

（`STS2_DATA_DIR` 指向游戏数据目录；默认 C: 路径会导致 CS0246。）

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

## 第三方致谢

本 Mod 的战斗求解器核心 **Vendoring 并深度适配**了以下第三方项目，特此致谢：

- **[Combat Solver](https://github.com/Torch1230/CombatSolver)**（作者：**Torch / Torch1230**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3790899961)。战斗路线求解器。其 **0.28 搜索/模拟核心**被本 Mod 内嵌并适配，用于 `GET /solver/plan` 的战斗决策建议。已获作者授权使用。
  - 卡效推断使用 **RitsuLib** 的 `HarmonyIl` / `RitsuLibFramework.GetMaxHandSize` / `IComputedDynamicVar`（提升保真度：不再有 `unsupported` 卡模）；
  - 引擎对游戏私有成员的直接访问改为 **`GameRef` 反射**（端口不依赖编译期 publicize / 运行时 publicize）；
  - **未**保留 `Krafs.Publicizer`（已确认纯 `GameRef` 反射即可编译运行）。
- **[Random Foreseer](https://github.com/hotwords123/StS2.RandomForeseer)**（作者：**hotwords123**）—— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3747531952)。Combat Solver 的战斗模拟引擎核（战斗状态、牌堆、RNG、Fork、历史、Mirror）部分来自其实现，并在获得 hotwords123 书面许可后进行改造。本 Mod 仅 Vendoring 引擎源码，**不加载/分发 Random Foreseer 程序集作为运行时依赖**。

> 第三方来源代码遵循**原作者许可边界**：Combat Solver 仓库无统一许可证；Random Foreseer 来源代码以 Combat Solver 的 `THIRD_PARTY_NOTICES.md` 为准。上述来源不受本仓库 AGPL-3.0-only 自动覆盖。

本 Mod 的MCP模块参考：

- **[STS2-Agent](https://github.com/CharTyr/STS2-Agent)**（作者：**杉茶 / CharTyr**）—— AI Agent Mod。在可视化窗口里配置模型端点、对话、按模型调整思考强度、让模型自动游玩。其具体数据来源本 MOD 并未过多修改。

本 Mod 的弹幕模块参考：

- **[danmuai](https://github.com/PEPETII/danmuai)**（作者：**timerome / PEPETII**）—— 让屏幕内容拥有自己的 AI 弹幕。看到这个仓库才诞生的加入猫娘弹幕的功能，故写致谢里。

- **[弹幕尖塔 DanmakuSpire]**（作者：**Icnsis**） —— [创意工坊页面](https://steamcommunity.com/sharedfiles/filedetails/?id=3779807977)。本 MOD 主要借鉴了该 MOD 对于弹幕显示的处理，并未直接使用。他的样式也很好看，故写致谢里。

**感谢开源**我嘞个缝合怪。

## License

This project is licensed under the GNU Affero General Public License v3.0 only (AGPL-3.0-only).
