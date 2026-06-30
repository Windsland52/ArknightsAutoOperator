from __future__ import annotations

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition


class AgentServer:
    @classmethod
    def register_custom_action(cls, name: str, action: CustomAction) -> bool: ...
    @classmethod
    def register_custom_recognition(cls, name: str, recognition: CustomRecognition) -> bool: ...
    @classmethod
    def start_up(cls, identifier: str) -> bool: ...
    @classmethod
    def join(cls) -> None: ...
