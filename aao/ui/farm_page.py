"""凹图运行页：参数 + 开始/停止 + 状态 + 结果历史 + 日志面板。

后台跑 FarmWorker（QThread），进度经信号回 UI。
互斥：开始凹图时 MainWindow 置 _busy，禁用打轴热键与打轴页。

用法：由 MainWindow 实例化并注入 controller/tasker；本页只管参数与展示。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aao.core.timing.time_source import format_timer
from aao.ui import floating_state
from aao.ui.farm_worker import FarmWorker
from aao.ui.floating_log_window import FloatingLogWindow
from aao.ui.scrollbar_style import apply_themed_scrollbar
from aao.utils.runtime_paths import project_root

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent

    from aao.ui.farm_worker import RoundResult
    from aao.ui.log_handler import QtLogHandler


class FarmPage(QWidget):
    """凹图运行页。"""

    # 开始/结束凹图时通知 MainWindow 切换互斥状态
    busy_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._controller = None
        self._tasker = None
        self._worker: FarmWorker | None = None
        self._thread: QThread | None = None
        self._floating_log: FloatingLogWindow | None = None
        self._max_retries = 50
        self._build_ui()
        self._restore_floating_log_state()

    # --- UI 构建 ---

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 顶栏参数
        top = QGroupBox("参数")
        form = QFormLayout(top)

        self.cb_timeline = QComboBox()
        self._load_timelines()
        form.addRow("时间轴:", self.cb_timeline)

        diff_row = QHBoxLayout()
        self.rb_normal = QRadioButton("普通")
        self.rb_sand = QRadioButton("沙盘推演")
        self.chk_practice = QCheckBox("演习")
        self.rb_normal.setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_normal)
        g.addButton(self.rb_sand)
        diff_row.addWidget(self.rb_normal)
        diff_row.addWidget(self.rb_sand)
        diff_row.addWidget(self.chk_practice)
        diff_row.addStretch()
        form.addRow("难度:", diff_row)

        self.edit_retries = QLineEdit("50")
        self.edit_retries.setMaximumWidth(80)
        form.addRow("最大次数:", self.edit_retries)

        self.cb_profile = QComboBox()
        self.cb_profile.setEditable(True)
        self._load_profiles()
        form.addRow("profile:", self.cb_profile)

        # 控制按钮
        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ 开始凹图")
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_float_log = QPushButton("📋 悬浮日志")
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_float_log)
        btn_row.addStretch()
        form.addRow(btn_row)

        root.addWidget(top)

        # 状态区
        status_box = QGroupBox("状态")
        s = QHBoxLayout(status_box)
        self.lbl_state = QLabel("就绪")
        self.lbl_attempt = QLabel("第 0 / 0 次")
        self.lbl_round = QLabel("本轮: --")
        self.lbl_last = QLabel("上轮: --")
        for w in (self.lbl_state, self.lbl_attempt, self.lbl_round, self.lbl_last):
            s.addWidget(w)
        s.addStretch()
        root.addWidget(status_box)

        # 结果历史 + 日志（左右分栏）
        splitter = QSplitter(Qt.Orientation.Horizontal)

        hist_box = QGroupBox("结果历史")
        hist_box.setStyleSheet("QGroupBox { background: transparent; }")
        hl = QVBoxLayout(hist_box)
        self.list_history = QListWidget()
        apply_themed_scrollbar(self.list_history, "QListWidget { background: transparent; }")
        hl.addWidget(self.list_history)
        splitter.addWidget(hist_box)

        log_box = QGroupBox("日志")
        log_box.setStyleSheet("QGroupBox { background: transparent; }")
        ll = QVBoxLayout(log_box)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        apply_themed_scrollbar(self.txt_log, "QTextEdit { background: transparent; }")
        self.txt_log.document().setMaximumBlockCount(2000)
        ll.addWidget(self.txt_log)
        splitter.addWidget(log_box)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        # 信号
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)
        self.rb_sand.toggled.connect(self._update_practice_visible)
        self.btn_float_log.clicked.connect(self._show_floating_log)
        self._update_practice_visible()

    def _update_practice_visible(self) -> None:
        is_sand = self.rb_sand.isChecked()
        self.chk_practice.setVisible(not is_sand)
        if is_sand:
            self.chk_practice.setChecked(False)

    def _load_timelines(self) -> None:
        cur = self.cb_timeline.currentText()
        self.cb_timeline.clear()
        d = project_root() / "config" / "timelines"
        for p in sorted(d.glob("*.json")):
            self.cb_timeline.addItem(p.name)
        if cur:
            self.cb_timeline.setCurrentText(cur)

    def _load_profiles(self) -> None:
        cur = self.cb_profile.currentText()
        self.cb_profile.clear()
        d = project_root() / "config" / "calibration"
        for p in sorted(d.glob("*.json")):
            self.cb_profile.addItem(p.name)
        if cur:
            self.cb_profile.setCurrentText(cur)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: D401
        # 切到凹图页时刷新下拉（新加的 timeline/profile 立即可见）
        self._load_timelines()
        self._load_profiles()
        super().showEvent(event)

    # --- 注入（由 MainWindow 调用）---

    def set_runtime(self, controller: Any, tasker: Any) -> None:
        self._controller = controller
        self._tasker = tasker

    def set_log_handler(self, handler: QtLogHandler) -> None:
        """接 QtLogHandler 信号到日志面板（HTML，按日志等级着色）。"""
        handler.log_html.connect(self._append_log)

    def _append_log(self, html: str) -> None:
        self.txt_log.append(html)
        if self._floating_log is not None:
            self._floating_log.append_log(html)

    def _ensure_floating_log(self) -> FloatingLogWindow:
        if self._floating_log is None:
            # 顶层窗口不要设置 parent，否则主控台最小化时会把悬浮日志一起最小化。
            self._floating_log = FloatingLogWindow()
            self._floating_log.stop_requested.connect(self._on_stop)
        self._floating_log.set_log_html(self.txt_log.toHtml())
        self._sync_floating_status()
        return self._floating_log

    def _show_floating_log(self) -> None:
        log_win = self._ensure_floating_log()
        from aao.ui.settings_page import load_settings

        log_win.set_always_on_top(load_settings().get("floating_log_topmost", True))
        log_win.show()
        log_win.raise_()
        log_win.activateWindow()

    def _restore_floating_log_state(self) -> None:
        # 上次显示过，或保存了吸附跟随关系（例如吸附到游戏窗口/计时窗）时，启动自动恢复。
        if floating_state.load_visible("farm_log", False) or floating_state.load_follow("farm_log"):
            self._show_floating_log()

    def _sync_floating_status(self) -> None:
        if self._floating_log is None:
            return
        self._floating_log.set_status(
            self.lbl_state.text(),
            self.lbl_attempt.text(),
            self.lbl_round.text(),
            self.lbl_last.text(),
        )
        self._floating_log.set_running(self._worker is not None)

    # --- 控制 ---

    def _on_start(self) -> None:
        if self._controller is None or self._tasker is None:
            QMessageBox.warning(
                self, "未连接", "游戏窗口未连接。请到「设置」页选择窗口并设为默认。"
            )
            return
        timeline = self.cb_timeline.currentText()
        if not timeline:
            QMessageBox.warning(self, "未选时间轴", "请先选择时间轴文件。")
            return
        difficulty = "sand" if self.rb_sand.isChecked() else "normal"
        practice = self.chk_practice.isChecked() and difficulty != "sand"
        try:
            self._max_retries = int(self.edit_retries.text() or "0")
        except ValueError:
            self._max_retries = 0
        profile = self.cb_profile.currentText() or None

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_inputs_enabled(False)
        self.lbl_state.setText("运行中…")
        self.list_history.clear()
        self._round_start_time = None
        self.busy_changed.emit(True)
        from aao.ui.settings_page import load_settings

        s = load_settings()
        if s.get("floating_log_auto_show", False):
            self._show_floating_log()
        if self._floating_log is not None:
            self._floating_log.set_always_on_top(s.get("floating_log_topmost", True))
            self._sync_floating_status()

        self._worker = FarmWorker(
            self._controller,
            self._tasker,
            timeline_path=timeline,
            difficulty=difficulty,
            max_retries=self._max_retries,
            profile=profile,
            practice=practice,
        )
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.round_finished.connect(self._on_round_finished)
        self._worker.round_outcome.connect(self._on_round_outcome)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        # 线程真正退出后再清理（避免 QThread 被 GC 时仍 running → 崩溃）
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_stop(self) -> None:
        if self._worker is not None:
            self.lbl_state.setText("停止中…")
            self._sync_floating_status()
            self._worker.stop()

    def stop_and_wait(self, timeout_ms: int = 10000) -> None:
        """请求停止凹图并等待 worker 线程退出（关窗时由 MainWindow 调用）。"""
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(timeout_ms)

    def _on_round_finished(self, result: RoundResult) -> None:
        n = result.attempt_count
        outcome = getattr(result, "outcome", None) or ("漏怪" if result.leaked else "未漏怪")
        timer = format_timer(result.elapsed_frames)
        self.lbl_attempt.setText(f"第 {n} / {self._max_retries or '∞'} 次")
        self.lbl_round.setText(f"本轮: {timer}")
        self.lbl_last.setText(f"上轮: {outcome}")
        self.list_history.insertItem(0, f"#{n} {outcome}  ({timer})")
        self._sync_floating_status()

    def _on_round_outcome(self, n: int, outcome: str) -> None:
        """结算节点命中后，更新第 n 轮那行（round_finished 时还是"进行中"）。"""
        self.lbl_last.setText(f"上轮: {outcome}")
        self._sync_floating_status()
        prefix = f"#{n} "
        for i in range(self.list_history.count()):
            item = self.list_history.item(i)
            if item and item.text().startswith(prefix):
                # 保留原计时后缀，只换 outcome
                old = item.text()
                timer_part = old[old.find("  (") :] if "  (" in old else ""
                item.setText(f"#{n} {outcome}{timer_part}")
                break

    def _on_finished(self, success: bool) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_inputs_enabled(True)
        if success:
            self.lbl_state.setText("★ 三星成功！")
        else:
            self.lbl_state.setText("已停止/失败")
        self._sync_floating_status()
        self.busy_changed.emit(False)
        # 注意：不在此处置空 _worker/_thread——_thread.quit() 只是请求退出，
        # 线程可能仍在跑。由 _cleanup_thread（thread.finished 信号）在真正退出后清理，
        # 避免 QThread 被 GC 时仍 running → "Destroyed while thread is still running"。

    def _cleanup_thread(self) -> None:
        """QThread 真正退出后清理引用（由 thread.finished 触发）。"""
        if self._thread is not None:
            self._thread.wait()
        self._worker = None
        self._thread = None
        self._sync_floating_status()

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for w in (
            self.cb_timeline,
            self.rb_normal,
            self.rb_sand,
            self.chk_practice,
            self.edit_retries,
            self.cb_profile,
        ):
            w.setEnabled(enabled)

    # --- 互斥（由 MainWindow 调用）---

    def set_busy(self, busy: bool) -> None:
        """打轴侧运行时禁用本页开始按钮。"""
        self.btn_start.setEnabled(not busy)
