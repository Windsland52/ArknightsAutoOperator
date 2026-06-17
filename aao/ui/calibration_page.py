"""校准页：费用条校准 UI。

校准流程（calibration.calibrate）是阻塞采集循环，放后台线程跑，进度经信号回。
capture_fn 用 controller.post_screencap 截图。

前置条件（用户须手动满足）：
- 游戏进入关卡、选中干员进入子弹时间（费用条缓慢循环可见）
- controller 已连接

校准完调 calibration.save() 存 profile，文件名含帧数+分辨率。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from aao import config
from aao.core.timing import calibration
from aao.utils.logger import logger

if TYPE_CHECKING:
    from aao.core.timing.calibration import FullCalibrationData


class _CalibWorker(QObject):
    """后台跑 calibration.calibrate。"""

    progress = Signal(float)  # 0.0-1.0
    log = Signal(str)
    finished_ok = Signal(object)  # FullCalibrationData
    failed = Signal(str)

    def __init__(self, controller: Any, num_cycles: int):
        super().__init__()
        self._controller = controller
        self._num_cycles = num_cycles
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        def capture():
            try:
                return self._controller.post_screencap().wait().get()
            except Exception:  # noqa: BLE001
                return None

        try:
            self.log.emit(f"开始校准（目标 {self._num_cycles} 周期）...")
            data = calibration.calibrate(
                capture_fn=capture,
                num_cycles=self._num_cycles,
                progress_cb=lambda p: self.progress.emit(p),
                cancel_cb=lambda: self._cancel,
            )
            self.log.emit(
                f"校准完成：{len(data.profiles)} 档 profile ({data.detection_mode})，"
                f"{data.screen_width}x{data.screen_height}"
            )
            self.finished_ok.emit(data)
        except Exception as e:  # noqa: BLE001
            logger.exception("校准失败")
            self.failed.emit(str(e))


class CalibrationPage(QWidget):
    """费用条校准页。"""

    # 校准开始/结束通知 MainWindow 暂停/恢复 measure worker（避免抢截图）
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self._controller = None
        self._worker: _CalibWorker | None = None
        self._thread: QThread | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # 前置条件提示
        tip = QGroupBox("前置条件")
        tl = QVBoxLayout(tip)
        tl.addWidget(QLabel("1. 连接游戏窗口（主控台启动时已连）"))
        tl.addWidget(QLabel("2. 进入关卡，选中干员进入子弹时间（费用条缓慢循环可见）"))
        tl.addWidget(QLabel("3. 保持费用条在画面内，不要遮挡"))
        tl.addWidget(QLabel("4. 校准与悬浮窗测量共用截图，建议校准期间不要同时跑凹图"))
        root.addWidget(tip)

        # 参数
        param = QGroupBox("参数")
        form = QFormLayout(param)
        self.edit_cycles = QLineEdit(str(config.DEFAULT_NUM_CYCLES))
        self.edit_cycles.setMaximumWidth(60)
        form.addRow("采集周期数:", self.edit_cycles)

        self.edit_name = QLineEdit("test")
        self.edit_name.setPlaceholderText("保存文件名前缀（自动补 _<帧>f_<分辨率>.json）")
        form.addRow("保存名前缀:", self.edit_name)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ 开始校准")
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setEnabled(False)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        form.addRow(btn_row)
        root.addWidget(param)

        # 进度
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFormat("%p%")
        root.addWidget(self.progress)

        # 日志
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(160)
        root.addWidget(self.txt_log)
        root.addStretch()

        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop.clicked.connect(self._on_stop)

    def set_runtime(self, controller: Any) -> None:
        self._controller = controller

    def _on_start(self) -> None:
        if self._controller is None:
            QMessageBox.warning(self, "未连接", "游戏窗口未连接，无法校准")
            return
        try:
            num_cycles = int(self.edit_cycles.text())
        except ValueError:
            QMessageBox.warning(self, "参数无效", "采集周期数必须是整数")
            return
        if num_cycles <= 0:
            num_cycles = config.DEFAULT_NUM_CYCLES

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.edit_cycles.setEnabled(False)
        self.edit_name.setEnabled(False)
        self.progress.setValue(0)
        self.txt_log.clear()
        self.busy_changed.emit(True)  # 暂停 measure worker，避免抢截图

        self._worker = _CalibWorker(self._controller, num_cycles)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(lambda p: self.progress.setValue(int(p * 100)))
        self._worker.log.connect(self.txt_log.append)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished_ok.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _on_stop(self) -> None:
        if self._worker is not None:
            self.txt_log.append("正在取消（等待当前采样点完成）…")
            self._worker.cancel()

    def _on_done(self, data: FullCalibrationData) -> None:
        name = self.edit_name.text().strip() or "calib"
        try:
            filename = calibration.save(data, name)
            self.txt_log.append(f"✓ 已保存: {filename}")
            QMessageBox.information(self, "校准完成", f"已保存校准: {filename}")
        except Exception as e:  # noqa: BLE001
            self.txt_log.append(f"保存失败: {e}")
            QMessageBox.warning(self, "保存失败", str(e))
        self._reset_buttons()

    def _on_failed(self, msg: str) -> None:
        if "取消" in msg:
            self.txt_log.append("已取消校准")
        else:
            self.txt_log.append(f"✗ 校准失败: {msg}")
            QMessageBox.warning(self, "校准失败", msg)
        self._reset_buttons()

    def _reset_buttons(self) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.edit_cycles.setEnabled(True)
        self.edit_name.setEnabled(True)
        self.busy_changed.emit(False)  # 恢复 measure worker

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.wait()
        self._worker = None
        self._thread = None
