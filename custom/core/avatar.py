"""头像定位 + 运行时自学习。

MAA 方案：运行时从游戏截图截取头像，按职业分组缓存。
首次遇到未知干员 → 点击 → OCR 名字 → 截头像 → 存盘。
后续直接用缓存做 TemplateMatch。

简化版（不依赖 MAA context，纯 numpy + PIL）：
- locate_avatar(frame, oper_name) → 在待部署区匹配头像 → 返回 (x_ratio, y_ratio)
- learn_avatar(frame, avatar_rect, name) → 截取 + 存盘
- 自动按职业分组（从 operator_mapping 查 profession）
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from custom import config

logger = logging.getLogger(__name__)

# 待部署区比例（bottom 20%）
_OPER_AREA = (
    int(config.OPERATOR_AREA_RATIO[0] * config.SCREEN_STANDARD[0]),
    int(config.OPERATOR_AREA_RATIO[1] * config.SCREEN_STANDARD[1]),
    int(config.OPERATOR_AREA_RATIO[2] * config.SCREEN_STANDARD[0]),
    int(config.OPERATOR_AREA_RATIO[3] * config.SCREEN_STANDARD[1]),
)
_AVATAR_W, _AVATAR_H = 60, 60  # 裁剪后头像尺寸（中心区域）


def avatar_dir() -> Path:
    from custom.utils.runtime_paths import project_root

    d = project_root() / "resource" / "image" / "avatar"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_oper_info(name: str) -> tuple[str, str]:
    """从 operator_mapping 查 charId + profession。"""
    import json

    from custom.utils.runtime_paths import project_root

    mapping_path = project_root() / "data" / "operator_mapping.json"
    names_path = project_root() / "data" / "operator_names.json"
    if not mapping_path.exists():
        return "", ""
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    char_id = mapping.get(name, "")
    profession = ""
    if names_path.exists():
        all_names = json.loads(names_path.read_text(encoding="utf-8"))
        for item in all_names:
            if item.get("name") == name:
                profession = item.get("profession", "")
                break
    return char_id, profession


def _load_avatar_template(char_id: str) -> np.ndarray | None:
    """加载已缓存的头像模板（灰度 + 裁剪中心）。"""
    path = avatar_dir() / f"{char_id}.png"
    if not path.exists():
        return None
    from PIL import Image

    img = Image.open(path).convert("L")
    img = img.resize((120, 120), Image.Resampling.LANCZOS)
    arr = np.array(img, dtype=np.uint8)
    # 裁剪中心 60x60
    cy, cx = 60, 60
    return arr[cy - _AVATAR_H // 2 : cy + _AVATAR_H // 2, cx - _AVATAR_W // 2 : cx + _AVATAR_W // 2]


def _extract_oper_area(frame: np.ndarray) -> np.ndarray:
    """从全屏截图截取待部署区，转灰度。"""
    h, w = frame.shape[:2]
    x1 = int(_OPER_AREA[0] * w / config.SCREEN_STANDARD[0])
    y1 = int(_OPER_AREA[1] * h / config.SCREEN_STANDARD[1])
    x2 = int(_OPER_AREA[2] * w / config.SCREEN_STANDARD[0])
    y2 = int(_OPER_AREA[3] * h / config.SCREEN_STANDARD[1])
    area = frame[y1:y2, x1:x2]
    # 转灰度
    return np.dot(area[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8)


def locate_avatar(frame: np.ndarray, oper_name: str) -> tuple[float, float] | None:
    """在待部署区找到指定干员的头像位置。

    Args:
        frame: 全屏截图 (H, W, 3) BGR。
        oper_name: 干员名。

    Returns:
        (x_ratio, y_ratio) 相对全屏的位置，或 None 未找到。
    """
    char_id, _ = _get_oper_info(oper_name)
    if not char_id:
        logger.warning("干员 %s 不在 operator_mapping 中", oper_name)
        return None

    template = _load_avatar_template(char_id)
    if template is None:
        logger.warning("干员 %s (%s) 无缓存头像，需先 learn_avatar", oper_name, char_id)
        return None

    # 截取待部署区
    h, w = frame.shape[:2]
    x1 = int(_OPER_AREA[0] * w / config.SCREEN_STANDARD[0])
    y1 = int(_OPER_AREA[1] * h / config.SCREEN_STANDARD[1])
    x2 = int(_OPER_AREA[2] * w / config.SCREEN_STANDARD[0])
    y2 = int(_OPER_AREA[3] * h / config.SCREEN_STANDARD[1])
    area = frame[y1:y2, x1:x2]
    area_gray = np.dot(area[..., :3], [0.299, 0.587, 0.114]).astype(np.uint8)

    # 模板匹配（归一化互相关）
    result = _match_template(area_gray, template)
    if result is None:
        return None

    score, (mx, my) = result
    if score < 0.6:
        logger.warning("干员 %s 头像匹配分数过低: %.2f", oper_name, score)
        return None

    # 转全屏比例
    avatar_cx = x1 + mx + _AVATAR_W // 2
    avatar_cy = y1 + my + _AVATAR_H // 2
    return (avatar_cx / w, avatar_cy / h)


def _match_template(
    image: np.ndarray, template: np.ndarray
) -> tuple[float, tuple[int, int]] | None:
    """归一化互相关模板匹配（numpy 向量化，无需 cv2）。

    Returns:
        (best_score, (x, y)) 或 None。
    """
    ih, iw = image.shape
    th, tw = template.shape
    if th > ih or tw > iw:
        return None

    # 模板归一化
    t_mean = template.mean()
    t_centered = template.astype(np.float32) - t_mean
    t_norm = np.sqrt(np.sum(t_centered**2))
    if t_norm < 1e-6:
        return None

    best_score = -1.0
    best_pos = (0, 0)

    # 滑动窗口（步长 2 加速）
    step = 2
    for y in range(0, ih - th + 1, step):
        for x in range(0, iw - tw + 1, step):
            patch = image[y : y + th, x : x + tw].astype(np.float32)
            p_mean = patch.mean()
            p_centered = patch - p_mean
            p_norm = np.sqrt(np.sum(p_centered**2))
            if p_norm < 1e-6:
                continue
            score = float(np.sum(t_centered * p_centered) / (t_norm * p_norm))
            if score > best_score:
                best_score = score
                best_pos = (x, y)

    # 精细搜索（步长 1，在 best 附近 ±2px）
    bx, by = best_pos
    for y in range(max(0, by - 2), min(ih - th + 1, by + 3)):
        for x in range(max(0, bx - 2), min(iw - tw + 1, bx + 3)):
            patch = image[y : y + th, x : x + tw].astype(np.float32)
            p_mean = patch.mean()
            p_centered = patch - p_mean
            p_norm = np.sqrt(np.sum(p_centered**2))
            if p_norm < 1e-6:
                continue
            score = float(np.sum(t_centered * p_centered) / (t_norm * p_norm))
            if score > best_score:
                best_score = score
                best_pos = (x, y)

    return (best_score, best_pos)


def learn_avatar(frame: np.ndarray, oper_name: str) -> bool:
    """从截图中截取干员头像并缓存。

    用法：在战斗中，点击待部署区的干员 → 截图 → 调用此函数。
    或者：用 locate_avatar 找到位置后截取。

    Args:
        frame: 全屏截图 (H, W, 3) BGR。
        oper_name: 干员名。

    Returns:
        是否成功保存。
    """
    char_id, _ = _get_oper_info(oper_name)
    if not char_id:
        logger.error("干员 %s 不在 operator_mapping 中", oper_name)
        return False

    # 先定位
    pos = locate_avatar(frame, oper_name)
    if pos is None:
        logger.warning("无法定位 %s 的头像位置", oper_name)
        return False

    # 截取头像区域（120x120）
    h, w = frame.shape[:2]
    cx = int(pos[0] * w)
    cy = int(pos[1] * h)
    half = 60
    x1 = max(0, cx - half)
    y1 = max(0, cy - half)
    x2 = min(w, cx + half)
    y2 = min(h, cy + half)

    avatar = frame[y1:y2, x1:x2]

    # 保存
    from PIL import Image

    out_path = avatar_dir() / f"{char_id}.png"
    # BGR → RGB
    avatar_rgb = avatar[..., ::-1].copy()
    Image.fromarray(avatar_rgb).save(out_path)
    logger.info("头像已缓存: %s (%s) → %s", oper_name, char_id, out_path)
    return True


def list_cached_avatars() -> list[str]:
    """列出已缓存的头像 charId。"""
    return sorted(p.stem for p in avatar_dir().glob("*.png"))
