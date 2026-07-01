"""统一时间源：消费 tick + 校准数据 → 周期计数 + 全局计时器。

移植自 reference/ArknightsCostBarRuler-master/ruler/main.py 的 analysis_worker 状态机，
加入 PLL 外推（帧卡住时按周期估算实际帧，解决部署 UI 遮挡费用条右半部分的 PC 客户端特有问题）。

核心机制：
- 喂帧 ``update(frame)`` → 当前逻辑帧（周期内）。
- 周期 wrap 检测：帧显著下降（> _WRAP_MARGIN）→ cycle_counter++、累计 cycle_base。
- 战斗状态检测（仅用于清零规则）：InBattle → arm；BattleBegin + armed → reset_timer。
  不 gate 费用条读取（暂停/部署/结算时 pause glyph 可能弱化，gate 会丢帧）。
  暂停不清零（费用条仍可见）；退出战斗后费用条消失 >= 30 帧 → 标记重入清零；
  重新 InBattle → reset（进关卡清计时器）。
- PLL 外推：当测量帧连续不变（费用条被遮挡/走不满）时，按最近 wrap 间隔估算的周期外推帧。
- 全局计时器 ``total_elapsed = timer_offset + cycle_base + logical_frame``。
- None 时 hold 显示（不设 -1），不再用 timeout 清零。
"""

from __future__ import annotations

import time

import numpy as np

from aao import config
from aao.core.timing import tick
from aao.core.timing.battle_state import BattleState, detect_battle_state
from aao.core.timing.calibration import FullCalibrationData
from aao.utils.logger import logger

_WRAP_MARGIN = 3  # 帧下降超过此值 → 判定周期 wrap（过滤抖动）
_BOUNDARY_SWITCH_FRAME = 315  # 开局第 315 逻辑帧所在周期为边界周期
# 费用条连续不可见帧数阈值：NotInBattle + lf=None 连续 >= 此帧数
# → 标记重入清零。部署遮挡通常 < 6 秒（180帧@30fps），退出战斗到重进更长。
_NO_COST_BAR_RESET_FRAMES = 180


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

    def __init__(self, calibration: FullCalibrationData):
        self.calibration = calibration
        self.profiles = calibration.profiles

        self.cycle_counter = 0
        self.cycle_base_frames = 0
        self.timer_offset = 0
        self.previous_frame = -1  # 显示用（可能含外推）
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
        self._prev_cost_is_negative = False  # 上次负费状态（排除负费切换的遮挡 hold）
        self._stuck_lf: int | None = None  # 卡住时的测量帧
        self._stuck_since = 0.0  # 卡住开始时间
        self._last_wrap_time = 0.0  # 上次 wrap 时间
        self._cycle_period = 0.0  # 估算的周期（秒）

        # BattleBegin armed 机制（对应 Rust engine.rs:260-265）：
        # InBattle → arm；BattleBegin + armed → reset_timer + disarm。
        # 避免在非战斗界面误清零，只在"刚打完一局 → 进入下一局标题屏"时触发。
        self._battle_begin_reset_armed = False

        # 费用条消失计数器 + 重入清零标志：
        # NotInBattle 且 lf=None（费用条不可见）连续 >= _NO_COST_BAR_RESET_FRAMES 帧
        # → 标记 _needs_reentry_reset。下次 InBattle → reset（进关卡清计时器）。
        # 暂停时费用条仍可见（lf != None）→ 不计数 → 不清零。
        self._no_cost_bar_frames = 0
        self._needs_reentry_reset = False

    @property
    def num_profiles(self) -> int:
        return len(self.profiles)

    @property
    def active_profile(self):
        return self.profiles[self.cycle_counter % self.num_profiles]

    def update(self, frame: np.ndarray) -> int | None:
        """喂一帧，更新状态。返回当前周期内逻辑帧（含 PLL 外推，None = 未检出）。"""
        now = time.time()

        # --- 战斗状态检测（仅用于清零规则，不 gate 费用条读取）---
        # 对应 Rust engine.rs:253-265。
        # InBattle → arm；
        # NotInBattle（暂停/部署/结算）→ 仍读费用条，不清零。
        #   暂停时费用条仍可见 → 不清零。
        #   退出战斗后费用条消失（lf=None 连续 >= _NO_COST_BAR_RESET_FRAMES 帧）→ 标记清零。
        #   重新 InBattle 时如果标记了清零 → reset（进关卡清计时器）。
        battle_state = detect_battle_state(frame)
        if battle_state.is_in_battle:
            if self._needs_reentry_reset:
                logger.debug("time_source: 重新进入战斗，重置计时器")
                self._reset_state()
                self._needs_reentry_reset = False
                self._battle_begin_reset_armed = True
                # reset 后继续读费用条（首帧 lf 作为新起点）
            else:
                self._battle_begin_reset_armed = True
            self._no_cost_bar_frames = 0
        elif battle_state is BattleState.BATTLE_BEGIN and self._battle_begin_reset_armed:
            logger.debug("time_source: BattleBegin 标题屏 + armed → 重置计时器")
            self._reset_state()
            self._battle_begin_reset_armed = False
            self._needs_reentry_reset = False
            self._no_cost_bar_frames = 0
            return None

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
            # 费用条被遮挡或不可见 → hold 显示。
            # 同时累计费用条消失帧数：连续不可见 >= 阈值 → 标记重入清零。
            # 但只在 NotInBattle 时计数：InBattle 时 None 是遮挡（部署框），
            # 不是费用条消失，不应触发清零。
            self._stuck_lf = None
            if self._battle_begin_reset_armed and not battle_state.is_in_battle:
                self._no_cost_bar_frames += 1
                if (
                    self._no_cost_bar_frames >= _NO_COST_BAR_RESET_FRAMES
                    and not self._needs_reentry_reset
                ):
                    self._needs_reentry_reset = True
                    logger.debug(
                        "time_source: 费用条连续 %d 帧不可见，标记重入清零",
                        self._no_cost_bar_frames,
                    )
            return self.previous_frame if self.previous_frame >= 0 else None

        # 费用条可见 → 重置消失计数
        self._no_cost_bar_frames = 0

        # --- 遮挡 hold ---
        # 两种遮挡模式：
        # A. lf 回退到中间值（pw=偏小非0）→ hold
        # B. lf 回退到 0~2 但 prev 不在满条附近（pw=0，遮挡物是灰色）→ hold
        # 正常 wrap：lf 从满条（>= eff_total - _WRAP_MARGIN）回到 0~2 → 不 hold
        # 负费切换：eff_total 60↔30 导致 lf 跳变 → 不 hold（neg_changed）
        neg_changed = self._cost_is_negative != self._prev_cost_is_negative
        prev_high = self._prev_measured >= effective_total - _WRAP_MARGIN
        if (
            not neg_changed
            and self._prev_measured >= 0
            and lf_measured < self._prev_measured - _WRAP_MARGIN
            and (lf_measured >= _WRAP_MARGIN or not prev_high)
        ):
            return self.previous_frame if self.previous_frame >= 0 else None

        # --- wrap 检测（整数帧差）---
        # 旧逻辑：lf 显著下降（> _WRAP_MARGIN）→ 周期 wrap。
        # 曾尝试改用相位（prev>0.75 && cur<0.25），但负费切换时
        # total_frames_in_cycle 从 60 变 30 导致相位不连续，误判。
        # 保持整数帧差，与原始行为一致。
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
        self._prev_cost_is_negative = self._cost_is_negative
        self.previous_frame = lf
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
        self._prev_cost_is_negative = False
        self._stuck_lf = None
        self._stuck_since = 0.0
        self._last_wrap_time = 0.0
        self._cycle_period = 0.0
        self._cost_is_negative = False
        self._no_cost_bar_frames = 0
        self._needs_reentry_reset = False
        # 不清 _battle_begin_reset_armed：由 BattleBegin 转移逻辑控制

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
