"""通用按键 Custom action：AFA SendInput 发任意虚拟键。

maafw 的 ClickKey 走窗口消息（PostMessage/SendMessage），明日方舟 PC 端键盘不响应。
本 action 走 AFA 前台 SendInput（和 F/Space/R 同通道），可靠。

pipeline 用法：
    {"action": "Custom", "custom_action": "KeyPress",
     "custom_action_param": {"key": 86}}          // VK_V，放弃行动
     // 可选: {"key": [86, 27], "interval_ms": 30}  // 多键序列
"""

from __future__ import annotations

import json
import logging
import time

from maa.context import Context
from maa.custom_action import CustomAction

from custom.core import afa_hotkey
from custom.maa.registry import custom_action

logger = logging.getLogger(__name__)


@custom_action("KeyPress")
class KeyPressAction(CustomAction):
    """发一个或多个虚拟键（AFA SendInput）。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        raw = argv.custom_action_param
        logger.info("KeyPress 收到参数: %r", raw)
        try:
            if isinstance(raw, dict):
                params = raw
            elif isinstance(raw, str):
                params = json.loads(raw) if raw else {}
                if isinstance(params, str):  # 双重 JSON 编码
                    params = json.loads(params)
            else:
                params = {}

            key = params.get("key")
            interval_ms = int(params.get("interval_ms", 0))
            if key is None:
                logger.error("KeyPress 缺少 key，params=%r", params)
                return CustomAction.RunResult(success=False)

            keys = key if isinstance(key, list) else [key]
            for i, vk in enumerate(keys):
                if i and interval_ms:
                    time.sleep(interval_ms / 1000.0)
                afa_hotkey.tap_key(int(vk))
            logger.info("KeyPress 已发: %s", keys)
            return CustomAction.RunResult(success=True)
        except Exception:
            logger.exception("KeyPress 异常")
            return CustomAction.RunResult(success=False)
