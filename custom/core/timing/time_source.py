"""统一时间源：消费 tick + 校准数据 → 周期计数 + 全局计时器。

移植自 reference/ArknightsCostBarRuler-master/ruler/main.py 的 analysis_worker 状态机：
- 喂帧 ``update(frame)`` → 当前逻辑帧（周期内）。
- 周期完成检测（prev > 0.75*total 且 curr < 0.25*total）→ cycle_counter++、累计 cycle_base。
- 多档 profile 按 cycle 轮换（``cycle_counter % num_profiles``，处理交替回费）。
- 全局计时器 ``total_elapsed = timer_offset + cycle_base + logical_frame``。
- 1.5s 无检测 → 重置。
- 显示模式 0..n-1 / 0..n / 1..n。

悬浮窗与执行器共享同一个 TimeSource 实例。
"""

from __future__ import annotations

import logging
import time

import numpy as np

from custom import config
from custom.core.timing import tick
from custom.core.timing.calibration import FullCalibrationData

logger = logging.getLogger(__name__)

RESET_TIMEOUT_S = 1.5
_WRAP_MARGIN = 3  # 帧下降超过此值 → 判定周期 wrap（过滤抖动）


def format_timer(total_frames: int, fps: int = config.FRAMES_PER_SECOND) -> str:
    """总逻辑帧 → MM:SS:FF。"""
    if total_frames < 0:
        return "00:00:00"
    frames = total_frames % fps
    total_seconds = total_frames // fps
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}:{frames:02d}"


class TimeSource:
    """费用条时间状态机。喂帧驱动，产出周期帧 / 全局计时器。"""

    def __init__(self, calibration: FullCalibrationData, reset_timeout: float = RESET_TIMEOUT_S):
        self.calibration = calibration
        self.profiles = calibration.profiles
        self.reset_timeout = reset_timeout

        self.cycle_counter = 0
        self.cycle_base_frames = 0
        self.timer_offset = 0
        self.previous_frame = -1
        self.last_detect_time = 0.0
        self._total_frames = 0

    @property
    def num_profiles(self) -> int:
        return len(self.profiles)

    @property
    def active_profile(self):
        return self.profiles[self.cycle_counter % self.num_profiles]

    def update(self, frame: np.ndarray) -> int | None:
        """喂一帧，更新状态。返回当前周期内逻辑帧（None = 未检出）。"""
        roi = tick.find_cost_bar_roi(frame.shape[1], frame.shape[0])
        profile = self.active_profile
        lf = tick.get_logical_frame(frame, roi, profile.pixel_map)
        now = time.time()

        if lf is None:
            self.previous_frame = -1
            if self.last_detect_time and now - self.last_detect_time > self.reset_timeout:
                if self.cycle_counter or self.cycle_base_frames or self.timer_offset:
                    logger.info("time_source: %ss 无检测，重置", self.reset_timeout)
                    self._reset_state()
            return None

        total_this = profile.total_frames
        # 周期完成：帧显著下降（> _WRAP_MARGIN）→ wrap（适配部署时 bar 不走满的情况）
        if self.previous_frame >= 0 and lf < self.previous_frame - _WRAP_MARGIN:
            self.cycle_base_frames += total_this
            self.cycle_counter += 1
            logger.info(
                "time_source: 周期 %d 完成 (cycle_base=%d)",
                self.cycle_counter,
                self.cycle_base_frames,
            )

        self.previous_frame = lf
        self.last_detect_time = now
        self._total_frames = self.timer_offset + self.cycle_base_frames + lf
        return lf

    def _reset_state(self) -> None:
        self.cycle_counter = 0
        self.cycle_base_frames = 0
        self.timer_offset = 0
        self._total_frames = 0
        self.previous_frame = -1

    # --- 查询 ---

    @property
    def current_frame_in_cycle(self) -> int | None:
        """当前周期内逻辑帧（0..total-1），未检出返回 None。"""
        return self.previous_frame if self.previous_frame >= 0 else None

    @property
    def total_elapsed_frames(self) -> int:
        """全局累计逻辑帧（驱动 MM:SS:FF 计时器）。"""
        return self._total_frames

    @property
    def is_running(self) -> bool:
        return self.previous_frame >= 0

    @property
    def total_frames_in_cycle(self) -> int:
        return self.active_profile.total_frames

    @property
    def timer_str(self) -> str:
        return format_timer(self._total_frames)

    def display(self, mode: str = "0_to_n-1") -> tuple[str, str]:
        """返回 (display_frame, display_total) 供悬浮窗。

        mode: "0_to_n-1" | "0_to_n" | "1_to_n"。
        """
        total = self.active_profile.total_frames
        dt = total - 1 if mode == "0_to_n-1" else total
        lf = self.previous_frame
        if lf < 0:
            return ("--", f"/{dt}")
        df = lf + 1 if mode == "1_to_n" else lf
        return (str(df), f"/{dt}")

    # --- 手动调整 ---

    def adjust_timer(self, frames: int) -> None:
        self.timer_offset += frames
        self._total_frames += frames

    def reset_timer(self) -> None:
        self._reset_state()
        logger.info("time_source: 全局计时器已重置")
