"""资源同步器：从 MaaAssistantArknights 提取干员/地图数据。

用法：
    uv run python -m custom.resources.syncer

产出（到 data/）：
- operator_mapping.json：干员名 → charId
- operator_names.json：干员名列表（编辑器下拉选）
- level_codes.json：关卡代号列表
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from custom.utils.runtime_paths import project_root

logger = logging.getLogger(__name__)

_MAA_ROOT = Path("../MaaAssistantArknights")
_BATTLE_DATA = _MAA_ROOT / "resource" / "battle_data.json"
_TILE_POS = _MAA_ROOT / "resource" / "Arknights-Tile-Pos"


def sync_operators() -> None:
    """从 battle_data.json 提取干员名 → charId 映射。"""
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if not _BATTLE_DATA.exists():
        logger.error("battle_data.json 不存在: %s", _BATTLE_DATA)
        return

    raw = json.loads(_BATTLE_DATA.read_text(encoding="utf-8"))
    chars = raw.get("chars", {})

    # 干员名 → charId
    mapping: dict[str, str] = {}
    names: list[dict[str, str]] = []
    for char_id, info in chars.items():
        name = info.get("name", "")
        if not name:
            continue
        mapping[name] = char_id
        names.append(
            {
                "name": name,
                "char_id": char_id,
                "profession": info.get("profession", ""),
                "rarity": info.get("rarity", 0),
            }
        )

    # 按稀有度降序 + 名字排序
    names.sort(key=lambda x: (-int(x["rarity"]), x["name"]))

    (data_dir / "operator_mapping.json").write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / "operator_names.json").write_text(
        json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("干员数据: %d 名 → data/operator_mapping.json + operator_names.json", len(names))


def sync_level_codes() -> None:
    """扫描 Arknights-Tile-Pos 提取关卡代号列表。"""
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if not _TILE_POS.exists():
        logger.error("Arknights-Tile-Pos 不存在: %s", _TILE_POS)
        return

    codes: dict[str, str] = {}  # code → filename
    for p in _TILE_POS.glob("*.json"):
        if "#f#" in p.name:
            continue
        code = p.name.split("-")[0]
        if code not in codes:
            codes[code] = p.name

    sorted_codes = dict(sorted(codes.items()))
    (data_dir / "level_codes.json").write_text(
        json.dumps(sorted_codes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("关卡数据: %d 关 → data/level_codes.json", len(sorted_codes))


def sync_all() -> None:
    """同步全部资源。"""
    logger.info("开始资源同步（源: %s）", _MAA_ROOT.resolve())
    sync_operators()
    sync_level_codes()
    logger.info("资源同步完成")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sync_all()
