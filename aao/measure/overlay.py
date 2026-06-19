"""无边框置顶半透明悬浮窗（PySide6）。

显示实时帧（currentFrame/total）+ 全局计时器(MM:SS:FF) + 当前 profile。
可拖拽。worker 的 ``state_changed`` 信号连到 ``on_state``。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QResizeEvent,
)
from PySide6.QtWidgets import QApplication, QLabel, QSizeGrip, QVBoxLayout, QWidget

from aao.core.timing.time_source import format_timer
from aao.ui import floating_state
from aao.ui.window_snap import (
    create_snap_follow,
    follow_from_dict,
    follow_top_left,
    register_snap_window,
    snap_top_left_with_target,
)

_WINDOW_ID = "overlay"


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowOpacity(0.82)
        self.resize(170, 72)
        self.setMinimumSize(170, 72)
        self._drag_offset = None
        self._snap_follow = None
        self._resize_save_timer = QTimer(self)
        self._resize_save_timer.setSingleShot(True)
        self._resize_save_timer.setInterval(300)
        self._resize_save_timer.timeout.connect(self._save_window_state)
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(150)
        self._follow_timer.timeout.connect(self._follow_snap_target)
        self._follow_timer.start()
        register_snap_window(self, _WINDOW_ID)

        mono = QFont("Consolas", 15)
        mono.setBold(True)
        small = QFont("Consolas", 9)

        self.frame_label = QLabel("-- / --", self)
        self.frame_label.setFont(mono)
        self.frame_label.setStyleSheet("color: #00e5ff;")

        self.timer_label = QLabel("00:00:00", self)
        self.timer_label.setFont(mono)
        self.timer_label.setStyleSheet("color: #9aa0a6;")

        self.profile_label = QLabel("(no profile)", self)
        self.profile_label.setFont(small)
        self._size_grip = QSizeGrip(self)
        self._size_grip.setFixedSize(14, 14)
        self._refresh_theme_styles()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.frame_label)
        layout.addWidget(self.timer_label)
        layout.addWidget(self.profile_label)
        self._restore_window_state()

    def _is_dark(self) -> bool:
        app = QApplication.instance()
        pal = app.palette() if isinstance(app, QApplication) else self.palette()
        return pal.color(QPalette.ColorRole.Window).lightness() < 128

    def _refresh_theme_styles(self) -> None:
        is_dark = self._is_dark()
        self.frame_label.setStyleSheet(f"color: {'#00e5ff' if is_dark else '#00789a'};")
        self.timer_label.setStyleSheet(f"color: {'#e6e6e6' if is_dark else '#1f1f1f'};")
        self.profile_label.setStyleSheet(f"color: {'#9aa0a6' if is_dark else '#6a737d'};")
        self.update()

    def _restore_window_state(self) -> None:
        floating_state.restore_geometry(_WINDOW_ID, self)
        self._snap_follow = follow_from_dict(floating_state.load_follow(_WINDOW_ID))

    def _save_window_state(self) -> None:
        floating_state.save_geometry(_WINDOW_ID, self)
        floating_state.save_follow(
            _WINDOW_ID, self._snap_follow.to_dict() if self._snap_follow is not None else None
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: D401
        self._size_grip.move(
            self.width() - self._size_grip.width(), self.height() - self._size_grip.height()
        )
        if self.isVisible():
            self._resize_save_timer.start()
        super().resizeEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        is_dark = self._is_dark()
        bg = QColor(43, 43, 43, 230) if is_dark else QColor(244, 244, 244, 230)
        border = QColor(255, 255, 255, 55) if is_dark else QColor(0, 0, 0, 55)
        painter.fillPath(path, bg)
        painter.setPen(border)
        painter.drawPath(path)

    def reset_layout(self, x: int, y: int) -> None:
        self._snap_follow = None
        self.resize(170, 72)
        self.move(x, y)
        self._save_window_state()

    def on_state(self, state: dict) -> None:
        running = state.get("isRunning", False)
        cf = state.get("currentFrame")
        total = state.get("totalFramesInCycle", 0)
        if running and cf is not None:
            self.frame_label.setText(f"{cf} / {total - 1}")
        else:
            self.frame_label.setText("-- / --")
        self.timer_label.setText(format_timer(state.get("totalElapsedFrames", 0)))
        self.profile_label.setText(state.get("activeProfile") or "(no profile)")

    # --- 拖拽 ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._snap_follow = None
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            pos = event.globalPosition().toPoint() - self._drag_offset
            if not (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier):
                pos, target = snap_top_left_with_target(self, pos)
                self._snap_follow = create_snap_follow(target, pos)
            self.move(pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        self._save_window_state()

    def _follow_snap_target(self) -> None:
        if self._drag_offset is not None or self._snap_follow is None:
            return
        pos = follow_top_left(self._snap_follow)
        if pos is None:
            self._snap_follow = None
            return
        if pos != self.frameGeometry().topLeft():
            self.move(pos)
