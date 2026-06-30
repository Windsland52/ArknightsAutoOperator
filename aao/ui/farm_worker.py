"""凹图后台 worker：在 QThread 中跑 Tasker.post_task("Farm")，不阻塞 UI。

照 custom/measure/worker.py 的 QObject + Signal + moveToThread 范式。
进度经信号回 UI 线程（Qt 默认 QueuedConnection 跨线程安全）：

- round_finished: 每轮 ExecuteTimeline 末尾触发（漏怪/未漏怪 + 计时 + 次数）
- finished: 整个凹图会话结束（success = 三星成功 / False = 超时或停止）
- log: 日志文本
"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

from aao.types import JsonObject
from aao.utils.jsonc import load as load_jsonc
from custom.action.executor import ExecuteTimeline, RoundResult
from custom.reco.click_stage import get_attempt_count, reset_attempt_count

if TYPE_CHECKING:
    from maa.controller import Win32Controller
    from maa.tasker import Tasker

    from custom.outcome import Outcome

from aao.utils.logger import logger

# 次数监控线程的轮询间隔（秒）
_POLL_INTERVAL = 2.0


class FarmWorker(QObject):
    """凹图会话 worker（moveToThread 到 QThread 运行）。"""

    started_sig = Signal()  # 已开始凹图会话
    round_finished = Signal(object)  # RoundResult（ExecuteTimeline 末尾，outcome 可能"进行中"）
    round_outcome = Signal(int, str)  # (attempt_count, outcome_str) 结算节点命中后更新该轮
    reset_timer_requested = Signal(str)  # pipeline 节点触发计时器清空（进入战斗/结算/放弃）
    finished = Signal(bool)  # real_success
    log = Signal(str)

    def __init__(
        self,
        controller: Win32Controller,
        tasker: Tasker,
        timeline_path: str,
        difficulty: str,
        max_retries: int,
        profile: str | None,
        practice: bool = False,
    ):
        super().__init__()
        self._controller = controller
        self._tasker = tasker
        self._timeline_path = timeline_path
        self._difficulty = difficulty
        self._max_retries = max_retries
        self._profile = profile
        self._practice = practice
        self._stopping = False
        self._stopped_by_user = False

    def run(self) -> None:
        """凹图会话主循环（阻塞在 tasker.post_task().wait()）。"""
        from aao.utils.runtime_paths import project_root

        self._stopping = False
        self._stopped_by_user = False
        self.started_sig.emit()

        # 挂本轮回调 → 转 round_finished 信号（tasker 线程触发，Qt 自动跨线程）
        def _on_round(result: RoundResult) -> None:
            self.round_finished.emit(result)

        ExecuteTimeline.on_round_finished = _on_round

        # 装 context_sink 监听结算节点（漏怪/二星/失败/三星），写 outcome 模块变量。
        # 结算节点在 ExecuteTimeline return 之后命中，此时 round_finished 已发过
        # （该轮 outcome="进行中"），故 sink 命中时再发 round_outcome 更新 UI 那一行。
        from custom.outcome import make_sink

        reset_nodes = {
            "Farm@StartButton2",
            "Farm@Abandon",
            "Farm@AbandonConfirm",
            "Farm@MissionFailed",
            "Farm@Settlement",
            "Farm@Stars3",
            "Farm@StarsNo3",
        }

        def _on_outcome(outcome: Outcome) -> None:
            self.round_outcome.emit(get_attempt_count(), outcome.value)

        def _on_node(node_name: str) -> None:
            if node_name in reset_nodes:
                self.reset_timer_requested.emit(node_name)

        sink, should_debug = make_sink(on_outcome=_on_outcome, on_node=_on_node)
        self._tasker.add_context_sink(sink)
        if should_debug:
            self._tasker.set_debug_mode(True)

        reset_attempt_count()

        # 加载 pipeline + 注入参数（搬自 farm.py）
        pipeline_path = project_root() / "resource" / "base" / "pipeline" / "farm.json"
        pipeline = load_jsonc(pipeline_path)

        tl_param = json.dumps({"timeline_path": self._timeline_path}, ensure_ascii=False)
        pipeline["Farm@ClickStage"]["custom_recognition_param"] = tl_param

        exec_param: JsonObject = {"timeline_path": self._timeline_path}
        if self._profile:
            exec_param["calibration"] = self._profile
        pipeline["Farm@Execute"]["custom_action_param"] = json.dumps(exec_param, ensure_ascii=False)

        # 难度/演习分支（参考 interface.json：沙盘无演习；普通/自选难度才切 StartButton1 expected）
        if self._difficulty == "sand":
            anchor_target = "Farm@SwitchDifficulty"
        else:
            anchor_target = "Farm@StartButton1"
            pipeline["Farm@StartButton1"]["expected"] = ["演习" if self._practice else "开始行动"]
        pipeline["Farm"]["anchor"] = {"Farm@SwitchDifficulty": anchor_target}

        # max-retries 次数监控线程
        if self._max_retries:

            def _count_stop() -> None:
                while not self._tasker.stopping:
                    time.sleep(_POLL_INTERVAL)
                    if get_attempt_count() > self._max_retries:
                        if not self._tasker.stopping:
                            logger.warning(
                                "已达 max-retries %d 次（实际 %d），停止",
                                self._max_retries,
                                get_attempt_count(),
                            )
                            self._tasker.post_stop()
                        return

            threading.Thread(target=_count_stop, daemon=True).start()

        logger.info(
            "开始凹图（时间轴=%s，难度=%s%s，最多 %s 次）",
            self._timeline_path,
            self._difficulty,
            "，演习" if self._practice and self._difficulty != "sand" else "",
            self._max_retries or "∞",
        )

        detail = self._tasker.post_task("Farm", pipeline_override=pipeline).wait()
        task_failed = bool(detail.status.failed)

        # 清回调，避免后续会话误触发
        ExecuteTimeline.on_round_finished = None

        # 区分停止原因。注意：post_stop 后 MAA 任务 status 可能仍为非 failed
        # （停止不算失败），故 stopping 必须优先于 success 判定，否则被停止的
        # 会话会被误判"三星成功"。
        if self._tasker.stopping:
            reason = "用户停止" if self._stopped_by_user else "次数达上限停止"
            real_success = False
        elif task_failed:
            reason = "超时/失败"
            real_success = False
        else:
            reason = "三星成功"
            real_success = True
        logger.info("凹图结束（%s）", reason)
        self.finished.emit(real_success)

    def stop(self) -> None:
        """请求停止（用户手动，post_stop 中断 pipeline）。"""
        self._stopping = True
        self._stopped_by_user = True
        if not self._tasker.stopping:
            self._tasker.post_stop()
