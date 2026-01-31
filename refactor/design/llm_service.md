# LLM 服务设计

> **注意**: 本文档部分内容描述了从 LLMClientManager 迁移到 LLMService 的历史过程，这些是迁移前/历史说明，供参考。当前实现已使用 LLMService。

## 📋 概述

本文档描述 LLM 调用能力的重构设计。LLM 服务是**核心基础设施服务**，与 EventBus 同级，可被任何 Provider、模块或插件使用。

### 关键概念澄清

**LLM 服务不是 Provider**。项目中的 Provider（InputProvider、OutputProvider、DecisionProvider）是数据流架构中的节点，而 LLM 是被这些节点调用的基础设施服务。

| 概念 | 项目中的 Provider | LLM 服务 |
|-----|------------------|---------|
| **定位** | 数据流架构中的节点 | 基础设施服务 |
| **职责** | 处理特定数据类型的输入/输出/决策 | 提供 AI 能力供各模块调用 |
| **数据流** | 有明确的输入输出类型 | 无，是工具 |
| **类比** | 管道中的处理器 | EventBus、Logger 这类基础服务 |

---

## 🎯 重构目标

### 当前问题

| 问题 | 描述 |
|-----|------|
| **架构分裂** | `LLMClientManager` + `LLMClient` 与 `LocalLLMDecisionProvider` 是两套独立系统 |
| **配置分散** | LLMClientManager 读 config.toml，LocalLLMDecisionProvider 有独立配置，LLMClient 还有 global_config 回退 |
| **缺乏统一抽象** | 没有统一的 LLM 调用接口 |
| **错误处理不一致** | LocalLLMDecisionProvider 有重试/降级，LLMClient 只返回 `success: false` |
| **流式支持不统一** | LLMClient 支持流式，LocalLLMDecisionProvider 不支持 |

### 重构目标

1. **统一服务接口**：所有 LLM 调用通过 `LLMService` 统一接口
2. **后端抽象**：支持多种 LLM 提供商（OpenAI、Ollama、Anthropic 等）
3. **统一错误处理**：内置重试、超时、降级机制
4. **配置集中**：所有 LLM 配置在统一位置
5. **依赖注入**：通过构造函数注入，便于测试

---

## 🏗️ 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                   LLM 作为核心服务                              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  核心服务层 (与 EventBus 同级)                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                │
│  │  EventBus  │  │ LLMService │  │  Logger    │                │
│  └────────────┘  └────────────┘  └────────────┘                │
└─────────────────────────────────────────────────────────────────┘
         │                │                │
         ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│  使用者 (Provider / 模块 / 插件)                                │
│  ┌──────────────────┐  ┌──────────────────┐                    │
│  │ LocalLLMDecision │  │   AvatarManager  │                    │
│  │    Provider      │  │   (表情分析)      │                    │
│  └──────────────────┘  └──────────────────┘                    │
│  ┌──────────────────┐  ┌──────────────────┐                    │
│  │ EmotionJudge     │  │   Maicraft       │                    │
│  │    Plugin        │  │    Plugin        │                    │
│  └──────────────────┘  └──────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

### 组件层次

```
┌─────────────────────────────────────────────────────────────────┐
│  LLMService (统一接口)                                          │
│  - chat(), stream_chat(), call_tools(), vision()               │
│  - 配置管理、后端选择、重试/降级                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLMBackend 抽象层                                              │
│  - 定义统一的后端接口                                            │
│  - 每个后端实现特定 API 的调用逻辑                               │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  OpenAIBackend  │ │  OllamaBackend  │ │ AnthropicBackend│
│  (云端 API)     │ │  (本地模型)     │ │  (Claude API)   │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

---

## 📝 接口设计

### LLMResponse 数据类

```python
@dataclass
class LLMResponse:
    """LLM 响应结果"""
    success: bool
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    error: Optional[str] = None
    reasoning_content: Optional[str] = None  # 推理链内容（如 DeepSeek R1）
```

### LLMBackend 抽象基类

```python
class LLMBackend(ABC):
    """
    LLM 后端抽象基类
    
    不同的 LLM 提供商实现此接口：
    - OpenAIBackend: OpenAI 兼容 API（包括 SiliconFlow、DeepSeek 等）
    - OllamaBackend: 本地 Ollama
    - AnthropicBackend: Claude API
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
    
    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> LLMResponse:
        """聊天调用"""
        ...
    
    @abstractmethod
    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """流式聊天"""
        ...
    
    @abstractmethod
    async def vision(
        self,
        messages: List[Dict[str, Any]],
        images: List[Union[str, bytes]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """视觉理解"""
        ...
    
    async def cleanup(self) -> None:
        """清理资源（子类可选重写）"""
        pass
    
    def get_info(self) -> Dict[str, Any]:
        """获取后端信息"""
        return {
            "name": self.__class__.__name__,
            "model": self.config.get("model"),
            "base_url": self.config.get("base_url"),
        }
```

### LLMService 主类

```python
class LLMService:
    """
    LLM 服务管理器
    
    核心基础设施服务，与 EventBus 同级。
    
    职责：
    - 管理多个 LLM 后端配置（llm, llm_fast, vlm 等）
    - 提供统一的调用接口
    - 内置重试、超时、降级机制
    - Token 使用量统计
    
    使用示例：
        ```python
        # 在 AmaidesuCore 中初始化
        self.llm_service = LLMService()
        await self.llm_service.setup(config)
        
        # 在 Provider/模块中使用
        response = await llm_service.chat(
            prompt="你好",
            backend="llm_fast",
        )
        ```
    """
    
    def __init__(self):
        self.logger = get_logger("LLMService")
        self._backends: Dict[str, LLMBackend] = {}
        self._config: Dict[str, Any] = {}
        self._token_manager = TokenUsageManager()
        self._retry_config = RetryConfig()
    
    async def setup(self, config: Dict[str, Any]) -> None:
        """
        从配置初始化所有 LLM 后端
        
        Args:
            config: 完整配置字典，包含 [llm], [llm_fast], [vlm] 等部分
        """
        self._config = config
        
        # 支持的后端类型映射
        backend_types = {
            "openai": OpenAIBackend,
            "ollama": OllamaBackend,
            # "anthropic": AnthropicBackend,  # 未来扩展
        }
        
        # 初始化配置中定义的后端
        for name in ["llm", "llm_fast", "vlm"]:
            if name in config:
                backend_config = config[name]
                backend_type = backend_config.get("backend", "openai")
                
                if backend_type not in backend_types:
                    self.logger.warning(f"未知的后端类型: {backend_type}，使用 openai")
                    backend_type = "openai"
                
                backend_class = backend_types[backend_type]
                self._backends[name] = backend_class(backend_config)
                self.logger.info(f"已初始化 {name} 后端 ({backend_type})")
    
    # === 主要调用接口 ===
    
    async def chat(
        self,
        prompt: str,
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        聊天调用
        
        Args:
            prompt: 用户输入
            backend: 使用的后端名称（llm, llm_fast, vlm）
            system_message: 系统消息
            temperature: 温度参数
            max_tokens: 最大 token 数
        
        Returns:
            LLMResponse: 响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "chat",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    async def stream_chat(
        self,
        prompt: str,
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[str]:
        """
        流式聊天调用
        
        Args:
            prompt: 用户输入
            backend: 使用的后端名称
            system_message: 系统消息
            stop_event: 停止事件（用于中断流式输出）
        
        Yields:
            str: 增量文本内容
        """
        llm_backend = self._get_backend(backend)
        messages = self._build_messages(prompt, system_message)
        
        async for chunk in llm_backend.stream_chat(
            messages=messages,
            stop_event=stop_event,
        ):
            yield chunk
    
    async def call_tools(
        self,
        prompt: str,
        tools: List[Dict[str, Any]],
        *,
        backend: str = "llm",
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        工具调用
        
        Args:
            prompt: 用户输入
            tools: 工具定义列表（OpenAI 格式）
            backend: 使用的后端名称
            system_message: 系统消息
        
        Returns:
            LLMResponse: 包含 tool_calls 的响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "chat",
            messages=messages,
            tools=tools,
        )
    
    async def vision(
        self,
        prompt: str,
        images: List[Union[str, bytes]],
        *,
        backend: str = "vlm",
        system_message: Optional[str] = None,
    ) -> LLMResponse:
        """
        视觉理解调用
        
        Args:
            prompt: 用户输入
            images: 图片列表（URL、路径或字节）
            backend: 使用的后端名称（默认 vlm）
            system_message: 系统消息
        
        Returns:
            LLMResponse: 响应结果
        """
        messages = self._build_messages(prompt, system_message)
        return await self._call_with_retry(
            backend,
            "vision",
            messages=messages,
            images=images,
        )
    
    # === 便捷方法 ===
    
    async def simple_chat(
        self,
        prompt: str,
        backend: str = "llm",
        system_message: Optional[str] = None,
    ) -> str:
        """
        简化聊天，直接返回文本
        
        Args:
            prompt: 用户输入
            backend: 使用的后端名称
            system_message: 系统消息
        
        Returns:
            str: 响应文本，失败时返回错误信息
        """
        result = await self.chat(prompt, backend=backend, system_message=system_message)
        return result.content if result.success else f"错误: {result.error}"
    
    async def simple_vision(
        self,
        prompt: str,
        images: List[Union[str, bytes]],
        backend: str = "vlm",
    ) -> str:
        """简化视觉理解，直接返回文本"""
        result = await self.vision(prompt, images, backend=backend)
        return result.content if result.success else f"错误: {result.error}"
    
    # === 内部方法 ===
    
    def _get_backend(self, name: str) -> LLMBackend:
        """获取指定后端"""
        if name not in self._backends:
            raise ValueError(f"LLM 后端 '{name}' 未配置")
        return self._backends[name]
    
    def _build_messages(
        self,
        prompt: str,
        system_message: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """构建消息列表"""
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        return messages
    
    async def _call_with_retry(
        self,
        backend_name: str,
        method: str,
        **kwargs,
    ) -> LLMResponse:
        """带重试的调用"""
        llm_backend = self._get_backend(backend_name)
        last_error = None
        
        for attempt in range(self._retry_config.max_retries):
            try:
                method_func = getattr(llm_backend, method)
                result = await method_func(**kwargs)
                
                # 记录 token 使用量
                if result.success and result.usage:
                    self._token_manager.record_usage(
                        model_name=result.model or "unknown",
                        prompt_tokens=result.usage.get("prompt_tokens", 0),
                        completion_tokens=result.usage.get("completion_tokens", 0),
                        total_tokens=result.usage.get("total_tokens", 0),
                    )
                
                return result
                
            except Exception as e:
                last_error = e
                self.logger.warning(
                    f"LLM 调用失败 (尝试 {attempt + 1}/{self._retry_config.max_retries}): {e}"
                )
                if attempt < self._retry_config.max_retries - 1:
                    delay = min(
                        self._retry_config.base_delay * (2 ** attempt),
                        self._retry_config.max_delay,
                    )
                    await asyncio.sleep(delay)
        
        # 所有重试失败
        self.logger.error(f"所有 LLM 调用重试失败: {last_error}")
        return LLMResponse(success=False, content=None, error=str(last_error))
    
    # === 生命周期 ===
    
    async def cleanup(self) -> None:
        """清理所有后端资源"""
        for name, backend in self._backends.items():
            try:
                await backend.cleanup()
                self.logger.debug(f"已清理 {name} 后端")
            except Exception as e:
                self.logger.warning(f"清理 {name} 后端失败: {e}")
        self._backends.clear()
    
    # === 统计信息 ===
    
    def get_token_usage_summary(self) -> str:
        """获取 token 使用量摘要"""
        return self._token_manager.format_total_cost_summary()
    
    def get_backend_info(self) -> Dict[str, Any]:
        """获取所有后端信息"""
        return {name: backend.get_info() for name, backend in self._backends.items()}
```

### RetryConfig 配置类

```python
@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
```

---

## 📁 目录结构

```
src/core/
├── llm_service.py              # LLMService 主类 + LLMResponse
├── llm_backends/               # 后端实现目录
│   ├── __init__.py             # 导出所有后端
│   ├── base.py                 # LLMBackend 抽象基类
│   ├── openai_backend.py       # OpenAI 兼容 API（从 llm_request.py 演化）
│   └── ollama_backend.py       # Ollama 本地模型（可选）
├── providers/                  # 数据流 Provider（保持不变）
│   ├── input_provider.py
│   ├── output_provider.py
│   └── decision_provider.py
└── ...

# 删除的文件
src/core/llm_client_manager.py  # 删除，由 LLMService 替代
src/openai_client/              # 重构为 llm_backends/openai_backend.py
```

---

## ⚙️ 配置格式

### 新配置格式

```toml
# config.toml

[llm]
# 标准 LLM（高质量任务）
backend = "openai"              # openai | ollama | anthropic
model = "deepseek-ai/DeepSeek-V3"
api_key = "your-api-key"
base_url = "https://api.siliconflow.cn/v1/"
temperature = 0.2
max_tokens = 1024
max_retries = 3                 # 可选，默认 3
retry_delay = 1.0               # 可选，默认 1.0

[llm_fast]
# 快速 LLM（低延迟任务，如 Avatar 表情分析）
backend = "openai"
model = "Qwen/Qwen3-8B"
api_key = "your-api-key"
base_url = "https://api.siliconflow.cn/v1/"
temperature = 0.2
max_tokens = 512

[vlm]
# 视觉语言模型
backend = "openai"
model = "zai-org/GLM-4.6V"
api_key = "your-api-key"
base_url = "https://api.siliconflow.cn/v1/"

# 可选：本地 Ollama
[llm_local]
backend = "ollama"
model = "llama3"
api_base = "http://localhost:11434"
api_key = "sk-dummy"            # Ollama 不需要真实 API key
```

### 配置变化对比

| 旧配置 | 新配置 | 说明 |
|-------|--------|------|
| `[llm]` | `[llm]` | 保持不变 |
| `[llm_fast]` | `[llm_fast]` | 保持不变 |
| `[vlm]` | `[vlm]` | 保持不变 |
| - | `backend` 字段 | 新增，指定后端类型 |
| - | `max_retries` 字段 | 新增，重试次数 |
| - | `retry_delay` 字段 | 新增，重试延迟 |

---

## 🔄 使用示例

### 在 AmaidesuCore 中初始化

```python
# src/core/amaidesu_core.py
class AmaidesuCore:
    def __init__(self):
        self.event_bus = EventBus()
        self.llm_service = LLMService()  # 与 EventBus 同级
        # ...
    
    async def setup(self, config: Dict[str, Any]) -> None:
        # 初始化 LLM 服务
        await self.llm_service.setup(config)
        # ...
    
    async def cleanup(self) -> None:
        await self.llm_service.cleanup()
        # ...
```

### 在 DecisionProvider 中使用

```python
# src/core/providers/local_llm_decision_provider.py
class LocalLLMDecisionProvider(DecisionProvider):
    def __init__(self, config: dict, llm_service: LLMService):
        super().__init__(config)
        self.llm_service = llm_service
    
    async def decide(self, canonical_message: CanonicalMessage) -> MessageBase:
        # 使用 LLMService 进行决策
        response = await self.llm_service.chat(
            prompt=self._build_prompt(canonical_message),
            backend="llm",
            system_message=self.system_prompt,
        )
        
        if not response.success:
            # 降级处理
            return self._create_fallback_message(canonical_message)
        
        return self._create_message_base(response.content, canonical_message)
```

### 在 AvatarManager 中使用

```python
# src/core/avatar/avatar_manager.py
class AvatarManager:
    def __init__(self, llm_service: LLMService, event_bus: EventBus):
        self.llm_service = llm_service
        self.event_bus = event_bus
    
    async def analyze_expression(self, text: str) -> Dict[str, Any]:
        """分析文本并生成表情参数"""
        response = await self.llm_service.call_tools(
            prompt=text,
            tools=self.expression_tools,
            backend="llm_fast",  # 使用快速模型降低延迟
            system_message=self.expression_system_prompt,
        )
        
        if response.success and response.tool_calls:
            return self._parse_tool_calls(response.tool_calls)
        
        return self._default_expression()
```

### 在插件中使用

```python
# src/plugins/emotion_judge/plugin.py
class EmotionJudgePlugin:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.llm_service: Optional[LLMService] = None
    
    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        # 从 core 获取 LLMService（通过 event_bus 或直接注入）
        self.llm_service = config.get("llm_service")
        
        # 注册事件监听
        event_bus.subscribe("text.received", self._on_text_received)
        return []
    
    async def _on_text_received(self, event_data: Dict[str, Any]) -> None:
        text = event_data.get("text", "")
        
        response = await self.llm_service.chat(
            prompt=f"分析以下文本的情感: {text}",
            backend="llm_fast",
        )
        
        if response.success:
            await self.event_bus.emit("emotion.analyzed", {
                "text": text,
                "emotion": response.content,
            })
```

---

## 📋 实施步骤

### Phase 1: 创建基础结构

1. 创建 `src/core/llm_service.py`
   - 定义 `LLMResponse` 数据类
   - 定义 `RetryConfig` 配置类
   - 实现 `LLMService` 主类框架

2. 创建 `src/core/llm_backends/` 目录
   - 创建 `base.py`：`LLMBackend` 抽象基类
   - 创建 `__init__.py`：导出

### Phase 2: 实现 OpenAIBackend

1. 将 `src/openai_client/llm_request.py` 中的 `LLMClient` 重构为 `OpenAIBackend`
   - 保留核心调用逻辑
   - 适配 `LLMBackend` 接口
   - 使用 `git mv` 保留历史

2. 实现 `chat()`, `stream_chat()`, `vision()` 方法

### Phase 3: 集成到 AmaidesuCore

1. 在 `AmaidesuCore.__init__()` 中创建 `LLMService`
2. 在 `AmaidesuCore.setup()` 中初始化 `LLMService`
3. 在 `AmaidesuCore.cleanup()` 中清理 `LLMService`

### Phase 4: 迁移使用者

1. 更新 `LocalLLMDecisionProvider`：使用 `LLMService` 而非直接调用 API
2. 更新 `AvatarManager`：使用 `LLMService`
3. 更新其他使用 LLM 的模块/插件

### Phase 5: 清理

1. 删除 `src/core/llm_client_manager.py`
2. 删除或归档 `src/openai_client/` 目录中不再使用的文件
3. 更新配置文件模板

### Git 操作策略

| 操作 | Git 命令 | 说明 |
|-----|---------|------|
| 重命名文件 | `git mv old_path new_path` | 保留文件历史 |
| 修改现有文件 | 直接编辑 | 保留修改历史 |
| 删除旧文件 | `git rm path` | 记录删除历史 |
| 创建新文件 | 直接创建 | 新文件从此开始历史 |

---

## ✅ 成功标准

### 功能标准

- [ ] 所有现有 LLM 调用功能正常运行
- [ ] 支持 `llm`, `llm_fast`, `vlm` 三种配置
- [ ] 流式输出正常工作
- [ ] 工具调用（Function Calling）正常工作
- [ ] 视觉理解（VLM）正常工作
- [ ] Token 使用量统计正常

### 架构标准

- [ ] LLMService 作为核心服务与 EventBus 同级
- [ ] 所有 LLM 调用通过 `LLMService` 统一接口
- [ ] 后端可扩展（支持添加新的 LLMBackend）
- [ ] 配置集中在 `config.toml` 的 `[llm*]` 部分
- [ ] 重试和降级机制统一

### 代码质量标准

- [ ] 删除 `LLMClientManager`
- [ ] 删除重复的 LLM 调用代码
- [ ] 所有 LLM 相关配置在一处

---

## 🔗 相关文档

- [架构总览](./overview.md) - 整体架构设计
- [决策层设计](./decision_layer.md) - DecisionProvider 系统
- [核心重构设计](./core_refactoring.md) - AmaidesuCore 重构
