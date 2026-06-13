# ArknightsAutoOperator — 推推翻重写计划

> 本文件为已批准的总体重写计划（2026-06-14），原档位于 `.claude/plans/`。实施进度按「构建顺序（里程碑）」推进。

## Context（为什么重写）

当前 Go + Wails 项目**不可用**，且历史规划不完善：

- **执行器是空壳**：`backend/battle/executor.go:173` 把部署头像写死 `LastOperRatio(0.95,0.9)`；`executor.go:408` 用 `PostSwipe` 冒充右键拖拽部署（方舟部署是右键拖拽，swipe 在 PC 客户端不生效）。
- **费用从不识别**：`backend/vision/time_tracker.go:120` 返回 `Cost:0` 注释 `// needs OCR`，动作表 cost 字段被忽略。
- **头像匹配是纯 Go NCC**（`avatar.go`），自承「consider using gocv」，无法实时。
- **仅 Win32**：`StartApp/StopApp` 是 no-op，不支持模拟器；窗口枚举是手搓 `EnumWindows`。
- **悬浮窗/打轴对轴/WebSocket API 全无**；前端是单个 `App.vue`。
- **架构耦合**：`app.go/main.go/battle_api.go` 全塞 `package main`，到处 `os.Executable()`。

**走的是 MAA 的「方案三·全代码」路线**——而 MAA 官方明确不推荐该路线（失去可视化编辑/调试/通用 UI 生态）。

两个参考项目分两层、久经验证：
- **prts-plus**（Python，MuMu 专用）= 执行器层（子弹时间同步 + 逐帧推进 + 真·右键拖拽部署/技能/撤退）。
- **ArknightsCostBarRuler**（Python，多模拟器）= 测量+UI 层（Jaccard 聚类校准、无边框置顶悬浮窗、托盘、WebSocket API、打轴对轴器）。

**预期成果**：基于 MaaFramework 官方推荐的**方案二（JSON pipeline + 自定义扩展）**重写，遵循 `../maa-project`（`create-maa-project` 脚手架）的项目规范。pipeline 跑粗流程（进关/开战/结算/开/弹窗），一个进程内 Custom action 跑帧级执行器，集成应用提供费用条计时+悬浮窗+WebSocket+打轴对轴编辑器。一次交付「核心自动凹图 + 实时帧数悬浮窗 + 打轴对轴」。

## 关键判断：MAA = pipeline + 自定义（方案二），不是纯代码

依据 `../MaaFramework` 文档与 `sample/python/demo1.py`、`demo3_agent.py`：

- **三档集成**：方案一纯 JSON / 方案二 JSON+Custom（官方推荐）/ 方案三全代码（官方不推荐）。
- **右键拖拽部署被 MAA 直接解决**：`controller.post_touch_down/move/up(contact=1)`——Win32 下 `contact=1` 即右键。部署 = down→move→up(contact=1)。
- **多平台自带**：`Win32Controller`（PC 客户端）+ `AdbController`（MuMu/雷电），同一套 `post_*`。满足「保留 MAA 但多平台」。
- **窗口/设备发现自带**：`Toolkit.find_desktop_windows()` / `Toolkit.find_adb_devices()`，替换手搓 EnumWindows。
- **帧级执行器 = Custom action 标准用例**：prts-plus 的子弹时间+逐帧作为 `@resource.custom_action("ExecuteTimeline")` 被 pipeline 调用，内部用 `context.tasker.controller` 跑紧循环。
- **执行器内可回调 MAA 识别**：`context.run_recognition(...)` 调用 MAA 内置 TemplateMatch/OCR（C++ 优化）——头像定位可直接复用。

## 技术栈与项目规范

- **性能不影响帧级精度**：精度来自游戏机制（子弹时间降速 + `steptiny` 每次精确推进一帧），程序每帧是 I/O 密集（截图=原生 Win32/MAA；阈值/计数=cv2/numpy 的 C 实现），Python 仅编排（µs vs ms）。参考项目在 Python 下已实现帧级操作。**性能对冲**：把「截图+tick 检测」隔离成独立模块，实测抖动可平替 ctypes/Cython/Rust 原生扩展。
- **栈**：Python ≥3.13，`maafw`（pip 发布版，控制器+识别+pipeline 引擎）、`opencv-python`/`numpy`（费用条 tick）、**PySide6**（Qt；`QGraphicsView` 做时间尺+等距地图选点，`FramelessWindowHint`+`WindowStaysOnTopHint`+`setWindowOpacity`/`WA_TransparentForMouseEvents` 做悬浮窗；LGPL）、`websockets`（API）。OCR 费用走 MAA OCR 节点或自带 PaddleOCR-onnx。
- **项目规范（参考 `../maa-project` = `create-maa-project` 脚手架）**：采用其目录与工具链——`custom/` 包、`resource/base/` bundle、`tasks/*.json`、`interface.json`（ProjectInterface v2）、`pyproject.toml`（**uv**+`ruff`+`pyright`）、`package.json`（**pnpm**+`@nekosu/maa-tools`+`prettier`+`prettier-plugin-maafw-sort`）、`tools/*.mjs`（check-project/validate-schema/optimize-images/sync-schema/build-release）、`.github/workflows/`（check/format/schema-sync/optimize-images/release）、`logo.ico`、`.vscode/launch.json`。**可先用 `create-maa-project`（MCP 工具 `mcp__create-maa-project__create_project`）生成骨架再改造。**
- **已定决策**：① **Option B 自建集成应用**——我们自己写 主控台+悬浮窗+编辑器，单 Python 进程拥有 Tasker/Resource/Controller；不依赖通用 UI(MFAAvalonia)。② Custom action **进程内注册**（`@resource.custom_action(...)`，参考 `../MaaFramework/sample/python/demo1.py`），不用 AgentServer 子进程。③ MAA 依赖走 pip `maafw`。④ 打轴**手动+磁铁标记**（无需视觉事件识别）。⑤ 编辑器**独立窗口**。⑥ 坐标**可视化地图选点**。⑦ UI 框架 **PySide6 (Qt)**（编辑器体验/悬浮窗原生最佳，LGPL）。

## 目标架构（Option B：自建集成应用 + create-maa-project 规范）

```
arknights-auto-operator/
├── pyproject.toml            # uv: maafw/opencv/numpy/PySide6/websockets; dev: ruff/pyright; py313
├── package.json              # pnpm: @nekosu/maa-tools, prettier, prettier-plugin-maafw-sort
├── interface.json            # ProjectInterface v2: controller(Win32+ADB) / resource(base) / tasks
├── logo.ico  README.md  README.en.md  .vscode/launch.json
├── custom/                    # 主 Python 包（Option B 集成应用，进程内 MAA）
│   ├── main.py               # ★集成应用入口：建 controller+resource+tasker（进程内），启动 主控台+悬浮窗
│   ├── runtime.py            # 拥有 Tasker/Resource/Controller；进程内注册 custom；运行 pipeline（改编自 agent_runtime.py）
│   ├── bootstrap.py          # Python 版本/依赖检查（沿用）
│   ├── config.py             # 比例/阈值/路径（prts-plus config.py + CostBarRuler CONFIG）
│   ├── maa/                  # MAA 自定义扩展（进程内注册）
│   │   ├── action/executor.py   # ★ @resource.custom_action("ExecuteTimeline")：子弹时间+逐帧+右键拖拽部署/技能/撤退
│   │   └── reco/                # 可选自定义识别（费用条 tick 作 recognition）
│   ├── core/
│   │   ├── timing/{tick,calibration,time_source,cost_ocr}.py   # ★统一 tick 源 + 校准 + 费用 OCR
│   │   ├── geometry/view.py     # 3D 投影 格子→屏幕比例 正/侧（prts-plus calc_view）
│   │   ├── battle/{action,game_time,convert_pos}.py            # 动作数据（prts-plus）
│   │   └── avatar.py            # 头像定位（优先 context.run_recognition TemplateMatch；回退 cv2）
│   ├── measure/              # 独立"尺子"层：采集循环→time_source→悬浮窗+API
│   │   ├── worker.py  overlay.py  api_server.py
│   ├── timeline/             # 打轴/对轴
│   │   ├── model.py  io.py  editor_window.py  timeline_canvas.py  action_panel.py
│   │   ├── map_picker.py        # ★可视化地图选点：地图数据+view投影渲染格子，点击→tile→坐标
│   │   └── xlsm_import.py       # prts-plus .xlsm 导入
│   ├── ui/                   # 主控台 + 托盘 + 热键
│   │   ├── main_window.py  tray.py  hotkeys.py
│   ├── resources/syncer.py   # 下载地图/头像/映射（backend/resource/syncer.go 逻辑）
│   └── utils/                # logger/params/runtime_paths（沿用 create-maa-project）
├── resource/base/            # MAA Bundle（方案二）
│   ├── default_pipeline.json
│   ├── pipeline/farm.json       # ★粗流程：进关/开战/识别战斗开始→ExecuteTimeline→结算/重开/弹窗
│   ├── image/                   # OCR/TemplateMatch 模板图
│   └── model/ocr/               # PaddleOCR-onnx（det/rec/keys，复用 MaaCommonAssets）
├── tasks/*.json              # 任务入口（进关/校准/执行 等，interface.json 引用）
├── data/                     # 战斗数据（非 MAA bundle）：map/ level_code_mapping.json operator_mapping.json avatar/
├── config/                   # 运行时配置：校准 profile、控制器配置、设置
├── tools/*.mjs               # check-project/validate-schema/optimize-images/sync-schema/build-release（改编）
├── .github/workflows/        # check/format/schema-sync/optimize-images/release（release 改为 PyInstaller 打包我们的应用）
├── reference/                # 保留（算法事实来源）
└── tests/
```

### 关键设计

1. **统一 tick 源（`custom/core/timing/time_source.py`）**：CostBarRuler 校准像素图作唯一时间真值，同时供悬浮窗显示与执行器同步。执行期由执行器 Custom action 驱动采集（紧循环里本就每帧截图），纯尺子模式由 `measure/worker` 驱动；二者不同时跑，避免对单一控制器争抢。移植 `ruler/main.py` 的 `analysis_worker`：周期检测(>0.75\*total→<0.25\*total 完成一轮) + 全局计时器(`offset+cycle_base+logical_frame`) + **多档 profile 按 cycle 轮换**(`cycle_counter%num_profiles`，处理交替回费) + 显示模式(0..n-1/0..n/1..n) + 1.5s 无检测超时重置；线程+队列(maxsize=1 丢旧)驱动 UI/API。tick 三函数（ROI/普通+遮罩双模式/容差5）从 `ruler/utils.py` 移植并 numpy 向量化。截图默认走 MAA Win32/ADB 控制器；若对明日方舟 Unity 窗口不稳，回退 CostBarRuler `controllers/windows.py`（PrintWindow+PW_RENDERFULLCONTENT）。
2. **执行器 = 进程内 Custom action（`custom/maa/action/executor.py`，平台感知）**：pipeline 节点 `{"action":"Custom","custom_action":"ExecuteTimeline","custom_action_param":{timeline_id,calib,...}}` 触发；内部循环 `ctrl.post_screencap→tick→子弹时间同步→逐帧推进→部署/技能/撤退`；每步检查 `context.tasker.stopping` 优雅停止；`context.run_recognition` 复用 MAA TemplateMatch 找头像。**输入分两套策略**：① **模拟器(ADB)= prts-plus 路线**（steptiny Esc+点击过帧、SKILL/RETREAT_RATIO 鼠标技能撤退、右键拖拽部署 `post_touch_*(contact=1)`）；② **PC(Win32/`Arknights.exe`)= AFA 路线**（**定时过帧**：ESC 恢复→高精度 sleep ~30ms(1x)/~165ms(0.2x)→Space 暂停；键盘 E 技能 / Q 撤退；拖拽部署）。游戏原生按键：Space 暂停 / ESC 菜单 / D 变速 / E 技能 / Q 撤退 / V 放弃。**PC 过帧需高精度 sleep**（ctypes `QueryPerformanceCounter` 自旋——Python `time.sleep` Win 下 ~15ms 粒度不够）；**PC 暂停操作"有缝"**（AFA 注：PC 输入限制致非完美逐帧）→ 执行器过帧后用费用条 tick 复核、偏差则补偿；**极限精度建议走模拟器路线**。
3. **粗流程 = pipeline JSON（`resource/base/pipeline/farm.json`）**：进关/开战/识别战斗开始/跑时间轴/结算/重开赛博塑料循环/弹窗（`jump_back`/`anchor`），OCR+TemplateMatch 驱动，比手写鲁棒、可可视化调试。
4. **多平台自动**：`Win32Controller`+`AdbController`，contact 语义两平台一致（Win32 contact=1=右键；ADB=手指），右键拖拽部署两平台通用。
5. **测量/UI 为集成应用的一部分**（非独立程序）：悬浮窗+WebSocket+打轴对轴编辑器与执行器共享同一进程的控制器与 time_source；纯尺子模式可单独跑（不执行任务时）。

### 复用映射

| 参考文件 | 新位置 |
|---|---|
| `reference/prts-plus/logic/perform_action.py` | `custom/maa/action/executor.py` |
| `reference/prts-plus/logic/analyze_time.py` | `custom/core/timing/tick.py` + `cost_ocr.py` |
| `reference/prts-plus/logic/calc_view.py` | `custom/core/geometry/view.py` |
| `reference/prts-plus/logic/locate_avatar.py` | `custom/core/avatar.py`（优先 MAA TemplateMatch） |
| `reference/prts-plus/logic/{action,game_time,convert_pos}.py` | `custom/core/battle/*` |
| `reference/prts-plus/config.py` | `custom/config.py` |
| `reference/ArknightsCostBarRuler-master/ruler/calibration_manager.py` | `custom/core/timing/calibration.py` |
| `reference/ArknightsCostBarRuler-master/ruler/utils.py`（`find_cost_bar_roi`/`_get_raw_filled_pixel_width`普通+遮罩/`get_logical_frame_from_calibration`容差5） | `custom/core/timing/tick.py`（**numpy 向量化**提速） |
| `reference/ArknightsCostBarRuler-master/ruler/main.py`（`analysis_worker`：周期检测/全局计时器/多档 profile 按 cycle 轮换/显示模式/1.5s 超时重置/线程+队列） | `custom/measure/worker.py` + `custom/core/timing/time_source.py` |
| `reference/ArknightsCostBarRuler-master/ruler/overlay_window.py` | `custom/measure/overlay.py`（Qt 重写） |
| `reference/ArknightsCostBarRuler-master/ruler/api_server.py` + `API.md`（`{isRunning,currentFrame,totalFramesInCycle,totalElapsedFrames,activeProfile}`） | `custom/measure/api_server.py` |
| `reference/ArknightsCostBarRuler-master/timeline_tool/app.py`（时间尺/菱形节点/磁铁/惯性/吸附/声光提醒/打轴·对轴双模式；节点为通用 `{frame,name,color}`） | `custom/timeline/timeline_canvas.py`（Qt QGraphicsView）+ 打轴/对轴；**在此扩展动作语义**(oper/pos/direction) |
| `reference/ArknightsCostBarRuler-master/ruler/controllers/windows.py`（PrintWindow+PW_RENDERFULLCONTENT 三级回退） | 测量层截图**回退后端**（若 MAA Win32 截图对 Unity 窗口不稳） |
| `backend/resource/syncer.go`（逻辑） | `custom/resources/syncer.py` |
| `../maa-project`（目录/工具链/CI） | 根目录 `pyproject.toml`/`package.json`/`tools/`/`.github/`/`interface.json` |
| MAA 控制器/识别/发现/pipeline 引擎 | `maafw` 直接用 |
| `../MaaAssistantArknights/resource/Arknights-Tile-Pos/*.json` | `data/map/`（全量关卡地图，格式与 prts-plus calc_view 一致，直接喂 `geometry/view.py`）|
| `../MaaAssistantArknights/3rdparty/include/Arknights-Tile-Pos/TileCalc2.hpp` | 验证 `geometry/view.py` 投影（prts-plus calc_view 是其 numpy 移植）|
| `../MaaAssistantArknights/resource/tasks/{Stages/base.json,UiTheme/Terminal.json,Copilot/formation.json}` | 编写 `resource/base/pipeline/farm.json` 的模式参照（ROI/OCR 文本/流转/循环，字段名翻成 MaaFW pipeline）|
| `reference/arknights-frame-assistant-main/src/lib/hotkey_actions.ahk`（PC 过帧/技能/撤退/快捷键；AHK v2，GPL-3.0） | `custom/maa/action/executor.py` 的 **PC 输入策略**（定时过帧 + 键盘 E/Q 技能撤退）；技术参照，Python 重实现 |

### 参考 MaaAssistantArknights（maafw 基石，帧级非其重点但作参照）

- **地图数据直接来自 MAA**：`resource/Arknights-Tile-Pos/` 全量自带每关 `{code,height,width,view[2],tiles[][]{heightType,buildableType,tileKey}}`，与 prts-plus `calc_view` 输入格式完全一致。**直接 vendor 进 `data/map/`（或 syncer 按需拉取）**，替代原项目「下载 map + level_code_mapping」的做法；文件名即关卡代号（如 `main_01-07-...`）。干员头像/operator_mapping 仍由 `resources/syncer.py` 同步（MAA tile-pos 不含）。
- **投影算法对齐**：`geometry/view.py` 移植自 prts-plus `calc_view`，用 `TileCalc2.hpp`（正统 C++ 实现）交叉验证，避免投影误差。
- **粗流程参照**：`farm.json`（进关/开战/结算/重开/弹窗/理智）参照 MAA `Stages/base.json`+`UiTheme/Terminal.json` 的 OCR/ROI/循环模式 + `Copilot/formation.json`（MAA 自动战斗，与我们最接近），翻译为 MaaFW pipeline 字段（`recognition`/`action`/`roi`/`expected`/`next`/`post_delay`）。

### 丢弃

- 全部 Go/Wails：`app.go/main.go/battle_api.go/backend/*/go.mod/wails.json`、`frontend/`。校准逻辑从 Python 原版重新导入。
- 保留：`reference/`、`data/`（地图改取自 MAA Arknights-Tile-Pos，其余已同步资产迁移）、`.github/`（CI 按新栈改写）。

## 界面与功能设计（已定：手动+磁铁打轴 / 编辑器独立窗口 / 可视化地图选点）

**三窗口 + 托盘：**

- **主控台**（Qt 主窗，标签）：**连接**（Toolkit 发现+测试+截图/输入方式）/ **校准**（profile 列表+引导新建）/ **运行**（选轴+profile+关卡，开始/停止，实时：当前帧/当前动作/下一动作/已用时长/截图预览/日志，赛博塑料循环设置）/ **资源**（地图/头像同步，代理+token）+ 「打开打轴编辑器」入口。
- **悬浮窗**（Qt 无边框置顶半透明，可点穿透）：`FramelessWindowHint`+`WindowStaysOnTopHint`+`setWindowOpacity`（+可选 `WA_TransparentForMouseEvents` 点击穿透）；实时帧 + 全局计时器(MM:SS:FF) + 停表；状态 idle/pre-cal/calibrating/running；QSystemTrayIcon 托盘菜单镜像。
- **打轴/对轴编辑器**（独立 Qt 窗口）：
  - **打轴模式**：顶栏（关卡/profile/打开保存 JSON/模式切换/磁铁开关）+ 左动作列表表（QTableWidget：时间·类型·干员·坐标·朝向·有效）+ 中**时间尺**（`QGraphicsView`/`QGraphicsScene`：费用大刻度/帧小刻度，可拖拽动作节点，磁铁开启显示实时帧游标，「标记此刻」以此刻建动作）+ 右动作编辑面板（类型/干员下拉/朝向/时间/简称/有效性）。
  - **可视化地图选点**（`custom/timeline/map_picker.py`）：`QGraphicsView` + 地图数据 + view 投影渲染关卡格子（可部署格高亮），点击→tile→坐标；技能/撤退可复用已部署干员的格子（记忆）。
  - **对轴模式**：时间轴只读，游标由实时 `time_source` 驱动并跟随滚动；每节点提前提醒时间/声音/视觉开关，用于演练校验。
- **全局热键**（`custom/ui/hotkeys.py`）：磁铁标记此刻、紧急停止（游戏抢焦点时可用）。

**功能清单：** 连接(Win32+ADB 多平台)；校准(多档 profile/单档·交替/隐藏辉光帧)；费用条实时计时+费用 OCR；打轴编辑器(手动+磁铁+可视化选点)+JSON 时间轴+prts-plus `.xlsm` 导入；对轴演练(声/光提醒)；执行器(子弹时间+逐帧+右键拖拽，进程内 Custom action)；赛博塑料循环刷图(pipeline：重进关/重跑/结算/掉落计数/理智止)；悬浮窗+托盘+WebSocket API；资源同步；调试(MAA debug：save_draw/save_on_error/vision draws 日志面板)。

## 构建顺序（里程碑）

每步可独立验证。里程碑 3 即交付可独立使用的「费用条尺子」（与 CostBarRuler 等价）。

1. **脚手架 + MAA 接线**：用 `create-maa-project`（MCP）生成骨架；改编 `custom/main.py`+`runtime.py` 为**进程内**拥有 Tasker（`resource.register_custom_action` 而非 AgentServer）；`Toolkit` 发现 Win32/ADB 控制器 + resource bundle + 一个截图/点击 smoke。落地 `pyproject.toml`(uv)/`package.json`(pnpm)/`tools/`/`.github/`。
2. **费用条计时**：`core/timing/tick.py` + `calibration.py` + `time_source.py`。
3. **悬浮窗 + WebSocket API**：`measure/overlay.py` + `api_server.py`——此步后即有可用尺子。
4. **几何 + 头像 + 费用 OCR + 地图数据**：vendor MAA `Arknights-Tile-Pos`→`data/map/`；`core/geometry/view.py`（对齐 `TileCalc2.hpp`）；`core/avatar.py`（MAA TemplateMatch）；`core/timing/cost_ocr.py`。
5. **执行器（进程内 Custom action，平台感知）**：`custom/maa/action/executor.py`——子弹时间同步+逐帧+部署/技能/撤退；**两套输入策略**（模拟器 prts-plus / PC AFA 定时过帧+键盘）+ **高精度 sleep**（ctypes 自旋）；注册并跑通单节点（先模拟器后 PC）。
6. **粗流程 pipeline**：`resource/base/pipeline/farm.json`——进关/开战/识别战斗开始→ExecuteTimeline→结算/重开赛博塑料循环/弹窗（参照 MAA `Stages/base.json`+`UiTheme/Terminal.json`+`Copilot/formation.json` 模式翻译）；补 `tasks/*.json`+`interface.json`。
7. **时间轴模型 + JSON IO + 独立打轴/对轴编辑器窗口**：`timeline/*`——动作列表+时间尺+磁铁游标+动作面板+**可视化地图选点**(`map_picker.py`)+prts-plus `.xlsm` 导入+对轴演练。
8. **主控台 + 托盘 + 热键 + app 编排**：`ui/main_window.py`、`ui/tray.py`、`ui/hotkeys.py`、`main.py`。
9. **资源同步器 + 测试 + 基准 + 打包**：`resources/syncer.py`（地图取自 MAA Arknights-Tile-Pos，仅同步干员头像/mapping）、`tests/`、release workflow（PyInstaller 打包集成应用）。

## 验证

- **规范检查**：`pnpm check`（prettier+schema+`maa-tools check`+lint）+ `uv run ruff`/`pyright` 全绿。
- **单测**：`game_time` 运算、`view` 投影（对比 prts-plus 1-7 已知格子屏幕比例）、`convert_pos`、`calibration` 在合成样本上的聚类。
- **性能基准（关键）**：`post_screencap→tick` 往返延迟，N 秒报 p50/p99，目标 p99 ≪ 一个游戏帧（~33ms）。若超标触发原生平替或改 MAA `ColorMatch`。
- **执行器单节点**：连暂停中的游戏，触发 `ExecuteTimeline`，验证右键拖拽部署落在正确格子、正确 tick（对比录像 tick 与计划 tick）。
- **pipeline 端到端**：真实/模拟器方舟实例跑简单关，farm.json 完成进关→开战→执行→结算→重开闭环。
- **测量层**：ws 客户端连 `ws://localhost:2606`，校验 JSON 符合 `reference/ArknightsCostBarRuler/API.md`；悬浮窗实时帧数与游戏一致。
