"""Runtime path configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境。"""
    return getattr(sys, "frozen", False)


def project_root() -> Path:
    """项目根目录。

    - 开发环境：aao/utils/runtime_paths.py 往上两级
    - PyInstaller onedir：exe 同级目录（resource/data/config 外置在 exe 旁）
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # aao/utils/runtime_paths.py -> aao/utils -> aao -> root
    return Path(__file__).resolve().parents[2]


def configure_paths() -> dict[str, Path]:
    root = project_root()
    paths = {
        "root": root,
        "debug": root / "debug",
        "resource": root / "resource",
        "data": root / "data",
        "config": root / "config",
    }
    for key in ("debug", "data", "config"):
        paths[key].mkdir(parents=True, exist_ok=True)
    (paths["resource"] / "base").mkdir(parents=True, exist_ok=True)

    # PyInstaller 环境下，告诉 maafw 去 _internal/maa/bin 找 MaaFramework.dll。
    # （maafw 的 ctypes 加载见 maa/__init__.py：优先读 MAAFW_BINARY_PATH 环境变量）
    if is_frozen():
        maa_bin = Path(sys._MEIPASS) / "maa" / "bin"  # type: ignore[attr-defined] # pyright: ignore[reportOptionalMemberAccess]
        if maa_bin.exists() and not os.environ.get("MAAFW_BINARY_PATH"):
            os.environ["MAAFW_BINARY_PATH"] = str(maa_bin)

    if os.getcwd() != str(root):
        os.chdir(root)
    return paths
