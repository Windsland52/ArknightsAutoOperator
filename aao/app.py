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

from PySide6.QtCore import QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
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
from aao.ui.calibration_page import CalibrationPage  # noqa: E402
from aao.ui.farm_page import FarmPage  # noqa: E402
from aao.ui.log_handler import QtLogHandler  # noqa: E402
from aao.ui.runtime import build_tasker, connect_window  # noqa: E402
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
        self.resize(960, 620)

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

        self._force_quit = False
        self.tray = TrayController(self)
        self.tray.show_requested.connect(self._restore_from_tray)
        self.tray.quit_requested.connect(self._quit_from_tray)
        self.tray.show()

    # --- 新手引导 ---

    @staticmethod
    def _needs_onboarding() -> bool:
        """是否需要新手引导：未标记 onboarded 时引导一次。"""
        from aao.ui.settings_page import load_settings

        return not load_settings().get("onboarded", False)

    def _on_window_configured(self) -> None:
        """设置页"设为默认窗口"后：按新窗口重连 controller/tasker 并注入各页；
        引导中则提示去校准并跳页。"""
        ok = self._reconnect()
        if not ok:
            QMessageBox.warning(
                self,
                "连接失败",
                "未找到指定窗口，请确认游戏已启动且窗口选择正确。",
            )
            return
        if not self._needs_onboarding():
            return
        QMessageBox.information(
            self,
            "下一步：校准",
            "窗口已设置。请到「校准」页完成费用条校准后再凹图。",
        )
        self.nav.setCurrentRow(_PAGE_CALIB)

    def _reconnect(self) -> bool:
        """按 settings.json 的 window_name/class 重连 controller + 重建 tasker，
        注入凹图页/校准页，并（若有校准）启 measure worker。返回是否连上。

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
        if self._calibration_data is not None:
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

        self.status_conn.setText("● 已连接")
        self.status_profile.setText(f"profile: {self._profile_name}")
        return True

    def _on_profile_saved(self, filename: str) -> None:
        """校准页保存后：重载校准数据 + 重连（measure worker 用新校准），引导中则提示跳凹图。"""
        try:
            self._calibration_data = calibration.load(filename)
            self._profile_name = filename
        except (OSError, ValueError):
            logger.exception("校准 %s 加载失败", filename)
        if self._controller is not None:
            self._reconnect()  # 用新校准重启 measure worker

        if not self._needs_onboarding():
            return
        from aao.ui.settings_page import load_settings, save_settings

        QMessageBox.information(
            self,
            "可以凹图了",
            "校准完成。可到「凹图」页开始自动凹图。",
        )
        s = load_settings()
        s["onboarded"] = True
        save_settings(s)
        self.nav.setCurrentRow(_PAGE_FARM)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 左侧侧栏
        self.nav = QListWidget()
        self.nav.setFixedWidth(120)
        for label in ["🎯 凹图", "✏️ 打轴", "📏 校准", "⚙️ 设置"]:
            QListWidgetItem(label, self.nav)
        self.nav.setCurrentRow(0)
        root.addWidget(self.nav)

        # 右侧内容区
        self.stack = QStackedWidget()
        self.farm_page = FarmPage()
        if self._controller is not None and self._tasker is not None:
            self.farm_page.set_runtime(self._controller, self._tasker)
        self.farm_page.busy_changed.connect(self._set_busy)

        self.editor_page = EditorWindow()  # 打轴编辑器（已接入 canvas + 地图选点）
        self._editor = self.editor_page  # 供热键 mark_action
        self.calib_page = CalibrationPage()
        self.settings_page = SettingsPage()
        # 注入 controller（校准页需要截图；凹图页需 controller+tasker，已在上面注入）
        if self._controller is not None:
            self.calib_page.set_runtime(self._controller)

        self.stack.addWidget(self.farm_page)
        self.stack.addWidget(self.editor_page)
        self.stack.addWidget(self.calib_page)
        self.stack.addWidget(self.settings_page)
        root.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        # 新手引导：设置页连好窗口 → 引导中则跳校准页；校准页保存 → 跳凹图页 + 标记完成
        # 普通 settings_changed（改端口/profile）不触发引导
        self.settings_page.window_configured.connect(self._on_window_configured)
        self.calib_page.profile_saved.connect(self._on_profile_saved)

        # 状态栏
        connected = self._controller is not None
        self.status_conn = QLabel("● 已连接" if connected else "○ 未连接")
        self.status_profile = QLabel(f"profile: {self._profile_name}")
        self.status_attempt = QLabel("第 0 次")
        self.status_timer = QLabel("--:--:--")
        sb = self.statusBar()
        sb.addWidget(self.status_conn)
        sb.addWidget(self.status_profile)
        sb.addWidget(self.status_attempt)
        sb.addPermanentWidget(self.status_timer)

    # --- 测量状态更新 ---

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
            self.status_conn.setText("● 凹图运行中")
        else:
            self.hotkey_timer.start(16)
            self.editor_page.setEnabled(True)
            self.status_conn.setText("● 已连接")

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

    def _restore_from_tray(self) -> None:
        """从托盘还原窗口。"""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        """托盘「退出」：强制真退出。"""
        self._force_quit = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        if not self._force_quit:
            # 普通关闭=隐藏到托盘（不退出，凹图可继续）
            event.ignore()
            self.hide()
            self.tray.show_message("ArknightsAutoOperator", "已在后台运行，双击托盘图标恢复。")
            return
        # 真退出：停所有 worker
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

    # 重新以管理员身份启动自己，只带原参数（不含 argv[0]=自身路径，避免重复）
    args = sys.argv[1:]
    params = " ".join(f'"{a}"' for a in args)
    # 工作目录用 exe 所在目录（否则 UAC 提权后默认 system32，资源找不到）
    work_dir = os.path.dirname(os.path.abspath(sys.executable))
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

    # 校准加载：目录无任何 .json → 弹窗提示先校准，UI 仍启动（measure worker 跳过）
    calib_dir = calibration.calibration_dir()
    has_calib = any(calib_dir.glob("*.json"))
    data = None
    if has_calib:
        try:
            data = calibration.load(profile)
            logger.info("校准 %s：%d 档 (%s)", profile, len(data.profiles), data.detection_mode)
        except (OSError, ValueError):
            logger.exception("校准 %s 加载失败，计时功能不可用", profile)
            QMessageBox.warning(
                None,
                "校准加载失败",
                f"校准文件 {profile} 加载失败，计时/凹图功能不可用。\n请到「校准」页重新校准。",
            )
    else:
        QMessageBox.information(
            None,
            "未校准",
            "尚未进行费用条校准，计时与凹图功能不可用。\n请到「校准」页完成校准后再使用。",
        )

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
