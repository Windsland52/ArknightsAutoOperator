"""录制工具：持续截图 → 帧序列 PNG（直接兼容 tests/replay/ 回归框架）。

用法::

    uv run python tools/screencap.py --profile test_30f_1280x720.json --name 边界周期
    uv run python tools/screencap.py -p test_30f_1280x720.json -n 负费 --duration 120

Output::

    tests/replay/cases/<name>/
        frames/            # 逐帧 PNG（%06d.png）
        calibration.json   # 对应校准文件副本

无间隔轮询（截图本身的耗时就是自然速率），Ctrl+C 停止；加 --duration 自动到时停。
"""

from __future__ import annotations

import argparse
import os
import shutil
import time

import numpy as np
from PIL import Image

_RECORD_DIR = "tests/replay/cases"


def main() -> int:
    parser = argparse.ArgumentParser(description="录制截图帧序列，供录制回放回归")
    parser.add_argument(
        "-p",
        "--profile",
        required=True,
        help="校准文件名（config/calibration/ 下）",
    )
    parser.add_argument(
        "-n",
        "--name",
        required=True,
        help=f"用例名称（输出到 {_RECORD_DIR}/<name>/）",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="录制时长（秒），0=持续录制靠 Ctrl+C 停止",
    )
    args = parser.parse_args()

    # --- 连 Win32 ---
    from maa.toolkit import Toolkit

    Toolkit.init_option("./")
    wins = Toolkit.find_desktop_windows()
    target = next((w for w in wins if "明日方舟" in (w.window_name or "")), None)
    if target is None:
        print("ERROR: 未找到「明日方舟」窗口")
        return 1

    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )

    ctrl = Win32Controller(
        target.hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        MaaWin32InputMethodEnum.PostMessage,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(720)
    print(f"已连接：{target.window_name} ({target.hwnd})")

    # --- 输出目录 ---
    out_dir = os.path.join(_RECORD_DIR, args.name)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # --- 拷贝校准文件 ---
    calib_src = os.path.join("config", "calibration", args.profile)
    if not os.path.exists(calib_src):
        print(f"ERROR: 校准文件不存在: {calib_src}")
        return 1
    calib_dst = os.path.join(out_dir, "calibration.json")
    shutil.copy2(calib_src, calib_dst)
    print(f"校准已拷贝: {calib_src} → {calib_dst}")

    # --- 录制循环（无间隔，截图耗时就是自然速率） ---
    start_time = time.monotonic()
    frame_no = 0
    dur_hint = f"时长={args.duration:.0f}s" if args.duration else "Ctrl+C 停止"
    print(f"开始录制（无间隔轮询），{dur_hint}")

    try:
        while True:
            if args.duration and (time.monotonic() - start_time) >= args.duration:
                break

            job = ctrl.post_screencap()
            job.wait()
            if job.failed:
                print(f"WARN: 帧 {frame_no} 截图失败")
                continue

            img = job.get()
            if img is None or img.size == 0:  # pyright: ignore[reportUnnecessaryComparison]
                continue

            # BGR → RGB，存 BMP（无压缩，避免 PNG 编码吃掉帧间隔）
            rgb: np.ndarray = img[:, :, ::-1].copy()
            path = os.path.join(frames_dir, f"{frame_no:06d}.bmp")
            Image.fromarray(rgb).save(path, format="BMP")
            frame_no += 1

            if frame_no % 60 == 0:
                elapsed = time.monotonic() - start_time
                real_fps = frame_no / elapsed if elapsed > 0 else 0
                print(f"... {frame_no} 帧 ({elapsed:.1f}s, {real_fps:.0f}fps)")

        if args.duration:
            elapsed = time.monotonic() - start_time
            print(f"录制完成：{frame_no} 帧 ({elapsed:.1f}s) → {frames_dir}/")

    except KeyboardInterrupt:
        elapsed = time.monotonic() - start_time
        print(f"\n录制停止：{frame_no} 帧 ({elapsed:.1f}s) → {frames_dir}/")

    # --- 转 PNG（BMP 体积太大，录制完成后批量压缩） ---
    _bmp_to_png(frames_dir)

    print(
        f"\n用例已就绪。添加 expected.json 后运行：\n  uv run pytest tests/replay/ -k {args.name}"
    )
    return 0


def _bmp_to_png(frames_dir: str) -> None:
    bmps = sorted(f for f in os.listdir(frames_dir) if f.endswith(".bmp"))
    if not bmps:
        return
    total = len(bmps)
    print(f"转 PNG 中（{total} 帧）...", end="", flush=True)
    for i, name in enumerate(bmps):
        bmp_path = os.path.join(frames_dir, name)
        png_path = bmp_path[:-4] + ".png"
        with Image.open(bmp_path) as im:
            im.save(png_path, format="PNG")
        os.remove(bmp_path)
        if (i + 1) % 120 == 0:
            print(f"\r转 PNG 中（{total} 帧）... {i + 1}/{total}", end="", flush=True)
    print(f"\r转 PNG 完成（{total} 帧）  ")


if __name__ == "__main__":
    raise SystemExit(main())
