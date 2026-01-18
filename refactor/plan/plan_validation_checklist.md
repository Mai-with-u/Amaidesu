# 计划验证检查清单

**用途**: 在开始开发前验证6阶段重构计划的完整性和准确性，确保每个阶段都有充分的测试方案

## 核心原则

### 重构方式
- **快速完全重构**：不需要向后兼容，直接替换为新的6层架构
- **测试优先**：每个阶段都必须有完整的测试方案（自动测试 + 手动测试）
- **增量验证**：每个阶段完成后立即验证，确保功能正确

---

## 阶段完整性检查

### Phase 1: 基础设施（5-7天）

#### 功能完成检查

- [ ] Provider 接口定义完整
  - [ ] InputProvider 接口定义了 `connect()`, `disconnect()`, `get_raw_data()` 方法
  - [ ] OutputProvider 接口定义了 `send_to_client()`, `broadcast()` 方法
  - [ ] DecisionProvider 接口定义了 `decide()` 方法
  - [ ] 所有接口都有清晰的类型注解和文档字符串
  - [ ] 接口设计支持未来扩展（例如：添加新的输入/输出源）

- [ ] EventBus 增强设计
  - [ ] 错误隔离机制设计（一个监听器失败不影响其他监听器）
  - [ ] 统计功能设计（事件发布/订阅次数，失败次数）
  - [ ] 事件过滤和优先级支持设计

- [ ] DataCache 设计
  - [ ] TTL（过期时间）机制设计
  - [ ] LRU（最近最少使用）缓存策略设计
  - [ ] 线程安全设计（使用 asyncio.Lock 保护）
  - [ ] 缓存命中率统计功能设计
  - [ ] 支持不同数据类型的缓存（字符串、字典、对象）

- [ ] 配置系统迁移设计
  - [ ] 新的配置结构设计（减少 40%+ 行数）
  - [ ] 配置验证和默认值机制
  - [ ] 配置热加载支持（如果需要）
  - [ ] 配置文件格式统一为 TOML

#### 自动测试方案

- [ ] 单元测试（覆盖率 ≥ 90%）
  - [ ] InputProvider 接口测试（mock 实现）
  - [ ] OutputProvider 接口测试（mock 实现）
  - [ ] DecisionProvider 接口测试（mock 实现）
  - [ ] EventBus 错误隔离测试
  - [ ] EventBus 统计功能测试
  - [ ] DataCache TTL 测试
  - [ ] DataCache LRU 测试
  - [ ] DataCache 线程安全测试
  - [ ] 配置加载和验证测试

- [ ] 集成测试
  - [ ] EventBus 与 DataCache 集成测试
  - [ ] 配置系统集成测试

#### 手动测试方案

- [ ] 接口正确性验证
  - [ ] 检查所有接口的文档字符串是否完整
  - [ ] 检查所有接口的类型注解是否正确
  - [ ] 检查接口是否支持未来扩展

- [ ] 配置验证
  - [ ] 手动测试配置加载
  - [ ] 手动测试配置验证
  - [ ] 手动测试配置默认值

**风险评估**: 低风险（无依赖）
**验证方法**: 代码审查、单元测试、集成测试

---

### Phase 2: 输入层（7-10天）

#### 功能完成检查

- [ ] 数据结构设计
  - [ ] RawData 类设计（封装原始输入数据）
  - [ ] NormalizedText 类设计（标准化文本数据）
  - [ ] 支持多种输入源（控制台、弹幕、音频识别等）
  - [ ] 清晰的数据转换链（RawData → NormalizedText → CanonicalMessage）

- [ ] InputProviderManager 设计
  - [ ] 多源并发管理（同时从多个输入源获取数据）
  - [ ] 输入优先级机制（控制台 > 弹幕 > 其他）
  - [ ] 输入过滤和去重逻辑
  - [ ] 输入统计功能（每秒输入数量、来源分布）

- [ ] 插件迁移（2个）
  - [ ] **console_input** → ConsoleInputProvider
    - [ ] 迁移控制台输入功能
    - [ ] 迁移命令处理（`/help`, `/status`, `/reload`）
  - [ ] **mock_danmaku** → MockDanmakuProvider
    - [ ] 迁移弹幕模拟功能
    - [ ] 迁移弹幕生成逻辑

- [ ] InputLayer 集成
  - [ ] 与 Phase 1 的 Provider 接口集成
  - [ ] 与 EventBus 通信（发布 `input.received` 事件）
  - [ ] 与 DataCache 交互（缓存原始输入）
  - [ ] 完整的输入数据流（InputProvider → InputLayer → DecisionLayer）

#### 自动测试方案

- [ ] 单元测试（覆盖率 ≥ 85%）
  - [ ] RawData 测试（创建、序列化、反序列化）
  - [ ] NormalizedText 测试（创建、标准化、转换）
  - [ ] InputProviderManager 测试（加载、并发管理、优先级）
  - [ ] ConsoleInputProvider 测试（连接、断开、获取数据）
  - [ ] MockDanmakuProvider 测试（生成、配置）
  - [ ] 输入过滤和去重测试

- [ ] 集成测试
  - [ ] InputProvider 与 EventBus 集成测试
  - [ ] InputProvider 与 DataCache 集成测试
  - [ ] 多源并发输入测试
  - [ ] 完整输入数据流测试

#### 手动测试方案

- [ ] 控制台输入测试
  - [ ] 启动程序，输入文本，验证是否正确接收
  - [ ] 输入命令 `/help`，验证是否显示帮助信息
  - [ ] 输入命令 `/status`，验证是否显示系统状态
  - [ ] 输入命令 `/reload`，验证是否重新加载配置
  - [ ] 输入无效命令，验证是否显示错误信息

- [ ] 弹幕模拟测试
  - [ ] 启动程序，观察模拟弹幕是否生成
  - [ ] 验证弹幕生成间隔是否正确
  - [ ] 验证弹幕内容是否正确

- [ ] 多源并发测试
  - [ ] 同时启用控制台输入和弹幕输入
  - [ ] 验证两个输入源都能正常工作
  - [ ] 验证输入优先级是否正确

**依赖**: Phase 1（Provider 接口、EventBus、DataCache）
**风险评估**: 中风险（涉及输入功能迁移）
**验证方法**: 单元测试、集成测试、手动测试

---

### Phase 3: 决策层 + 中间层（10-14天）

#### 功能完成检查

- [ ] Layer 3: CanonicalMessage 设计
  - [ ] 清晰的消息结构（来源、内容、时间戳、元数据）
  - [ ] 支持多种消息类型（文本、图像、音频、系统消息）
  - [ ] 消息验证和序列化机制
  - [ ] 与现有 MessageBase 的转换（如果需要）

- [ ] DecisionProvider 接口和实现
  - [ ] DecisionProvider 接口设计（`decide(canonical_message)`）
  - [ ] **DecisionManager** 工厂模式设计（支持多种决策引擎切换）
  - [ ] **MaiCoreDecisionProvider** 实现
    - [ ] 迁移 WebSocket 通信逻辑（~100行）
    - [ ] 迁移 HTTP 客户端逻辑（~200行）
    - [ ] 迁移路由分发逻辑（~150行）
  - [ ] **LocalLLMDecisionProvider** 实现（可选）
    - [ ] 本地 LLM 调用接口设计
    - [ ] 提示词模板管理
    - [ ] 流式响应支持
  - [ ] **RuleEngineDecisionProvider** 实现（可选）
    - [ ] 规则配置格式设计（如 YAML/JSON）
    - [ ] 规则匹配和执行引擎
    - [ ] 支持动态规则更新

- [ ] Layer 4: 理解层设计
  - [ ] 意图识别机制（从 CanonicalMessage 提取用户意图）
  - [ ] 上下文管理（对话历史、用户状态）
  - [ ] 现有 Pipeline 系统的迁移或兼容
  - [ ] 消息处理和转换逻辑（MessageBase → Intent）

- [ ] AmaidesuCore 重构
  - [ ] 移除 WebSocket/HTTP/Router 逻辑（~500行代码）
  - [ ] 保留核心功能：EventBus、Pipeline、Context、Avatar、LLM
  - [ ] 新的决策层集成（DecisionProvider 调用）
  - [ ] 减少到约 350 行代码（54% 代码减少）

#### 自动测试方案

- [ ] 单元测试（覆盖率 ≥ 90%）
  - [ ] CanonicalMessage 测试（创建、验证、序列化）
  - [ ] DecisionProvider 接口测试（mock 实现）
  - [ ] MaiCoreDecisionProvider 测试（WebSocket、HTTP、路由）
  - [ ] LocalLLMDecisionProvider 测试（LLM 调用、提示词）
  - [ ] RuleEngineDecisionProvider 测试（规则匹配、执行）
  - [ ] DecisionManager 测试（工厂模式、切换）
  - [ ] Layer 4 意图识别测试
  - [ ] 上下文管理测试

- [ ] 集成测试
  - [ ] DecisionProvider 与 EventBus 集成测试
  - [ ] DecisionProvider 与 InputLayer 集成测试
  - [ ] DecisionProvider 与 OutputLayer 集成测试
  - [ ] Layer 3 → DecisionProvider → Layer 4 完整流程测试
  - [ ] AmaidesuCore 重构后集成测试

#### 手动测试方案

- [ ] MaiCoreDecisionProvider 测试
  - [ ] 启动程序，连接到 MaiCore
  - [ ] 发送输入，验证决策是否正确
  - [ ] 验证 WebSocket 通信是否正常
  - [ ] 验证 HTTP 通信是否正常
  - [ ] 验证路由分发是否正确

- [ ] LocalLLMDecisionProvider 测试（如果实现）
  - [ ] 配置本地 LLM
  - [ ] 发送输入，验证决策是否正确
  - [ ] 验证流式响应是否正常

- [ ] RuleEngineDecisionProvider 测试（如果实现）
  - [ ] 配置规则文件
  - [ ] 发送输入，验证规则匹配是否正确
  - [ ] 验证规则执行是否正确

- [ ] DecisionProvider 切换测试
  - [ ] 切换到 MaiCoreDecisionProvider，验证是否正常
  - [ ] 切换到 LocalLLMDecisionProvider，验证是否正常
  - [ ] 切换到 RuleEngineDecisionProvider，验证是否正常

- [ ] AmaidesuCore 重构测试
  - [ ] 启动重构后的 AmaidesuCore，验证是否能正常运行
  - [ ] 验证 EventBus 是否正常
  - [ ] 验证 Pipeline 是否正常
  - [ ] 验证 Context 是否正常
  - [ ] 验证决策层集成是否正常

**依赖**: Phase 1（接口定义）
**并行启动**: 可在 Phase 2 完成后开始（需要 Phase 2 的数据结构）
**风险评估**: 高风险（核心功能迁移）
**验证方法**: 单元测试、集成测试、手动测试、性能测试

---

### Phase 4: 输出层（10-14天）

#### 功能完成检查

- [ ] Layer 5: 表达生成层设计
  - [ ] Intent → RenderParameters 转换逻辑
  - [ ] 支持多种表达类型（文本、语音、表情、动作）
  - [ ] 输出优先级和排队机制
  - [ ] 与现有的 Pipeline 系统集成（如果需要）

- [ ] Layer 6: 渲染层接口设计
  - [ ] OutputProvider 接口的具体实现要求
  - [ ] 多目标输出支持（TTS、字幕、VTS、OmniTTS等）
  - [ ] 输出失败重试机制
  - [ ] 输出统计功能（每秒输出数量、目标分布）

- [ ] OutputProviderManager 设计
  - [ ] 多源并发管理（同时向多个输出目标发送数据）
  - [ ] 输出优先级机制（关键消息 > 普通消息）
  - [ ] 输出过滤和去重逻辑
  - [ ] 输出统计功能（成功/失败率）

- [ ] 插件迁移（5个）
  - [ ] **tts** → TTSOutputProvider
    - [ ] 迁移 TTS 功能
  - [ ] **subtitle** → SubtitleOutputProvider
    - [ ] 迁移字幕功能
  - [ ] **sticker** → StickerOutputProvider
    - [ ] 迁移贴纸功能
  - [ ] **vtube_studio** → VTSOutputProvider
    - [ ] 迁移 VTS 控制功能
  - [ ] **omni_tts** → OmniTTSOutputProvider
    - [ ] 迁移 OmniTTS 功能

- [ ] OutputLayer 集成
  - [ ] 与 Phase 1 的 Provider 接口集成
  - [ ] 与 EventBus 通信（发布 `output.sent` 事件，订阅 `render.requested` 事件）
  - [ ] 与 Phase 3 的决策层集成（接收决策结果）
  - [ ] 完整的输出数据流（DecisionLayer → ExpressionLayer → OutputLayer → OutputProvider）

#### 自动测试方案

- [ ] 单元测试（覆盖率 ≥ 85%）
  - [ ] Layer 5 表达生成测试（Intent → RenderParameters）
  - [ ] TTSOutputProvider 测试（连接、断开、发送、广播）
  - [ ] SubtitleOutputProvider 测试（连接、断开、发送、广播）
  - [ ] StickerOutputProvider 测试（连接、断开、发送、广播）
  - [ ] VTSOutputProvider 测试（连接、断开、发送、广播）
  - [ ] OmniTTSOutputProvider 测试（连接、断开、发送、广播）
  - [ ] OutputProviderManager 测试（加载、并发管理、优先级）

- [ ] 集成测试
  - [ ] OutputProvider 与 EventBus 集成测试
  - [ ] OutputProvider 与 DecisionLayer 集成测试
  - [ ] 多目标并发输出测试
  - [ ] 完整输出数据流测试

#### 手动测试方案

- [ ] TTS 输出测试
  - [ ] 发送文本输入，验证是否生成语音
  - [ ] 验证语音参数（语速、音调、音量）是否正确
  - [ ] 验证多语言支持
  - [ ] 验证长文本分段处理

- [ ] 字幕输出测试
  - [ ] 发送文本输入，验证是否显示字幕
  - [ ] 验证字幕样式（颜色、字体、大小）是否正确
  - [ ] 验证字幕位置是否正确
  - [ ] 验证长文本换行

- [ ] 贴纸输出测试
  - [ ] 发送关键词，验证是否显示贴纸
  - [ ] 验证贴纸匹配是否正确
  - [ ] 验证多个贴纸排队显示

- [ ] VTS 输出测试
  - [ ] 发送指令，验证 VTS 是否响应
  - [ ] 验证模型参数更新（位置、旋转、缩放）
  - [ ] 验证表情切换
  - [ ] 验证动作触发

- [ ] OmniTTS 输出测试
  - [ ] 发送文本输入，验证是否生成语音
  - [ ] 验证语音模型切换
  - [ ] 验证语音参数调整

- [ ] 多目标并发输出测试
  - [ ] 同时启用 TTS、字幕、贴纸、VTS
  - [ ] 验证所有输出都能正常工作
  - [ ] 验证输出优先级是否正确

**依赖**: Phase 1, 2, 3
**风险评估**: 中风险（涉及输出功能迁移）
**验证方法**: 单元测试、集成测试、手动测试

---

### Phase 5: 扩展系统（14-18天）

#### 功能完成检查

- [ ] Extension 接口定义
  - [ ] Extension 接口设计（`setup()`, `cleanup()`, `on_event()`）
  - [ ] 支持多种扩展类型（输入、输出、处理、游戏）
  - [ ] 扩展依赖管理（如某个扩展需要 TTS 服务）
  - [ ] 扩展优先级和加载顺序

- [ ] ExtensionManager 设计
  - [ ] 自动加载机制（扫描扩展目录）
  - [ ] 扩展注册和注销功能
  - [ ] 扩展依赖解析（确保依赖的扩展先加载）
  - [ ] 扩展生命周期管理（setup → activate → deactivate → cleanup）
  - [ ] 扩展配置管理（每个扩展有独立的配置文件）

- [ ] 插件迁移（剩余 16 个）

  **游戏类扩展（4个）**
  - [ ] **minecraft** → MinecraftExtension
  - [ ] **mainosaba** → MainosabaExtension
  - [ ] **arknights** → ArknightsExtension
  - [ ] **warudo** → WarudoExtension

  **输入类扩展（4个）**
  - [ ] **bili_danmaku** → BiliDanmakuExtension
  - [ ] **bili_live** → BiliLiveExtension
  - [ ] **stt** → STTExtension
  - [ ] **yt_danmaku** → YTDanmakuExtension

  **处理类扩展（5个）**
  - [ ] **llm_text_processor** → LLMTextProcessorExtension
  - [ ] **keyword_action** → KeywordActionExtension
  - [ ] **emotion_judge** → EmotionJudgeExtension
  - [ ] **dg_lab_service** → DGLabServiceExtension
  - [ ] **console_chat** → ConsoleChatExtension

  **输出类扩展（3个）**
  - [ ] **remote_stream** → RemoteStreamExtension
  - [ ] **vrchat** → VRChatExtension
  - [ ] **read_pingmu** → ReadPingmuExtension

- [ ] Extension 系统集成
  - [ ] 与 Phase 1 的 Provider 接口集成（某些 Extension 可能实现 Provider 接口）
  - [ ] 与 EventBus 通信（发布/订阅扩展事件）
  - [ ] 与 Phase 3 的决策层集成（扩展可以影响决策）
  - [ ] 完整的扩展生命周期（加载 → 激活 → 运行 → 停用 → 卸载）

#### 自动测试方案

- [ ] 单元测试（覆盖率 ≥ 80%）
  - [ ] Extension 接口测试（mock 实现）
  - [ ] ExtensionManager 测试（加载、注册、注销、依赖解析）
  - [ ] 所有 16 个 Extension 的单元测试

- [ ] 集成测试
  - [ ] Extension 与 EventBus 集成测试
  - [ ] Extension 与 DecisionLayer 集成测试
  - [ ] Extension 与 OutputLayer 集成测试
  - [ ] 扩展依赖解析测试
  - [ ] 扩展生命周期测试

#### 手动测试方案

- [ ] 游戏类扩展测试
  - [ ] Minecraft: 连接到服务器，验证游戏交互是否正常
  - [ ] Mainosaba: 初始化 API，验证游戏交互是否正常
  - [ ] Arknights: 登录游戏，验证游戏交互是否正常
  - [ ] Warudo: 连接到虚拟世界，验证交互是否正常

- [ ] 输入类扩展测试
  - [ ] BiliDanmaku: 连接到 B站直播间，验证弹幕接收是否正常
  - [ ] BiliLive: 启动直播推流，验证推流是否正常
  - [ ] STT: 启动语音识别，验证识别是否正常
  - [ ] YTDanmaku: 连接到 YouTube 直播，验证弹幕接收是否正常

- [ ] 处理类扩展测试
  - [ ] LLMTextProcessor: 发送文本，验证处理是否正常
  - [ ] KeywordAction: 发送关键词，验证动作是否触发
  - [ ] EmotionJudge: 发送文本/音频，验证情感识别是否正常
  - [ ] DGLabService: 调用 API，验证服务集成是否正常
  - [ ] ConsoleChat: 发送消息，验证显示是否正常

- [ ] 输出类扩展测试
  - [ ] RemoteStream: 启动推流，验证推流是否正常
  - [ ] VRChat: 连接到 VRChat，验证交互是否正常
  - [ ] ReadPingmu: 启动屏幕阅读，验证识别是否正常

- [ ] 扩展系统测试
  - [ ] 验证扩展自动加载
  - [ ] 验证扩展依赖解析
  - [ ] 验证扩展生命周期
  - [ ] 验证扩展热加载（如果支持）

**依赖**: Phase 2, 4
**风险评估**: 中风险（涉及大量插件迁移）
**验证方法**: 单元测试、集成测试、手动测试、回归测试

---

### Phase 6: 清理和测试（7-10天）

#### 功能完成检查

- [ ] AmaidesuCore 简化
  - [ ] 最终代码行数检查（目标约 350 行）
  - [ ] 移除所有废弃的功能和代码
  - [ ] 清理未使用的导入和变量
  - [ ] 优化代码结构和可读性

- [ ] 旧代码清理
  - [ ] 删除旧的插件文件（已迁移到 Provider/Extension）
  - [ ] 删除未使用的工具函数和类
  - [ ] 清理测试代码中的废弃引用
  - [ ] 清理文档中的过时内容

- [ ] 文档完善
  - [ ] 更新 README 和用户文档
  - [ ] 更新 API 文档（Provider/Extension 接口）
  - [ ] 更新配置文档（新的配置结构）
  - [ ] 更新插件开发指南（Provider/Extension 开发）

- [ ] 配置迁移工具
  - [ ] 创建旧配置 → 新配置的迁移脚本
  - [ ] 验证配置迁移的准确性
  - [ ] 创建配置迁移文档
  - [ ] 测试配置迁移工具的边界情况

#### 自动测试方案

- [ ] 端到端测试
  - [ ] 完整的 6 层数据流测试
    - [ ] 输入层：控制台输入 → 弹幕输入 → 语音识别
    - [ ] 决策层：MaiCore决策 → 本地LLM决策 → 规则引擎决策
    - [ ] 输出层：TTS输出 → 字幕输出 → 贴纸输出 → VTS输出
  - [ ] 测试所有 23 个插件的功能
  - [ ] 测试插件之间的交互（如：输入 → 决策 → 输出）
  - [ ] 测试错误恢复和容错能力

- [ ] 性能测试
  - [ ] 核心功能响应时间测试（目标：不增加）
  - [ ] 并发处理能力测试（多输入源、多输出目标）
  - [ ] 内存使用测试（确保没有内存泄漏）
  - [ ] 缓存命中率测试（DataCache 有效性验证）

- [ ] 回归测试
  - [ ] 运行所有单元测试
  - [ ] 运行所有集成测试
  - [ ] 运行所有端到端测试
  - [ ] 测试覆盖率验证（≥ 80%）

#### 手动测试方案

- [ ] 完整系统测试
  - [ ] 启动系统，验证是否能正常运行
  - [ ] 测试所有输入源（控制台、弹幕、游戏、语音）
  - [ ] 测试所有决策引擎（MaiCore、本地LLM、规则引擎）
  - [ ] 测试所有输出目标（TTS、字幕、贴纸、VTS、OmniTTS）
  - [ ] 测试所有扩展（游戏、输入、处理、输出）

- [ ] 配置迁移测试
  - [ ] 使用旧配置运行迁移工具
  - [ ] 验证新配置是否正确
  - [ ] 使用新配置启动系统，验证是否能正常运行

- [ ] 用户验收测试
  - [ ] 邀请实际用户测试系统
  - [ ] 收集用户反馈
  - [ ] 修复发现的问题

**依赖**: 所有前序阶段
**风险评估**: 低风险（主要是测试和清理）
**验证方法**: 端到端测试、性能测试、用户验收测试、回归测试

---

## 全局验证项

### 插件分配完整性

- [ ] 所有 23 个插件都已分配到具体阶段
- [ ] 每个插件的迁移路径清晰（插件 → Provider/Extension）
- [ ] 没有遗漏的插件或功能

### 测试覆盖完整性

- [ ] 每个阶段都有自动测试方案
- [ ] 每个阶段都有手动测试方案
- [ ] 测试覆盖率要求明确
  - Phase 1: ≥ 90%
  - Phase 2: ≥ 85%
  - Phase 3: ≥ 90%
  - Phase 4: ≥ 85%
  - Phase 5: ≥ 80%
  - Phase 6: ≥ 80%

### 依赖关系验证

- [ ] 阶段之间的依赖关系正确
- [ ] 没有循环依赖
- [ ] 并行任务可以安全执行
- [ ] 依赖路径清晰（Phase 1 → Phase 2 → Phase 3 → Phase 4/5 → Phase 6）

### 风险评估验证

- [ ] 每个阶段的风险评估合理
- [ ] 高风险阶段有充分的测试策略
- [ ] 低风险阶段可以快速完成
- [ ] 风险缓解措施充分

### 时间估算验证

- [ ] 每个阶段的时间估算合理
- [ ] 总时间在 53-73 天范围内
- [ ] 并行任务的时间考虑了资源限制
- [ ] 缓冲时间充足

### 成功标准验证

- [ ] 配置文件行数减少 40%+
- [ ] 核心功能响应时间不增加
- [ ] 代码重复减少 30%+
- [ ] 服务注册调用减少 80%+
- [ ] EventBus 事件调用覆盖率达到 90%+

### Git 历史保留

- [ ] 所有文件移动使用 `git mv` 命令
- [ ] 没有使用文件系统直接移动（会丢失历史）
- [ ] Git 历史完整性验证

---

## 开发前检查清单

在开始 Phase 1 开发前，确保：

- [ ] 所有 6 个阶段的详细计划已创建并审查
- [ ] 插件分配完整且准确
- [ ] 依赖关系无冲突
- [ ] 风险评估合理
- [ ] 时间估算可信
- [ ] 成功标准清晰
- [ ] 测试方案完整（自动测试 + 手动测试）
- [ ] 测试覆盖率要求明确
- [ ] Git 工作流程确定
- [ ] 测试基础设施就绪
- [ ] 持续集成配置完成
- [ ] 代码审查流程确定

---

## 文档创建时间

**创建时间**: 2026-01-18
**版本**: 2.0（移除向后兼容性，强调测试）
**状态**: 待验证
