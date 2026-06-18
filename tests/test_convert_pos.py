"""convert_pos 棋盘坐标转换测试。"""

from __future__ import annotations

import pytest

from aao.core.geometry.convert_pos import convert_position, tile_position_to_str


class TestConvertPosition:
    def test_basic_a1(self) -> None:
        # 7 行地图，A1 = 最左下 → col=0, row=6
        assert convert_position("A1", 7, 10) == (0, 6)

    def test_d2(self) -> None:
        # docstring 示例：7 行 10 列，D2 → col=3, row=5
        assert convert_position("D2", 7, 10) == (3, 5)

    def test_g10(self) -> None:
        # 10 行地图，G10 → col=6, row=0（最上行）
        assert convert_position("G10", 10, 10) == (6, 0)

    def test_lowercase(self) -> None:
        # 小写字母也能识别
        assert convert_position("d2", 7, 10) == (3, 5)

    def test_multiletter_column(self) -> None:
        # AA = 第 27 列（A=0..Z=25, AA=26）
        assert convert_position("AA1", 5, 30) == (26, 4)

    def test_round_trip(self) -> None:
        # convert_position ↔ tile_position_to_str 互逆（10 行 11 列地图）
        for pos in ["A1", "D2", "G10", "J7"]:
            col, row = convert_position(pos, 10, 11)
            assert tile_position_to_str(col, row, 10) == pos

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            convert_position("", 7, 10)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            convert_position("abc", 7, 10)

    def test_out_of_range_raises(self) -> None:
        # col 超出
        with pytest.raises(ValueError):
            convert_position("K1", 7, 10)
        # row 超出
        with pytest.raises(ValueError):
            convert_position("A8", 7, 10)


class TestTilePositionToStr:
    def test_basic(self) -> None:
        assert tile_position_to_str(0, 6, 7) == "A1"

    def test_top_right(self) -> None:
        # col=6, row=0 → G7（7行地图行号7）
        assert tile_position_to_str(6, 0, 7) == "G7"
