"""关于页：项目简介、Logo、链接与许可说明。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from aao import __version__
from aao.utils.runtime_paths import project_root


class AboutPage(QWidget):
    """关于页。"""

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.setSpacing(14)
        root.setContentsMargins(40, 30, 40, 30)

        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = self._load_logo()
        if not pix.isNull():
            logo.setPixmap(pix.scaledToWidth(140, Qt.TransformationMode.SmoothTransformation))
        root.addWidget(logo)

        title = QLabel(f"<h2>ArknightsAutoOperator  v{__version__}</h2>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(title)

        desc = QLabel(
            "明日方舟 PC 官方客户端自动凹图工具。<br>"
            "帧级自动操作 · 费用条计时 · 打轴对轴 · 循环重试"
        )
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        root.addWidget(desc)

        link = QLabel(
            '<a href="https://github.com/Windsland52/ArknightsAutoOperator">'
            "🔗 GitHub 项目地址</a><br>"
            "如果这个项目对你有帮助，欢迎在仓库右上角点个 Star ⭐"
        )
        link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link.setOpenExternalLinks(True)
        root.addWidget(link)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(line)

        info = QLabel(
            "仅支持 Windows / PC 官方客户端（Arknights.exe）。<br>"
            "发版包自带 ArknightsFrameAssistant(AFA, GPL-3.0)，用于暂停/步进/技能/撤退热键。<br>"
            "AFA 源码与许可见："
            '<a href="https://github.com/CloudTracey/arknights-frame-assistant">'
            "CloudTracey/arknights-frame-assistant</a>"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        info.setStyleSheet("color: #9aa0a6;")
        root.addWidget(info)

        root.addStretch()

    @staticmethod
    def _load_logo() -> QPixmap:
        for name in ("logo.png", "logo.ico"):
            p = project_root() / name
            if p.exists():
                return QPixmap(str(p))
        return QPixmap()
