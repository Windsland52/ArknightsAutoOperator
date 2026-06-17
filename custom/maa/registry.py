"""Custom action/recognition 注册中心（装饰器收集 + 统一注册）。

用法：在 ``custom.maa.action`` / ``custom.maa.reco`` 子模块用
``@custom_action("name")`` / ``@custom_recognition("name")`` 装饰类，
``register_all(target)`` 自动扫描这两个包并注册到 ``target``
（Resource 或 AgentServer 均可，二者 register_custom_* 签名一致）。

加新 Custom：在对应包新建文件 + 装饰，无需改 agent.py / farm.py / run.py。
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

# [(kind, name, cls), ...]；kind: "action" | "recognition"
_REGISTRY: list[tuple[str, str, type]] = []

# 要扫描的包（action 放 CustomAction，reco 放 CustomRecognition）
_PACKAGES = ("custom.maa.action", "custom.maa.reco")
_SKIP = {"__init__"}


def custom_action(name: str) -> Callable[[type], type]:
    """装饰器：登记一个 Custom action（注册名 name）。"""

    def deco(cls: type) -> type:
        _REGISTRY.append(("action", name, cls))
        return cls

    return deco


def custom_recognition(name: str) -> Callable[[type], type]:
    """装饰器：登记一个 Custom recognition（注册名 name）。"""

    def deco(cls: type) -> type:
        _REGISTRY.append(("recognition", name, cls))
        return cls

    return deco


def collect(target: Any) -> None:
    """把 _REGISTRY 里的 Custom 实例化并注册到 target（Resource 或 AgentServer）。"""
    for kind, name, cls in _REGISTRY:
        if kind == "action":
            target.register_custom_action(name, cls())
        else:
            target.register_custom_recognition(name, cls())


def register_all(target: Any) -> None:
    """扫描 action/reco 包（触发装饰器填 _REGISTRY），统一注册到 target。"""
    for pkg_name in _PACKAGES:
        pkg = importlib.import_module(pkg_name)
        for m in pkgutil.iter_modules(pkg.__path__):
            if m.name in _SKIP:
                continue
            importlib.import_module(f"{pkg_name}.{m.name}")
    collect(target)
