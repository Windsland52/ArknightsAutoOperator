"""资源同步器：从 MaaAssistantArknights（本地或远程 GitHub）提取干员/地图数据。

用法：
    uv run python -m aao.resources.syncer           # 同步全部（本地优先，远程回退）
    uv run python -m aao.resources.syncer --remote  # 强制从 GitHub 下载
    uv run python -m aao.resources.syncer --remote --proxy http://127.0.0.1:7890

产出（到 data/）：
- operator_mapping.json / operator_names.json：干员名 + charId
- map/*.json：关卡地图数据
- level_codes.json：关卡代号列表

注意：头像不在此同步——按 MAA 方式运行时自学习（见 aao.core.avatar）。
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from aao.utils.logger import logger, setup_logging
from aao.utils.runtime_paths import project_root

# 本地源（开发环境）
_MAA_ROOT = Path("../MaaAssistantArknights")
_BATTLE_DATA_LOCAL = _MAA_ROOT / "resource" / "battle_data.json"
_TILE_POS_LOCAL = _MAA_ROOT / "resource" / "Arknights-Tile-Pos"

# 远程源（用户环境）—— MAA 资源已移至 dev-v2 分支（main 分支无 battle_data / Tile-Pos）
_GITHUB = "https://raw.githubusercontent.com/MaaAssistantArknights/MaaAssistantArknights/dev-v2"
_BATTLE_DATA_REMOTE = f"{_GITHUB}/resource/battle_data.json"
_TILE_POS_API = "https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights/contents/resource/Arknights-Tile-Pos?ref=dev-v2"

_MAP_MANIFEST = ".manifest.json"
_MAX_WORKERS = 8


def _settings_proxy() -> str | None:
    """从 config/settings.json 读代理（不依赖 UI 模块，CLI 同步也能用）。"""
    try:
        path = project_root() / "config" / "settings.json"
        if not path.exists():
            return None
        proxy = json.loads(path.read_text(encoding="utf-8")).get("proxy", "")
        return str(proxy).strip() or None
    except Exception:  # noqa: BLE001
        return None


def _make_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    """构造 urllib opener。proxy 显式传入优先；否则使用系统/环境代理。"""
    if proxy:
        logger.info("资源同步使用代理: %s", proxy)
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    # build_opener 默认会读取 urllib.request.getproxies()（系统/环境代理）
    return urllib.request.build_opener()


def _download(opener: urllib.request.OpenerDirector, url: str, dest: Path) -> bool:
    try:
        with opener.open(url, timeout=30) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("下载失败 %s: %s", url, e)
        return False


def _get_battle_data(
    opener: urllib.request.OpenerDirector, force_remote: bool = False
) -> dict[str, Any] | None:
    """获取 battle_data.json（本地优先，远程回退）。"""
    if not force_remote and _BATTLE_DATA_LOCAL.exists():
        return json.loads(_BATTLE_DATA_LOCAL.read_text(encoding="utf-8"))

    logger.info("从 GitHub 下载 battle_data.json...")
    tmp = project_root() / "data" / ".battle_data.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if _download(opener, _BATTLE_DATA_REMOTE, tmp):
        return json.loads(tmp.read_text(encoding="utf-8"))
    return None


def sync_operators(force_remote: bool = False, proxy: str | None = None) -> None:
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    opener = _make_opener(proxy or _settings_proxy())
    raw = _get_battle_data(opener, force_remote)
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


def sync_maps(force_remote: bool = False, proxy: str | None = None) -> None:
    data_dir = project_root() / "data"
    map_dir = data_dir / "map"
    map_dir.mkdir(parents=True, exist_ok=True)

    opener = _make_opener(proxy or _settings_proxy())
    if not force_remote and _TILE_POS_LOCAL.exists():
        count = _copy_maps_local(_TILE_POS_LOCAL, map_dir)
    else:
        count = _download_maps_remote(map_dir, opener)

    codes: dict[str, str] = {}
    for p in map_dir.glob("*.json"):
        if "#f#" in p.name or p.name == _MAP_MANIFEST:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            code = data.get("code", "")
            if code:
                codes[code] = p.name
        except Exception:  # noqa: BLE001
            pass
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


def _load_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _download_maps_remote(dst_dir: Path, opener: urllib.request.OpenerDirector) -> int:
    """从 GitHub API 批量下载地图文件。

    增量机制：GitHub Contents API 返回 sha；本地 .manifest.json 记录 sha。
    文件存在且 sha 未变 → 跳过；sha 变化/缺失 → 重新下载。下载并发执行。
    """
    logger.info("从 GitHub 下载地图列表...")
    try:
        with opener.open(_TILE_POS_API, timeout=30) as resp:
            files = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        logger.error("无法获取地图列表: %s", e)
        return 0

    manifest_path = dst_dir / _MAP_MANIFEST
    manifest = _load_manifest(manifest_path)
    remote_names: set[str] = set()
    tasks: list[tuple[str, str, Path, str]] = []  # (name, url, path, sha)
    ready = 0

    for f in files:
        if f.get("type") != "file" or "#f#" in f.get("name", ""):
            continue
        name = f["name"]
        sha = f.get("sha", "")
        remote_names.add(name)
        dst = dst_dir / name
        if dst.exists() and manifest.get(name) == sha:
            ready += 1
            continue
        tasks.append((name, f["download_url"], dst, sha))

    if tasks:
        logger.info("地图增量下载: %d 个需更新 / %d 个总文件", len(tasks), ready + len(tasks))
        done = 0
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futs = {
                pool.submit(_download, opener, url, dst): (name, sha)
                for name, url, dst, sha in tasks
            }
            for fut in as_completed(futs):
                name, sha = futs[fut]
                if fut.result():
                    ready += 1
                    done += 1
                    manifest[name] = sha
                if done and done % 100 == 0:
                    logger.info("地图下载进度: %d/%d", done, len(tasks))
    else:
        logger.info("地图数据已是最新（%d 文件）", ready)

    # 只写入远端仍存在且本地已成功下载/存在的文件 sha；失败的下次会重试。
    saved_manifest = {
        name: manifest[name]
        for name in sorted(remote_names)
        if (dst_dir / name).exists() and manifest.get(name)
    }
    manifest_path.write_text(
        json.dumps(saved_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return ready


def sync_all(force_remote: bool = False, proxy: str | None = None) -> None:
    logger.info("资源同步%s...", "（远程）" if force_remote else "")
    sync_operators(force_remote, proxy=proxy)
    sync_maps(force_remote, proxy=proxy)
    logger.info("资源同步完成")


if __name__ == "__main__":
    import argparse

    setup_logging("INFO")
    parser = argparse.ArgumentParser(description="资源同步器")
    parser.add_argument("--operators", action="store_true")
    parser.add_argument("--maps", action="store_true")
    parser.add_argument("--remote", action="store_true", help="强制从 GitHub 下载")
    parser.add_argument("--proxy", default=None, help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
    args = parser.parse_args()

    if args.operators:
        sync_operators(args.remote, proxy=args.proxy)
    elif args.maps:
        sync_maps(args.remote, proxy=args.proxy)
    else:
        sync_all(args.remote, proxy=args.proxy)
