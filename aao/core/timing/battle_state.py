"""战斗状态检测（移植自 Rust battle_state.rs）。

检测右上角暂停/播放 + 速度按钮 glyph，分类为：
- IN_BATTLE：pause glyph 有效（暂停/播放），speed glyph 区分 1x/2x/0.2x
- BATTLE_BEGIN：两 glyph 全暗 + 标题屏 band sampling 通过
- NOT_IN_BATTLE：其余

maafw 截图统一缩放到 1280x720，故 ROI 用固定坐标。
对应 Rust crates/ruler-core/src/analysis/scanner/battle_state.rs。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

# --- Reference coordinates (1280x720) ---
_REF_WIDTH = 1280.0
_REF_HEIGHT = 720.0
_REF_ASPECT = _REF_WIDTH / _REF_HEIGHT

# 速度按钮 glyph ROI（距右边缘偏移，参考 1280x720）
_SPEED_GLYPH_LEFT_FROM_RIGHT = 207.0
_SPEED_GLYPH_RIGHT_FROM_RIGHT = 153.0
_SPEED_GLYPH_TOP = 28.0
_SPEED_GLYPH_BOTTOM = 79.0

# 暂停/播放按钮 glyph ROI（距右边缘偏移，参考 1280x720）
_PAUSE_GLYPH_LEFT_FROM_RIGHT = 92.0
_PAUSE_GLYPH_RIGHT_FROM_RIGHT = 50.0
_PAUSE_GLYPH_TOP = 38.0
_PAUSE_GLYPH_BOTTOM = 69.0

# --- Glyph thresholds (1:1 from battle_state.rs) ---
GLYPH_BRIGHT_THRESHOLD = 180  # per-channel；brightness = B+G+R >= 540
GLYPH_DIM_THRESHOLD = 120  # per-channel；brightness = B+G+R >= 360

# 归一化亮/暗像素面积（÷ scale²）。播放 glyph（两根竖条）比暂停 glyph（三角）多。
PAUSE_RUNNING_MIN = 560.0
PAUSE_RUNNING_MAX = 710.0
PAUSE_PAUSED_MIN = 300.0  # 实测暂停态 bright_norm=340 < Rust 原版 380
PAUSE_PAUSED_MAX = 500.0

# Before/after battle: both glyphs dark.
GLYPH_PRESENT_MAX = 150.0

# --- BattleBegin title-screen thresholds (1:1 from battle_state.rs) ---
BATTLE_BEGIN_TOP_RIGHT_DIM_MAX = 2.0
BATTLE_BEGIN_WHITE_MIN = 210
BATTLE_BEGIN_WHITE_TOLERANCE = 45
BATTLE_BEGIN_DARK_MAX_SUM = 150  # R+G+B <= 此值 → dark
BATTLE_BEGIN_SAMPLE_STEP_SCALE = 14.0
BATTLE_BEGIN_SIDE_AVG_MAX = 120
BATTLE_BEGIN_TOP_WHITE_MAX = 0
BATTLE_BEGIN_TOP_COLOR_DELTA_MAX = 4
BATTLE_BEGIN_SIDE_COLOR_DELTA_MAX = 4
BATTLE_BEGIN_CODE_WHITE_PERMYRIAD_MIN = 50
BATTLE_BEGIN_TITLE_WHITE_PERMYRIAD_MIN = 150
BATTLE_BEGIN_TEXT_SCORE_PERMYRIAD_MIN = 650


class BattleState(Enum):
    """战斗状态（对应 Rust BattleState）。"""

    IN_BATTLE = "in_battle"
    BATTLE_BEGIN = "battle_begin"
    NOT_IN_BATTLE = "not_in_battle"

    @property
    def is_in_battle(self) -> bool:
        return self is BattleState.IN_BATTLE


# --- Scale & rect helpers ---


def _battle_button_scale(width: int, height: int) -> float:
    """1280x720 参考的比例缩放（ui_scaler=1.0 → edge_scale=1.0）。"""
    aspect = width / height
    if aspect >= _REF_ASPECT:
        return height / _REF_HEIGHT
    return width / _REF_WIDTH


def _glyph_rect_from_right(
    width: int,
    height: int,
    scale: float,
    left_from_right: float,
    right_from_right: float,
    top: float,
    bottom: float,
) -> tuple[int, int, int, int]:
    """计算 glyph ROI（距右边缘偏移）→ (left, right, top, bottom)。"""
    left = round(width - left_from_right * scale)
    right = round(width - right_from_right * scale)
    top = round(top * scale)
    bottom = round(bottom * scale)
    return (
        max(0, min(left, width)),
        max(0, min(right, width)),
        max(0, min(top, height)),
        max(0, min(bottom, height)),
    )


def _pause_glyph_rect(width: int, height: int, scale: float) -> tuple[int, int, int, int]:
    return _glyph_rect_from_right(
        width,
        height,
        scale,
        _PAUSE_GLYPH_LEFT_FROM_RIGHT,
        _PAUSE_GLYPH_RIGHT_FROM_RIGHT,
        _PAUSE_GLYPH_TOP,
        _PAUSE_GLYPH_BOTTOM,
    )


def _speed_glyph_rect(width: int, height: int, scale: float) -> tuple[int, int, int, int]:
    return _glyph_rect_from_right(
        width,
        height,
        scale,
        _SPEED_GLYPH_LEFT_FROM_RIGHT,
        _SPEED_GLYPH_RIGHT_FROM_RIGHT,
        _SPEED_GLYPH_TOP,
        _SPEED_GLYPH_BOTTOM,
    )


# --- Pixel counting (numpy vectorized) ---


def _count_at_thresholds(region: np.ndarray, low: int, high: int, step: int = 1) -> tuple[int, int]:
    """统计 region 内 dim(>=low*3) 和 bright(>=high*3) 像素数（带 step 采样）。

    Returns:
        (dim_count, bright_count) 未归一化。
    """
    if region.size == 0 or step <= 0:
        return 0, 0
    sampled = region[::step, ::step]
    brightness = sampled.astype(np.int16).sum(axis=2)  # (h', w')
    low_sum = low * 3
    high_sum = high * 3
    dim_count = int((brightness >= low_sum).sum())
    bright_count = int((brightness >= high_sum).sum())
    # 采样放大：每个采样点代表 step² 个像素
    dim_count *= step * step
    bright_count *= step * step
    return dim_count, bright_count


def _normalized_count(count: int, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return count / (scale * scale)


# --- Classification ---


def _classify_pause(bright_norm: float) -> bool:
    """归一化亮像素面积 → 是否为暂停/播放 glyph（in-battle 信号）。"""
    return (PAUSE_PAUSED_MIN <= bright_norm <= PAUSE_PAUSED_MAX) or (
        PAUSE_RUNNING_MIN <= bright_norm <= PAUSE_RUNNING_MAX
    )


# --- BattleBegin band sampling ---


@dataclass
class _BandStats:
    """单 band 统计（对应 Rust BattleBeginBandStats）。"""

    total: int = 0
    white: int = 0
    dark: int = 0
    brightness_sum: int = 0
    channel_delta_sum: int = 0

    def white_permyriad(self) -> int:
        if self.total == 0:
            return 0
        return self.white * 10000 // self.total

    def avg_brightness(self) -> int:
        if self.total == 0:
            return 999999
        return self.brightness_sum // (self.total * 3)

    def avg_channel_delta(self) -> int:
        if self.total == 0:
            return 999999
        return self.channel_delta_sum // self.total

    def is_dim_backdrop(self) -> bool:
        return (
            self.total > 0
            and self.avg_brightness() <= BATTLE_BEGIN_SIDE_AVG_MAX
            and self.avg_channel_delta() <= BATTLE_BEGIN_SIDE_COLOR_DELTA_MAX
        )


def _sample_band(
    frame: np.ndarray,
    width: int,
    height: int,
    left_ratio: float,
    right_ratio: float,
    top_ratio: float,
    bottom_ratio: float,
    x_step: int,
    y_step: int,
) -> _BandStats:
    """采样一个矩形 band，返回统计（对应 Rust sample_battle_begin_band）。

    注意：frame 是 BGR (H, W, 3)，y=0 在顶部。Rust buffer y=0 在底部（已翻转）。
    """
    if width == 0 or height == 0 or x_step <= 0 or y_step <= 0:
        return _BandStats()

    left = max(0, min(round(width * left_ratio), width))
    right = max(0, min(round(width * right_ratio), width))
    top = max(0, min(round(height * top_ratio), height))
    bottom = max(0, min(round(height * bottom_ratio), height))
    if left >= right or top >= bottom:
        return _BandStats()

    region = frame[top:bottom, left:right]
    # 采样
    sampled = region[::y_step, ::x_step]
    # BGR → R,G,B
    b_ch = sampled[:, :, 0].astype(np.int32)
    g_ch = sampled[:, :, 1].astype(np.int32)
    r_ch = sampled[:, :, 2].astype(np.int32)

    brightness = r_ch + g_ch + b_ch
    total = sampled.shape[0] * sampled.shape[1]
    bright_sum = int(brightness.sum())
    # channel_delta per pixel
    delta = np.maximum(np.maximum(r_ch, g_ch), b_ch) - np.minimum(np.minimum(r_ch, g_ch), b_ch)
    delta_sum = int(delta.sum())

    dark = int((brightness <= BATTLE_BEGIN_DARK_MAX_SUM).sum())
    white = int(
        (
            (r_ch >= BATTLE_BEGIN_WHITE_MIN)
            & (g_ch >= BATTLE_BEGIN_WHITE_MIN)
            & (b_ch >= BATTLE_BEGIN_WHITE_MIN)
            & (delta <= BATTLE_BEGIN_WHITE_TOLERANCE)
        ).sum()
    )

    return _BandStats(
        total=total,
        white=white,
        dark=dark,
        brightness_sum=bright_sum,
        channel_delta_sum=delta_sum,
    )


def _has_battle_begin_title_screen(
    frame: np.ndarray,
    width: int,
    height: int,
    scale: float,
) -> bool:
    """检测 BattleBegin 标题屏（对应 Rust has_battle_begin_title_screen）。

    7 个 band 采样：
    1. top_clear: 顶部全黑
    2. left_background: 左侧暗背景
    3. right_background: 右侧暗背景
    4. operation: "作战"二字
    5. code: 关卡代号（white >= 1%）
    6. title: 关卡标题（white >= 1.5%）
    7. bottom: 底部文字
    最后 4+5+6+7 的 white_permyriad 总和 >= 650。
    """
    step = max(4, round(scale * BATTLE_BEGIN_SAMPLE_STEP_SCALE))
    top_y_step = max(step, round(height * 0.10))

    # 1. 顶部必须全黑
    top_clear = _sample_band(frame, width, height, 0.20, 0.80, 0.03, 0.34, step * 2, top_y_step)
    if top_clear.white > BATTLE_BEGIN_TOP_WHITE_MAX:
        return False
    if top_clear.avg_channel_delta() > BATTLE_BEGIN_TOP_COLOR_DELTA_MAX:
        return False

    # 2. 左侧暗背景
    left_bg = _sample_band(frame, width, height, 0.03, 0.18, 0.40, 0.80, step, step)
    if not left_bg.is_dim_backdrop():
        return False

    # 3. 右侧暗背景
    right_bg = _sample_band(frame, width, height, 0.82, 0.97, 0.40, 0.80, step, step)
    if not right_bg.is_dim_backdrop():
        return False

    # 4-7. 文字区域
    operation = _sample_band(frame, width, height, 0.20, 0.80, 0.38, 0.47, step, step)
    # code band 去掉单独检查：实际标题屏关卡代号可能很细/小，
    # white_permyriad 波动大。title + score 已足够区分标题屏 vs 结算/部署。
    code = _sample_band(frame, width, height, 0.25, 0.75, 0.46, 0.58, step, step)
    title = _sample_band(frame, width, height, 0.20, 0.80, 0.56, 0.72, step, step)
    if title.white_permyriad() < BATTLE_BEGIN_TITLE_WHITE_PERMYRIAD_MIN:
        return False
    bottom = _sample_band(frame, width, height, 0.20, 0.80, 0.84, 0.99, step, step)

    text_score = (
        operation.white_permyriad()
        + code.white_permyriad()
        + title.white_permyriad()
        + bottom.white_permyriad()
    )
    return text_score >= BATTLE_BEGIN_TEXT_SCORE_PERMYRIAD_MIN


# --- Main entry ---


def detect_battle_state(frame: np.ndarray) -> BattleState:
    """检测当前帧的战斗状态。

    三段逻辑（对应 Rust detect_battle_state_with_ui_scaler）：
    1. pause glyph 有效 → IN_BATTLE
    2. 两 glyph 全暗 + dim<=2 + 标题屏 → BATTLE_BEGIN
    3. 其余 → NOT_IN_BATTLE

    Args:
        frame: BGR ndarray (H, W, 3) uint8。

    Returns:
        BattleState.IN_BATTLE / BATTLE_BEGIN / NOT_IN_BATTLE。
    """
    h, w = frame.shape[:2]
    if w == 0 or h == 0:
        return BattleState.NOT_IN_BATTLE

    scale = _battle_button_scale(w, h)
    count_step = 2 if scale >= 1.5 else 1

    speed_rect = _speed_glyph_rect(w, h, scale)
    pause_rect = _pause_glyph_rect(w, h, scale)

    speed_region = frame[speed_rect[2] : speed_rect[3], speed_rect[0] : speed_rect[1]]
    pause_region = frame[pause_rect[2] : pause_rect[3], pause_rect[0] : pause_rect[1]]

    speed_dim, speed_bright = _count_at_thresholds(
        speed_region, GLYPH_DIM_THRESHOLD, GLYPH_BRIGHT_THRESHOLD, count_step
    )
    pause_dim, pause_bright = _count_at_thresholds(
        pause_region, GLYPH_DIM_THRESHOLD, GLYPH_BRIGHT_THRESHOLD, count_step
    )

    speed_bright_norm = _normalized_count(speed_bright, scale)
    speed_dim_norm = _normalized_count(speed_dim, scale)
    pause_bright_norm = _normalized_count(pause_bright, scale)
    pause_dim_norm = _normalized_count(pause_dim, scale)

    # 1. pause glyph 有效 → IN_BATTLE
    if _classify_pause(pause_bright_norm):
        return BattleState.IN_BATTLE

    # 2. 两 glyph 全暗 + dim<=2 + 标题屏 → BATTLE_BEGIN
    glyphs_dark = speed_bright_norm < GLYPH_PRESENT_MAX and pause_bright_norm < GLYPH_PRESENT_MAX
    if (
        glyphs_dark
        and speed_dim_norm <= BATTLE_BEGIN_TOP_RIGHT_DIM_MAX
        and pause_dim_norm <= BATTLE_BEGIN_TOP_RIGHT_DIM_MAX
        and _has_battle_begin_title_screen(frame, w, h, scale)
    ):
        return BattleState.BATTLE_BEGIN

    # 3. 其余 → NOT_IN_BATTLE
    # （BeforeOrAfterBattle 未移植 takeover overlay 检测，归入 NOT_IN_BATTLE）
    return BattleState.NOT_IN_BATTLE
    h, w = frame.shape[:2]
    if w == 0 or h == 0:
        return {"error": "empty frame"}

    scale = _battle_button_scale(w, h)
    count_step = 2 if scale >= 1.5 else 1

    speed_rect = _speed_glyph_rect(w, h, scale)
    pause_rect = _pause_glyph_rect(w, h, scale)

    speed_region = frame[speed_rect[2] : speed_rect[3], speed_rect[0] : speed_rect[1]]
    pause_region = frame[pause_rect[2] : pause_rect[3], pause_rect[0] : pause_rect[1]]

    speed_dim, speed_bright = _count_at_thresholds(
        speed_region, GLYPH_DIM_THRESHOLD, GLYPH_BRIGHT_THRESHOLD, count_step
    )
    pause_dim, pause_bright = _count_at_thresholds(
        pause_region, GLYPH_DIM_THRESHOLD, GLYPH_BRIGHT_THRESHOLD, count_step
    )

    speed_bright_norm = _normalized_count(speed_bright, scale)
    speed_dim_norm = _normalized_count(speed_dim, scale)
    pause_bright_norm = _normalized_count(pause_bright, scale)
    pause_dim_norm = _normalized_count(pause_dim, scale)

    is_in_battle = _classify_pause(pause_bright_norm)
    glyphs_dark = speed_bright_norm < GLYPH_PRESENT_MAX and pause_bright_norm < GLYPH_PRESENT_MAX
    dim_ok = (
        speed_dim_norm <= BATTLE_BEGIN_TOP_RIGHT_DIM_MAX
        and pause_dim_norm <= BATTLE_BEGIN_TOP_RIGHT_DIM_MAX
    )

    # band sampling（只在前置条件接近满足时才有意义）
    band_result = None
    if glyphs_dark and dim_ok:
        band_result = _diagnose_battle_begin_bands(frame, w, h, scale)

    return {
        "scale": round(scale, 3),
        "count_step": count_step,
        "speed_bright_norm": round(speed_bright_norm, 1),
        "speed_dim_norm": round(speed_dim_norm, 1),
        "pause_bright_norm": round(pause_bright_norm, 1),
        "pause_dim_norm": round(pause_dim_norm, 1),
        "is_in_battle": is_in_battle,
        "glyphs_dark": glyphs_dark,
        "glyphs_dark_threshold": GLYPH_PRESENT_MAX,
        "dim_ok": dim_ok,
        "dim_threshold": BATTLE_BEGIN_TOP_RIGHT_DIM_MAX,
        "band_result": band_result,
        # 失败原因
        "fail_reason": (
            "in_battle"
            if is_in_battle
            else "glyphs_not_dark"
            if not glyphs_dark
            else "dim_too_high"
            if not dim_ok
            else "band_sampling_failed"
            if band_result and not band_result.get("passed")
            else None
        ),
    }


def _diagnose_battle_begin_bands(frame: np.ndarray, width: int, height: int, scale: float) -> dict:
    """诊断 7 band sampling 各 band 的值。"""
    step = max(4, round(scale * BATTLE_BEGIN_SAMPLE_STEP_SCALE))
    top_y_step = max(step, round(height * 0.10))

    top_clear = _sample_band(frame, width, height, 0.20, 0.80, 0.03, 0.34, step * 2, top_y_step)
    left_bg = _sample_band(frame, width, height, 0.03, 0.18, 0.40, 0.80, step, step)
    right_bg = _sample_band(frame, width, height, 0.82, 0.97, 0.40, 0.80, step, step)
    operation = _sample_band(frame, width, height, 0.20, 0.80, 0.38, 0.47, step, step)
    code = _sample_band(frame, width, height, 0.25, 0.75, 0.46, 0.58, step, step)
    title = _sample_band(frame, width, height, 0.20, 0.80, 0.56, 0.72, step, step)
    bottom = _sample_band(frame, width, height, 0.20, 0.80, 0.84, 0.99, step, step)

    text_score = (
        operation.white_permyriad()
        + code.white_permyriad()
        + title.white_permyriad()
        + bottom.white_permyriad()
    )

    top_ok = (
        top_clear.white <= BATTLE_BEGIN_TOP_WHITE_MAX
        and top_clear.avg_channel_delta() <= BATTLE_BEGIN_TOP_COLOR_DELTA_MAX
    )
    left_ok = left_bg.is_dim_backdrop()
    right_ok = right_bg.is_dim_backdrop()
    code_ok = code.white_permyriad() >= BATTLE_BEGIN_CODE_WHITE_PERMYRIAD_MIN
    title_ok = title.white_permyriad() >= BATTLE_BEGIN_TITLE_WHITE_PERMYRIAD_MIN
    score_ok = text_score >= BATTLE_BEGIN_TEXT_SCORE_PERMYRIAD_MIN

    return {
        "passed": (top_ok and left_ok and right_ok and code_ok and title_ok and score_ok),
        "top_clear": {
            "white": top_clear.white,
            "avg_ch_delta": top_clear.avg_channel_delta(),
            "ok": top_ok,
        },
        "left_bg": {
            "avg_brightness": left_bg.avg_brightness(),
            "avg_ch_delta": left_bg.avg_channel_delta(),
            "ok": left_ok,
        },
        "right_bg": {
            "avg_brightness": right_bg.avg_brightness(),
            "avg_ch_delta": right_bg.avg_channel_delta(),
            "ok": right_ok,
        },
        "code": {
            "white_permyriad": code.white_permyriad(),
            "min": BATTLE_BEGIN_CODE_WHITE_PERMYRIAD_MIN,
            "ok": code_ok,
        },
        "title": {
            "white_permyriad": title.white_permyriad(),
            "min": BATTLE_BEGIN_TITLE_WHITE_PERMYRIAD_MIN,
            "ok": title_ok,
        },
        "text_score": text_score,
        "text_score_min": BATTLE_BEGIN_TEXT_SCORE_PERMYRIAD_MIN,
        "score_ok": score_ok,
    }
