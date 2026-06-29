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
        """1280x720 BGR 帧：费用条填充 + 减号标记（触发 detect_negative_cost）。"""
        roi = tick.find_cost_bar_roi(1280, 720)
        x1, x2, y = roi
        frame = np.full((720, 1280, 3), 150, dtype=np.uint8)
        if fill_width > 0:
            frame[y, x1 : x1 + fill_width] = 255
        # 减号 ROI 全白 → detect_negative_cost 返回 True
        sx, sy, sw, sh = config.COST_SIGN_ROI
        frame[sy : sy + sh, sx : sx + sw] = 255
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
