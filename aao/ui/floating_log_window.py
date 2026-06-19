"""凹图悬浮日志窗口。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizeGrip,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aao.ui import floating_state
from aao.ui.scrollbar_style import apply_themed_scrollbar
from aao.ui.window_snap import (
    create_snap_follow,
    follow_from_dict,
    follow_top_left,
    register_snap_window,
    snap_top_left_with_target,
)

_WINDOW_ID = "farm_log"


class FloatingLogWindow(QWidget):
    """小型半透明悬浮日志：无标题栏、可拖动、关闭即隐藏，不影响凹图运行。"""

    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("凹图日志")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.82)  # 与费用/计时悬浮窗保持一致
        self.resize(360, 220)
        self.setMinimumSize(280, 160)
        self._drag_pos: QPoint | None = None
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(14, 14)
        self._resize_save_timer = QTimer(self)
        self._resize_save_timer.setSingleShot(True)
        self._resize_save_timer.setInterval(300)
        self._resize_save_timer.timeout.connect(self._save_window_state)
        self._bg_pixmap: QPixmap | None = None
        self._bg_opacity = 0.25
        self._snap_follow = None
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(150)
        self._follow_timer.timeout.connect(self._follow_snap_target)
        self._follow_timer.start()
        register_snap_window(self, _WINDOW_ID)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        self.lbl_status = QLabel("就绪  |  第 0 / 0 次  |  本轮: --  |  上轮: --")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.installEventFilter(self)
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(24, 22)
        title_row.addWidget(self.lbl_status, 1)
        title_row.addWidget(self.btn_close)
        root.addLayout(title_row)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFrameShape(QTextEdit.Shape.NoFrame)
        self.txt_log.document().setMaximumBlockCount(300)
        root.addWidget(self.txt_log, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        self.btn_stop = QPushButton("⏹ 停止")
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        root.addLayout(btn_row)

        self._refresh_theme_styles()
        self._restore_window_state()
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_close.clicked.connect(self.hide)

    def _palette(self) -> QPalette:
        app = QApplication.instance()
        return app.palette() if isinstance(app, QApplication) else self.palette()

    def _is_dark(self) -> bool:
        return self._palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _reload_background(self) -> None:
        from aao.ui.settings_page import load_settings

        s = load_settings()
        self._bg_opacity = max(0.0, min(1.0, s.get("background_opacity", 25) / 100.0))
        path = str(s.get("background_image", ""))
        if path:
            pm = QPixmap(path)
            self._bg_pixmap = pm if not pm.isNull() else None
        else:
            self._bg_pixmap = None
        self.update()

    def _refresh_theme_styles(self) -> None:
        is_dark = self._is_dark()
        text = "#e6e6e6" if is_dark else "#1f1f1f"
        btn_bg = "rgba(255, 255, 255, 45)" if is_dark else "rgba(0, 0, 0, 22)"
        btn_hover = "rgba(255, 255, 255, 70)" if is_dark else "rgba(0, 0, 0, 38)"
        self.setStyleSheet(
            f"FloatingLogWindow QLabel {{ color: {text}; }}"
            f"FloatingLogWindow QPushButton {{ color: {text}; background: {btn_bg}; "
            "border: 0; border-radius: 5px; padding: 3px 8px; }}"
            f"FloatingLogWindow QPushButton:hover {{ background: {btn_hover}; }}"
        )
        apply_themed_scrollbar(
            self.txt_log,
            f"QTextEdit {{ background: transparent; color: {text}; border: none; }}",
        )
        self._reload_background()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401
        self._size_grip.move(
            self.width() - self._size_grip.width(), self.height() - self._size_grip.height()
        )
        if self.isVisible():
            self._resize_save_timer.start()
        super().resizeEvent(event)

    def paintEvent(self, _event: QEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        painter.setClipPath(path)

        pal = self._palette()
        is_dark = self._is_dark()
        base = pal.color(QPalette.ColorRole.Window)
        base.setAlpha(205 if is_dark else 218)
        painter.fillPath(path, base)

        pm = self._bg_pixmap
        if pm is not None and not pm.isNull() and self._bg_opacity > 0:
            cw, ch = self.width(), self.height()
            iw, ih = pm.width(), pm.height()
            if iw > 0 and ih > 0:
                scale = max(cw / iw, ch / ih)
                dw, dh = iw * scale, ih * scale
                painter.setOpacity(self._bg_opacity)
                painter.drawPixmap(
                    QRectF((cw - dw) / 2, (ch - dh) / 2, dw, dh),
                    pm,
                    QRectF(0, 0, iw, ih),
                )
                painter.setOpacity(1.0)
                overlay = pal.color(QPalette.ColorRole.Window)
                overlay.setAlpha(135 if is_dark else 165)
                painter.fillPath(path, overlay)

        border = QColor(255, 255, 255, 55) if is_dark else QColor(0, 0, 0, 55)
        painter.setClipping(False)
        painter.setPen(border)
        painter.drawPath(path)

    def _restore_window_state(self) -> None:
        floating_state.restore_geometry(_WINDOW_ID, self)
        self._snap_follow = follow_from_dict(floating_state.load_follow(_WINDOW_ID))

    def _save_window_state(self) -> None:
        floating_state.save_geometry(_WINDOW_ID, self)
        floating_state.save_follow(
            _WINDOW_ID, self._snap_follow.to_dict() if self._snap_follow is not None else None
        )

    def reset_layout(self, x: int, y: int) -> None:
        self._snap_follow = None
        self.resize(360, 220)
        self.move(x, y)
        self._save_window_state()

    def set_always_on_top(self, enabled: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()

    def set_running(self, running: bool) -> None:
        self.btn_stop.setEnabled(running)

    def set_status(self, state: str, attempt: str, round_text: str, last_text: str) -> None:
        self.lbl_status.setText(f"{state}  |  {attempt}  |  {round_text}  |  {last_text}")

    def set_log_html(self, html: str) -> None:
        self.txt_log.setHtml(html)

    def append_log(self, html: str) -> None:
        self.txt_log.append(html)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.lbl_status:
            if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    self._snap_follow = None
                    self._drag_pos = (
                        event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    )
                    return True
            if event.type() == QEvent.Type.MouseMove and isinstance(event, QMouseEvent):
                if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
                    pos = event.globalPosition().toPoint() - self._drag_pos
                    if not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier):
                        pos, target = snap_top_left_with_target(self, pos)
                        self._snap_follow = create_snap_follow(target, pos)
                    self.move(pos)
                    return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_pos = None
                self._save_window_state()
        return super().eventFilter(obj, event)

    def _follow_snap_target(self) -> None:
        if self._drag_pos is not None or self._snap_follow is None:
            return
        pos = follow_top_left(self._snap_follow)
        if pos is None:
            self._snap_follow = None
            return
        if pos != self.frameGeometry().topLeft():
            self.move(pos)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        self._refresh_theme_styles()
        floating_state.save_visible(_WINDOW_ID, True)
        super().showEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: D401
        floating_state.save_visible(_WINDOW_ID, False)
        super().hideEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: D401
        event.ignore()
        floating_state.save_visible(_WINDOW_ID, False)
        self.hide()
