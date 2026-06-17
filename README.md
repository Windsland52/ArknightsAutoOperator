# ArknightsAutoOperator

明日方舟自动凹图：帧级自动操作 + 费用条计时 + 打轴对轴。

MaaFramework **方案二**（JSON pipeline + 进程内 custom action）+ PySide6 单进程主控台 + loguru 日志。
仅支持 **PC 官方客户端**（Win32，`Arknights.exe`），不再适配模拟器。

> 总体方案见 [`docs/rewrite-plan.md`](docs/rewrite-plan.md)。

## 功能

- **凹图**：自动点关卡 → 开战 → 帧级执行时间轴 → 结算 → 三星停 / 非三星或漏怪放弃重试，循环到通关或达次数上限
- **打轴**：F8/F9/F10 标记部署/技能/撤退，时间轴 canvas 双刻度（frame + 秒），节点可拖拽改帧
- **对轴**：游标跟随实时帧，临近节点磁铁高亮，校验时间轴能否稳定复现
- **校准**：费用条像素宽 → 逻辑帧映射（Jaccard 聚类），支持多档 profile（交替回费）
- **悬浮窗**：置顶显示实时帧 / 计时器 / profile
- **WebSocket API**：实时帧状态广播（`ws://localhost:2606`）

## 开发

```bash
uv sync
uv run python -m aao.app              # 启动主控台（可选 --profile xxx.json）
uv run python -m aao.resources.syncer # 同步干员名 + 地图数据
```

前置：管理员终端（PostMessage 输入需 UIPI 权限）+ AFA（arknights-frame-assistant）常驻 + 游戏窗口前台。

## 项目结构

```txt
aao/        应用主体（UI / 计时 / 打轴 / 测量 / 资源 / 工具）
  app.py    主控台入口（QMainWindow 侧栏：凹图/打轴/校准/设置）
  core/     timing（费用条计时）/ geometry（投影）/ battle / avatar / afa_hotkey
  measure/  测量 worker + 悬浮窗 + WebSocket API
  timeline/ 时间轴模型 + 打轴编辑器
  ui/       各功能页 + farm_worker + canvas + map_picker
  resources/ 干员名/地图同步 + 更新检查
custom/     MAA custom 实现（pipeline 调用）
  agent.py  MAA 子进程入口（interface.json 的 child_exec）
  action/   ExecuteTimeline（帧级执行）/ KeyPress
  reco/     ClickStage（关卡 OCR + 凹图计数）
  registry.py / outcome.py
resource/   MAA pipeline（farm.json 自循环）/ image 模板 / ocr 模型
config/     calibration（校准）/ timelines（时间轴）/ settings.json
debug/      日志（aao/YYYY-MM-DD.log，按天轮转保留 2 周）
```

## 日志

loguru 双 sink：

- **控制台 / UI 日志面板**：仅消息，去来源，INFO
- **文件 `debug/aao/*.log`**：完整含来源（模块:函数:行），DEBUG，按天轮转、保留 2 周、zip 压缩

## 鸣谢

### 依赖

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别自动化框架（pipeline + 进程内 custom action）
- [PySide6](https://www.qt.io/) — Qt UI
- [loguru](https://github.com/Delgan/loguru) — 日志
- [numpy](https://numpy.org/) / [websockets](https://websockets.readthedocs.io/) / [Pillow](https://python-pillow.org/) / [json5](https://github.com/dpranke/pyjson5)

### 捆绑组件

- [**ArknightsFrameAssistant (AFA)**](https://github.com/CloudTracey/arknights-frame-assistant)（GPL-3.0）— 凹图的暂停/步进/技能/撤退依赖 AFA 的全局热键。**发版包自带 AFA.exe**（从上游 release 下载），启动时自动拉起。AFA 源码与许可见上游仓库；本仓库不持有其版权，仅作分发。

### 参考项目

- [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) — 地图数据（`Arknights-Tile-Pos`，运行时同步）+ 粗流程参照
- [ArknightsCostBarRuler](https://github.com/ZeroAd-06/ArknightsCostBarRuler) — 费用条计时 / 悬浮窗 / 打轴对轴（测量层、校准、tick 检测来源）
- [prts-plus](https://github.com/jue-ce-zhe/prts-plus) — 帧级自动操作的执行器算法（action / 投影 / 配置）
