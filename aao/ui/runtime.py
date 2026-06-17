"""共享运行时装配：Win32 窗口连接 + Resource/Tasker 构建。

提取自 farm.py / measure/run.py / app.py 三处重复的 _connect_win32，
供主控台与凹图 worker 复用。

调用前需先 ``Toolkit.init_option(debug_dir)`` 并 ``configure_paths()``。

窗口选择：系统里可能有多个名字含「明日方舟」的窗口（启动器/官网/客户端），
故支持按 (window_name, class_name) 精确匹配——由设置页选定后存 settings.json，
启动时优先用；未指定时 fallback 第一个匹配的（旧行为）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aao.utils.runtime_paths import project_root
from custom.registry import register_all

if TYPE_CHECKING:
    from maa.controller import Win32Controller
    from maa.tasker import Tasker

from aao.utils.logger import logger

_WINDOW_TITLE_FRAGMENT = "明日方舟"
_GAME_CLASS = "UnityWndClass"  # 明日方舟 PC 客户端窗口类名（区分浏览器/启动器等同名窗口）
_SHORT_SIDE = 720


def list_game_windows(Toolkit: Any) -> list[Any]:
    """列出所有名字含「明日方舟」的桌面窗口（DesktopWindow 列表）。"""
    wins = Toolkit.find_desktop_windows()
    return [w for w in wins if _WINDOW_TITLE_FRAGMENT in (w.window_name or "")]


def _match_window(wins: list[Any], prefer_name: str | None, prefer_class: str | None) -> Any | None:
    """选窗口：设置页指定的 (name,class) 精确匹配优先；否则按 class=UnityWndClass
    （游戏客户端特征）优先 fallback——避免浏览器标签/启动器等含「明日方舟」的
    非游戏窗口被误选。"""
    # 1. 精确匹配用户指定的窗口
    if prefer_name:
        for w in wins:
            if w.window_name == prefer_name and (
                prefer_class is None or w.class_name == prefer_class
            ):
                return w
        logger.warning(
            "未找到指定窗口 (name=%s, class=%s)，回退到自动选择", prefer_name, prefer_class
        )
    # 2. 优先游戏客户端类名（UnityWndClass）
    for w in wins:
        if w.class_name == _GAME_CLASS:
            return w
    # 3. 兜底第一个
    return wins[0] if wins else None


def connect_window(
    Toolkit: Any,
    prefer_name: str | None = None,
    prefer_class: str | None = None,
) -> Win32Controller | None:
    """连接明日方舟窗口（FramePool 截图 + PostMessageWithCursorPos 鼠标）。

    prefer_name/prefer_class：设置页选定的窗口标识，优先精确匹配。
    返回已 post_connection 的 Win32Controller，或 None。
    """
    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )

    wins = list_game_windows(Toolkit)
    if not wins:
        logger.error("未找到「%s」窗口", _WINDOW_TITLE_FRAGMENT)
        return None
    target = _match_window(wins, prefer_name, prefer_class)
    if target is None:
        return None
    logger.info("连接窗口: name=%s class=%s", target.window_name, target.class_name)
    ctrl = Win32Controller(
        target.hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        MaaWin32InputMethodEnum.PostMessage,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(_SHORT_SIDE)
    return ctrl


def connect_hwnd(Toolkit: Any, hwnd: Any) -> Win32Controller | None:
    """按 hwnd 连接窗口（用于设置页预览截图，不存偏好）。"""
    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )

    ctrl = Win32Controller(
        hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        MaaWin32InputMethodEnum.PostMessage,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(_SHORT_SIDE)
    return ctrl


def build_tasker(controller: Win32Controller) -> Tasker | None:
    """构建 Tasker：加载 base resource bundle + 注册所有 custom + bind controller。

    返回已 init 的 Tasker，或 None。
    """
    from maa.resource import Resource
    from maa.tasker import Tasker

    res = Resource()
    res.post_bundle(str(project_root() / "resource" / "base")).wait()
    register_all(res)

    tasker = Tasker()
    tasker.bind(res, controller)
    if not tasker.inited:
        logger.error("Tasker 初始化失败")
        return None
    return tasker
