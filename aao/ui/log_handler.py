"""把 loguru 日志桥接到 Qt 信号（供 UI 日志面板显示）。

用法：
    from aao.utils.logger import add_qt_sink
    handler = QtLogHandler()
    add_qt_sink(handler.emit)          # loguru sink → handler.emit → Signal → UI
    handler.log.connect(text_edit.appendPlainText)

loguru sink 在产生日志的线程触发；经 Signal（默认 QueuedConnection）跨线程
投递到 UI 线程槽。显示格式由 logger 配置（仅 {message}，去来源）。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, SignalInstance


class _Emitter(QObject):
    """承载 Signal（loguru sink 是普通 callable，需 QObject 转 Signal 跨线程）。"""

    log = Signal(str)


class QtLogHandler:
    """loguru sink → Signal(str)。

    emit 作为 loguru sink 的 callback，接收格式化后的消息字符串，转 Signal。
    """

    def __init__(self) -> None:
        self.emitter = _Emitter()

    @property
    def log(self) -> SignalInstance:
        return self.emitter.log

    def emit(self, message: str) -> None:  # noqa: A003
        """loguru sink callback：message 是已格式化的日志文本。"""
        self.emitter.log.emit(message)
