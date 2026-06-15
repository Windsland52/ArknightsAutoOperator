"""集成应用入口：悬浮窗 + 打轴编辑器 + 全局热键。

用法：
    uv run python -m custom.app --profile test_30f_1280x720.json
    uv run python -m custom.app --profile test_30f_1280x720.json --mode adb

启动后：
- 悬浮窗显示实时帧/计时器
- 编辑器窗口显示动作列表，支持 F8/F9/F10 标记
- WebSocket API 在 ws://localhost:2606
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from custom import config  # noqa: E402
from custom.core.battle.action import ActionType  # noqa: E402
from custom.core.timing import calibration  # noqa: E402
from custom.measure.api_server import ApiServer  # noqa: E402
from custom.measure.overlay import OverlayWindow  # noqa: E402
from custom.measure.worker import MeasurementWorker  # noqa: E402
from custom.timeline.editor_window import EditorWindow  # noqa: E402
from custom.utils.logger import setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402

logger = logging.getLogger(__name__)

# 全局热键 VK codes
_VK_F8 = 0x77
_VK_F9 = 0x78
_VK_F10 = 0x79

# Win32 GetAsyncKeyState
try:
    import ctypes

    _user32 = ctypes.windll.user32

    def _key_pressed(vk: int) -> bool:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)

except (ImportError, OSError):

    def _key_pressed(vk: int) -> bool:
        return False


# 模拟器 ROI（CostBarRuler 原值）
_ROI_ADB = {
    "X1_OFFSET_FROM_RIGHT": config.REF_WIDTH - 1740,
    "Y1_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 810,
    "Y2_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 817,
}

# 热键防抖
_key_states: dict[int, bool] = {_VK_F8: False, _VK_F9: False, _VK_F10: False}


def _connect_win32(Toolkit):
    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )

    wins = Toolkit.find_desktop_windows()
    target = next((w for w in wins if "明日方舟" in (w.window_name or "")), None)
    if target is None:
        logger.error("未找到「明日方舟」窗口")
        return None
    ctrl = Win32Controller(
        target.hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        MaaWin32InputMethodEnum.PostMessage,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(720)
    return ctrl


def _connect_adb(Toolkit):
    from maa.controller import AdbController

    devices = Toolkit.find_adb_devices()
    if not devices:
        logger.error("未找到 ADB 设备")
        return None
    d = devices[0]
    logger.info("ADB 设备: %s @ %s", d.name, d.address)
    ctrl = AdbController(
        adb_path=d.adb_path,
        address=d.address,
        screencap_methods=d.screencap_methods,
        input_methods=d.input_methods,
        config=d.config,
    )
    ctrl.post_connection().wait()
    for key, val in _ROI_ADB.items():
        setattr(config, key, val)
    logger.info("已切换到模拟器 ROI")
    return ctrl


def main() -> int:
    parser = argparse.ArgumentParser(description="ArknightsAutoOperator 集成应用")
    parser.add_argument("--profile", required=True, help="校准文件名")
    parser.add_argument("--mode", choices=["win32", "adb"], default="win32")
    parser.add_argument("--port", type=int, default=2606)
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--no-editor", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    data = calibration.load(args.profile)
    logger.info("校准 %s：%d 档 (%s)", args.profile, len(data.profiles), data.detection_mode)

    controller = _connect_adb(Toolkit) if args.mode == "adb" else _connect_win32(Toolkit)
    if controller is None:
        return 2

    app = QApplication(sys.argv)

    # 悬浮窗
    overlay = OverlayWindow()
    overlay.show()

    # 编辑器（可选）
    editor: EditorWindow | None = None
    if not args.no_editor:
        editor = EditorWindow()
        editor.show()

    # 测量 worker
    worker = MeasurementWorker(controller, data, profile_name=args.profile)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.state_changed.connect(overlay.on_state)
    if editor:
        worker.state_changed.connect(
            lambda state: editor.update_frame(
                state.get("totalElapsedFrames", 0),
                f"cost={state.get('currentFrame')}",
            )
        )

    # WebSocket API
    if not args.no_api:
        ApiServer(lambda: worker.latest_state, port=args.port).start()

    # 全局热键轮询（QTimer，~60Hz）
    def poll_hotkeys():
        for vk, action_type in [
            (_VK_F8, ActionType.DEPLOY),
            (_VK_F9, ActionType.SKILL),
            (_VK_F10, ActionType.RETREAT),
        ]:
            pressed = _key_pressed(vk)
            if pressed and not _key_states[vk]:
                # 上升沿：按下瞬间
                if editor:
                    editor.mark_action(action_type)
            _key_states[vk] = pressed

    hotkey_timer = QTimer()
    hotkey_timer.timeout.connect(poll_hotkeys)
    hotkey_timer.start(16)  # ~60Hz

    thread.start()
    logger.info("应用启动。F8=部署 F9=技能 F10=撤退。关闭窗口退出。")
    rc = app.exec()

    hotkey_timer.stop()
    worker.stop()
    thread.quit()
    thread.wait()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
