"""打轴/对轴编辑器窗口（PySide6）。

打轴模式（磁铁关）：用户拖拽时间轴浏览；实时帧游标独立显示。
对轴模式（磁铁开）：时间轴跟随实时帧；节点临近时声/光提醒。

交互：
- F8/F9/F10：在当前帧标记 部署/技能/撤退（全局热键，需注册）
- 左侧动作列表：选中 → 右侧编辑面板填干员/位置/朝向
- 顶栏：关卡/profile/打开/保存/模式切换/磁铁开关
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QCompleter,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aao.core.battle.action import ActionType, DirectionType
from aao.timeline.io import load_timeline, save_timeline
from aao.timeline.model import Timeline, TimelineAction
from aao.ui.map_picker import MapPickerDialog
from aao.ui.timeline_canvas import TimelineCanvas
from aao.utils.logger import logger


class EditorWindow(QWidget):
    """打轴/对轴编辑器主窗口。"""

    action_marked = Signal(object)  # TimelineAction，热键标记时发出

    def __init__(self):
        super().__init__()
        self.setWindowTitle("打轴编辑器")
        self.setWindowFlags(Qt.WindowType.Window)
        self.resize(900, 500)

        self.timeline = Timeline()
        self._current_frame = 0  # 实时帧（由外部更新）
        self._align_mode = False  # False=打轴（游标不跟随），True=对轴（游标跟实时帧）
        self._candidates: list[str] = []  # 候选干员/装置列表
        self._profile_name = ""  # 当前校准 profile 文件名（由 MainWindow 注入）

        self.canvas = TimelineCanvas()
        self.canvas.node_clicked.connect(self._on_canvas_node_clicked)
        self.canvas.node_moved.connect(self._on_canvas_node_moved)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- 顶栏 ---
        top = QHBoxLayout()
        self.btn_open = QPushButton("📂 打开")
        self.btn_save = QPushButton("💾 保存")
        self.lbl_map = QLabel("关卡:")
        self.edit_map = QLineEdit()
        self.edit_map.setPlaceholderText("如 1-7")
        self.edit_map.setMaximumWidth(120)
        self._map_completer = QCompleter()
        self._map_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.edit_map.setCompleter(self._map_completer)
        self._load_level_codes()

        self.rb_author = QRadioButton("打轴")
        self.rb_align = QRadioButton("对轴")
        self.rb_author.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.rb_author)
        mode_group.addButton(self.rb_align)
        self.rb_align.toggled.connect(self._on_mode_changed)

        self.lbl_frame = QLabel("--")
        f = QFont("Consolas", 14)
        f.setBold(True)
        self.lbl_frame.setFont(f)
        self.lbl_frame.setStyleSheet("color: #00e5ff;")

        self.btn_help = QPushButton("❓ F8=部署 F9=技能 F10=撤退")

        top.addWidget(self.btn_open)
        top.addWidget(self.btn_save)
        top.addWidget(self.lbl_map)
        top.addWidget(self.edit_map)
        top.addSpacing(20)
        top.addWidget(self.rb_author)
        top.addWidget(self.rb_align)
        top.addStretch()
        top.addWidget(self.lbl_frame)
        top.addSpacing(20)
        top.addWidget(self.btn_help)
        layout.addLayout(top)

        # --- 时间轴 canvas ---
        self.canvas.set_timeline(self.timeline)
        layout.addWidget(self.canvas)

        # --- 中部：动作列表 + 右侧面板（候选区 + 编辑面板）---
        middle = QHBoxLayout()

        # 动作列表
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "类型", "干员", "位置", "朝向"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setMinimumWidth(400)
        middle.addWidget(self.table, 3)

        # 右侧竖分两半：上=候选干员/装置，下=动作编辑面板
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # --- 候选区 ---
        cand_box = QGroupBox("候选干员/装置")
        cl = QVBoxLayout(cand_box)
        add_row = QHBoxLayout()
        self.edit_cand = QLineEdit()
        self.edit_cand.setPlaceholderText("输入名称后回车添加")
        cand_completer = QCompleter()
        cand_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.edit_cand.setCompleter(cand_completer)
        self._cand_completer = cand_completer
        self._load_operator_names(cand_completer)
        self.btn_cand_add = QPushButton("➕")
        self.btn_cand_add.setMaximumWidth(40)
        self.btn_cand_del = QPushButton("➖")
        self.btn_cand_del.setMaximumWidth(40)
        add_row.addWidget(self.edit_cand)
        add_row.addWidget(self.btn_cand_add)
        add_row.addWidget(self.btn_cand_del)
        cl.addLayout(add_row)
        self.list_candidates = QListWidget()
        cl.addWidget(self.list_candidates)
        right_splitter.addWidget(cand_box)

        # --- 编辑面板 ---
        panel = QWidget()
        p_layout = QGridLayout(panel)
        p_layout.setContentsMargins(8, 8, 8, 8)

        p_layout.addWidget(QLabel("类型:"), 0, 0)
        self.cb_type = QComboBox()
        self.cb_type.addItems(["部署", "技能", "撤退"])
        p_layout.addWidget(self.cb_type, 0, 1)

        p_layout.addWidget(QLabel("干员:"), 1, 0)
        self.cb_oper = QComboBox()
        self.cb_oper.setEditable(True)
        oper_completer = QCompleter()
        oper_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.cb_oper.setCompleter(oper_completer)
        p_layout.addWidget(self.cb_oper, 1, 1)

        p_layout.addWidget(QLabel("位置:"), 2, 0)
        pos_row = QHBoxLayout()
        self.edit_pos = QLineEdit()
        self.edit_pos.setMaximumWidth(60)
        self.edit_pos.setPlaceholderText("D2")
        self.btn_pick = QPushButton("📍")
        pos_row.addWidget(self.edit_pos)
        pos_row.addWidget(self.btn_pick)
        pos_w = QWidget()
        pos_w.setLayout(pos_row)
        p_layout.addWidget(pos_w, 2, 1)

        p_layout.addWidget(QLabel("朝向:"), 3, 0)
        self.cb_dir = QComboBox()
        self.cb_dir.addItems(["无", "上", "下", "左", "右"])
        p_layout.addWidget(self.cb_dir, 3, 1)

        self.btn_apply = QPushButton("✓ 应用")
        self.btn_delete = QPushButton("✕ 删除")
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_delete)
        btn_w = QWidget()
        btn_w.setLayout(btn_row)
        p_layout.addWidget(btn_w, 4, 0, 1, 2)
        right_splitter.addWidget(panel)

        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setMaximumWidth(280)
        middle.addWidget(right_splitter, 1)
        layout.addLayout(middle, 1)

        # --- 底部状态 ---
        self.lbl_status = QLabel("就绪。按 F8/F9/F10 标记动作。")
        layout.addWidget(self.lbl_status)

        # --- 信号 ---
        self.btn_open.clicked.connect(self._on_open)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_pick.clicked.connect(self._on_pick_pos)
        self.table.currentItemChanged.connect(self._on_select)
        self.btn_cand_add.clicked.connect(self._add_candidate)
        self.btn_cand_del.clicked.connect(self._del_candidate)
        self.edit_cand.returnPressed.connect(self._add_candidate)
        self.list_candidates.currentRowChanged.connect(self._on_candidate_selected)

    # --- 候选干员/装置管理 ---

    def _add_candidate(self) -> None:
        """添加候选（输入框回车或按钮）。"""
        name = self.edit_cand.text().strip()
        if not name:
            return
        if name in self._candidates:
            self.lbl_status.setText(f"{name} 已在候选列表")
            return
        self._candidates.append(name)
        self.list_candidates.addItem(name)
        self.cb_oper.addItem(name)
        self.edit_cand.clear()
        self.lbl_status.setText(f"已添加候选: {name}")

    def _del_candidate(self) -> None:
        """删除选中的候选。"""
        row = self.list_candidates.currentRow()
        if row < 0:
            return
        name = self._candidates.pop(row)
        self.list_candidates.takeItem(row)
        idx = self.cb_oper.findText(name)
        if idx >= 0:
            self.cb_oper.removeItem(idx)
        self.lbl_status.setText(f"已移除候选: {name}")

    def _on_candidate_selected(self, row: int) -> None:
        """选中候选 → 同步到编辑面板干员框。"""
        if 0 <= row < len(self._candidates):
            self.cb_oper.setCurrentText(self._candidates[row])

    def _sync_candidates_from_timeline(self) -> None:
        """从 timeline 数据加载候选列表。"""
        cands = getattr(self.timeline, "candidates", None) or []
        self._candidates.clear()
        self.list_candidates.clear()
        self.cb_oper.clear()
        for name in cands:
            self._candidates.append(name)
            self.list_candidates.addItem(name)
            self.cb_oper.addItem(name)

    def _collect_candidates(self) -> None:
        """保存前把候选写回 timeline。"""
        # 从已有动作中收集用到的干员名，补进候选
        existing = {a.oper for a in self.timeline.actions if a.oper}
        for name in self._candidates:
            existing.discard(name)
        for name in sorted(existing):
            if name and name not in self._candidates:
                self._candidates.append(name)
                self.list_candidates.addItem(name)
                self.cb_oper.addItem(name)
        self.timeline.candidates = list(self._candidates)

    # --- 数据加载 ---

    def _load_operator_names(self, completer: QCompleter) -> None:
        """从 data/operator_names.json 加载干员名到 completer。"""
        from PySide6.QtCore import QStringListModel

        from aao.utils.runtime_paths import project_root

        path = project_root() / "data" / "operator_names.json"
        if not path.exists():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        names = [item["name"] for item in raw if item.get("name")]
        completer.setModel(QStringListModel(names))

    def _load_level_codes(self) -> None:
        """从 data/level_codes.json 加载关卡代号。"""
        from aao.utils.runtime_paths import project_root

        path = project_root() / "data" / "level_codes.json"
        if not path.exists():
            return
        codes = json.loads(path.read_text(encoding="utf-8"))
        self._map_completer.setModel(QCompleter().model())
        from PySide6.QtCore import QStringListModel

        model = QStringListModel(list(codes.keys()))
        self._map_completer.setModel(model)

    # --- 实时帧更新（由外部调用）---

    def update_frame(self, frame: int, timer_str: str = "") -> None:
        """更新实时帧显示。对轴模式额外驱动 canvas 游标。"""
        self._current_frame = frame
        self.lbl_frame.setText(f"{frame}")
        if timer_str:
            self.lbl_frame.setToolTip(timer_str)
        # 仅对轴模式游标跟随实时帧；打轴模式游标固定，便于手动标节点时观察
        if self._align_mode:
            self.canvas.set_current_frame(frame)

    def _on_mode_changed(self, checked: bool) -> None:
        """rb_align 切换：对轴=游标跟随，打轴=游标固定。"""
        self._align_mode = checked
        if self._align_mode:
            # 切到对轴：立即把游标同步到当前帧
            self.canvas.set_current_frame(self._current_frame)

    # --- 热键标记 ---

    def mark_action(self, action_type: ActionType) -> None:
        """热键触发：在当前帧标记一个动作，自动带当前选中的候选干员。"""
        # 选中候选 → 自动填干员名
        oper = ""
        row = self.list_candidates.currentRow()
        if 0 <= row < len(self._candidates):
            oper = self._candidates[row]
        action = TimelineAction(
            frame=self._current_frame,
            action_type=action_type,
            oper=oper or "",
        )
        self.timeline.actions.append(action)
        self.timeline.sorted()
        self._refresh_table()
        self.lbl_status.setText(f"标记: {action_type.value} @ frame={self._current_frame}")
        self.action_marked.emit(action)
        logger.info("标记 %s @ frame=%d", action_type.value, self._current_frame)

    # --- 动作列表操作 ---

    def _refresh_table(self):
        self.table.setRowCount(len(self.timeline.actions))
        for i, a in enumerate(self.timeline.actions):
            time_str = f"{a.frame}" if a.frame is not None else f"{a.time}s"
            self.table.setItem(i, 0, QTableWidgetItem(time_str))
            self.table.setItem(i, 1, QTableWidgetItem(a.action_type.value))
            self.table.setItem(i, 2, QTableWidgetItem(a.oper))
            self.table.setItem(i, 3, QTableWidgetItem(a.pos))
            self.table.setItem(i, 4, QTableWidgetItem(a.direction.value))
        self.canvas.set_timeline(self.timeline)

    # --- canvas 回调 ---

    def _on_canvas_node_clicked(self, action: TimelineAction) -> None:
        """canvas 点击节点 → 同步选中表格行 + 编辑面板。"""
        try:
            row = self.timeline.actions.index(action)
        except ValueError:
            return
        self.table.setCurrentCell(row, 0)
        self._on_select()

    def _on_canvas_node_moved(self, action: TimelineAction) -> None:
        """canvas 拖拽改帧 → 刷新表格时间列。"""
        self._refresh_table()
        self.lbl_status.setText(f"已移动 {action.action_type.value} → frame={action.frame}")

    # --- 地图选点 ---

    def _on_pick_pos(self) -> None:
        """📍 按钮 → 弹地图选点，回填位置。"""
        map_code = self.edit_map.text().strip()
        if not map_code:
            self.lbl_status.setText("请先填关卡代号")
            return
        dlg = MapPickerDialog(map_code, self)
        if dlg.exec() == MapPickerDialog.DialogCode.Accepted:
            pos = dlg.selected_pos()
            if pos:
                self.edit_pos.setText(pos)
                self.lbl_status.setText(f"已选位置 {pos}")

    def _on_select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.timeline.actions):
            return
        a = self.timeline.actions[row]
        self.cb_type.setCurrentText(a.action_type.value)
        self.cb_oper.setCurrentText(a.oper)
        self.edit_pos.setText(a.pos)
        self.cb_dir.setCurrentText(a.direction.value)

    def _on_apply(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.timeline.actions):
            return
        a = self.timeline.actions[row]
        a.action_type = ActionType(self.cb_type.currentText())
        a.oper = self.cb_oper.currentText().strip()
        a.pos = self.edit_pos.text().strip()
        a.direction = DirectionType(self.cb_dir.currentText())
        self._refresh_table()
        self.lbl_status.setText(f"已更新 #{row + 1}")

    def _on_delete(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.timeline.actions):
            return
        del self.timeline.actions[row]
        self._refresh_table()
        self.lbl_status.setText(f"已删除 #{row + 1}")

    def _on_open(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开时间轴", "", "JSON (*.json)")
        if not path:
            return
        self.timeline = load_timeline(path)
        self.edit_map.setText(self.timeline.map_code)
        self._sync_candidates_from_timeline()
        self._refresh_table()
        self.lbl_status.setText(f"已加载 {len(self.timeline.actions)} 个动作")

    def set_profile(self, profile_name: str) -> None:
        """注入当前校准 profile 文件名（打轴帧数依赖此 profile，保存时写入 timeline）。"""
        self._profile_name = profile_name

    def _on_save(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存时间轴", "", "JSON (*.json)")
        if not path:
            return
        self.timeline.map_code = self.edit_map.text().strip()
        self.timeline.calibration_profile = self._profile_name
        self._collect_candidates()
        save_timeline(self.timeline, path)
        self.lbl_status.setText(f"已保存到 {path}")
