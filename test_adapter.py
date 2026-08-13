"""icqq AstrBot 适配器功能测试。

在装有真实 astrbot SDK 与 icqq-py 的 Python 3.12+ 环境运行：
    python test_adapter.py

验证：
1. register_platform_adapter 是否正确注册了 icqq 适配器
2. icqq 消息对象 -> AstrBotMessage 转换
3. AstrBot MessageChain -> icqq 消息段转换（含引用/合并转发/文件拆分）
4. 事件 send() / send_by_session() 路由到正确的 icqq API（桩客户端）
5. 登录事件回调不抛异常
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent
PROJECT_DIR = ADAPTER_DIR.parent
ICQQ_PY_DIR = Path(r"D:\zhuomian\icqq")

sys.path.insert(0, str(PROJECT_DIR))
sys.path.insert(0, str(ICQQ_PY_DIR))

PASS = []


def check(name, cond, detail=""):
    PASS.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not cond else ""))


from astrbot.api.message_components import At, Face, Image, Node, Nodes, Plain, Reply
from astrbot.api.platform import MessageType, PlatformMetadata
from astrbot.api.event import MessageChain
from astrbot.core.platform.message_type import MessageType as CoreMT
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.platform.register import platform_cls_map, platform_registry


# ---------------------------------------------------------------------------
# 1. 注册
# ---------------------------------------------------------------------------
from astrbot_platform_icqq import icqq_platform_adapter  # noqa: F401  触发注册

check("adapter registered", "icqq" in platform_cls_map, str(list(platform_cls_map.keys())))
pm = next((p for p in platform_registry if p.name == "icqq"), None)
check("metadata present", pm is not None and pm.adapter_display_name, getattr(pm, "adapter_display_name", None))
check("default_config has sign_api_addr", pm is not None and "sign_api_addr" in (pm.default_config_tmpl or {}))


# ---------------------------------------------------------------------------
# 2. 伪造 icqq 消息对象（跳过 protobuf）
# ---------------------------------------------------------------------------

def fake_group_msg():
    return types.SimpleNamespace(
        message_type="group", self_id=123456, group_id=10001, user_id=8888,
        sender={"user_id": 8888, "nickname": "张三", "card": "阿三", "role": "member"},
        message_id="AAAA", time=1700000000,
        message=[
            {"type": "at", "qq": 123456},
            {"type": "text", "text": "你好，帮我看看"},
            {"type": "image", "url": "https://example.com/a.jpg"},
            {"type": "face", "id": 178},
        ],
        raw_message="你好，帮我看看 [图片]",
        source={"user_id": 6666, "time": 1699999999, "seq": 123, "rand": 456,
                "message": "被引用的消息"},
    )


def fake_private_msg():
    return types.SimpleNamespace(
        message_type="private", self_id=123456, user_id=9999,
        sender={"user_id": 9999, "nickname": "李四"},
        message_id="BBBB", time=1700000001,
        message=[{"type": "text", "text": "私聊测试"}],
        raw_message="私聊测试", source=None,
    )


# ---------------------------------------------------------------------------
# 3. 桩客户端
# ---------------------------------------------------------------------------

class StubClient:
    def __init__(self):
        self.calls = []
        self.online = True
        self.uin = 123456

    def isOnline(self):
        return self.online

    def pickGroup(self, gid):
        return StubGroup(self, gid)

    def pickFriend(self, uid):
        return StubFriend(self, uid)


class StubGroup:
    def __init__(self, c, gid):
        self.c, self.gid = c, gid

    async def sendMsg(self, content, source=None, anony=False):
        self.c.calls.append(("group.sendMsg", self.gid, content, source))

    async def sendFile(self, path, name=None, callback=None):
        self.c.calls.append(("group.sendFile", self.gid, path, name))

    async def makeForwardMsg(self, msglist, nt=False):
        return {"type": "json", "data": {"app": "com.tencent.multimsg", "meta": {"detail": {"uniseq": "123"}}}}


class StubFriend:
    def __init__(self, c, uid):
        self.c, self.uid = c, uid

    async def sendMsg(self, content, source=None):
        self.c.calls.append(("friend.sendMsg", self.uid, content, source))

    async def sendFile(self, path, filename=None, callback=None):
        self.c.calls.append(("friend.sendFile", self.uid, path, filename))

    async def makeForwardMsg(self, msglist, nt=False):
        return {"type": "json", "data": {"app": "com.tencent.multimsg", "meta": {"detail": {"uniseq": "123"}}}}


from astrbot_platform_icqq.icqq_platform_adapter import IcqqPlatformAdapter
from astrbot_platform_icqq.icqq_message_event import IcqqMessageEvent
from astrbot_platform_icqq.message_converter import astr_chain_to_icqq, icqq_message_to_astr


async def main():
    # 建一个真实适配器实例（不登录，挂桩客户端）
    q = asyncio.Queue()
    real = IcqqPlatformAdapter(
        {"id": "icqq", "uin": 0, "password": "", "platform": 2, "sign_api_addr": "",
         "data_dir": "data/icqq", "log_level": "info", "reconnect_interval": 5},
        {}, q,
    )
    stub = StubClient()
    real._client = stub

    # ---- 消息对象 -> AstrBotMessage ----
    abm = icqq_message_to_astr(real, fake_group_msg(), is_group=True)
    check("group abm type", abm.type == MessageType.GROUP_MESSAGE)
    check("group abm session", abm.session_id == "10001" and abm.group_id == "10001")
    check("group abm sender", abm.sender.user_id == "8888" and abm.sender.nickname == "阿三")
    check("group abm reply", isinstance(abm.message[0], Reply), str(abm.message[0]))
    check("group abm at", any(isinstance(c, At) and str(c.qq) == "123456" for c in abm.message))
    check("group abm image", any(isinstance(c, Image) for c in abm.message))
    check("group abm face", any(isinstance(c, Face) for c in abm.message))
    check("group abm message_str", "你好" in abm.message_str, abm.message_str)

    abm2 = icqq_message_to_astr(real, fake_private_msg(), is_group=False)
    check("private abm type", abm2.type == MessageType.FRIEND_MESSAGE)
    check("private abm session", abm2.session_id == "9999")

    # ---- AstrBot MessageChain -> icqq 段 ----
    chain = MessageChain()
    chain.chain = [
        At(qq="8888"),
        Plain(text="测试消息"),
        Reply(id=abm.message[0].id, sender_id=6666, message_str="引用内容"),
    ]
    segs, source, fwd = await astr_chain_to_icqq(chain, is_group=True)
    check("send chain has at", any(s.get("type") == "at" and s.get("qq") == 8888 for s in segs))
    check("send chain has text", any(s.get("type") == "text" and "测试消息" in s.get("text", "") for s in segs))
    check("reply source built", source is not None and source["seq"] == 123 and source["rand"] == 456, str(source))

    # ---- 合并转发 ----
    nodes = Nodes(nodes=[
        Node(uin=8888, name="张三", content=[Plain("第一条")]),
        Node(uin=9999, name="李四", content=[Plain("第二条")]),
    ])
    chain2 = MessageChain()
    chain2.chain = [nodes]
    segs2, source2, fwd2 = await astr_chain_to_icqq(chain2, is_group=True)
    check("forward nodes extracted", fwd2 is not None and len(fwd2) == 2, str(fwd2))
    check("forward fake user_id", fwd2[0]["user_id"] == 8888 and fwd2[1]["user_id"] == 9999)

    # ---- 事件 send() 路由到桩客户端 ----
    event = IcqqMessageEvent(
        message_str="测试", message_obj=abm, platform_meta=real.meta(),
        session_id="10001", adapter=real,
    )
    mc = MessageChain()
    mc.chain = [Plain("回复内容")]
    await event.send(mc)
    check("event send routes to group", stub.calls and stub.calls[0][0] == "group.sendMsg", str(stub.calls))
    check("event send target", stub.calls[0][1] == 10001)

    # ---- send_by_session ----
    stub.calls.clear()
    mc2 = MessageChain()
    mc2.chain = [Plain("会话消息")]
    session = MessageSesion("icqq", CoreMT.GROUP_MESSAGE, "10001")
    await real.send_by_session(session, mc2)
    check("send_by_session group", stub.calls and stub.calls[0][0] == "group.sendMsg" and stub.calls[0][1] == 10001, str(stub.calls))

    stub.calls.clear()
    session2 = MessageSesion("icqq", CoreMT.FRIEND_MESSAGE, "9999")
    await real.send_by_session(session2, mc2)
    check("send_by_session friend", stub.calls and stub.calls[0][0] == "friend.sendMsg" and stub.calls[0][1] == 9999, str(stub.calls))

    # ---- 离线时发送应报错 ----
    stub.online = False
    try:
        await real.send_by_session(session, mc2)
        check("offline send raises", False)
    except RuntimeError as e:
        check("offline send raises", "未在线" in str(e), str(e))
    stub.online = True

    # ---- 登录事件回调 ----
    try:
        real._on_online(stub)
        real._on_qrcode(stub, {"image": b"PNGDATA"})
        real._on_login_error(stub, {"code": 1, "message": "密码错误"})
        real._on_slider(stub, {"url": "https://ssl.captcha.qq.com/1"})
        real._on_device_verify(stub, {"url": "https://ssl.qq.com/1", "phone": "138****0000"})
        real._on_kickoff(stub, {"message": "账号在别处登录"})
        check("login callbacks no crash", True)
    except Exception as e:
        check("login callbacks no crash", False, str(e))
    if real._watchdog_task:
        real._watchdog_task.cancel()

    # ---- 设备锁验证状态机 ----
    check("device verify sets state", real._verify_state == "device" and real._verify_url == "https://ssl.qq.com/1" and real._verify_phone == "138****0000")
    real._on_online(stub)
    check("online clears verify state", real._verify_state is None)

    # ---- 设备验证自动重试（桩客户端记录 login 调用） ----
    class RetryStub(StubClient):
        def __init__(self):
            super().__init__()
            self.online = False
            self.login_calls = 0

        async def login(self, password=None):
            self.login_calls += 1
            # 第一次重试后模拟验证完成，上线
            if self.login_calls >= 1:
                self.online = True

    rs = RetryStub()
    real2 = IcqqPlatformAdapter(
        {"id": "icqq", "uin": 123456, "password": "mypassword", "platform": 2,
         "sign_api_addr": "", "data_dir": "data/icqq", "log_level": "error",
         "verify_retry_interval": 1}, {}, asyncio.Queue(),
    )
    real2._client = rs
    real2._verify_state = "device"
    wd = asyncio.create_task(real2._watchdog(rs))
    await asyncio.sleep(2.5)
    wd.cancel()
    check("device verify auto-retry login", rs.login_calls >= 1, f"calls={rs.login_calls}")
    check("device verify retry then online", rs.online is True)

    # ---- 二维码路径记录 ----
    check("qrcode path recorded", real._qr_path.endswith("qrcode.png"), real._qr_path)

    n_fail = PASS.count(False)
    print("\nRESULT:", "ALL PASS" if n_fail == 0 else f"{n_fail} FAILURES", f"({len(PASS)} checks)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
