"""测量工作线程：持续截图 → TimeSource → 状态（驱动悬浮窗 + WebSocket API）。

在 QThread 中跑采集循环；每帧产出状态 dict（符合 CostBarRuler API.md schema），
通过 ``state_changed`` 信号推给悬浮窗（跨线程自动 QueuedConnection），
并存 ``latest_state`` 供 ApiServer 读取。
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from aao.core.timing.calibration import FullCalibrationData
from aao.core.timing.time_source import TimeSource
from aao.utils.logger import logger

if TYPE_CHECKING:
    from maa.controller import Win32Controller


class MeasurementWorker(QObject):
    """采集循环 worker（moveToThread 到 QThread 运行）。"""

    state_changed = Signal(dict)

    def __init__(
        self,
        controller: Win32Controller,
        calibration: FullCalibrationData,
        profile_name: str = "",
        interval_s: float = 1 / 60,
    ):
        super().__init__()
        self.controller = controller
        self.time_source = TimeSource(calibration)
        self.profile_name = profile_name
        self.interval_s = interval_s
        self._running = False
        self._reset_requested = False
        self._latest: dict = {}
        self._lock = threading.Lock()

    @property
    def latest_state(self) -> dict:
        with self._lock:
            return dict(self._latest)

    def run(self) -> None:
        self._running = True
        logger.info("MeasurementWorker 启动 (profile=%s)", self.profile_name)
        while self._running:
            if self._reset_requested:
                self._reset_requested = False
                self.time_source.reset_timer()
            try:
                img = self.controller.post_screencap().wait().get()
                self.time_source.update(img)
            except Exception:
                logger.exception("采集/更新失败，跳过")
                time.sleep(self.interval_s)
                continue

            state = {
                "isRunning": self.time_source.is_running,
                "currentFrame": self.time_source.current_frame_in_cycle,
                "totalFramesInCycle": self.time_source.total_frames_in_cycle,
                "totalElapsedFrames": self.time_source.total_elapsed_frames,
                "activeProfile": self.profile_name,
            }
            with self._lock:
                self._latest = state
            self.state_changed.emit(state)
            time.sleep(self.interval_s)
        logger.info("MeasurementWorker 停止")

    def request_reset_timer(self) -> None:
        self._reset_requested = True

    def stop(self) -> None:
        self._running = False
