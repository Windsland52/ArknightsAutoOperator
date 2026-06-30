"""悬浮窗位置/可见性/吸附跟随状态持久化。

统一写入 settings.json 的 ``floating_windows``：

{
  "floating_windows": {
    "overlay": {"geometry": [x, y, w, h], "visible": true, "follow": {...}},
    "farm_log": {...}
  }
}
"""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtWidgets import QWidget


def clear_all() -> None:
    s = _load_settings()
    s.pop("floating_windows", None)
    _save_settings(s)


def load_state(window_id: str) -> dict[str, Any]:
    s = _load_settings()
    data = s.get("floating_windows", {})
    windows = cast(dict[str, Any], data) if isinstance(data, dict) else {}
    item = windows.get(window_id, {})
    return cast(dict[str, Any], item) if isinstance(item, dict) else {}


def save_state(window_id: str, patch: dict[str, Any]) -> None:
    s = _load_settings()
    windows = s.get("floating_windows", {})
    if not isinstance(windows, dict):
        windows = {}
    windows = cast(dict[str, Any], windows)
    item = windows.get(window_id, {})
    if not isinstance(item, dict):
        item = {}
    item = cast(dict[str, Any], item)
    item.update(patch)
    windows[window_id] = item
    s["floating_windows"] = windows
    _save_settings(s)


def save_geometry(window_id: str, widget: QWidget) -> None:
    g = widget.frameGeometry()
    save_state(window_id, {"geometry": [g.x(), g.y(), g.width(), g.height()]})


def restore_geometry(window_id: str, widget: QWidget) -> None:
    g = load_state(window_id).get("geometry")
    if not _valid_geometry(g):
        return
    if not isinstance(g, list | tuple):
        return
    values = [_to_int(v) for v in cast(list[object] | tuple[object, ...], g)]
    x, y, w, h = values
    widget.setGeometry(x, y, w, h)


def save_visible(window_id: str, visible: bool) -> None:
    save_state(window_id, {"visible": visible})


def load_visible(window_id: str, default: bool = False) -> bool:
    return bool(load_state(window_id).get("visible", default))


def save_follow(window_id: str, follow: dict[str, Any] | None) -> None:
    save_state(window_id, {"follow": follow})


def load_follow(window_id: str) -> dict[str, Any] | None:
    value = load_state(window_id).get("follow")
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _valid_geometry(value: object) -> bool:
    if not isinstance(value, list | tuple):
        return False
    seq = cast(list[object] | tuple[object, ...], value)
    if len(seq) != 4:
        return False
    try:
        values = [_to_int(v) for v in seq]
        _x, _y, w, h = values
    except (TypeError, ValueError):
        return False
    return w > 0 and h > 0


def _to_int(value: object) -> int:
    if isinstance(value, str | int | float):
        return int(value)
    raise TypeError(f"invalid integer value: {value!r}")


def _load_settings() -> dict[str, Any]:
    from aao.ui.settings_page import load_settings

    return load_settings()


def _save_settings(data: dict[str, Any]) -> None:
    from aao.ui.settings_page import save_settings

    save_settings(data)
