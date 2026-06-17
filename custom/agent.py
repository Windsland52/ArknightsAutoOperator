"""测试 Agent：以 AgentServer 模式注册 Custom，供 maa-support-extension 调试工具调用。

工作原理：
    maa-support-extension（AgentClient）通过 child_exec 启动本脚本，传一个 identifier。
    本脚本以 AgentServer 模式启动，注册 ClickStage / ExecuteTimeline，
    maa-server 跑 pipeline 遇到 Custom 节点时，委托给本进程执行。

用法（通常由 maa-support-extension 自动启动，无需手动跑）：
    python -m custom.agent <identifier>

生产环境（用户实际刷图）不走本脚本，而是 farm.py 本地注册 Custom。
"""

from __future__ import annotations

import sys

from maa.agent.agent_server import AgentServer

from aao.utils.logger import logger, setup_logging
from custom.registry import register_all


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python -m custom.agent <identifier>", file=sys.stderr)
        return 2

    identifier = sys.argv[1]
    setup_logging("INFO")

    register_all(AgentServer)
    logger.info("已注册全部 Custom 到 AgentServer")

    if not AgentServer.start_up(identifier):
        logger.error("AgentServer 启动失败: identifier=%s", identifier)
        return 1

    logger.info("AgentServer 已启动（identifier=%s），等待 pipeline 调用...", identifier)
    AgentServer.join()
    logger.info("AgentServer 结束")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
