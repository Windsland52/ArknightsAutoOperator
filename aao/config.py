"""Project-wide constants: UI ratios, thresholds, timing, keybindings.

Ported from:
- reference/prts-plus/config.py (GameRatioConfig, ImageProcessingConfig, PerformActionConfig)
- reference/ArknightsCostBarRuler-master/ruler/utils.py (cost-bar ROI / thresholds)
- reference/arknights-frame-assistant-main (PC frame-step timing + native keys)
"""

from __future__ import annotations

# --- Game UI ratios (normalized 0-1, 720p-reference) ---
COST_AREA_RATIO = (0.906, 0.685, 1.0, 0.755)  # (left, top, right, bottom)
COST_NUMBER_AREA_RATIO = (0.33, 0.0, 1.0, 0.9)
OPERATOR_AREA_RATIO = (0.0, 0.8, 1.0, 1.0)
LAST_OPER_RATIO = (0.95, 0.9)
SKILL_RATIO = (0.6412, 0.5857)
RETREAT_RATIO = (0.4569, 0.3352)
PAUSE_BUTTON_RATIO = (0.94, 0.07)  # AFA: 0.9442, 0.0666 — consistent
START_BUTTON_RATIO = (0.87, 0.74)
DIRECTION_RATIO = 0.2
DEPLOY_DELTA_RATIO = 0.02
OPERATOR_SELECTED_RATIO = 0.9

SCREEN_STANDARD = (1280, 720)

# --- Cost-bar ROI (reference 1920x1080) ---
REF_WIDTH = 1920.0
REF_HEIGHT = 1080.0
# 实测当前游戏版（PC 客户端 1280x720）：费用条 x1172-1279（原 CostBarRuler 1739 偏左 13px）。
X1_OFFSET_FROM_RIGHT = REF_WIDTH - 1758  # 162  (-> x1172 @720)
X2_OFFSET_FROM_RIGHT = REF_WIDTH - 1919  # 1    (-> x1279 @720)
# 注：当前游戏版（PC 客户端）费用条比 CostBarRuler/prts-plus 参考低 ~18px，
# 实测在 1280x720 下位于 y559-561（mid 560）；原参考 810/817 → ROI y542 偏高。
Y1_OFFSET_FROM_BOTTOM = REF_HEIGHT - 838  # 242  (-> y559 @720 / y838 @1080)
Y2_OFFSET_FROM_BOTTOM = REF_HEIGHT - 841  # 239  (-> y561 @720 / y841 @1080)

# --- Cost-bar detection thresholds ---
WHITE_THRESHOLD = 250
MASKED_WHITE_THRESHOLD = 150
MASKED_MAX_BRIGHTNESS = 165
GRAY_TOLERANCE = 20
PIXEL_TOLERANCE = 5  # frame-map nearest-match tolerance

# --- Timing (frames / ms) ---
FRAMES_PER_SECOND = 30
TICK_MAX_DEFAULT = 30  # 1s = 30 ticks

BULLET_THRESHOLD = 2  # frames before target → enter bullet time
SPEED_UP_THRESHOLD = 90  # 距目标超此帧数 → 开 2 倍速（远了加速省时）
FRAME_THRESHOLD = 2  # frames before target → frame-by-frame
MINIMUM_WAIT_MS = 20
MOUSE_WAIT_MS = 100
GENERAL_WAIT_MS = 300

# --- Game native keybindings (PC client, Arknights.exe) ---
VK_SPACE = 0x20  # pause
VK_ESCAPE = 0x1B  # menu / resume
VK_D = 0x44  # toggle speed
VK_E = 0x45  # skill
VK_Q = 0x51  # retreat
VK_V = 0x56  # abandon operation

PC_PROCESS_NAME = "Arknights.exe"

# --- Calibration clustering ---
DEFAULT_NUM_CYCLES = 6
SIMILARITY_THRESHOLD = 0.8
CYCLE_HIGH_THRESHOLD = 0.9
CYCLE_LOW_THRESHOLD = 0.1
OUTLIER_MULTIPLIER = 5.0

# --- Default resources ---
DEFAULT_CALIBRATION = "test_30f_1280x720.json"  # 默认校准文件名（config/calibration/ 下）
