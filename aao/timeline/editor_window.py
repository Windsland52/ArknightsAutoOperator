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

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
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
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from aao.core.battle.action import ActionType, DirectionType
from aao.timeline.io import load_timeline, save_timeline
from aao.timeline.model import Timeline, TimelineAction
from aao.ui.map_picker import MapPickerDialog
from aao.ui.scrollbar_style import apply_themed_scrollbar
from aao.ui.timeline_canvas import TimelineCanvas
from aao.ui.toggle_switch import ToggleSwitch
from aao.utils.logger import logger


class _TimelineHeader(QHeaderView):
    """动作表自绘表头：不走平台 header 文本绘制，避免浅色模式被画成白字。"""

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        if not rect.isValid():
            return
        is_dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        bg = QColor(255, 255, 255, 45) if is_dark else QColor(255, 255, 255, 170)
        fg = QColor("#e6e6e6") if is_dark else QColor("#000000")
        border = QColor(255, 255, 255, 55) if is_dark else QColor(0, 0, 0, 70)
        painter.save()
        painter.fillRect(rect, bg)
        painter.setPen(border)
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        data = self.model().headerData(
            logical_index, self.orientation(), Qt.ItemDataRole.DisplayRole
        )
        text = "" if data is None else str(data)
        painter.setPen(fg)
        painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


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
        self._style_frame_label()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- 顶栏 ---
        top = QHBoxLayout()
        self.btn_open = QPushButton("📂 打开")
        self.btn_save = QPushButton("💾 保存")
        self.lbl_map = QLabel("关卡:")
        self.edit_map = QLineEdit()
        self.edit_map.setPlaceholderText("如 1-7")
        self.edit_map.setMinimumWidth(90)
        self._map_completer = QCompleter()
        self._map_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.edit_map.setCompleter(self._map_completer)
        self._load_level_codes()

        self.toggle_mode = ToggleSwitch("打轴", "对轴")
        self.toggle_mode.toggled.connect(self._on_mode_toggle)

        self.toggle_speed = ToggleSwitch("自动变速", "手动变速")
        self.toggle_speed.toggled.connect(self._on_speed_toggle)

        self.lbl_frame = QLabel("--")
        f = QFont("Consolas", 14)
        f.setBold(True)
        self.lbl_frame.setFont(f)

        self.btn_help = QPushButton("❓ 快捷键")
        self.btn_help.setToolTip("F8：标记部署\nF9：标记技能\nF10：标记撤退")
        self.btn_help.setToolTipDuration(10000)
        self.btn_help.installEventFilter(self)

        top.addWidget(self.btn_open)
        top.addWidget(self.btn_save)
        top.addWidget(self.lbl_map)
        top.addWidget(self.edit_map, 1)
        top.addSpacing(8)
        top.addWidget(self.toggle_mode)
        top.addSpacing(8)
        top.addWidget(self.toggle_speed)
        top.addStretch(1)
        top.addWidget(self.lbl_frame)
        top.addSpacing(8)
        top.addWidget(self.btn_help)
        layout.addLayout(top)

        # --- 时间轴 canvas ---
        self.canvas.set_timeline(self.timeline)
        layout.addWidget(self.canvas)

        # --- 中部：动作列表 + 右侧面板（候选区 + 编辑面板）---
        middle = QHBoxLayout()

        # 动作列表
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeader(_TimelineHeader(Qt.Orientation.Horizontal, self.table))
        self.table.viewport().setStyleSheet("background: transparent;")
        self.table.setHorizontalHeaderLabels(["时间", "类型", "干员", "位置", "朝向"])
        self._style_table_scrollbars()
        self.table.verticalHeader().hide()  # 行号列冗余（已有时间列），隐藏后更像轻量面板
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setMinimumWidth(320)
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
        apply_themed_scrollbar(self.list_candidates, "QListWidget { background: transparent; }")
        cl.addWidget(self.list_candidates)
        right_splitter.addWidget(cand_box)

        # --- 编辑面板 ---
        panel = QWidget()
        panel.setMaximumHeight(120)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        p_layout = QGridLayout(panel)
        p_layout.setContentsMargins(4, 4, 4, 4)
        p_layout.setVerticalSpacing(2)
        # 固定列宽：标签列不缩放，控件列填满（隐藏行时不影响列宽）
        p_layout.setColumnMinimumWidth(0, 40)
        p_layout.setColumnStretch(0, 0)
        p_layout.setColumnStretch(1, 1)

        p_layout.addWidget(QLabel("类型:"), 0, 0)
        self.cb_type = QComboBox()
        self.cb_type.addItems(["部署", "技能", "撤退", "变速"])
        self.lbl_speed = QLabel("速度:")
        p_layout.addWidget(self.lbl_speed, 4, 0)
        self.cb_speed = QComboBox()
        self.cb_speed.addItems(["1x", "2x"])
        p_layout.addWidget(self.cb_speed, 4, 1)
        # 默认隐藏变速相关（自动模式下不需要）
        self.lbl_speed.hide()
        self.cb_speed.hide()
        p_layout.addWidget(self.cb_type, 0, 1)

        self.lbl_oper = QLabel("干员:")
        p_layout.addWidget(self.lbl_oper, 1, 0)
        self.cb_oper = QComboBox()
        self.cb_oper.setEditable(True)
        oper_completer = QCompleter()
        oper_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.cb_oper.setCompleter(oper_completer)
        p_layout.addWidget(self.cb_oper, 1, 1)

        self.lbl_pos = QLabel("位置:")
        p_layout.addWidget(self.lbl_pos, 2, 0)
        pos_row = QHBoxLayout()
        pos_row.setContentsMargins(0, 0, 0, 0)
        pos_row.setSpacing(2)
        self.edit_pos = QLineEdit()
        self.edit_pos.setFixedWidth(48)
        self.edit_pos.setPlaceholderText("D2")
        self.btn_pick = QPushButton("📍")
        self.btn_pick.setFixedWidth(28)
        pos_row.addWidget(self.edit_pos)
        pos_row.addWidget(self.btn_pick)
        self.pos_w = QWidget()
        self.pos_w.setFixedWidth(78)
        self.pos_w.setLayout(pos_row)
        p_layout.addWidget(self.pos_w, 2, 1, Qt.AlignmentFlag.AlignLeft)

        self.lbl_dir = QLabel("朝向:")
        p_layout.addWidget(self.lbl_dir, 3, 0)
        self.cb_dir = QComboBox()
        self.cb_dir.addItems(["无", "上", "下", "左", "右"])
        p_layout.addWidget(self.cb_dir, 3, 1)

        self.btn_apply = QPushButton("✓ 应用")
        self.btn_delete = QPushButton("✕ 删除")
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(4)
        btn_row.addWidget(self.btn_apply)
        btn_row.addWidget(self.btn_delete)
        btn_w = QWidget()
        btn_w.setLayout(btn_row)
        p_layout.addWidget(btn_w, 5, 0, 1, 2)
        right_splitter.addWidget(panel)

        # 上方候选列表吃掉竖向剩余空间；下方编辑面板按内容高度，避免空白挤占候选区。
        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setSizes([440, 120])
        right_splitter.setMaximumWidth(240)
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
        self.btn_help.clicked.connect(self._show_shortcut_tip)
        self.cb_type.currentTextChanged.connect(self._on_type_changed)
        self.table.currentItemChanged.connect(self._on_select)
        self.btn_cand_add.clicked.connect(self._add_candidate)
        self.btn_cand_del.clicked.connect(self._del_candidate)
        self.edit_cand.returnPressed.connect(self._add_candidate)
        self.list_candidates.currentRowChanged.connect(self._on_candidate_selected)

        # 初始化：默认自动变速 → 移除"变速"选项 + 隐藏速度选择
        self._on_speed_mode_changed(False)

    def _style_frame_label(self) -> None:
        """帧数显示用强调青色，按当前主题明暗选亮青/深青（浅底可读）。"""
        is_dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        self.lbl_frame.setStyleSheet(f"color: {'#00e5ff' if is_dark else '#00789a'};")

    def _style_table_scrollbars(self) -> None:
        """打轴表格透明时，表头/滚动条也按主题着色，避免平台默认凸起样式突兀。"""
        is_dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        selection = "rgba(61, 125, 206, 115)" if is_dark else "rgba(61, 125, 206, 75)"
        self.table.horizontalHeader().update()
        base = (
            "QTableWidget { background: transparent; border: none; }"
            f"QTableWidget::item:selected {{ background: {selection}; }}"
        )
        apply_themed_scrollbar(self.table, base)

    def _show_shortcut_tip(self) -> None:
        """主动显示快捷键说明：悬停/点击都能看到，不依赖系统 tooltip 延迟。"""
        pos = self.btn_help.mapToGlobal(self.btn_help.rect().bottomLeft())
        QToolTip.showText(pos, self.btn_help.toolTip(), self.btn_help, self.btn_help.rect(), 10000)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.btn_help and event.type() == QEvent.Type.Enter:
            self._show_shortcut_tip()
        return super().eventFilter(obj, event)

    def _refresh_theme_styles(self) -> None:
        """主题切换后同步刷新自定义 QSS，避免表头保留上一主题颜色。"""
        self._style_frame_label()
        self._style_table_scrollbars()

    def changeEvent(self, event: QEvent) -> None:
        # 主题切换 → palette 变 → 帧数标签/表头换适配色
        if event.type() == QEvent.Type.PaletteChange:
            self._refresh_theme_styles()
        super().changeEvent(event)

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
        """从 timeline 数据加载候选列表（candidates 字段 + 已有动作里的干员名）。"""
        cands = list(getattr(self.timeline, "candidates", None) or [])
        # 补充动作里用到但不在 candidates 里的干员/装置
        for a in self.timeline.actions:
            if a.oper and a.oper not in cands:
                cands.append(a.oper)
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

    def _on_mode_toggle(self, is_left: bool) -> None:
        """打轴(left)/对轴(right) toggle。"""
        self._align_mode = not is_left
        if self._align_mode:
            self.canvas.set_current_frame(self._current_frame)

    def _on_speed_toggle(self, is_left: bool) -> None:
        """自动(left)/手动(right)变速 toggle。"""
        self._on_speed_mode_changed(not is_left)

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
        align = Qt.AlignmentFlag.AlignCenter
        self.table.setRowCount(len(self.timeline.actions))
        for i, a in enumerate(self.timeline.actions):
            time_str = f"{a.frame}" if a.frame is not None else f"{a.time}s"
            item0 = QTableWidgetItem(time_str)
            item0.setTextAlignment(align)
            self.table.setItem(i, 0, item0)
            item1 = QTableWidgetItem(a.action_type.value)
            item1.setTextAlignment(align)
            self.table.setItem(i, 1, item1)
            if a.action_type == ActionType.SPEED:
                item2 = QTableWidgetItem(f"{a.speed or 1}x")
                item2.setTextAlignment(align)
                self.table.setItem(i, 2, item2)
                item3 = QTableWidgetItem("—")
                item3.setTextAlignment(align)
                self.table.setItem(i, 3, item3)
            else:
                item2 = QTableWidgetItem(a.oper)
                item2.setTextAlignment(align)
                self.table.setItem(i, 2, item2)
                item3 = QTableWidgetItem(a.pos)
                item3.setTextAlignment(align)
                self.table.setItem(i, 3, item3)
            if a.action_type == ActionType.DEPLOY and a.direction != DirectionType.NONE:
                dir_text = a.direction.value
            else:
                dir_text = "—"
            item4 = QTableWidgetItem(dir_text)
            item4.setTextAlignment(align)
            self.table.setItem(i, 4, item4)
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

    def _on_speed_mode_changed(self, manual: bool) -> None:
        """自动/手动变速切换 → 显示/隐藏变速动作类型和速度选择。"""
        if manual:
            # 手动模式：类型下拉加"变速"，速度选择可见
            if self.cb_type.findText("变速") < 0:
                self.cb_type.addItem("变速")
            self._update_speed_visibility()
        else:
            # 自动模式：移除"变速"，隐藏速度选择
            idx = self.cb_type.findText("变速")
            if idx >= 0:
                self.cb_type.removeItem(idx)
            self.lbl_speed.hide()
            self.cb_speed.hide()

    def _update_speed_visibility(self) -> None:
        """手动模式下，类型=变速时显示速度选择，否则隐藏。"""
        is_speed = self.cb_type.currentText() == "变速"
        self.lbl_speed.setVisible(is_speed)
        self.cb_speed.setVisible(is_speed)

    def _on_type_changed(self, type_text: str) -> None:
        """动作类型变化 → 显示/隐藏相关字段。"""
        is_deploy = type_text == "部署"
        is_speed = type_text == "变速"
        show_oper = not is_speed
        show_pos = not is_speed
        show_dir = is_deploy
        # 干员
        self.lbl_oper.setVisible(show_oper)
        self.cb_oper.setVisible(show_oper)
        if not show_oper:
            self.cb_oper.clearEditText()
        # 位置
        self.lbl_pos.setVisible(show_pos)
        self.pos_w.setVisible(show_pos)
        if not show_pos:
            self.edit_pos.clear()
        # 朝向
        self.lbl_dir.setVisible(show_dir)
        self.cb_dir.setVisible(show_dir)
        if not show_dir:
            self.cb_dir.setCurrentText("无")
        # 手动模式下更新速度选择可见性
        if not self.toggle_speed.is_left():
            self._update_speed_visibility()

    def _on_select(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.timeline.actions):
            return
        a = self.timeline.actions[row]
        self.cb_type.setCurrentText(a.action_type.value)
        self.cb_oper.setCurrentText(a.oper)
        self.edit_pos.setText(a.pos)
        self.cb_dir.setCurrentText(a.direction.value)
        self.cb_speed.setCurrentIndex((a.speed or 1) - 1)
        self._on_type_changed(a.action_type.value)

    def _on_apply(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.timeline.actions):
            return
        a = self.timeline.actions[row]
        a.action_type = ActionType(self.cb_type.currentText())
        a.oper = self.cb_oper.currentText().strip()
        a.pos = self.edit_pos.text().strip()
        a.direction = DirectionType(self.cb_dir.currentText())
        a.speed = self.cb_speed.currentIndex() + 1 if a.action_type == ActionType.SPEED else None
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
        self.toggle_speed.set_left(self.timeline.speed_mode != "manual")
        self._on_speed_mode_changed(self.timeline.speed_mode == "manual")
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
        self.timeline.speed_mode = "manual" if not self.toggle_speed.is_left() else "auto"
        self._collect_candidates()
        save_timeline(self.timeline, path)
        self.lbl_status.setText(f"已保存到 {path}")
