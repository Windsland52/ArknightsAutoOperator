"""头像定位 + 运行时自学习。

使用 MAA 的 TemplateMatch（C++ OpenCV cv::matchTemplate）做模板匹配。
头像模板放在 resource/base/image/avatar/ 下。

- locate_avatar(context, image, oper_name) → 用 context.run_recognition 做 TemplateMatch
- learn_avatar(frame, oper_name) → 截取头像 → 存 resource/base/image/avatar/
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from custom import config

if TYPE_CHECKING:
    from maa.context import Context

logger = logging.getLogger(__name__)

# 待部署区 ROI（1280×720 坐标，bottom 20%）
_OPER_ROI = [
    0,
    int(config.OPERATOR_AREA_RATIO[1] * config.SCREEN_STANDARD[1]),
    config.SCREEN_STANDARD[0],
    int(
        (config.OPERATOR_AREA_RATIO[3] - config.OPERATOR_AREA_RATIO[1]) * config.SCREEN_STANDARD[1]
    ),
]


def _avatar_dir() -> Path:
    from custom.utils.runtime_paths import project_root

    d = project_root() / "resource" / "base" / "image" / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_char_id(oper_name: str) -> str:
    import json

    from custom.utils.runtime_paths import project_root

    mapping_path = project_root() / "data" / "operator_mapping.json"
    if not mapping_path.exists():
        return ""
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    return mapping.get(oper_name, "")


def locate_avatar(
    context: Context,
    image: np.ndarray,
    oper_name: str,
    threshold: float = 0.7,
) -> tuple[float, float] | None:
    """用 MAA TemplateMatch 在待部署区定位干员头像。

    Args:
        context: MAA Context（执行器内可用）。
        image: 当前截图（作为 run_recognition 的 image 参数）。
        oper_name: 干员名。
        threshold: 匹配阈值。

    Returns:
        (x_ratio, y_ratio) 全屏比例位置，或 None。
    """
    char_id = _get_char_id(oper_name)
    if not char_id:
        logger.warning("干员 %s 不在 operator_mapping", oper_name)
        return None

    template_path = f"avatar/{char_id}.png"
    node_name = f"LocateAvatar_{char_id}"

    reco_detail = context.run_recognition(
        node_name,
        image,
        pipeline_override={
            node_name: {
                "recognition": "TemplateMatch",
                "template": template_path,
                "threshold": threshold,
                "roi": _OPER_ROI,
                "method": 5,  # TM_CCOEFF_NORMED
            }
        },
    )

    if not reco_detail or not reco_detail.hit or reco_detail.box is None:
        logger.warning("未找到干员 %s 的头像", oper_name)
        return None

    box = reco_detail.box  # (x, y, w, h)
    h, w = image.shape[:2]
    cx = box[0] + box[2] // 2
    cy = box[1] + box[3] // 2
    logger.info("干员 %s 头像定位: (%d, %d)", oper_name, cx, cy)
    return (cx / w, cy / h)


def learn_avatar(frame: np.ndarray, oper_name: str) -> bool:
    """截取干员头像并存为 MAA 模板。

    用户手动指定头像中心位置（如点击待部署区干员后截图），
    或由 executor 定位后截取。

    Args:
        frame: 全屏截图 BGR。
        oper_name: 干员名。
    """
    char_id = _get_char_id(oper_name)
    if not char_id:
        logger.error("干员 %s 不在 operator_mapping", oper_name)
        return False

    # 用 LAST_OPER_RATIO 作为默认截取位置（待部署区最右）
    h, w = frame.shape[:2]
    cx = int(config.LAST_OPER_RATIO[0] * w)
    cy = int(config.LAST_OPER_RATIO[1] * h)
    half = 60

    x1 = max(0, cx - half)
    y1 = max(0, cy - half)
    x2 = min(w, cx + half)
    y2 = min(h, cy + half)
    avatar = frame[y1:y2, x1:x2]

    from PIL import Image

    out_path = _avatar_dir() / f"{char_id}.png"
    avatar_rgb = avatar[..., ::-1].copy()
    Image.fromarray(avatar_rgb).resize((120, 120), Image.Resampling.LANCZOS).save(out_path)
    logger.info("头像已缓存: %s → %s", oper_name, out_path)
    return True


def has_avatar(oper_name: str) -> bool:
    """检查干员是否有缓存头像。"""
    char_id = _get_char_id(oper_name)
    if not char_id:
        return False
    return (_avatar_dir() / f"{char_id}.png").exists()


def list_cached() -> list[str]:
    """列出已缓存的头像文件名。"""
    return sorted(p.name for p in _avatar_dir().glob("*.png"))
