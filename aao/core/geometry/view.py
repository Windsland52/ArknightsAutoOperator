"""3D 投影：棋盘格子 (row, col) → 屏幕坐标 (x_ratio, y_ratio)。

移植自 reference/prts-plus/logic/calc_view.py（numpy 向量化）。
原理：透视投影矩阵 + 旋转（X轴30°/Y轴10°），把地图格子的 3D 坐标投影到 0-1 的屏幕比例。

front view（正面）：用于技能/撤退点击位置。
side view（侧面，多一个 Y 轴旋转）：用于部署拖拽落点。
"""

from __future__ import annotations

import math

import numpy as np

from aao.types import JsonObject

# 投影参数（prts-plus ViewCalculationConfig）
_FROM_RATIO = 9 / 16
_NEAR = 0.3
_FAR = 1000
_FOV_HALF_DEG = 20  # 半视场角
_ROT_X_DEG = 30  # X 轴旋转
_ROT_Y_DEG = 10  # Y 轴旋转（仅 side view）

_DEG = math.pi / 180


def _build_matrix(view_offset: list[float], side: bool) -> np.ndarray:
    """构建投影矩阵（view_offset = [x, y, z] 相机位置）。"""
    x, y, z = view_offset

    transform = np.array(
        [[1, 0, 0, -x], [0, 1, 0, -y], [0, 0, 1, -z], [0, 0, 0, 1]],
        dtype=np.float64,
    )

    perspective = np.array(
        [
            [_FROM_RATIO / math.tan(_FOV_HALF_DEG * _DEG), 0, 0, 0],
            [0, 1 / math.tan(_FOV_HALF_DEG * _DEG), 0, 0],
            [0, 0, -(_FAR + _NEAR) / (_FAR - _NEAR), -(_FAR * _NEAR * 2) / (_FAR - _NEAR)],
            [0, 0, -1, 0],
        ],
        dtype=np.float64,
    )

    rot_x = np.array(
        [
            [1, 0, 0, 0],
            [0, math.cos(_ROT_X_DEG * _DEG), -math.sin(_ROT_X_DEG * _DEG), 0],
            [0, -math.sin(_ROT_X_DEG * _DEG), -math.cos(_ROT_X_DEG * _DEG), 0],
            [0, 0, 0, 1],
        ],
        dtype=np.float64,
    )

    if side:
        rot_y = np.array(
            [
                [math.cos(_ROT_Y_DEG * _DEG), 0, math.sin(_ROT_Y_DEG * _DEG), 0],
                [0, 1, 0, 0],
                [-math.sin(_ROT_Y_DEG * _DEG), 0, math.cos(_ROT_Y_DEG * _DEG), 0],
                [0, 0, 0, 1],
            ],
            dtype=np.float64,
        )
        return perspective @ rot_x @ rot_y @ transform

    return perspective @ rot_x @ transform


def transform_map_to_view(
    level_data: JsonObject,
    side: bool = False,
) -> list[list[tuple[float, float]]]:
    """把关卡数据投影为屏幕坐标（0-1 比例）。

    Args:
        level_data: MAA Arknights-Tile-Pos JSON（含 height/width/view/tiles）。
        side: True=侧面视角（部署落点），False=正面视角（技能/撤退）。

    Returns:
        height×width 的二维列表，每个元素 (x_ratio, y_ratio)。
    """
    height = level_data["height"]
    width = level_data["width"]
    view_offset = level_data["view"][1 if side else 0]

    matrix = _build_matrix(view_offset, side)

    out: list[list[tuple[float, float]]] = []
    for i in range(height):
        row: list[tuple[float, float]] = []
        for j in range(width):
            tile = level_data["tiles"][i][j]
            map_point = np.array(
                [
                    j - (width - 1) / 2.0,
                    (height - 1) / 2.0 - i,
                    tile["heightType"] * -0.4,
                    1.0,
                ],
                dtype=np.float64,
            )
            view_point = matrix @ map_point
            view_point = view_point / view_point[3]
            view_point = (view_point + 1) / 2
            row.append((float(view_point[0]), 1 - float(view_point[1])))
        out.append(row)

    return out
