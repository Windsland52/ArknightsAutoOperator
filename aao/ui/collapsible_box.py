"""可折叠区域组件。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleBox(QWidget):
    """标题行 + 内容区；点击标题可展开/折叠。"""

    toggled = Signal(bool)  # expanded

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._summary = ""
        self._expanded = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("collapsibleHeader")
        header_l = QHBoxLayout(self.header)
        header_l.setContentsMargins(8, 5, 8, 5)
        header_l.setSpacing(6)

        self.btn = QToolButton()
        self.btn.setAutoRaise(True)
        self.btn.setFixedWidth(18)
        self.lbl_title = QLabel(title)
        self.lbl_summary = QLabel("")
        self._refresh_theme_styles()
        self.lbl_summary.setWordWrap(False)

        header_l.addWidget(self.btn)
        header_l.addWidget(self.lbl_title)
        header_l.addWidget(self.lbl_summary, 1)
        root.addWidget(self.header)

        self.content = QWidget()
        self.content_l = QVBoxLayout(self.content)
        self.content_l.setContentsMargins(0, 4, 0, 8)
        root.addWidget(self.content)

        self.header.mousePressEvent = lambda _e: self.set_expanded(not self._expanded)
        self.btn.clicked.connect(lambda: self.set_expanded(not self._expanded))
        self.setStyleSheet(
            "#collapsibleHeader { background: rgba(127, 127, 127, 22); border-radius: 6px; }"
        )
        self.set_expanded(True)

    def add_widget(self, widget: QWidget) -> None:
        self.content_l.addWidget(widget)

    def set_summary(self, summary: str) -> None:
        self._summary = summary
        self._update_header()

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.content.setVisible(expanded)
        self._update_header()
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def _refresh_theme_styles(self) -> None:
        app = QApplication.instance()
        pal = app.palette() if isinstance(app, QApplication) else self.palette()
        is_dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        summary_color = "#c7cbd1" if is_dark else "#4f5965"
        self.lbl_title.setStyleSheet("font-weight: bold;")
        self.lbl_summary.setStyleSheet(f"color: {summary_color}; font-weight: 500;")

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange:
            self._refresh_theme_styles()
        super().changeEvent(event)

    def _update_header(self) -> None:
        self.btn.setText("▾" if self._expanded else "▸")
        self.lbl_summary.setText("" if self._expanded else self._summary)
