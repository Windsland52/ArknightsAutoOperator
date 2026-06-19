"""主题感知滚动条样式。

用 widget property 记录需要刷新的控件；主题切换时 theme.py 会统一 refresh，避免
静态 QSS 在深/浅切换后卡住旧颜色。
"""

from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QWidget

_PROP_ENABLED = "aao_themed_scrollbar"
_PROP_BASE_QSS = "aao_themed_scrollbar_base"


def _is_dark(widget: QWidget) -> bool:
    app = QApplication.instance()
    pal = app.palette() if isinstance(app, QApplication) else widget.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def _scrollbar_qss(widget: QWidget) -> str:
    is_dark = _is_dark(widget)
    track = "rgba(255, 255, 255, 35)" if is_dark else "rgba(0, 0, 0, 24)"
    handle = "rgba(255, 255, 255, 90)" if is_dark else "rgba(0, 0, 0, 80)"
    hover = "rgba(255, 255, 255, 130)" if is_dark else "rgba(0, 0, 0, 120)"
    return (
        f"QScrollBar:vertical {{ background: {track}; width: 10px; margin: 0; }}"
        f"QScrollBar::handle:vertical {{ background: {handle}; border-radius: 5px; "
        "min-height: 24px; }"
        f"QScrollBar::handle:vertical:hover {{ background: {hover}; }}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { "
        "background: transparent; }"
        f"QScrollBar:horizontal {{ background: {track}; height: 10px; margin: 0; }}"
        f"QScrollBar::handle:horizontal {{ background: {handle}; border-radius: 5px; "
        "min-width: 24px; }"
        f"QScrollBar::handle:horizontal:hover {{ background: {hover}; }}"
        "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }"
        "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { "
        "background: transparent; }"
    )


def apply_themed_scrollbar(widget: QWidget, base_qss: str = "") -> None:
    """给 widget 应用主题滚动条；base_qss 保留控件自己的背景/选择等局部样式。"""
    widget.setProperty(_PROP_ENABLED, True)
    widget.setProperty(_PROP_BASE_QSS, base_qss)
    refresh_themed_scrollbar(widget)


def refresh_themed_scrollbar(widget: QWidget) -> None:
    base = widget.property(_PROP_BASE_QSS)
    widget.setStyleSheet(f"{base or ''}{_scrollbar_qss(widget)}")


def refresh_themed_scrollbars(app: QApplication) -> None:
    for widget in app.allWidgets():
        if widget.property(_PROP_ENABLED):
            refresh_themed_scrollbar(widget)
