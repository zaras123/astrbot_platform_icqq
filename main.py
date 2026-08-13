"""astrbot_platform_icqq —— icqq 平台适配器插件入口。

本插件为 AstrBot 注册一个名为 `icqq` 的平台适配器。AstrBot 在插件加载阶段
导入本模块：
- `icqq_platform_adapter` 里的 `@register_platform_adapter` 装饰器把适配器注册进
  平台注册表（platform_cls_map）；
- 本模块同时定义一个 Star 子类（AstrBot 要求插件必须通过 Star 注册才能加载）。

启用方式：WebUI「消息平台」→ 添加平台 → 类型 `icqq`（不是在「插件」里启用）。
"""
from __future__ import annotations

from astrbot.api import logger
from astrbot.api.star import Context, Star

from .icqq_platform_adapter import IcqqPlatformAdapter  # noqa: F401  触发适配器注册

logger.info("icqq 平台适配器已注册（QQ 个人号）")


class IcqqAdapterPlugin(Star):
    """icqq 平台适配器的插件载体。

    平台逻辑（登录/收发/事件）都在 IcqqPlatformAdapter 里；这里只是一个
    空壳 Star，让 AstrBot 能正常加载并管理本插件。
    """

    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.config = config

    async def initialize(self) -> None:
        logger.info("[icqq] 插件已加载（平台适配器请在「消息平台」启用并配置）")
