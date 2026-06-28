"""AFA（明日方舟帧操小助手）进程管理：检测 + 拉起自带 AFA.exe。

AFA 是独立常驻 AutoHotkey 工具，注册全局热键完成暂停/步进/技能/撤退时序。
executor.afa_hotkey 通过 SendInput 模拟按键触发 AFA 热键——故 AFA 必须先运行。

本模块：
- is_afa_running()：查 AFA.exe 进程是否在
- ensure_afa()：没在则拉起自带的 AFA.exe（打包在 exe 同级 afa/AFA.exe）

AFA 用默认热键配置（F/Space/R/W/S/A），与 afa_hotkey 假设一致。
AFA 是 GPL-3.0（CloudTracey/arknights-frame-assistant），自带分发见 README 许可声明。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from aao.utils.logger import logger
from aao.utils.runtime_paths import project_root

_AFA_PROCESS = "AFA.exe"  # AFA 进程名（编译后）
_AFA_REL_PATH = "afa" / Path(_AFA_PROCESS)  # 相对项目根/exe 同级


def _afa_exe_path() -> Path:
    """自带 AFA.exe 路径（项目根 或 exe 同级的 afa/AFA.exe）。"""
    return project_root() / _AFA_REL_PATH


def is_afa_running() -> bool:
    """AFA.exe 进程是否在运行。非 Windows 返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        # 用 CreateToolhelp32Snapshot 枚举进程，避免依赖 tasklist/PowerShell
        # 简单起见用 tasklist（Windows 自带）
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_AFA_PROCESS}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        running = _AFA_PROCESS.lower() in out.stdout.lower()
        # 引用 ctypes 防止 pyright 报未用（保留 Windows 判断接口）
        _ = ctypes
        return running
    except (OSError, subprocess.SubprocessError):
        logger.warning("无法检测 AFA 进程，假定未运行")
        return False


def ensure_afa() -> bool:
    """确保 AFA 在运行：没在则拉起自带 AFA.exe（后台、无窗口阻塞）。

    Returns:
        True 若 AFA 已在运行 或 成功拉起；False 若拉起失败。
    """
    if is_afa_running():
        logger.info("AFA 已在运行")
        return True

    exe = _afa_exe_path()
    if not exe.exists():
        logger.warning("未找到自带 AFA.exe（%s），请手动启动 AFA 或重新安装", exe)
        return False

    try:
        # 后台启动 AFA（ detached，不随本进程退出而终止）
        # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS（Windows）
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # noqa: S107
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
        logger.info("已拉起自带 AFA： %s", exe)
        return True
    except (OSError, subprocess.SubprocessError):
        logger.exception("拉起 AFA 失败")
        return False


def stop_afa(timeout: float = 5.0) -> bool:
    """停止自带 AFA 进程（自更新前调用，避免 afa/AFA.exe 被占用导致 robocopy 跳过覆盖）。

    AFA 是 detached 独立进程，aao.app 退出不会带走它；若更新时不先停掉，
    robocopy 覆盖 afa/AFA.exe 会因文件占用而静默跳过（rc 仍 <8 不报错），
    用户拿到旧 AFA。

    Returns:
        True 若已无 AFA 进程（本就没跑或已成功停止）；False 若超时仍在。
    """
    if not is_afa_running():
        return True
    if sys.platform != "win32":
        return False
    try:
        # taskkill /F /IM 精确按进程名杀；/T 连带子进程。
        subprocess.run(
            ["taskkill", "/F", "/T", "/IM", _AFA_PROCESS],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("停止 AFA 失败")
        return False

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_afa_running():
            logger.info("AFA 已停止（为自更新让路）")
            return True
        time.sleep(0.2)
    logger.warning("停止 AFA 超时（%ss 仍存活），更新可能跳过 afa/AFA.exe", timeout)
    return False
