"""时间轴 JSON 读写。"""

from __future__ import annotations

import json
from pathlib import Path

from aao.timeline.model import Timeline
from aao.utils.logger import logger


def save_timeline(timeline: Timeline, path: str | Path) -> None:
    """保存时间轴到 JSON 文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(timeline.to_dict(), indent=4, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("时间轴已保存: %s (%d 动作)", path, len(timeline.actions))


def load_timeline(path: str | Path) -> Timeline:
    """从 JSON 文件加载时间轴。"""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return Timeline.from_dict(data)


def timelines_dir() -> Path:
    """时间轴默认存储目录。"""
    from aao.utils.runtime_paths import project_root

    d = project_root() / "config" / "timelines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_timelines() -> list[str]:
    """列出所有时间轴文件名。"""
    return sorted(p.name for p in timelines_dir().glob("*.json"))
