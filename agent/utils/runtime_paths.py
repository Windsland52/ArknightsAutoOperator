"""Runtime path configuration."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    # agent/utils/runtime_paths.py -> agent/utils -> agent -> root
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

    if os.getcwd() != str(root):
        os.chdir(root)
    return paths
