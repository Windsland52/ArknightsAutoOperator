"""时间轴数据模型。

Timeline = 一个关卡的完整动作序列。
TimelineAction = 单个标记（帧/时间 + 动作类型 + 干员 + 坐标 + 朝向）。

JSON 格式：
{
    "map_code": "1-7",
    "coordinate": "frame",  // "frame" | "time"
    "actions": [
        {"frame": 450, "action_type": "部署", "oper": "斑点", "pos": "D2", "direction": "右"},
        {"frame": 1260, "action_type": "技能", "oper": "斑点"}
    ]
}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aao.core.battle.action import ActionType, DirectionType


@dataclass
class TimelineAction:
    """时间轴上的单个动作标记。"""

    # 时间坐标（二选一）
    frame: int | None = None  # 全局累计帧（coordinate="frame"）
    time: float | None = None  # 绝对秒（coordinate="time"）

    # 动作内容
    action_type: ActionType = ActionType.DEPLOY
    oper: str = ""
    pos: str = ""  # 棋盘记号 "D2"
    direction: DirectionType = DirectionType.NONE
    speed: int | None = None  # 变速动作的目标速度（1 或 2）
    note: str = ""  # 备注

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.frame is not None:
            d["frame"] = self.frame
        if self.time is not None:
            d["time"] = self.time
        d["action_type"] = self.action_type.value
        if self.oper:
            d["oper"] = self.oper
        if self.pos:
            d["pos"] = self.pos
        if self.direction != DirectionType.NONE:
            d["direction"] = self.direction.value
        if self.speed is not None:
            d["speed"] = self.speed
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TimelineAction:
        return cls(
            frame=d.get("frame"),
            time=d.get("time"),
            action_type=ActionType(d.get("action_type", "部署")),
            oper=d.get("oper", ""),
            pos=d.get("pos", ""),
            direction=DirectionType(d.get("direction", "无")),
            speed=d.get("speed"),
            note=d.get("note", ""),
        )

    @property
    def time_value(self) -> float:
        """用于排序/显示的数值。"""
        if self.frame is not None:
            return float(self.frame)
        if self.time is not None:
            return self.time * 30  # 转为帧等效值
        return 0.0

    def __str__(self) -> str:
        t = f"frame={self.frame}" if self.frame is not None else f"time={self.time}"
        return f"{self.action_type.value} {self.oper} {self.pos} ({t})"


@dataclass
class Timeline:
    """完整时间轴（一个关卡）。"""

    map_code: str = ""
    coordinate: str = "frame"  # "frame" | "time"
    actions: list[TimelineAction] = field(default_factory=list)
    name: str = ""  # 用户可读名称
    candidates: list[str] = field(default_factory=list)  # 候选干员/装置名
    calibration_profile: str = ""  # 打轴时用的校准 profile 文件名
    speed_mode: str = "auto"  # "auto"（自动变速）或 "manual"（手动变速）

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "map_code": self.map_code,
            "coordinate": self.coordinate,
            "name": self.name,
            "candidates": self.candidates,
            "actions": [a.to_dict() for a in self.actions],
        }
        if self.calibration_profile:
            d["calibration_profile"] = self.calibration_profile
        if self.speed_mode != "auto":
            d["speed_mode"] = self.speed_mode
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Timeline:
        return cls(
            map_code=d.get("map_code", ""),
            coordinate=d.get("coordinate", "frame"),
            name=d.get("name", ""),
            candidates=d.get("candidates", []),
            calibration_profile=d.get("calibration_profile", ""),
            speed_mode=d.get("speed_mode", "auto"),
            actions=[TimelineAction.from_dict(a) for a in d.get("actions", [])],
        )

    def sorted(self) -> None:
        """按时间排序。"""
        self.actions.sort(key=lambda a: a.time_value)
