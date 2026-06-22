"""集成应用入口：QMainWindow 主控台（侧栏导航）+ 悬浮窗 + 全局热键。

单进程 Option B：主控台持有 controller / MeasurementWorker，凹图页与打轴页
共享同一连接；凹图与打轴互斥（_busy 标志统一管）。

用法：
    uv run python -m aao.app --profile test_30f_1280x720.json

启动后：
- 主控台窗口：左侧侧栏（凹图/打轴/校准/设置），右侧内容区
- 悬浮窗：实时帧/计时器（置顶可拖拽）
- 全局热键 F8/F9/F10：打轴标记（凹图运行时禁用）
- WebSocket API：ws://localhost:2606
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maa.controller import Win32Controller
    from maa.tasker import Tasker
    from PySide6.QtGui import QCloseEvent

    from aao.core.timing.calibration import FullCalibrationData

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import Qt, QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QWidget,
)

from aao import config  # noqa: E402
from aao.core.battle.action import ActionType  # noqa: E402
from aao.core.timing import calibration  # noqa: E402
from aao.measure.api_server import ApiServer  # noqa: E402
from aao.measure.overlay import OverlayWindow  # noqa: E402
from aao.measure.worker import MeasurementWorker  # noqa: E402
from aao.timeline.editor_window import EditorWindow  # noqa: E402
from aao.ui.about_page import AboutPage  # noqa: E402
from aao.ui.background import BackgroundContainer  # noqa: E402
from aao.ui.calibration_page import CalibrationPage  # noqa: E402
from aao.ui.farm_page import FarmPage  # noqa: E402
from aao.ui.log_handler import QtLogHandler  # noqa: E402
from aao.ui.runtime import build_tasker, connect_window  # noqa: E402
from aao.ui.scrollbar_style import apply_themed_scrollbar  # noqa: E402
from aao.ui.settings_page import SettingsPage  # noqa: E402
from aao.utils.logger import logger, setup_logging  # noqa: E402
from aao.utils.runtime_paths import configure_paths  # noqa: E402

# 全局热键 VK codes
_VK_F8 = 0x77
_VK_F9 = 0x78
_VK_F10 = 0x79

# Win32 GetAsyncKeyState
try:
    import ctypes

    _user32 = ctypes.windll.user32  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]

    def _key_pressed(vk: int) -> bool:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

except (ImportError, OSError):

    def _key_pressed(vk: int) -> bool:
        return False


# 侧栏页索引（与 _build_ui addWidget 顺序一致）
_PAGE_FARM = 0
_PAGE_EDITOR = 1
_PAGE_CALIB = 2
_PAGE_SETTINGS = 3


class MainWindow(QMainWindow):
    """主控台：侧栏导航 + QStackedWidget 内容区。"""

    def __init__(
        self,
        controller: Win32Controller | None,
        tasker: Tasker | None,
        calibration_data: FullCalibrationData | None,
        profile_name: str,
        no_api: bool,
        port: int,
    ) -> None:
        super().__init__()
        self.setWindowTitle("ArknightsAutoOperator")
        self.resize(860, 560)
        # 窗口图标（左上角 + Windows 任务栏）
        from PySide6.QtGui import QIcon

        from aao.utils.runtime_paths import project_root

        icon_path = project_root() / "logo.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._controller = controller
        self._tasker = tasker
        self._calibration_data = calibration_data
        self._profile_name = profile_name
        self._busy = False  # 互斥：凹图/打轴运行时为 True
        self._editor = None  # 打轴编辑器（占位页后续接入）

        self._build_ui()

        # 悬浮窗 + 测量 worker（供悬浮窗/打轴显示实时帧）——需 controller + 校准
        self.overlay: OverlayWindow | None = None
        self.worker: MeasurementWorker | None = None
        self.worker_thread: QThread | None = None
        if controller is not None and calibration_data is not None:
            self.overlay = OverlayWindow()
            self.overlay.show()
            self.worker = MeasurementWorker(controller, calibration_data, profile_name=profile_name)
            self.worker_thread = QThread()
            self.worker.moveToThread(self.worker_thread)
            self.worker_thread.started.connect(self.worker.run)
            self.worker.state_changed.connect(self.overlay.on_state)
            self.worker.state_changed.connect(self._on_measure_state)
            self.worker_thread.start()

        # WebSocket API（需 worker 提供 state）
        if not no_api and self.worker is not None:
            worker_ref = self.worker
            ApiServer(lambda: worker_ref.latest_state, port=port).start()

        # 全局热键轮询（打轴用，凹图运行时暂停）
        self._key_states: dict[int, bool] = {_VK_F8: False, _VK_F9: False, _VK_F10: False}
        self.hotkey_timer = QTimer()
        self.hotkey_timer.timeout.connect(self._poll_hotkeys)
        self.hotkey_timer.start(16)  # ~60Hz

        # 新手引导：未完成过引导（settings 无 onboarded）则默认停在设置页
        if self._needs_onboarding():
            self.nav.setCurrentRow(_PAGE_SETTINGS)
            logger.info("首次启动，引导到设置页")

        # 系统托盘：关闭窗口=隐藏到托盘；托盘「退出」=真退出
        from aao.ui.tray import TrayController
        from aao.ui.window_snap import register_snap_window

        register_snap_window(self, "main")
        self._force_quit = False
        self.tray = TrayController(self)
        self.tray.show_requested.connect(self._restore_from_tray)
        self.tray.reset_layout_requested.connect(self._reset_window_layout)
        self.tray.quit_requested.connect(self._quit_from_tray)
        self.tray.show()

        # 背景图（读 settings 启动即应用；后续切换由设置页 background_changed 驱动）
        from aao.ui.settings_page import load_settings

        bg = load_settings()
        self._apply_background(
            bg.get("background_image", ""), bg.get("background_opacity", 25) / 100.0
        )

    # --- 新手引导 ---

    @staticmethod
    def _needs_onboarding() -> bool:
        """是否需要新手引导：未标记 onboarded 时引导一次。"""
        from aao.ui.settings_page import load_settings

        return not load_settings().get("onboarded", False)

    def _hide_overlay(self) -> None:
        """临时隐藏悬浮窗（弹 QMessageBox 前调，避免遮挡）。"""
        if self.overlay is not None:
            self.overlay.hide()

    def _show_overlay(self) -> None:
        """恢复悬浮窗显示。"""
        if self.overlay is not None:
            self.overlay.show()

    def _on_window_configured(self) -> None:
        """设置页"设为默认窗口"后：按新窗口重连 controller/tasker 并注入各页；
        引导中则提示去校准并跳页。"""
        onboarding = self._needs_onboarding()
        # 引导中不创建悬浮窗（避免遮挡后续 QMessageBox）
        ok = self._reconnect(skip_overlay=onboarding)
        if not ok:
            self._hide_overlay()
            QMessageBox.warning(
                self,
                "连接失败",
                "未找到指定窗口，请确认游戏已启动且窗口选择正确。",
            )
            self._show_overlay()
            return
        if not onboarding:
            return
        self._hide_overlay()
        QMessageBox.information(
            self,
            "下一步：校准",
            "窗口已设置。请到「校准」页完成费用条校准后再凹图。",
        )
        self._show_overlay()
        self.nav.setCurrentRow(_PAGE_CALIB)

    def _reconnect(self, skip_overlay: bool = False) -> bool:
        """按 settings.json 的 window_name/class 重连 controller + 重建 tasker，
        注入凹图页/校准页，并（若有校准）启 measure worker。返回是否连上。

        skip_overlay=True 时不创建/显示悬浮窗（新手引导时避免遮挡对话框）。
        同步执行（连窗口+post_bundle 约 1-2s，短暂卡 UI 可接受）。
        """
        from maa.toolkit import Toolkit

        from aao.ui.settings_page import load_settings

        s = load_settings()
        controller = connect_window(
            Toolkit,
            prefer_name=s.get("window_name"),
            prefer_class=s.get("window_class"),
        )
        if controller is None:
            return False
        tasker = build_tasker(controller)
        if tasker is None:
            return False

        # 停旧的 measure worker（若有）
        if self.worker is not None:
            self.worker.stop()
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker = None
            self.worker_thread = None

        self._controller = controller
        self._tasker = tasker
        self.farm_page.set_runtime(controller, tasker)
        self.calib_page.set_runtime(controller)

        # 有校准则启 measure worker（供悬浮窗/打轴实时帧）
        if self._calibration_data is not None and not skip_overlay:
            if self.overlay is None:  # 首次启动 controller 为 None 时未建，此处补建
                self.overlay = OverlayWindow()
                self.overlay.show()
            self.worker = MeasurementWorker(
                controller, self._calibration_data, profile_name=self._profile_name
            )
            self.worker_thread = QThread()
            self.worker.moveToThread(self.worker_thread)
            self.worker_thread.started.connect(self.worker.run)
            self.worker.state_changed.connect(self.overlay.on_state)
            self.worker.state_changed.connect(self._on_measure_state)
            self.worker_thread.start()

        self._set_connection_status("已连接", "#4caf50")
        self.status_profile.setText(f"profile: {self._profile_name}")
        return True

    def _on_profile_saved(self, filename: str) -> None:
        """校准页保存后：重载校准数据 + 重连（measure worker 用新校准），引导中则提示跳凹图。"""
        try:
            self._calibration_data = calibration.load(filename)
            self._profile_name = filename
            self.editor_page.set_profile(filename)
        except (OSError, ValueError):
            logger.exception("校准 %s 加载失败", filename)
        onboarding = self._needs_onboarding()
        if self._controller is not None:
            self._reconnect(skip_overlay=onboarding)

        if not onboarding:
            return
        from aao.ui.settings_page import load_settings, save_settings

        self._hide_overlay()
        QMessageBox.information(
            self,
            "可以凹图了",
            "校准完成。可到「凹图」页开始自动凹图。",
        )
        s = load_settings()
        s["onboarded"] = True
        save_settings(s)
        self.nav.setCurrentRow(_PAGE_FARM)

    def _scroll_page(self, page: QWidget) -> QScrollArea:
        """把页面包进滚动区：窗口保持小巧，页面内容超出时滚动。"""
        area = QScrollArea()
        area.setWidget(page)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.viewport().setObjectName("bg_layer")
        area.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        apply_themed_scrollbar(area)
        return area

    def _build_ui(self) -> None:
        central = BackgroundContainer()  # 带 cover 背景图的 central（paintEvent 画图）
        self.bg_container = central
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧侧栏
        self.nav = QListWidget()
        apply_themed_scrollbar(self.nav)
        self.nav.setFixedWidth(104)
        for label in ["🎯 凹图", "✏️ 打轴", "📏 校准", "⚙️ 设置", "ℹ️ 关于"]:
            QListWidgetItem(label, self.nav)
        self.nav.setCurrentRow(0)
        root.addWidget(self.nav)

        # 右侧内容区
        self.stack = QStackedWidget()
        self.farm_page = FarmPage()
        if self._controller is not None and self._tasker is not None:
            self.farm_page.set_runtime(self._controller, self._tasker)
        self.farm_page.busy_changed.connect(self._set_busy)
        self.farm_page.reset_timer_requested.connect(self._reset_measure_timer)

        self.editor_page = EditorWindow()  # 打轴编辑器（已接入 canvas + 地图选点）
        self.editor_page.set_profile(self._profile_name)
        self._editor = self.editor_page  # 供热键 mark_action
        self.calib_page = CalibrationPage()
        self.settings_page = SettingsPage()
        self.about_page = AboutPage()
        # 注入 controller（校准页需要截图；凹图页需 controller+tasker，已在上面注入）
        if self._controller is not None:
            self.calib_page.set_runtime(self._controller)

        self.page_widgets = [
            self._scroll_page(self.farm_page),
            self.editor_page,  # 打轴页是工作区：一屏内布局，不加外层滚动条
            self._scroll_page(self.calib_page),
            self._scroll_page(self.settings_page),
            self._scroll_page(self.about_page),
        ]
        for page in self.page_widgets:
            self.stack.addWidget(page)
        root.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        # 背景图从侧栏/内容栈/各页根透出。用 objectName 限定透明：只让这些容器本身透明，
        # 不级联到表格表头/下拉项等子控件——否则 "background: transparent" 会破坏子控件
        # 渲染（表头变黑、下拉项 focus 时字变白看不清）。控件本身保持 palette 不透明。
        # 注意顺序：先 setObjectName，再 setStyleSheet；否则启动时 selector 可能没匹配，直到切主题
        # repolish 后才透明。
        for w in (
            self.nav,
            self.stack,
            *self.page_widgets,
            self.farm_page,
            self.editor_page,
            self.calib_page,
            self.settings_page,
            self.about_page,
        ):
            w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            w.setObjectName("bg_layer")
        self.setStyleSheet("#bg_layer { background: transparent; }")
        for w in self.findChildren(QWidget):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()
        self.settings_page.background_changed.connect(self._apply_background)

        # 新手引导：设置页连好窗口 → 引导中则跳校准页；校准页保存 → 跳凹图页 + 标记完成
        # 普通 settings_changed（改端口/profile）不触发引导
        self.settings_page.window_configured.connect(self._on_window_configured)
        self.calib_page.profile_saved.connect(self._on_profile_saved)

        # 状态栏
        connected = self._controller is not None
        self.status_conn = QLabel()
        if connected:
            self._set_connection_status("已连接", "#4caf50")
        else:
            self._set_connection_status("未连接", "#f44336")
        self.status_profile = QLabel(f"profile: {self._profile_name}")
        self.status_attempt = QLabel("第 0 次")
        self.status_timer = QLabel("--:--:--")
        sb = self.statusBar()
        sb.addWidget(self.status_conn)
        sb.addWidget(self.status_profile)
        sb.addWidget(self.status_attempt)
        sb.addPermanentWidget(self.status_timer)

    def _set_connection_status(self, text: str, color: str) -> None:
        """设置状态栏连接状态，圆点按状态着色。"""
        self.status_conn.setText(f"● {text}")
        self.status_conn.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _apply_background(self, path: str, opacity: float) -> None:
        """应用背景图 + 透明度到 central（设置页 background_changed / 启动时调用）。"""
        self.bg_container.set_background(path)
        self.bg_container.set_opacity(opacity)

    # --- 测量状态更新 ---

    def _reset_measure_timer(self, node_name: str) -> None:
        if self.worker is not None:
            logger.debug("计时器重置（pipeline 节点: %s）", node_name)
            self.worker.request_reset_timer()

    def _on_measure_state(self, state: dict) -> None:
        from aao.core.timing.time_source import format_timer

        total = state.get("totalElapsedFrames", 0)
        self.status_timer.setText(format_timer(total))
        # 驱动打轴编辑器实时帧 + canvas 游标
        if self._editor is not None and state.get("isRunning"):
            self._editor.update_frame(total, f"cost={state.get('currentFrame')}")

    # --- 互斥 ---

    def _set_busy(self, busy: bool) -> None:
        """凹图开始/结束 → 切换互斥。"""
        self._busy = busy
        if busy:
            self.hotkey_timer.stop()
            self.editor_page.setEnabled(False)
            self._set_connection_status("凹图运行中", "#ff9800")
        else:
            self.hotkey_timer.start(16)
            self.editor_page.setEnabled(True)
            self._set_connection_status("已连接", "#4caf50")

    def _poll_hotkeys(self) -> None:
        if self._busy or self._editor is None:
            return
        for vk, action_type in [
            (_VK_F8, ActionType.DEPLOY),
            (_VK_F9, ActionType.SKILL),
            (_VK_F10, ActionType.RETREAT),
        ]:
            pressed = _key_pressed(vk)
            if pressed and not self._key_states[vk]:
                self._editor.mark_action(action_type)
            self._key_states[vk] = pressed

    def set_log_handler(self, handler: QtLogHandler) -> None:
        self.farm_page.set_log_handler(handler)

    def _get_close_action(self) -> str:
        """点 X 时的行为：读 settings，无偏好则弹窗询问。

        返回 "minimize" 或 "exit"。
        """
        from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QPushButton, QVBoxLayout

        from aao.ui.settings_page import load_settings, save_settings

        s = load_settings()
        saved = s.get("close_action", "")  # "minimize" / "exit" / "" (每次问)
        if saved in ("minimize", "exit"):
            return saved

        # 首次/未设偏好：弹自定义对话框
        self._hide_overlay()
        dlg = QDialog(self)
        dlg.setWindowTitle("关闭窗口")
        dl = QVBoxLayout(dlg)
        btn_row = QHBoxLayout()
        btn_exit = QPushButton("退出程序")
        btn_minimize = QPushButton("最小化到托盘")
        btn_row.addWidget(btn_exit)
        btn_row.addWidget(btn_minimize)
        dl.addLayout(btn_row)
        chk = QCheckBox("不再询问（可在设置中修改）")
        dl.addWidget(chk)

        choice = "exit"

        def _on_exit():
            nonlocal choice
            choice = "exit"
            dlg.accept()

        def _on_minimize():
            nonlocal choice
            choice = "minimize"
            dlg.accept()

        btn_exit.clicked.connect(_on_exit)
        btn_minimize.clicked.connect(_on_minimize)
        dlg.exec()
        self._show_overlay()

        if chk.isChecked():
            s["close_action"] = choice
            save_settings(s)
        return choice

    def _restore_from_tray(self) -> None:
        """从托盘还原窗口。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _reset_window_layout(self) -> None:
        """托盘菜单：重置主窗口位置/大小，并清除悬浮窗持久化布局。"""
        from aao.ui import floating_state

        floating_state.clear_all()
        geo = self.screen().availableGeometry()
        self.resize(860, 560)
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self._restore_from_tray()

        margin = 20
        if self.overlay is not None:
            self.overlay.reset_layout(geo.right() - 170 - margin, geo.top() + margin)
        log = getattr(self.farm_page, "_floating_log", None)
        if log is not None:
            x = geo.right() - 360 - margin
            y = geo.top() + margin + (self.overlay.height() + 12 if self.overlay is not None else 0)
            log.reset_layout(x, y)
        self.tray.show_message("ArknightsAutoOperator", "已重置主窗口和悬浮窗布局。")

    def _quit_from_tray(self) -> None:
        """托盘「退出」：停所有 worker + 退出应用。"""
        self._force_quit = True
        self.hotkey_timer.stop()
        self.farm_page.stop_and_wait()
        if self.worker is not None:
            self.worker.stop()
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        if not self._force_quit:
            # 点 X：按用户偏好退出或最小化
            action = self._get_close_action()
            if action == "minimize":
                event.ignore()
                self.hide()
                self.tray.show_message("ArknightsAutoOperator", "已在后台运行，双击托盘图标恢复。")
            else:
                # 走真退出路径
                self._force_quit = True
            return
        # 真退出（_quit_from_tray 已做清理，或窗口在可见时被强制关闭）
        self.hotkey_timer.stop()
        self.farm_page.stop_and_wait()
        if self.worker is not None:
            self.worker.stop()
        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()
        super().closeEvent(event)


def _ensure_admin() -> None:
    """Windows 下若非管理员，用 UAC 提权重启自己（凹图 PostMessage 需 UIPI 权限）。

    开发与打包均生效。用户拒绝 UAC 或非 Windows 则不重启（程序以普通权限继续）。
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        if ctypes.windll.shell32.IsUserAnAdmin():  # pyright: ignore[reportAttributeAccessIssue]
            return
    except (AttributeError, OSError):
        return

    frozen = getattr(sys, "frozen", False)
    if frozen:
        # 打包 exe：重启自身，只带原参数（不含 argv[0]=自身路径，避免重复）
        args = sys.argv[1:]
        work_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 开发模式：`uv run python -m aao.app` 时 sys.argv 不包含 `-m aao.app`，
        # 提权后必须显式补上模块入口，否则只会打开 python 交互 REPL。
        args = ["-m", "aao.app", *sys.argv[1:]]
        work_dir = os.getcwd()
    params = " ".join(f'"{a}"' for a in args)
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(  # pyright: ignore[reportAttributeAccessIssue]
            None, "runas", sys.executable, params, work_dir, 1
        )
        # ShellExecuteW 返回 >32 表示成功
        if rc > 32:
            raise SystemExit(0)
    except (AttributeError, OSError):
        pass


def main() -> int:
    _ensure_admin()

    # Windows 任务栏分组：设 AppUserModelID，否则用 Python/PyInstaller 默认图标
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Windsland52.ArknightsAutoOperator"
            )  # pyright: ignore[reportAttributeAccessIssue]
        except (AttributeError, OSError):
            pass
    # 先 configure_paths 让 settings_page 能读到 config/settings.json
    configure_paths()
    # 确保 AFA 在运行（凹图热键依赖；没在则拉起自带 AFA.exe）
    from aao.core.afa import ensure_afa

    ensure_afa()
    from aao.ui.settings_page import load_settings

    saved = load_settings()

    parser = argparse.ArgumentParser(description="ArknightsAutoOperator 集成应用")
    parser.add_argument(
        "--profile",
        default=saved.get("profile"),
        help="校准文件名（可选；默认读 config/settings.json，再缺省用内置默认）",
    )
    parser.add_argument("--port", type=int, default=int(saved.get("port", 2606)))
    parser.add_argument("--no-api", action="store_true", default=not saved.get("api", True))
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # profile 优先级：命令行 > settings.json > 内置默认（config.DEFAULT_CALIBRATION）
    profile = args.profile or config.DEFAULT_CALIBRATION

    setup_logging("DEBUG" if args.debug else "INFO")
    paths = configure_paths()

    # 从 settings.json 覆盖 config 高级参数（步进/调速/等待时间）
    from aao.ui.settings_page import load_settings

    s = load_settings()
    if "bullet_threshold" in s:
        config.BULLET_THRESHOLD = s["bullet_threshold"]
    if "speed_up_threshold" in s:
        config.SPEED_UP_THRESHOLD = s["speed_up_threshold"]
    if "general_wait_ms" in s:
        config.GENERAL_WAIT_MS = s["general_wait_ms"]
    if "mouse_wait_ms" in s:
        config.MOUSE_WAIT_MS = s["mouse_wait_ms"]
    if "minimum_wait_ms" in s:
        config.MINIMUM_WAIT_MS = s["minimum_wait_ms"]
    if "pause_wait_ms" in s:
        config.PAUSE_WAIT_MS = s["pause_wait_ms"]
    if "step_wait_ms" in s:
        config.STEP_WAIT_MS = s["step_wait_ms"]
    if "accept_early_frames" in s:
        config.ACCEPT_EARLY_FRAMES = s["accept_early_frames"]
    if "accept_late_frames" in s:
        config.ACCEPT_LATE_FRAMES = s["accept_late_frames"]
    if "big_step_threshold" in s:
        config.BIG_STEP_THRESHOLD = s["big_step_threshold"]

    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    controller = connect_window(
        Toolkit,
        prefer_name=saved.get("window_name"),
        prefer_class=saved.get("window_class"),
    )
    tasker = build_tasker(controller) if controller is not None else None
    if controller is None:
        logger.warning("未连接游戏窗口，凹图功能不可用（UI 仍可启动）")

    app = QApplication(sys.argv)

    # 主题：读 settings.theme（缺省跟随系统），在窗口创建前应用
    from aao.ui import theme

    theme.apply_theme(saved.get("theme", theme.AUTO))

    # 校准加载：无校准则 data=None，MainWindow 会跳过 measure worker/悬浮窗，
    # 引导流程（首次→设置页→连接→校准）自然覆盖，不需要 pre-window 弹窗。
    calib_dir = calibration.calibration_dir()
    has_calib = any(calib_dir.glob("*.json"))
    data = None
    if has_calib:
        try:
            data = calibration.load(profile)
            logger.info("校准 %s：%d 档 (%s)", profile, len(data.profiles), data.detection_mode)
        except (OSError, ValueError):
            logger.exception("校准 %s 加载失败，计时功能不可用", profile)

    window = MainWindow(controller, tasker, data, profile, args.no_api, args.port)

    # 日志接 UI（loguru sink → QtLogHandler.emit → Signal → 日志面板）
    handler = QtLogHandler()
    from aao.utils.logger import add_qt_sink

    add_qt_sink(handler.emit)
    window.set_log_handler(handler)

    window.show()
    logger.info("应用启动。侧栏切换功能；凹图页 ▶ 开始。")
    rc = app.exec()
    return rc


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # 打包后提权重启的子进程崩溃看不到控制台，写 crash.log 到 exe 同级便于定位
        import datetime
        import traceback

        crash_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "crash.log")
        with open(crash_path, "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.datetime.now()} ====\n")
            traceback.print_exc(file=f)
        raise
