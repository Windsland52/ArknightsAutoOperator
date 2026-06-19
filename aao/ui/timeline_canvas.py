"""时间轴 canvas（QGraphicsView）。

横向时间轴：动作节点按 frame 位置摆放，部署/技能/撤退三色。
双刻度：上方 frame，下方秒（game-internal, fps=30）。
实时帧游标竖线（对轴模式跟随 worker 的 totalElapsedFrames）。
节点可拖拽改 frame。

时间坐标混搭：TimelineAction 可带 frame（int）或 time（float 绝对秒）。
本 canvas 统一用"帧等效值"做 x 定位（time → *fps），显示时保留原单位。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
    QGraphicsTextItem,
    QGraphicsView,
)

from aao import config
from aao.core.battle.action import ActionType
from aao.timeline.model import Timeline, TimelineAction

# 布局常量
_PX_PER_FRAME = 0.6  # 横向：每帧像素
_LEFT_PAD = 60  # 左侧留给类型标签
_TOP = 10  # canvas 顶部
_TRACK_H = 30  # 每条轨道高
_TRACKS = {ActionType.DEPLOY: 0, ActionType.SKILL: 1, ActionType.RETREAT: 2}
_COLOR = {
    ActionType.DEPLOY: QColor("#4caf50"),
    ActionType.SKILL: QColor("#2196f3"),
    ActionType.RETREAT: QColor("#f44336"),
}
_NODE_W = 8
_NODE_H = 18


def _action_x(action: TimelineAction) -> float:
    """动作 → x 坐标（帧等效）。time 秒 ×fps。"""
    return _LEFT_PAD + action.time_value * _PX_PER_FRAME


class _NodeItem(QGraphicsRectItem):
    """单个动作节点（可拖拽）。"""

    def __init__(self, action: TimelineAction, canvas: TimelineCanvas):
        x = _action_x(action)
        track = _TRACKS.get(action.action_type, 0)
        y = _TOP + track * _TRACK_H
        super().__init__(x - _NODE_W / 2, y, _NODE_W, _NODE_H)
        self.action = action
        self._canvas = canvas
        self._drag_start_x: float | None = None
        self.setBrush(QBrush(_COLOR.get(action.action_type, QColor("#999"))))
        self.setPen(QPen(self._canvas.palette().color(QPalette.ColorRole.WindowText), 1))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        # 提示
        self.setToolTip(str(action))

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: object) -> object:
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            # 限制只能水平移动，并写回 action.frame
            new_x = self.pos().x() + _NODE_W / 2
            self.setPos(QPointF(self.pos().x(), 0))  # 锁 y（相对构造时的 y）
            frame = round((new_x - _LEFT_PAD) / _PX_PER_FRAME)
            frame = max(0, frame)
            if self.action.frame is not None:
                self.action.frame = frame
            else:
                # time 坐标：换算回秒
                self.action.time = round(frame / config.FRAMES_PER_SECOND, 3)
            self._canvas.node_moved.emit(self.action)
        return super().itemChange(change, value)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_x = self.pos().x()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self._drag_start_x = None
        super().mouseReleaseEvent(event)


class TimelineCanvas(QGraphicsView):
    """时间轴可视化。"""

    node_moved = Signal(object)  # TimelineAction，拖拽改帧后发出
    node_clicked = Signal(object)  # TimelineAction，单击选中

    def __init__(self):
        super().__init__()
        # view 背景跟随 palette（Base）——打轴页随全局主题，不固定深色
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._timeline: Timeline | None = None
        self._nodes: list[_NodeItem] = []
        self._cursor: QGraphicsRectItem | None = None
        self._frame_scale: list[QGraphicsTextItem] = []
        self._max_frame = 1800  # 默认显示范围

    # --- 主题感知配色 ---

    def _is_dark(self) -> bool:
        return self.palette().color(QPalette.ColorRole.Window).lightness() < 128

    def _accent(self) -> QColor:
        """帧刻度/节点高亮用：深色亮青、浅色深青（保证浅底可读）。"""
        return QColor("#00e5ff") if self._is_dark() else QColor("#00789a")

    def changeEvent(self, event: QEvent) -> None:
        # 主题切换 → palette 变 → 重建场景（HUD 色按新 palette 重画）
        if event.type() == QEvent.Type.PaletteChange:
            self._rebuild()
        super().changeEvent(event)

    # --- 数据 ---

    def set_timeline(self, timeline: Timeline) -> None:
        self._timeline = timeline
        # 根据动作最大帧自动扩展显示范围（至少 1800）
        if timeline.actions:
            max_af = max(a.time_value for a in timeline.actions)
            self._max_frame = max(1800, int(max_af + 300))
        self._rebuild()

    def set_max_frame(self, max_frame: int) -> None:
        self._max_frame = max(1, max_frame)
        self._rebuild()

    def _rebuild(self) -> None:
        self._scene.clear()
        self._nodes.clear()
        self._frame_scale.clear()
        self._cursor = None
        if self._timeline is None:
            return

        # 主题感知配色：中性色从 palette 派生，强调色（帧刻度/游标）按明暗二选一
        pal = self.palette()
        track_bg = pal.color(QPalette.ColorRole.AlternateBase)
        track_border = pal.color(QPalette.ColorRole.Mid)
        text = pal.color(QPalette.ColorRole.WindowText)
        accent = self._accent()
        cursor_c = QColor("#ffeb3b") if self._is_dark() else QColor("#f9a825")

        # 轨道背景 + 标签
        for at, idx in _TRACKS.items():
            y = _TOP + idx * _TRACK_H
            rect = self._scene.addRect(
                0,
                y,
                _LEFT_PAD + self._max_frame * _PX_PER_FRAME,
                _TRACK_H,
                QPen(track_border),
                QBrush(track_bg),
            )
            rect.setZValue(-2)
            lbl = self._scene.addText(at.value, QFont("Consolas", 9))
            lbl.setPos(4, y + 6)
            lbl.setDefaultTextColor(text)

        # 刻度（frame + 秒双轴）
        step = 300  # 每 300 帧（10s）一刻度
        for f in range(0, self._max_frame + 1, step):
            x = _LEFT_PAD + f * _PX_PER_FRAME
            self._scene.addLine(x, _TOP, x, _TOP + 3 * _TRACK_H, QPen(track_border, 1))
            t = self._scene.addText(f"{f}", QFont("Consolas", 8))
            t.setPos(x - 8, _TOP - 14)
            t.setDefaultTextColor(accent)
            s = self._scene.addText(f"{f // config.FRAMES_PER_SECOND}s", QFont("Consolas", 8))
            s.setPos(x - 10, _TOP + 3 * _TRACK_H + 2)
            s.setDefaultTextColor(text)
            self._frame_scale.append(t)

        # 游标
        self._cursor = QGraphicsRectItem(0, _TOP, 2, 3 * _TRACK_H)
        self._cursor.setBrush(QBrush(cursor_c))
        self._cursor.setPen(QPen(Qt.PenStyle.NoPen))
        self._cursor.setZValue(5)
        self._scene.addItem(self._cursor)

        # 节点
        for action in self._timeline.actions:
            node = _NodeItem(action, self)
            self._scene.addItem(node)
            self._nodes.append(node)

        scene_w = _LEFT_PAD + self._max_frame * _PX_PER_FRAME + 40
        scene_h = _TOP + 3 * _TRACK_H + 30
        self._scene.setSceneRect(0, 0, scene_w, scene_h)

    # --- 实时帧游标 ---

    def set_current_frame(self, frame: int) -> None:
        if self._cursor is None:
            return
        x = _LEFT_PAD + frame * _PX_PER_FRAME
        self._cursor.setRect(x, _TOP, 2, 3 * _TRACK_H)
        # 对轴磁铁：临近节点高亮
        self._magnet_hint(frame)

    def _magnet_hint(self, frame: int) -> None:
        threshold = config.FRAMES_PER_SECOND  # 1s 内吸附提示
        edge = self.palette().color(QPalette.ColorRole.WindowText)
        accent = self._accent()
        for node in self._nodes:
            af = node.action.time_value
            if abs(af - frame) < threshold:
                node.setPen(QPen(accent, 2))
            else:
                node.setPen(QPen(edge, 1))

    # --- 鼠标点击节点选中 ---

    def mousePressEvent(self, event: QMouseEvent) -> None:
        item = self.itemAt(event.pos())
        if isinstance(item, _NodeItem):
            self.node_clicked.emit(item.action)
        super().mousePressEvent(event)
