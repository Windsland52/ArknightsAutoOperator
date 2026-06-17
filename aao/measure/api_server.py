"""WebSocket API（ws://localhost:2606），广播测量状态。

符合 reference/ArknightsCostBarRuler-master/API.md schema：
``{isRunning, currentFrame, totalFramesInCycle, totalElapsedFrames, activeProfile}``。
第三方工具（如打轴对轴器）可连接获取实时帧/计时器。
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable

import websockets

from aao.utils.logger import logger

DEFAULT_PORT = 2606


class ApiServer:
    """在独立线程跑 WebSocket 服务器，周期广播 ``get_state()`` 返回的状态。"""

    def __init__(
        self,
        get_state: Callable[[], dict],
        host: str = "localhost",
        port: int = DEFAULT_PORT,
        rate_hz: float = 60.0,
    ):
        self.get_state = get_state
        self.host = host
        self.port = port
        self.rate_hz = rate_hz
        self._clients: set = set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="ApiServer")
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:
            logger.exception("ApiServer 异常退出")

    async def _serve(self) -> None:
        async with websockets.serve(self._handler, self.host, self.port):
            logger.info("ApiServer 监听 ws://%s:%d", self.host, self.port)
            interval = 1.0 / self.rate_hz
            while True:
                if self._clients:
                    msg = json.dumps(self.get_state())
                    websockets.broadcast(self._clients, msg)
                await asyncio.sleep(interval)

    async def _handler(self, ws) -> None:
        self._clients.add(ws)
        try:
            await ws.wait_closed()
        finally:
            self._clients.discard(ws)
