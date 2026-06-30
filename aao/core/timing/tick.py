"""费用条 tick 检测。

移植自 reference/ArknightsCostBarRuler-master/ruler/utils.py 的三个函数：
- find_cost_bar_roi(width, height) -> (x1, x2, y)
- get_filled_pixel_width(frame, roi) -> int | None（向量化）
- get_logical_frame(frame, roi, pixel_map) -> int | None

输入 frame 为 maafw 截图返回的 BGR ndarray (H, W, 3) uint8。
白/灰度判定对通道顺序不敏感（|R-G|、|G-B|、全通道>阈值），故 BGR 直接处理无需转换。
"""

from __future__ import annotations

import bisect
from typing import cast

import numpy as np

from aao import config

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


_GRAY_EXT_MIN = 35  # 灰色扩展最小白色填充（低于此值不扩展，避免遮罩灰色背景误判）


def _contiguous_fill_width(white: np.ndarray, valid: np.ndarray) -> int | None:
    """左→右扫描：从 x1 起的连续白像素数。无桥接（避免部署 UI 杂散白误判）。"""
    total = len(white)
    if not white[0]:
        return 0
    not_white = np.where(~white)[0]
    not_white_after = not_white[not_white > 0]
    if not_white_after.size == 0:
        return total
    edge = int(not_white_after[0])
    if valid[edge:].all():
        return edge
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
        r = _contiguous_fill_width(white, gray)
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
            r = _contiguous_fill_width(mw, gray & ~too_bright)
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


def _internal_to_display(pw: int, internal: int) -> int:
    """internal frame → display frame（open_interior 语义）。

    pixel_map value 是 internal frame（不含周期起点）。display = internal + 1，
    但 width=0 是周期起点端点，display=0（对应 Rust open_interior_endpoint_frame）。
    """
    return 0 if pw == 0 else internal + 1


def get_logical_frame(frame: np.ndarray, roi: Roi, pixel_map: dict[str, int]) -> int | None:
    """像素宽 → 逻辑帧（via 校准 pixel_map）。

    pixel_map value 是 internal frame，返回 display frame = internal + 1
    （width=0 端点返回 0）。对应 Rust open_interior_internal_frame。

    先直接命中，否则在 PIXEL_TOLERANCE(5) 内取最近。未命中返回 None。
    """
    pw = get_filled_pixel_width(frame, roi)
    if pw is None:
        return None
    key = str(pw)
    if key in pixel_map:
        return _internal_to_display(pw, pixel_map[key])
    best_frame: int | None = None
    best_diff = config.PIXEL_TOLERANCE + 1
    for k, v in pixel_map.items():
        diff = abs(pw - int(k))
        if diff < best_diff:
            best_diff = diff
            best_frame = v
    if best_frame is None or best_diff > config.PIXEL_TOLERANCE:
        return None
    return _internal_to_display(pw, best_frame)


def get_logical_frame_f64(frame: np.ndarray, roi: Roi, pixel_map: dict[str, int]) -> float | None:
    """像素宽 → 逻辑帧（浮点插值）。

    与 ``get_logical_frame`` 的离散最近邻不同，本函数在两个校准点之间线性插值，
    保留亚帧精度。用于负费相位重投射（pixel_map 按正常速率校准，负费时回费
    减半，需亚帧精度做相位映射）。

    pixel_map value 是 internal frame。插值在 internal 上做，返回 display = internal + 1
    （width=0 端点返回 0.0）。对应 Rust lookup_interpolated_frame +
    open_interior_internal_frame_f64。

    Returns:
        逻辑帧（浮点），或 None 表示 ROI 无效 / 未命中。
    """
    pw = get_filled_pixel_width(frame, roi)
    if pw is None:
        return None

    # width=0 端点修正（对应 Rust open_interior_endpoint_frame）。
    if pw == 0 and "0" in pixel_map:
        return 0.0

    # 排序条目（pixel_width 升序；internal frame 随 width 单调递增）。
    entries = sorted((int(k), v) for k, v in pixel_map.items())
    if not entries:
        return None

    widths = [e[0] for e in entries]
    pos = bisect.bisect_left(widths, pw)

    # 精确命中：internal → display = internal + 1
    if pos < len(entries) and widths[pos] == pw:
        return float(entries[pos][1] + 1)

    # 左边界：仅在容差内返回端点
    if pos == 0:
        w, f = entries[0]
        if abs(w - pw) <= config.PIXEL_TOLERANCE:
            return float(_internal_to_display(w, f))
        return None

    # 右边界：仅在容差内返回端点
    if pos >= len(entries):
        w, f = entries[-1]
        if abs(w - pw) <= config.PIXEL_TOLERANCE:
            return float(_internal_to_display(w, f))
        return None

    # 两点之间线性插值（在 internal 上插值，再 +1 得 display）
    lw, lf = entries[pos - 1]
    uw, uf = entries[pos]
    if lw == uw:
        return float(lf + 1)
    ratio = (pw - lw) / (uw - lw)
    internal = lf + ratio * (uf - lf)
    return internal + 1.0


def detect_negative_cost(frame: np.ndarray) -> bool:
    """检测费用是否为负（可露希尔负费）。

    减号是费用数字前的一段集中横向纯白 run（实测 ≈19px）；正数字笔画不会在
    这条窄横条 ROI 内产生同等长度的连续纯白 run。在 ``COST_SIGN_ROI`` 内逐行扫描，
    任一行的最长连续纯白 run ≥ ``COST_SIGN_MIN_RUN`` 即判定为负费。

    maafw 截图统一缩放到 1280x720，故 ROI 用固定坐标。

    Args:
        frame: BGR ndarray (H, W, 3) uint8。

    Returns:
        True 表示检测到减号（负费）。
    """
    x, y, w, h = config.COST_SIGN_ROI
    fh, fw = frame.shape[:2]
    if x < 0 or y < 0 or x + w > fw or y + h > fh:
        return False

    region = frame[y : y + h, x : x + w].astype(np.int16)  # (h, w, 3) BGR
    white = (region > config.WHITE_THRESHOLD).all(axis=2)  # (h, w) bool
    white_rows = cast(np.ndarray, white)

    # 逐行求最长连续纯白 run：对每行做行内累计，遇非白清零，取全局最大。
    for row_index in range(int(white_rows.shape[0])):
        row = cast(np.ndarray, white_rows[row_index])
        if not row.any():
            continue
        # 累计连续 True 长度：cumsum 在非白处“断点”归零的经典向量化写法。
        idx = np.arange(len(row))
        # 每个位置减去“上一个非白位置”，得到当前连续白的长度。
        last_false = np.where(row, 0, idx)
        max_run = int((idx - np.maximum.accumulate(last_false)).max())
        if max_run >= config.COST_SIGN_MIN_RUN:
            return True
    return False


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
