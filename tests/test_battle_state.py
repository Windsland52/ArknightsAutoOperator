"""battle_state 战斗状态检测测试。"""

from __future__ import annotations

import numpy as np

from aao.core.timing.battle_state import (
    BattleState,
    _battle_button_scale,
    _classify_pause,
    _pause_glyph_rect,
    detect_battle_state,
)

# 1280x720 暂停 glyph ROI: (1188, 1230, 38, 69) = 42x31 = 1302 px


def _make_frame_with_bright_count(
    bright_count: int,
    width: int = 1280,
    height: int = 720,
) -> np.ndarray:
    """构造帧：暂停 glyph ROI 内前 bright_count 个像素亮（255），其余暗。"""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    left, right, top, bottom = _pause_glyph_rect(width, height, _battle_button_scale(width, height))
    filled = 0
    for y in range(top, bottom):
        for x in range(left, right):
            if filled >= bright_count:
                return frame
            frame[y, x] = 255
            filled += 1
    return frame


class TestBattleButtonScale:
    def test_720p(self) -> None:
        assert _battle_button_scale(1280, 720) == 1.0

    def test_1080p(self) -> None:
        assert _battle_button_scale(1920, 1080) == 1.5

    def test_1440p(self) -> None:
        assert abs(_battle_button_scale(2560, 1440) - 2.0) < 1e-9


class TestPauseGlyphRect:
    def test_720p(self) -> None:
        left, right, top, bottom = _pause_glyph_rect(1280, 720, 1.0)
        assert (left, right, top, bottom) == (1188, 1230, 38, 69)

    def test_1080p(self) -> None:
        left, right, top, bottom = _pause_glyph_rect(1920, 1080, 1.5)
        assert (left, right, top, bottom) == (1782, 1845, 57, 104)


class TestClassifyPause:
    def test_paused_range(self) -> None:
        assert _classify_pause(450.0)  # [380, 500]

    def test_running_range(self) -> None:
        assert _classify_pause(630.0)  # [560, 710]

    def test_below_ranges(self) -> None:
        assert not _classify_pause(100.0)

    def test_between_ranges(self) -> None:
        # (500, 560) 是 gap，不判 in-battle
        assert not _classify_pause(530.0)

    def test_above_ranges(self) -> None:
        assert not _classify_pause(800.0)


class TestDetectBattleState:
    def test_dark_frame_not_in_battle(self) -> None:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        assert detect_battle_state(frame) is BattleState.NOT_IN_BATTLE

    def test_running_glyph_in_battle(self) -> None:
        """播放 glyph：亮像素归一化面积 ~635（落在 [560,710]）。"""
        frame = _make_frame_with_bright_count(635)
        assert detect_battle_state(frame) is BattleState.IN_BATTLE

    def test_paused_glyph_in_battle(self) -> None:
        """暂停 glyph：亮像素归一化面积 ~450（落在 [380,500]）。"""
        frame = _make_frame_with_bright_count(450)
        assert detect_battle_state(frame) is BattleState.IN_BATTLE

    def test_too_bright_not_in_battle(self) -> None:
        """全亮 ROI → 1302 > 710，超出范围 → NotInBattle。"""
        frame = np.full((720, 1280, 3), 255, dtype=np.uint8)
        assert detect_battle_state(frame) is BattleState.NOT_IN_BATTLE

    def test_scale_invariant_1080p(self) -> None:
        """1920x1080 下同样亮像素归一化面积 → 同判定。"""
        # 1080p: scale=1.5, ROI = 63×47 = 2961 px, 归一化 ÷ 1.5² = ÷2.25
        # 要归一化 ~635 → 实际像素 635*2.25 = 1429
        frame = _make_frame_with_bright_count(1429, 1920, 1080)
        assert detect_battle_state(frame) is BattleState.IN_BATTLE

    def test_empty_frame(self) -> None:
        frame = np.zeros((0, 0, 3), dtype=np.uint8)
        assert detect_battle_state(frame) is BattleState.NOT_IN_BATTLE

    def test_battle_state_property(self) -> None:
        assert BattleState.IN_BATTLE.is_in_battle is True
        assert BattleState.NOT_IN_BATTLE.is_in_battle is False
