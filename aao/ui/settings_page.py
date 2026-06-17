"""设置页：默认 profile / WebSocket 端口 / API 开关 + 资源同步 + 更新检查。

网络操作（同步干员/地图、检查更新）放后台线程，避免卡 UI；进度/结果经信号回。
设置项持久化到 config/settings.json（运行时读取，下次启动生效）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aao import __version__
from aao.resources.syncer import sync_all
from aao.resources.updater import UpdateChecker
from aao.utils.logger import logger
from aao.utils.runtime_paths import project_root

if TYPE_CHECKING:
    from PySide6.QtGui import QImage, QShowEvent


def _settings_path():
    return project_root() / "config" / "settings.json"


def load_settings() -> dict:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class _ResourceWorker(QObject):
    """后台跑资源同步/更新检查。"""

    log = Signal(str)
    finished_ok = Signal(str)  # 总结消息
    failed = Signal(str)

    def __init__(self, mode: str, force_remote: bool):
        super().__init__()
        self._mode = mode  # "sync" | "check_update" | "update_all"
        self._force_remote = force_remote

    def run(self) -> None:
        try:
            if self._mode == "sync":
                self.log.emit("开始同步资源（干员名 + 地图）...")
                sync_all(self._force_remote)
                self.finished_ok.emit("资源同步完成")
            elif self._mode == "check_update":
                self.log.emit("检查 GitHub 最新版本...")
                has, ver, url = UpdateChecker().check_software()
                if has:
                    self.finished_ok.emit(f"发现新版本: v{ver}\n{url}")
                else:
                    self.finished_ok.emit(f"已是最新版本 (v{__version__})")
            elif self._mode == "update_all":
                UpdateChecker().update_all(progress_cb=lambda m: self.log.emit(m))
                self.finished_ok.emit("软件检查 + 资源更新完成")
        except Exception as e:  # noqa: BLE001
            logger.exception("资源/更新操作失败")
            self.failed.emit(str(e))


class _PreviewWorker(QObject):
    """后台连指定 hwnd 截一张图，转 QPixmap 发回。"""

    got_image = Signal(object)  # QImage
    failed = Signal(str)

    def __init__(self, hwnd: Any):
        super().__init__()
        self._hwnd = hwnd

    def run(self) -> None:
        try:
            from maa.toolkit import Toolkit

            from aao.ui.runtime import connect_hwnd

            ctrl = connect_hwnd(Toolkit, self._hwnd)
            if ctrl is None:
                self.failed.emit("连接窗口失败")
                return
            img = ctrl.post_screencap().wait().get()
            if img is None:  # pyright: ignore[reportUnnecessaryComparison]  # MAA .get() 运行时可能 None（截图失败），存根未标 Optional
                self.failed.emit("截图失败")
                return
            # MAA 截图返回 numpy HxWxC(BGR)；转 QImage
            h, w = img.shape[:2]
            from PySide6.QtGui import QImage

            qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format.Format_BGR888)
            self.got_image.emit(qimg.copy())
        except Exception as e:  # noqa: BLE001
            logger.exception("预览截图失败")
            self.failed.emit(str(e))


class SettingsPage(QWidget):
    """设置页。"""

    settings_changed = Signal()  # 保存后通知 MainWindow（端口/profile 变更需重启生效）
    window_configured = Signal()  # 设为默认窗口后（新手引导用：连完窗口 → 校准）

    def __init__(self):
        super().__init__()
        self._worker: _ResourceWorker | None = None
        self._thread: QThread | None = None
        self._build_ui()
        self._load()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        # 切到设置页自动刷新窗口列表 + profile 下拉（新校准的立即可见）
        self._refresh_windows()
        self._load_profiles()
        super().showEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- 运行设置 ---
        run_box = QGroupBox("运行设置")
        form = QFormLayout(run_box)
        self.cb_profile = QComboBox()
        self.cb_profile.setEditable(True)
        self._load_profiles()
        form.addRow("默认 profile:", self.cb_profile)

        self.edit_port = QLineEdit("2606")
        self.edit_port.setMaximumWidth(80)
        form.addRow("WebSocket 端口:", self.edit_port)

        self.chk_api = QCheckBox("启动 WebSocket API（实时帧广播）")
        self.chk_api.setChecked(True)
        form.addRow(self.chk_api)

        self.btn_save = QPushButton("💾 保存设置")
        form.addRow(self.btn_save)
        root.addWidget(run_box)

        # --- 游戏窗口选择 ---
        win_box = QGroupBox("游戏窗口（多个「明日方舟」窗口时需指定）")
        wl = QVBoxLayout(win_box)
        self.list_windows = QListWidget()
        self.list_windows.setMaximumHeight(120)
        wl.addWidget(self.list_windows)
        win_btn_row = QHBoxLayout()
        self.btn_refresh_win = QPushButton("🔄 刷新列表")
        self.btn_preview = QPushButton("📸 预览截图")
        self.btn_save_win = QPushButton("✓ 设为默认")
        for b in (self.btn_refresh_win, self.btn_preview, self.btn_save_win):
            win_btn_row.addWidget(b)
        win_btn_row.addStretch()
        wl.addLayout(win_btn_row)
        self.lbl_win_status = QLabel("未选择")
        self.lbl_win_status.setStyleSheet("color: #9aa0a6;")
        wl.addWidget(self.lbl_win_status)
        root.addWidget(win_box)

        # --- 资源 ---
        res_box = QGroupBox("资源（干员名 / 地图）")
        rl = QVBoxLayout(res_box)
        self.lbl_res_status = QLabel(self._res_status_text())
        rl.addWidget(self.lbl_res_status)
        btn_row = QHBoxLayout()
        self.btn_sync = QPushButton("🔄 同步资源")
        self.btn_sync_remote = QPushButton("🌐 强制远程同步")
        self.btn_check = QPushButton("🔍 检查更新")
        for b in (self.btn_sync, self.btn_sync_remote, self.btn_check):
            btn_row.addWidget(b)
        btn_row.addStretch()
        rl.addLayout(btn_row)
        root.addWidget(res_box)

        # --- 关于 ---
        about_box = QGroupBox("关于")
        al = QVBoxLayout(about_box)
        al.addWidget(QLabel(f"ArknightsAutoOperator  v{__version__}"))
        al.addWidget(QLabel("MaaFramework 方案二 + PySide6 · 帧级自动凹图"))
        root.addWidget(about_box)
        root.addStretch()

        # 日志
        self.lbl_op = QLabel("")
        self.lbl_op.setStyleSheet("color: #9aa0a6;")
        root.addWidget(self.lbl_op)

        # 信号
        self.btn_save.clicked.connect(self._on_save)
        self.btn_sync.clicked.connect(lambda: self._run_resource("sync", False))
        self.btn_sync_remote.clicked.connect(lambda: self._run_resource("sync", True))
        self.btn_check.clicked.connect(lambda: self._run_resource("check_update", False))
        self.btn_refresh_win.clicked.connect(self._refresh_windows)
        self.btn_preview.clicked.connect(self._preview_window)
        self.btn_save_win.clicked.connect(self._save_window)

        self._windows: list = []  # DesktopWindow 列表（与 list_windows 行对应）
        self._preview_thread: QThread | None = None
        self._preview_worker: _PreviewWorker | None = None

    def _load_profiles(self) -> None:
        cur = self.cb_profile.currentText()
        self.cb_profile.clear()
        d = project_root() / "config" / "calibration"
        for p in sorted(d.glob("*.json")):
            self.cb_profile.addItem(p.name)
        if cur:
            self.cb_profile.setCurrentText(cur)

    def _res_status_text(self) -> str:
        from aao.core.geometry.map_loader import list_codes

        names_p = project_root() / "data" / "operator_names.json"
        n_oper = 0
        if names_p.exists():
            try:
                n_oper = len(json.loads(names_p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        try:
            n_maps = len(list_codes())
        except Exception:  # noqa: BLE001
            n_maps = 0
        return f"干员名: {n_oper} 个  |  地图: {n_maps} 关"

    # --- 游戏窗口选择 ---

    def _refresh_windows(self) -> None:
        """刷新候选窗口列表（名字含「明日方舟」）。"""
        from maa.toolkit import Toolkit

        self.list_windows.clear()
        self._windows = []
        try:
            from aao.ui.runtime import list_game_windows

            wins = list_game_windows(Toolkit)
        except Exception as e:  # noqa: BLE001
            self.lbl_win_status.setText(f"刷新失败: {e}")
            return
        if not wins:
            self.lbl_win_status.setText("未找到「明日方舟」窗口")
            return
        saved = load_settings()
        saved_name = saved.get("window_name")
        for w in wins:
            text = f"{w.window_name}  [类: {w.class_name}]"
            item = QListWidgetItem(text)
            if w.window_name == saved_name:
                # 标记当前默认
                item.setText("★ " + text)
            self.list_windows.addItem(item)
            self._windows.append(w)
        self.lbl_win_status.setText(f"找到 {len(wins)} 个窗口，选中后可预览/设为默认")

    def _selected_window(self):
        row = self.list_windows.currentRow()
        if row < 0 or row >= len(self._windows):
            return None
        return self._windows[row]

    def _preview_window(self) -> None:
        """对选中窗口截图，弹窗预览（后台线程避免卡 UI）。"""
        w = self._selected_window()
        if w is None:
            self.lbl_win_status.setText("请先在列表选中一个窗口")
            return
        if self._preview_thread is not None:
            self.lbl_win_status.setText("上一次预览还在进行…")
            return
        self.lbl_win_status.setText(f"正在截图预览: {w.window_name}…")
        self._preview_worker = _PreviewWorker(w.hwnd)
        self._preview_thread = QThread()
        self._preview_worker.moveToThread(self._preview_thread)
        self._preview_thread.started.connect(self._preview_worker.run)
        self._preview_worker.got_image.connect(self._show_preview)
        self._preview_worker.failed.connect(self._on_preview_failed)
        self._preview_worker.got_image.connect(self._preview_thread.quit)
        self._preview_worker.failed.connect(self._preview_thread.quit)
        self._preview_thread.finished.connect(self._cleanup_preview_thread)
        self._preview_thread.start()

    def _show_preview(self, qimg: QImage) -> None:
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("窗口截图预览")
        lbl = QLabel()
        pix = QPixmap.fromImage(qimg)
        if pix.width() > 800:  # 缩放到合理大小预览
            pix = pix.scaledToWidth(800)
        lbl.setPixmap(pix)
        lay = QVBoxLayout(dlg)
        lay.addWidget(lbl)
        self.lbl_win_status.setText("预览已生成，确认是对的画面后可设为默认")
        dlg.exec()

    def _on_preview_failed(self, msg: str) -> None:
        self.lbl_win_status.setText(f"预览失败: {msg}")

    def _save_window(self) -> None:
        """把选中窗口的 name/class 存入 settings.json（启动时优先连它）。"""
        w = self._selected_window()
        if w is None:
            self.lbl_win_status.setText("请先在列表选中一个窗口")
            return
        s = load_settings()
        s["window_name"] = w.window_name
        s["window_class"] = w.class_name
        save_settings(s)
        self.lbl_win_status.setText(
            f"已设为默认: {w.window_name}（重启生效）"
        )
        self.settings_changed.emit()
        self.window_configured.emit()
        self._refresh_windows()  # 刷新★标记

    def _cleanup_preview_thread(self) -> None:
        if self._preview_thread is not None:
            self._preview_thread.wait()
        self._preview_worker = None
        self._preview_thread = None

    def _load(self) -> None:
        s = load_settings()
        if s.get("profile"):
            self.cb_profile.setCurrentText(s["profile"])
        if s.get("port"):
            self.edit_port.setText(str(s["port"]))
        self.chk_api.setChecked(s.get("api", True))

    def _on_save(self) -> None:
        try:
            port = int(self.edit_port.text())
        except ValueError:
            QMessageBox.warning(self, "端口无效", "WebSocket 端口必须是整数")
            return
        data = {
            "profile": self.cb_profile.currentText(),
            "port": port,
            "api": self.chk_api.isChecked(),
        }
        save_settings(data)
        self.lbl_op.setText("设置已保存（端口/profile 变更需重启生效）")
        self.settings_changed.emit()

    def _run_resource(self, mode: str, force_remote: bool) -> None:
        if self._thread is not None:
            self.lbl_op.setText("上一次操作还在进行中…")
            return
        self._set_res_buttons(False)
        self.lbl_op.setText("进行中…")
        self._worker = _ResourceWorker(mode, force_remote)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self.lbl_op.setText)
        self._worker.finished_ok.connect(self._on_res_done)
        self._worker.failed.connect(self._on_res_done)
        self._worker.finished_ok.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_res_done(self, msg: str) -> None:
        self.lbl_op.setText(msg)
        self._set_res_buttons(True)
        self.lbl_res_status.setText(self._res_status_text())

    def _set_res_buttons(self, enabled: bool) -> None:
        for b in (self.btn_sync, self.btn_sync_remote, self.btn_check):
            b.setEnabled(enabled)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._worker = None
        self._thread = None
