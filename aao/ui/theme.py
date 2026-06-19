"""主题模式：自动跟随系统 / 浅色 / 深色。

统一用 Fusion style + QPalette 切换：Fusion 对调色板响应一致，深/浅两套外观可控；
原生 windowsvista style 在深色系统下硬编码绘制，无法可靠切深色。

「自动」模式按 ``QStyleHints.colorScheme()`` 选择，并监听其变化实时切换
（Windows 下 Qt 监听注册表 AppsUseLightTheme）。

需在 ``QApplication`` 创建后调用 ``apply_theme``。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# 主题模式常量（亦作 settings.json 的 "theme" 取值，顺序对应下拉框）
AUTO = "auto"
LIGHT = "light"
DARK = "dark"
MODES: tuple[str, ...] = (AUTO, LIGHT, DARK)
_LABELS = {AUTO: "自动跟随系统", LIGHT: "浅色", DARK: "深色"}

# 当前模式（auto 时由系统变化驱动；否则固定）。模块级单例——全 app 只切一份主题。
_current_mode: str = AUTO
# colorSchemeChanged 是否已连接：避免对未连接信号 disconnect 触发 libpyside RuntimeWarning。
_listening: bool = False


def label(mode: str) -> str:
    return _LABELS.get(mode, mode)


# disabled 组：背景沿用 normal（控件底色不变），前景文字用主题感知中灰，
# 保证 disabled 控件（如初始禁用的「停止」按钮）文字仍可辨——
# 若按 normal 色 darker/lighter 一刀切，深色下 disabled 底色(#686868) 与文字(#808080)
# 会糊成一片（对比度仅 ~1.3:1）。
_FG_ROLES = (
    QPalette.ColorRole.WindowText,
    QPalette.ColorRole.Text,
    QPalette.ColorRole.ButtonText,
    QPalette.ColorRole.ToolTipText,
)
_BG_ROLES = (
    QPalette.ColorRole.Window,
    QPalette.ColorRole.Base,
    QPalette.ColorRole.Button,
    QPalette.ColorRole.AlternateBase,
    QPalette.ColorRole.ToolTipBase,
)


def _finish_disabled(p: QPalette, fg_mid: QColor) -> None:
    """disabled 组：背景沿用 normal，前景文字用中灰（在深/浅背景上都 ~3.5:1 可辨）。"""
    for role in _BG_ROLES:
        p.setColor(QPalette.ColorGroup.Disabled, role, p.color(QPalette.ColorGroup.Normal, role))
    for role in _FG_ROLES:
        p.setColor(QPalette.ColorGroup.Disabled, role, fg_mid)


def dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#1f1f1f"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#e6e6e6"))
    p.setColor(QPalette.ColorRole.Base, QColor("#2b2b2b"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#333333"))
    p.setColor(QPalette.ColorRole.Text, QColor("#e6e6e6"))
    p.setColor(QPalette.ColorRole.Button, QColor("#3a3a3a"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e6e6"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9aa0a6"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2b2b2b"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e6e6"))
    p.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    p.setColor(QPalette.ColorRole.Highlight, QColor("#3d7dce"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor("#4aa3ff"))
    _finish_disabled(p, QColor("#9aa0a6"))
    return p


def light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#f4f4f4"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#1f1f1f"))
    p.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#eaeaea"))
    p.setColor(QPalette.ColorRole.Text, QColor("#1f1f1f"))
    p.setColor(QPalette.ColorRole.Button, QColor("#e8e8e8"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#1f1f1f"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#8a8a8a"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#1f1f1f"))
    p.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    p.setColor(QPalette.ColorRole.Highlight, QColor("#3d7dce"))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor("#1a73e8"))
    _finish_disabled(p, QColor("#6a737d"))
    return p


def _refresh_existing_widgets(app: QApplication, palette: QPalette) -> None:
    """同步刷新已存在控件的 palette，避免主题切换时控件外观落后一拍。

    Qt 会异步向现有 widget 分发 PaletteChange；对下拉框/表格/按钮这类控件，
    视觉上可能要到下一次交互或下一轮切换才更新。这里主动同步 palette + repolish。
    """
    from aao.ui.scrollbar_style import refresh_themed_scrollbars

    for w in app.allWidgets():
        w.setPalette(palette)
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()
    refresh_themed_scrollbars(app)


def _apply_palette(palette: QPalette) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    app.setPalette(palette)
    _refresh_existing_widgets(app, palette)


def _apply_palette_for_scheme(scheme: Qt.ColorScheme) -> None:
    _apply_palette(dark_palette() if scheme == Qt.ColorScheme.Dark else light_palette())


def _on_system_scheme_changed(scheme: Qt.ColorScheme) -> None:
    """系统深浅色变化时：仅 auto 模式跟随；用户已固定浅/深则忽略。"""
    if _current_mode == AUTO:
        _apply_palette_for_scheme(scheme)


def apply_theme(mode: str) -> None:
    """应用主题模式到 QApplication（须在 QApplication 创建后调用）。

    AUTO：按系统 colorScheme 选择并监听其变化；LIGHT/DARK：固定，忽略系统变化。
    多次调用幂等（先断开旧监听再按需重连）。
    """
    global _current_mode, _listening
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    _current_mode = mode if mode in MODES else AUTO
    app.setStyle("Fusion")

    hints = app.styleHints()
    if _listening:
        try:
            hints.colorSchemeChanged.disconnect(_on_system_scheme_changed)
        except (RuntimeError, TypeError):
            pass
        _listening = False
    if _current_mode == AUTO:
        hints.colorSchemeChanged.connect(_on_system_scheme_changed)
        _listening = True
        _apply_palette_for_scheme(hints.colorScheme())
    else:
        _apply_palette(dark_palette() if _current_mode == DARK else light_palette())
