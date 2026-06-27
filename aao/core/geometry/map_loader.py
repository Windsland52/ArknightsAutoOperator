"""关卡地图数据加载。

数据来源：data/map/*.json（由 aao.resources.syncer 从 MaaAssistantArknights GitHub 同步）。
文件名格式：{code}-{type}-level_{stageId}.json，如 main_01-07-obt-main-level_main_01-07.json

用户输入的代号（如 "1-7"）通过 level_codes.json 映射到实际文件名。
"""

from __future__ import annotations

import json
from pathlib import Path

from aao.utils.logger import logger
from aao.utils.runtime_paths import project_root


def _map_dir() -> Path:
    """地图数据目录。单一来源：syncer 同步到的 data/map。"""
    return project_root() / "data" / "map"


def _level_codes() -> dict[str, str]:
    """加载 level_codes.json（代号 → 文件名）。"""
    path = project_root() / "data" / "level_codes.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def find_map_file(code: str) -> Path | None:
    """按关卡代号（如 '1-7'）查找地图文件。

    优先用 level_codes.json 映射；回退到 glob 精确匹配。
    """
    d = _map_dir()
    if not d.exists():
        return None

    # 1. 通过 level_codes 映射
    codes = _level_codes()
    if code in codes:
        p = d / codes[code]
        if p.exists():
            return p

    # 2. glob 精确前缀匹配（如 code 本身就是文件名前缀 main_01-07）
    for p in d.glob(f"{code}-*.json"):
        if "#f#" not in p.name:
            return p

    return None


def load_map(code: str) -> dict | None:
    """加载关卡数据。code 如 '1-7'。"""
    path = find_map_file(code)
    if path is None:
        logger.error("未找到关卡 %s 的地图数据", code)
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    logger.info(
        "加载关卡 %s (%s): %dx%d", code, data.get("name", "?"), data["height"], data["width"]
    )
    return data


def list_codes() -> list[str]:
    """列出所有可用关卡代号。"""
    return sorted(_level_codes().keys())
