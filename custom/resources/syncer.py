"""资源同步器：从 MaaAssistantArknights 提取干员/地图/头像数据。

用法：
    uv run python -m custom.resources.syncer              # 同步全部
    uv run python -m custom.resources.syncer --maps       # 只同步地图
    uv run python -m custom.resources.syncer --operators  # 只同步干员名
    uv run python -m custom.resources.syncer --avatars "斑点,芬"  # 按需下载头像

产出（到 data/）：
- operator_mapping.json / operator_names.json：干员名 + charId
- level_codes.json：关卡代号列表
- map/*.json：关卡地图数据（从 Arknights-Tile-Pos 复制）
- avatar/{charId}.png：干员部署头像（从 PRTS Wiki 下载，按需）
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from custom.utils.runtime_paths import project_root

logger = logging.getLogger(__name__)

_MAA_ROOT = Path("../MaaAssistantArknights")
_BATTLE_DATA = _MAA_ROOT / "resource" / "battle_data.json"
_TILE_POS = _MAA_ROOT / "resource" / "Arknights-Tile-Pos"

# PRTS Wiki 头像 URL 模板
_AVATAR_URL = "https://media.prts.wiki/thumb.php?f=avg_{char_id}.png&w=120"


def sync_operators() -> None:
    """从 battle_data.json 提取干员名 → charId 映射。"""
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if not _BATTLE_DATA.exists():
        logger.error("battle_data.json 不存在: %s", _BATTLE_DATA)
        return

    raw = json.loads(_BATTLE_DATA.read_text(encoding="utf-8"))
    chars = raw.get("chars", {})

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

    names.sort(key=lambda x: (-int(x["rarity"]), x["name"]))

    (data_dir / "operator_mapping.json").write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / "operator_names.json").write_text(
        json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("干员数据: %d 名", len(names))


def sync_maps() -> None:
    """复制 Arknights-Tile-Pos 地图 JSON 到 data/map/（跳过 #f# 翻转变体）。"""
    data_dir = project_root() / "data"
    map_dir = data_dir / "map"
    map_dir.mkdir(parents=True, exist_ok=True)

    if not _TILE_POS.exists():
        logger.error("Arknights-Tile-Pos 不存在: %s", _TILE_POS)
        return

    count = 0
    codes: dict[str, str] = {}
    for src in _TILE_POS.glob("*.json"):
        if "#f#" in src.name:
            continue
        dst = map_dir / src.name
        shutil.copy2(src, dst)
        count += 1
        code = src.name.split("-")[0]
        if code not in codes:
            codes[code] = src.name

    sorted_codes = dict(sorted(codes.items()))
    (data_dir / "level_codes.json").write_text(
        json.dumps(sorted_codes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "地图数据: %d 文件 → data/map/ + level_codes.json (%d 关)",
        count,
        len(sorted_codes),
    )


def sync_avatars(operator_names: list[str] | None = None) -> None:
    """按需下载干员头像到 resource/image/avatar/。

    Args:
        operator_names: 要下载的干员名列表。None = 下载全部（慢）。
    """
    import urllib.request

    data_dir = project_root() / "data"
    avatar_dir = project_root() / "resource" / "image" / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # 加载映射
    mapping_path = data_dir / "operator_mapping.json"
    if not mapping_path.exists():
        logger.error("operator_mapping.json 不存在，请先运行 --operators")
        return
    mapping: dict[str, str] = json.loads(mapping_path.read_text(encoding="utf-8"))

    if operator_names is None:
        targets = list(mapping.items())
    else:
        targets = [(name, mapping[name]) for name in operator_names if name in mapping]

    downloaded = 0
    for name, char_id in targets:
        out_path = avatar_dir / f"{char_id}.png"
        if out_path.exists():
            continue  # 已存在跳过

        url = _AVATAR_URL.format(char_id=char_id)
        try:
            urllib.request.urlretrieve(url, out_path)
            downloaded += 1
            logger.info("下载头像: %s (%s)", name, char_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("下载失败 %s: %s", name, e)

    logger.info("头像下载完成: %d 张 (共 %d 目标)", downloaded, len(targets))


def sync_all() -> None:
    """同步全部资源（干员 + 地图）。"""
    logger.info("开始资源同步（源: %s）", _MAA_ROOT.resolve())
    sync_operators()
    sync_maps()
    logger.info("资源同步完成。头像按需下载：--avatars '干员名1,干员名2'")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="资源同步器")
    parser.add_argument("--operators", action="store_true", help="只同步干员名")
    parser.add_argument("--maps", action="store_true", help="只同步地图")
    parser.add_argument("--avatars", type=str, default=None, help="下载头像（干员名逗号分隔）")
    args = parser.parse_args()

    if args.avatars is not None:
        names = [n.strip() for n in args.avatars.split(",") if n.strip()]
        sync_avatars(names)
    elif args.operators:
        sync_operators()
    elif args.maps:
        sync_maps()
    else:
        sync_all()
