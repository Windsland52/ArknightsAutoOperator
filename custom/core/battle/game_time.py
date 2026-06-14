"""游戏时间（cost + tick + time），支持比较和运算。

移植自 reference/prts-plus/logic/game_time.py。
prts-plus 约定：1 秒 = 30 tick（TICK_MAX）。费用条循环一次 = 1 秒 = 1 cost。
"""

from __future__ import annotations

from dataclasses import dataclass

from custom import config

TICK_MAX = config.TICK_MAX_DEFAULT  # 30


@dataclass
class GameTime:
    """游戏时间：cost（第几费）+ tick（费内第几帧 0..29）+ time（绝对秒，可选）。"""

    cost: int | None = None
    tick: int | None = None
    time: int | None = None

    def is_complete(self) -> bool:
        return self.cost is not None or self.time is not None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GameTime):
            return NotImplemented
        return self._to_total() == other._to_total()

    def __lt__(self, other: GameTime) -> bool:
        return self._to_total() < other._to_total()

    def __le__(self, other: GameTime) -> bool:
        return self._to_total() <= other._to_total()

    def __gt__(self, other: GameTime) -> bool:
        return self._to_total() > other._to_total()

    def __ge__(self, other: GameTime) -> bool:
        return self._to_total() >= other._to_total()

    def __sub__(self, other: GameTime) -> GameTime:
        """返回差值（self - other）。"""
        total = self._to_total() - other._to_total()
        return GameTime(cost=total // TICK_MAX, tick=total % TICK_MAX)

    def __add__(self, other: GameTime) -> GameTime:
        """返回和（self + other）。"""
        total = self._to_total() + other._to_total()
        return GameTime(cost=total // TICK_MAX, tick=total % TICK_MAX)

    def _to_total(self) -> int:
        """转为总 tick 数用于比较。"""
        cost = self.cost or 0
        tick = self.tick or 0
        if self.time is not None:
            cost = self.time
        return cost * TICK_MAX + tick

    def __str__(self) -> str:
        parts = []
        if self.cost is not None:
            parts.append(f"cost={self.cost}")
        if self.tick is not None:
            parts.append(f"tick={self.tick}")
        if self.time is not None:
            parts.append(f"time={self.time}")
        return f"GameTime({', '.join(parts)})"
