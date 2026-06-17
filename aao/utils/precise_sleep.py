"""高精度 sleep（Windows ctypes QueryPerformanceCounter 自旋）。

PC 端帧步进需要 ~30ms 精确定时（AFA 的 ESC→sleep→Space 模式）。
Python time.sleep 在 Windows 下粒度 ~15ms，不够精确。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import sys
import time

_is_windows = sys.platform == "win32"

if _is_windows:
    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined] # pyright: ignore[reportAttributeAccessIssue]
    _qpc = _kernel32.QueryPerformanceCounter
    _qpf = _kernel32.QueryPerformanceFrequency
    _qpc.restype = wintypes.BOOL
    _qpc.argtypes = [ctypes.POINTER(ctypes.c_int64)]
    _qpf.restype = wintypes.BOOL
    _qpf.argtypes = [ctypes.POINTER(ctypes.c_int64)]

    _freq = ctypes.c_int64()
    _qpf(ctypes.byref(_freq))
    _freq_val = _freq.value
else:
    _freq_val = 0


def precise_sleep_ms(milliseconds: float) -> None:
    """高精度 sleep（毫秒）。

    Windows: QPC 自旋等待（亚毫秒精度）。
    非 Windows: 回退到 time.sleep。
    """
    if not _is_windows or _freq_val == 0:
        time.sleep(milliseconds / 1000.0)
        return

    target_ticks = int(milliseconds * _freq_val / 1000.0)
    start = ctypes.c_int64()
    _qpc(ctypes.byref(start))
    target = start.value + target_ticks

    while True:
        now = ctypes.c_int64()
        _qpc(ctypes.byref(now))
        remaining = target - now.value
        if remaining <= 0:
            break
        # 剩余 > 2ms 时用 time.sleep 让出 CPU，否则自旋
        remaining_ms = remaining * 1000.0 / _freq_val
        if remaining_ms > 2:
            time.sleep(0.001)
