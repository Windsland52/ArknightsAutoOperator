"""time_source.format_timer + tick 纯函数测试。"""

from __future__ import annotations

import numpy as np

from aao import config
from aao.core.timing import tick
from aao.core.timing.calibration import CalibrationProfile, FullCalibrationData
from aao.core.timing.time_source import TimeSource, format_timer


class TestFormatTimer:
    def test_zero(self) -> None:
        assert format_timer(0) == "00:00:00"

    def test_negative(self) -> None:
        assert format_timer(-5) == "00:00:00"

    def test_one_second(self) -> None:
        # 30 帧 = 1 秒
        assert format_timer(30) == "00:01:00"

    def test_one_minute(self) -> None:
        # 60 秒 = 1800 帧
        assert format_timer(1800) == "01:00:00"

    def test_mixed(self) -> None:
        # 1 分 2 秒 3 帧 = 1*1800 + 2*30 + 3 = 1863
        assert format_timer(1863) == "01:02:03"

    def test_custom_fps(self) -> None:
        # fps=60: 60 帧=1 秒
        assert format_timer(60, fps=60) == "00:01:00"


class TestFindCostBarRoi:
    def test_roi_within_frame(self) -> None:
        roi = tick.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi  # Roi = (x1, x2, y)
        assert 0 <= x1 < x2 <= 1280
        assert 0 <= y < 720
        # ROI 在右下区域（费用条位置）
        assert x1 > 1000  # 靠右
        assert y > 400  # 靠下

    def test_roi_scales_with_resolution(self) -> None:
        roi_720 = tick.find_cost_bar_roi(1280, 720)
        roi_1080 = tick.find_cost_bar_roi(1920, 1080)
        # 比例一致（同一相对位置）
        assert abs(roi_720[0] / 1280 - roi_1080[0] / 1920) < 0.01


class TestGetFilledPixelWidth:
    def test_all_white(self) -> None:
        """全白 ROI → 填充宽度 = ROI 宽。"""
        roi = (100, 200, 100)  # (x1, x2, y)
        frame = np.full((102, 1280, 3), 255, dtype=np.uint8)
        w = tick.get_filled_pixel_width(frame, roi)
        assert w is not None
        assert w > 0

    def test_all_black(self) -> None:
        """全黑 ROI → 无白色填充。"""
        roi = (100, 200, 100)
        frame = np.zeros((102, 1280, 3), dtype=np.uint8)
        w = tick.get_filled_pixel_width(frame, roi)
        assert w is None or w == 0


class TestGetLogicalFrame:
    """离散最近邻逻辑帧查找（正费路径用）。

    pixel_map value = internal frame。返回 display = internal + 1
    （width=0 端点返回 0）。对应 Rust open_interior_internal_frame。
    """

    @staticmethod
    def _make_frame(fill_width: int, roi: tick.Roi, width: int = 200) -> np.ndarray:
        x1, x2, y = roi
        frame = np.full((y + 2, width, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        return frame

    def test_width_zero_endpoint(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "3": 1, "7": 2}  # internal
        frame = self._make_frame(0, roi)
        result = tick.get_logical_frame(frame, roi, pixel_map)
        assert result == 0

    def test_exact_hit_returns_display(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "3": 1, "7": 2, "11": 3}
        frame = self._make_frame(3, roi)
        result = tick.get_logical_frame(frame, roi, pixel_map)
        assert result == 2  # display = internal(1) + 1

    def test_approximate_match(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "10": 2, "20": 4}
        frame = self._make_frame(8, roi)  # 距 10 差 2，容差内
        result = tick.get_logical_frame(frame, roi, pixel_map)
        assert result == 3  # display = internal(2) + 1

    def test_beyond_tolerance(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"5": 1, "10": 3}
        frame = self._make_frame(50, roi)
        result = tick.get_logical_frame(frame, roi, pixel_map)
        assert result is None


class TestGetLogicalFrameF64:
    """浮点插值逻辑帧查找（负费重投射用）。

    pixel_map value = internal frame。函数返回 display = internal + 1
    （width=0 端点返回 0）。对应 Rust lookup_interpolated_frame +
    open_interior_internal_frame_f64。
    """

    @staticmethod
    def _make_frame(fill_width: int, roi: tick.Roi, width: int = 200) -> np.ndarray:
        x1, x2, y = roi
        frame = np.full((y + 2, width, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        return frame

    def test_width_zero_endpoint_returns_zero(self) -> None:
        """width=0 是周期起点端点，display=0（不经过 internal+1）。"""
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "5": 1, "10": 3}
        frame = self._make_frame(0, roi)
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result == 0.0

    def test_exact_hit_returns_display(self) -> None:
        """width=5, internal=1 → display=2。"""
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "5": 1, "10": 3, "15": 5}
        frame = self._make_frame(5, roi)
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result == 2.0

    def test_interpolation_on_internal_then_plus_one(self) -> None:
        """width=7 在 (5,internal=1) 和 (10,internal=3) 之间。

        internal 插值：1 + (7-5)/(10-5) * (3-1) = 1.8
        display = 1.8 + 1 = 2.8
        """
        roi = (100, 200, 100)
        pixel_map = {"0": 0, "5": 1, "10": 3, "15": 5}
        frame = self._make_frame(7, roi)
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result is not None
        assert abs(result - 2.8) < 1e-9

    def test_left_edge_within_tolerance(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"5": 1, "10": 3}  # no width=0 entry
        frame = self._make_frame(3, roi)  # 3 距 5 差 2，在容差 5 内
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result == 2.0  # display = internal(1) + 1

    def test_right_edge_within_tolerance(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"5": 1, "10": 3}
        frame = self._make_frame(12, roi)  # 12 距 10 差 2，在容差 5 内
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result == 4.0  # display = internal(3) + 1

    def test_beyond_tolerance_returns_none(self) -> None:
        roi = (100, 200, 100)
        pixel_map = {"5": 1, "10": 3}
        frame = self._make_frame(50, roi)  # 50 距最近点 10 差 40，超出容差
        result = tick.get_logical_frame_f64(frame, roi, pixel_map)
        assert result is None


class TestNegativeCostReprojection:
    """负费相位重投射：验证 multiplier 修正 + 插值精度 + 早期帧对齐。

    pixel_map value = internal frame。tick 层返回 display = internal + 1。
    负费重投射：phase = display / total → round(phase * eff_total)。

    旧代码两个 bug（已修复）：
    A. round(phase * (eff-1)) → 下半段少 1 帧（改为 round(phase*eff), clamp）
    B. 离散最近邻丢亚帧精度（改为 f64 插值）
    C. pixel_map 用 display frame，插值起点 width=0→0 而非 internal+1=1，
       导致早期帧偏移（改为 internal frame + tick 层 +1 转换）
    """

    @staticmethod
    def _make_negative_frame(fill_width: int) -> np.ndarray:
        """1280x720 BGR 帧：费用条填充 + 减号标记（触发 detect_negative_cost）+ InBattle glyph。"""
        from aao.core.timing.battle_state import _battle_button_scale, _pause_glyph_rect

        roi = tick.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        # 减号 ROI 全白 → detect_negative_cost 返回 True
        sx, sy, sw, sh = config.COST_SIGN_ROI
        frame[sy : sy + sh, sx : sx + sw] = 255
        # 暂停 glyph ROI 填充亮像素（InBattle 信号）
        scale = _battle_button_scale(1280, 720)
        gl, gr, gt, gb = _pause_glyph_rect(1280, 720, scale)
        filled = 0
        for gy in range(gt, gb):
            for gx in range(gl, gr):
                if filled >= 635:
                    break
                frame[gy, gx] = 255
                filled += 1
        return frame

    @staticmethod
    def _make_calibration(pixel_map: dict[str, int], total_frames: int) -> FullCalibrationData:
        profile = CalibrationProfile(total_frames=total_frames, pixel_map=pixel_map)
        return FullCalibrationData(
            detection_mode="single",
            profiles=[profile],
            screen_width=1280,
            screen_height=720,
        )

    @staticmethod
    def _standard_internal_map(total: int) -> dict[str, int]:
        """标准校准生成的 internal frame pixel_map：width=w → internal=w-1。"""
        return {str(w): w - 1 for w in range(1, total)} | {"0": 0}

    def test_phase_half_maps_to_midpoint(self) -> None:
        """width=15, internal=14 → display=15 → phase=0.5 → round(0.5*60)=30。"""
        pixel_map = self._standard_internal_map(30)
        cal = self._make_calibration(pixel_map, 30)
        ts = TimeSource(cal)
        frame = self._make_negative_frame(15)
        lf = ts.update(frame)
        assert lf is not None
        assert lf == 30

    def test_upper_half_not_off_by_one(self) -> None:
        """width=22, internal=21 → display=22 → phase=0.733 → round(0.733*60)=44。"""
        pixel_map = self._standard_internal_map(30)
        cal = self._make_calibration(pixel_map, 30)
        ts = TimeSource(cal)
        frame = self._make_negative_frame(22)
        lf = ts.update(frame)
        assert lf is not None
        assert lf == 44

    def test_early_frame_zero_width(self) -> None:
        """width=0 → display=0 → phase=0 → lf=0。"""
        pixel_map = self._standard_internal_map(30)
        cal = self._make_calibration(pixel_map, 30)
        ts = TimeSource(cal)
        frame = self._make_negative_frame(0)
        lf = ts.update(frame)
        assert lf is not None
        assert lf == 0

    def test_interpolation_preserves_subframe_precision(self) -> None:
        """width=15 在 (10,internal=9) 和 (20,internal=19) 之间。

        internal 插值：9 + (15-10)/(20-10) * (19-9) = 14.0
        display = 14.0 + 1 = 15.0 → phase=0.5 → round(0.5*60)=30
        """
        pixel_map = self._standard_internal_map(30)
        cal = self._make_calibration(pixel_map, 30)
        ts = TimeSource(cal)
        frame = self._make_negative_frame(15)
        lf = ts.update(frame)
        assert lf is not None
        assert lf == 30

    def test_matches_rust_reference_negative_cost_midpoint(self) -> None:
        """交叉验证：对应 Rust reference 的 entering_negative_cost_at_same_phase_does_not_jump。

        Rust 期望：width=15, negative, total=30 → logical_frame=30
        Python（internal frame pixel_map）：width=15 → internal=14 → display=15
        → phase=0.5 → round(0.5*60)=30。两边一致。
        """
        pixel_map = self._standard_internal_map(30)
        cal = self._make_calibration(pixel_map, 30)
        ts = TimeSource(cal)
        frame = self._make_negative_frame(15)
        lf = ts.update(frame)
        assert lf is not None
        assert lf == 30  # Rust reference 期望值


class TestCalibrationMigration:
    """校准文件格式迁移：v1 (display frame) → v2 (internal frame)。"""

    def test_migrate_v1_to_v2(self) -> None:
        from aao.core.timing.calibration import _migrate_v1_to_v2

        # v1 格式：pixel_map value = display frame
        profiles_v1 = [{"total_frames": 30, "pixel_map": {"0": 0, "3": 2, "7": 3, "11": 4}}]
        migrated = _migrate_v1_to_v2(profiles_v1)
        pm = migrated[0]["pixel_map"]
        # width=0 不变（端点）
        assert pm["0"] == 0
        # 非零 width 的 frame -1（display → internal）
        assert pm["3"] == 1
        assert pm["7"] == 2
        assert pm["11"] == 3

    def test_from_dict_migrates_v1(self) -> None:
        from aao.core.timing.calibration import from_dict

        raw_v1 = {
            "detection_mode": "single",
            "profiles": [{"total_frames": 30, "pixel_map": {"0": 0, "5": 2, "10": 4}}],
            "screen_width": 1280,
            "screen_height": 720,
            "calibration_time": 0.0,
        }
        cal = from_dict(raw_v1)
        pm = cal.profiles[0].pixel_map
        # 迁移后 width=0 → 0, width=5 → 1 (internal), width=10 → 3 (internal)
        assert pm["0"] == 0
        assert pm["5"] == 1
        assert pm["10"] == 3

    def test_from_dict_v2_no_migration(self) -> None:
        from aao.core.timing.calibration import from_dict

        raw_v2 = {
            "format_version": 2,
            "detection_mode": "single",
            "profiles": [{"total_frames": 30, "pixel_map": {"0": 0, "5": 1, "10": 3}}],
            "screen_width": 1280,
            "screen_height": 720,
            "calibration_time": 0.0,
        }
        cal = from_dict(raw_v2)
        pm = cal.profiles[0].pixel_map
        # v2 不迁移
        assert pm["0"] == 0
        assert pm["5"] == 1
        assert pm["10"] == 3


class TestOcclusionNone:
    """遮挡检测（tick 层）：灰色遮挡物截断白色填充 → None → hold。

    _contiguous_fill_width 检测到 edge 后仍有白色像素 → 费用条被遮挡 → None。
    正常费用条末端（edge 后全灰，无白色）→ 返回 edge。
    """

    @staticmethod
    def _make_calibration(total: int = 30) -> FullCalibrationData:
        pixel_map = {str(w): w - 1 for w in range(1, total)} | {"0": 0}
        return FullCalibrationData(
            detection_mode="single",
            profiles=[CalibrationProfile(total_frames=total, pixel_map=pixel_map)],
            screen_width=1280,
            screen_height=720,
        )

    @staticmethod
    def _make_cost_bar_frame(fill_width: int) -> np.ndarray:
        """构造有费用条填充的帧 + InBattle 暂停 glyph。"""
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.battle_state import _battle_button_scale, _pause_glyph_rect

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        scale = _battle_button_scale(1280, 720)
        gl, gr, gt, gb = _pause_glyph_rect(1280, 720, scale)
        filled = 0
        for gy in range(gt, gb):
            for gx in range(gl, gr):
                if filled >= 635:
                    break
                frame[gy, gx] = 255
                filled += 1
        return frame

    def test_gray_occluder_returns_none(self) -> None:
        """灰色遮挡物夹在白色填充中间 → None（费用条被遮挡）。"""
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.tick import get_filled_pixel_width

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        bar_w = x2 - x1
        frame = self._make_cost_bar_frame(bar_w)  # 填满整条
        # 在费用条 1/3 处插入灰色遮挡物，遮挡物之后仍有白色
        mid = x1 + bar_w // 3
        frame[y, mid : mid + 5] = 150  # 灰色遮挡物
        pw = get_filled_pixel_width(frame, roi)
        assert pw is None

    def test_non_gray_occluder_returns_none(self) -> None:
        """非灰度遮挡物夹在白色填充中间 → None。"""
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.tick import get_filled_pixel_width

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        bar_w = x2 - x1
        frame = self._make_cost_bar_frame(bar_w)
        mid = x1 + bar_w // 3
        frame[y, mid : mid + 5] = [0, 0, 255]  # 彩色遮挡物
        pw = get_filled_pixel_width(frame, roi)
        assert pw is None

    def test_normal_fill_returns_width(self) -> None:
        """正常费用条（白色填充 + 灰色末端）→ 返回正确宽度。"""
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.tick import get_filled_pixel_width

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        frame = self._make_cost_bar_frame(200)
        pw = get_filled_pixel_width(frame, roi)
        assert pw is not None
        assert pw > 0

    def test_occlusion_hold_in_time_source(self) -> None:
        """遮挡 → lf=None → TimeSource hold previous_frame，不跳帧。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_cost_bar_frame(15))
        held = ts.previous_frame
        from aao.core.timing import tick as tick_mod

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        bar_w = x2 - x1
        frame = self._make_cost_bar_frame(bar_w)
        mid = x1 + bar_w // 3
        frame[y, mid : mid + 5] = 150  # 灰色遮挡物
        result = ts.update(frame)
        assert result == held  # hold，不跳
        assert ts.previous_frame == held

    def test_wrap_still_works(self) -> None:
        """正常 wrap（lf 29→1）→ cycle_counter++，遮挡检测不干扰。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_cost_bar_frame(29))
        assert ts.cycle_counter == 0
        ts.update(self._make_cost_bar_frame(1))
        assert ts.cycle_counter == 1


class TestOcclusionHoldTimeSource:
    """遮挡 hold（time_source 层）：pw=0 伪装成空费用条 → lf=0。

    实际遮挡场景（来自游戏日志）：
    - 费用条左端被灰色遮挡物盖住 → pw=0 → lf=0
    - 但 _prev_measured 在中间值（非满条）→ 不是正常 wrap → hold

    遮挡 hold 规则：
    - lf 大幅回退（> _WRAP_MARGIN）
    - 且 lf 在中间值（>= _WRAP_MARGIN）→ hold（遮挡导致读到偏小值）
    - 或 lf 接近 0（< _WRAP_MARGIN）但 prev 不在满条附近 → hold（遮挡导致 pw=0）
    - 正常 wrap（prev 在满条附近 → lf=0）→ 不 hold
    - 负费切换（neg_changed）→ 不 hold
    """

    @staticmethod
    def _make_calibration(total: int = 30) -> FullCalibrationData:
        pixel_map = {str(w): w - 1 for w in range(1, total)} | {"0": 0}
        return FullCalibrationData(
            detection_mode="single",
            profiles=[CalibrationProfile(total_frames=total, pixel_map=pixel_map)],
            screen_width=1280,
            screen_height=720,
        )

    @staticmethod
    def _make_frame(fill_width: int) -> np.ndarray:
        """InBattle 帧 + 费用条填充。fill_width=0 → 空费用条（全灰）。"""
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.battle_state import _battle_button_scale, _pause_glyph_rect

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        scale = _battle_button_scale(1280, 720)
        gl, gr, gt, gb = _pause_glyph_rect(1280, 720, scale)
        filled = 0
        for gy in range(gt, gb):
            for gx in range(gl, gr):
                if filled >= 635:
                    break
                frame[gy, gx] = 255
                filled += 1
        return frame

    @staticmethod
    def _width_for_frame(target_lf: int, total: int = 30) -> int:
        """display frame → 像素宽度（查测试 calibration 的 pixel_map 反转）。

        测试用 pixel_map: {0:0, 1:0, 2:1, ..., 29:28}。
        display = internal+1（width=0 → display=0）。
        key=w, value=w-1, display=w-1+1=w → display=target_lf → key=target_lf。
        """
        if target_lf == 0:
            return 0
        return target_lf  # pixel_map key=target_lf → display=target_lf

    def test_occlusion_pw0_mid_value_holds(self) -> None:
        """遮挡 pw=0：lf 16→0（prev 不满条）→ hold，不 wrap。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        # 喂到 lf=16（中间值）
        w16 = self._width_for_frame(16)
        ts.update(self._make_frame(w16))
        assert ts.previous_frame >= 0
        held = ts.previous_frame
        # 遮挡 → 空费用条 → lf=0
        ts.update(self._make_frame(0))
        assert ts.cycle_counter == 0  # 不 wrap
        assert ts.previous_frame == held  # hold

    def test_occlusion_pw0_high_value_holds(self) -> None:
        """遮挡 pw=0：lf 23→0（prev 不满条）→ hold。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        w23 = self._width_for_frame(23)
        ts.update(self._make_frame(w23))
        held = ts.previous_frame
        ts.update(self._make_frame(0))
        assert ts.cycle_counter == 0
        assert ts.previous_frame == held

    def test_normal_wrap_from_full_not_held(self) -> None:
        """正常 wrap：lf 29→0（prev 在满条附近）→ wrap，不 hold。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        w29 = self._width_for_frame(29)
        ts.update(self._make_frame(w29))
        assert ts.cycle_counter == 0
        ts.update(self._make_frame(0))
        assert ts.cycle_counter == 1  # wrap

    def test_normal_wrap_from_near_full_not_held(self) -> None:
        """正常 wrap：lf 27→0（prev=27 >= 27）→ wrap。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        w27 = self._width_for_frame(27)
        ts.update(self._make_frame(w27))
        ts.update(self._make_frame(0))
        assert ts.cycle_counter == 1

    def test_occlusion_mid_value_to_mid_value_holds(self) -> None:
        """遮挡 pw=偏小值：lf 22→15（中间值回退）→ hold。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        w22 = self._width_for_frame(22)
        w15 = self._width_for_frame(15)
        ts.update(self._make_frame(w22))
        held = ts.previous_frame
        ts.update(self._make_frame(w15))
        assert ts.cycle_counter == 0
        assert ts.previous_frame == held

    def test_occlusion_recovery(self) -> None:
        """遮挡恢复：lf 16→0(hold)→17(恢复) → 正常更新。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        w16 = self._width_for_frame(16)
        ts.update(self._make_frame(w16))
        # 遮挡
        ts.update(self._make_frame(0))
        assert ts.cycle_counter == 0
        # 恢复
        w17 = self._width_for_frame(17)
        ts.update(self._make_frame(w17))
        assert ts.cycle_counter == 0  # 仍未 wrap
        assert ts.previous_frame >= 0  # 正常更新


class TestBattleStateIntegration:
    """清零规则：费用条可见性 + BattleBegin 双路径。

    - InBattle → arm + 读费用条
    - NotInBattle + 费用条可见（暂停）→ 不清零
    - NotInBattle + 费用条不可见（lf=None）连续 >= 30 帧 → 标记重入清零
    - 重新 InBattle + 标记 → reset（进关卡清计时器）
    - BattleBegin + armed → reset + disarm
    """

    @staticmethod
    def _make_calibration(total: int = 30) -> FullCalibrationData:
        pixel_map = {str(w): w - 1 for w in range(1, total)} | {"0": 0}
        return FullCalibrationData(
            detection_mode="single",
            profiles=[CalibrationProfile(total_frames=total, pixel_map=pixel_map)],
            screen_width=1280,
            screen_height=720,
        )

    @staticmethod
    def _make_in_battle_frame(fill_width: int) -> np.ndarray:
        """InBattle 帧：暂停 glyph ROI 亮 + 费用条填充。

        1280x720: 暂停 glyph ROI (1188, 1230, 38, 69)。
        需要亮像素归一化面积 ~635（播放区间 [560,710]）→ 填充 635 个亮像素。
        """
        from aao.core.timing import tick as tick_mod
        from aao.core.timing.battle_state import _battle_button_scale, _pause_glyph_rect

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        # 暂停 glyph ROI 填充亮像素（播放状态）
        scale = _battle_button_scale(1280, 720)
        gl, gr, gt, gb = _pause_glyph_rect(1280, 720, scale)
        # 填充前 635 个像素（归一化面积 635，在 [560,710] 内）
        filled = 0
        target = 635
        for gy in range(gt, gb):
            for gx in range(gl, gr):
                if filled >= target:
                    break
                frame[gy, gx] = 255
                filled += 1
            if filled >= target:
                break
        return frame

    @staticmethod
    def _make_not_in_battle_frame() -> np.ndarray:
        """NotInBattle 帧：两个 glyph ROI 全暗 + 费用条 ROI 末端非灰度 → None。

        全黑帧会让 get_filled_pixel_width 返回 0 → lf=0 → 误 wrap。
        末端放彩色像素 → None → hold，模拟真实遮挡场景。
        """
        from aao.core.timing import tick as tick_mod

        frame = np.full((720, 1280, 3), 0, dtype=np.uint8)
        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame[y, x2 - 1] = [200, 0, 0]  # 末端非灰度 → None
        return frame

    def test_in_battle_reads_cost_bar_and_arms(self) -> None:
        """InBattle 帧 + 费用条填充 → 正常读帧 + arm。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        result = ts.update(self._make_in_battle_frame(15))
        assert result is not None
        assert ts.previous_frame >= 0
        assert ts._battle_begin_reset_armed is True

    def test_not_in_battle_with_cost_bar_no_reset(self) -> None:
        """NotInBattle + 费用条仍可见（暂停）→ 不标记清零。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_in_battle_frame(15))
        # NotInBattle 但费用条有填充（暂停时费用条可见）
        from aao.core.timing import tick as tick_mod

        roi = tick_mod.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        for _ in range(100):
            # 全灰背景 + 费用条填充（末端灰度 → get_filled_pixel_width 非 None）
            frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
            frame[y, x1 : x1 + 15] = 255  # 费用条填充 → lf != None
            ts.update(frame)
        assert ts._needs_reentry_reset is False  # 未标记清零

    def test_not_in_battle_no_cost_bar_marks_reset(self) -> None:
        """NotInBattle + 费用条不可见（lf=None）连续 >= 180 帧 → 标记清零。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_in_battle_frame(15))
        assert ts._needs_reentry_reset is False
        # 180 帧 NotInBattle + 费用条不可见（None）
        for _ in range(180):
            ts.update(self._make_not_in_battle_frame())
        assert ts._needs_reentry_reset is True  # 标记清零

    def test_reentry_in_battle_resets(self) -> None:
        """标记清零后 → InBattle → reset（进关卡清计时器）。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_in_battle_frame(15))
        assert ts._total_frames > 0
        # 180 帧 NotInBattle + 费用条不可见 → 标记
        for _ in range(180):
            ts.update(self._make_not_in_battle_frame())
        assert ts._needs_reentry_reset is True
        # 重新 InBattle → reset
        ts.update(self._make_in_battle_frame(5))
        assert ts._needs_reentry_reset is False
        assert ts.cycle_base_frames == 0
        assert ts.timer_offset == 0

    def test_short_no_cost_bar_no_reset(self) -> None:
        """NotInBattle + 费用条不可见 < 180 帧 → 不标记清零。"""
        cal = self._make_calibration()
        ts = TimeSource(cal)
        ts.update(self._make_in_battle_frame(15))
        for _ in range(179):
            ts.update(self._make_not_in_battle_frame())
        assert ts._needs_reentry_reset is False  # 未到阈值
