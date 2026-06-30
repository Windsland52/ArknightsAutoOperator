"""Shared typing aliases for dynamic JSON and runtime state payloads."""

from __future__ import annotations

from typing import Any, TypedDict

type JsonObject = dict[str, Any]


class MeasureState(TypedDict, total=False):
    isRunning: bool
    currentFrame: int | None
    totalFramesInCycle: int
    totalElapsedFrames: int
    activeProfile: str
