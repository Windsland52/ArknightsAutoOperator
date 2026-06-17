"""费用条校准：采集周期样本 → Jaccard 聚类 → pixel_map（像素宽 → 逻辑帧）。

移植自 reference/ArknightsCostBarRuler-master/ruler/calibration_manager.py。
产出 FullCalibrationData（含 1 个或多个 CalibrationProfile），供
``tick.get_logical_frame`` 与 ``time_source`` 使用。

校准需游戏处于慢速/子弹时间（选中干员），费用条缓慢循环；采集 ``num_cycles`` 个周期。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import numpy as np

from aao import config
from aao.core.timing import tick
from aao.utils.logger import logger

_CALIB_POLL_S = 0.01  # 校准采集轮询间隔（~100fps）


@dataclass
class CalibrationProfile:
    """单档校准：像素宽 → 逻辑帧 映射。"""

    total_frames: int
    pixel_map: dict[str, int]  # str(pixel_width) -> logical_frame


@dataclass
class FullCalibrationData:
    """完整校准数据（可含多档 profile，对应交替回费）。"""

    detection_mode: str  # "single" | "alternating"
    profiles: list[CalibrationProfile]
    screen_width: int
    screen_height: int
    calibration_time: float = 0.0


CaptureFn = Callable[[], np.ndarray | None]


def calibrate(
    capture_fn: CaptureFn,
    num_cycles: int = config.DEFAULT_NUM_CYCLES,
    progress_cb: Callable[[float], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> FullCalibrationData:
    """采集 ``num_cycles`` 个费用条周期样本，Jaccard 聚类建 profile。

    cancel_cb：可选，返回 True 时立即中止采集（抛 RuntimeError）。
    """
    if num_cycles <= 0:
        num_cycles = config.DEFAULT_NUM_CYCLES

    first = capture_fn()
    if first is None:
        raise RuntimeError("calibrate: 首帧采集失败，检查控制器/连接")
    h, w = first.shape[:2]
    roi = tick.find_cost_bar_roi(w, h)
    total_bar_width = roi[1] - roi[0]
    logger.info(
        "calibrate: %dx%d ROI=%s bar_width=%d 目标 %d 周期",
        w,
        h,
        roi,
        total_bar_width,
        num_cycles,
    )

    cycle_samples: list[list[int]] = []
    current: list[int] = []
    prev_pw: int | None = None
    collecting = False

    while len(cycle_samples) < num_cycles:
        if cancel_cb and cancel_cb():
            raise RuntimeError("calibrate: 用户取消")
        frame = capture_fn()
        if frame is None:
            time.sleep(_CALIB_POLL_S)
            prev_pw = None
            continue

        pw = tick.get_filled_pixel_width(frame, roi)
        if pw is None or pw < 0:
            prev_pw = None
            continue

        fill = pw / total_bar_width if total_bar_width > 0 else 0.0
        if progress_cb:
            progress_cb(min(1.0, (len(cycle_samples) + fill) / num_cycles))

        # 周期完成检测：高 → 低 跳变（>90% → <10%）
        if prev_pw is not None and total_bar_width > 0:
            if (
                prev_pw > total_bar_width * config.CYCLE_HIGH_THRESHOLD
                and pw < total_bar_width * config.CYCLE_LOW_THRESHOLD
            ):
                collecting = True
                if current:
                    cycle_samples.append(current)
                    logger.info(
                        "收集到周期 %d/%d (%d 采样点)",
                        len(cycle_samples),
                        num_cycles,
                        len(current),
                    )
                    current = []

        if collecting:
            current.append(pw)

        prev_pw = pw
        time.sleep(_CALIB_POLL_S)

    if not cycle_samples:
        raise RuntimeError("calibrate: 未收集到周期样本（确保游戏慢速 + 费用条可见）")

    clusters = _cluster(cycle_samples)
    profiles = [_build_profile(c, i + 1) for i, c in enumerate(clusters)]
    if not profiles:
        raise RuntimeError("calibrate: 未能构建任何 profile")

    mode = "alternating" if len(profiles) > 1 else "single"
    logger.info("calibrate: 完成，%d 档 profile (%s)", len(profiles), mode)
    return FullCalibrationData(
        detection_mode=mode,
        profiles=profiles,
        screen_width=w,
        screen_height=h,
        calibration_time=time.time(),
    )


def _cluster(samples: list[list[int]]) -> list[list[list[int]]]:
    """Jaccard 相似度聚类周期样本。"""
    clusters: list[list[list[int]]] = []
    for sample in samples:
        sample_set = set(sample)
        if not sample_set:
            continue
        best_idx, best_sim = -1, -1.0
        for i, cluster in enumerate(clusters):
            sim = _jaccard(sample_set, set(cluster[0]))
            if sim > best_sim:
                best_sim, best_idx = sim, i
        if best_sim >= config.SIMILARITY_THRESHOLD:
            clusters[best_idx].append(sample)
        else:
            clusters.append([sample])
    logger.info("聚类完成：%d 簇", len(clusters))
    return clusters


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _build_profile(cluster: list[list[int]], model_num: int) -> CalibrationProfile:
    """从一簇周期样本建 pixel_map（含隐藏辉光帧检测）。"""
    width_counts: dict[int, int] = {}
    for sample in cluster:
        for width in sample:
            width_counts[width] = width_counts.get(width, 0) + 1

    # 隐藏辉光帧检测：零宽度的采样数 ÷ 基准频率 → 空状态帧数
    count_zero = width_counts.get(0, 0)
    non_zero_counts = [c for w, c in width_counts.items() if w > 0]
    num_hidden = 0
    if non_zero_counts:
        med = median(non_zero_counts)
        outlier_thr = med * config.OUTLIER_MULTIPLIER
        filtered = [c for c in non_zero_counts if c < outlier_thr]
        if filtered:
            baseline = median(filtered)
            logger.info("profile%d: 基准频率 ≈ %.1f 采样/帧", model_num, baseline)
            if baseline > 0:
                num_hidden = max(0, round(count_zero / baseline) - 1)
                if num_hidden:
                    logger.warning("profile%d: 检测到 %d 个隐藏辉光帧", model_num, num_hidden)

    unique_widths = sorted(width_counts)
    total_frames = len(unique_widths) + num_hidden

    pixel_map: dict[str, int] = {}
    if 0 in width_counts:
        pixel_map["0"] = 0
    frame_offset = 1 + num_hidden
    nonzero_widths = [w for w in unique_widths if w > 0]
    for idx, width in enumerate(nonzero_widths):
        pixel_map[str(width)] = idx + frame_offset

    logger.info(
        "profile%d: total_frames=%d 映射 %d 个像素宽",
        model_num,
        total_frames,
        len(pixel_map),
    )
    return CalibrationProfile(total_frames=total_frames, pixel_map=pixel_map)


# --- 文件 IO ---


def calibration_dir() -> Path:
    from aao.utils.runtime_paths import project_root

    d = project_root() / "config" / "calibration"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(data: FullCalibrationData, basename: str) -> str:
    """保存校准数据，文件名含帧数+分辨率。返回文件名（不含目录）。"""
    parts = [str(p.total_frames) for p in data.profiles]
    frame_str = "-".join(parts) + "f" if parts else "0f"
    filename = f"{basename}_{frame_str}_{data.screen_width}x{data.screen_height}.json"
    path = calibration_dir() / filename
    payload = {
        "detection_mode": data.detection_mode,
        "profiles": [asdict(p) for p in data.profiles],
        "screen_width": data.screen_width,
        "screen_height": data.screen_height,
        "calibration_time": data.calibration_time,
    }
    path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
    logger.info("校准已保存: %s", path)
    return filename


def load(filename: str) -> FullCalibrationData:
    path = calibration_dir() / filename
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles = [
        CalibrationProfile(total_frames=p["total_frames"], pixel_map=p["pixel_map"])
        for p in raw["profiles"]
    ]
    return FullCalibrationData(
        detection_mode=raw["detection_mode"],
        profiles=profiles,
        screen_width=raw["screen_width"],
        screen_height=raw["screen_height"],
        calibration_time=raw.get("calibration_time", 0.0),
    )


def list_files() -> list[str]:
    return sorted(p.name for p in calibration_dir().glob("*.json"))


def delete(filename: str) -> bool:
    path = calibration_dir() / filename
    if path.exists():
        path.unlink()
        return True
    return False
