"""系统托盘：最小化到托盘 + 右键菜单 + 双击还原。

MainWindow 关闭时不退出，隐藏到托盘（凹图运行时不占任务栏）。
真正退出走托盘菜单「退出」或 closeEvent 的强制退出。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget


def _load_icon() -> QIcon:
    """加载 logo 图标（ico 优先，回退 png，再回退空）。"""
    root = Path(__file__).resolve().parents[2]
    for name in ("logo.ico", "logo.png"):
        p = root / name
        if p.exists():
            return QIcon(str(p))
    return QIcon()


class TrayController(QObject):
    """系统托盘控制器。"""

    quit_requested = Signal()  # 用户点「退出」
    show_requested = Signal()  # 用户点「显示」/ 双击图标

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(_load_icon(), parent)
        self._tray.setToolTip("ArknightsAutoOperator")

        menu = QMenu(parent)
        act_show = QAction("显示窗口", parent)
        act_show.triggered.connect(self.show_requested.emit)
        act_quit = QAction("退出", parent)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def show(self) -> None:
        """显示托盘图标。"""
        self._tray.show()

    def hide(self) -> None:
        """隐藏托盘图标。"""
        self._tray.hide()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # 双击/中击 → 显示窗口
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self.show_requested.emit()

    def show_message(self, title: str, msg: str) -> None:
        """弹托盘通知。"""
        self._tray.showMessage(title, msg, QSystemTrayIcon.MessageIcon.Information, 3000)
