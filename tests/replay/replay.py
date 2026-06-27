"""录制回放回归工具：帧序列 PNG → TimeSource → 比对预期帧。

用于回归费用条计时逻辑（边界周期修正、负费等）——改 ``time_source``/``tick``
后，拿一段录制跑一遍，核对每帧的全局计时器读数是否与预期一致。

不引入视频解码依赖：素材用**帧序列 PNG**（与 maafw 截图同格式 BGR）。
视频→PNG 用系统 ffmpeg 抽帧（见 ``cases/README.md``）。

用例目录结构（见 ``cases/``）::

    cases/<case_name>/
        frames/           # 逐帧 PNG，命名 000001.png ...（字典序 = 时间序）
        calibration.json  # 对应校准 profile（config/calibration 同格式）
        expected.json     # {"frames": [全局帧, ...]} 每帧预期 total_elapsed_frames
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from aao.core.timing.calibration import FullCalibrationData, from_dict
from aao.core.timing.time_source import TimeSource

CASES_DIR = Path(__file__).parent / "cases"


def read_frame_bgr(path: Path) -> np.ndarray:
    """读 PNG → BGR (H, W, 3) uint8（与 maafw 截图格式一致）。"""
    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
    return rgb[:, :, ::-1].copy()  # RGB → BGR


def load_case(
    case_dir: Path,
) -> tuple[list[np.ndarray], FullCalibrationData, list[int], list[int]]:
    """加载一个回放用例：帧序列 + 校准 + 预期全局帧 + 预期周期内帧（可选）。"""
    frames_dir = case_dir / "frames"
    frames = [
        read_frame_bgr(p)
        for p in sorted(frames_dir.glob("*"))
        if p.suffix.lower() in (".png", ".bmp")
    ]
    if not frames:
        raise FileNotFoundError(f"{case_dir}/frames 下无帧文件")

    calib_raw = json.loads((case_dir / "calibration.json").read_text(encoding="utf-8"))
    calibration = from_dict(calib_raw)

    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected_total = [int(x) for x in expected["frames"]]
    expected_per_cycle = [int(x) for x in expected.get("per_cycle", [])]
    return frames, calibration, expected_total, expected_per_cycle


def run_replay(
    frames: list[np.ndarray],
    calibration: FullCalibrationData,
) -> tuple[list[int], list[int]]:
    """逐帧喂 TimeSource，返回 (全局帧序列, 周期内帧序列)。

    周期内帧 = ``TimeSource.previous_frame``，-1 表示该帧无检出。
    """
    ts = TimeSource(calibration)
    elapsed: list[int] = []
    per_cycle: list[int] = []
    for frame in frames:
        ts.update(frame)
        elapsed.append(ts.total_elapsed_frames)
        per_cycle.append(ts.previous_frame)
    return elapsed, per_cycle


def diff_frames(actual: list[int], expected: list[int]) -> list[tuple[int, int, int]]:
    """返回 (帧序号, 实际, 预期) 不一致列表（长度不等也算错）。"""
    diffs: list[tuple[int, int, int]] = []
    for i in range(max(len(actual), len(expected))):
        a = actual[i] if i < len(actual) else -1
        e = expected[i] if i < len(expected) else -1
        if a != e:
            diffs.append((i, a, e))
    return diffs


def list_cases() -> list[Path]:
    """枚举 cases/ 下的回放用例目录（无素材时返回空）。"""
    if not CASES_DIR.exists():
        return []
    return sorted(p for p in CASES_DIR.iterdir() if (p / "expected.json").exists())
