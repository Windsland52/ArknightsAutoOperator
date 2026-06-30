"""悬浮窗吸附辅助：屏幕边缘 + AAO/游戏窗口吸附与跟随。

拖动时把候选 top-left 传入 ``snap_top_left_with_target``，返回吸附后的 top-left
以及吸附目标。调用方可用 ``create_snap_follow`` 记录跟随关系，再定时调用
``follow_top_left`` 让悬浮窗跟随目标移动。

当前支持：屏幕可用区域、已注册且可见的 AAO 窗口、settings 里保存的游戏窗口。
按住 Shift 时调用方跳过即可。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import weakref
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QWidget

_SNAP_WINDOWS: weakref.WeakSet[QWidget] = weakref.WeakSet()
_SNAP_WINDOW_IDS: weakref.WeakKeyDictionary[QWidget, str] = weakref.WeakKeyDictionary()
_SNAP_WINDOWS_BY_ID: dict[str, weakref.ReferenceType[QWidget]] = {}
_DEFAULT_THRESHOLD = 12


@dataclass
class SnapTarget:
    kind: str  # "screen" | "floating" | "main" | "game"
    rect: QRect
    target_id: str | None = None
    widget_ref: weakref.ReferenceType[QWidget] | None = None


@dataclass
class SnapFollow:
    kind: str  # "floating" | "main" | "game"
    target_id: str | None
    offset: QPoint
    widget_ref: weakref.ReferenceType[QWidget] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "offset": [self.offset.x(), self.offset.y()],
        }


def register_snap_window(widget: QWidget, window_id: str | None = None) -> None:
    _SNAP_WINDOWS.add(widget)
    if window_id:
        _SNAP_WINDOW_IDS[widget] = window_id
        _SNAP_WINDOWS_BY_ID[window_id] = weakref.ref(widget)


def snap_top_left(widget: QWidget, top_left: QPoint, threshold: int = _DEFAULT_THRESHOLD) -> QPoint:
    """兼容旧接口：只返回吸附后的 top-left。"""
    snapped, _target = snap_top_left_with_target(widget, top_left, threshold)
    return snapped


def snap_top_left_with_target(
    widget: QWidget, top_left: QPoint, threshold: int = _DEFAULT_THRESHOLD
) -> tuple[QPoint, SnapTarget | None]:
    """返回吸附后的 top-left 和吸附目标；屏幕目标不用于跟随。"""
    rect = QRect(top_left, widget.size())
    x = top_left.x()
    y = top_left.y()
    snapped_target: SnapTarget | None = None

    for target in _snap_targets(widget):
        x, snapped_x = _snap_axis_result(
            start=rect.left(),
            end=rect.right(),
            size=rect.width(),
            target_start=target.rect.left(),
            target_end=target.rect.right(),
            current=x,
            threshold=threshold,
        )
        y, snapped_y = _snap_axis_result(
            start=rect.top(),
            end=rect.bottom(),
            size=rect.height(),
            target_start=target.rect.top(),
            target_end=target.rect.bottom(),
            current=y,
            threshold=threshold,
        )
        if snapped_x or snapped_y:
            snapped_target = target
        rect.moveTopLeft(QPoint(x, y))

    return QPoint(x, y), snapped_target


def create_snap_follow(target: SnapTarget | None, top_left: QPoint) -> SnapFollow | None:
    """由吸附目标创建跟随关系；屏幕边缘不跟随。"""
    if target is None or target.kind == "screen":
        return None
    return SnapFollow(
        target.kind, target.target_id, top_left - target.rect.topLeft(), target.widget_ref
    )


def follow_from_dict(data: dict[str, object] | None) -> SnapFollow | None:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    target_id = data.get("target_id")
    offset = data.get("offset")
    if kind not in ("floating", "main", "game"):
        return None
    if target_id is not None and not isinstance(target_id, str):
        return None
    if not isinstance(offset, list | tuple):
        return None
    pair = cast(list[object] | tuple[object, ...], offset)
    if len(pair) != 2:
        return None
    try:
        point = QPoint(_to_int(pair[0]), _to_int(pair[1]))
    except (TypeError, ValueError):
        return None
    widget_ref = None
    if kind in ("floating", "main") and target_id:
        widget_ref = _SNAP_WINDOWS_BY_ID.get(target_id)
    return SnapFollow(str(kind), target_id, point, widget_ref)


def _to_int(value: object) -> int:
    if isinstance(value, str | int | float):
        return int(value)
    raise TypeError(f"invalid integer value: {value!r}")


def follow_top_left(follow: SnapFollow) -> QPoint | None:
    """根据跟随关系返回新的 top-left；目标不可用时返回 None。"""
    rect: QRect | None = None
    if follow.kind in ("floating", "main"):
        target = follow.widget_ref() if follow.widget_ref is not None else None
        if target is None and follow.target_id:
            ref = _SNAP_WINDOWS_BY_ID.get(follow.target_id)
            target = ref() if ref is not None else None
        if target is None or not target.isVisible():
            return None
        rect = target.frameGeometry()
    elif follow.kind == "game":
        rect = _saved_game_window_rect()
    if rect is None:
        return None
    return rect.topLeft() + follow.offset


def _snap_targets(widget: QWidget) -> list[SnapTarget]:
    targets: list[SnapTarget] = []
    app = QApplication.instance()
    if isinstance(app, QApplication):
        for screen in app.screens():
            targets.append(SnapTarget("screen", screen.availableGeometry()))
    for other in list(_SNAP_WINDOWS):
        if other is widget or not other.isVisible():
            continue
        target_id = _SNAP_WINDOW_IDS.get(other)
        kind = "main" if target_id == "main" else "floating"
        targets.append(SnapTarget(kind, other.frameGeometry(), target_id, weakref.ref(other)))
    game_rect = _saved_game_window_rect()
    if game_rect is not None:
        targets.append(SnapTarget("game", game_rect, "game"))
    return targets


def _saved_game_window_rect() -> QRect | None:
    """按 settings 里保存的 window_name/class 找游戏窗口矩形；非 Windows 或未找到返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        from aao.ui.settings_page import load_settings

        s = load_settings()
        name = str(s.get("window_name", ""))
        class_name = str(s.get("window_class", ""))
    except Exception:  # noqa: BLE001
        return None
    if not name:
        return None
    hwnd = _find_window(name, class_name)
    if not hwnd:
        return None
    rect = _get_window_rect(hwnd)
    return _to_qt_rect(rect) if rect is not None else None


def _find_window(name: str, class_name: str) -> int | None:
    """找窗口：精确匹配优先；失败后按 class + 标题包含「明日方舟」兜底。"""
    user32 = ctypes.windll.user32
    exact: list[int] = []
    fallback: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def enum_proc(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title_buf = ctypes.create_unicode_buffer(512)
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        user32.GetClassNameW(hwnd, class_buf, len(class_buf))
        title = title_buf.value
        cls = class_buf.value
        if title == name and (not class_name or cls == class_name):
            exact.append(hwnd)
            return False
        if class_name and cls == class_name and "明日方舟" in title:
            fallback.append(hwnd)
        elif not class_name and "明日方舟" in title:
            fallback.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    return exact[0] if exact else (fallback[0] if fallback else None)


def _get_window_rect(hwnd: int) -> QRect | None:
    user32 = ctypes.windll.user32
    rect = ctypes.wintypes.RECT()
    # DWM 扩展边界更贴近真实可见窗口；失败则回退 GetWindowRect。
    try:
        dwmapi = ctypes.windll.dwmapi
        if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            rect = ctypes.wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
    except (AttributeError, OSError):
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _to_qt_rect(rect: QRect) -> QRect:
    """Win32 物理坐标 → Qt 逻辑坐标（高 DPI 时需要），无法判断时保持原样。

    注意：物理 rect 可能仍与 Qt 的逻辑 screen 有交集（尤其 150% 缩放），不能只靠
    intersects 判断。若原 rect 没完整落在 Qt screen 内，而按 dpr 缩放后的 rect 更合理，
    则采用缩放后的 rect。
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return rect
    for screen in app.screens():
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()
        if dpr <= 1.0:
            if geo.intersects(rect):
                return rect
            continue
        scaled = QRect(
            round(rect.x() / dpr),
            round(rect.y() / dpr),
            round(rect.width() / dpr),
            round(rect.height() / dpr),
        )
        if geo.contains(rect):
            return rect
        if geo.intersects(scaled):
            return scaled
    return rect


def _snap_axis_result(
    *,
    start: int,
    end: int,
    size: int,
    target_start: int,
    target_end: int,
    current: int,
    threshold: int,
) -> tuple[int, bool]:
    candidates = [
        (abs(start - target_start), target_start),  # 左/上边贴同边
        (abs(end - target_end), target_end - size + 1),  # 右/下边贴同边
        (abs(start - target_end), target_end + 1),  # 左/上边贴目标右/下边（外侧相邻）
        (abs(end - target_start), target_start - size),  # 右/下边贴目标左/上边（外侧相邻）
    ]
    dist, snapped = min(candidates, key=lambda item: item[0])
    return (snapped, True) if dist <= threshold else (current, False)
