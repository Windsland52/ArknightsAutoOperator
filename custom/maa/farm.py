"""自动凹图运行器——重试直到无漏三星通关（本地注册 Custom，不走插件调试）。

用法（管理员终端 + AFA 常驻 + 游戏停在关卡列表页）：
    uv run python -m custom.maa.farm --timeline test_1-7.json --max-retries 10
    uv run python -m custom.maa.farm --timeline test_1-7.json --difficulty sand

pipeline 定义在 resource/base/pipeline/farm.json（自循环：三星=结束，非三星/漏怪=放弃重试）。
本脚本：加载 pipeline → 注入 timeline_path + 难度 anchor → 跑 → max-retries 超时停。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from custom.core.timing import calibration  # noqa: E402
from custom.maa.reco.click_stage import get_attempt_count, reset_attempt_count  # noqa: E402
from custom.maa.registry import register_all  # noqa: E402
from custom.utils.jsonc import load as load_jsonc  # noqa: E402
from custom.utils.logger import setup_logging  # noqa: E402
from custom.utils.runtime_paths import configure_paths  # noqa: E402

logger = logging.getLogger(__name__)

# 次数监控线程的轮询间隔（秒）
_POLL_INTERVAL = 2.0


def main() -> int:
    parser = argparse.ArgumentParser(description="自动凹图运行器")
    parser.add_argument(
        "--timeline",
        required=True,
        help="时间轴文件（config/timelines/ 下文件名，如 test_1-7-2.json）",
    )
    parser.add_argument(
        "--difficulty",
        choices=["normal", "sand"],
        default="normal",
        help="难度：normal=普通 | sand=沙盘推演",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=50,
        help="最大凹图次数（0=无限，靠 Ctrl+C 停）。按实际尝试计数，不再用估时",
    )
    parser.add_argument(
        "--profile", default=None, help="校准文件名（可选，默认 config.DEFAULT_CALIBRATION）"
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    paths = configure_paths()

    from maa.controller import (
        MaaWin32InputMethodEnum,
        MaaWin32ScreencapMethodEnum,
        Win32Controller,
    )
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit

    Toolkit.init_option(str(paths["debug"]))

    if args.profile:
        calibration.load(args.profile)  # 校验可加载
    reset_attempt_count()  # 每次运行从 0 计数
    logger.info("时间轴: %s（map_code 从文件读取）", args.timeline)

    wins = Toolkit.find_desktop_windows()
    target = next((w for w in wins if "明日方舟" in (w.window_name or "")), None)
    if target is None:
        logger.error("未找到「明日方舟」窗口")
        return 2
    ctrl = Win32Controller(
        target.hwnd,
        MaaWin32ScreencapMethodEnum.FramePool,
        MaaWin32InputMethodEnum.PostMessageWithCursorPos,
        MaaWin32InputMethodEnum.PostMessage,
    )
    ctrl.post_connection().wait()
    ctrl.set_screenshot_target_short_side(720)

    res = Resource()
    res.post_bundle(str(paths["resource"] / "base")).wait()
    register_all(res)

    tasker = Tasker()
    tasker.bind(res, ctrl)
    if not tasker.inited:
        logger.error("Tasker 初始化失败")
        return 2

    # 加载 pipeline + 注入 timeline_path（ClickStage 识别 + Execute 执行共用）
    pipeline_path = paths["resource"] / "base" / "pipeline" / "farm.json"
    pipeline = load_jsonc(pipeline_path)

    tl_param = json.dumps({"timeline_path": args.timeline}, ensure_ascii=False)
    pipeline["Farm@ClickStage"]["custom_recognition_param"] = tl_param

    exec_param: dict = {"timeline_path": args.timeline}
    if args.profile:
        exec_param["calibration"] = args.profile
    pipeline["Farm@Execute"]["custom_action_param"] = json.dumps(exec_param, ensure_ascii=False)

    # 难度分支：anchor 指向普通(StartButton1) 或 沙盘(SwitchDifficulty)
    anchor_target = "Farm@SwitchDifficulty" if args.difficulty == "sand" else "Farm@StartButton1"
    pipeline["Farm"]["anchor"] = {"Farm@SwitchDifficulty": anchor_target}
    logger.info("难度：%s", "沙盘推演" if args.difficulty == "sand" else "普通")

    # max-retries 次数监控线程：累计凹图尝试达上限则 post_stop
    # 用 > 而非 >=：count 在 ClickStage 识别成功时自增（约每轮一次），
    # 故 count > max_retries 意味着已发起 max_retries 次完整尝试、第 N+1 次刚开始，此时停。
    if args.max_retries:

        def _count_stop() -> None:
            while not tasker.stopping:
                time.sleep(_POLL_INTERVAL)
                if get_attempt_count() > args.max_retries:
                    if not tasker.stopping:
                        logger.warning(
                            "已达 max-retries %d 次（实际 %d 次），停止",
                            args.max_retries,
                            get_attempt_count(),
                        )
                        tasker.post_stop()
                    return

        threading.Thread(target=_count_stop, daemon=True).start()

    logger.info(
        "开始凹图%s。请在关卡列表页等待...",
        f"（最多 {args.max_retries} 次凹图）" if args.max_retries else "（无限，Ctrl+C 停）",
    )

    t_start = time.time()
    detail = tasker.post_task("Farm", pipeline_override=pipeline).wait()
    elapsed = time.time() - t_start

    if detail and not detail.status.failed:
        logger.info("★★★ 凹图成功！总耗时 %.0f 秒", elapsed)
        return 0

    reason = "用户停止" if tasker.stopping else "超时/失败"
    logger.warning("凹图未达成（%s），总耗时 %.0f 秒", reason, elapsed)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
