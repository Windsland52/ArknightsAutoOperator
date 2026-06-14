"""费用条 tick 检测。

移植自 reference/ArknightsCostBarRuler-master/ruler/utils.py 的三个函数：
- find_cost_bar_roi(width, height) -> (x1, x2, y)
- get_filled_pixel_width(frame, roi) -> int | None（向量化）
- get_logical_frame(frame, roi, pixel_map) -> int | None

输入 frame 为 maafw 截图返回的 BGR ndarray (H, W, 3) uint8。
白/灰度判定对通道顺序不敏感（|R-G|、|G-B|、全通道>阈值），故 BGR 直接处理无需转换。
"""

from __future__ import annotations

import numpy as np

from custom import config

Roi = tuple[int, int, int]  # (x1, x2, y)


def find_cost_bar_roi(width: int, height: int) -> Roi:
    """根据屏幕分辨率计算费用条 ROI。

    参考 1920x1080（config.REF_WIDTH/HEIGHT），按短边等比缩放。
    """
    ref_aspect = config.REF_WIDTH / config.REF_HEIGHT
    cur_aspect = width / height
    scale = height / config.REF_HEIGHT if cur_aspect >= ref_aspect else width / config.REF_WIDTH

    x1 = width - config.X1_OFFSET_FROM_RIGHT * scale
    x2 = width - config.X2_OFFSET_FROM_RIGHT * scale
    y1 = height - config.Y1_OFFSET_FROM_BOTTOM * scale
    y2 = height - config.Y2_OFFSET_FROM_BOTTOM * scale
    return (round(x1), round(x2), round((y1 + y2) / 2))


def get_filled_pixel_width(frame: np.ndarray, roi: Roi) -> int | None:
    """提取费用条填充像素宽。

    双模式（与 CostBarRuler 一致）：
    - 普通模式：白像素阈值 > WHITE_THRESHOLD(250)。
    - 遮罩模式（变暗）：> MASKED_WHITE_THRESHOLD(150) 且整体 <= MASKED_MAX_BRIGHTNESS(165)。
    ROI 行必须为灰度（GRAY_TOLERANCE 内）；末端像素非灰度则判定 ROI 无效返回 None。

    Returns:
        填充像素宽（0 表示空/未检出），或 None 表示 ROI 无效。
    """
    x1, x2, y = roi
    total = x2 - x1
    if total <= 0:
        return None
    h, w = frame.shape[:2]
    if not (0 <= y < h and 0 <= x1 and x2 <= w):
        return None

    row = frame[y, x1:x2].astype(np.int16)  # (total, 3) BGR
    c0, c1, c2 = row[:, 0], row[:, 1], row[:, 2]
    gray = (np.abs(c0 - c1) <= config.GRAY_TOLERANCE) & (np.abs(c1 - c2) <= config.GRAY_TOLERANCE)

    # 末端像素必须灰度，否则 ROI 无效。
    if not gray[-1]:
        return None

    # --- 普通模式 ---
    white = (
        (c0 > config.WHITE_THRESHOLD)
        & (c1 > config.WHITE_THRESHOLD)
        & (c2 > config.WHITE_THRESHOLD)
    )
    if white[-1]:
        return total
    non_end_white = np.where(white[:-1])[0]  # 右→左扫到第一个白边
    if non_end_white.size > 0:
        edge = int(non_end_white[-1])
        # edge 右侧（扫描经过的填充段）必须全灰度，否则 ROI 含杂色 → 无效。
        if gray[edge + 1 : -1].all():
            return edge + 1
        return None

    # --- 遮罩模式回退（filled == 0）---
    too_bright = (
        (c0 > config.MASKED_MAX_BRIGHTNESS)
        | (c1 > config.MASKED_MAX_BRIGHTNESS)
        | (c2 > config.MASKED_MAX_BRIGHTNESS)
    )
    if too_bright[-1]:
        return 0  # 末端过亮，不可能是遮罩模式。
    masked_white = (
        (c0 > config.MASKED_WHITE_THRESHOLD)
        & (c1 > config.MASKED_WHITE_THRESHOLD)
        & (c2 > config.MASKED_WHITE_THRESHOLD)
    )
    if masked_white[-1]:
        return total
    candidates = np.where(masked_white[:-1] & ~too_bright[:-1])[0]
    if candidates.size > 0:
        edge = int(candidates[-1])
        seg_gray = gray[edge + 1 : -1]
        seg_bright = too_bright[edge + 1 : -1]
        if seg_gray.all() and not seg_bright.any():
            return edge + 1
    return 0


def get_logical_frame(frame: np.ndarray, roi: Roi, pixel_map: dict[str, int]) -> int | None:
    """像素宽 → 逻辑帧（via 校准 pixel_map）。

    先直接命中，否则在 PIXEL_TOLERANCE(5) 内取最近。未命中返回 None。
    """
    pw = get_filled_pixel_width(frame, roi)
    if pw is None:
        return None
    key = str(pw)
    if key in pixel_map:
        return pixel_map[key]
    best_frame: int | None = None
    best_diff = config.PIXEL_TOLERANCE + 1
    for k, v in pixel_map.items():
        diff = abs(pw - int(k))
        if diff < best_diff:
            best_diff = diff
            best_frame = v
    return best_frame if best_diff <= config.PIXEL_TOLERANCE else None


def detect(frame: np.ndarray, pixel_map: dict[str, int] | None = None) -> tuple[Roi, int | None]:
    """便捷：算 ROI + 取填充宽（+ 可选逻辑帧）。

    Returns:
        (roi, logical_frame or None)；若 pixel_map 为 None 则第二项为填充像素宽。
    """
    h, w = frame.shape[:2]
    roi = find_cost_bar_roi(w, h)
    if pixel_map is None:
        return roi, get_filled_pixel_width(frame, roi)
    return roi, get_logical_frame(frame, roi, pixel_map)
