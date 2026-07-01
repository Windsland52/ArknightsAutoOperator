"""BattleState 截图回归测试。

用实际游戏截图验证 detect_battle_state 在各场景下返回正确的状态。
截图放在 tests/replay/cases/battle_state_screenshots/ 下。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aao.core.timing.battle_state import BattleState, detect_battle_state

SCREENSHOTS_DIR = Path(__file__).parent / "replay" / "cases" / "battle_state_screenshots"


def _load_bgr(name: str) -> np.ndarray:
    """加载 PNG → BGR (H, W, 3) uint8。"""
    rgb = np.asarray(Image.open(SCREENSHOTS_DIR / f"{name}.png").convert("RGB"), dtype=np.uint8)
    return rgb[:, :, ::-1].copy()


# 场景 → 期望的 BattleState
EXPECTED_STATES = {
    "in_battle_running": BattleState.IN_BATTLE,
    "in_battle_paused": BattleState.IN_BATTLE,
    "title_screen": BattleState.BATTLE_BEGIN,
    "settlement": BattleState.NOT_IN_BATTLE,
    "main_menu": BattleState.NOT_IN_BATTLE,
    "deployment": BattleState.NOT_IN_BATTLE,
}


def _make_parametrize():
    """生成参数化测试列表（无截图时 skip）。"""
    cases = []
    for name, expected in EXPECTED_STATES.items():
        png = SCREENSHOTS_DIR / f"{name}.png"
        if png.exists():
            cases.append((name, expected))
    return cases


_CASES = _make_parametrize()


@pytest.mark.parametrize("name,expected", _CASES, ids=[c[0] for c in _CASES])
def test_battle_state_screenshot(name: str, expected: BattleState) -> None:
    """截图场景回归：验证 detect_battle_state 返回正确状态。"""
    frame = _load_bgr(name)
    actual = detect_battle_state(frame)
    assert actual is expected, f"{name}: expected {expected.value}, got {actual.value}"


def test_has_screenshots_or_skip() -> None:
    """无截图时不 fail，仅提示。"""
    if not _CASES:
        pytest.skip("无截图：请按 battle_state_screenshots/ 添加素材")
