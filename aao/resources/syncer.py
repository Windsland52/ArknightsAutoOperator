"""资源同步器：从 MaaAssistantArknights（GitHub dev-v2 分支）提取干员/地图数据。

用法：
    uv run python -m aao.resources.syncer           # 同步全部
    uv run python -m aao.resources.syncer --proxy http://127.0.0.1:7890

产出（到 data/）：
- operator_mapping.json / operator_names.json：干员名 + charId
- map/*.json：关卡地图数据
- level_codes.json：关卡代号列表

注意：头像不在此同步——按 MAA 方式运行时自学习（见 aao.core.avatar）。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aao.utils.logger import logger, setup_logging
from aao.utils.runtime_paths import project_root


@dataclass(frozen=True, slots=True)
class SyncResult:
    """一次同步的结构化结果，调用方可据此如实汇报成败。"""

    ok: bool
    name: str  # "干员名" / "地图"
    total: int = 0  # 远端拿到的条目数（operators 为 0 或 1，maps 为 trees tile 数）
    done: int = 0  # 成功条目数
    failed: int = 0  # 显式失败条目数
    skipped: int = 0  # 已是最新被跳过的条目数
    error: str = ""  # 致命错误描述（fatal 时填）

    @property
    def message(self) -> str:
        """供 UI 进度回调使用的人话汇报。"""
        if self.error:
            return f"{self.name}更新失败：{self.error}"
        if self.failed == 0:
            return f"{self.name}更新完成（{self.done} 条）"
        return f"{self.name}部分失败（成功 {self.done}，失败 {self.failed}）"

# 主源：GitHub dev-v2 分支（main 分支改名而来）
_REPO = "MaaAssistantArknights/MaaAssistantArknights"
_BRANCH = "dev-v2"
_GITHUB = f"https://raw.githubusercontent.com/{_REPO}/{_BRANCH}"
_BATTLE_DATA_REMOTE = f"{_GITHUB}/resource/battle_data.json"
# 列表 API：用 git trees recursive 一次性拿到整个分支所有 path+sha。
# GitHub Contents API 对大目录最多返回 1000 项且不真分页（page=N 回放同一批数据），
# Arknights-Tile-Pos 目录已超 3000 文件，Contents API 会漏掉大量新关卡。
_TREES_API = f"https://api.github.com/repos/{_REPO}/git/trees/{_BRANCH}?recursive=1"
_TILE_POS_PREFIX = "resource/Arknights-Tile-Pos/"

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


def _settings_github_token() -> str | None:
    """从 settings.json 读取并解密 GitHub token（可选）。"""
    try:
        from aao.utils.secure_store import decrypt_text

        path = project_root() / "config" / "settings.json"
        if not path.exists():
            return None
        enc = json.loads(path.read_text(encoding="utf-8")).get("github_token_enc", "")
        return decrypt_text(str(enc)) if enc else None
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


def _make_request(url: str, token: str | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ArknightsAutoOperator")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _download(
    opener: urllib.request.OpenerDirector, url: str, dest: Path, token: str | None = None
) -> bool:
    try:
        with opener.open(_make_request(url, token), timeout=30) as resp:
            data = resp.read()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("下载失败 %s: %s", url, e)
        return False


def _list_remote_tiles(
    opener: urllib.request.OpenerDirector, token: str | None = None
) -> list[dict[str, str]] | None:
    """用 git trees API（recursive=1）一次性拉取整个分支所有 path+sha。

    返回 list[{name, sha, raw_url}]，仅包含 Arknights-Tile-Pos 下的 .json blob
    （过滤掉 ``#f#`` 命名的分支变体文件）。

    为什么不用 Contents API：GitHub Contents API 对单目录硬上限 1000 项，
    且 page=N 会回放同一批数据（不真分页）。Arknights-Tile-Pos 已超 3000 文件，
    Contents API 漏掉大量新关卡，syncer 也就永远拿不到 act26side / main_14 等新增。
    git/trees?recursive=1 一次拉完整树（实测 3987 项也远未到 100000 上限），
    blob sha 与 Contents API sha 一致，可直接沿用同一份 manifest。
    """
    logger.info("从 GitHub git/trees 拉取 dev-v2 完整树...")
    try:
        with opener.open(_make_request(_TREES_API, token), timeout=60) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        logger.error("拉取 git/trees 失败: %s", e)
        return None

    if data.get("truncated"):
        logger.error("git/trees 返回 truncated=true，分支过大，需按子目录分批拉取（当前未实现）。")
        return None

    result: list[dict[str, str]] = []
    for entry in data.get("tree", []):
        if entry.get("type") != "blob":
            continue
        path: str = entry.get("path", "")
        if not path.startswith(_TILE_POS_PREFIX) or not path.endswith(".json"):
            continue
        name = path[len(_TILE_POS_PREFIX) :]
        if "#f#" in name:
            continue
        result.append(
            {
                "name": name,
                "sha": entry.get("sha", ""),
                # path 里的 ``#`` 会被 urllib 当作 URL fragment 起始符，
                # 实际 path 在 ``#`` 之前就截断了，GitHub raw 返回 404。
                # 把 path 部分单独 quote：保留 ``/`` 不动，编码 ``#`` 为 ``%23``。
                "raw_url": f"{_GITHUB}/{urllib.parse.quote(path, safe='/')}",
            }
        )
    logger.info("git/trees: Tile-Pos 远端文件 %d 个", len(result))
    return result


def _get_battle_data(
    opener: urllib.request.OpenerDirector, token: str | None = None
) -> dict[str, Any] | None:
    """从 GitHub 下载 battle_data.json。

    下载到 data/.battle_data.json 后立即读出解析、并删掉临时文件，
    避免每次 sync_operators 都留一个 550 KB 临时 dump 进 data/ 影响发版包体积。
    """
    logger.info("从 GitHub 下载 battle_data.json...")
    tmp = project_root() / "data" / ".battle_data.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if not _download(opener, _BATTLE_DATA_REMOTE, tmp, token=token):
        tmp.unlink(missing_ok=True)
        return None
    try:
        return json.loads(tmp.read_text(encoding="utf-8"))
    finally:
        tmp.unlink(missing_ok=True)


def sync_operators(proxy: str | None = None) -> SyncResult:
    data_dir = project_root() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    opener = _make_opener(proxy or _settings_proxy())
    token = _settings_github_token()
    raw = _get_battle_data(opener, token=token)
    if raw is None:
        logger.error("无法获取 battle_data.json")
        return SyncResult(ok=False, name="干员名", error="无法获取 battle_data.json")

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
    return SyncResult(
        ok=True,
        name="干员名",
        total=len(chars),
        done=len(names),
        skipped=len(chars) - len(names),
    )


def rebuild_level_codes() -> int:
    """扫盘 data/map/*.json，重写 data/level_codes.json (代号 → 文件名)。

    独立于下载：即便上次 sync_maps 中途被中断、manifest 未提交，
    只要 disk 上有 .json 文件，本函数就能让运行时关卡查找反映 disk 真相。
    CLI: `python -m aao.resources.syncer --rebuild-codes`。
    """
    map_dir = project_root() / "data" / "map"
    if not map_dir.exists():
        logger.error("data/map 不存在，无可扫描的地图文件")
        return 0
    return _rebuild_level_codes(map_dir)


def _rebuild_level_codes(map_dir: Path) -> int:
    """扫盘 map/*.json，重写 data/level_codes.json (代号 → 文件名)。

    独立于下载：即便上次 sync_maps 中途被中断下载未完成、manifest 未提交，
    只要 disk 上有 .json 文件，本函数就能让运行时关卡查找反映 disk 真相。
    返回收录的代号数量。
    """
    data_dir = map_dir.parent
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
    return len(codes)


def sync_maps(proxy: str | None = None) -> SyncResult:
    data_dir = project_root() / "data"
    map_dir = data_dir / "map"
    map_dir.mkdir(parents=True, exist_ok=True)

    # 1. 先扫盘生成 level_codes.json —— 即便下载从未成功，运行期也能用已落盘的旧地图。
    pre_codes = _rebuild_level_codes(map_dir)
    logger.info("level_codes.json 初始刷新: %d 关", pre_codes)

    # 2. 增量下载（基于 git/trees blob sha）。
    opener = _make_opener(proxy or _settings_proxy())
    token = _settings_github_token()
    result = _download_maps_remote(map_dir, opener, token=token)

    # 3. 下载完成后再扫一次，把新关卡也写进 level_codes.json。
    post_codes = _rebuild_level_codes(map_dir)

    if result is None:
        logger.error("无法拉取 git/trees，地图数据未更新")
        return SyncResult(ok=False, name="地图", error="无法拉取 git/trees")

    ready, done, failed = result
    logger.info("地图数据: ready=%d done=%d failed=%d, level_codes=%d 关", ready, done, failed, post_codes)
    return SyncResult(
        ok=failed == 0,
        name="地图",
        total=ready + failed,
        done=ready,
        failed=failed,
        skipped=ready - done,
    )


def _load_manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _download_maps_remote(
    dst_dir: Path, opener: urllib.request.OpenerDirector, token: str | None = None
) -> tuple[int, int, int] | None:
    """从 GitHub 下载地图文件（增量：基于 git/trees blob sha）。

    git/trees 一次拉完整分支树；Arknights-Tile-Pos 子树下每个 .json blob 的 sha
    与 Contents API sha 一致，可直接与本地的 .manifest.json 比对：
    - 文件存在且 sha 与远端一致 → 跳过
    - sha 变化或缺失 → 并发下载 raw URL
    失败的不更新 manifest sha，下次重试。

    Returns:
        (ready, done, failed) 或 None（git/trees 拉取失败）。
        ready = 下载/已是最新成功的累计。
        done  = 本次实际新下载成功的数量。
        failed = 本次尝试下载但失败的数量。
    """
    files = _list_remote_tiles(opener, token)
    if files is None:
        return None

    manifest_path = dst_dir / _MAP_MANIFEST
    manifest = _load_manifest(manifest_path)
    remote_names: set[str] = set()
    tasks: list[tuple[str, str, Path, str]] = []  # (name, url, path, sha)
    ready = 0

    for f in files:
        name = f["name"]
        sha = f["sha"]
        url = f["raw_url"]
        remote_names.add(name)
        dst = dst_dir / name
        if dst.exists() and manifest.get(name) == sha:
            ready += 1
            continue
        tasks.append((name, url, dst, sha))

    def _commit_manifest() -> None:
        # 落盘前过滤：只保留远端仍存在且 disk 上确实存在的文件 sha。
        saved = {
            name: manifest[name]
            for name in sorted(remote_names)
            if (dst_dir / name).exists() and manifest.get(name)
        }
        manifest_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")

    done = 0
    failed = 0
    if tasks:
        logger.info("地图增量下载: %d 个需更新 / %d 个总文件", len(tasks), ready + len(tasks))
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futs = {
                pool.submit(_download, opener, url, dst, token): (name, sha)
                for name, url, dst, sha in tasks
            }
            for fut in as_completed(futs):
                name, sha = futs[fut]
                if fut.result():
                    ready += 1
                    done += 1
                    manifest[name] = sha
                    # chunked commit：每 100 个落盘一次，被中断也不丢进度。
                    if done % 100 == 0:
                        _commit_manifest()
                        logger.info("地图下载进度: %d/%d", done, len(tasks))
                else:
                    failed += 1
    else:
        logger.info("地图数据已是最新（%d 文件）", ready)

    _commit_manifest()
    return ready, done, failed


def sync_all(proxy: str | None = None) -> list[SyncResult]:
    """同步干员名 + 地图。返回每一步的 SyncResult，调用方可据此汇报。"""
    logger.info("资源同步（远程）...")
    results = [sync_operators(proxy=proxy), sync_maps(proxy=proxy)]
    if all(r.ok for r in results):
        logger.info("资源同步完成")
    else:
        failed_steps = [r.name for r in results if not r.ok]
        logger.warning("资源同步部分失败: %s", ", ".join(failed_steps))
    return results


if __name__ == "__main__":
    import argparse

    setup_logging("INFO")
    parser = argparse.ArgumentParser(description="资源同步器")
    parser.add_argument("--operators", action="store_true")
    parser.add_argument("--maps", action="store_true")
    parser.add_argument(
        "--rebuild-codes",
        action="store_true",
        help="只重写 level_codes.json（不下载，扫盘即可）",
    )
    parser.add_argument("--proxy", default=None, help="HTTP/HTTPS 代理，如 http://127.0.0.1:7890")
    args = parser.parse_args()

    if args.rebuild_codes:
        n = rebuild_level_codes()
        print(f"level_codes.json: {n} 关")
    elif args.operators:
        r = sync_operators(proxy=args.proxy)
        print(r.message)
    elif args.maps:
        r = sync_maps(proxy=args.proxy)
        print(r.message)
    else:
        for r in sync_all(proxy=args.proxy):
            print(r.message)
