# 尖塔2陪玩（sts2_autoplay）

陪伴你在《杀戮尖塔2》（Slay the Spire 2）中游玩的 N.E.K.O 插件：实时监听本地 STS2-Agent 的局势快照，提供弹幕陪玩浮层、自动出牌、局势点评与猫娘 LLM 实时解说。

## 功能

- **弹幕陪玩浮层**：`qt_overlay.py` 驱动的全屏弹幕层，把猫娘的实时点评以弹幕形式展示在游戏窗口上。
- **自动出牌**：基于策略文件的启发式规划器，可在战斗中自动推荐 / 执行出牌。
- **局势点评**：对每回合局面生成风险等级、打法建议与评论，通过弹幕浮层和前端通知推送。
- **猫娘 LLM 解说**：可选的本地 LLM 频道，为关键局面生成更有趣的解说。
- **策略配置**：`strategies/` 目录下的角色策略文件（铁甲战士 / 故障机器人等），支持按场景覆盖。

## 依赖

插件本体不引入外部 Python 依赖（`pyproject.toml` 依赖列表为空），运行时依赖 N.E.K.O 主机提供的能力（`plugin.sdk`）。Qt 弹幕浮层通过独立子进程脚本 `qt_overlay.py` 运行，使用系统 Python + PyQt6（参见 `install_pyqt6.py`）。

## 安装

1. 将本目录放入 N.E.K.O 的 `plugin/plugins/sts2_autoplay/`。
2. 启动 N.E.K.O 并在插件市场启用 `sts2_autoplay`。
3. 按需填写配置（参见 `config.example.toml`），其中 `sts2.base_url` 指向本地 STS2-Agent 服务（默认 `http://127.0.0.1:8080`）。

## 配置

- 运行时配置保存在 `config.example.toml`（`[sts2]` 与 `[plugin_runtime]`），以 `config.example.toml` 为默认模板。
- 常用项：`base_url`、`autoplay_on_start`、`character_strategy`、`danmu_overlay_enabled`、`catgirl_llm_enabled`、`neko_commentary_enabled` 等。

## 测试

```bash
python -m pytest tests/
```

## 许可

本项目为 N.E.K.O 插件，遵循其开源许可。
