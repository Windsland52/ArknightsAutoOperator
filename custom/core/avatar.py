"""头像定位 + 运行时自学习（MAA 方案）。

流程（移植自 MAA BattlefieldMatcher::deployment_analyze）：
1. detect_slots: 用 BattleOpersFlag 模板在待部署区找到所有干员槽位
2. locate_avatar: 从每个槽位提取头像，和缓存做 TemplateMatch
3. learn_avatar: 点击未知干员 → OCR 名字 → 截取头像存盘

模板来源：MaaAssistantArknights/resource/template/Battle/BattleFlag/BattleOpersFlag.png
偏移量来源：tasks.json BattleOperAvatar.rectMove = [7, 32, 60, 60]
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from maa.context import Context

logger = logging.getLogger(__name__)

# MAA 常量（1280×720 坐标）
_FLAG_ROI = [33, 600, 1245, 18]  # BattleOpersFlag roi
_FLAG_THRESHOLD = 0.65
_AVATAR_OFFSET = [7, 32, 60, 60]  # BattleOperAvatar rectMove（相对 flag）


def _avatar_dir() -> Path:
    from custom.utils.runtime_paths import project_root

    d = project_root() / "resource" / "base" / "image" / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_char_id(oper_name: str) -> str:
    from custom.utils.runtime_paths import project_root

    mapping_path = project_root() / "data" / "operator_mapping.json"
    if not mapping_path.exists():
        return ""
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    return mapping.get(oper_name, "")


def detect_slots(
    context: Context,
    image: np.ndarray,
    threshold: float = _FLAG_THRESHOLD,
) -> list[dict]:
    """用 MAA TemplateMatch 检测待部署区所有干员槽位。

    Returns:
        [{"rect": [x, y, w, h], "avatar_rect": [ax, ay, aw, ah]}, ...]
    """
    reco_detail = context.run_recognition(
        "DetectSlots",
        image,
        pipeline_override={
            "DetectSlots": {
                "recognition": "TemplateMatch",
                "template": "BattleOpersFlag.png",
                "threshold": threshold,
                "roi": _FLAG_ROI,
                "method": 5,  # TM_CCOEFF_NORMED
                "green_mask": True,  # 模板黑色区域已转绿，green_mask 忽略
                "order_by": "Horizontal",
            }
        },
    )

    if not reco_detail or not reco_detail.hit:
        logger.debug("未检测到干员槽位")
        return []

    slots = []
    for result in reco_detail.all_results:
        box = getattr(result, "box", None)
        if box is None:
            continue
        fx, fy, fw, fh = box
        # 头像区域 = flag 位置 + avatar offset
        ax = int(fx + _AVATAR_OFFSET[0])
        ay = int(fy + _AVATAR_OFFSET[1])
        aw = _AVATAR_OFFSET[2]
        ah = _AVATAR_OFFSET[3]
        slots.append(
            {
                "flag_rect": (int(fx), int(fy), int(fw), int(fh)),
                "avatar_rect": (ax, ay, aw, ah),
                "click_rect": (
                    int(fx - 45),
                    int(fy + 6),
                    75,
                    120,
                ),  # BattleOperClickRange
            }
        )

    logger.info("检测到 %d 个干员槽位", len(slots))
    return slots


def locate_avatar(
    context: Context,
    image: np.ndarray,
    oper_name: str,
    threshold: float = 0.7,
) -> tuple[float, float] | None:
    """在待部署区定位指定干员。

    1. detect_slots 找所有槽位
    2. 用缓存的干员头像在每个槽位做 TemplateMatch
    """
    char_id = _get_char_id(oper_name)
    if not char_id:
        logger.warning("干员 %s 不在 operator_mapping", oper_name)
        return None

    slots = detect_slots(context, image)
    if not slots:
        return None

    # 在每个槽位的头像区域做 TemplateMatch
    for i, slot in enumerate(slots):
        ax, ay, aw, ah = slot["avatar_rect"]
        # 扩大 ROI 略大于 avatar，给匹配留余量
        roi = [max(0, ax - 5), max(0, ay - 5), aw + 10, ah + 10]

        reco_detail = context.run_recognition(
            f"MatchAvatar_{char_id}_slot{i}",
            image,
            pipeline_override={
                f"MatchAvatar_{char_id}_slot{i}": {
                    "recognition": "TemplateMatch",
                    "template": f"avatar/{char_id}.png",
                    "threshold": threshold,
                    "roi": roi,
                    "method": 5,
                }
            },
        )

        if reco_detail and reco_detail.hit:
            # 返回干员的点击位置（相对全屏比例）
            cx, cy = ax + aw // 2, ay + ah // 2
            h, w = image.shape[:2]
            logger.info("干员 %s 在槽位 %d: (%d, %d)", oper_name, i, cx, cy)
            return (cx / w, cy / h)

    logger.warning("干员 %s 在 %d 个槽位中均未匹配", oper_name, len(slots))
    return None


def has_avatar(oper_name: str) -> bool:
    """检查干员是否有缓存头像。"""
    char_id = _get_char_id(oper_name)
    if not char_id:
        return False
    return (_avatar_dir() / f"{char_id}.png").exists()


def learn_avatar_from_slot(
    image: np.ndarray,
    slot: dict,
    oper_name: str,
) -> bool:
    """从指定槽位截取头像并存盘。

    Args:
        image: 全屏截图。
        slot: detect_slots 返回的槽位 dict。
        oper_name: 干员名。
    """
    char_id = _get_char_id(oper_name)
    if not char_id:
        logger.error("干员 %s 不在 operator_mapping", oper_name)
        return False

    ax, ay, aw, ah = slot["avatar_rect"]
    h, w = image.shape[:2]
    # 边界检查
    x1, y1 = max(0, ax), max(0, ay)
    x2, y2 = min(w, ax + aw), min(h, ay + ah)

    avatar = image[y1:y2, x1:x2]
    if avatar.size == 0:
        logger.error("头像区域为空")
        return False

    from PIL import Image

    out_path = _avatar_dir() / f"{char_id}.png"
    avatar_rgb = avatar[..., ::-1].copy()  # BGR → RGB
    Image.fromarray(avatar_rgb).save(out_path)
    logger.info("头像已缓存: %s → %s", oper_name, out_path)
    return True


def list_cached() -> list[str]:
    """列出已缓存的头像文件名。"""
    return sorted(p.name for p in _avatar_dir().glob("*.png"))
