"""录制回放回归测试。

对 ``cases/`` 下每个用例：逐帧喂 TimeSource，核对全局帧 / 周期内帧
与 expected.json 一致。无素材时不报错（skip），仅提示。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from replay import diff_frames, list_cases, load_case, run_replay

CASES = list_cases()


def _run_and_check(case_dir: Path) -> None:
    frames, calibration, exp_total, exp_per_cycle = load_case(case_dir)

    actual_total, actual_per_cycle = run_replay(frames, calibration)

    total_diffs = diff_frames(actual_total, exp_total)
    assert not total_diffs, f"{case_dir.name} 全局帧不符（前 10）: {total_diffs[:10]}"

    if exp_per_cycle:
        per_diffs = diff_frames(actual_per_cycle, exp_per_cycle)
        assert not per_diffs, f"{case_dir.name} 周期内帧不符（前 10）: {per_diffs[:10]}"


@pytest.mark.parametrize("case_dir", CASES, ids=[p.name for p in CASES])
def test_replay(case_dir: Path) -> None:
    _run_and_check(case_dir)


def test_has_cases_or_skip() -> None:
    """无回放素材时不 fail，仅提示用户补素材。"""
    if not CASES:
        pytest.skip("无回放用例：请按 tests/replay/cases/README.md 添加素材")
