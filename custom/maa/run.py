"""执行器测试运行器。

注册 ExecuteTimeline custom action → 通过 pipeline 触发 → 端到端测试。

用法：
    # 前提：进战斗、暂停、选好关卡
    uv run python -m custom.maa.run --profile test_30f_1280x720.json --map 1-7
    uv run python -m custom.maa.run --profile test_30f_1280x720.json --map 1-7 --mode adb

custom_action_param 里的 timeline 从 JSON 文件加载（--timeline）或命令行指定。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from custom import config  # noqa: E402
from custom.core.timing import calibration  # noqa: E402
from custom.maa.action.executor import ExecuteTimeline  # noqa: E402
from custom.timeline.io import load_timeline  # noqa: E402
from custom.utils.logger import setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402

logger = logging.getLogger(__name__)

_ROI_ADB = {
    "X1_OFFSET_FROM_RIGHT": config.REF_WIDTH - 1740,
    "Y1_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 810,
    "Y2_OFFSET_FROM_BOTTOM": config.REF_HEIGHT - 817,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="执行器测试运行器")
    parser.add_argument("--profile", required=True, help="校准文件名")
    parser.add_argument("--map", required=True, help="关卡代号")
    parser.add_argument("--timeline", required=True, help="时间轴 JSON 文件路径")
    parser.add_argument("--mode", choices=["win32", "adb"], default="win32")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.controller import (
        AdbController,
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    # 加载时间轴
    tl = load_timeline(args.timeline)
    if not tl.actions:
        logger.error("时间轴为空")
        return 2
    logger.info("时间轴: %d 个动作 (关卡 %s)", len(tl.actions), args.map)

    # 验证校准文件
    calib = calibration.load(args.profile)
    logger.info("校准: %d 档 profile", len(calib.profiles))

    # 连接控制器
    if args.mode == "adb":
        devices = Toolkit.find_adb_devices()
        if not devices:
            logger.error("未找到 ADB 设备")
            return 2
        d = devices[0]
        ctrl = AdbController(
            adb_path=d.adb_path,
            address=d.address,
            screencap_methods=d.screencap_methods,
            input_methods=d.input_methods,
            config=d.config,
        )
        for key, val in _ROI_ADB.items():
            setattr(config, key, val)
    else:
        wins = Toolkit.find_desktop_windows()
        target = next((w for w in wins if "明日方舟" in (w.window_name or "")), None)
        if target is None:
            logger.error("未找到「明日方舟」窗口")
            return 2
        ctrl = Win32Controller(
            target.hwnd,
            MaaWin32ScreencapMethodEnum.FramePool,
            MaaWin32InputMethodEnum.PostMessage,
            MaaWin32InputMethodEnum.PostMessage,
        )
    ctrl.post_connection().wait()
    if args.mode == "win32":
        ctrl.set_screenshot_target_short_side(720)

    # 创建 Resource + 注册 custom action
    res = Resource()
    res.post_bundle(str(paths["resource"] / "base")).wait()
    res.register_custom_action("ExecuteTimeline", ExecuteTimeline())

    # 创建 Tasker
    tasker = Tasker()
    tasker.bind(res, ctrl)
    if not tasker.inited:
        logger.error("Tasker 初始化失败")
        return 2

    # 构建 custom_action_param
    param = json.dumps(
        {
            "timeline": [a.to_dict() for a in tl.actions],
            "calibration": args.profile,
            "map_code": args.map,
        },
        ensure_ascii=False,
    )

    # 通过 pipeline_override 触发
    logger.info("启动执行器...")
    detail = tasker.post_task(
        "ExecuteEntry",
        pipeline_override={
            "ExecuteEntry": {
                "recognition": "DirectHit",
                "action": "Custom",
                "custom_action": "ExecuteTimeline",
                "custom_action_param": param,
            }
        },
    ).wait()

    if detail and not detail.status.failed:
        logger.info("执行完成")
        return 0

    logger.error("执行失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
