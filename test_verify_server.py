# -*- coding: utf-8 -*-
"""验证中转网页（icqq/verify_server.py）测试。

运行：python verify/test_verify_server.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aiohttp import ClientSession

from verify_server import VerifyServer

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


async def main():
    tf = os.path.join(tempfile.mkdtemp(), "ticket.txt")
    srv = VerifyServer(ticket_file=tf, port=18767)
    await srv.start()
    try:
        await srv.show(
            url="https://accounts.qq.com/safe/verify?uin=1&sig=ABC",
            phone="151****7", qr_bytes=b"\x89PNGfake", state="设备锁验证", cls="device",
        )
        async with ClientSession() as cs:
            # 页面
            html = await (await cs.get("http://127.0.0.1:18767/")).text()
            check("页面渲染", "verify-server" in html.lower() or "icqq" in html.lower() or "ticket" in html.lower(), f"len={len(html)}")
            check("页面有自动捕获+提交", "startCaptcha" in html and "manualSubmit" in html and "api/ticket" in html)
            # 状态 API
            st = await (await cs.get("http://127.0.0.1:18767/api/status")).json()
            check("status 返回状态", st["state"] == "设备锁验证")
            check("status 返回 URL", "sig=ABC" in st["url"])
            check("status 返回二维码", len(st["qr"]) > 0)
            check("status 返回密保", st["phone"] == "151****7")
            # 提交 ticket
            r = await (await cs.post("http://127.0.0.1:18767/api/ticket", json={"ticket": "TICKET_X"})).json()
            check("提交 ticket", r.get("ok") is True, str(r))
            check("ticket 写入文件", open(tf).read().strip() == "TICKET_X")
            # 空 ticket 拒绝
            r2 = await (await cs.post("http://127.0.0.1:18767/api/ticket", json={"ticket": "  "})).json()
            check("空 ticket 拒绝", r2.get("ok") is False)
    finally:
        await srv.stop()

    print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
