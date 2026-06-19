"""左右拨动开关（单滑块 + 滑动动画）。

文字标签在开关两侧，一个圆角滑块平滑滑动到选中侧。
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class _Track(QWidget):
    """开关轨道：只画背景 + 滑块（无文字）。"""

    def __init__(self, parent: ToggleSwitch):
        super().__init__(parent)
        self._knob_ratio = 0.0  # 0.0=左, 1.0=右

    def set_knob_ratio(self, val: float) -> None:
        self._knob_ratio = max(0.0, min(1.0, val))
        self.update()

    def get_knob_ratio(self) -> float:
        return self._knob_ratio

    knob_ratio = Property(float, get_knob_ratio, set_knob_ratio)

    def paintEvent(self, _event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景圆角矩形
        bg_color = QColor("#1e5f3f") if self._knob_ratio < 0.5 else QColor("#5a3a8a")
        p.setBrush(bg_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, h // 2, h // 2)

        # 滑块
        knob_size = h - 6
        margin = 3
        travel = w - knob_size - margin * 2
        kx = margin + travel * self._knob_ratio
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(int(kx), margin, knob_size, knob_size, knob_size // 2, knob_size // 2)


class ToggleSwitch(QWidget):
    """左右二选一开关，文字在两侧，滑块带滑动动画。

    用法：
        toggle = ToggleSwitch("打轴", "对轴")
        toggle.toggled.connect(lambda is_left: ...)
        toggle.set_left(True)
    """

    toggled = Signal(bool)  # True=左，False=右

    def __init__(self, left_text: str, right_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._is_left = True

        self._track = _Track(self)
        self._track.setFixedHeight(28)
        self._track.setFixedWidth(52)

        self._left_label = QLabel(left_text)
        self._right_label = QLabel(right_text)
        self._update_labels()

        wrap = QHBoxLayout(self)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(4)
        wrap.addWidget(self._left_label)
        wrap.addWidget(self._track)
        wrap.addWidget(self._right_label)

        self._anim: QPropertyAnimation | None = None
        self._track.mousePressEvent = lambda _e: self._toggle()

    def _update_labels(self) -> None:
        """选中侧高亮（enabled + 粗体），非选中侧弱化（disabled 灰，走 palette 自适应主题）。"""
        self._left_label.setEnabled(self._is_left)
        self._right_label.setEnabled(not self._is_left)
        f_left = self._left_label.font()
        f_right = self._right_label.font()
        f_left.setBold(self._is_left)
        f_right.setBold(not self._is_left)
        self._left_label.setFont(f_left)
        self._right_label.setFont(f_right)

    def is_left(self) -> bool:
        return self._is_left

    def set_left(self, left: bool, animate: bool = True) -> None:
        if self._is_left == left:
            return
        self._is_left = left
        self._animate(left, animate)
        self._update_labels()
        self.toggled.emit(self._is_left)

    def _toggle(self) -> None:
        self._is_left = not self._is_left
        self._animate(self._is_left, True)
        self._update_labels()
        self.toggled.emit(self._is_left)

    def _animate(self, is_left: bool, animate: bool) -> None:
        target = 0.0 if is_left else 1.0
        if animate:
            if self._anim is not None:
                self._anim.stop()
            self._anim = QPropertyAnimation(self._track, b"knob_ratio")
            self._anim.setDuration(150)
            self._anim.setStartValue(self._track.get_knob_ratio())
            self._anim.setEndValue(target)
            self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._anim.start()
        else:
            self._track.set_knob_ratio(target)
