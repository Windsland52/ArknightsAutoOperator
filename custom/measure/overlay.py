"""无边框置顶半透明悬浮窗（PySide6）。

显示实时帧（currentFrame/total）+ 全局计时器(MM:SS:FF) + 当前 profile。
可拖拽。worker 的 ``state_changed`` 信号连到 ``on_state``。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from custom.core.timing.time_source import format_timer


class OverlayWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowOpacity(0.82)
        self.setStyleSheet("background-color: #2b2b2b;")
        self.resize(230, 96)
        self._drag_offset = None

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
        self.profile_label.setStyleSheet("color: #6b6b6b;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(self.frame_label)
        layout.addWidget(self.timer_label)
        layout.addWidget(self.profile_label)

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
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
