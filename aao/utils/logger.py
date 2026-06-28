"""日志配置：loguru 双 sink + %-style 兼容包装。

- 用户显示 sink（控制台 + UI 日志面板）：仅 {message}，去掉来源，INFO 级
- 文件 sink：完整 {time}|{level}|{name}:{function}:{line}|{message}，DEBUG 级，
  按天轮转、保留 2 周、zip 压缩，写 debug/aao/{time}.log

loguru 原生只支持 {} 风格格式化；现有代码用 logging 的 %-style
（``logger.info("检测到 %d 个", n)``）。本模块导出的 ``logger`` 是 loguru
的薄包装，先对含 % 的消息做 %-格式化再转发，让现有调用无需改动。

各模块 ``from aao.utils.logger import logger`` 使用。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger as _loguru_logger

# 用户显示格式（控制台 + UI）：只消息，去来源
_CONSOLE_FORMAT = "{message}"
# 文件格式：完整含来源
_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"

_DEFAULT_LOG_DIR = "debug/aao"


class _PercentLogger:
    """loguru 薄包装：支持 logging 风格 ``logger.info("%s", arg)`` 的 %-格式化。

    %-格式化在转发前完成；其它方法（add/remove/opt/bind 等）透传给 loguru。
    """

    _LEVELS = ("trace", "debug", "info", "success", "warning", "error", "critical")

    def __getattr__(self, name: str) -> Any:
        return getattr(_loguru_logger, name)

    def _log(self, level: str, message: object, *args: object, **kwargs: Any) -> None:
        if args and isinstance(message, str) and "%" in message:
            try:
                message = message % args
                args = ()
            except (TypeError, ValueError):
                pass  # 格式化失败则原样交给 loguru
        # depth=2：跳过本 _log 和外层 wrapper（info/debug/...），让 loguru 的
        # name/function/line 指向真正发日志的调用处。log() 的级别名需大写。
        _loguru_logger.opt(depth=2).log(level.upper(), message, *args, **kwargs)

    def trace(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("trace", message, *args, **kwargs)

    def debug(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("debug", message, *args, **kwargs)

    def info(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("info", message, *args, **kwargs)

    def success(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("success", message, *args, **kwargs)

    def warning(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("warning", message, *args, **kwargs)

    def error(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("error", message, *args, **kwargs)

    def critical(self, message: object, *args: object, **kwargs: Any) -> None:
        self._log("critical", message, *args, **kwargs)

    def exception(self, message: object, *args: object, **kwargs: Any) -> None:
        if args and isinstance(message, str) and "%" in message:
            try:
                message = message % args
                args = ()
            except (TypeError, ValueError):
                pass
        _loguru_logger.opt(depth=2, exception=True).error(message, *args, **kwargs)


logger = _PercentLogger()


def setup_logging(level: str = "INFO", log_dir: str | Path = _DEFAULT_LOG_DIR) -> None:
    """配置 loguru（控制台 + 文件）。

    Args:
        level: 控制台/UI 显示级别（"DEBUG"/"INFO"/...）；文件始终 DEBUG。
        log_dir: 日志文件目录。
    """
    # windowed 打包(console=False)下 sys.stdout 为 None，跳过控制台 sink
    # （日志仍写文件 + UI 面板）
    has_console = sys.stdout is not None
    if has_console:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # pyright: ignore[reportAttributeAccessIssue]
        except AttributeError:
            pass

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    _loguru_logger.remove()

    # 用户显示 sink：控制台，仅消息，去来源（windowed 下无控制台则跳过）
    if has_console:
        _loguru_logger.add(
            sys.stdout,
            format=_CONSOLE_FORMAT,
            level=level,
            colorize=True,
        )

    # 文件 sink：完整含来源，DEBUG，按天轮转 + 保留 2 周 + 压缩
    _loguru_logger.add(
        str(log_path / "{time:YYYY-MM-DD}.log"),
        format=_FILE_FORMAT,
        level="DEBUG",
        rotation="00:00",
        retention="2 weeks",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )


def add_qt_sink(callback: Callable[[str], None], level: str = "INFO") -> Any:
    """给 UI 日志面板挂一个 loguru sink（仅消息，去来源）。

    callback 通常是经 Signal 转发的槽（如 QtLogHandler.emit）。
    返回 sink id，可用于 _loguru_logger.remove。
    """
    return _loguru_logger.add(callback, format=_CONSOLE_FORMAT, level=level, colorize=False)


def export_logs(dest_zip: Path) -> int:
    """把整个 debug/ 目录打成 zip（aao 日志 + maafw.log + 历史压缩包）。

    用于用户反馈/排查：一键导出全部运行时日志。当前正在写的日志文件会被
    读取后写入 zip（loguru enqueue 的文件句柄不阻止读取）。

    Args:
        dest_zip: 目标 zip 路径。

    Returns:
        打入 zip 的文件数；debug/ 不存在返回 0。
    """
    import zipfile

    from aao.utils.runtime_paths import project_root

    debug_dir = project_root() / "debug"
    if not debug_dir.exists():
        return 0

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(debug_dir.rglob("*")):
            if p.is_file():
                # zip 内相对路径保留 debug/aao/... 与 debug/maafw.log 结构
                zf.write(p, p.relative_to(debug_dir.parent))
                count += 1
    return count


__all__ = ["setup_logging", "add_qt_sink", "export_logs", "logger"]
