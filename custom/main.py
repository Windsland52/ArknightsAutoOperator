"""Milestone-1 smoke: in-process MAA wiring + screencap.

Run after `uv sync`:
    uv run python custom/main.py --mode win32        # PC client (Arknights.exe) / emulator window
    uv run python custom/main.py --mode adb          # Android emulator via ADB

Validates: maafw install, Toolkit discovery, Win32/ADB controller connect, screencap.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Make the repo root importable so `custom.*` resolves when run as a script
# (uv run python custom/main.py). No-op when run as a module (-m custom.main)
# or via the installed package.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from custom import config  # noqa: E402
from custom.utils.logger import get_logger, setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402

logger = get_logger(__name__)


def pick_win32_controller():  # type: ignore[no-untyped-def]
    from maa.controller import MaaWin32InputMethodEnum, MaaWin32ScreencapMethodEnum, Win32Controller
    from maa.toolkit import Toolkit

    windows = Toolkit.find_desktop_windows()
    if not windows:
        logger.warning("no desktop windows found")
        return None

    target = None
    for w in windows:
        name = getattr(w, "window_name", "") or ""
        cls = getattr(w, "class_name", "") or ""
        if any(k in name for k in ("Arknights", "明日方舟", "MuMu", "雷电", "Leidian")):
            target = w
            logger.info("matched window: name=%r class=%r hwnd=%s", name, cls, w.hwnd)
            break
    if target is None:
        target = windows[0]
        name = getattr(target, "window_name", "")
        logger.info("no Arknights/emulator window matched; using first: %r", name)

    return Win32Controller(
        hWnd=target.hwnd,
        screencap_method=MaaWin32ScreencapMethodEnum.FramePool,
        mouse_method=MaaWin32InputMethodEnum.PostMessage,
        keyboard_method=MaaWin32InputMethodEnum.PostMessage,
    )


def pick_adb_controller():  # type: ignore[no-untyped-def]
    from maa.controller import AdbController
    from maa.toolkit import Toolkit

    devices = Toolkit.find_adb_devices()
    if not devices:
        logger.warning("no adb devices found")
        return None
    d = devices[0]
    logger.info("using adb device: name=%r address=%r", getattr(d, "name", ""), d.address)
    return AdbController(
        adb_path=d.adb_path,
        address=d.address,
        screencap_methods=d.screencap_methods,
        input_methods=d.input_methods,
        config=d.config,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="AKOP milestone-1 MAA wiring smoke")
    parser.add_argument("--mode", choices=["win32", "adb"], default="win32")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))
    logger.info("project root: %s", paths["root"])

    controller = pick_win32_controller() if args.mode == "win32" else pick_adb_controller()
    if controller is None:
        logger.error("no controller available (mode=%s)", args.mode)
        return 2

    logger.info("connecting controller (mode=%s)...", args.mode)
    controller.post_connection().wait()
    logger.info("connected")

    logger.info("screencap...")
    image = controller.post_screencap().wait().get()
    logger.info("screencap ok: type=%s", type(image).__name__)

    out = paths["debug"] / "smoke.png"
    try:
        import numpy as np
        from PIL import Image

        if isinstance(image, np.ndarray):
            # maafw 返回 BGR（OpenCV 约定）；PIL 要 RGB → 反转通道。
            rgb = np.ascontiguousarray(image[..., ::-1])
            Image.fromarray(rgb).save(str(out))
            logger.info("saved %s shape=%s dtype=%s", out, image.shape, image.dtype)
        else:
            logger.warning("image is not ndarray (type=%s)", type(image).__name__)
    except Exception as e:  # noqa: BLE001
        logger.exception("save failed: %r", e)

    logger.info("process target: %s", config.PC_PROCESS_NAME)
    logger.info("smoke done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
