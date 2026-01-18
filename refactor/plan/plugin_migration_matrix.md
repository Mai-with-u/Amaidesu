# 插件迁移矩阵

**用途**: 详细追踪所有 23 个插件的迁移路径、依赖和测试需求

## 插件分类总览

| 类别 | 数量 | 迁移到 | 阶段 |
|------|------|--------|------|
| 输入类插件 | 6 | InputProvider | Phase 2 |
| 输出类插件 | 5 | OutputProvider | Phase 4 |
| 游戏类插件 | 4 | Extension | Phase 5 |
| 处理类插件 | 5 | Extension | Phase 5 |
| 其他插件 | 3 | Extension | Phase 5 |

---

## Phase 2: 输入类插件迁移

### 1. console_input → ConsoleInputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/console_input/plugin.py` |
| **目标位置** | `src/providers/input/console_input_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | InputProvider |
| **代码行数** | ~500 行 |
| **核心功能** | 控制台输入、命令处理（`/help`, `/status`, `/reload`） |
| **依赖** | `MessageBase` (maim_message), `config` |
| **新依赖** | `InputProvider`, `RawData`, `NormalizedText` |

**迁移步骤**:
1. 创建 `ConsoleInputProvider` 类，继承 `InputProvider`
2. 迁移 `connect()` 方法（启动控制台监听）
3. 迁移 `disconnect()` 方法（停止监听）
4. 迁移 `get_raw_data()` 方法（返回用户输入）
5. 将命令处理逻辑移到独立的 `CommandHandler` 类
6. 保留现有命令支持（`/help`, `/status`, `/reload`）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `InputProvider` 接口（Phase 1）
- ✅ 添加: `RawData`, `NormalizedText` 数据结构（Phase 2）
- ✅ 保留: `MessageBase` 兼容性（如果需要）

**测试用例**:
- [ ] 测试控制台输入正确接收
- [ ] 测试命令 `/help` 正确显示帮助信息
- [ ] 测试命令 `/status` 正确显示系统状态
- [ ] 测试命令 `/reload` 正确重新加载配置
- [ ] 测试无效命令显示错误信息
- [ ] 测试多行输入处理
- [ ] 测试连接和断开逻辑

**风险等级**: 低
**预计工时**: 1-2 天

---

### 2. mock_danmaku → MockDanmakuProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/mock_danmaku/plugin.py` |
| **目标位置** | `src/providers/input/mock_danmaku_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | InputProvider |
| **代码行数** | ~200 行 |
| **核心功能** | 弹幕模拟（用于测试） |
| **依赖** | `MessageBase`, `config` |
| **新依赖** | `InputProvider`, `RawData`, `NormalizedText` |

**迁移步骤**:
1. 创建 `MockDanmakuProvider` 类，继承 `InputProvider`
2. 迁移 `connect()` 方法（启动弹幕模拟器）
3. 迁移 `disconnect()` 方法（停止模拟）
4. 迁移 `get_raw_data()` 方法（返回模拟弹幕）
5. 保留现有配置（弹幕生成间隔、内容列表）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `InputProvider` 接口（Phase 1）
- ✅ 添加: `RawData`, `NormalizedText` 数据结构（Phase 2）
- ✅ 保留: `MessageBase` 兼容性（如果需要）

**测试用例**:
- [ ] 测试弹幕模拟正确生成
- [ ] 测试弹幕生成间隔正确
- [ ] 测试弹幕内容随机性
- [ ] 测试连接和断开逻辑
- [ ] 测试自定义弹幕列表

**风险等级**: 低
**预计工时**: 1 天

---

## Phase 4: 输出类插件迁移

### 3. tts → TTSOutputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/tts/plugin.py` |
| **目标位置** | `src/providers/output/tts_output_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | OutputProvider |
| **代码行数** | ~400 行 |
| **核心功能** | 文本转语音（TTS） |
| **依赖** | `MessageBase`, `tts 服务` (通过 `get_service("tts")`) |
| **新依赖** | `OutputProvider`, `RenderParameters` |

**迁移步骤**:
1. 创建 `TTSOutputProvider` 类，继承 `OutputProvider`
2. 迁移 `send_to_client()` 方法（发送文本到 TTS 引擎）
3. 迁移 `broadcast()` 方法（如果支持广播）
4. 将 TTS 服务调用改为直接调用 TTS 引擎
5. 保留现有配置（语音引擎、语速、音调、音量）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ❌ 移除: `get_service("tts")` 调用（改为直接集成）
- ✅ 添加: `OutputProvider` 接口（Phase 1）
- ✅ 添加: `RenderParameters` 数据结构（Phase 4）
- ✅ 保留: TTS 引擎配置

**测试用例**:
- [ ] 测试文本转语音正确生成
- [ ] 测试语音参数正确应用（语速、音调、音量）
- [ ] 测试多语言支持
- [ ] 测试长文本分段处理
- [ ] 测试 TTS 引擎失败重试
- [ ] 测试静音文本处理
- [ ] 测试并发请求处理

**风险等级**: 中
**预计工时**: 2-3 天

---

### 4. subtitle → SubtitleOutputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/subtitle/plugin.py` |
| **目标位置** | `src/providers/output/subtitle_output_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | OutputProvider |
| **代码行数** | ~300 行 |
| **核心功能** | 字幕显示（通过 WebSocket 推送到前端） |
| **依赖** | `MessageBase`, `aiohttp` |
| **新依赖** | `OutputProvider`, `RenderParameters` |

**迁移步骤**:
1. 创建 `SubtitleOutputProvider` 类，继承 `OutputProvider`
2. 迁移 `send_to_client()` 方法（发送字幕数据）
3. 迁移 WebSocket 推送逻辑
4. 保留现有配置（字幕样式、位置、颜色、字体大小）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `OutputProvider` 接口（Phase 1）
- ✅ 添加: `RenderParameters` 数据结构（Phase 4）
- ✅ 保留: `aiohttp` WebSocket 逻辑

**测试用例**:
- [ ] 测试字幕正确显示
- [ ] 测试字幕样式正确应用（颜色、字体、大小）
- [ ] 测试字幕位置正确显示
- [ ] 测试长文本字幕换行
- [ ] 测试多语言字幕支持
- [ ] 测试 WebSocket 连接稳定性
- [ ] 测试字幕历史记录

**风险等级**: 中
**预计工时**: 2 天

---

### 5. sticker → StickerOutputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/sticker/plugin.py` |
| **目标位置** | `src/providers/output/sticker_output_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | OutputProvider |
| **代码行数** | ~250 行 |
| **核心功能** | 贴纸显示（通过 WebSocket 推送到前端） |
| **依赖** | `MessageBase`, `aiohttp`, `sticker 库` |
| **新依赖** | `OutputProvider`, `RenderParameters` |

**迁移步骤**:
1. 创建 `StickerOutputProvider` 类，继承 `OutputProvider`
2. 迁移 `send_to_client()` 方法（发送贴纸数据）
3. 迁移贴纸匹配逻辑（关键词 → 贴纸）
4. 迁移 WebSocket 推送逻辑
5. 保留现有配置（贴纸库、触发规则、显示时长）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `OutputProvider` 接口（Phase 1）
- ✅ 添加: `RenderParameters` 数据结构（Phase 4）
- ✅ 保留: `aiohttp` WebSocket 逻辑
- ✅ 保留: 贴纸库和匹配逻辑

**测试用例**:
- [ ] 测试贴纸正确匹配关键词
- [ ] 测试贴纸正确显示
- [ ] 测试贴纸显示时长正确
- [ ] 测试多个贴纸排队显示
- [ ] 测试未知关键词处理
- [ ] 测试贴纸库热更新
- [ ] 测试 WebSocket 连接稳定性

**风险等级**: 中
**预计工时**: 2 天

---

### 6. vtube_studio → VTSOutputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/vtube_studio/plugin.py` |
| **目标位置** | `src/providers/output/vts_output_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | OutputProvider |
| **代码行数** | ~500 行 |
| **核心功能** | VTubeStudio 虚拟形象控制 |
| **依赖** | `MessageBase`, `VTS API`, `avatar_control_manager 服务` |
| **新依赖** | `OutputProvider`, `RenderParameters` |

**迁移步骤**:
1. 创建 `VTSOutputProvider` 类，继承 `OutputProvider`
2. 迁移 `send_to_client()` 方法（发送控制指令到 VTS）
3. 迁移 WebSocket 通信逻辑（与 VTS 通信）
4. 将 `avatar_control_manager` 服务调用改为直接控制
5. 保留现有配置（WebSocket 地址、模型参数、动作映射）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ❌ 移除: `get_service("avatar_control_manager")` 调用（改为直接集成）
- ✅ 添加: `OutputProvider` 接口（Phase 1）
- ✅ 添加: `RenderParameters` 数据结构（Phase 4）
- ✅ 保留: VTS API 集成

**测试用例**:
- [ ] 测试 VTS 连接成功
- [ ] 测试模型参数更新（位置、旋转、缩放）
- [ ] 测试表情切换（开心、悲伤、惊讶等）
- [ ] 测试动作触发（挥手、点头等）
- [ ] 测试 WebSocket 断线重连
- [ ] 测试多个指令排队执行
- [ ] 测试 VTS API 错误处理

**风险等级**: 中
**预计工时**: 3-4 天

---

### 7. omni_tts → OmniTTSOutputProvider

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/omni_tts/plugin.py` |
| **目标位置** | `src/providers/output/omni_tts_output_provider.py` |
| **当前类型** | BasePlugin |
| **目标类型** | OutputProvider |
| **代码行数** | ~350 行 |
| **核心功能** | OmniTTS 备用语音输出 |
| **依赖** | `MessageBase`, `OmniTTS API` |
| **新依赖** | `OutputProvider`, `RenderParameters` |

**迁移步骤**:
1. 创建 `OmniTTSOutputProvider` 类，继承 `OutputProvider`
2. 迁移 `send_to_client()` 方法（发送文本到 OmniTTS）
3. 迁移 OmniTTS API 调用逻辑
4. 保留现有配置（API 密钥、语音模型、语速）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `OutputProvider` 接口（Phase 1）
- ✅ 添加: `RenderParameters` 数据结构（Phase 4）
- ✅ 保留: OmniTTS API 集成

**测试用例**:
- [ ] 测试 OmniTTS 连接成功
- [ ] 测试文本转语音正确生成
- [ ] 测试语音模型切换
- [ ] 测试语音参数调整（语速、音调）
- [ ] 测试 API 错误处理和重试
- [ ] 测试备用语音切换（当主 TTS 失败时）

**风险等级**: 中
**预计工时**: 2-3 天

---

## Phase 5: 游戏类插件迁移

### 8. minecraft → MinecraftExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/minecraft/plugin.py` |
| **目标位置** | `src/extensions/games/minecraft_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~400 行 |
| **核心功能** | Minecraft 服务器交互（聊天、命令、事件） |
| **依赖** | `MessageBase`, `Minecraft API` (RCON/websocket) |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `MinecraftExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（连接到 Minecraft 服务器）
3. 实现 `cleanup()` 方法（断开连接）
4. 实现 `on_event()` 方法（处理游戏事件）
5. 迁移聊天监听逻辑
6. 迁移命令执行逻辑
7. 保留现有配置（服务器地址、端口、密码、RCON 配置）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（发布 `minecraft.chat.received`, `minecraft.command.sent` 事件）
- ✅ 保留: Minecraft API 集成

**测试用例**:
- [ ] 测试 Minecraft 服务器连接
- [ ] 测试聊天消息监听
- [ ] 测试命令执行（如 `/gamemode`）
- [ ] 测试玩家事件（加入、离开、死亡）
- [ ] 测试服务器信息查询
- [ ] 测试断线重连机制
- [ ] 测试多个服务器支持（如果需要）

**风险等级**: 中
**预计工时**: 2-3 天

---

### 9. mainosaba → MainosabaExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/mainosaba/plugin.py` |
| **目标位置** | `src/extensions/games/mainosaba_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~300 行 |
| **核心功能** | Mainosaba 游戏交互 |
| **依赖** | `MessageBase`, `Mainosaba API` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `MainosabaExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（初始化 API 连接）
3. 实现 `cleanup()` 方法（清理资源）
4. 实现 `on_event()` 方法（处理游戏事件）
5. 迁移游戏状态监听
6. 迁移操作执行逻辑
7. 保留现有配置（API 地址、认证信息）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信
- ✅ 保留: Mainosaba API 集成

**测试用例**:
- [ ] 测试 Mainosaba API 连接
- [ ] 测试游戏状态查询
- [ ] 测试操作执行
- [ ] 测试事件监听
- [ ] 测试错误处理
- [ ] 测试认证流程

**风险等级**: 中
**预计工时**: 2 天

---

### 10. arknights → ArknightsExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/arknights/plugin.py` |
| **目标位置** | `src/extensions/games/arknights_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~350 行 |
| **核心功能** | 明日方舟游戏交互（账号、战斗、基建） |
| **依赖** | `MessageBase`, `Arknights API` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `ArknightsExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（登录游戏账号）
3. 实现 `cleanup()` 方法（退出登录）
4. 实现 `on_event()` 方法（处理游戏事件）
5. 迁移基建管理逻辑
6. 迁移战斗相关逻辑
7. 保留现有配置（游戏账号、服务器、代理设置）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信
- ✅ 保留: Arknights API 集成

**测试用例**:
- [ ] 测试游戏账号登录
- [ ] 测试基建数据查询
- [ ] 测试基建操作（收取赤金、订单）
- [ ] 测试战斗相关功能
- [ ] 测试每日任务检查
- [ ] 测试登录状态保持
- [ ] 测试错误处理（如账号被封）

**风险等级**: 中
**预计工时**: 2-3 天

---

### 11. warudo → WarudoExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/warudo/plugin.py` |
| **目标位置** | `src/extensions/games/warudo_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~400 行 |
| **核心功能** | Warudo 虚拟世界交互 |
| **依赖** | `MessageBase`, `Warudo API` (WebSocket) |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `WarudoExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（连接到 Warudo）
3. 实现 `cleanup()` 方法（断开连接）
4. 实现 `on_event()` 方法（处理虚拟世界事件）
5. 迁移场景控制逻辑
6. 迁移角色控制逻辑
7. 保留现有配置（WebSocket 地址、场景配置、角色配置）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信
- ✅ 保留: Warudo API 集成

**测试用例**:
- [ ] 测试 Warudo WebSocket 连接
- [ ] 测试场景切换
- [ ] 测试角色控制
- [ ] 测试事件监听（如用户进入/离开）
- [ ] 测试断线重连机制
- [ ] 测试多场景支持

**风险等级**: 中
**预计工时**: 2-3 天

---

## Phase 5: 输入类插件迁移

### 12. bili_danmaku → BiliDanmakuExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/bili_danmaku/plugin.py` |
| **目标位置** | `src/extensions/input/bili_danmaku_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 InputProvider) |
| **代码行数** | ~500 行 |
| **核心功能** | B站直播弹幕监听 |
| **依赖** | `MessageBase`, `Bilibili API` (WebSocket) |
| **新依赖** | `Extension`, `InputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `BiliDanmakuExtension` 类，实现 `Extension` 和 `InputProvider` 接口
2. 实现 `setup()` 方法（连接到 B站直播间）
3. 实现 `cleanup()` 方法（断开连接）
4. 实现 `on_event()` 方法（处理弹幕事件）
5. 实现 `connect()`, `disconnect()`, `get_raw_data()` 方法（InputProvider 接口）
6. 迁移弹幕解析逻辑
7. 迁移礼物处理逻辑
8. 保留现有配置（房间号、认证信息、过滤规则）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `InputProvider` 接口（Phase 2）
- ✅ 添加: `EventBus` 通信（发布 `bili.danmaku.received` 事件）
- ✅ 保留: Bilibili API 集成

**测试用例**:
- [ ] 测试 B站直播间连接
- [ ] 测试弹幕接收和解析
- [ ] 测试礼物接收和解析
- [ ] 测试弹幕过滤（关键词、用户）
- [ ] 测试断线重连机制
- [ ] 测试高并发弹幕处理
- [ ] 测试礼物统计功能

**风险等级**: 中
**预计工时**: 3-4 天

---

### 13. bili_live → BiliLiveExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/bili_live/plugin.py` |
| **目标位置** | `src/extensions/input/bili_live_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 InputProvider) |
| **代码行数** | ~400 行 |
| **核心功能** | B站直播推流和状态监控 |
| **依赖** | `MessageBase`, `Bilibili Live API` |
| **新依赖** | `Extension`, `InputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `BiliLiveExtension` 类，实现 `Extension` 和 `InputProvider` 接口
2. 实现 `setup()` 方法（初始化直播推流）
3. 实现 `cleanup()` 方法（停止推流）
4. 实现 `on_event()` 方法（处理直播事件）
5. 实现 `connect()`, `disconnect()`, `get_raw_data()` 方法（InputProvider 接口）
6. 迁移推流控制逻辑
7. 迁移直播状态监控逻辑
8. 保留现有配置（推流地址、码率、分辨率）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `InputProvider` 接口（Phase 2）
- ✅ 添加: `EventBus` 通信（发布 `bili.live.status_changed` 事件）
- ✅ 保留: Bilibili Live API 集成

**测试用例**:
- [ ] 测试直播推流启动
- [ ] 测试直播推流停止
- [ ] 测试直播状态监控
- [ ] 测试推流参数调整（码率、分辨率）
- [ ] 测试推流断线重连
- [ ] 测试弹幕互动统计

**风险等级**: 中
**预计工时**: 2-3 天

---

### 14. stt → STTExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/stt/plugin.py` |
| **目标位置** | `src/extensions/input/stt_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 InputProvider) |
| **代码行数** | ~350 行 |
| **核心功能** | 语音识别（Speech-to-Text） |
| **依赖** | `MessageBase`, `STT API` (如 Whisper) |
| **新依赖** | `Extension`, `InputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `STTExtension` 类，实现 `Extension` 和 `InputProvider` 接口
2. 实现 `setup()` 方法（初始化 STT 引擎）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理音频事件）
5. 实现 `connect()`, `disconnect()`, `get_raw_data()` 方法（InputProvider 接口）
6. 迁移音频录制逻辑
7. 迁移语音识别逻辑
8. 保留现有配置（识别引擎、语言、模型）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `InputProvider` 接口（Phase 2）
- ✅ 添加: `EventBus` 通信（发布 `stt.text.recognized` 事件）
- ✅ 保留: STT API 集成

**测试用例**:
- [ ] 测试音频录制
- [ ] 测试语音识别准确性
- [ ] 测试多语言识别
- [ ] 测试噪声环境识别
- [ ] 测试实时识别性能
- [ ] 测试识别结果置信度
- [ ] 测试断句和标点处理

**风险等级**: 中
**预计工时**: 3-4 天

---

### 15. yt_danmaku → YTDanmakuExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/yt_danmaku/plugin.py` |
| **目标位置** | `src/extensions/input/yt_danmaku_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 InputProvider) |
| **代码行数** | ~400 行 |
| **核心功能** | YouTube 直播弹幕监听 |
| **依赖** | `MessageBase`, `YouTube API` |
| **新依赖** | `Extension`, `InputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `YTDanmakuExtension` 类，实现 `Extension` 和 `InputProvider` 接口
2. 实现 `setup()` 方法（连接到 YouTube 直播）
3. 实现 `cleanup()` 方法（断开连接）
4. 实现 `on_event()` 方法（处理弹幕事件）
5. 实现 `connect()`, `disconnect()`, `get_raw_data()` 方法（InputProvider 接口）
6. 迁移弹幕解析逻辑
7. 迁移超聊处理逻辑
8. 保留现有配置（视频ID、认证信息、过滤规则）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `InputProvider` 接口（Phase 2）
- ✅ 添加: `EventBus` 通信（发布 `yt.danmaku.received` 事件）
- ✅ 保留: YouTube API 集成

**测试用例**:
- [ ] 测试 YouTube 直播连接
- [ ] 测试弹幕接收和解析
- [ ] 测试超聊接收和解析
- [ ] 测试弹幕过滤（关键词、用户）
- [ ] 测试断线重连机制
- [ ] 测试高并发弹幕处理

**风险等级**: 中
**预计工时**: 3-4 天

---

## Phase 5: 处理类插件迁移

### 16. llm_text_processor → LLMTextProcessorExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/llm_text_processor/plugin.py` |
| **目标位置** | `src/extensions/processing/llm_text_processor_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~300 行 |
| **核心功能** | LLM 文本处理（总结、翻译、改写） |
| **依赖** | `MessageBase`, `LLM API` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `LLMTextProcessorExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（初始化 LLM 客户端）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理文本处理请求）
5. 迁移文本总结逻辑
6. 迁移文本翻译逻辑
7. 迁移文本改写逻辑
8. 保留现有配置（模型配置、提示词模板）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（订阅 `text.process.request`，发布 `text.process.result`）
- ✅ 保留: LLM API 集成

**测试用例**:
- [ ] 测试文本总结准确性
- [ ] 测试文本翻译准确性
- [ ] 测试文本改写准确性
- [ ] 测试长文本处理（分段、超长处理）
- [ ] 测试并发处理能力
- [ ] 测试自定义提示词模板
- [ ] 测试 LLM API 错误处理

**风险等级**: 中
**预计工时**: 2-3 天

---

### 17. keyword_action → KeywordActionExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/keyword_action/plugin.py` |
| **目标位置** | `src/extensions/processing/keyword_action_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~250 行 |
| **核心功能** | 关键词触发动作 |
| **依赖** | `MessageBase` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `KeywordActionExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（加载关键词映射）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理消息，匹配关键词）
5. 迁移关键词匹配逻辑
6. 迁移动作执行逻辑（如发送消息、触发事件）
7. 保留现有配置（关键词映射、动作列表）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（订阅 `input.received`，发布 `action.triggered`）
- ✅ 保留: 关键词匹配逻辑

**测试用例**:
- [ ] 测试关键词匹配准确性
- [ ] 测试大小写不敏感匹配
- [ ] 测试正则表达式匹配
- [ ] 测试多个关键词匹配
- [ ] 测试动作执行准确性
- [ ] 测试配置热更新
- [ ] 测试冲突处理（多个关键词触发同一动作）

**风险等级**: 低
**预计工时**: 1-2 天

---

### 18. emotion_judge → EmotionJudgeExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/emotion_judge/plugin.py` |
| **目标位置** | `src/extensions/processing/emotion_judge_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~350 行 |
| **核心功能** | 情感识别（从文本或音频识别情感） |
| **依赖** | `MessageBase`, `Emotion API` (如情感分析模型) |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `EmotionJudgeExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（加载情感模型）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理文本/音频，识别情感）
5. 迁移文本情感分析逻辑
6. 迁移音频情感分析逻辑
7. 保留现有配置（情感模型、阈值）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（发布 `emotion.detected` 事件）
- ✅ 保留: 情感分析模型

**测试用例**:
- [ ] 测试文本情感识别准确性
- [ ] 测试音频情感识别准确性
- [ ] 测试情感类别覆盖（开心、悲伤、愤怒、惊讶等）
- [ ] 测试情感强度评分
- [ ] 测试多情感混合识别
- [ ] 测试实时性能
- [ ] 测试模型热更新

**风险等级**: 中
**预计工时**: 2-3 天

---

### 19. dg_lab_service → DGLabServiceExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/dg_lab_service/plugin.py` |
| **目标位置** | `src/extensions/processing/dg_lab_service_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~300 行 |
| **核心功能** | DG Lab 外部服务集成 |
| **依赖** | `MessageBase`, `DG Lab API` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `DGLabServiceExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（初始化 API 连接）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理服务请求）
5. 迁移 API 调用逻辑
6. 迁移数据转换逻辑
7. 保留现有配置（API 地址、认证信息）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（订阅 `dg.request`，发布 `dg.response`）
- ✅ 保留: DG Lab API 集成

**测试用例**:
- [ ] 测试 API 连接成功
- [ ] 测试数据请求发送
- [ ] 测试数据响应解析
- [ ] 测试错误处理（API 失败、超时）
- [ ] 测试认证流程
- [ ] 测试并发请求处理

**风险等级**: 中
**预计工时**: 2 天

---

### 20. console_chat → ConsoleChatExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/console_chat/plugin.py` |
| **目标位置** | `src/extensions/processing/console_chat_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension |
| **代码行数** | ~200 行 |
| **核心功能** | 控制台聊天显示 |
| **依赖** | `MessageBase` |
| **新依赖** | `Extension`, `EventBus` |

**迁移步骤**:
1. 创建 `ConsoleChatExtension` 类，实现 `Extension` 接口
2. 实现 `setup()` 方法（初始化显示）
3. 实现 `cleanup()` 方法（清理资源）
4. 实现 `on_event()` 方法（处理消息，显示到控制台）
5. 迁移消息格式化逻辑
6. 迁移颜色显示逻辑
7. 保留现有配置（聊天格式、颜色）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `EventBus` 通信（订阅 `output.sent`，`input.received` 等事件）
- ✅ 保留: 消息格式化逻辑

**测试用例**:
- [ ] 测试消息正确显示
- [ ] 测试颜色正确应用
- [ ] 测试多行消息处理
- [ ] 测试特殊字符转义
- [ ] 测试消息过滤（如隐藏系统消息）
- [ ] 测试配置热更新

**风险等级**: 低
**预计工时**: 1 天

---

## Phase 5: 输出类插件迁移

### 21. remote_stream → RemoteStreamExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/remote_stream/plugin.py` |
| **目标位置** | `src/extensions/output/remote_stream_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 OutputProvider) |
| **代码行数** | ~350 行 |
| **核心功能** | 远程推流（如推流到其他平台） |
| **依赖** | `MessageBase`, `Stream API` (如 FFmpeg) |
| **新依赖** | `Extension`, `OutputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `RemoteStreamExtension` 类，实现 `Extension` 和 `OutputProvider` 接口
2. 实现 `setup()` 方法（初始化推流）
3. 实现 `cleanup()` 方法（停止推流）
4. 实现 `on_event()` 方法（处理推流请求）
5. 实现 `send_to_client()`, `broadcast()` 方法（OutputProvider 接口）
6. 迁移推流控制逻辑
7. 迁移视频/音频编码逻辑
8. 保留现有配置（推流地址、编码配置、码率）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `OutputProvider` 接口（Phase 4）
- ✅ 添加: `EventBus` 通信（订阅 `output.request`，发布 `stream.status`）
- ✅ 保留: 推流 API 集成

**测试用例**:
- [ ] 测试推流启动
- [ ] 测试推流停止
- [ ] 测试视频编码质量
- [ ] 测试音频编码质量
- [ ] 测试码率调整
- [ ] 测试推流断线重连
- [ ] 测试多平台推流（如果支持）

**风险等级**: 中
**预计工时**: 3-4 天

---

### 22. vrchat → VRChatExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/vrchat/plugin.py` |
| **目标位置** | `src/extensions/output/vrchat_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 OutputProvider) |
| **代码行数** | ~400 行 |
| **核心功能** | VRChat 虚拟社交交互 |
| **依赖** | `MessageBase`, `VRChat API` (WebSocket) |
| **新依赖** | `Extension`, `OutputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `VRChatExtension` 类，实现 `Extension` 和 `OutputProvider` 接口
2. 实现 `setup()` 方法（连接到 VRChat）
3. 实现 `cleanup()` 方法（断开连接）
4. 实现 `on_event()` 方法（处理 VRChat 事件）
5. 实现 `send_to_client()`, `broadcast()` 方法（OutputProvider 接口）
6. 迁移聊天发送逻辑
7. 迁移表情和动作控制逻辑
8. 保留现有配置（房间ID、用户认证）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `OutputProvider` 接口（Phase 4）
- ✅ 添加: `EventBus` 通信（发布 `vrchat.event.received`，订阅 `vrchat.command.send`）
- ✅ 保留: VRChat API 集成

**测试用例**:
- [ ] 测试 VRChat 连接成功
- [ ] 测试聊天消息发送
- [ ] 测试表情触发
- [ ] 测试动作触发
- [ ] 测试用户事件监听（加入、离开、说话）
- [ ] 测试断线重连机制
- [ ] 测试多房间支持（如果需要）

**风险等级**: 中
**预计工时**: 3-4 天

---

### 23. read_pingmu → ReadPingmuExtension

| 属性 | 详情 |
|------|------|
| **当前位置** | `src/plugins/read_pingmu/plugin.py` |
| **目标位置** | `src/extensions/output/read_pingmu_extension.py` |
| **当前类型** | BasePlugin |
| **目标类型** | Extension (实现 OutputProvider) |
| **代码行数** | ~300 行 |
| **核心功能** | 屏幕阅读（OCR） |
| **依赖** | `MessageBase`, `OCR API` (如 Tesseract) |
| **新依赖** | `Extension`, `OutputProvider`, `EventBus` |

**迁移步骤**:
1. 创建 `ReadPingmuExtension` 类，实现 `Extension` 和 `OutputProvider` 接口
2. 实现 `setup()` 方法（初始化 OCR 引擎）
3. 实现 `cleanup()` 方法（释放资源）
4. 实现 `on_event()` 方法（处理屏幕阅读请求）
5. 实现 `send_to_client()`, `broadcast()` 方法（OutputProvider 接口）
6. 迁移屏幕截图逻辑
7. 迁移 OCR 识别逻辑
8. 保留现有配置（OCR 配置、识别语言、识别区域）

**依赖解析**:
- ❌ 移除: `BasePlugin` 依赖
- ✅ 添加: `Extension` 接口（Phase 5）
- ✅ 添加: `OutputProvider` 接口（Phase 4）
- ✅ 添加: `EventBus` 通信（订阅 `pingmu.read.request`，发布 `pingmu.read.result`）
- ✅ 保留: OCR API 集成

**测试用例**:
- [ ] 测试屏幕截图
- [ ] 测试 OCR 识别准确性
- [ ] 测试多语言识别
- [ ] 测试识别区域裁剪
- [ ] 测试实时性能
- [ ] 测试识别结果格式化
- [ ] 测试 OCR 引擎切换

**风险等级**: 中
**预计工时**: 2-3 天

---

## 迁移统计汇总

### 按类型统计

| 类型 | 数量 | 总代码行数 | 总工时 |
|------|------|-----------|--------|
| InputProvider | 6 | ~2,350 | 12-16 天 |
| OutputProvider | 5 | ~1,800 | 12-17 天 |
| Game Extension | 4 | ~1,450 | 8-11 天 |
| Input Extension | 4 | ~1,650 | 11-15 天 |
| Processing Extension | 5 | ~1,400 | 8-12 天 |
| Output Extension | 3 | ~1,050 | 8-11 天 |
| **总计** | **23** | **~8,700** | **59-82 天** |

### 按风险等级统计

| 风险等级 | 数量 | 占比 |
|---------|------|------|
| 低 | 5 | 22% |
| 中 | 18 | 78% |
| 高 | 0 | 0% |

### 按阶段统计

| 阶段 | 插件数量 | 总工时 | 占比 |
|------|---------|--------|------|
| Phase 2 (输入层) | 2 | 2-3 天 | 4% |
| Phase 4 (输出层) | 5 | 12-17 天 | 23% |
| Phase 5 (扩展系统) | 16 | 45-62 天 | 73% |

### 关键依赖关系

```
Phase 1 (基础设施)
  ├─→ Phase 2 (输入层) ─┐
  │   (2 插件)          │
  │                     │
  ├─→ Phase 3 (决策层)  ├→ Phase 4 (输出层) ─┐
  │   (无插件)          │   (5 插件)          │
  │                     │                     │
  └─────────────────────┴─→ Phase 5 (扩展系统) ├→ Phase 6 (清理)
                         (16 插件)          │
                                             │
                                             └─→ 完成
```

---

## 迁移注意事项

### 通用迁移规则

1. **保留功能完整性**
   - 所有插件功能必须完整迁移，不能减少功能
   - 配置文件格式保持兼容（如果可能）
   - API 接口保持向后兼容（如果可能）

2. **数据结构转换**
   - `MessageBase` → `RawData` / `NormalizedText` / `CanonicalMessage` / `RenderParameters`
   - 确保数据转换逻辑正确，不丢失信息
   - 添加数据验证和序列化支持

3. **错误处理**
   - 保留原有的错误处理逻辑
   - 增强错误日志记录
   - 添加重试机制（如果需要）

4. **测试覆盖**
   - 每个插件必须有对应的测试用例
   - 测试覆盖率达到 80%+
   - 包括单元测试、集成测试、端到端测试

### 特殊注意事项

1. **服务依赖迁移**
   - `tts`, `subtitle`, `sticker`, `vtube_studio` 插件依赖 `get_service()` 调用
   - 迁移后改为直接集成或通过 EventBus 通信
   - 确保服务依赖正确解析

2. **WebSocket 连接**
   - 多个插件使用 WebSocket（bili_danmaku, yt_danmaku, vrchat, warudo, subtitle, sticker）
   - 确保断线重连机制正确
   - 测试高并发连接稳定性

3. **外部 API 调用**
   - 多个插件调用外部 API（LLM、OCR、TTS、游戏 API）
   - 添加 API 调用限流
   - 添加错误重试和降级机制
   - 测试 API 失败场景

4. **配置迁移**
   - 保留现有配置文件格式（如果可能）
   - 创建配置迁移工具（如果需要修改格式）
   - 测试配置加载和热更新

5. **Git 历史保留**
   - 所有文件移动使用 `git mv` 命令
   - 保留提交历史和分支历史
   - 不使用文件系统直接移动（会丢失历史）

---

## 文档创建时间

**创建时间**: 2026-01-18
**版本**: 1.0
**状态**: 待验证
