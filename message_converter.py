"""icqq 消息模型 <-> AstrBot 消息模型 的双向转换。

icqq 消息段（dict，字段与 icqq JS 版逐字一致）:
    {"type": "text", "text": ...}
    {"type": "at", "qq": <int|"all">}
    {"type": "image", "file": ..., "url": "https://..."}
    {"type": "face", "id": <int>}
    {"type": "record", "file": "protobuf://...", "url": "https://..."}
    {"type": "video", "file": ..., "url": ...}
    {"type": "json", "data": "<json字符串>"}
    {"type": "xml", "data": "..."}
    {"type": "file", "name": ..., "fid": ...}
    等

AstrBot 消息组件: Plain / At / AtAll / Face / Image / Record / Video /
Json / File / Reply / Node / Nodes ...
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Optional

from astrbot.api import logger
from astrbot.api.message_components import (
    At,
    AtAll,
    BaseMessageComponent,
    Face,
    File,
    Image,
    Json,
    Node,
    Nodes,
    Plain,
    Record,
    Reply,
    Unknown,
    Video,
)
from astrbot.api.platform import AstrBotMessage, Group, MessageMember, MessageType

from icqq import segment
from icqq.message import genDmMessageId, genGroupMessageId, parseDmMessageId, parseGroupMessageId


def _tostr(v) -> str:
    if v is None:
        return ""
    return str(v)


def _extract_url(elem: dict) -> str:
    """从 icqq 媒体段里取可用的 https URL（优先 url 字段，其次 file 若为 http）。"""
    url = _tostr(elem.get("url") or "")
    if url.startswith("http"):
        return url
    file_ = _tostr(elem.get("file") or "")
    if file_.startswith("http"):
        return file_
    return ""


# ---------------------------------------------------------------------------
# icqq 消息段 -> AstrBot 组件（接收方向）
# ---------------------------------------------------------------------------

def icqq_chain_to_astr(segments, msg=None, is_group: bool = False):
    """把 icqq 的消息段列表转成 AstrBot 组件列表 + 纯文本。

    返回 (components, message_str)
    """
    components: list[BaseMessageComponent] = []
    message_str = ""

    for elem in segments or []:
        if not isinstance(elem, dict):
            continue
        etype = elem.get("type")

        if etype == "text":
            text = _tostr(elem.get("text") or "")
            if text:
                components.append(Plain(text=text))
                message_str += text

        elif etype == "at":
            qq = elem.get("qq")
            if str(qq) == "all":
                components.append(AtAll())
                message_str += " [@全体成员]"
            else:
                qq_s = str(qq or "")
                # icqq 的 at 段通常不带名字，尽量用 sender 信息补全（可选）
                name = _tostr(elem.get("name") or "")
                components.append(At(qq=qq_s, name=name))
                message_str += f" @{name}({qq_s}) " if name else f" @{qq_s} "

        elif etype in ("image", "flash"):
            url = _extract_url(elem)
            if url:
                components.append(Image.fromURL(url))
            else:
                # 本地路径（离线缓存图片）或无法解析的 protobuf 串
                file_ = _tostr(elem.get("file") or "")
                if file_ and not file_.startswith("protobuf://") and not file_.startswith("file://"):
                    try:
                        components.append(Image.fromFileSystem(file_))
                    except Exception as e:
                        logger.warning(f"[icqq] 图片路径无效，已忽略: {file_} ({e})")
            message_str += " [图片]"

        elif etype == "face":
            try:
                components.append(Face(id=int(elem.get("id") or 0)))
            except (TypeError, ValueError):
                pass
            message_str += f" [表情:{elem.get('id')}]"

        elif etype == "record":
            url = _extract_url(elem)
            if url:
                components.append(Record.fromURL(url))
            message_str += " [语音]"

        elif etype == "video":
            url = _extract_url(elem)
            if url:
                components.append(Video.fromURL(url))
            message_str += " [视频]"

        elif etype == "json":
            data = _tostr(elem.get("data") or "")
            try:
                parsed = json.loads(data)
                components.append(Json(data=parsed))
                # json 卡片常见 prompt 字段，可作摘要
                if isinstance(parsed, dict) and parsed.get("prompt"):
                    message_str += _tostr(parsed["prompt"])
                else:
                    message_str += " [JSON卡片]"
            except Exception:
                components.append(Plain(text=data))
                message_str += data

        elif etype == "xml":
            data = _tostr(elem.get("data") or "")
            # 取 xml 的 brief 属性作为摘要
            brief = re.search(r'brief="(.*?)"', data)
            if brief and brief.group(1):
                message_str += brief.group(1)
            else:
                message_str += " [XML卡片]"
            components.append(Unknown(text=data))

        elif etype == "file":
            name = _tostr(elem.get("name") or "")
            components.append(File(name=name, url=""))
            message_str += f" [文件:{name}]"

        elif etype in ("bface", "sface", "rps", "dice", "poke", "mirai", "shake"):
            # 不便于映射为 AstrBot 组件的特殊表情/玩法，保留文本占位
            message_str += f" [{etype}]"

        else:
            logger.debug(f"[icqq] 未映射的消息段类型: {etype} {elem}")
            message_str += f" [{etype}]"

    return components, message_str.strip()


def icqq_message_to_astr(adapter, msg, is_group: bool) -> AstrBotMessage:
    """把 icqq 的 GroupMessage / PrivateMessage 转为 AstrBotMessage。"""
    abm = AstrBotMessage()
    client = adapter.get_client()
    bot_uin = str(getattr(client, "uin", "") or "")
    abm.self_id = str(getattr(msg, "self_id", "") or bot_uin)

    sender = getattr(msg, "sender", None) or {}
    user_id = str(sender.get("user_id") or getattr(msg, "user_id", "") or "")
    nickname = _tostr(sender.get("card") or sender.get("nickname") or "")
    abm.sender = MessageMember(user_id=user_id, nickname=nickname or user_id)

    if is_group:
        abm.type = MessageType.GROUP_MESSAGE
        gid = str(getattr(msg, "group_id", "") or "")
        abm.group = Group(group_id=gid)
        abm.group_id = gid
        abm.session_id = gid
    else:
        abm.type = MessageType.FRIEND_MESSAGE
        abm.session_id = user_id

    abm.message_id = str(getattr(msg, "message_id", "") or uuid.uuid4().hex)
    abm.timestamp = int(getattr(msg, "time", 0) or time.time())
    abm.raw_message = msg

    # 引用回复（source）优先插入到链首
    components: list[BaseMessageComponent] = []
    source = getattr(msg, "source", None)
    if source and isinstance(source, dict):
        q_sender = source.get("user_id") or 0
        q_time = source.get("time") or 0
        q_seq = source.get("seq") or 0
        q_rand = source.get("rand") or 0
        q_brief = _tostr(source.get("message"))
        try:
            if is_group and getattr(msg, "group_id", None):
                mid = genGroupMessageId(msg.group_id, int(q_sender), int(q_seq), int(q_rand), int(q_time), 1)
            else:
                mid = genDmMessageId(int(q_sender), int(q_seq), int(q_rand), int(q_time), 1)
            components.append(Reply(
                id=mid,
                chain=[Plain(q_brief)] if q_brief else [],
                sender_id=int(q_sender),
                sender_nickname="",
                time=int(q_time),
                message_str=q_brief,
            ))
        except Exception as e:
            logger.debug(f"[icqq] 构建引用回复失败: {e}")

    chain, message_str = icqq_chain_to_astr(getattr(msg, "message", []), msg, is_group)
    components.extend(chain)
    abm.message = components
    abm.message_str = message_str or _tostr(getattr(msg, "raw_message", ""))
    return abm


# ---------------------------------------------------------------------------
# AstrBot 消息链 -> icqq 消息段（发送方向）
# ---------------------------------------------------------------------------

async def astr_chain_to_icqq(chain, is_group: bool = False):
    """把 AstrBot MessageChain 转成 (icqq 消息段列表, 引用回复 source)。

    引用回复、合并转发、文件等特殊段单独提取，返回给发送方处理。
    """
    segs: list = []
    source: Optional[dict] = None
    forward_nodes: Optional[list] = None  # 合并转发节点

    for comp in (chain.chain if chain is not None else []):
        if isinstance(comp, Plain):
            if comp.text and comp.text.strip():
                segs.append(segment.text(comp.text))

        elif isinstance(comp, At):
            qq = str(getattr(comp, "qq", "") or "")
            if qq == "all":
                segs.append(segment.at("all"))
            elif qq.isdigit():
                segs.append(segment.at(int(qq)))
            else:
                segs.append(segment.at(qq))

        elif isinstance(comp, AtAll):
            segs.append(segment.at("all"))

        elif isinstance(comp, Face):
            try:
                segs.append(segment.face(int(comp.id)))
            except (TypeError, ValueError):
                segs.append(segment.face(comp.id))

        elif isinstance(comp, Image):
            try:
                path = await comp.convert_to_file_path()
                segs.append(segment.image(path))
            except Exception as e:
                logger.error(f"[icqq] 发送图片失败: {e}")

        elif isinstance(comp, Record):
            try:
                path = await comp.convert_to_file_path()
                segs.append(segment.record(path))
            except Exception as e:
                logger.error(f"[icqq] 发送语音失败: {e}")

        elif isinstance(comp, Video):
            try:
                path = await comp.convert_to_file_path()
                segs.append(segment.video(path))
            except Exception as e:
                logger.error(f"[icqq] 发送视频失败: {e}")

        elif isinstance(comp, Json):
            data = comp.data
            if isinstance(data, dict):
                data = json.dumps(data, ensure_ascii=False)
            segs.append(segment.json(_tostr(data)))

        elif isinstance(comp, Reply):
            source = await reply_to_icqq_source(comp, is_group)
            if source is None:
                logger.warning("[icqq] 无法解析引用消息 id，已忽略引用")

        elif isinstance(comp, Node):
            if forward_nodes is None:
                forward_nodes = []
            forward_nodes.append(node_to_fake(comp))

        elif isinstance(comp, Nodes):
            if forward_nodes is None:
                forward_nodes = []
            for node in getattr(comp, "nodes", []) or []:
                forward_nodes.append(node_to_fake(node))

        elif isinstance(comp, File):
            # 文件用 sendFile API 单独发送，这里标记由发送方处理
            file_path = None
            try:
                file_path = await comp.get_file(allow_return_url=False)
            except Exception:
                pass
            if not file_path and getattr(comp, "url", ""):
                logger.warning("[icqq] 收到文件 URL，但 icqq 需本地文件路径，已跳过该文件段")
            segs.append({
                "__astr_file__": True,
                "path": file_path,
                "name": getattr(comp, "name", "") or (file_path and file_path.rsplit("/", 1)[-1]),
            })

        elif isinstance(comp, Unknown):
            if getattr(comp, "text", ""):
                segs.append(Plain(text=str(comp.text)))

        else:
            logger.warning(f"[icqq] 发送时忽略不支持的组件: {type(comp).__name__}")

    return segs, source, forward_nodes


def node_to_fake(node) -> dict:
    """把 AstrBot Node 转成 icqq makeForwardMsg 需要的 fake 对象。"""
    uin = getattr(node, "uin", 0) or 0
    try:
        uin = int(uin)
    except (TypeError, ValueError):
        uin = 0
    fake = {
        "user_id": uin,
        "nickname": _tostr(getattr(node, "name", "") or ""),
        "message": [],
    }
    content = getattr(node, "content", None)
    if isinstance(content, (list, tuple)):
        fake["message"] = [Plain(c) if isinstance(c, str) else c for c in content]
    elif content is not None:
        fake["message"] = [content]
    return fake


async def reply_to_icqq_source(reply_comp, is_group: bool) -> Optional[dict]:
    """从 AstrBot Reply 组件构建 icqq 引用回复 source。

    AstrBot 收到的引用消息，其 Reply.id 就是 icqq 的消息 id（base64），
    可直接解析出 seq/rand/time 用于构造 source。
    """
    try:
        mid = _tostr(getattr(reply_comp, "id", "") or "")
        if not mid:
            return None
        message_str = _tostr(getattr(reply_comp, "message_str", "") or "")
        if is_group and len(mid) > 24:
            info = parseGroupMessageId(mid)
            return {
                "user_id": int(getattr(reply_comp, "sender_id", 0) or info.get("user_id") or 0),
                "seq": info["seq"],
                "rand": info["rand"],
                "time": info["time"],
                "message": message_str,
            }
        if not is_group and len(mid) <= 24:
            info = parseDmMessageId(mid)
            return {
                "user_id": info["user_id"],
                "seq": info["seq"],
                "rand": info["rand"],
                "time": info["time"],
                "message": message_str,
            }
    except Exception as e:
        logger.debug(f"[icqq] 解析引用消息失败: {e}")
    return None
