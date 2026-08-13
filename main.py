"""astrbot_platform_icqq —— icqq 平台适配器插件入口。

本插件为 AstrBot 注册一个名为 `icqq` 的平台适配器。AstrBot 在插件加载阶段
导入本模块，`icqq_platform_adapter` 里的 `@register_platform_adapter` 装饰器
会把适配器注册进平台注册表。
"""
from __future__ import annotations

from astrbot.api import logger

from .icqq_platform_adapter import IcqqPlatformAdapter  # noqa: F401  触发注册

logger.info("icqq 平台适配器已注册（QQ 个人号）")
