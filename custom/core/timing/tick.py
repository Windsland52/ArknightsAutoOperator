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


_BRIDGE_MAX_GAP = 25  # 最大可桥接暗间隙（部署 UI 遮挡宽度，px）
_BRIDGE_MIN_RUN = 3  # 间隙后至少 N 连续白才认为遮挡（过滤杂散单像素）
_GRAY_EXT_MIN = 35  # 灰色扩展最小白色填充（低于此值不扩展，避免遮罩灰色背景误判）


def _bridged_fill_width(white: np.ndarray, valid: np.ndarray) -> int | None:
    """左→右扫描，遇短暗间隙且后方有 ≥ _BRIDGE_MIN_RUN 连续白时桥接（视为遮挡，跳过继续）。

    干净费用条（无间隙）行为与普通左→右一致；用于穿透部署拖拽时部署 UI 的固定遮挡。
    """
    total = len(white)
    pos = 0
    while pos < total:
        if white[pos]:
            pos += 1
            continue
        # pos 处暗；看前方 _BRIDGE_MAX_GAP 内是否有白
        ahead = pos + 1
        limit = min(pos + _BRIDGE_MAX_GAP, total)
        while ahead < limit and not white[ahead]:
            ahead += 1
        if ahead >= limit:
            break  # 间隙外无白 → 真实未填充边
        # 检查 ahead 起的连续白长度
        run = 0
        j = ahead
        while j < total and white[j]:
            run += 1
            j += 1
        if run >= _BRIDGE_MIN_RUN:
            pos = ahead + run  # 桥接：跳过间隙 + 后续白段
        else:
            break  # 杂散单像素 → 真实边
    if pos >= total:
        return total
    if valid[pos:].all():
        return pos
    return None


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

    # --- 普通模式（左→右 + 间隙桥接，穿透部署 UI 遮挡）---
    white = (
        (c0 > config.WHITE_THRESHOLD)
        & (c1 > config.WHITE_THRESHOLD)
        & (c2 > config.WHITE_THRESHOLD)
    )
    result = 0
    if white[0]:
        r = _bridged_fill_width(white, gray)
        if r is None:
            return None
        result = r

    # --- 遮罩模式回退（x1 非普通白）---
    if result == 0:
        too_bright = (
            (c0 > config.MASKED_MAX_BRIGHTNESS)
            | (c1 > config.MASKED_MAX_BRIGHTNESS)
            | (c2 > config.MASKED_MAX_BRIGHTNESS)
        )
        if too_bright[-1]:
            return 0
        mw = (
            (c0 > config.MASKED_WHITE_THRESHOLD)
            & (c1 > config.MASKED_WHITE_THRESHOLD)
            & (c2 > config.MASKED_WHITE_THRESHOLD)
            & ~too_bright
        )
        if mw[0]:
            r = _bridged_fill_width(mw, gray & ~too_bright)
            if r is not None:
                result = r

    # --- 灰色扩展（部署遮罩外费用条是灰色 ~155，继续扫 >150）---
    # 只在白色填充接近遮罩边缘时才扩展（避免低填充时把遮罩灰色背景误认为填充）
    if result >= _GRAY_EXT_MIN and result < total:
        ext = (
            (c0 > config.MASKED_WHITE_THRESHOLD)
            & (c1 > config.MASKED_WHITE_THRESHOLD)
            & (c2 > config.MASKED_WHITE_THRESHOLD)
        )
        pos = result
        while pos < total and ext[pos]:
            pos += 1
        result = pos

    return result


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
