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

from typing import Any

from PySide6.QtWidgets import QWidget


def load_state(window_id: str) -> dict[str, Any]:
    s = _load_settings()
    data = s.get("floating_windows", {})
    item = data.get(window_id, {}) if isinstance(data, dict) else {}
    return item if isinstance(item, dict) else {}


def save_state(window_id: str, patch: dict[str, Any]) -> None:
    s = _load_settings()
    windows = s.get("floating_windows", {})
    if not isinstance(windows, dict):
        windows = {}
    item = windows.get(window_id, {})
    if not isinstance(item, dict):
        item = {}
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
    x, y, w, h = [int(v) for v in g]
    widget.setGeometry(x, y, w, h)


def save_visible(window_id: str, visible: bool) -> None:
    save_state(window_id, {"visible": visible})


def load_visible(window_id: str, default: bool = False) -> bool:
    return bool(load_state(window_id).get("visible", default))


def save_follow(window_id: str, follow: dict[str, Any] | None) -> None:
    save_state(window_id, {"follow": follow})


def load_follow(window_id: str) -> dict[str, Any] | None:
    value = load_state(window_id).get("follow")
    return value if isinstance(value, dict) else None


def _valid_geometry(value: object) -> bool:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return False
    try:
        _x, _y, w, h = [int(v) for v in value]
    except (TypeError, ValueError):
        return False
    return w > 0 and h > 0


def _load_settings() -> dict[str, Any]:
    from aao.ui.settings_page import load_settings

    return load_settings()


def _save_settings(data: dict[str, Any]) -> None:
    from aao.ui.settings_page import save_settings

    save_settings(data)
