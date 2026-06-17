"""关卡识别 Custom recognition：读 timeline_path → map_code → OCR 手识关卡名。

pipeline 节点用 recognition: Custom + action: Click，MAA 拿到本识别返回的 box 自动点击。
timeline_path 默认是 config/timelines/ 下的文件名（不带前缀）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from maa.context import Context
from maa.custom_recognition import CustomRecognition

from custom.maa.registry import custom_recognition
from custom.utils.runtime_paths import project_root

logger = logging.getLogger(__name__)

_TIMELINE_DIR = project_root() / "config" / "timelines"


def _resolve_timeline_path(timeline_path: str) -> Path:
    """timeline_path 纯文件名 → config/timelines/ 下；带路径 → 相对项目根。"""
    p = Path(timeline_path)
    if p.is_absolute():
        return p
    if "/" in timeline_path or "\\" in timeline_path:
        return project_root() / timeline_path
    return _TIMELINE_DIR / timeline_path


def _load_timeline_data(timeline_path: str | None) -> dict | None:
    """加载 timeline JSON。"""
    if not timeline_path:
        logger.error("timeline_path 为空")
        return None
    p = _resolve_timeline_path(timeline_path)
    if not p.exists():
        logger.error("时间轴文件不存在: %s", p)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("时间轴文件解析失败: %s", p)
        return None


def read_map_code(timeline_path: str | None) -> str | None:
    """从 timeline 文件读 map_code（用于加载地图数据）。"""
    data = _load_timeline_data(timeline_path)
    if not data:
        return None
    mc = data.get("map_code")
    if not mc:
        logger.error("时间轴文件无 map_code")
    return mc


def read_stage_text(timeline_path: str | None) -> str | None:
    """关卡列表 OCR 文本：优先 stage_text，缺省 fallback 到 map_code。"""
    data = _load_timeline_data(timeline_path)
    if not data:
        return None
    return data.get("stage_text") or data.get("map_code")


@custom_recognition("ClickStage")
class ClickStageRecognition(CustomRecognition):
    """OCR 手识 timeline 指定 map_code 的关卡，返回命中 box。"""

    def analyze(
        self, context: Context, argv: CustomRecognition.AnalyzeArg
    ) -> CustomRecognition.AnalyzeResult | None:
        raw = argv.custom_recognition_param or "{}"
        try:
            params = json.loads(raw)
            if isinstance(params, str):
                params = json.loads(params)
        except ValueError:
            logger.exception("ClickStage 参数解析失败")
            return None

        timeline_path = params.get("timeline_path")
        stage_text = read_stage_text(timeline_path)
        if not stage_text:
            return None

        reco = context.run_recognition(
            "ClickStageMatch",
            argv.image,
            pipeline_override={
                "ClickStageMatch": {
                    "recognition": "OCR",
                    "expected": [stage_text],
                }
            },
        )
        if not reco or not reco.hit or not reco.all_results:
            logger.warning("未在关卡列表找到 %s", stage_text)
            return None

        box = getattr(reco, "box", None)
        if not box:
            return None
        logger.info("识别到关卡 %s at %s", stage_text, box)
        return box
