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

# 退避参数：前 _FAST_RETRY_THRESHOLD 次快速重试（检测瞬断），
# 之后按 _BACKOFF_S 间隔重试（游戏可能已退出/最小化，无需高频截图）。
_FAST_RETRY_THRESHOLD = 3
_BACKOFF_S = 5.0  # 连续失败退避间隔（用户要求 5-10s）
_PERIODIC_LOG_EVERY = 60  # 持续退避中每 N 次失败输出一条进度日志（≈5min 一条）


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
        self._consecutive_errors = 0
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
                # 拆开链式调用: wait() 只阻塞不检查状态，需显式判 job.failed。
                # 否则 C++ 层 screencap failed 时 cached_image 仍返回空/旧图、不抛异常，
                # 导致 except 永远不触发、退避不生效（见 maafw.log 连续刷屏问题）。
                job = self.controller.post_screencap()
                job.wait()
                if job.failed:
                    raise RuntimeError("screencap job failed")
                img = job.get()
                # 防御: MaaFW 有时返回 (0,0,3) 空数组而非抛异常
                if img is None or img.size == 0:  # pyright: ignore[reportUnnecessaryComparison]
                    raise RuntimeError("screencap returned empty image")
                self.time_source.update(img)
                self._consecutive_errors = 0
            except Exception:
                self._consecutive_errors += 1
                n = self._consecutive_errors
                if n <= _FAST_RETRY_THRESHOLD:
                    logger.warning("截图失败 %d 次", n)
                elif n == _FAST_RETRY_THRESHOLD + 1:
                    logger.error(
                        "截图连续失败 %d 次, 游戏可能已退出或最小化, 降低重试频率至 %.0fs",
                        n,
                        _BACKOFF_S,
                    )
                elif n % _PERIODIC_LOG_EVERY == 0:
                    logger.warning(
                        "截图仍连续失败 (%d 次), 每 %.0fs 重试一次",
                        n,
                        _BACKOFF_S,
                    )
                # 退避: 前 N 次快重试, 之后 _BACKOFF_S 一次
                backoff = self.interval_s if n <= _FAST_RETRY_THRESHOLD else _BACKOFF_S
                time.sleep(backoff)
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
