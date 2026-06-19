"""把 loguru 日志桥接到 Qt 信号（供 UI 日志面板显示）。

用法：
    from aao.utils.logger import add_qt_sink
    handler = QtLogHandler()
    add_qt_sink(handler.emit)          # loguru sink → handler.emit → Signal → UI
    handler.log_html.connect(text_edit.appendHtml)

loguru sink 在产生日志的线程触发；经 Signal（默认 QueuedConnection）跨线程
投递到 UI 线程槽。显示格式由 logger 配置（仅 {message}，去来源），此处按 level
追加 HTML 颜色。
"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import QObject, Signal, SignalInstance

_LEVEL_COLORS = {
    "TRACE": "#6b7280",
    "DEBUG": "#6b7280",
    # INFO 不写死颜色，继承 QTextEdit 当前 palette 文本色（浅/深主题与背景图下都可读）。
    "INFO": "",
    "SUCCESS": "#4caf50",
    "WARNING": "#ff9800",
    "ERROR": "#f44336",
    "CRITICAL": "#ffffff;background-color:#b91c1c",
}


class _Emitter(QObject):
    """承载 Signal（loguru sink 是普通 callable，需 QObject 转 Signal 跨线程）。"""

    log_html = Signal(str)


class QtLogHandler:
    """loguru sink → Signal(str HTML)。

    emit 作为 loguru sink 的 callback，接收 loguru Message，按 level 着色后转 Signal。
    """

    def __init__(self) -> None:
        self.emitter = _Emitter()

    @property
    def log_html(self) -> SignalInstance:
        return self.emitter.log_html

    def emit(self, message: Any) -> None:  # noqa: A003
        """loguru sink callback：message 是 loguru Message（含 record）。"""
        text = str(message).rstrip("\n")
        level = "INFO"
        record = getattr(message, "record", None)
        if record:
            level = record["level"].name
        color = _LEVEL_COLORS.get(level, "")
        safe = html.escape(text)
        if color:
            self.emitter.log_html.emit(f'<span style="color:{color};">{safe}</span>')
        else:
            self.emitter.log_html.emit(f"<span>{safe}</span>")
