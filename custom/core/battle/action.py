"""动作数据类型。

移植自 reference/prts-plus/logic/action.py。
一个 Action = 在特定游戏时间对特定干员执行 deploy/skill/retreat。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from custom.core.battle.game_time import GameTime


class ActionType(Enum):
    DEPLOY = "部署"
    SKILL = "技能"
    RETREAT = "撤退"


class DirectionType(Enum):
    UP = "上"
    DOWN = "下"
    LEFT = "左"
    RIGHT = "右"
    NONE = "无"


@dataclass
class Action:
    """单个战斗动作。"""

    cost: int | None = None
    tick: int | None = None
    time: int | None = None  # 绝对时间（秒），可选
    action_type: ActionType | None = None
    oper: str | None = None
    pos: str | None = None  # 棋盘记号如 "D2"
    direction: DirectionType | None = None
    alias: str | None = None

    # 运行时填充
    tile_pos: tuple[int, int] | None = None  # (col, row)
    avatar_pos: tuple[float, float] | None = None  # 屏幕比例 (x, y)
    view_pos_front: tuple[float, float] | None = None
    view_pos_side: tuple[float, float] | None = None

    def get_game_time(self):
        """返回此动作的目标游戏时间。"""
        return GameTime(cost=self.cost, tick=self.tick, time=self.time)

    def is_valid(self) -> bool:
        """校验动作是否完整。"""
        if self.cost is None and self.time is None:
            return False
        if self.tick is None or self.tick < 0:
            return False
        if self.action_type is None:
            return False
        if self.action_type == ActionType.DEPLOY:
            if self.pos is None or self.direction is None:
                return False
        return True

    def __str__(self) -> str:
        return (
            f"{self.action_type.value if self.action_type else '?'} "
            f"{self.oper or '?'} {self.pos or ''} "
            f"{self.direction.value if self.direction else ''}"
        )
