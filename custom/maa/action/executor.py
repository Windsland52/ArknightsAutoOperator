"""帧级执行器（进程内 Custom action）。

pipeline 节点：
    {"action": "Custom", "custom_action": "ExecuteTimeline",
     "custom_action_param": {"timeline": [...], "calibration": "...", "map_code": "1-7"}}

内部流程（每个 action）：
1. 读费用条累计帧 → 逼近目标帧（运行中等待）
2. 到达 bullet 阈值 → 暂停
3. 逐帧步进到精确帧
4. 暂停下执行 deploy/skill/retreat
5. 保持暂停，进入下一个动作（pause invariant）

平台：Win32（PC 客户端）。
- 暂停/步进/技能/撤退全部经 AFA 热键（见 custom.core.afa_hotkey）。
- 部署拖拽 + 朝向用 MAA post_touch。
- AFA 需独立常驻运行，游戏窗口须前台。执行器需管理员权限（PostMessage）。
"""

from __future__ import annotations

import json
import logging
import time

from maa.context import Context
from maa.controller import Controller
from maa.custom_action import CustomAction

from custom import config
from custom.core import afa_hotkey
from custom.core.avatar import locate_oper
from custom.core.battle.action import Action, ActionType, DirectionType
from custom.core.geometry.convert_pos import convert_position
from custom.core.geometry.map_loader import load_map
from custom.core.geometry.view import transform_map_to_view
from custom.core.timing.calibration import load as load_calibration
from custom.core.timing.time_source import TimeSource

logger = logging.getLogger(__name__)


class ExecuteTimeline(CustomAction):
    """执行时间轴上所有动作。"""

    # 运行时状态（_execute 中初始化）
    _paused: bool = False
    _hwnd: int | None = None

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            params = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
            # MAA 可能双重 JSON 编码 custom_action_param
            if isinstance(params, str):
                params = json.loads(params)
            return self._execute(context, params)
        except Exception:
            logger.exception("ExecuteTimeline 异常")
            return CustomAction.RunResult(success=False)

    def _execute(self, context: Context, params: dict) -> CustomAction.RunResult:
        ctrl = context.tasker.controller
        raw_actions = params.get("timeline", [])
        calib_file = params.get("calibration", "")
        map_code = params.get("map_code", "")

        if not raw_actions or not calib_file or not map_code:
            logger.error("缺少参数: timeline/calibration/map_code")
            return CustomAction.RunResult(success=False)

        # 加载数据
        calib = load_calibration(calib_file)
        map_data = load_map(map_code)
        if map_data is None:
            logger.error("无法加载关卡 %s", map_code)
            return CustomAction.RunResult(success=False)

        actions = self._parse_actions(raw_actions, map_data)
        if not actions:
            logger.error("无有效动作")
            return CustomAction.RunResult(success=False)

        logger.info("执行 %d 个动作 (关卡 %s)", len(actions), map_code)

        # 创建 TimeSource（用执行器自己的截图循环驱动）
        time_source = TimeSource(calib)

        # AFA 需要游戏窗口前台 + 暂停状态机
        self._paused = False
        self._hwnd = afa_hotkey.find_game_window()
        if self._hwnd is None:
            logger.error("未找到「明日方舟」窗口，AFA 热键无法生效")
            return CustomAction.RunResult(success=False)
        afa_hotkey.activate(self._hwnd)
        logger.info("游戏窗口 HWND=%s，已激活（AFA 热键就绪）", self._hwnd)

        for i, action in enumerate(actions):
            if context.tasker.stopping:
                logger.info("用户停止")
                return CustomAction.RunResult(success=False)

            logger.info("[%d/%d] %s", i + 1, len(actions), action)
            self._perform_action(context, ctrl, action, time_source)

        # 全部动作执行完，恢复游戏运行
        self._resume()
        return CustomAction.RunResult(success=True)

    def _parse_actions(self, raw: list[dict], map_data: dict) -> list[Action]:
        """解析 JSON 动作列表 → Action 对象（含投影坐标 + 目标帧）。"""
        h, w = map_data["height"], map_data["width"]
        front = transform_map_to_view(map_data, side=False)
        side = transform_map_to_view(map_data, side=True)

        actions: list[Action] = []
        for item in raw:
            # 时间坐标转换：frame → cost + tick（TICK_MAX=30）
            frame = item.get("frame")
            if frame is not None:
                cost_val = frame // config.TICK_MAX_DEFAULT
                tick_val = frame % config.TICK_MAX_DEFAULT
                target_frame = int(frame)
            else:
                cost_val = item.get("cost")
                tick_val = item.get("tick")
                target_frame = (cost_val or 0) * config.TICK_MAX_DEFAULT + (tick_val or 0)

            a = Action(
                cost=cost_val,
                tick=tick_val,
                time=item.get("time"),
                action_type=ActionType(item["action_type"]) if "action_type" in item else None,
                oper=item.get("oper"),
                pos=item.get("pos"),
                direction=DirectionType(item["direction"]) if "direction" in item else None,
                alias=item.get("alias"),
            )
            if not a.is_valid():
                logger.warning("跳过无效动作: %s", a)
                continue

            # 存储原始帧号用于计时
            a.target_frame = target_frame

            # 棋盘坐标 → 格子 → 投影
            if a.pos:
                col, row = convert_position(a.pos, h, w)
                a.tile_pos = (col, row)
                a.view_pos_front = front[row][col]
                a.view_pos_side = side[row][col]

            actions.append(a)

        return actions

    def _locate_oper(
        self, context: Context, ctrl: Controller, oper_name: str
    ) -> tuple[float, float] | None:
        """定位干员头像。MAA 方案：检测槽位 → 有缓存 TemplateMatch → 无缓存 点击+OCR+存头像。"""
        return locate_oper(context, ctrl, oper_name)

    def _perform_action(
        self, context: Context, ctrl: Controller, action: Action, ts: TimeSource
    ) -> None:
        """执行单个动作：逼近目标帧 → 暂停 → 逐帧 → 操作（暂停下）。

        维持 pause invariant：动作执行期间保持暂停，只有逼近阶段才恢复。
        动作结束时不恢复暂停，留给下一个动作。
        """
        target_frame = action.target_frame
        bullet_threshold = config.BULLET_THRESHOLD

        # 读当前累计帧
        current = self._read_frames(ctrl, ts)
        if current is None:
            logger.warning("无法读时间，直接执行")
            current = target_frame

        logger.info("当前帧 %d → 目标帧 %d", current, target_frame)

        # 逼近：当前 + threshold < target → 恢复运行，等待接近
        if current + bullet_threshold < target_frame:
            logger.debug("距离目标 %d 帧，等待", target_frame - current)
            self._resume()
            self._wait_until_frames(
                ctrl, ts, target_frame - bullet_threshold, context_tasker_stopping=lambda: False
            )

        # 到达 bullet 阈值 → 暂停（AFA F 键）
        self._pause()
        time.sleep(config.GENERAL_WAIT_MS / 1000)

        # 逐帧步进到目标
        self._step_to_frames(ctrl, ts, target_frame)

        # 执行动作（此时游戏已暂停）
        if action.action_type == ActionType.DEPLOY:
            self._deploy(context, ctrl, action)
        elif action.action_type == ActionType.SKILL:
            self._skill(ctrl, action)
        elif action.action_type == ActionType.RETREAT:
            self._retreat(ctrl, action)

    def _read_frames(self, ctrl: Controller, ts: TimeSource) -> int | None:
        """截图 → TimeSource → 累计帧。"""
        img = ctrl.post_screencap().wait().get()
        lf = ts.update(img)
        if lf is None:
            return None
        return ts.total_elapsed_frames

    def _wait_until_frames(
        self, ctrl: Controller, ts: TimeSource, target_frame: int, context_tasker_stopping
    ) -> None:
        """等待直到累计帧到达 target_frame。"""
        deadline = time.time() + 120
        while time.time() < deadline:
            if context_tasker_stopping():
                return
            img = ctrl.post_screencap().wait().get()
            lf = ts.update(img)
            if lf is not None and ts.total_elapsed_frames >= target_frame:
                return
            time.sleep(0.016)

    # --- 暂停状态机（经 AFA 热键） ---

    def _pause(self) -> None:
        """暂停游戏。幂等：已暂停则不发。"""
        if self._paused:
            return
        afa_hotkey.tap_key(afa_hotkey.VK_F)  # AFA: 按下暂停（ESC 脉冲）
        self._paused = True

    def _resume(self) -> None:
        """恢复游戏运行。幂等：已运行则不发。"""
        if not self._paused:
            return
        afa_hotkey.tap_key(afa_hotkey.VK_SPACE)  # AFA: 松开暂停（Space 脉冲）
        self._paused = False

    def _step_to_frames(
        self, ctrl: Controller, ts: TimeSource, target_frame: int
    ) -> None:
        """逐帧步进到累计帧 target_frame（游戏须已暂停）。

        发 AFA R 键（Action33ms），AFA 要求光标在游戏客户区内。
        """
        max_steps = 90
        for _ in range(max_steps):
            img = ctrl.post_screencap().wait().get()
            lf = ts.update(img)
            if lf is None:
                break
            if ts.total_elapsed_frames >= target_frame:
                logger.debug("到达目标帧 %d", target_frame)
                return
            self._step_one_frame()
            time.sleep(config.GENERAL_WAIT_MS / 1000)
        logger.warning("逐帧步进超时（目标帧 %d）", target_frame)

    def _step_one_frame(self) -> None:
        """推进 1 帧（游戏须已暂停）。发 AFA R 键。"""
        self._ensure_cursor_in_game()
        afa_hotkey.tap_key(afa_hotkey.VK_R)  # AFA: 前进 33ms

    def _ensure_cursor_in_game(self) -> None:
        """把真实光标移到游戏客户区中心（满足 AFA IsMouseInClient）。"""
        if self._hwnd is not None:
            afa_hotkey.move_cursor(self._hwnd, 0.5, 0.5)

    def _move_cursor_to_unit(self, action: Action) -> None:
        """把真实光标移到干员正面投影位置（W 暂停选中用）。"""
        if self._hwnd is None or action.view_pos_front is None:
            self._ensure_cursor_in_game()
            return
        afa_hotkey.move_cursor(self._hwnd, action.view_pos_front[0], action.view_pos_front[1])

    def _deploy(self, context: Context, ctrl: Controller, action: Action) -> None:
        """部署干员（游戏须已暂停）。"""
        if action.view_pos_side is None or action.oper is None:
            logger.error("部署缺少坐标/干员")
            return

        logger.info("部署 %s at %s", action.oper, action.pos)

        # 定位干员头像（MAA TemplateMatch，回退到 LAST_OPER_RATIO）
        avatar_pos = self._locate_oper(context, ctrl, action.oper)
        if avatar_pos is not None:
            avatar_x = int(avatar_pos[0] * 1280)
            avatar_y = int(avatar_pos[1] * 720)
        else:
            logger.warning("头像定位失败，回退到 LAST_OPER_RATIO")
            avatar_x = int(config.LAST_OPER_RATIO[0] * 1280)
            avatar_y = int(config.LAST_OPER_RATIO[1] * 720)

        # 部署位置
        deploy_x = int(action.view_pos_side[0] * 1280)
        deploy_y = int(action.view_pos_side[1] * 720 + int(config.DEPLOY_DELTA_RATIO * 720))

        # 左键拖拽（PostMessage）
        ctrl.post_touch_down(avatar_x, avatar_y, 0, 1).wait()
        time.sleep(0.05)
        ctrl.post_touch_move(deploy_x, deploy_y, 0, 1).wait()
        time.sleep(0.05)
        ctrl.post_touch_up(0).wait()

        time.sleep(config.GENERAL_WAIT_MS / 1000)
        self._set_direction(ctrl, action)

    def _set_direction(self, ctrl: Controller, action: Action) -> None:
        """设置朝向。"""
        if action.direction is None or action.direction == DirectionType.NONE:
            return
        if action.view_pos_side is None:
            return

        x = action.view_pos_side[0]
        y = action.view_pos_side[1]
        d = config.DIRECTION_RATIO

        if action.direction == DirectionType.LEFT:
            dx, dy = -d, 0
        elif action.direction == DirectionType.RIGHT:
            dx, dy = d, 0
        elif action.direction == DirectionType.UP:
            dx, dy = 0, -d
        elif action.direction == DirectionType.DOWN:
            dx, dy = 0, d
        else:
            return

        x1 = int(x * 1280)
        y1 = int(y * 720)
        x2 = int(max(0, min(1, x + dx)) * 1280)
        y2 = int(max(0, min(1, y + dy)) * 720)

        ctrl.post_touch_down(x1, y1, 0, 1).wait()
        time.sleep(0.05)
        ctrl.post_touch_move(x2, y2, 0, 1).wait()
        time.sleep(0.05)
        ctrl.post_touch_up(0).wait()
        time.sleep(config.GENERAL_WAIT_MS / 1000)

    def _skill(self, ctrl: Controller, action: Action) -> None:
        """技能（游戏须已暂停）。光标移到单位 → W 暂停选中 → S 发 E。"""
        logger.info("技能 %s", action.oper)
        self._move_cursor_to_unit(action)
        afa_hotkey.tap_key(afa_hotkey.VK_W)  # 暂停选中
        time.sleep(config.GENERAL_WAIT_MS / 1000)
        afa_hotkey.tap_key(afa_hotkey.VK_S)  # 单位技能（发 E）
        time.sleep(config.GENERAL_WAIT_MS / 1000)

    def _retreat(self, ctrl: Controller, action: Action) -> None:
        """撤退（游戏须已暂停）。光标移到单位 → W 暂停选中 → A 发 Q。"""
        logger.info("撤退 %s", action.oper)
        self._move_cursor_to_unit(action)
        afa_hotkey.tap_key(afa_hotkey.VK_W)  # 暂停选中
        time.sleep(config.GENERAL_WAIT_MS / 1000)
        afa_hotkey.tap_key(afa_hotkey.VK_A)  # 单位撤退（发 Q）
        time.sleep(config.GENERAL_WAIT_MS / 1000)
