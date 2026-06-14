"""关卡地图数据加载。

数据来源：MaaAssistantArknights/resource/Arknights-Tile-Pos/*.json
文件名格式：{code}-{type}-level_{stageId}.json，如 main_01-07-obt-main-level_main_01-07.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from custom.utils.runtime_paths import project_root

logger = logging.getLogger(__name__)


def _map_dirs() -> list[Path]:
    """地图数据搜索路径（优先 data/map，回退 ../MaaAssistantArknights）。"""
    root = project_root()
    dirs = [
        root / "data" / "map",
        Path("../MaaAssistantArknights/resource/Arknights-Tile-Pos"),
    ]
    return [d for d in dirs if d.exists()]


def find_map_file(code: str) -> Optional[Path]:
    """按关卡代号（如 '1-7'）查找地图文件。"""
    for d in _map_dirs():
        # 精确匹配 code 前缀
        for p in d.glob(f"{code}-*.json"):
            if "#f#" not in p.name:  # 跳过翻转变体
                return p
    return None


def load_map(code: str) -> Optional[dict]:
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
    codes: set[str] = set()
    for d in _map_dirs():
        for p in d.glob("*.json"):
            if "#f#" in p.name:
                continue
            code = p.name.split("-")[0]
            codes.add(code)
    return sorted(codes)
