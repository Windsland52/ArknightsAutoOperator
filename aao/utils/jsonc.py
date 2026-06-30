"""JSONC（带注释的 JSON）解析，基于 json5。

MaaFramework pipeline JSON 常含 // 和 /* */ 注释，标准 json 解析失败。
json5 是广泛使用的 JSONC/JSON5 库，兼容 // /* */ 注释、尾逗号等。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import json5


def loads(text: str) -> Any:
    """解析 JSONC/JSON5 字符串。"""
    return cast(Any, json5.loads(text))


def load(path: Path | str) -> Any:
    """从文件解析 JSONC/JSON5。"""
    return cast(Any, json5.loads(Path(path).read_text(encoding="utf-8")))
