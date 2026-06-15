"""头像定位 + 运行时自学习（MAA 方案）。

完整流程（移植自 MAA BattlefieldMatcher + BattleHelper::update_deployment_）：
1. detect_slots: BattleOpersFlag 模板匹配 → 所有干员槽位
2. locate_oper: 遍历槽位 → 有缓存则 TemplateMatch → 无缓存则点击+OCR+存头像
3. OCR ROI 来自 MAA BattleOperName task: [5, 177, 191, 37]
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from maa.context import Context
    from maa.controller import Controller

logger = logging.getLogger(__name__)

# MAA 常量（1280×720）
_FLAG_ROI = [33, 600, 1245, 18]
_FLAG_THRESHOLD = 0.65
_AVATAR_OFFSET = [7, 32, 60, 60]  # BattleOperAvatar rectMove
_NAME_ROI = [5, 177, 191, 37]  # BattleOperName OCR roi
_DETAIL_WAIT = 0.5  # 等详情页打开


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
    """用 TemplateMatch 检测待部署区所有干员槽位。"""
    reco_detail = context.run_recognition(
        "DetectSlots",
        image,
        pipeline_override={
            "DetectSlots": {
                "recognition": "TemplateMatch",
                "template": "BattleOpersFlag.png",
                "threshold": threshold,
                "roi": _FLAG_ROI,
                "method": 5,
                "green_mask": True,
                "order_by": "Horizontal",
            }
        },
    )

    if not reco_detail or not reco_detail.hit:
        return []

    slots = []
    for result in reco_detail.all_results:
        box = getattr(result, "box", None)
        if box is None:
            continue
        fx, fy, fw, fh = box
        ax = int(fx + _AVATAR_OFFSET[0])
        ay = int(fy + _AVATAR_OFFSET[1])
        aw = _AVATAR_OFFSET[2]
        ah = _AVATAR_OFFSET[3]
        click_x = int(fx - 45 + 75 // 2)
        click_y = int(fy + 6 + 120 // 2)
        slots.append(
            {
                "flag_rect": (int(fx), int(fy), int(fw), int(fh)),
                "avatar_rect": (ax, ay, aw, ah),
                "click_pos": (click_x, click_y),
            }
        )

    logger.info("检测到 %d 个干员槽位", len(slots))
    return slots


def has_avatar(oper_name: str) -> bool:
    char_id = _get_char_id(oper_name)
    if not char_id:
        return False
    return (_avatar_dir() / f"{char_id}.png").exists()


def locate_oper(
    context: Context,
    ctrl: Controller,
    oper_name: str,
) -> tuple[float, float] | None:
    """定位指定干员在待部署区的位置。

    MAA 方案：
    1. 检测所有槽位
    2. 有缓存 → TemplateMatch 匹配
    3. 无缓存/匹配失败 → 逐个点击槽位 → OCR 干员名 → 截取头像存盘
    """
    image = ctrl.post_screencap().wait().get()
    slots = detect_slots(context, image)
    if not slots:
        logger.error("未检测到干员槽位")
        return None

    char_id = _get_char_id(oper_name)
    h, w = image.shape[:2]

    # Step 1: 有缓存 → 在每个槽位做 TemplateMatch
    if char_id and (_avatar_dir() / f"{char_id}.png").exists():
        for i, slot in enumerate(slots):
            ax, ay, aw, ah = slot["avatar_rect"]
            roi = [max(0, ax - 5), max(0, ay - 5), aw + 10, ah + 10]

            reco = context.run_recognition(
                f"MatchAvatar_{char_id}_{i}",
                image,
                pipeline_override={
                    f"MatchAvatar_{char_id}_{i}": {
                        "recognition": "TemplateMatch",
                        "template": f"avatar/{char_id}.png",
                        "threshold": 0.7,
                        "roi": roi,
                        "method": 5,
                    }
                },
            )

            if reco and reco.hit:
                cx = ax + aw // 2
                cy = ay + ah // 2
                logger.info("干员 %s 在槽位 %d", oper_name, i)
                return (cx / w, cy / h)

        logger.info("干员 %s 有缓存但未匹配，转入 OCR 学习", oper_name)

    # Step 2: 无缓存或匹配失败 → 点击每个未识别槽位 → OCR → 存头像
    for i, slot in enumerate(slots):
        click_x, click_y = slot["click_pos"]

        # 点击打开详情页
        logger.debug("点击槽位 %d (%d, %d)", i, click_x, click_y)
        ctrl.post_click(click_x, click_y).wait()
        time.sleep(_DETAIL_WAIT)

        # 截图详情页
        detail_img = ctrl.post_screencap().wait().get()

        # OCR 干员名
        name = _ocr_oper_name(context, detail_img)
        logger.info("槽位 %d OCR: %s", i, name or "(空)")

        # 关闭详情页（再点一次）
        ctrl.post_click(click_x, click_y).wait()
        time.sleep(0.3)

        if name:
            # 存头像（从原始 deployment 截图截取，不是详情页）
            _save_avatar_from_image(image, slot, name)

            if name == oper_name:
                cx = click_x
                cy = click_y
                logger.info("找到目标干员 %s 在槽位 %d", oper_name, i)
                return (cx / w, cy / h)

    logger.error("未找到干员 %s", oper_name)
    return None


def _ocr_oper_name(context: Context, detail_img: np.ndarray) -> str | None:
    """OCR 读取详情页干员名。"""
    reco = context.run_recognition(
        "OcrOperName",
        detail_img,
        pipeline_override={
            "OcrOperName": {
                "recognition": "OCR",
                "roi": _NAME_ROI,
                "threshold": 0.3,
                "order_by": "Horizontal",
            }
        },
    )

    if not reco or not reco.hit:
        return None

    # 取最高分结果
    detail = getattr(reco, "raw_detail", None)

    # OCR 结果的文字在 raw_detail 里
    if detail and isinstance(detail, dict):
        text = detail.get("text", "")
        if text:
            return text.strip()

    # 尝试从 all_results 获取
    for result in reco.all_results if hasattr(reco, "all_results") else []:
        detail_r = getattr(result, "detail", None) or getattr(result, "raw_detail", None)
        if detail_r and isinstance(detail_r, dict):
            text = detail_r.get("text", "")
            if text:
                return text.strip()

    return None


def _save_avatar_from_image(
    image: np.ndarray,
    slot: dict,
    oper_name: str,
) -> bool:
    """从截图截取槽位头像并存盘。"""
    char_id = _get_char_id(oper_name)
    if not char_id:
        return False

    ax, ay, aw, ah = slot["avatar_rect"]
    h, w = image.shape[:2]
    x1, y1 = max(0, ax), max(0, ay)
    x2, y2 = min(w, ax + aw), min(h, ay + ah)

    avatar = image[y1:y2, x1:x2]
    if avatar.size == 0:
        return False

    from PIL import Image

    out_path = _avatar_dir() / f"{char_id}.png"
    avatar_rgb = avatar[..., ::-1].copy()
    Image.fromarray(avatar_rgb).save(out_path)
    logger.info("头像已缓存: %s → %s", oper_name, out_path)
    return True


def list_cached() -> list[str]:
    return sorted(p.name for p in _avatar_dir().glob("*.png"))
