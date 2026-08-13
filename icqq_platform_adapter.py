"""icqq 平台适配器（AstrBot Platform 实现）。

基于 icqq-py（icqq 协议库的纯 Python 移植）将 QQ 个人号接入 AstrBot。
支持群聊/私聊收发、图片/语音/视频/文件/引用回复/合并转发、密码与扫码登录。
"""
from __future__ import annotations

import asyncio
import os
import traceback
import uuid
from typing import Any, cast

from astrbot.api import logger
from astrbot.api.event import MessageChain
from astrbot.api.platform import (
    AstrBotMessage,
    MessageType,
    Platform,
    PlatformMetadata,
    register_platform_adapter,
)
from astrbot.core.platform.astr_message_event import MessageSesion

from icqq import Client
from icqq import Platform as QQPlatform

from .icqq_message_event import IcqqMessageEvent
from .message_converter import astr_chain_to_icqq, icqq_message_to_astr

CONFIG_METADATA = {
    "uin": {
        "description": "机器人 QQ 号",
        "type": "int",
        "hint": "留 0 则使用扫码登录。密码登录时必填。",
    },
    "password": {
        "description": "QQ 密码",
        "type": "string",
        "hint": "留空则扫码登录。可填明文密码或 32 位十六进制 MD5（推荐）。",
    },
    "platform": {
        "description": "登录协议（1 Android / 2 aPad / 3 Watch / 4 iMac / 5 iPad / 6 Tim）",
        "type": "int",
        "hint": "默认 2（aPad）。不同协议对应不同的设备信息，风控表现略有差异。",
    },
    "sign_api_addr": {
        "description": "签名服务器地址（必填）",
        "type": "string",
        "hint": "如 http://127.0.0.1:8080/sign?key=xxx（qsign）或不带 key 的 tx-sign 地址。未配置则登录/发消息大概率失败。",
    },
    "data_dir": {
        "description": "数据目录（设备信息/二维码/token 等）",
        "type": "string",
        "hint": "相对 AstrBot 工作目录。",
    },
    "log_level": {
        "description": "icqq 日志级别",
        "type": "string",
        "hint": "trace / debug / info / warn / error / mark。",
    },
    "ignore_self": {
        "description": "忽略机器人自己发出的消息",
        "type": "bool",
    },
    "reconnect_interval": {
        "description": "自动重连间隔（秒）",
        "type": "int",
    },
    "resend": {
        "description": "群消息被风控时分片重发",
        "type": "bool",
    },
    "cache_group_member": {
        "description": "缓存群成员信息",
        "type": "bool",
    },
}


@register_platform_adapter(
    "icqq",
    "基于 icqq 协议库的 QQ 个人号平台适配器",
    default_config_tmpl={
        "uin": 0,
        "password": "",
        "platform": 2,
        "sign_api_addr": "",
        "data_dir": "data/icqq",
        "log_level": "info",
        "ignore_self": True,
        "reconnect_interval": 5,
        "resend": True,
        "cache_group_member": True,
    },
    adapter_display_name="icqq（QQ 个人号）",
    support_streaming_message=False,
    config_metadata=CONFIG_METADATA,
)
class IcqqPlatformAdapter(Platform):
    def __init__(
        self,
        platform_config: dict,
        platform_settings: dict,
        event_queue: asyncio.Queue,
    ) -> None:
        super().__init__(platform_config, event_queue)
        self.settings = platform_settings
        self.metadata = PlatformMetadata(
            name="icqq",
            description="基于 icqq 协议库的 QQ 个人号平台适配器",
            id=cast(str, self.config.get("id", "icqq")),
            adapter_display_name="icqq（QQ 个人号）",
            support_streaming_message=False,
        )

        self._client: Client | None = None
        self._stop_event = asyncio.Event()
        self._connected = False
        self._qr_path: str = ""
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ meta

    def meta(self) -> PlatformMetadata:
        return self.metadata

    def get_client(self) -> Client | None:
        return self._client

    def _data_dir(self) -> str:
        raw = str(self.config.get("data_dir") or "data/icqq")
        return raw if os.path.isabs(raw) else os.path.join(os.getcwd(), raw)

    def _resolve_platform(self) -> QQPlatform:
        v = self.config.get("platform", 2)
        if isinstance(v, QQPlatform):
            return v
        try:
            return QQPlatform(int(v))
        except (TypeError, ValueError):
            pass
        try:
            return QQPlatform[str(v)]
        except (KeyError, TypeError):
            return QQPlatform.aPad

    # --------------------------------------------------------------- 生命周期

    async def run(self) -> None:
        """启动 icqq 客户端并保持运行（阻塞直到 terminate）。"""
        reconnect_interval = int(self.config.get("reconnect_interval", 5) or 5)
        while not self._stop_event.is_set():
            try:
                await self._login_and_idle()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.record_error(str(e), traceback.format_exc())
                logger.exception(f"[icqq] 适配器运行异常：{e}")
            if self._stop_event.is_set():
                break
            logger.info(f"[icqq] {reconnect_interval} 秒后尝试重新连接...")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=reconnect_interval)
            except asyncio.TimeoutError:
                pass

    async def _login_and_idle(self) -> None:
        client = await self._build_client()
        await self._login(client)
        # 登录完成后保持空闲（icqq 内部的心跳/监听/重连自行运转），直到 terminate
        await self._stop_event.wait()

    async def _build_client(self) -> Client:
        if self._client is not None:
            return self._client
        uin = int(self.config.get("uin") or 0)
        client = Client(uin if uin else None, {
            "platform": self._resolve_platform(),
            "sign_api_addr": str(self.config.get("sign_api_addr") or ""),
            "data_dir": self._data_dir(),
            "log_level": str(self.config.get("log_level") or "info"),
            "ignore_self": bool(self.config.get("ignore_self", True)),
            "resend": bool(self.config.get("resend", True)),
            "cache_group_member": bool(self.config.get("cache_group_member", True)),
            "reconn_interval": int(self.config.get("reconnect_interval", 5) or 5),
        })
        # 绑定消息与登录事件
        client.on("message.group", self._on_group_message)
        client.on("message.private", self._on_private_message)
        client.on("message.discuss", self._on_discuss_message)
        client.on("system.online", self._on_online)
        client.on("system.offline", self._on_offline)
        client.on("system.offline.kickoff", self._on_kickoff)
        client.on("system.login.qrcode", self._on_qrcode)
        client.on("system.login.slider", self._on_slider)
        client.on("system.login.device", self._on_device_verify)
        client.on("system.login.error", self._on_login_error)
        self._client = client
        return client

    async def _login(self, client: Client) -> None:
        """执行登录（密码 / 扫码 / token）。密码登录为异步返回，扫码登录自行轮询。"""
        if client.isOnline():
            return
        password = str(self.config.get("password") or "")
        try:
            await client.login(password if password else None)
        except Exception as e:
            # 密码错误等登录失败通过 system.login.error 事件暴露，这里兜底记录
            logger.error(f"[icqq] 登录调用异常：{e}")
            self.record_error(str(e), traceback.format_exc())

    async def terminate(self) -> None:
        self._stop_event.set()
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._client:
            try:
                self._client.terminate()
            except Exception as e:
                logger.warning(f"[icqq] 关闭客户端失败：{e}")

    # --------------------------------------------------------------- 事件处理

    def handle_msg(self, message: AstrBotMessage) -> None:
        self.commit_event(self.create_event(message))

    def create_event(self, message: AstrBotMessage) -> IcqqMessageEvent:
        return IcqqMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
            adapter=self,
        )

    def _on_group_message(self, client, event) -> None:
        try:
            abm = icqq_message_to_astr(self, event, is_group=True)
            if abm:
                self.handle_msg(abm)
        except Exception as e:
            logger.exception(f"[icqq] 处理群消息失败：{e}")

    def _on_private_message(self, client, event) -> None:
        try:
            abm = icqq_message_to_astr(self, event, is_group=False)
            if abm:
                self.handle_msg(abm)
        except Exception as e:
            logger.exception(f"[icqq] 处理私聊消息失败：{e}")

    def _on_discuss_message(self, client, event) -> None:
        logger.debug("[icqq] 暂不支持讨论组消息，已忽略")

    def _on_online(self, client, *args) -> None:
        self._connected = True
        nickname = getattr(client, "nickname", "") or ""
        logger.info(f"[icqq] 登录成功：{nickname} ({client.uin})")
        # 启动离线看门狗
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog(client))

    def _on_offline(self, client, *args) -> None:
        self._connected = False
        logger.warning("[icqq] 已离线")

    def _on_kickoff(self, client, data) -> None:
        msg = (data or {}).get("message", "")
        self.record_error(f"被踢下线：{msg}", None)
        logger.error(f"[icqq] 被踢下线：{msg}")

    def _on_qrcode(self, client, data) -> None:
        # data = {"image": <png bytes>}；icqq 已把二维码保存到数据目录
        self._qr_path = os.path.join(self._data_dir(), "qrcode.png")
        logger.info(f"[icqq] 请用手机 QQ 扫码登录，二维码已保存：{self._qr_path}")

    def _on_slider(self, client, data) -> None:
        url = (data or {}).get("url", "")
        logger.info(f"[icqq] 收到滑动验证码，请访问：{url}")
        self.record_error(f"需要滑动验证：{url}", None)

    def _on_device_verify(self, client, data) -> None:
        url = (data or {}).get("url", "")
        phone = (data or {}).get("phone", "")
        logger.info(f"[icqq] 登录保护验证 URL：{url} 密保手机：{phone}")

    def _on_login_error(self, client, data) -> None:
        data = data or {}
        code = data.get("code")
        message = data.get("message") or ""
        self.record_error(f"登录失败 [{code}] {message}", None)
        logger.error(f"[icqq] 登录失败 [{code}] {message}")

    async def _watchdog(self, client: Client) -> None:
        """离线超时自动重新登录（处理 token 过期、被踢后恢复等场景）。"""
        offline_since: float | None = None
        try:
            while not self._stop_event.is_set():
                await asyncio.sleep(30)
                if self._stop_event.is_set():
                    break
                if client.isOnline():
                    offline_since = None
                    continue
                if offline_since is None:
                    offline_since = asyncio.get_running_loop().time()
                    continue
                if asyncio.get_running_loop().time() - offline_since >= 60:
                    logger.warning("[icqq] 客户端离线超过 60 秒，尝试重新登录...")
                    offline_since = None
                    await self._login(client)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"[icqq] 看门狗异常：{e}")

    # --------------------------------------------------------------- 发送

    async def send_by_session(self, session: MessageSesion, message_chain: MessageChain) -> None:
        """通过可持久化的会话数据主动发消息（定时任务/主动推送）。"""
        is_group = session.message_type == MessageType.GROUP_MESSAGE
        session_id = self._extract_numeric_id(session.session_id)
        if not session_id:
            raise ValueError(f"[icqq] 无效的会话 ID：{session.session_id}")
        await self._dispatch_send(is_group, session_id, message_chain)
        await super().send_by_session(session, message_chain)

    @staticmethod
    def _extract_numeric_id(session_id: str) -> str:
        """从会话 ID 中提取数字目标（兼容 unique_session 等加了前缀/后缀的场景）。"""
        for part in reversed(str(session_id).split("_")):
            if part.isdigit():
                return part
        return str(session_id)

    async def _dispatch_send(
        self,
        is_group: bool,
        target_id: str,
        message_chain: MessageChain,
    ) -> None:
        client = self.get_client()
        if client is None:
            raise RuntimeError("icqq 客户端未初始化")
        if not client.isOnline():
            raise RuntimeError("icqq 客户端未在线")
        if not target_id or not str(target_id).isdigit():
            raise ValueError(f"[icqq] 无效的发送目标：{target_id}")
        tid = int(target_id)

        segs, source, forward_nodes = await astr_chain_to_icqq(message_chain, is_group)

        # 提取并拆分文件段（文件走 sendFile 单独发送）
        files = [s for s in segs if s.get("__astr_file__")]
        segs = [s for s in segs if not s.get("__astr_file__")]

        # 合并转发
        if forward_nodes:
            try:
                target = client.pickGroup(tid) if is_group else client.pickFriend(tid)
                forward = await target.makeForwardMsg(forward_nodes)
                if forward:
                    segs.append(forward)
            except Exception as e:
                logger.error(f"[icqq] 发送合并转发失败：{e}")

        # 普通消息
        if segs:
            if is_group:
                await client.pickGroup(tid).sendMsg(segs, source)
            else:
                await client.pickFriend(tid).sendMsg(segs, source)

        # 文件
        for f in files:
            path = f.get("path")
            name = f.get("name")
            if not path:
                continue
            try:
                if is_group:
                    await client.pickGroup(tid).sendFile(path, name=name)
                else:
                    await client.pickFriend(tid).sendFile(path, filename=name)
            except Exception as e:
                logger.error(f"[icqq] 发送文件失败：{e}")
