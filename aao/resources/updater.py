"""软件 + 资源更新检查。

用法（CLI）：
    uv run python -m aao.resources.updater --check      # 检查软件更新
    uv run python -m aao.resources.updater --resources  # 更新资源
    uv run python -m aao.resources.updater --all        # 全部

用法（代码，从 UI 调用）：
    from aao.resources.updater import UpdateChecker
    checker = UpdateChecker()
    has_update, version = checker.check_software()
    checker.update_resources(progress_cb=lambda p: ...)
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

from aao import __version__
from aao.resources import syncer
from aao.utils.logger import logger, setup_logging

_REPO = "Windsland52/ArknightsAutoOperator"
_RELEASES_API = f"https://api.github.com/repos/{_REPO}/releases/latest"


class UpdateChecker:
    """软件 + 资源更新检查器。"""

    def check_software(self) -> tuple[bool, str, str]:
        """检查 GitHub 最新 release。

        Returns:
            (has_update, latest_version, download_url)
        """
        try:
            req = urllib.request.Request(_RELEASES_API)
            req.add_header("User-Agent", "ArknightsAutoOperator")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001
            logger.warning("检查更新失败: %s", e)
            return False, "", ""

        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            return False, "", ""

        has_update = _compare_versions(latest, __version__) > 0
        download_url = data.get("html_url", "")
        logger.info(
            "当前 %s → 最新 %s (%s)",
            __version__,
            latest,
            "有更新" if has_update else "最新",
        )
        return has_update, latest, download_url

    def update_resources(
        self,
        progress_cb: Callable[[str], None] | None = None,
    ) -> list[syncer.SyncResult]:
        """从远程更新全部资源（干员名 + 地图）。

        失败不再被吞：每步的 SyncResult 都返回，调用方可据此如实汇报。
        即便某步失败也继续后续步骤（部分同步好过完全不同步）。

        Args:
            progress_cb: 可选进度回调 (message: str) -> None。

        Returns:
            每一步的 SyncResult。`all(r.ok)` 为完全成功。
        """
        steps: list[tuple[str, Callable[[], syncer.SyncResult]]] = [
            ("干员名", syncer.sync_operators),
            ("地图", syncer.sync_maps),
        ]
        results: list[syncer.SyncResult] = []
        for name, fn in steps:
            if progress_cb:
                progress_cb(f"正在更新{name}...")
            r = fn()
            results.append(r)
            if progress_cb:
                progress_cb(r.message)
        return results

    def update_all(self, progress_cb: Callable[[str], None] | None = None) -> dict:
        """检查软件更新 + 更新资源。

        Returns:
            {"software": (has_update, version, url), "resources": [SyncResult]}
        """
        result: dict = {}

        if progress_cb:
            progress_cb("检查软件更新...")
        result["software"] = self.check_software()

        result["resources"] = self.update_resources(progress_cb=progress_cb)
        return result


def _compare_versions(a: str, b: str) -> int:
    """比较语义版本号。a > b → 1, a == b → 0, a < b → -1。"""
    pa = [int(x) for x in a.split(".") if x.isdigit()]
    pb = [int(x) for x in b.split(".") if x.isdigit()]
    for i in range(max(len(pa), len(pb))):
        va = pa[i] if i < len(pa) else 0
        vb = pb[i] if i < len(pb) else 0
        if va > vb:
            return 1
        if va < vb:
            return -1
    return 0


if __name__ == "__main__":
    import argparse

    setup_logging("INFO")
    parser = argparse.ArgumentParser(description="更新检查")
    parser.add_argument("--check", action="store_true", help="检查软件更新")
    parser.add_argument("--resources", action="store_true", help="更新资源")
    parser.add_argument("--all", action="store_true", help="全部")
    args = parser.parse_args()

    checker = UpdateChecker()
    if args.check:
        has, ver, url = checker.check_software()
        if has:
            print(f"发现新版本: {ver}")
            print(f"下载: {url}")
    elif args.resources:
        for r in checker.update_resources():
            print(r.message)
    elif args.all:
        result = checker.update_all()
        sw = result["software"]
        if sw[0]:
            print(f"发现新版本: {sw[1]}")
            print(f"下载: {sw[2]}")
        else:
            print(f"软件已是最新版本 (v{__version__})")
        for r in result["resources"]:
            print(r.message)
    else:
        parser.print_help()
