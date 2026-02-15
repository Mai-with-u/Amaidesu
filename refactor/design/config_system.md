# 配置系统变化

本文档详细对比重构前后的配置系统设计，解释新架构如何简化配置管理。

## 配置系统总览

### 旧架构：多层配置合并

```
┌─────────────────────────────────────────────────────────────────┐
│                         config.toml                              │
│  [inner], [general], [maicore], [plugins], [pipelines], [llm]   │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      config/config.py                            │
│                    GlobalConfig (Pydantic)                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   插件级 config.toml (可选)                       │
│                 每个插件目录下可能有独立配置                       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    配置合并逻辑                                   │
│         merge_component_configs(main_config, plugin_config)      │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 全局配置 + 插件级配置
- 复杂的配置合并逻辑
- 配置验证分散

### 新架构：统一的 Provider 配置

```
┌─────────────────────────────────────────────────────────────────┐
│                         config.toml                              │
│                                                                  │
│  [providers.input]      # 输入 Provider 配置                    │
│  [providers.decision]   # 决策 Provider 配置                    │
│  [providers.output]     # 输出 Provider 配置                    │
│  [pipelines]            # 管道配置                               │
│  [llm]                  # LLM 配置                               │
│  [context]              # 上下文配置                             │
│                                                                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    modules/config/                               │
│                    ConfigManager (统一管理)                       │
│                                                                  │
│  - load_config()       # 加载配置                                │
│  - get_provider_config() # 获取 Provider 配置                   │
│  - validate()          # 验证配置                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**特点**：
- 统一的配置文件
- 配置与 Provider 类型绑定
- 简化的配置加载

## 配置文件结构对比

### 旧架构配置结构

```toml
# config-template.toml

[inner]
version = "1.0.0"

[general]
platform_id = "amaidesu_default"

[maicore]
host = "127.0.0.1"
port = 8000

[plugins]
enabled = ["bili_danmaku", "tts", "vts", "subtitle"]

[plugins.bili_danmaku]
room_id = 123456
# ... 插件特定配置

[plugins.tts]
voice = "zh-CN-XiaoxiaoNeural"
# ... TTS 特定配置

[plugins.vts]
host = "127.0.0.1"
port = 8001
# ... VTS 特定配置

[pipelines]
command_router = { priority = 100 }
similar_message_filter = { priority = 200 }

[llm]
model = "gpt-4o-mini"
api_key = ""
base_url = ""
temperature = 0.2
max_tokens = 1024

[llm_fast]
# ... 快速 LLM 配置

[vlm]
# ... 视觉模型配置
```

**问题**：
- 所有插件配置混在 `[plugins]` 下
- 无类型区分（输入/输出/服务）
- 配置层级深

### 新架构配置结构

```toml
# config-template.toml

[general]
platform_id = "amaidesu_default"

# ==================== Input Providers ====================
[providers.input]
enabled_inputs = ["console_input", "bili_danmaku"]

[providers.input.console_input]
# 控制台输入无需额外配置

[providers.input.bili_danmaku]
room_id = 123456
# ... 弹幕配置

# ==================== Decision Providers ====================
[providers.decision]
active_provider = "maicore"  # 只能有一个活跃的决策 Provider

[providers.decision.maicore]
host = "127.0.0.1"
port = 8000

[providers.decision.llm]
model = "gpt-4o-mini"
# ... LLM 配置

# ==================== Output Providers ====================
[providers.output]
enabled_outputs = ["edge_tts", "subtitle", "vts"]

[providers.output.edge_tts]
voice = "zh-CN-XiaoxiaoNeural"

[providers.output.subtitle]
font_size = 24

[providers.output.vts]
host = "127.0.0.1"
port = 8001

# ==================== Pipelines ====================
[pipelines.rate_limit]
max_messages_per_minute = 30

[pipelines.similar_filter]
similarity_threshold = 0.8

# ==================== Shared Services ====================
[llm]
model = "gpt-4o-mini"
api_key = "${LLM_API_KEY}"  # 支持环境变量
base_url = ""
temperature = 0.2
max_tokens = 1024

[context]
max_history_length = 50
```

**改进**：
- 按 Provider 类型分组
- 配置结构清晰
- 决策 Provider 明确"只选一个"

## 配置加载对比

### 旧架构配置加载

```python
# config/config.py
class GlobalConfig:
    inner: InnerConfig
    general: GeneralConfig
    maicore: MaiCoreConfig
    plugins: Dict[str, Any]
    pipelines: Dict[str, Any]
    llm: LLMConfig
    llm_fast: LLMConfigFast
    vlm: VLMConfig

def load_config():
    # 加载主配置
    config_data = toml.load("config.toml")
    global_config = GlobalConfig(**config_data)
    return global_config

# plugin_manager.py
async def load_plugins(self, plugin_dir: str):
    for plugin_name in enabled_plugins:
        # 加载插件目录下的 config.toml
        plugin_config = load_component_specific_config(plugin_dir, plugin_name)

        # 合并主配置和插件配置
        final_config = merge_component_configs(
            plugin_config,
            self.global_plugin_config.get(plugin_name, {}),
        )

        plugin = PluginClass(self.core, final_config)
```

**问题**：
- 配置分散在多个地方
- 合并逻辑复杂
- 插件配置可能覆盖主配置

### 新架构配置加载

```python
# modules/config/manager.py
class ConfigManager:
    def __init__(self, config_path: str):
        self._config = self._load_config(config_path)

    def _load_config(self, path: str) -> Dict[str, Any]:
        """加载配置文件"""
        if not os.path.exists(path):
            # 从模板复制
            shutil.copy("config-template.toml", path)
        return toml.load(path)

    def get_provider_config(self, domain: str, provider_name: str) -> Dict[str, Any]:
        """获取 Provider 配置"""
        domain_config = self._config.get("providers", {}).get(domain, {})
        return domain_config.get(provider_name, {})

    def get_enabled_providers(self, domain: str) -> List[str]:
        """获取启用的 Provider 列表"""
        domain_config = self._config.get("providers", {}).get(domain, {})

        if domain == "decision":
            # 决策域只返回 active_provider
            return [domain_config.get("active_provider")]
        else:
            # 输入/输出域返回 enabled 列表
            return domain_config.get(f"enabled_{domain}s", [])

# provider_manager.py
async def load_providers(self):
    enabled = self._config_manager.get_enabled_providers("input")

    for provider_name in enabled:
        config = self._config_manager.get_provider_config("input", provider_name)
        provider = ProviderClass(config=config, context=self._context)
```

**改进**：
- 统一的配置管理
- 简化的加载逻辑
- 类型安全的配置获取

## 配置验证对比

### 旧架构配置验证

```python
# 使用 Pydantic 进行验证
class LLMConfig(BaseModel):
    model: str = "gpt-4o-mini"
    api_key: str = None
    base_url: Optional[str] = None
    temperature: float = 0.2
    max_tokens: int = 1024

    @validator("api_key")
    def validate_api_key(cls, v):
        if not v:
            raise ValueError("API Key is required")
        return v

# 验证分散在各个配置类中
```

**问题**：
- 验证逻辑分散
- 插件配置验证在运行时
- 错误信息不统一

### 新架构配置验证

新架构使用 `ConfigService` 统一管理配置，支持可选的 Pydantic Schema 验证：

```python
# modules/config/service.py
class ConfigService:
    """统一的配置管理服务"""

    def get_provider_config_with_defaults(
        self,
        provider_name: str,
        provider_layer: Literal["input", "output", "decision"],
        schema_class: Optional[type] = None,
    ) -> Dict[str, Any]:
        """
        获取Provider配置（二级合并）

        配置合并顺序（后者覆盖前者）:
        1. Schema默认值（从Pydantic Schema类获取）
        2. 主配置覆盖 (config.toml中的[providers.*.{provider_name}])
        """
        # 步骤1: 获取Schema默认值
        result = self._get_schema_defaults(schema_class, provider_name)

        # 步骤2: 应用主配置覆盖
        global_override = self.load_global_overrides(config_section, provider_name)
        if global_override:
            result = deep_merge_configs(result, global_override)

        # 步骤3: Schema验证（如果提供）
        if schema_class is not None:
            validated = schema_class(**result)
            result = validated.model_dump(exclude_unset=False)

        return result

# 具体 Provider 的配置 Schema（可选）
# modules/config/schemas/input_providers.py
class BiliDanmakuConfig(BaseModel):
    """B站弹幕配置"""
    room_id: int
    cookie: Optional[str] = None

    @validator("room_id")
    def validate_room_id(cls, v):
        if v <= 0:
            raise ValueError("room_id must be positive")
        return v

# 使用示例
config = config_service.get_provider_config_with_defaults(
    "bili_danmaku_official", "input", BiliDanmakuConfig
)
```

**改进**：
- 统一的 ConfigService 管理所有配置
- 可选的 Pydantic Schema 验证
- 二级配置合并（Schema默认值 → 主配置覆盖）
- 配置验证在加载时进行

## 环境变量支持

### 旧架构

```python
# 部分支持环境变量
api_key = os.environ.get("LLM_API_KEY") or config.llm.api_key
```

### 新架构

```python
# 支持在配置文件中使用 ${VAR} 语法
[llm]
api_key = "${LLM_API_KEY}"
base_url = "${LLM_BASE_URL:-https://api.openai.com}"  # 带默认值

# config/manager.py
def _expand_env_vars(self, config: Dict) -> Dict:
    """展开环境变量"""
    def expand(value):
        if isinstance(value, str):
            # 匹配 ${VAR} 或 ${VAR:-default}
            pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'
            def replace(match):
                var_name = match.group(1)
                default = match.group(2)
                return os.environ.get(var_name, default or "")
            return re.sub(pattern, replace, value)
        elif isinstance(value, dict):
            return {k: expand(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [expand(v) for v in value]
        return value

    return expand(config)
```

## 迁移建议

### 配置文件迁移

```toml
# 旧配置
[plugins]
enabled = ["bili_danmaku", "tts"]

[plugins.bili_danmaku]
room_id = 123456

# 新配置
[providers.input]
enabled_inputs = ["bili_danmaku"]

[providers.input.bili_danmaku]
room_id = 123456
```

### 配置加载迁移

```python
# 旧代码
config = load_config()
plugin_config = config.plugins.get("my_plugin", {})

# 新代码
config_manager = ConfigManager("config.toml")
provider_config = config_manager.get_provider_config("input", "my_provider")
```

### 添加新 Provider 配置

1. 在 `config-template.toml` 中添加默认配置：

```toml
[providers.input.my_new_provider]
enabled = true
option1 = "value1"
option2 = 123
```

2. 创建配置 Schema（可选）：

```python
class MyNewProviderConfig(InputProviderConfig):
    option1: str = "value1"
    option2: int = 123
```

3. 在 Provider 中使用：

```python
from src.modules.di import ProviderContext

class MyNewProvider(InputProvider):
    def __init__(self, config: dict, context: ProviderContext):
        super().__init__(config, context)
        self.option1 = config.get("option1", "value1")
        self.option2 = config.get("option2", 123)
```

---

*最后更新：2026-02-15*
