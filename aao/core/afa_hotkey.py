"""AFA（明日方舟帧操小助手）热键驱动。

AFA 是独立常驻的 AutoHotkey 工具，注册全局热键完成暂停/步进/技能/撤退的复杂按键时序。
本模块不复刻 AFA 的时序，而是通过 SendInput 模拟按下 AFA 的热键，让 AFA 代为执行。

前提（来自 reference/arknights-frame-assistant-main/src/lib/hotkey_control.ahk）：
- 热键注册为 ``HotIfWinActive("ahk_exe Arknights.exe")`` → 游戏窗口必须前台激活才生效。
- 热键未加 ``$`` 屏蔽符 → SendInput 模拟的按键能触发 AFA 热键。
- 步进/暂停选中/暂停技能/暂停撤退内部检查 ``IsMouseInClient()`` →
  发这些键前真实光标必须在游戏客户区内。

AFA 默认热键（reference/.../config.ahk）：
    F          = 按下暂停（ActionPressPause → ESC 脉冲 → 游戏暂停）
    Space      = 松开暂停（ActionReleasePause → Space 脉冲 → 游戏恢复）
    R          = 前进 33ms（Action33ms → 步进 1 帧，需游戏已暂停）
    W          = 暂停选中（ActionPauseSelect → 暂停下选中光标处单位）
    S          = 单位技能（ActionSkill → 发 E，需先 W 选中）
    A          = 单位撤退（ActionRetreat → 发 Q，需先 W 选中）

技能/撤退流程：暂停态下 move_cursor 到单位 → W 选中 → S（技能）/A（撤退）。
S/A 用前台 Send 发 E/Q，暂停态有效（不同于 MAA PostMessage 的后台按键）。

注意：Action33ms 假设调用时游戏已处于暂停状态。调用方必须自行维护暂停状态机
（见 executor 的 pause invariant），不能盲目发 R。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys

_is_windows = sys.platform == "win32"

# --- AFA 默认热键（虚拟键码） ---
VK_F = 0x46
VK_SPACE = 0x20
VK_R = 0x52
VK_T = 0x54
VK_W = 0x57
VK_S = 0x53  # 单位技能（ActionSkill → 发 E）
VK_A = 0x41  # 单位撤退（ActionRetreat → 发 Q）

# 鼠标侧键（SendInput mouseData 的 wButton 值）
XBUTTON1 = 0x0001  # 后侧键 → AFA 暂停撤退
XBUTTON2 = 0x0002  # 前侧键 → AFA 暂停技能

# SendInput 常量
_INPUT_KEYBOARD = 1
_INPUT_MOUSE = 2
_KEYEVENTF_KEYUP = 0x0002
_MOUSEEVENTF_XDOWN = 0x0080
_MOUSEEVENTF_XUP = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


if _is_windows:
    _user32 = ctypes.windll.user32  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
    # SendInput
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    # SetCursorPos
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = wintypes.BOOL
    # 窗口枚举/标题
    _user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    # 窗口几何/前台
    _user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindowAsync.restype = wintypes.BOOL
    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _send_keyboard(vk: int, up: bool) -> None:
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.ki.wVk = vk
    inp.ki.dwFlags = _KEYEVENTF_KEYUP if up else 0
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _send_mouse_xbutton(button: int, up: bool) -> None:
    inp = _INPUT()
    inp.type = _INPUT_MOUSE
    inp.mi.mouseData = button
    inp.mi.dwFlags = _MOUSEEVENTF_XUP if up else _MOUSEEVENTF_XDOWN
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def tap_key(vk: int) -> None:
    """按下并松开一个键盘键（触发 AFA 热键）。"""
    if not _is_windows:
        return
    _send_keyboard(vk, up=False)
    _send_keyboard(vk, up=True)


def tap_mouse_xbutton(button: int) -> None:
    """按下并松开一个鼠标侧键（XBUTTON1/XBUTTON2）。"""
    if not _is_windows:
        return
    _send_mouse_xbutton(button, up=False)
    _send_mouse_xbutton(button, up=True)


def foreground_info() -> dict[str, object]:
    """返回当前前台窗口信息（用于诊断 AFA HotIfWinActive 是否满足）。"""
    if not _is_windows:
        return {"hwnd": None, "title": "", "pid": None, "exe": ""}
    hwnd = int(_user32.GetForegroundWindow())
    title = _window_title(hwnd)
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = _process_image_path(pid.value) if pid.value else ""
    return {"hwnd": hwnd, "title": title, "pid": int(pid.value), "exe": exe}


def is_game_foreground(hwnd: int | None = None) -> bool:
    """当前前台是否是 Arknights.exe（若给 hwnd，则还要求 hwnd 相同）。"""
    if not _is_windows:
        return False
    fg = int(_user32.GetForegroundWindow())
    if hwnd is not None and fg != hwnd:
        return False
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
    exe = _process_image_path(pid.value).lower() if pid.value else ""
    return exe.endswith("arknights.exe")


def _window_title(hwnd: int) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _process_image_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def find_game_window(title_substr: str = "明日方舟") -> int | None:
    """按窗口标题包含的字串查找游戏窗口 HWND。"""
    if not _is_windows:
        return None
    EnumWindowsProc = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    found: list[int] = []

    def _cb(hwnd: int, _lparam: int) -> int:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_substr in buf.value and _user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return 1  # 继续枚举

    _user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return found[0] if found else None


def activate(hwnd: int) -> None:
    """把游戏窗口拉到前台（AFA 热键要求游戏前台激活）。"""
    if not _is_windows:
        return
    if _user32.IsIconic(hwnd):  # 最小化则先还原
        _user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
    _user32.SetForegroundWindow(hwnd)


def client_to_screen_ratio(hwnd: int, rx: float, ry: float) -> tuple[int, int]:
    """客户区比例 (0-1) → 屏幕坐标 (px)。

    Args:
        hwnd: 游戏窗口句柄。
        rx, ry: 客户区内归一化坐标（与投影 view_pos 一致）。
    """
    rect = wintypes.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    pt = wintypes.POINT(int(rx * width), int(ry * height))
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def move_cursor(hwnd: int, rx: float, ry: float) -> None:
    """把真实光标移到游戏客户区的 (rx, ry) 比例位置（AFA IsMouseInClient 要求）。"""
    if not _is_windows:
        return
    x, y = client_to_screen_ratio(hwnd, rx, ry)
    _user32.SetCursorPos(x, y)
