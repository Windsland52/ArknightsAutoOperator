"""运行「费用条尺子」：悬浮窗 + WebSocket API。

用法：
    uv run python -m custom.measure.run --profile <校准文件名>
    # 例：--profile test_30f_1280x720.json （config/calibration/ 下）

自动连接「明日方舟」窗口（1280x720）；ESC 或关窗退出。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# 让 `from custom.*` 在脚本模式下也能解析。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from custom.core.timing import calibration  # noqa: E402
from custom.measure.api_server import ApiServer  # noqa: E402
from custom.measure.overlay import OverlayWindow  # noqa: E402
from custom.measure.worker import MeasurementWorker  # noqa: E402
from custom.utils.logger import setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="费用条尺子（悬浮窗 + WebSocket API）")
    parser.add_argument("--profile", required=True, help="校准文件名（config/calibration/ 下）")
    parser.add_argument("--port", type=int, default=2606, help="WebSocket 端口")
    parser.add_argument("--no-api", action="store_true", help="不启动 WebSocket API")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )
    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    data = calibration.load(args.profile)
    logging.getLogger(__name__).info(
        "加载校准 %s：%d 档 profile (%s)",
        args.profile,
        len(data.profiles),
        data.detection_mode,
    )

    wins = Toolkit.find_desktop_windows()
    target = next((w for w in wins if "明日方舟" in (w.window_name or "")), None)
    if target is None:
        logging.getLogger(__name__).error("未找到「明日方舟」窗口")
        return 2
    controller = Win32Controller(
        target.hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessage,
        MaaWin32InputMethodEnum.PostMessage,
    )
    controller.post_connection().wait()
    controller.set_screenshot_target_short_side(720)

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
