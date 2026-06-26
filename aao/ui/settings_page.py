"""设置页：默认 profile / WebSocket 端口 / API 开关 + 资源同步 + 更新检查。

网络操作（同步干员/地图、检查更新）放后台线程，避免卡 UI；进度/结果经信号回。
设置项持久化到 config/settings.json（运行时读取，下次启动生效）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from aao import __version__
from aao.resources.syncer import sync_all
from aao.resources.updater import UpdateChecker
from aao.ui import theme
from aao.ui.collapsible_box import CollapsibleBox
from aao.ui.scrollbar_style import apply_themed_scrollbar
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

    def __init__(self, mode: str):
        super().__init__()
        self._mode = mode  # "sync" | "check_update" | "update_all"

    def run(self) -> None:
        try:
            if self._mode == "sync":
                self.log.emit("开始同步资源（干员名 + 地图）...")
                sync_all()
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
    background_changed = Signal(str, float)  # 背景图路径 + 透明度(0-1)，即时应用

    def __init__(self):
        super().__init__()
        self._worker: _ResourceWorker | None = None
        self._thread: QThread | None = None
        self._auto_preview_done = False
        self._collapsibles: dict[str, CollapsibleBox] = {}
        self._build_ui()
        self._load()

    def _add_collapsible(
        self, root: QVBoxLayout, key: str, title: str, widget: QWidget, summary: str = ""
    ) -> CollapsibleBox:
        box = CollapsibleBox(title)
        box.set_summary(summary)
        box.add_widget(widget)
        box.toggled.connect(lambda expanded, k=key: self._save_collapsible_state(k, expanded))
        self._collapsibles[key] = box
        root.addWidget(box)
        return box

    def _save_collapsible_state(self, key: str, expanded: bool) -> None:
        s = load_settings()
        states = s.get("collapsible_sections", {})
        if not isinstance(states, dict):
            states = {}
        states[key] = expanded
        s["collapsible_sections"] = states
        save_settings(s)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        # 切到设置页自动刷新窗口列表 + profile 下拉（新校准的立即可见）
        self._refresh_windows()
        self._load_profiles()
        # 默认窗口存在时，首次进入设置页自动截一次预览；后续由用户点“刷新截图”更新
        if not self._auto_preview_done and self._selected_window() is not None:
            self._auto_preview_done = True
            self._preview_window()
        super().showEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # --- 游戏窗口选择 ---
        win_box = QGroupBox("游戏窗口（多个「明日方舟」窗口时需指定）")
        win_layout = QHBoxLayout(win_box)

        # 左侧：截图预览
        self.lbl_preview = QLabel("未预览")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(320, 180)
        self.lbl_preview.setStyleSheet("background:#1f1f1f; color:#9aa0a6; border:1px solid #444;")
        win_layout.addWidget(self.lbl_preview, 1)

        # 右侧：窗口列表 + 操作按钮 + 状态
        right = QVBoxLayout()
        self.list_windows = QListWidget()
        apply_themed_scrollbar(self.list_windows, "QListWidget { background: transparent; }")
        self.list_windows.setMinimumWidth(320)
        self.list_windows.setMaximumHeight(140)
        right.addWidget(self.list_windows)

        win_btn_row = QHBoxLayout()
        self.btn_refresh_win = QPushButton("🔄 刷新列表")
        self.btn_preview = QPushButton("📸 刷新截图")
        self.btn_save_win = QPushButton("✓ 设为默认")
        for b in (self.btn_refresh_win, self.btn_preview, self.btn_save_win):
            win_btn_row.addWidget(b)
        win_btn_row.addStretch()
        right.addLayout(win_btn_row)
        self.lbl_win_status = QLabel("未选择")
        right.addWidget(self.lbl_win_status)
        right.addStretch()

        win_layout.addLayout(right, 2)
        self._add_collapsible(root, "settings_window", "游戏窗口", win_box, "选择/预览默认游戏窗口")

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

        # --- 资源 ---
        res_box = QGroupBox("资源（干员名 / 地图）")
        rl = QVBoxLayout(res_box)
        self.lbl_res_status = QLabel(self._res_status_text())
        rl.addWidget(self.lbl_res_status)
        proxy_form = QFormLayout()
        self.edit_proxy = QLineEdit()
        self.edit_proxy.setPlaceholderText("可选，如 http://127.0.0.1:7890（资源同步/更新使用）")
        proxy_form.addRow("下载代理:", self.edit_proxy)
        token_row = QHBoxLayout()
        self.edit_github_token = QLineEdit()
        self.edit_github_token.setPlaceholderText("可选，GitHub token（本机 DPAPI 加密保存）")
        self.edit_github_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_token_eye = QPushButton("👁")
        self.btn_token_eye.setMaximumWidth(40)
        token_row.addWidget(self.edit_github_token)
        token_row.addWidget(self.btn_token_eye)
        proxy_form.addRow("GitHub Token:", token_row)
        rl.addLayout(proxy_form)
        btn_row = QHBoxLayout()
        self.btn_sync = QPushButton("🔄 同步资源")
        self.btn_check = QPushButton("🔍 检查更新")
        for b in (self.btn_sync, self.btn_check):
            btn_row.addWidget(b)
        btn_row.addStretch()
        rl.addLayout(btn_row)
        self._add_collapsible(root, "settings_resources", "资源", res_box, self._res_status_text())

        # --- 软件设置 ---
        ui_box = QGroupBox("软件设置")
        ui_form = QFormLayout(ui_box)
        self.cb_theme = QComboBox()
        self.cb_theme.addItems([theme.label(m) for m in theme.MODES])
        ui_form.addRow("主题:", self.cb_theme)

        self.cb_close_action = QComboBox()
        self.cb_close_action.addItems(["每次询问", "最小化到托盘", "退出程序"])
        ui_form.addRow("关闭窗口时:", self.cb_close_action)

        self._add_collapsible(root, "settings_software", "软件设置", ui_box, "主题 / 关闭行为")

        # --- 背景图 ---
        bg_box = QGroupBox("背景图")
        bg_form = QFormLayout(bg_box)
        self.edit_bg = QLineEdit()
        self.edit_bg.setPlaceholderText("可选，留空 = 无背景图")
        self.btn_bg_pick = QPushButton("📂 选择")
        self.btn_bg_clear = QPushButton("✕ 清除")
        bg_file_row = QHBoxLayout()
        bg_file_row.addWidget(self.edit_bg, 1)
        bg_file_row.addWidget(self.btn_bg_pick)
        bg_file_row.addWidget(self.btn_bg_clear)
        bg_form.addRow("图片:", bg_file_row)
        self.slider_bg = QSlider(Qt.Orientation.Horizontal)
        self.slider_bg.setRange(0, 100)
        self.slider_bg.setValue(25)
        self.lbl_bg_val = QLabel("25%")
        bg_op_row = QHBoxLayout()
        bg_op_row.addWidget(self.slider_bg, 1)
        bg_op_row.addWidget(self.lbl_bg_val)
        bg_form.addRow("透明度:", bg_op_row)
        self._add_collapsible(root, "settings_background", "背景图", bg_box, "设置主控台背景图片")

        root.addStretch()

        # 日志
        self.lbl_op = QLabel("")
        root.addWidget(self.lbl_op)

        # 信号
        self.btn_save.clicked.connect(self._on_save)
        self.cb_theme.currentIndexChanged.connect(self._on_theme_changed)
        self.btn_bg_pick.clicked.connect(self._on_bg_pick)
        self.btn_bg_clear.clicked.connect(self._on_bg_clear)
        self.slider_bg.valueChanged.connect(self._on_bg_opacity)
        self.btn_sync.clicked.connect(lambda: self._run_resource("sync"))
        self.btn_check.clicked.connect(lambda: self._run_resource("check_update"))
        self.btn_refresh_win.clicked.connect(self._refresh_windows)
        self.btn_preview.clicked.connect(self._preview_window)
        self.btn_save_win.clicked.connect(self._save_window)
        self.btn_token_eye.clicked.connect(self._toggle_token_visible)

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
        saved_class = saved.get("window_class")
        saved_row = -1
        for i, w in enumerate(wins):
            text = f"{w.window_name}  [类: {w.class_name}]"
            item = QListWidgetItem(text)
            if w.window_name == saved_name and (not saved_class or w.class_name == saved_class):
                # 标记当前默认
                item.setText("★ " + text)
                saved_row = i
            self.list_windows.addItem(item)
            self._windows.append(w)
        if saved_row >= 0:
            self.list_windows.setCurrentRow(saved_row)
        self.lbl_win_status.setText(f"找到 {len(wins)} 个窗口，选中后可预览/设为默认")

    def _selected_window(self):
        row = self.list_windows.currentRow()
        if row < 0 or row >= len(self._windows):
            return None
        return self._windows[row]

    def _preview_window(self) -> None:
        """对选中窗口截图，并嵌入显示在游戏窗口设置区（后台线程避免卡 UI）。"""
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

        pix = QPixmap.fromImage(qimg)
        target_w = max(320, self.lbl_preview.width() - 12)
        if pix.width() > target_w:
            pix = pix.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)
        self.lbl_preview.setPixmap(pix)
        self.lbl_preview.setText("")
        self.lbl_win_status.setText("预览已更新，确认是对的画面后可设为默认")

    def _on_preview_failed(self, msg: str) -> None:
        from PySide6.QtGui import QPixmap

        self.lbl_preview.setPixmap(QPixmap())
        self.lbl_preview.setText("预览失败")
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
        self.lbl_win_status.setText(f"已设为默认: {w.window_name}（重启生效）")
        self.settings_changed.emit()
        self.window_configured.emit()
        self._refresh_windows()  # 刷新★标记

    def _cleanup_preview_thread(self) -> None:
        if self._preview_thread is not None:
            self._preview_thread.wait()
        self._preview_worker = None
        self._preview_thread = None

    def _toggle_token_visible(self) -> None:
        if self.edit_github_token.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_github_token.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_token_eye.setText("🙈")
        else:
            self.edit_github_token.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_token_eye.setText("👁")

    def _on_theme_changed(self, idx: int) -> None:
        """主题下拉切换：即时应用 + 持久化（纯 UI 偏好，无需重启或点保存按钮）。"""
        mode = theme.MODES[idx]
        theme.apply_theme(mode)
        # 背景层是自绘的：主题切换后在下一轮事件循环刷新一次，等 Qt 完成 palette 传播。
        QTimer.singleShot(0, lambda: self._emit_background(save=False))
        s = load_settings()
        s["theme"] = mode
        save_settings(s)
        self.lbl_op.setText(f"主题已切换为：{theme.label(mode)}")

    def _on_bg_pick(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        self.edit_bg.setText(path)
        self._emit_background()

    def _on_bg_clear(self) -> None:
        self.edit_bg.clear()
        self._emit_background()

    def _on_bg_opacity(self, value: int) -> None:
        self.lbl_bg_val.setText(f"{value}%")
        self._emit_background()

    def _emit_background(self, save: bool = True) -> None:
        """即时应用背景（发信号给 MainWindow）；默认同时持久化。"""
        path = self.edit_bg.text().strip()
        opacity = self.slider_bg.value() / 100.0
        self.background_changed.emit(path, opacity)
        if not save:
            return
        s = load_settings()
        s["background_image"] = path
        s["background_opacity"] = self.slider_bg.value()
        save_settings(s)

    def _load(self) -> None:
        s = load_settings()
        # 主题：静默选中（启动时已由 app.py 应用，这里只同步下拉），切换时才即时生效
        self.cb_theme.blockSignals(True)
        mode = s.get("theme", theme.AUTO)
        self.cb_theme.setCurrentIndex(theme.MODES.index(mode) if mode in theme.MODES else 0)
        self.cb_theme.blockSignals(False)
        # 背景图：同步控件（启动时由 app.py 应用，这里静默，切换时才 emit）
        self.edit_bg.setText(s.get("background_image", ""))
        self.slider_bg.blockSignals(True)
        self.slider_bg.setValue(int(s.get("background_opacity", 25)))
        self.slider_bg.blockSignals(False)
        self.lbl_bg_val.setText(f"{self.slider_bg.value()}%")
        states = s.get("collapsible_sections", {})
        if isinstance(states, dict):
            for key, box in self._collapsibles.items():
                if key in states:
                    box.set_expanded(bool(states[key]))
        if s.get("profile"):
            self.cb_profile.setCurrentText(s["profile"])
        if s.get("port"):
            self.edit_port.setText(str(s["port"]))
        self.chk_api.setChecked(s.get("api", True))
        close_map = {"": 0, "minimize": 1, "exit": 2}
        self.cb_close_action.setCurrentIndex(close_map.get(s.get("close_action", ""), 0))
        if s.get("proxy"):
            self.edit_proxy.setText(str(s["proxy"]))
        if s.get("github_token_enc"):
            try:
                from aao.utils.secure_store import decrypt_text

                self.edit_github_token.setText(decrypt_text(str(s["github_token_enc"])))
            except Exception:  # noqa: BLE001
                self.edit_github_token.clear()
                self.lbl_op.setText("GitHub Token 解密失败，可重新填写")

    def _on_save(self) -> None:
        try:
            port = int(self.edit_port.text())
        except ValueError:
            QMessageBox.warning(self, "端口无效", "WebSocket 端口必须是整数")
            return
        data = load_settings()
        data.update(
            {
                "profile": self.cb_profile.currentText(),
                "port": port,
                "api": self.chk_api.isChecked(),
                "proxy": self.edit_proxy.text().strip(),
                "close_action": ["", "minimize", "exit"][self.cb_close_action.currentIndex()],
            }
        )
        token = self.edit_github_token.text().strip()
        if token:
            try:
                from aao.utils.secure_store import encrypt_text

                data["github_token_enc"] = encrypt_text(token)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Token 保存失败", f"GitHub Token 加密失败：{e}")
                return
        else:
            data.pop("github_token_enc", None)
        save_settings(data)
        self.lbl_op.setText("设置已保存（端口/profile/代理/Token 变更需重启或下次同步生效）")
        self.settings_changed.emit()

    def _run_resource(self, mode: str) -> None:
        if self._thread is not None:
            self.lbl_op.setText("上一次操作还在进行中…")
            return
        self._set_res_buttons(False)
        self.lbl_op.setText("进行中…")
        self._worker = _ResourceWorker(mode)
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
        for b in (self.btn_sync, self.btn_check):
            b.setEnabled(enabled)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._worker = None
        self._thread = None
