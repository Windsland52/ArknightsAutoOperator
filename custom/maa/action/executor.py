"""帧级执行器（进程内 Custom action）。

pipeline 节点：
    {"action": "Custom", "custom_action": "ExecuteTimeline",
     "custom_action_param": {"timeline": [...], "calibration": "...", "map_code": "1-7"}}

内部流程（每个 action）：
1. 读费用条时间 → 逼近目标帧
2. 进入子弹时间（选中干员/暂停）
3. 逐帧步进到精确帧（PC: ESC→precise_sleep→Space / 模拟器: Esc+click steptiny）
4. 执行 deploy/skill/retreat

平台感知：
- Win32（PC 客户端）：键盘 E 技能 / Q 撤退 / 定时过帧（AFA 路线）
- ADB（模拟器）：鼠标比例技能/撤退 / steptiny Esc+点击（prts-plus 路线）
"""

from __future__ import annotations

import json
import logging
import time

from maa.context import Context
from maa.controller import Controller
from maa.custom_action import CustomAction

from custom import config
from custom.core.avatar import (
    detect_slots,
    has_avatar,
    learn_avatar_from_slot,
    locate_avatar,
)
from custom.core.battle.action import Action, ActionType, DirectionType
from custom.core.battle.game_time import GameTime
from custom.core.geometry.convert_pos import convert_position
from custom.core.geometry.map_loader import load_map
from custom.core.geometry.view import transform_map_to_view
from custom.core.timing.calibration import load as load_calibration
from custom.core.timing.precise_sleep import precise_sleep_ms
from custom.core.timing.time_source import TimeSource

logger = logging.getLogger(__name__)

# VK codes (PC 客户端原生按键)
_VK = {
    "escape": 0x1B,
    "space": 0x20,
    "e": 0x45,
    "q": 0x51,
    "d": 0x44,
}


class ExecuteTimeline(CustomAction):
    """执行时间轴上所有动作。"""

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
        is_pc = self._detect_platform(ctrl)

        for i, action in enumerate(actions):
            if context.tasker.stopping:
                logger.info("用户停止")
                return CustomAction.RunResult(success=False)

            logger.info("[%d/%d] %s", i + 1, len(actions), action)
            self._perform_action(context, ctrl, action, time_source, is_pc)

        return CustomAction.RunResult(success=True)

    def _parse_actions(self, raw: list[dict], map_data: dict) -> list[Action]:
        """解析 JSON 动作列表 → Action 对象（含投影坐标）。"""
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
            else:
                cost_val = item.get("cost")
                tick_val = item.get("tick")

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

            # 棋盘坐标 → 格子 → 投影
            if a.pos:
                col, row = convert_position(a.pos, h, w)
                a.tile_pos = (col, row)
                a.view_pos_front = front[row][col]
                a.view_pos_side = side[row][col]

            actions.append(a)

        return actions

    def _locate_oper(self, context: Context, oper_name: str) -> tuple[float, float] | None:
        """定位干员头像。MAA 方案：检测槽位 → TemplateMatch 匹配。

        无缓存头像时：检测槽位 → 从 LAST_OPER_RATIO 最近槽位学习。
        """
        img = context.tasker.controller.post_screencap().wait().get()

        # 有缓存 → 直接匹配
        if has_avatar(oper_name):
            pos = locate_avatar(context, img, oper_name)
            if pos is not None:
                return pos
            logger.warning("干员 %s 有缓存但未匹配，尝试重新学习", oper_name)

        # 无缓存或匹配失败 → 检测槽位 → 从最近槽位学习
        logger.info("干员 %s 学习头像...", oper_name)
        slots = detect_slots(context, img)
        if not slots:
            logger.error("未检测到干员槽位")
            return None

        # 选择最右边的槽位（LAST_OPER_RATIO 附近）学习
        last_slot = slots[-1]
        if learn_avatar_from_slot(img, last_slot, oper_name):
            logger.info("头像学习成功，重新定位")
            return locate_avatar(context, img, oper_name)

        logger.error("头像学习失败")
        return None

    def _detect_platform(self, ctrl: Controller) -> bool:
        """检测是否 PC 客户端（Win32）。"""
        info = ctrl.info
        is_win32 = info.get("type") == "win32"
        logger.info("平台: %s", "PC(Win32)" if is_win32 else "模拟器(ADB)")
        return is_win32

    def _perform_action(
        self, context: Context, ctrl: Controller, action: Action, ts: TimeSource, is_pc: bool
    ) -> None:
        """执行单个动作：逼近目标帧 → 子弹时间 → 逐帧 → 操作。"""
        target = action.get_game_time()
        bullet_threshold = GameTime(tick=config.BULLET_THRESHOLD)

        # 读当前时间
        current = self._read_time(ctrl, ts)
        if current is None:
            logger.warning("无法读时间，直接执行")
            current = target

        logger.info("当前 %s → 目标 %s", current, target)

        # 逼近：当前 + bullet_threshold < target → 需要逼近
        if current + bullet_threshold < target:
            logger.debug("距离目标较远，等待接近")
            self._wait_until(
                ctrl, ts, target - bullet_threshold, context_tasker_stopping=lambda: False
            )

        # 进入子弹时间（选中最后操作的干员）
        self._enter_bullet_time(ctrl, is_pc)
        time.sleep(config.GENERAL_WAIT_MS / 1000)

        # 逐帧步进到目标
        self._step_to_target(ctrl, ts, target, is_pc)

        # 执行动作
        if action.action_type == ActionType.DEPLOY:
            self._deploy(context, ctrl, action, is_pc)
        elif action.action_type == ActionType.SKILL:
            self._skill(ctrl, action, is_pc)
        elif action.action_type == ActionType.RETREAT:
            self._retreat(ctrl, action, is_pc)

    def _read_time(self, ctrl: Controller, ts: TimeSource) -> GameTime | None:
        """截图 → tick → TimeSource → GameTime。"""
        img = ctrl.post_screencap().wait().get()
        lf = ts.update(img)
        if lf is None:
            return None
        return GameTime(cost=0, tick=lf, time=None)  # 简化：只用周期内 tick

    def _wait_until(
        self, ctrl: Controller, ts: TimeSource, target: GameTime, context_tasker_stopping
    ) -> None:
        """等待直到费用条时间到达 target。"""
        deadline = time.time() + 120  # 2 分钟超时
        while time.time() < deadline:
            if context_tasker_stopping():
                return
            img = ctrl.post_screencap().wait().get()
            lf = ts.update(img)
            if lf is not None and GameTime(tick=lf) >= target:
                return
            time.sleep(0.016)

    def _enter_bullet_time(self, ctrl: Controller, is_pc: bool) -> None:
        """进入子弹时间。PC: Space（暂停后选中干员）。模拟器: 点击最后干员。"""
        if is_pc:
            # PC：先确保暂停，然后 Space 进入子弹时间
            ctrl.post_click_key(_VK["escape"]).wait()
            precise_sleep_ms(30)
            ctrl.post_click_key(_VK["space"]).wait()
        else:
            # 模拟器：点击最后操作干员位置进入慢速
            ctrl.post_click(
                int(config.LAST_OPER_RATIO[0] * 1280), int(config.LAST_OPER_RATIO[1] * 720)
            ).wait()
        time.sleep(config.GENERAL_WAIT_MS / 1000)

    def _step_to_target(
        self, ctrl: Controller, ts: TimeSource, target: GameTime, is_pc: bool
    ) -> None:
        """逐帧步进到精确目标帧。"""
        max_steps = 60
        for _ in range(max_steps):
            img = ctrl.post_screencap().wait().get()
            lf = ts.update(img)
            if lf is None:
                break
            if GameTime(tick=lf) >= target:
                logger.debug("到达目标帧 %d", lf)
                return
            self._step_one_frame(ctrl, is_pc)
            time.sleep(config.GENERAL_WAIT_MS / 1000)
        logger.warning("逐帧步进超时")

    def _step_one_frame(self, ctrl: Controller, is_pc: bool) -> None:
        """推进 1 帧。PC: ESC→30ms→Space。模拟器: Esc+click steptiny。"""
        if is_pc:
            ctrl.post_click_key(_VK["escape"]).wait()
            precise_sleep_ms(config.PC_STEP_1X_MS)
            ctrl.post_click_key(_VK["space"]).wait()
            precise_sleep_ms(config.PC_KEY_DELAY_MS)
        else:
            # 模拟器 steptiny: Esc + click center
            ctrl.post_click_key(_VK["escape"]).wait()
            time.sleep(0.05)
            ctrl.post_click(640, 360).wait()
            time.sleep(0.05)

    def _deploy(self, context: Context, ctrl: Controller, action: Action, is_pc: bool) -> None:
        """部署干员。"""
        if action.view_pos_side is None or action.oper is None:
            logger.error("部署缺少坐标/干员")
            return

        logger.info("部署 %s at %s", action.oper, action.pos)

        # 定位干员头像（MAA TemplateMatch，回退到 LAST_OPER_RATIO）
        avatar_pos = self._locate_oper(context, action.oper)
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

        if is_pc:
            # PC: 左键拖拽
            ctrl.post_touch_down(avatar_x, avatar_y, 0, 1).wait()
            time.sleep(0.05)
            ctrl.post_touch_move(deploy_x, deploy_y, 0, 1).wait()
            time.sleep(0.05)
            ctrl.post_touch_up(0).wait()
        else:
            # 模拟器: 右键拖拽（contact=1）
            ctrl.post_touch_down(avatar_x, avatar_y, 1, 1).wait()
            time.sleep(0.1)
            ctrl.post_touch_move(deploy_x, deploy_y, 1, 1).wait()
            time.sleep(0.1)
            ctrl.post_touch_up(1).wait()

        time.sleep(config.GENERAL_WAIT_MS / 1000)
        self._set_direction(ctrl, action, is_pc)

    def _set_direction(self, ctrl: Controller, action: Action, is_pc: bool) -> None:
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

    def _skill(self, ctrl: Controller, action: Action, is_pc: bool) -> None:
        """技能。"""
        logger.info("技能 %s", action.oper)
        if is_pc:
            # PC: 点击干员位置 → 按 E
            if action.view_pos_front:
                ctrl.post_click(
                    int(action.view_pos_front[0] * 1280),
                    int(action.view_pos_front[1] * 720),
                ).wait()
                time.sleep(0.05)
            ctrl.post_click_key(_VK["e"]).wait()
        else:
            # 模拟器: 点击干员 → 点击技能按钮比例
            if action.view_pos_front:
                ctrl.post_click(
                    int(action.view_pos_front[0] * 1280),
                    int(action.view_pos_front[1] * 720),
                ).wait()
                time.sleep(0.1)
            ctrl.post_click(
                int(config.SKILL_RATIO[0] * 1280),
                int(config.SKILL_RATIO[1] * 720),
            ).wait()
        time.sleep(config.GENERAL_WAIT_MS / 1000)

    def _retreat(self, ctrl: Controller, action: Action, is_pc: bool) -> None:
        """撤退。"""
        logger.info("撤退 %s", action.oper)
        if is_pc:
            if action.view_pos_front:
                ctrl.post_click(
                    int(action.view_pos_front[0] * 1280),
                    int(action.view_pos_front[1] * 720),
                ).wait()
                time.sleep(0.05)
            ctrl.post_click_key(_VK["q"]).wait()
        else:
            if action.view_pos_front:
                ctrl.post_click(
                    int(action.view_pos_front[0] * 1280),
                    int(action.view_pos_front[1] * 720),
                ).wait()
                time.sleep(0.1)
            ctrl.post_click(
                int(config.RETREAT_RATIO[0] * 1280),
                int(config.RETREAT_RATIO[1] * 720),
            ).wait()
        time.sleep(config.GENERAL_WAIT_MS / 1000)
