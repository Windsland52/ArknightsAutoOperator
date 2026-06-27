"""软件 + 资源更新检查与自更新。

用法（CLI）：
    uv run python -m aao.resources.updater --check      # 检查软件更新
    uv run python -m aao.resources.updater --resources  # 更新资源
    uv run python -m aao.resources.updater --all        # 全部

用法（代码，从 UI 调用）：
    from aao.resources.updater import UpdateChecker
    checker = UpdateChecker()
    info = checker.check_software()         # -> ReleaseInfo | None
    checker.download_update(info, ...)      # 流式下载 zip 到临时目录
    checker.apply_update(downloaded_zip)    # 生成中转 bat，app 退出后由 bat 完成替换+重启
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aao import __version__
from aao.resources import syncer
from aao.utils.logger import logger, setup_logging
from aao.utils.runtime_paths import is_frozen, project_root

_REPO = "Windsland52/ArknightsAutoOperator"
_RELEASES_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
# 中转 bat 名：固定放 %TEMP%（不能在安装目录，否则无法重命名安装目录）。
_UPDATER_BAT = "aao_self_update.bat"
# app 退出后 bat 轮询 exe 进程退出的最长等待（秒）。正常几秒即可，给清理留余量。
_WAIT_EXIT_TIMEOUT = 30
# bat 各步骤重试次数（解压/重命名可能因文件锁短暂失败）。
_BAT_RETRIES = 10
_BAT_RETRY_SLEEP = 1


@dataclass(frozen=True, slots=True)
class AssetInfo:
    """单个 release 资产。"""

    name: str
    url: str  # browser_download_url（重定向到 CDN）
    size: int  # bytes
    content_type: str

    @property
    def is_zip(self) -> bool:
        return self.name.lower().endswith(".zip") or "zip" in self.content_type


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """一次 release 检查结果。"""

    version: str  # 不带 v 前缀
    html_url: str
    asset: AssetInfo | None  # win-x64 zip（无则 None）
    notes: str = ""

    @property
    def has_update(self) -> bool:
        return _compare_versions(self.version, __version__) > 0


# ---------------------------------------------------------------------------
# Release 检查
# ---------------------------------------------------------------------------


class UpdateChecker:
    """软件 + 资源更新检查器。"""

    def check_software(self) -> ReleaseInfo | None:
        """检查 GitHub 最新 release。

        Returns:
            ReleaseInfo（无论是否有更新都返回最新版信息）；网络失败返回 None。
        """
        try:
            req = urllib.request.Request(_RELEASES_API)
            req.add_header("User-Agent", "ArknightsAutoOperator")
            token = _settings_github_token()
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:  # noqa: BLE001
            logger.warning("检查更新失败: %s", e)
            return None

        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            return None

        asset = _pick_win_asset(data.get("assets", []))
        info = ReleaseInfo(
            version=latest,
            html_url=data.get("html_url", ""),
            asset=asset,
            notes=data.get("body", "") or "",
        )
        logger.info(
            "当前 %s → 最新 %s (%s)",
            __version__,
            latest,
            "有更新" if info.has_update else "最新",
        )
        return info

    # 旧签名兼容：返回 (has_update, version, html_url)。供未迁移的调用方使用。
    def check_software_legacy(self) -> tuple[bool, str, str]:
        info = self.check_software()
        if info is None:
            return False, "", ""
        return info.has_update, info.version, info.html_url

    # ------------------------------------------------------------------
    # 资源更新（干员名 + 地图），委托 syncer
    # ------------------------------------------------------------------

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
            {"software": ReleaseInfo | None, "resources": [SyncResult]}
        """
        result: dict = {}

        if progress_cb:
            progress_cb("检查软件更新...")
        result["software"] = self.check_software()

        result["resources"] = self.update_resources(progress_cb=progress_cb)
        return result

    # ------------------------------------------------------------------
    # 软件自更新：下载 zip + 应用
    # ------------------------------------------------------------------

    def download_update(
        self,
        info: ReleaseInfo,
        dest: Path,
        progress_cb: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> bool:
        """流式下载 release zip 到 dest。

        与 syncer._download 不同：release 包通常 80-150MB，不能一次性 read() 进内存，
        分块落盘并周期回调进度 (downloaded_bytes, total_bytes)。支持取消。

        Args:
            info: check_software 返回的 ReleaseInfo（需有 asset）。
            dest: 目标路径（建议在 %TEMP% 下）。
            progress_cb: (downloaded, total) 回调；total 为 0 表示未知长度。
            cancel: 置位后尽快中止。

        Returns:
            True 下载成功并落盘；False（取消或失败，dest 已清理）。
        """
        if info.asset is None:
            logger.error("该 release 无可下载的 win-x64 zip")
            return False

        total = info.asset.size
        if progress_cb:
            progress_cb(0, total)

        opener = _make_opener(_settings_proxy())
        token = _settings_github_token()
        for attempt in range(1, _DOWNLOAD_RETRIES + 1):
            try:
                req = _make_request(info.asset.url, token)
                with opener.open(req, timeout=60) as resp:
                    # 资产 URL 是重定向到 CDN；若 resp 没带 Content-Length（被代理剥了），
                    # 回退用 info.asset.size。
                    cl = resp.headers.get("Content-Length")
                    total = int(cl) if cl and cl.isdigit() else total
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    tmp = dest.with_suffix(dest.suffix + ".tmp")
                    downloaded = 0
                    last_report = 0
                    with tmp.open("wb") as f:
                        while True:
                            if cancel is not None and cancel.is_set():
                                f.close()
                                tmp.unlink(missing_ok=True)
                                logger.info("更新下载已取消")
                                return False
                            chunk = resp.read(_CHUNK_SIZE)
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_cb and downloaded - last_report >= _PROGRESS_REPORT_BYTES:
                                progress_cb(downloaded, total)
                                last_report = downloaded
                    tmp.replace(dest)
                    if progress_cb:
                        progress_cb(downloaded, total or downloaded)
                    logger.info("更新包下载完成: %s (%d bytes)", dest.name, downloaded)
                    return True
            except Exception as e:  # noqa: BLE001
                if attempt < _DOWNLOAD_RETRIES:
                    time.sleep(_DOWNLOAD_BACKOFF_SEC * attempt)
                    continue
                logger.warning("更新包下载失败（%d 次重试均失败）: %s", _DOWNLOAD_RETRIES, e)
                return False
        return False

    def apply_update(self, zip_path: Path) -> None:
        """生成中转 bat 并启动它，准备在 app 退出后完成替换+重启。

        流程（bat 执行，app 已退出后）：
        1. 轮询等 exe 进程退出（最长 _WAIT_EXIT_TIMEOUT 秒）
        2. 解压 zip 到临时 staging 目录
        3. 把 staging 内的顶层目录（aao.app 等）内容覆盖到安装目录，
           保留 config/ debug/ crash.log 等用户/运行时文件
        4. 重启新 exe
        5. bat 自删

        必须在 app 退出前调用：本函数只写 bat + start 它，立即返回；
        调用方随后应 QApplication.quit()/sys.exit() 让 exe 真正退出。

        仅 frozen 环境有意义；开发环境调用会记 warning 并 no-op。
        """
        if not is_frozen():
            logger.warning("apply_update 仅在打包环境可用，开发环境跳过")
            return

        install_dir = project_root()  # exe 同级目录
        exe_name = Path(sys.executable).name
        # 中转 bat 必须在安装目录之外（否则无法重命名安装目录），放 %TEMP%。
        bat_dir = Path(os.environ.get("TEMP", str(install_dir)))
        bat_path = bat_dir / _UPDATER_BAT

        zip_abs = zip_path.resolve()
        install_abs = install_dir.resolve()
        staging = bat_dir / "aao_update_staging"

        bat = _build_updater_bat(
            exe=exe_name,
            install_dir=install_abs,
            zip_path=zip_abs,
            staging=staging,
            bat_path=bat_path,
        )
        bat_path.write_text(bat, encoding="gbk", errors="replace")
        logger.info("启动自更新中转脚本: %s", bat_path)

        # /B 不开新控制台窗口；DETACHED_PROCESS 让它脱离父进程生命周期。
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.DETACHED_PROCESS | 0x08000000,  # CREATE_NO_WINDOW
            close_fds=True,
        )


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

_CHUNK_SIZE = 64 * 1024
_PROGRESS_REPORT_BYTES = 512 * 1024  # 每 512KB 回调一次进度
_DOWNLOAD_RETRIES = 3
_DOWNLOAD_BACKOFF_SEC = 1.0


def _pick_win_asset(assets: list) -> AssetInfo | None:
    """从 release assets 里选 win-x64 zip。"""
    for a in assets:
        name = str(a.get("name", "")).lower()
        if "win" in name and name.endswith(".zip"):
            return AssetInfo(
                name=a.get("name", ""),
                url=a.get("browser_download_url", ""),
                size=int(a.get("size", 0) or 0),
                content_type=a.get("content_type", ""),
            )
    return None


def _settings_proxy() -> str | None:
    """复用 syncer 的代理读取逻辑（避免循环，独立实现同语义）。"""
    try:
        path = project_root() / "config" / "settings.json"
        if not path.exists():
            return None
        proxy = json.loads(path.read_text(encoding="utf-8")).get("proxy", "")
        return str(proxy).strip() or None
    except Exception:  # noqa: BLE001
        return None


def _settings_github_token() -> str | None:
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
    if proxy:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    return urllib.request.build_opener()


def _make_request(url: str, token: str | None = None) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ArknightsAutoOperator")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


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


def _build_updater_bat(
    *,
    exe: str,
    install_dir: Path,
    zip_path: Path,
    staging: Path,
    bat_path: Path,
) -> str:
    """生成自更新中转 bat 脚本（GBK 编码，Windows cmd 默认 codepage）。

    保留文件：config/、debug/、crash.log（用户数据 + 运行时日志）。
    解压用 PowerShell Expand-Archive（Win10+ 自带）。
    每个破坏性步骤重试 _BAT_RETRIES 次，间隔 _BAT_RETRY_SLEEP 秒。
    """
    # 安装目录里不应被覆盖的用户/运行时文件（robocopy /xd /xd 排除）：
    # config/（校准+时间轴+settings）、debug/（日志）、crash.log、根级 settings.json。
    # PowerShell 解压命令（单独成变量，避免 f-string 内超长行）。
    # ps_expand 是普通字符串（非 f-string），花括号写字面 { }；随后被外层 f-string 插入。
    # %ZIP%/%STAGING% 是 bat 变量原样保留。
    ps_expand = (
        'powershell -NoProfile -Command "try { Expand-Archive '
        "-LiteralPath '%ZIP%' -DestinationPath '%STAGING%' -Force; "
        'exit 0 } catch { exit 1 }"'
    )

    # bat 里用 %~dp0 取 bat 自身所在目录不可靠（我们在 %TEMP%），全部用绝对路径。
    return f"""@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "EXE={exe}"
set "INSTALL={install_dir}"
set "ZIP={zip_path}"
set "STAGING={staging}"
set "BAT={bat_path}"

echo [aao-self-update] waiting for app to exit...
for /l %%i in (1,1,{_WAIT_EXIT_TIMEOUT}) do (
    tasklist /fi "imagename eq %EXE%" 2>nul | findstr /i "%EXE%" >nul
    if errorlevel 1 goto :exited
    timeout /t 1 /nobreak >nul
)
echo [aao-self-update] WARN: app still running after {_WAIT_EXIT_TIMEOUT}s, aborting.
goto :cleanup_self

:exited
echo [aao-self-update] app exited. extracting...
if exist "%STAGING%" rmdir /s /q "%STAGING%"

REM 重试解压
for /l %%i in (1,1,{_BAT_RETRIES}) do (
    {ps_expand}
    if not errorlevel 1 goto :extracted
    echo [aao-self-update] extract retry %%i...
    timeout /t {_BAT_RETRY_SLEEP} /nobreak >nul
)
echo [aao-self-update] ERROR: extract failed.
goto :cleanup_self

:extracted
REM release zip 用 `.` 打包，内容平铺在 zip 根（无顶层目录包裹），
REM 故解压后 staging 本身即源目录（aao.app.exe / _internal / resource / data ...）。
set "SRC=%STAGING%"

echo [aao-self-update] source: !SRC!
echo [aao-self-update] installing to %INSTALL%...

REM 用 robocopy 把 staging 镜像覆盖到安装目录。
REM - /e  含子目录（含空目录）；不 purge → 安装目录里多余文件（旧版残留）保留，不删
REM - /xd 排除名为 config/debug 的目录（用户数据 + 运行时日志，绝不动）
REM - /xf 排除 crash.log/settings.json（运行时崩溃日志 + 根级配置）
REM - /njh /njs /ndl /np 安静输出；不 /xo —— 更新场景源是新版本必须覆盖（哪怕时间戳异常）
REM - /is 强制覆盖同名文件（确保 exe 一定被替换，不靠时间戳判断）
REM robocopy 退出码 <=7 均为成功（0=无变化,1=有拷贝,...8+才算错误）。
REM /xd /xf 的排除列表单独成变量，避免超长行 + 便于阅读。
set "ROBO_EXCLUDE=/xd config debug /xf crash.log settings.json"
set "ROBO_RC=8"
set "ROBO_TRIES=0"
:robocopy_loop
set /a ROBO_TRIES+=1
robocopy "%STAGING%" "%INSTALL%" /e /is /njh /njs /ndl /np %ROBO_EXCLUDE%
set "ROBO_RC=!errorlevel!"
if !ROBO_RC! lss 8 goto :installed
if !ROBO_TRIES! lss {_BAT_RETRIES} (
    echo [aao-self-update] robocopy retry !ROBO_TRIES! (rc=!ROBO_RC!)...
    timeout /t {_BAT_RETRY_SLEEP} /nobreak >nul
    goto :robocopy_loop
)
echo [aao-self-update] ERROR: robocopy failed (rc=!ROBO_RC!).
goto :cleanup_self

:installed
echo [aao-self-update] install done (robocopy rc=!ROBO_RC!).

echo [aao-self-update] restarting app...
start "" "%INSTALL%\\%EXE%"

:cleanup_self
if exist "%STAGING%" rmdir /s /q "%STAGING%" 2>nul
del "%ZIP%" 2>nul
REM bat 自删：写一个 del 自身的命令到临时再执行
(goto) 2>nul & del "%BAT%"
"""


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
        info = checker.check_software()
        if info is None:
            print("检查更新失败")
        elif info.has_update:
            print(f"发现新版本: {info.version}")
            print(f"下载: {info.html_url}")
            if info.asset:
                print(f"资产: {info.asset.name} ({info.asset.size} bytes)")
        else:
            print(f"软件已是最新版本 (v{__version__})")
    elif args.resources:
        for r in checker.update_resources():
            print(r.message)
    elif args.all:
        result = checker.update_all()
        info = result["software"]
        if info and info.has_update:
            print(f"发现新版本: {info.version}")
            print(f"下载: {info.html_url}")
        else:
            print(f"软件已是最新版本 (v{__version__})")
        for r in result["resources"]:
            print(r.message)
    else:
        parser.print_help()
