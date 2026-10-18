# LAPLACE Event Bridge 弹幕接入

## 什么是 LAPLACE

[LAPLACE Chat](https://laplace.live/chat) 是一个运行在浏览器中的 B 站直播聊天面板：把弹幕、Super Chat、礼物分栏显示，并提供 OBS 覆盖层、TTS、实时翻译、OBS/VTS 控制等能力；免安装、免账号。

[LAPLACE Event Bridge](https://laplace.live/chat/event-bridge) 是 LAPLACE Chat 的 **WebSocket 事件桥**：把 LAPLACE Chat 收到的实时 B 站直播事件（弹幕、礼物、SC、上舰、进房等）转发给本地客户端。Amaidesu 的 `bili_danmaku_laplace` 采集器以 **client 角色**连接桥接服务器，把事件转换为现有的 `room.message.*`，因此不需要直接调用 B 站接口。

## 数据链路

```
LAPLACE Chat 控制台 (https://chat.laplace.live/dashboard/<房间号>, server 角色)
        ↓ 推送 B 站事件
桥接服务器（leb-server，默认 ws://localhost:9696）
        ↓ 广播
Amaidesu bili_danmaku_laplace 采集器 (client 角色)
        ↓ room.message.danmaku / gift / super_chat / guard / enter
Planner / 存储 / 字幕 / 虚拟形象
```

LAPLACE 是**传输层**，事件仍属于 B 站：采集器统一写 `platform="bilibili"`，观众身份键与付费统计口径与直接接入 B 站时一致。

## 前置条件

**先阅读 [LAPLACE Event Bridge 文档](https://laplace.live/chat/event-bridge)，按其中说明准备一个可用的桥接服务器。** 本文只覆盖 Amaidesu 侧的配置与排障。

1. **桥接服务器（`leb-server`）**：从 [event-bridge Releases](https://github.com/laplace-live/event-bridge/releases) 下载对应平台的单文件二进制（Windows 为 `leb-server-windows-x64.exe`）。该文件不随仓库分发。也可以使用 LAPLACE Event Fetcher v2.2.0+ 的 bridge mode，此时不需要保持控制台页面开启。
2. **LAPLACE Chat 控制台**：使用 `leb-server` 时，打开 `https://chat.laplace.live/dashboard/<房间号>`。Event Bridge 设置只存在于控制台页面，OBS 页面不转发事件。

## 配置步骤

### 1. 启动 leb-server

```bash
# 默认监听 localhost:9696，无认证
leb-server-windows-x64.exe

# 需要认证时（token 与 Amaidesu 配置保持一致）
leb-server-windows-x64.exe --auth "your-token"
```

可选参数：`--host`（默认 `localhost`）、`--port`（默认 `9696`）、`--debug`。

### 2. 连接 LAPLACE 控制台

1. 打开 `https://chat.laplace.live/dashboard/<房间号>`。
2. 点击工具栏齿轮「设置」，打开「控制台设置」抽屉。
3. 找到「Event Bridge 集成」：
   - 主机地址：`localhost`
   - 端口：`9696`
   - 密码：留空；若 leb-server 用 `--auth` 启动，填写同一个 token
4. 点击「连接」；成功后可保持「页面载入时自动连接」开启。

> 控制台页面必须保持开启——它是事件源。浏览器可能节流后台标签页，建议把 `chat.laplace.live` 加入浏览器性能白名单。

### 3. 配置 Amaidesu

在 `config/collectors.toml` 的 `enabled` 名单加入 `bili_danmaku_laplace`，并填写同名子段：

```toml
enabled = ["console_input", "bili_danmaku_laplace"]

[bili_danmaku_laplace]
url = "ws://localhost:9696"
token = ""            # 与 leb-server --auth 一致；无认证留空
room_id = 0           # 0=不过滤；填写时使用规范长房间号
```

其余字段（事件开关、去重缓存、重连区间等）的权威定义在
[`BiliDanmakuLaplaceCollector.ConfigSchema`](../../src/modules/collectors/bilibili/laplace/bili_danmaku_laplace_collector.py)。
未写的键由配置加载管线补默认值并写回，不需要手工抄一份字段表。

**同一房间只启用一种 B 站消息源**（`bili_danmaku` / `bili_danmaku_official` / `bili_danmaku_laplace` 三选一）。同时启用多个真实源会让同一条弹幕被采集多次，导致落库与统计翻倍。

采集器启动后会无限重连，因此 Amaidesu、leb-server、控制台的启动顺序不限；首次连接失败只记录 warning，不阻塞启动。

## 事件映射

Laplace 的 `message` / `gift` / `superchat` / `toast` / `interaction` 分别映射为
`room.message.*` 语义事件；映射表与过滤/去重/脱敏逻辑的权威实现在
[`events.py` 的 `LaplaceEventMapper`](../../src/modules/collectors/bilibili/laplace/events.py)。
其他事件（在线人数、点赞、天选、红包、弹幕撤回等）当前忽略；SC 删除与弹幕撤回不会追溯修改已落库数据。

## 已知限制

- **实时流，无历史补播**：断线期间的事件不会补发；重连后依靠 `id` / `stableKey` 去重，不会重复落库。
- **礼物金额为源限制**：Laplace 只提供含舰队折扣的总价，采集器把它同时写入 `total_price` 与 `paid_price`；`unit_price` 是由总价反推的近似值。现有金额口径要求贡献按标价计算，该源无法提供标价，因此贡献统计会按折扣后金额计算；代码与本文均已显式注明。
- **上舰无粉丝牌快照**：Laplace `toast` 事件没有粉丝牌字段，`GuardInfo` 的该两项保持默认；原始事件仍在 `raw_data` 中。
- **Dashboard 实时事件流不显示 guard**：既有广播清单只包含 danmaku/gift/super_chat/enter；guard 仍会正常落库与进入 Planner 上下文。
- **控制台页面必须开启**；只开 OBS 弹幕窗口不会向 bridge 推送事件。

## 排障

| 现象 | 处理 |
|---|---|
| Amaidesu 日志「leb-server 不可达」 | 确认 exe 已启动且端口与 `url` 一致；检查系统代理是否拦截 localhost |
| 日志「认证失败（401）」 | `token` 与 leb-server `--auth` 不一致，或控制台密码与之一致但 Amaidesu 未填 |
| 连接成功但没有事件 | 确认控制台在 `/dashboard/<房间号>` 页面、Event Bridge 显示已连接、直播间确实在开播 |
| 事件被过滤 | 检查 `room_id` 是否与事件 `origin` 一致；填 0 可先排除过滤因素 |
| 同一弹幕出现两次 | 检查 `enabled` 是否同时启用了 legacy/official/laplace 中的多个 |
