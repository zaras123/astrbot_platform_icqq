# astrbot_platform_icqq

基于 **icqq-py**（icqq 协议库的纯 Python 移植）的 AstrBot **平台适配器**，把 QQ 个人号接入 AstrBot。

> 这是平台适配器（插件），不是机器人插件。安装后需在 AstrBot WebUI「消息平台」里启用一个
> 类型为 `icqq` 的平台实例，而不是在「插件」列表里操作。

## 功能

- 群聊 / 私聊收发（文本、@、图片、语音、视频、文件、引用回复、合并转发）
- 密码登录 / 扫码登录 / token 登录（自动缓存 token，免重复扫码）
- 滑动验证码、登录保护（设备锁）提示
- 断线自动重连 + 离线看门狗自动重登
- 主动消息（`send_by_session`，支持定时任务等场景）
- 与 icqq API 对齐：可在其他插件里用 `client.pickGroup(...)` 等直接操作

## 前置条件

1. **AstrBot**（≥ 4.16，< 5）
2. **icqq-py** —— 本适配器的协议核心（随本项目附带的独立 wheel，或本地源码）：
   ```bash
   # 方式一：安装随附的独立 wheel
   pip install icqq_py-0.6.10-py3-none-any.whl
   # 方式二：安装本地移植版源码
   pip install D:\zhuomian\icqq
   # 方式三：icqq-py 已发布到 PyPI 时
   pip install icqq-py
   ```
3. **签名服务器**（必须）—— qsign（unidbg-fetch-qsign）或 tx-sign 均可：
   - qsign：`http://127.0.0.1:8080/sign?key=xxx`
   - tx-sign：`http://127.0.0.1:8080`（不带 key 参数）
   
   没有签名服务器时登录/发消息大概率失败。

## 安装

1. 把本项目目录放到 AstrBot 的 `data/plugins/` 下（或通过 WebUI 插件市场/上传安装）
2. 在 AstrBot 运行环境中安装依赖：
   ```bash
   pip install -r requirements.txt      # qrcode 可选
   pip install <icqq-py 路径>           # 见前置条件
   ```
3. 重启 AstrBot（或重载插件）
4. WebUI →「消息平台」→ 添加平台，类型选 `icqq`，填写：
   - `uin`：机器人 QQ 号（留 0 则扫码登录）
   - `password`：密码（留空则扫码登录；可填 32 位十六进制 MD5）
   - `platform`：协议（默认 2 = aPad）
   - `sign_api_addr`：签名服务器地址（**必填**）
   - 启用该平台实例

## 扫码登录

密码留空即可。启动后看 AstrBot 日志（Dashboard → 日志），会输出二维码文件路径：

```
[icqq] 请用手机 QQ 扫码登录，二维码已保存：<data_dir>/qrcode.png
```

用手机 QQ 扫该图片即可。登录成功后 token 会缓存在数据目录，下次免扫码。

## 在其它插件中使用 icqq

AstrBot 通过 `context.register_platform_adapter_type("icqq")` 过滤器可以只响应 icqq 平台的
事件；也可以拿到底层 icqq 客户端做高级操作：

```python
from astrbot.api.platform import PlatformAdapterType
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterTypeFilter
```

事件对象里可通过 `event.platform_meta.name == "icqq"` 判断来源；若需要底层客户端，
可在 icqq 适配器实例上调用 `get_client()`。

## 配置项

| 配置 | 类型 | 说明 |
|---|---|---|
| uin | int | 机器人 QQ 号；0 = 扫码登录 |
| password | string | 密码；空 = 扫码登录 |
| platform | int | 1 Android / 2 aPad / 3 Watch / 4 iMac / 5 iPad / 6 Tim |
| sign_api_addr | string | 签名服务器地址（必填） |
| data_dir | string | 数据目录（设备信息/二维码/token） |
| log_level | string | icqq 日志级别 |
| ignore_self | bool | 忽略自己的消息 |
| reconnect_interval | int | 重连间隔（秒） |
| resend | bool | 群消息风控分片重发 |
| cache_group_member | bool | 缓存群成员 |

## 消息转换说明

| icqq 段 | AstrBot 组件 |
|---|---|
| text | Plain |
| at / at=all | At / AtAll |
| image | Image（取 url，本地路径亦可） |
| face | Face |
| record | Record（取 url） |
| video | Video（取 url） |
| json / xml | Json / Unknown |
| 引用(source) | Reply（消息 id 互通，可直接引用回复） |
| file（离线文件） | File |

发送方向：AstrBot 的 Image/Record/Video 会先转本地路径再经 icqq 上传通道发送；
Nodes 合并转发 → icqq makeForwardMsg；Reply → 解析消息 id 构建引用。

## 已知限制

- 语音（silk）转码需要 `pysilk`（对应 icqq 的 silk-wasm）；未安装时语音上传会失败，文本/图片不受影响
- 暂不处理讨论组消息
- 合并转发依赖 icqq 的 MultiMsg 上传通道，若签名服务器不支持可能失败
- icqq 协议登录本身有风控风险，请用正常使用的小号测试

## License

MPL-2.0（与 icqq 一致）。适配器使用 [icqq-py](https://github.com/icqqjs/icqq) 协议库。
