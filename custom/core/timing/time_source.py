"""统一时间源：消费 tick + 校准数据 → 周期计数 + 全局计时器。

移植自 reference/ArknightsCostBarRuler-master/ruler/main.py 的 analysis_worker 状态机，
加入 PLL 外推（帧卡住时按周期估算实际帧，解决部署 UI 遮挡费用条右半部分的 PC 客户端特有问题）。

核心机制：
- 喂帧 ``update(frame)`` → 当前逻辑帧（周期内）。
- 周期 wrap 检测：帧显著下降（> _WRAP_MARGIN）→ cycle_counter++、累计 cycle_base。
- PLL 外推：当测量帧连续不变（费用条被遮挡/走不满）时，按最近 wrap 间隔估算的周期外推帧。
- 全局计时器 ``total_elapsed = timer_offset + cycle_base + logical_frame``。
- 1.5s 无检测 → 重置。
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
    """费用条时间状态机 + PLL 外推。喂帧驱动，产出周期帧 / 全局计时器。"""

    def __init__(self, calibration: FullCalibrationData, reset_timeout: float = RESET_TIMEOUT_S):
        self.calibration = calibration
        self.profiles = calibration.profiles
        self.reset_timeout = reset_timeout

        self.cycle_counter = 0
        self.cycle_base_frames = 0
        self.timer_offset = 0
        self.previous_frame = -1  # 显示用（可能含外推）
        self.last_detect_time = 0.0
        self._total_frames = 0

        # PLL 外推状态
        self._prev_measured = -1  # 上次测量帧（用于 wrap 检测）
        self._stuck_lf: int | None = None  # 卡住时的测量帧
        self._stuck_since = 0.0  # 卡住开始时间
        self._last_wrap_time = 0.0  # 上次 wrap 时间
        self._cycle_period = 0.0  # 估算的周期（秒）

    @property
    def num_profiles(self) -> int:
        return len(self.profiles)

    @property
    def active_profile(self):
        return self.profiles[self.cycle_counter % self.num_profiles]

    def update(self, frame: np.ndarray) -> int | None:
        """喂一帧，更新状态。返回当前周期内逻辑帧（含 PLL 外推，None = 未检出）。"""
        roi = tick.find_cost_bar_roi(frame.shape[1], frame.shape[0])
        profile = self.active_profile
        lf_measured = tick.get_logical_frame(frame, roi, profile.pixel_map)
        now = time.time()

        if lf_measured is None:
            self.previous_frame = -1
            self._prev_measured = -1
            self._stuck_lf = None
            if self.last_detect_time and now - self.last_detect_time > self.reset_timeout:
                if self.cycle_counter or self.cycle_base_frames or self.timer_offset:
                    logger.info("time_source: %ss 无检测，重置", self.reset_timeout)
                    self._reset_state()
            return None

        total_this = profile.total_frames

        # --- wrap 检测（基于测量帧）---
        wrapped = False
        if self._prev_measured >= 0 and lf_measured < self._prev_measured - _WRAP_MARGIN:
            # 更新周期估算
            if self._last_wrap_time > 0:
                period = now - self._last_wrap_time
                if self._cycle_period > 0:
                    self._cycle_period = 0.7 * self._cycle_period + 0.3 * period
                else:
                    self._cycle_period = period
            self._last_wrap_time = now
            self.cycle_base_frames += total_this
            self.cycle_counter += 1
            wrapped = True
            self._stuck_lf = None
            logger.info(
                "time_source: 周期 %d (base=%d, period=%.2fs)",
                self.cycle_counter,
                self.cycle_base_frames,
                self._cycle_period,
            )

        # --- PLL 外推（帧卡住时按周期估算）---
        if not wrapped and lf_measured == self._prev_measured:
            # 测量帧不变 → 可能卡住（遮挡/走不满）
            if self._stuck_lf is None:
                self._stuck_lf = lf_measured
                self._stuck_since = now
            lf = self._extrapolate(lf_measured, total_this, now)
        else:
            # 帧变了（正常走动或刚 wrap）→ 用测量值
            self._stuck_lf = None
            lf = lf_measured

        self._prev_measured = lf_measured
        self.previous_frame = lf
        self.last_detect_time = now
        self._total_frames = self.timer_offset + self.cycle_base_frames + lf
        return lf

    def _extrapolate(self, stuck_lf: int, total: int, now: float) -> int:
        """PLL 外推：按估算周期 + 卡住时长推算实际帧。"""
        if self._cycle_period <= 0 or self._stuck_lf is None:
            return stuck_lf  # 周期未知 → 不外推
        elapsed = now - self._stuck_since
        fps = total / self._cycle_period
        extrap = stuck_lf + int(elapsed * fps)
        return min(extrap, total - 1)  # 不超过周期上限

    def _reset_state(self) -> None:
        self.cycle_counter = 0
        self.cycle_base_frames = 0
        self.timer_offset = 0
        self._total_frames = 0
        self.previous_frame = -1
        self._prev_measured = -1
        self._stuck_lf = None
        self._stuck_since = 0.0
        self._last_wrap_time = 0.0
        self._cycle_period = 0.0

    # --- 查询 ---

    @property
    def current_frame_in_cycle(self) -> int | None:
        return self.previous_frame if self.previous_frame >= 0 else None

    @property
    def total_elapsed_frames(self) -> int:
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
