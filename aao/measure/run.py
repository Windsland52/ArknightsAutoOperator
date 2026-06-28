"""运行「费用条尺子」：悬浮窗 + WebSocket API。

用法：
    # PC 客户端（Win32）
    uv run python -m aao.measure.run --profile test_30f_1280x720.json

自动连接「明日方舟」窗口；关窗退出。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aao.core.timing import calibration  # noqa: E402
from aao.measure.api_server import ApiServer  # noqa: E402
from aao.measure.overlay import OverlayWindow  # noqa: E402
from aao.measure.worker import MeasurementWorker  # noqa: E402
from aao.utils.logger import logger, setup_logging  # noqa: E402
from aao.utils.runtime_paths import configure_paths  # noqa: E402


def _connect_win32(Toolkit: Any):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="费用条尺子（悬浮窗 + WebSocket API）")
    parser.add_argument("--profile", required=True, help="校准文件名（config/calibration/ 下）")
    parser.add_argument("--port", type=int, default=2606)
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging("DEBUG" if args.debug else "INFO")
    paths = configure_paths()

    from maa.toolkit import Toolkit

    # init_option 传 root：maafw 自建 debug/（maafw.log）+ config/，与 debug/aao/ 平级。
    Toolkit.init_option(str(paths["root"]))

    data = calibration.load(args.profile)
    logger.info(
        "加载校准 %s：%d 档 profile (%s)", args.profile, len(data.profiles), data.detection_mode
    )

    controller = _connect_win32(Toolkit)
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
