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
- 暂停/步进/技能/撤退全部经 AFA 热键（见 aao.core.afa_hotkey）。
- 部署拖拽 + 朝向用 MAA post_touch。
- AFA 需独立常驻运行，游戏窗口须前台。执行器需管理员权限（PostMessage）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from maa.context import Context
from maa.controller import Controller
from maa.custom_action import CustomAction

from aao import config
from aao.core import afa_hotkey
from aao.core.avatar import locate_oper
from aao.core.battle.action import Action, ActionType, DirectionType
from aao.core.geometry.convert_pos import convert_position
from aao.core.geometry.map_loader import load_map
from aao.core.geometry.view import transform_map_to_view
from aao.core.timing.calibration import load as load_calibration
from aao.core.timing.time_source import TimeSource
from aao.utils.logger import logger
from custom.registry import custom_action


@dataclass
class RoundResult:
    """单轮凹图执行结果（ExecuteTimeline 末尾回传给 UI）。

    attempt_count 取自 click_stage 全局计数（本轮已计入）。
    leaked = 中途漏怪（执行器内血量检测，executor._leaked）。
    outcome = 结算结果（UNKNOWN=尚未结算；ExecuteTimeline return 时结算节点
              可能还没命中，UI 应等下一轮或会话结束看历史）。合并：若中途
              已漏怪，outcome 强制为 LEAKED。
    elapsed_frames = 本轮执行器 TimeSource 的累计帧（仅战斗内有效）。
    """

    attempt_count: int
    leaked: bool
    elapsed_frames: int
    outcome: str = "未知"


@custom_action("ExecuteTimeline")
class ExecuteTimeline(CustomAction):
    """执行时间轴上所有动作。"""

    # 运行时状态（_execute 中初始化）
    _paused: bool = False
    _leaked: bool = False
    _hwnd: int | None = None
    _speed: int = 1  # 当前游戏倍速（1 或 2），执行开始强制归 1

    # 单例回调：每轮 _execute 末尾触发（在 MAA tasker 线程）。
    # 调用方负责跨线程转 Qt 信号。None = 不回调。
    on_round_finished: Callable[[RoundResult], None] | None = None

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

        # 优先 timeline_path（从文件加载，文件内含 map_code），兼容显式 timeline 数组
        timeline_path = params.get("timeline_path")
        if timeline_path:
            tl = self._load_timeline_file(timeline_path)
            if tl is None:
                return CustomAction.RunResult(success=False)
            raw_actions = tl.get("actions", [])
            map_code = params.get("map_code") or tl.get("map_code", "")
        else:
            raw_actions = params.get("timeline", [])
            map_code = params.get("map_code", "")

        calib_file = params.get("calibration") or config.DEFAULT_CALIBRATION

        if not raw_actions or not map_code:
            logger.error("缺少参数: 需要 timeline_path 或 (timeline + map_code)")
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
        self._leaked = False
        self._speed = 1  # 假设进战斗默认 1 倍速（速度状态机起点）
        from custom.outcome import reset_outcome

        reset_outcome()  # 每轮开始清结算结果（sink 在结算节点命中时写入）
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

            if self._leaked:
                logger.warning("漏怪，中止剩余动作，放弃本局")
                break

        # 全部动作执行完（或漏怪中止），恢复游戏运行
        self._resume()

        # 最后一个动作执行完，开 2 倍速加速到结算（省时；漏怪中止则不必）
        if not self._leaked:
            self._set_speed(context, 2)

        # 回传本轮结果给 UI（若有回调）。在 tasker 线程触发。
        # 注意：on_round_finished 是被外部赋值的类属性（普通函数/闭包），
        # 经 self. 访问会触发描述符协议变成绑定方法、多注入一个 self。
        # 故从类字典取原始对象直接调用，绕过描述符绑定。
        cb = type(self).__dict__.get("on_round_finished")
        if cb is not None:
            try:
                from custom.outcome import Outcome, get_outcome
                from custom.reco.click_stage import get_attempt_count

                outcome = get_outcome()
                # 中途漏怪优先于结算 outcome（执行器内检测更直接）
                if self._leaked:
                    outcome_str = "漏怪"
                elif outcome is Outcome.UNKNOWN:
                    # 结算节点在 ExecuteTimeline return 之后才命中，此刻尚未结算；
                    # UI 先标"进行中"，待 sink 命中结算节点后单独更新该行。
                    outcome_str = "进行中"
                else:
                    outcome_str = outcome.value
                cb(
                    RoundResult(
                        attempt_count=get_attempt_count(),
                        leaked=self._leaked,
                        elapsed_frames=time_source.total_elapsed_frames,
                        outcome=outcome_str,
                    )
                )
            except Exception:
                logger.exception("on_round_finished 回调失败")

        # 漏怪 = 本局失败（farm pipeline 会走放弃重试）
        return CustomAction.RunResult(success=not self._leaked)

    def _load_timeline_file(self, path: str) -> dict | None:
        """加载时间轴 JSON（纯文件名→config/timelines/，带路径→相对项目根）。"""
        from custom.reco.click_stage import resolve_timeline_path

        p = resolve_timeline_path(path)
        if not p.exists():
            logger.error("时间轴文件不存在: %s", p)
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("时间轴文件解析失败: %s", p)
            return None
        n = len(data.get("actions", []))
        logger.info("加载时间轴 %s（map_code=%s, %d 动作）", p, data.get("map_code"), n)
        return data

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
                context,
                ctrl,
                ts,
                target_frame - bullet_threshold,
                context_tasker_stopping=lambda: False,
            )
            if self._leaked:
                return  # 漏怪，跳过本动作的暂停/逐帧/操作

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
        self,
        context: Context,
        ctrl: Controller,
        ts: TimeSource,
        target_frame: int,
        context_tasker_stopping: Callable[[], bool],
    ) -> None:
        """等待直到累计帧到达 target_frame（运行态）。

        期间按剩余距离动态调速：远（> SPEED_UP_THRESHOLD）开 2x，近则回 1x；
        每 1s 检测一次漏怪。
        """
        deadline = time.time() + 120
        last_leak_check = 0.0
        while time.time() < deadline:
            if context_tasker_stopping():
                return
            img = ctrl.post_screencap().wait().get()
            lf = ts.update(img)
            if lf is not None:
                current = ts.total_elapsed_frames
                remaining = target_frame - current
                # 倍速状态机：开局第一个周期（帧 0-29）不切 2x（费用条未稳）；
                # 之后剩余远(> SPEED_UP_THRESHOLD) → 2x，剩余近 → 1x
                if current < config.TICK_MAX_DEFAULT:
                    want_speed = 1
                else:
                    want_speed = 2 if remaining > config.SPEED_UP_THRESHOLD else 1
                if want_speed != self._speed:
                    self._set_speed(context, want_speed)
                if current >= target_frame:
                    return
            # 漏怪检测：血量图标变红（BattleHpFlag2），低频（1s），不影响计时
            now = time.time()
            if now - last_leak_check >= 1.0:
                last_leak_check = now
                if self._detect_leak(context, img):
                    logger.warning("检测到漏怪（血量图标变红），提前中止时间轴")
                    self._leaked = True
                    return
            time.sleep(0.016)

    def _detect_leak(self, context: Context, img: np.ndarray) -> bool:
        """漏怪检测：血量图标变红（BattleHpFlag2）命中 = 漏怪。"""
        reco = context.run_recognition(
            "Farm@LeakDetect",
            img,
        )
        return bool(reco and reco.hit)

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

    # --- 倍速状态机（pipeline 节点识别速度按钮并点击） ---

    def _set_speed(self, context: Context, speed: int) -> None:
        """设游戏倍速（1 或 2）。幂等：已是目标值则不调。

         速度按钮的识别/点击由 pipeline 节点 Speed2x / Speed1x 完成
        （TemplateMatch 速度按钮图标 + Click，roi/template 在 execute.json 填）。
         context.run_task 运行该节点。点一次切换，靠 self._speed 记当前状态。
        """
        if speed == self._speed:
            return
        node = "Speed2x" if speed == 2 else "Speed1x"
        context.run_task(node)
        self._speed = speed
        logger.debug("倍速 → %dx", speed)

    def _step_to_frames(self, ctrl: Controller, ts: TimeSource, target_frame: int) -> None:
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
