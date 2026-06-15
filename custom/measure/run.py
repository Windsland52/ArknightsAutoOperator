"""运行「费用条尺子」：悬浮窗 + WebSocket API。

用法：
    # PC 客户端（Win32）
    uv run python -m custom.measure.run --profile test_30f_1280x720.json

    # 模拟器（ADB）
    uv run python -m custom.measure.run --profile test_30f_1280x720.json --mode adb

自动连接「明日方舟」窗口或模拟器；关窗退出。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from custom import config  # noqa: E402
from custom.core.timing import calibration  # noqa: E402
from custom.measure.api_server import ApiServer  # noqa: E402
from custom.measure.overlay import OverlayWindow  # noqa: E402
from custom.measure.worker import MeasurementWorker  # noqa: E402
from custom.utils.logger import setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402

logger = logging.getLogger(__name__)

# 模拟器 ROI（CostBarRuler 原值；PC 客户端偏移见 config.py）
_ROI_ADB = {
    "X1_OFFSET_FROM_RIGHT": config.REF_WIDTH - 1740,  # 180 → x1160 @720
    "Y1_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 810,  # 270 → y540 @720
    "Y2_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 817,  # 263 → y544 @720
}


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
    # 模拟器原生 1280×720，切换 ROI 到 CostBarRuler 原值
    for key, val in _ROI_ADB.items():
        setattr(config, key, val)
    logger.info("已切换到模拟器 ROI: %s", _ROI_ADB)
    return ctrl


def main() -> int:
    parser = argparse.ArgumentParser(description="费用条尺子（悬浮窗 + WebSocket API）")
    parser.add_argument("--profile", required=True, help="校准文件名（config/calibration/ 下）")
    parser.add_argument("--mode", choices=["win32", "adb"], default="win32")
    parser.add_argument("--port", type=int, default=2606)
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    data = calibration.load(args.profile)
    logger.info(
        "加载校准 %s：%d 档 profile (%s)", args.profile, len(data.profiles), data.detection_mode
    )

    controller = _connect_adb(Toolkit) if args.mode == "adb" else _connect_win32(Toolkit)
    if controller is None:
        return 2

    app = QApplication(sys.argv)
    overlay = OverlayWindow()
    overlay.show()

    worker = MeasurementWorker(controller, data, profile_name=args.profile)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.state_changed.connect(overlay.on_state)

    if not args.no_api:
        ApiServer(lambda: worker.latest_state, port=args.port).start()

    thread.start()
    rc = app.exec()

    worker.stop()
    thread.quit()
    thread.wait()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
