"""地图选点弹窗（QGraphicsView 棋盘网格）。

点关卡 → 选中格子 → 生成棋盘记号（如 D2）→ 确认回填到编辑面板。
区分可部署/不可部署（buildableType: 0=不可, 1=地面, 2=高台）。

棋盘坐标与 convert_pos 一致：列=字母(A=0), 行=数字(从下往上, row=0 最下)。
QGraphicsScene 里 y 向下，故渲染时 row 越大越靠上 → y = (height-1-row)*cell。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aao.core.geometry.convert_pos import tile_position_to_str
from aao.core.geometry.map_loader import load_map

_CELL = 40
_COLORS = {
    "forbidden": QColor("#3a3a3a"),
    "wall": QColor("#6d4c41"),
    "road": QColor("#5a8f3a"),
    "hole": QColor("#222"),
    "start": QColor("#c62828"),
    "end": QColor("#1565c0"),
}


class _MapGrid(QGraphicsView):
    """关卡棋盘网格。"""

    picked = Signal(str)  # 棋盘记号，如 "D2"

    def __init__(self, map_data: dict):
        super().__init__()
        self._map_data = map_data
        self._height = map_data["height"]
        self._width = map_data["width"]
        self._tiles = map_data["tiles"]
        self._selected: tuple[int, int] | None = None  # (col, row)
        self._cell_items: dict[tuple[int, int], QGraphicsRectItem] = {}

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setFixedSize(self._width * _CELL + 4, self._height * _CELL + 4)
        scene = QGraphicsScene(self)
        self.setScene(scene)

        for r in range(self._height):
            for c in range(self._width):
                tile = self._tiles[r][c]
                key = (tile.get("tileKey") or "").replace("tile_", "")
                base = _COLORS.get(key, QColor("#777"))
                # 不可部署加深
                if tile.get("buildableType", 0) == 0 and key not in ("start", "end"):
                    base = base.darker(150)
                # y 向下：row 大=靠上 → y 小
                x = c * _CELL
                y = (self._height - 1 - r) * _CELL
                rect = QGraphicsRectItem(x, y, _CELL, _CELL)
                rect.setBrush(QBrush(base))
                rect.setPen(QPen(QColor("#000"), 1))
                # 记号标签
                label = scene.addText(
                    tile_position_to_str(c, r, self._height), QFont("Consolas", 7)
                )
                label.setPos(x + 2, y + 2)
                label.setDefaultTextColor(QColor("#eee"))
                scene.addItem(rect)
                self._cell_items[(c, r)] = rect

        scene.setSceneRect(0, 0, self._width * _CELL, self._height * _CELL)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        item = self.itemAt(event.pos())
        # 找到对应 rect
        for (c, r), rect in self._cell_items.items():
            if rect is item or (isinstance(item, QGraphicsRectItem) and item is rect):
                self._select(c, r)
                return
        # 点击的可能是被 label 覆盖，回退：按坐标算
        sp = self.mapToScene(event.pos())
        c = int(sp.x() // _CELL)
        y = int(sp.y() // _CELL)
        r = self._height - 1 - y
        if 0 <= c < self._width and 0 <= r < self._height:
            self._select(c, r)
        super().mousePressEvent(event)

    def _select(self, c: int, r: int) -> None:
        # 只允许选可部署格
        tile = self._tiles[r][c]
        if tile.get("buildableType", 0) == 0:
            return
        # 清旧高亮
        if self._selected is not None:
            old = self._cell_items.get(self._selected)
            if old is not None:
                old.setPen(QPen(QColor("#000"), 1))
        self._selected = (c, r)
        self._cell_items[(c, r)].setPen(QPen(QColor("#fff"), 3))
        self.picked.emit(tile_position_to_str(c, r, self._height))

    def selected_pos(self) -> str | None:
        if self._selected is None:
            return None
        c, r = self._selected
        return tile_position_to_str(c, r, self._height)


class MapPickerDialog(QDialog):
    """地图选点对话框。返回选中棋盘记号（或空）。"""

    def __init__(self, map_code: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"地图选点 - {map_code}")
        self._result_pos: str | None = None

        layout = QVBoxLayout(self)
        map_data = load_map(map_code)
        if map_data is None:
            layout.addWidget(QLabel(f"无法加载关卡 {map_code} 的地图数据"))
            self._grid = None
        else:
            self._grid = _MapGrid(map_data)
            layout.addWidget(self._grid)
            hint = QLabel("点击可部署格（绿=地面, 棕=高台）选中")
            hint.setStyleSheet("color: #9aa0a6;")
            layout.addWidget(hint)

        btn_row = QHBoxLayout()
        self.lbl_sel = QLabel("选中: —")
        btn_row.addWidget(self.lbl_sel)
        btn_row.addStretch()
        self.btn_ok = QPushButton("确定")
        self.btn_cancel = QPushButton("取消")
        self.btn_ok.setEnabled(False)
        btn_row.addWidget(self.btn_ok)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        if self._grid is not None:
            self._grid.picked.connect(self._on_picked)
        self.btn_ok.clicked.connect(self._on_ok)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_picked(self, pos: str) -> None:
        self._result_pos = pos
        self.lbl_sel.setText(f"选中: {pos}")
        self.btn_ok.setEnabled(True)

    def _on_ok(self) -> None:
        self.accept()

    def selected_pos(self) -> str | None:
        return self._result_pos
