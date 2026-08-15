"""icqq 平台事件（AstrMessageEvent 子类），实现 send()/send_streaming() 等。"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain


class IcqqMessageEvent(AstrMessageEvent):
    def __init__(self, message_str, message_obj, platform_meta, session_id, adapter) -> None:
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.adapter = adapter
        self._icqq_client = adapter.get_client()

    @property
    def is_group(self) -> bool:
        return bool(self.get_group_id())

    def _target_id(self) -> str:
        """发送目标：群号为群聊，QQ 号为私聊。"""
        if self.is_group:
            return self.get_group_id()
        return self.get_sender_id()

    async def send(self, message: MessageChain) -> None:
        """发送消息到 QQ（群/私聊）。"""
        if self._icqq_client is None:
            raise RuntimeError("icqq 客户端未初始化")
        await self.adapter._dispatch_send(
            is_group=self.is_group,
            target_id=self._target_id(),
            message_chain=message,
        )
        await super().send(message)

    async def send_streaming(self, generator: AsyncGenerator, use_fallback: bool = False):
        """icqq 不支持流式消息，统一走 fallback（按标点分片发送）。"""
        buffer = ""
        pattern = re.compile(r"[^。？！~…]+[。？！~…]+")
        async for chain in generator:
            if isinstance(chain, MessageChain):
                for comp in chain.chain:
                    if isinstance(comp, Plain):
                        buffer += comp.text
                        if any(p in buffer for p in "。？！~…"):
                            buffer = await self.process_buffer(buffer, pattern)
                    else:
                        await self.send(MessageChain(chain=[comp]))
                        await asyncio.sleep(1.5)  # 限速
        buffer = buffer.strip()
        if buffer:
            await self.send(MessageChain([Plain(buffer)]))
        return await super().send_streaming(generator, use_fallback)

    async def react(self, emoji: str) -> None:
        """icqq 暂无原生表情回应接口，回一条含该表情的消息。"""
        await self.send(MessageChain([Plain(emoji)]))
