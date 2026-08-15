"""验证中转网页：无浏览器环境下，用其他设备完成 QQ 登录验证。

适用场景：服务器是 Linux 无浏览器/无头，但登录触发设备锁或滑块验证。
此模块起一个局域网可访问的 HTTP 服务，页面显示验证 URL / 二维码，用户用
手机或其它设备打开页面完成验证，把 ticket 填入表单提交，服务端写入
ticket 文件，配合 icqq 的 submitSlider 自动续登。

用法（在 system.login.device / system.login.slider 事件里）：

    from .verify_server import VerifyServer

    srv = VerifyServer(port=8765, ticket_file="/path/ticket.txt")
    await srv.start()                     # 监听 0.0.0.0:8765
    await srv.show(url, phone=None, qr_bytes=None, state="device")
    # 用户访问 http://<服务器IP>:8765/ 完成验证并提交 ticket

    # 关闭：
    await srv.stop()

页面功能：
- 自动轮询 /api/status 显示当前验证状态、URL、二维码
- ticket 输入框提交到 /api/ticket（写入 ticket_file）
- 手机端友好（viewport + 大按钮）
"""
from __future__ import annotations

import base64
import os
import socket
from typing import Optional

from aiohttp import web


def _find_free_port(preferred: int = 8765) -> int:
    """找空闲端口：先试 preferred，再试附近端口，最后任意空闲端口。"""
    for port in (preferred, preferred + 1, preferred + 2, preferred + 3, 18765, 18766):
        with socket.socket() as s:
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#eef2f6">
<title>QQ 登录验证</title>
<style>
  :root{
    --bg:#eef2f6; --card:#ffffff; --line:#e4e9f0; --line-2:#d3dce6;
    --text:#1c2330; --text-2:#4d5868; --text-3:#8b95a5;
    --brand:#12b7f5; --brand-deep:#0ba2db; --brand-ink:#0e6f96;
    --ok:#1a9e57; --ok-bg:#e8f7ee;
    --warn:#d97706; --warn-bg:#fdf3e3;
    --danger:#d54542; --danger-bg:#fdeeee;
    --r-lg:16px; --r-md:11px; --r-sm:9px;
    --sh:0 1px 2px rgba(22,36,55,.05),0 8px 24px rgba(22,36,55,.06);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{height:100%}
  body{
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text); min-height:100%;
    -webkit-font-smoothing:antialiased;
    padding:clamp(20px,4vw,40px) 16px 64px;
  }
  .wrap{max-width:440px;margin:0 auto}
  .hidden{display:none!important}

  /* ---------- 顶栏 ---------- */
  .head{display:flex;align-items:center;gap:12px;margin-bottom:20px}
  .mark{width:42px;height:42px;border-radius:11px;background:var(--brand);color:#fff;flex-shrink:0;
    display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;
    box-shadow:0 6px 14px rgba(18,183,245,.28)}
  .title{font-size:18px;font-weight:700;line-height:1.35}
  .subtitle{font-size:12.5px;color:var(--text-3);margin-top:2px}

  /* ---------- 卡片 ---------- */
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--r-lg);
    box-shadow:var(--sh);padding:18px;margin-bottom:13px}
  .card h3{font-size:14px;font-weight:700;color:var(--text);margin-bottom:12px}

  /* ---------- 状态行 ---------- */
  .status{display:flex;align-items:center;gap:10px;padding:14px 16px}
  .dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;background:var(--text-3)}
  .st-txt{font-size:14px;font-weight:600}
  .phone{margin-left:auto;font-size:12px;color:var(--text-3);white-space:nowrap}
  .st-device .dot{background:var(--warn);animation:pulse 1.6s ease-in-out infinite}
  .st-slider .dot{background:var(--brand);animation:pulse 1.6s ease-in-out infinite}
  .st-ok .dot{background:var(--ok)}
  .st-err .dot{background:var(--danger)}
  @keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(217,119,6,.18)}50%{opacity:.55;box-shadow:0 0 0 5px rgba(217,119,6,.10)}}

  /* ---------- 验证地址 ---------- */
  .lbl{font-size:12px;color:var(--text-3);margin-bottom:8px}
  .urlrow{display:flex;gap:8px}
  .urlbox{flex:1;min-width:0;border:1px solid var(--line);border-radius:var(--r-sm);background:#f6f8fb;
    padding:10px 12px;display:flex;align-items:center}
  a.url{color:var(--brand-ink);font-size:13px;line-height:1.55;word-break:break-all;text-decoration:none}
  a.url:active{opacity:.7}
  .copyBtn{flex:0 0 auto;width:56px;border:1px solid var(--line);border-radius:var(--r-sm);background:var(--card);
    color:var(--text-2);font-size:12.5px;cursor:pointer;transition:border-color .15s,color .15s}
  .copyBtn:hover{border-color:var(--brand);color:var(--brand)}
  .copyBtn.copied{border-color:var(--ok);color:var(--ok)}
  .urlnote{font-size:12px;color:var(--text-3);margin-top:8px;line-height:1.65}

  /* ---------- 二维码 ---------- */
  .qrwrap{background:#f6f8fb;border:1px solid var(--line);border-radius:var(--r-md);
    width:200px;height:200px;margin:14px auto 0;padding:10px;display:flex;align-items:center;justify-content:center}
  img.qr{width:100%;height:100%;display:block}
  .qr-tip{text-align:center;font-size:12px;color:var(--text-3);margin-top:8px}

  /* ---------- 按钮 ---------- */
  .btn{display:block;width:100%;padding:13px;border-radius:var(--r-md);font-size:15px;font-weight:700;
    cursor:pointer;transition:background .15s,border-color .15s,color .15s}
  .btn-primary{background:var(--brand);color:#fff;border:1px solid var(--brand)}
  .btn-primary:hover{background:var(--brand-deep);border-color:var(--brand-deep)}
  .btn-primary:active{background:#0896c8}
  .btn-primary:disabled{background:#9fd6ef;border-color:#9fd6ef;cursor:not-allowed}
  .btn-ghost{background:var(--card);color:var(--text-2);border:1px solid var(--line);margin-top:10px}
  .btn-ghost:hover{border-color:var(--brand);color:var(--brand)}
  .tip{font-size:12.5px;color:var(--text-3);margin-top:10px;line-height:1.7}
  iframe#cap{width:100%;height:420px;border:1px solid var(--line);border-radius:var(--r-md);
    background:#fff;display:none;margin-top:12px}

  /* ---------- ticket 输入 ---------- */
  .row{display:flex;gap:8px;margin-top:14px}
  input[type=text]{flex:1;min-width:0;padding:12px;border:1px solid var(--line);border-radius:var(--r-sm);
    font-size:14px;color:var(--text);background:#fff;outline:none;transition:border-color .15s,box-shadow .15s}
  input[type=text]::placeholder{color:var(--text-3)}
  input[type=text]:focus{border-color:var(--brand);box-shadow:0 0 0 3px rgba(18,183,245,.14)}
  .subBtn{flex:0 0 auto;padding:0 24px;border:1px solid var(--brand);border-radius:var(--r-sm);background:var(--brand);
    color:#fff;font-size:14px;font-weight:700;cursor:pointer;transition:background .15s,border-color .15s}
  .subBtn:hover{background:var(--brand-deep);border-color:var(--brand-deep)}
  .subBtn:disabled{background:#9fd6ef;border-color:#9fd6ef;cursor:not-allowed}
  .msg{margin-top:10px;font-size:13px;line-height:1.6;min-height:18px}
  .msg .ok{color:var(--ok)}
  .msg .err{color:var(--danger)}

  /* ---------- 提示条 ---------- */
  .hint{display:flex;gap:8px;align-items:flex-start;padding:12px 14px;border-radius:var(--r-md);
    font-size:13px;line-height:1.65}
  .hint b{font-weight:600}
  .hint-device{background:var(--warn-bg);color:var(--warn)}
  .hint-device b{color:#b45309}
  .hint-ok{background:var(--ok-bg);color:var(--ok)}

  /* ---------- 步骤 ---------- */
  .steps{display:flex;flex-direction:column;gap:11px}
  .step{display:flex;gap:10px;align-items:flex-start;font-size:13px;color:var(--text-2);line-height:1.6}
  .step .n{flex-shrink:0;width:20px;height:20px;border-radius:50%;background:#f1f4f8;border:1px solid var(--line-2);
    color:var(--text-3);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700}
  .step b{color:var(--text);font-weight:600}

  /* ---------- toast ---------- */
  #toast{position:fixed;left:50%;bottom:30px;transform:translateX(-50%) translateY(90px);
    background:rgba(28,35,48,.94);color:#fff;padding:10px 18px;border-radius:20px;font-size:13px;font-weight:600;
    box-shadow:var(--sh);opacity:0;transition:all .28s cubic-bezier(.2,.9,.3,1.1);pointer-events:none;
    max-width:88vw;z-index:99}
  #toast.show{transform:translateX(-50%) translateY(0);opacity:1}
  #toast.ok{background:rgba(26,158,87,.96)}
  #toast.err{background:rgba(213,69,66,.96)}

  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="mark">Q</div>
    <div>
      <div class="title">QQ 登录验证</div>
      <div class="subtitle">在手机上完成登录验证 · 本页可从任意设备访问</div>
    </div>
  </div>

  <div class="card status st-{STATE_CLS}" id="st">
    <span class="dot"></span>
    <span class="st-txt" id="stTxt">{STATE}</span>
    <span class="phone" id="phone"></span>
  </div>

  <div class="card hidden" id="urlCard">
    <div class="lbl" id="urlLbl">验证地址</div>
    <div class="urlrow">
      <div class="urlbox"><a class="url" id="url" href="#" target="_blank" rel="noopener"></a></div>
      <button class="copyBtn" id="copyBtn" onclick="copyUrl()">复制</button>
    </div>
    <div class="urlnote" id="urlNote"></div>
    <div class="qrwrap"><img class="qr hidden" id="qr" src="" alt="二维码"/></div>
    <div class="qr-tip" id="qrTip">用手机 QQ 扫一扫完成验证</div>
  </div>

  <div class="card hidden" id="capCard">
    <h3>滑块 / 验证码验证</h3>
    <button class="btn btn-primary" id="startBtn" onclick="startCaptcha()">在页面中完成验证</button>
    <div class="tip" id="capTip">点击后在下方面板直接完成验证，ticket 会自动捕获并提交。</div>
    <iframe id="cap" src=""></iframe>
    <button class="btn btn-ghost" id="openBtn" onclick="openCaptcha()">上方无法显示？在新窗口打开验证</button>
    <div class="row">
      <input type="text" id="ticket" placeholder="自动捕获失败时，手动粘贴 ticket"/>
      <button class="subBtn" id="btn" onclick="manualSubmit()">提交</button>
    </div>
    <div class="msg" id="msg"></div>
  </div>

  <div class="card hidden" id="deviceHint">
    <div class="hint hint-device">请用 <b>手机 QQ</b> 打开上方验证地址并完成验证，完成后服务器会自动续登。</div>
  </div>

  <div class="card">
    <h3>操作步骤</h3>
    <div class="steps">
      <div class="step"><span class="n">1</span><div>点击 <b>「在页面中完成验证」</b>，直接在下方解决验证码</div></div>
      <div class="step"><span class="n">2</span><div>ticket <b>自动捕获并提交</b>，服务器自动续登</div></div>
      <div class="step"><span class="n">3</span><div>若显示异常，点 <b>「新窗口打开」</b> 解决后，把 ticket 粘贴到输入框</div></div>
    </div>
  </div>
</div>
<div id="toast"></div>
<script>
let capUrl='', toastTimer=null;
function extractTicket(s){const m=(s||'').match(/[?&#](?:ticket|TICKET)=([0-9A-Za-z_\\-%.+]+)/);return m?decodeURIComponent(m[1]):'';}
function toast(msg,type){const t=document.getElementById('toast');t.textContent=msg;t.className='show '+(type||'');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{t.className=''},2600);}
async function submit(t){
  const msg=document.getElementById('msg');const btn=document.getElementById('btn');
  if(btn) btn.disabled=true;
  try{
    const r=await fetch('/api/ticket',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket:t})});
    const d=await r.json();
    if(d.ok){
      msg.innerHTML='<span class="ok">已收到 ticket，服务器正在续登…</span>';
      document.getElementById('ticket').value='';
      const sb=document.getElementById('startBtn'); if(sb){sb.textContent='已提交';sb.disabled=true;}
      toast('ticket 已提交，等待登录','ok');
    } else {msg.innerHTML='<span class="err">提交失败：'+(d.error||'')+'</span>';toast('提交失败','err');}
  }catch(e){msg.textContent='网络错误，请重试';toast('网络错误','err');}
  if(btn) btn.disabled=false;
}
async function manualSubmit(){const t=document.getElementById('ticket').value.trim();
  if(!t){toast('请输入 ticket','err');return;} submit(t);}
async function copyUrl(){
  const a=document.getElementById('url'); if(!a.textContent) return;
  try{await navigator.clipboard.writeText(a.textContent);}catch(e){}
  const b=document.getElementById('copyBtn'); b.textContent='已复制'; b.classList.add('copied');
  setTimeout(()=>{b.textContent='复制';b.classList.remove('copied')},1800);
  toast('地址已复制','ok');
}
async function startCaptcha(){
  const d=await (await fetch('/api/status')).json(); capUrl=d.url||'';
  const sb=document.getElementById('startBtn');
  if(!capUrl){document.getElementById('capTip').textContent='当前无需验证码';toast('当前无需验证码');return;}
  sb.disabled=true; sb.textContent='正在加载验证…';
  const f=document.getElementById('cap'); f.src=capUrl; f.style.display='block';
  document.getElementById('capTip').textContent='请在下方完成验证，ticket 会自动捕获提交。若空白，点下方按钮在新窗口打开。';
  setTimeout(()=>{sb.textContent='验证已加载，请完成';},1200);
}
function openCaptcha(){ if(capUrl) window.open(capUrl,'_blank'); }
window.addEventListener('message',e=>{const d=e.data;
  if(d&&typeof d==='object'){autoSubmit(d.ticket||d.Ticket||'');}
  else if(typeof d==='string'){autoSubmit(extractTicket(d));}});
function autoSubmit(t){ if(t) submit(t); }
window.addEventListener('hashchange',()=>{autoSubmit(extractTicket(location.hash));});
setInterval(()=>{autoSubmit(extractTicket(location.href));},1500);
async function refresh(){
  try{
    const r=await fetch('/api/status'); const d=await r.json();
    const st=document.getElementById('st');
    st.className='card status st-'+(d.cls||'idle');
    document.getElementById('stTxt').textContent=d.state;
    document.getElementById('phone').textContent=d.phone?('密保 '+d.phone):'';
    const uc=document.getElementById('urlCard');
    const hasUrl=!!d.url;
    uc.classList.toggle('hidden', !hasUrl);
    if(hasUrl){
      const a=document.getElementById('url'); a.textContent=d.url; a.href=d.url;
      const q=document.getElementById('qr');
      if(d.qr){q.src='data:image/png;base64,'+d.qr;q.classList.remove('hidden');}
      else{q.classList.add('hidden');}
      if(d.cls==='device'){
        document.getElementById('urlLbl').textContent='验证地址 · 请用手机 QQ 打开';
        document.getElementById('urlNote').textContent='复制到浏览器可能无效，请在手机 QQ 内打开。完成后回到本页等待即可。';
      }else{
        document.getElementById('urlLbl').textContent='验证地址 · 也可在浏览器打开';
        document.getElementById('urlNote').textContent='点击下方按钮可在页面内直接完成；或在浏览器打开此地址，ticket 会自动捕获。';
      }
    }
    document.getElementById('capCard').classList.toggle('hidden', d.cls!=='slider');
    document.getElementById('deviceHint').classList.toggle('hidden', d.cls!=='device');
    if(d.cls==='ok'){
      const sb=document.getElementById('startBtn'); if(sb){sb.textContent='已提交，等待登录';sb.disabled=true;}
    }
  }catch(e){}
}
setInterval(refresh,3000); refresh();
</script>
</body></html>"""


class VerifyServer:
    """验证中转网页服务。"""

    def __init__(self, ticket_file: str, port: int = 8765, host: str = "0.0.0.0"):
        self.ticket_file = ticket_file
        self.port = port
        self.host = host
        self._state = "等待验证..."
        self._cls = "idle"
        self._url = ""
        self._phone = ""
        self._qr_b64 = ""
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_get("/api/status", self._status)
        app.router.add_post("/api/ticket", self._ticket)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        try:
            site = web.TCPSite(self._runner, self.host, self.port)
            await site.start()
        except OSError:
            # 端口被占：自动换空闲端口（如本机其它服务占用）
            free = _find_free_port(self.port)
            print(f"[verify-server] 端口 {self.port} 被占用，改用 {free}")
            self.port = free
            site = web.TCPSite(self._runner, self.host, self.port)
            await site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    def _get_ip(self) -> str:
        """局域网 IP（页面提示用）。"""
        try:
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def show(self, url: str = "", phone: str = "", qr_bytes: Optional[bytes] = None, state: str = "验证中", cls: str = "slider") -> None:
        """更新当前验证状态（URL/二维码/密保手机），页面自动刷新显示。"""
        self._url = url
        self._phone = phone
        self._state = state
        self._cls = cls
        self._qr_b64 = ""
        if qr_bytes:
            self._qr_b64 = base64.b64encode(qr_bytes).decode()
        ip = self._get_ip()
        print(f"[verify-server] 验证页面：http://{ip}:{self.port}/  或本机 http://127.0.0.1:{self.port}/")
        print(f"[verify-server] 请在其它设备打开上面地址完成验证并提交 ticket")

    async def _index(self, request):
        state_cls = self._cls
        return web.Response(
            text=_PAGE.replace("{STATE}", self._state).replace("{STATE_CLS}", state_cls),
            content_type="text/html",
            charset="utf-8",
        )

    async def _status(self, request):
        return web.json_response({
            "state": self._state,
            "cls": self._cls,
            "url": self._url,
            "phone": self._phone,
            "qr": self._qr_b64,
        })

    async def _ticket(self, request):
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "bad json"})
        ticket = str(data.get("ticket") or "").strip()
        if not ticket:
            return web.json_response({"ok": False, "error": "empty ticket"})
        try:
            os.makedirs(os.path.dirname(self.ticket_file) or ".", exist_ok=True)
            with open(self.ticket_file, "w", encoding="utf-8") as f:
                f.write(ticket)
        except OSError as e:
            return web.json_response({"ok": False, "error": str(e)})
        self._state = "已收到 ticket，等待登录"
        self._cls = "ok"
        return web.json_response({"ok": True})
