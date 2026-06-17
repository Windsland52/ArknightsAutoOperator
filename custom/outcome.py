"""本轮结算结果跟踪：用 MAA context_sink 监听 farm.json 结算节点命中。

sink 在 tasker 线程触发（on_node_next_list，节点执行完进入 next 时），
写模块级 last_outcome。ExecuteTimeline 回调（同 tasker 线程，pipeline 串行）
读取，合并中途漏怪信号，回传 UI。

outcome 取值：
- UNKNOWN：本轮尚未结算（ExecuteTimeline 刚跑完，还没到结算节点）
- LEAKED：Farm@LeakDetect 命中（结算阶段血量图标变红）
- MISSION_FAILED：Farm@MissionFailed 命中（任务失败画面）
- STARS_NO3：Farm@StarsNo3 命中（非三星，含二星）
- STARS3：Farm@Stars3 命中（三星成功）

注意：ExecuteTimeline 中途漏怪（_leaked）走的是执行器内检测，与结算节点
Farm@LeakDetect 是两套——前者在动作执行中、后者在结算等待中。两者都可能
触发"漏怪"。
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from aao.utils.logger import logger


class Outcome(enum.Enum):
    UNKNOWN = "未知"
    LEAKED = "漏怪"
    MISSION_FAILED = "任务失败"
    STARS_NO3 = "非三星"
    STARS3 = "三星成功"


# 节点名 → outcome
_NODE_MAP = {
    "Farm@LeakDetect": Outcome.LEAKED,
    "Farm@MissionFailed": Outcome.MISSION_FAILED,
    "Farm@StarsNo3": Outcome.STARS_NO3,
    "Farm@Stars3": Outcome.STARS3,
}

# 模块级：最近一次结算节点命中的 outcome。
# 由 sink 写（tasker 线程），ExecuteTimeline 回调读（同线程串行）。
_last_outcome: Outcome = Outcome.UNKNOWN
# 结算节点命中时通知外部（farm_worker 用来更新 UI 结果历史）。在 tasker 线程触发。
_on_outcome_cb: Callable[[Outcome], None] | None = None


def reset_outcome() -> None:
    """每轮 ExecuteTimeline 开始前重置（由执行器调用）。"""
    global _last_outcome
    _last_outcome = Outcome.UNKNOWN


def set_outcome(node_name: str) -> None:
    """sink 监听到结算节点命中时调用。幂等：同值不重复记日志/回调。"""
    global _last_outcome
    outcome = _NODE_MAP.get(node_name)
    if outcome is not None and outcome is not _last_outcome:
        _last_outcome = outcome
        logger.info("结算节点命中: %s → %s", node_name, outcome.value)
        if _on_outcome_cb is not None:
            try:
                _on_outcome_cb(outcome)
            except Exception:  # noqa: BLE001
                logger.exception("_on_outcome_cb 回调失败")


def get_outcome() -> Outcome:
    return _last_outcome


def make_sink(on_outcome: Callable[[Outcome], None] | None = None):
    """创建 ContextEventSink，监听结算节点。返回 (sink, should_set_debug)。

    on_outcome：结算节点命中且 outcome 变化时调用（tasker 线程）。
    on_node_next_list 在节点执行完进入 next 时触发，带 detail.name。
    返回 should_debug=False：next_list 是控制流事件，默认对所有节点触发，
    无需 set_debug_mode(True)（那会带来额外开销）。若实测某些节点不触发，
    再改 True。
    """
    global _on_outcome_cb
    from maa.context import ContextEventSink, NotificationType

    _on_outcome_cb = on_outcome

    class _Sink(ContextEventSink):
        def on_node_next_list(self, context, noti_type, detail):
            try:
                set_outcome(detail.name)
            except Exception:  # noqa: BLE001
                logger.exception("sink on_node_next_list 处理失败")

        def on_node_recognition(self, context, noti_type, detail):
            # 结算判定节点（LeakDetect/Stars3/StarsNo3/MissionFailed）是
            # recognition 命中才进 next，命中时 noti_type=Succeeded，记一次。
            try:
                if noti_type == NotificationType.Succeeded:
                    set_outcome(detail.name)
            except Exception:  # noqa: BLE001
                pass

    return _Sink(), False
