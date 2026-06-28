"""更新公告对话框：启动检查发现新版本时弹出，展示 release 更新日志。

参考 AFA 的 ChangelogUI：标题 + 只读正文 + 「直到下次更新前不再弹出」复选框 + 确定。
数据来自 GitHub release 的 body（markdown），用 QTextBrowser 渲染。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class ChangelogDialog(QDialog):
    """更新公告对话框。

    Args:
        version: 新版本号（不带 v 前缀）。
        notes: release body（markdown）。
        parent: 父 widget。
    """

    def __init__(self, version: str, notes: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._version = version
        self._dismissed = False  # 用户是否勾选「不再提醒此版本」

        self.setWindowTitle("更新公告")
        self.setMinimumSize(520, 460)
        self.setWindowFlag(Qt.WindowType.Window, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel(f"<h3>🆕 新版本 v{version} 已发布</h3>")
        root.addWidget(title)

        hint = QLabel("以下是本次更新的变更内容：")
        root.addWidget(hint)

        # QTextBrowser 渲染 markdown（Qt 内置 markdown 支持）；无 notes 时降级提示。
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        if notes.strip():
            self.browser.setMarkdown(notes)
        else:
            self.browser.setPlainText("（本次发布未附更新日志）")
        root.addWidget(self.browser, 1)

        self.chk_dismiss = QCheckBox("直到下次更新前不再弹出此版本公告")
        self.chk_dismiss.toggled.connect(self._on_dismiss_toggled)
        root.addWidget(self.chk_dismiss)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_open = QPushButton("打开 Release 页")
        self.btn_open.clicked.connect(self._on_open_release)
        self.btn_ok = QPushButton("确定")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_open)
        btn_row.addWidget(self.btn_ok)
        root.addLayout(btn_row)

    def _on_dismiss_toggled(self, checked: bool) -> None:
        self._dismissed = checked

    def _on_open_release(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(
            QUrl("https://github.com/Windsland52/ArknightsAutoOperator/releases/latest")
        )

    @property
    def dismissed(self) -> bool:
        """用户是否勾选「不再提醒此版本」。"""
        return self._dismissed
