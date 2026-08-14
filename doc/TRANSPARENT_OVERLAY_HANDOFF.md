# STS2 陪玩点评 — 透明弹幕浮层打包交接

sts2_autoplay 插件的陪玩点评弹幕浮层有两条宿主路径：

1. **首选：Electron 壳透明窗**（`neko_plugin_dashboard` 窗口）
2. **保底：Qt 透明窗**（PyQt6，本插件自带 `qt_overlay.py` + 前端控制）

本文件面向**打包/壳仓库（N.E.K.O.-PC）**维护者，说明首选路径需要的壳改动。

---

## 首选路径：Electron 壳透明窗

### 数据与页面（后端已就绪，本仓库）

- 弹幕页：`/plugin/sts2_autoplay/ui/`（插件 static UI，已注册 `register_static_ui`）
- 数据流：`POST /plugin/sts2_autoplay/ui-api/push` + SSE `/plugin/sts2_autoplay/ui-api/events`
- 弹幕页内 SSE 地址自动解析到 `../ui-api/events`（同源）

### 触发打开（壳侧拦截）

前端/插件从 **Pet 窗口上下文**执行：

```js
window.open(
  `http://127.0.0.1:${BACKEND_PORT}/plugin/sts2_autoplay/ui/`,
  'neko_plugin_dashboard'   // 壳已识别的插件专用窗口名
)
```

壳的 `pet-window-lifecycle.js` 已把 `windowName='neko_plugin_dashboard'` 识别为插件仪表盘窗口
（`isPluginDashboardPopup`，`childWindow._nekoKind='pluginDashboard'`），并按
`getManagedTopLevel()` 给 `screen-saver` 级 alwaysOnTop（Windows 可盖无边框全屏游戏）。

### 需要壳做的改动（打包时）

当前插件仪表盘窗口**默认不透明**。为弹幕浮层开启透明，需在
`src/main/pet-window-lifecycle.js` 的 pluginDashboard 窗口创建选项（`setWindowOpenHandler`
→ `overrideBrowserWindowOptions`，约 line 880 附近）中，当 `isPluginDashboardPopup(details)`
时追加：

```js
transparent: true,
frame: false,
backgroundColor: '#00000000',
hasShadow: false,
focusable: false,
skipTaskbar: true,
resizable: false,
```

并在 `did-create-window` 里对 `childWindow._nekoKind === 'pluginDashboard'` 追加：

```js
childWindow.setIgnoreMouseEvents(true, { forward: true });
```

> 注意：`isTransparentWindow()`（`top-coordinator.js`）目前只把 Pet/chat/agentHud/toast
> 列为透明窗口。弹幕窗加入后，若需要参与 topmost 重断言，可把 `pluginDashboard`
> 一并纳入透明窗口判定（避免闪烁 moveTop）。

---

## 保底路径：Qt 透明窗（本仓库已实现）

无需壳改动。浮层由插件 **LLM entry** 控制（不提供 HTTP 管理端点）：

- `sts2_overlay_status`：查询运行状态 + PyQt6 是否可用（附 `pip install PyQt6` 提示）
- `sts2_overlay_start`：用带 PyQt6 的 python 拉起 `qt_overlay.py` 子进程，订阅 SSE 浮层数据流
- `sts2_overlay_stop`：终止浮层进程（含兜底杀掉所有指向本插件 SSE 的 `qt_overlay.py`）
- `sts2_install_pyqt6`：一键安装 PyQt6（联网，可能耗时）

`qt_overlay.py` 用 PyQt6 建透明置顶窗（`FramelessWindowHint | WindowStaysOnTopHint | Tool`
+ `WA_TranslucentBackground` + `WA_TransparentForMouseEvents` + Win32
`WS_EX_LAYERED|WS_EX_TRANSPARENT`），订阅 SSE 事件流，原生 Qt 渲染多轨道滚动弹幕。

弹幕数据流（web 与 Qt 共用）：插件把点评/规则弹幕 POST 到 `/plugin/sts2_autoplay/ui-api/push`，
服务端广播到 SSE `/plugin/sts2_autoplay/ui-api/events`；web 弹幕页 `/plugin/sts2_autoplay/ui/`
与 Qt 浮层订阅同一条 SSE。PyQt6 为可选安装，不进主 requirements。

---

## 建议

首选 Electron 透明窗体验最稳（合成器级透明 + 屏幕保护级置顶，与 Pet/chat 同体系）。
Qt 保底在壳未更新透明支持、或用户未用壳（Web/开发环境）时可用。
