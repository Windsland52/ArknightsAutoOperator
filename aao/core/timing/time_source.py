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

import time

import numpy as np

from aao import config
from aao.core.timing import tick
from aao.core.timing.calibration import FullCalibrationData
from aao.utils.logger import logger

RESET_TIMEOUT_S = 1.5
_WRAP_MARGIN = 3  # 帧下降超过此值 → 判定周期 wrap（过滤抖动）
_BOUNDARY_SWITCH_FRAME = 315  # 开局第 315 逻辑帧所在周期为边界周期


def format_timer(total_frames: int, fps: int = config.FRAMES_PER_SECOND) -> str:
    """总逻辑帧 → MM:SS:FF。"""
    if total_frames < 0:
        return "00:00:00"
    frames = total_frames % fps
    total_seconds = total_frames // fps
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}:{frames:02d}"


def _compute_boundary_cycle(
    calibration: FullCalibrationData,
    switch_frame: int = _BOUNDARY_SWITCH_FRAME,
) -> int:
    """计算 boundary_switch_frame 落在哪个周期（0-indexed）。

    从 cycle 0 起累加各 profile 的 total_frames，目标帧落入的 cycle 即为边界周期。
    """
    elapsed = 0
    num = len(calibration.profiles)
    if num == 0:
        return 0
    for i in range(100):  # 安全上限
        total = calibration.profiles[i % num].total_frames
        if switch_frame < elapsed + total:
            return i
        elapsed += total
    return 0


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

        # 负费（可露希尔）状态
        self._cost_is_negative = False

        # 边界周期：开局第 315 逻辑帧所在的周期（0-indexed），
        # 该周期多 1 帧（左闭右闭 vs 左闭右开）。
        # 负费时不叠加边界修正（回费速率不同，边界位置尚未验证）。
        self._boundary_cycle_index = _compute_boundary_cycle(calibration)

        # 每档 profile 在边界前校准时检测到的隐藏帧数。
        # 边界后（左开右闭）这些隐藏帧不存在，需从显示中跳过。
        self._num_hidden = [p.total_frames - len(p.pixel_map) for p in calibration.profiles]

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
        total_this = profile.total_frames

        # 负费（可露希尔）检测：每帧判定，适应技能开关。
        self._cost_is_negative = tick.detect_negative_cost(frame)
        effective_total = total_this * 2 if self._cost_is_negative else total_this

        # 负费（可露希尔）：用浮点插值取亚帧相位，再重投射到翻倍周期。
        # pixel_map 按正常速率校准（30f/费），负费时回费减半（60f/费），
        # 离散最近邻会丢亚帧精度 → 负费下半段系统性偏小。
        # 对应 Rust lookup_open_interior_display_frame_f64 + frame_from_phase。
        if self._cost_is_negative:
            lf_f64 = tick.get_logical_frame_f64(frame, roi, profile.pixel_map)
            if lf_f64 is not None and total_this > 0:
                phase = lf_f64 / total_this
                # 乘 effective_total 再 clamp [0, eff-1]，而非乘 (eff-1)，
                # 后者会让上半段偏小、下半段系统性少 1 帧。
                lf_measured = round(phase * effective_total)
                lf_measured = max(0, min(effective_total - 1, lf_measured))
            else:
                lf_measured = None
        else:
            lf_measured = tick.get_logical_frame(frame, roi, profile.pixel_map)
        now = time.time()

        # 边界后周期去掉 hidden 帧：校准在慢速下检测到的隐藏辉光帧（如 frame 1
        # 无独立宽度）在边界后左开右闭周期里不存在，需从帧序列中跳过。
        # frame 0（0% 起点）不动，避免超时清空。
        # 必须在满条修正之前执行，否则 endpoint 值会被误减。
        if (
            lf_measured is not None
            and lf_measured > 0
            and not self._cost_is_negative
            and self.cycle_counter > self._boundary_cycle_index
        ):
            nh = profile.total_frames - len(profile.pixel_map)
            if nh > 0:
                if lf_measured <= nh:
                    lf_measured = None  # hidden 帧，跳过
                else:
                    lf_measured -= nh  # 可见帧，回正到无 hidden 的序号

        # 满条修正：pixel_map 按左闭右开校准，不含满条宽度。满条像素宽可能
        # 被 nearest-match 误判为 frame 29（容差 5 内），需在端点周期里覆盖。
        # 需在 None 检查之前执行，否则永远不触发。
        if not self._cost_is_negative:
            pw = tick.get_filled_pixel_width(frame, roi)
            bar_w = roi[1] - roi[0]
            if pw is not None and pw >= bar_w:
                if self.cycle_counter == self._boundary_cycle_index:
                    lf_measured = total_this  # 边界周期：满条 → frame 30
                elif self.cycle_counter > self._boundary_cycle_index:
                    lf_measured = total_this - 1  # 之后周期：满条 → frame 29

        if lf_measured is None:
            self.previous_frame = -1
            # 不重置 _prev_measured：保留上次有效值，以便 wrap 瞬间的短暂 None 后仍能检测周期
            self._stuck_lf = None
            if self.last_detect_time and now - self.last_detect_time > self.reset_timeout:
                if self.cycle_counter or self.cycle_base_frames or self.timer_offset:
                    logger.debug("time_source: %ss 无检测，重置", self.reset_timeout)
                    self._reset_state()
            return None

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
            inc = effective_total
            if not self._cost_is_negative and self.cycle_counter == self._boundary_cycle_index:
                inc += 1  # 边界周期（左闭右闭），多 1 帧
            self.cycle_base_frames += inc
            self.cycle_counter += 1
            wrapped = True
            self._stuck_lf = None
            logger.debug(
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
        """PLL 外推（已禁用）。

        60fps 实测：部署拖拽时费用条完全冻结（60/60 帧不变），费用回复暂停。
        外推基于"费用条在走"的假设 → 假设不成立 → 禁用。
        部署时的帧级计时改由执行器 steptiny（里程碑5）处理。
        """
        return stuck_lf
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
        self._cost_is_negative = False

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
        base = self.active_profile.total_frames
        if self._cost_is_negative:
            return base * 2
        if self.cycle_counter == self._boundary_cycle_index:
            return base + 1
        return base

    @property
    def timer_str(self) -> str:
        return format_timer(self._total_frames)

    def display(self, mode: str = "0_to_n-1") -> tuple[str, str]:
        total = self.total_frames_in_cycle
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
        logger.debug("time_source: 全局计时器已重置")
