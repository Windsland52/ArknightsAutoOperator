# ArknightsAutoOperator

明日方舟赛博塑料，自动凹图（帧级自动操作 + 费用条计时 + 打轴对轴）。

> **状态**：正推翻 Go + Wails 重写为 Python（MaaFramework **方案二** pipeline + 进程内 custom action + PySide6）。
> 总体方案见 [`docs/rewrite-plan.md`](docs/rewrite-plan.md)。

## 开发

```bash
uv sync
uv run python agent/main.py --mode win32   # MAA 进程内接线 + 截图 smoke（里程碑 1）
# 或 --mode adb 连模拟器
```

## 鸣谢

### 开源库与参考项目

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别自动化框架（pipeline + 自定义扩展）
- [PySide6](https://www.qt.io/) — Qt UI
- [prts-plus](https://github.com/jue-ce-zhe/prts-plus) — 模拟器帧级自动操作（执行器算法来源）
- [ArknightsCostBarRuler](https://github.com/ZeroAd-06/ArknightsCostBarRuler) — 费用条计时 / 悬浮窗 / 打轴对轴（测量层来源）
- [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) — 地图数据 / 投影 / 粗流程参照
- [arknights-frame-assistant](https://github.com/CloudTracey/arknights-frame-assistant) — PC 端帧操（PC 输入模型来源）
