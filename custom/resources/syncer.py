"""资源同步器：从 MaaAssistantArknights（本地或远程 GitHub）提取数据。

用法：
    uv run python -m custom.resources.syncer              # 同步全部（本地优先，远程回退）
    uv run python -m custom.resources.syncer --all-avatars  # 全量下载头像
    uv run python -m custom.resources.syncer --avatars "斑点,芬"  # 指定干员
    uv run python -m custom.resources.syncer --remote  # 从 GitHub 下载
"""

from __future__ import annotations

import json
import logging
import shutil
import urllib.request
from pathlib import Path

from custom.utils.runtime_paths import project_root

logger = logging.getLogger(__name__)

# 本地源（开发环境）
_MAA_ROOT = Path("../MaaAssistantArknights")
_BATTLE_DATA_LOCAL = _MAA_ROOT / "resource" / "battle_data.json"
_TILE_POS_LOCAL = _MAA_ROOT / "resource" / "Arknights-Tile-Pos"

# 远程源（用户环境）
_GITHUB = "https://raw.githubusercontent.com/MaaAssistantArknights/MaaAssistantArknights/main"
_BATTLE_DATA_REMOTE = f"{_GITHUB}/resource/battle_data.json"
_TILE_POS_API = "https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/contents/resource/Arknights-Tile-Pos"

# PRTS Wiki 头像
_AVATAR_URL = "https://media.prts.wiki/thumb.php?f=avg_{char_id}.png&w=120"


def _download(url: str, dest: Path) -> bool:
    try:
        urllib.request.urlretrieve(url, dest)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("下载失败 %s: %s", url, e)
        return False


def _get_battle_data(force_remote: bool = False) -> dict | None:
    """获取 battle_data.json（本地优先，远程回退）。"""
    if not force_remote and _BATTLE_DATA_LOCAL.exists():
        return json.loads(_BATTLE_DATA_LOCAL.read_text(encoding="utf-8"))

    logger.info("从 GitHub 下载 battle_data.json...")
    tmp = project_root() / "data" / ".battle_data.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if _download(_BATTLE_DATA_REMOTE, tmp):
        return json.loads(tmp.read_text(encoding="utf-8"))
    return None


# --- 干员 ---


def sync_operators(force_remote: bool = False) -> None:
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    raw = _get_battle_data(force_remote)
    if raw is None:
        logger.error("无法获取 battle_data.json")
        return

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


# --- 地图 ---


def sync_maps(force_remote: bool = False) -> None:
    data_dir = project_root() / "data"
    map_dir = data_dir / "map"
    map_dir.mkdir(parents=True, exist_ok=True)

    if not force_remote and _TILE_POS_LOCAL.exists():
        count = _copy_maps_local(_TILE_POS_LOCAL, map_dir)
    else:
        count = _download_maps_remote(map_dir)

    # 生成 level_codes
    codes: dict[str, str] = {}
    for p in map_dir.glob("*.json"):
        if "#f#" in p.name:
            continue
        code = p.name.split("-")[0]
        if code not in codes:
            codes[code] = p.name
    (data_dir / "level_codes.json").write_text(
        json.dumps(dict(sorted(codes.items())), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("地图数据: %d 文件 (%d 关)", count, len(codes))


def _copy_maps_local(src_dir: Path, dst_dir: Path) -> int:
    count = 0
    for src in src_dir.glob("*.json"):
        if "#f#" in src.name:
            continue
        shutil.copy2(src, dst_dir / src.name)
        count += 1
    return count


def _download_maps_remote(dst_dir: Path) -> int:
    """从 GitHub API 批量下载地图文件（较慢，仅首次/远程时用）。"""
    logger.info("从 GitHub 下载地图列表...")
    try:
        with urllib.request.urlopen(_TILE_POS_API) as resp:
            files = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        logger.error("无法获取地图列表: %s", e)
        return 0

    count = 0
    total = len(files)
    for i, f in enumerate(files):
        if f["type"] != "file" or "#f#" in f["name"]:
            continue
        dst = dst_dir / f["name"]
        if dst.exists():
            count += 1
            continue
        download_url = f["download_url"]
        if _download(download_url, dst):
            count += 1
        if (i + 1) % 200 == 0:
            logger.info("地图下载进度: %d/%d", i + 1, total)
    return count


# --- 头像 ---


def sync_avatars(
    operator_names: list[str] | None = None,
    all_avatars: bool = False,
) -> None:
    """下载干员头像。

    Args:
        operator_names: 指定干员名列表。
        all_avatars: 下载全部头像。
    """
    data_dir = project_root() / "data"
    avatar_dir = project_root() / "resource" / "image" / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    mapping_path = data_dir / "operator_mapping.json"
    if not mapping_path.exists():
        logger.error("operator_mapping.json 不存在，请先同步干员")
        return
    mapping: dict[str, str] = json.loads(mapping_path.read_text(encoding="utf-8"))

    if all_avatars:
        targets = list(mapping.items())
    elif operator_names:
        targets = [(n, mapping[n]) for n in operator_names if n in mapping]
    else:
        logger.info("未指定头像范围（用 --all-avatars 或 --avatars '名1,名2'）")
        return

    total = len(targets)
    downloaded = 0
    for i, (_name, char_id) in enumerate(targets):
        out_path = avatar_dir / f"{char_id}.png"
        if out_path.exists():
            continue
        url = _AVATAR_URL.format(char_id=char_id)
        if _download(url, out_path):
            downloaded += 1
        if (i + 1) % 100 == 0:
            logger.info("头像进度: %d/%d (新下载 %d)", i + 1, total, downloaded)

    existing = len(list(avatar_dir.glob("*.png")))
    logger.info("头像完成: 新下载 %d, 总计 %d 张", downloaded, existing)


# --- 统一入口 ---


def sync_all(force_remote: bool = False) -> None:
    logger.info("资源同步%s...", "（远程）" if force_remote else "")
    sync_operators(force_remote)
    sync_maps(force_remote)
    logger.info("资源同步完成")


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="资源同步器")
    parser.add_argument("--operators", action="store_true")
    parser.add_argument("--maps", action="store_true")
    parser.add_argument("--all-avatars", action="store_true", help="下载全部头像")
    parser.add_argument("--avatars", type=str, help="指定干员名（逗号分隔）")
    parser.add_argument("--remote", action="store_true", help="强制从 GitHub 下载")
    args = parser.parse_args()

    if args.all_avatars:
        sync_avatars(all_avatars=True)
    elif args.avatars:
        sync_avatars([n.strip() for n in args.avatars.split(",") if n.strip()])
    elif args.operators:
        sync_operators(args.remote)
    elif args.maps:
        sync_maps(args.remote)
    else:
        sync_all(args.remote)
