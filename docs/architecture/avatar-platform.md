# 虚拟形象（Avatar）

虚拟形象模块让 AI 主播拥有一个可见的身体。AI 主播决定说什么、带什么情绪，本模块负责把它变成虚拟形象上的实际表现：变表情、动嘴、身体微动、做预设动作。

支持三个平台：VTube Studio（简称 VTS）、Warudo、VRChat，用哪个由配置决定。代码在 `src/modules/avatar/`，配置文件是 `config/avatar.toml`。

## 模块组成

三个平台各有一个适配器（`src/modules/avatar/platform/` 下的 `vts/`、`warudo/`、`vrchat/`），适配器之间互不依赖，只共享三份代码：

- **接口约定**（`protocol.py`）：每个平台适配器都必须实现同一组方法——设置表情、列出预设动作、触发预设动作。方法齐全即满足约定，不需要继承任何基类。
- **口型分析器**（`lipsync/`）：把 TTS 正在播放的音频实时分析成"嘴张开多大、在发什么元音"的信号，分发给各平台的口型代码。分析结果不含任何平台专用参数名，所以三个平台可以共用同一个分析器。
- **事件绑定**（`speech_binding.py`）：订阅"主播说了什么（带什么情绪）"和"TTS 是否正在播放"两类事件，转成对适配器的调用。

## 形象的四种驱动方式

| 方式 | 什么时候发生 | 效果 |
|------|------------|------|
| LLM 调工具 | AI 主播在决策中主动调用 | 设置表情、查询/触发预设动作等 |
| 说话自动变表情 | 主播每发布一句发言 | 按发言携带的情绪和强度自动设置表情 |
| 口型同步 | TTS 播放语音期间 | 嘴巴跟着声音开合 |
| 自动小动作 | 后台循环持续运行 | 没人互动时也有轻微动作，形象不僵硬 |

四种方式互相独立：某一种出错只记日志，不影响其他方式，也不影响正常的说话和决策。

### LLM 调用的工具

工具名带平台前缀（如 `vts_set_expression`）；配置里启用了哪个平台，LLM 就能看到哪个平台的工具。所有工具参数都是语义化的——LLM 说"开心、强度 0.8"，不需要知道具体模型参数名。

| 工具 | 平台 | 作用 |
|------|------|------|
| `*_set_expression(emotion, intensity)` | 三个平台都有 | 设置情绪（17 种枚举值）和强度（0~1） |
| `*_list_preset_actions` | 三个平台都有 | 列出可用的预设动作目录 |
| `*_trigger_preset_action(action)` | 三个平台都有 | 触发一个预设动作；名字写错时会把可用目录随失败结果一起返回，LLM 据此自我纠正 |
| `vts_set_idle_enabled` | 仅 VTS | 开关 idle 微动 |
| `warudo_set_sight(target)` | 仅 Warudo | 设置视线看向哪里（摄像头 / 弹幕 / 手机） |

### 说话自动变表情

主播的每次发言都携带情绪标注（17 种之一）和强度。适配器订阅发言事件，把情绪翻译成本平台的参数写入。这条路不依赖 TTS——没开语音，表情照样变。

### 口型同步

TTS 播放器把正在播放的音频实时递给共享口型分析器；分析器算出嘴型信号后，各平台自己的口型代码再把信号写成本平台参数：

- VTS：写 `MouthOpen` 参数
- Warudo：写元音 blendshape 通道（启用时应关闭 Warudo 自带口型，避免两边打架）
- VRChat：不支持——VRChat 的口型参数每个模型各不相同、没有统一标准，如实不做

### 自动小动作

各平台自带的后台循环，让形象在没有互动时也不僵硬：

- VTS：idle 微动——头部和身体按随机节奏轻微摆动（刻意不用正弦波，随机更像真人）；说话时自动暂停，避免和口型打架
- Warudo：随机眨眼、移动视线、说话时点头、打字动作等后台任务
- VRChat：无

## 换平台不用改主播代码

主播 Agent 的代码里搜不到任何平台名——这是有验收标准的硬约束：`grep src/agents/streamer/` 必须零命中 `vts` / `warudo` / `vrchat`。主播只发布"我说了一句话，情绪是 X"这样的事件；怎么把它变成具体平台的动作，是适配器自己的事。情绪到平台参数的翻译知识全部住在适配器里（见 [ADR-023](../decisions/023-avatar-platform-agnostic-boundary.md)）。

新增一个平台的验收清单：

1. 实现接口约定的全部方法，通过接口约定测试
2. 工具命名和参数与上表逐字对齐
3. 实现口型代码，或如实说明不支持
4. 在 `tools/bootstrap.py` 与 `config/registry.py` 两处登记

## 三个平台对比

| | VTS | Warudo | VRChat |
|---|-----|--------|--------|
| 连接方式 | WebSocket（localhost:8001） | WebSocket（localhost:19190） | UDP OSC（127.0.0.1:9000） |
| 表情怎么做 | 写内置参数 | 写 blendshape 状态通道 | 不支持（OSC 无标准表情参数），如实返回"未应用" |
| 预设动作 | VTS 热键 | Warudo 蓝图动作 | VRChat 手势（9 种） |
| 断线处理 | 自动重连 + 健康检查 | 自动重连 | 无需（UDP 无连接概念） |
| 口型 | 支持 | 支持（元音通道） | 不支持 |
| 自动小动作 | idle 微动 | 眨眼 / 移眼 / 点头等 | 无 |

三个平台都不做字幕：字幕由独立的字幕模块统一提供（来龙去脉见 [ADR-025](../decisions/025-subtitle-single-source.md)）。

## VTS：换模型需要重新配置吗

不需要。默认配置用的是 VTS 内置参数（控制头部角度、张嘴、眉毛等的通用参数），任何模型都接受这些参数，换模型零操作。在 VTS 界面里换模型后，程序检测到模型变化，会自动重新读取新模型的热键清单、参数范围等数据。

需要注意："参数写入成功"和"模型上看得出效果"是两回事。模型作者制作模型时把哪些参数绑到了哪个部位，VTS 的接口查不到。默认参数按 17 个真实模型的实测数据选取（绝大多数模型都绑定了这些参数），但个别模型上某个表情可能看不出来——遇到这种情况，改配置里的参数名即可（实测依据与配置结构见 [ADR-028](../decisions/028-avatar-domain-config-graduation.md)）。

## VTS 连接管理

- **授权**：首次连接时在 VTS 里弹窗确认授权，token 保存在 `data/vts_token.txt`
- **后台检查**：每 5 秒一次——连接断了就重连；连着就检查健康状况、是否换了模型
- **请求排队**：所有对 VTS 的请求串行发送（底层库的限制：并发请求会互相抢响应）

工具设计的原则与取舍见 [ADR-026](../decisions/026-avatar-tool-surface-contract.md)；口型通道的设计见 [ADR-024](../decisions/024-lipsync-frame-channel.md)。

## 配置与装配

`config/avatar.toml` 结构（本文件内的段，在项目其他文档中带文件名记作 `[avatar.platform]`、`[avatar.lipsync]`）：

```toml
[platform]
enabled = ["vts"]        # 启用哪些平台，合法值：vts / warudo / vrchat

[platform.vts]           # 各平台自己的参数：地址端口、idle 设置等
# ...

[lipsync]                # 口型分析器调参（与具体平台无关）
# ...
```

- 每个配置键的权威定义在各组件代码的 ConfigSchema 里（Pydantic）；改配置结构须走配置系统的版本与迁移流程
- 启动时按 `enabled` 名单装配：名单外的平台完全不加载，工具也不注册

## 相关文档

- 架构决策：[ADR-023 平台无关边界](../decisions/023-avatar-platform-agnostic-boundary.md) / [ADR-024 口型帧级通道](../decisions/024-lipsync-frame-channel.md) / [ADR-025 字幕单一源](../decisions/025-subtitle-single-source.md) / [ADR-026 虚拟形象工具约定](../decisions/026-avatar-tool-surface-contract.md) / [ADR-028 虚拟形象配置文件](../decisions/028-avatar-domain-config-graduation.md)
- [事件系统](event-system.md) — 主播发言、TTS 播放事件的语义
- [数据流与边界规则](data-flow.md) — 情绪如何到达虚拟形象、口型音频从哪里来
- [v2 架构叙事](v2-architecture.md) — 工具类组件的判据
- 事件名常量、配置 Schema、目录结构以代码为唯一事实源（`src/modules/events/names.py`、`src/modules/config/avatar_schemas.py`、`src/modules/avatar/`）
