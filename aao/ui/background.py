"""主控台背景图层：cover 裁剪绘制图片 + 可调透明度。

cover：按较大缩放比把图片放大至完全覆盖容器，超出部分裁掉（保持原比例，不拉伸变形）。
透明度：``painter.setOpacity`` 控制，0=不画（纯底色），1=完全不透明；底色取 palette
的 Window 角色，故半透明图片自然叠在主题背景上，切主题时底色跟随。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF
from PySide6.QtGui import QPainter, QPaintEvent, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


class BackgroundContainer(QWidget):
    """带背景图的容器：paintEvent 画底色 + cover 裁剪图（可调透明度）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._opacity: float = 0.25

    def set_background(self, path: str) -> None:
        """按路径加载背景图；空路径或加载失败则清除。"""
        if path:
            pm = QPixmap(path)
            self._pixmap = pm if not pm.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def changeEvent(self, event: QEvent) -> None:
        # 主题切换 → palette 变 → 自绘底色需要主动刷新；否则加载背景图时旧底色会滞留，
        # 看起来像深/浅主题没有生效，直到清除/重载图片触发 update。
        if event.type() == QEvent.Type.PaletteChange:
            self.update()
        super().changeEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        # 底色（主题背景）——直接读 QApplication palette，避免 widget 自身 PaletteChange
        # 异步传播导致背景层落后一拍（标题栏/状态栏已变，中间仍是上一主题）。
        app = QApplication.instance()
        pal = app.palette() if isinstance(app, QApplication) else self.palette()
        painter.fillRect(self.rect(), pal.color(QPalette.ColorRole.Window))
        pm = self._pixmap
        if pm is None or pm.isNull() or self._opacity <= 0:
            return
        cw, ch = self.width(), self.height()
        iw, ih = pm.width(), pm.height()
        if iw <= 0 or ih <= 0:
            return
        painter.setOpacity(self._opacity)
        # cover：取较大缩放比 → 完全覆盖；居中绘制，超出窗口部分被裁
        scale = max(cw / iw, ch / ih)
        dw, dh = iw * scale, ih * scale
        painter.drawPixmap(QRectF((cw - dw) / 2, (ch - dh) / 2, dw, dh), pm, QRectF(0, 0, iw, ih))
