"""time_source.format_timer + tick 纯函数测试。"""

from __future__ import annotations

import numpy as np

from aao.core.timing import tick
from aao.core.timing.time_source import format_timer


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
        # 1 分 2 秒 3 帧 = 60+30*2+3? 不对：total_frames=分*60*30+秒*30+帧
        # 1:02:03 = 1*1800 + 2*30 + 3 = 1863
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
