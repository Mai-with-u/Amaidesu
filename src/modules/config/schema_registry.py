"""
配置 Schema 注册表

提供配置字段的元数据定义，用于生成动态表单。
"""

from typing import Any, Dict, List, Optional

from src.modules.logging import get_logger

logger = get_logger("SchemaRegistry")


class ConfigFieldDefinition:
    """配置字段定义"""

    def __init__(
        self,
        key: str,
        label: str,
        field_type: str,
        description: str = "",
        default: Any = None,
        required: bool = False,
        sensitive: bool = False,
        validation: Optional[Dict[str, Any]] = None,
        options: Optional[List[str]] = None,
        properties: Optional[Dict[str, "ConfigFieldDefinition"]] = None,
        items: Optional["ConfigFieldDefinition"] = None,
    ):
        self.key = key
        self.label = label
        self.field_type = field_type
        self.description = description
        self.default = default
        self.required = required
        self.sensitive = sensitive
        self.validation = validation or {}
        self.options = options
        self.properties = properties
        self.items = items

    def to_dict(self, current_value: Any = None) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "key": self.key,
            "label": self.label,
            "type": self.field_type,
            "description": self.description,
            "default": self.default,
            "value": current_value if current_value is not None else self.default,
            "required": self.required,
            "sensitive": self.sensitive,
        }

        if self.validation:
            result["validation"] = self.validation

        if self.options:
            if not result.get("validation"):
                result["validation"] = {}
            result["validation"]["options"] = self.options

        if self.properties:
            result["properties"] = {k: v.to_dict() for k, v in self.properties.items()}

        if self.items:
            result["items"] = self.items.to_dict()

        return result


class ConfigGroupDefinition:
    """配置分组定义"""

    def __init__(
        self,
        key: str,
        label: str,
        description: str = "",
        icon: str = "",
        order: int = 0,
    ):
        self.key = key
        self.label = label
        self.description = description
        self.icon = icon
        self.order = order
        self.fields: List[ConfigFieldDefinition] = []

    def add_field(self, field: ConfigFieldDefinition) -> "ConfigGroupDefinition":
        """添加字段"""
        self.fields.append(field)
        return self

    def to_dict(self, current_values: Dict[str, Any] = None) -> Dict[str, Any]:
        """转换为字典格式"""
        current_values = current_values or {}
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "order": self.order,
            "fields": [f.to_dict(current_values.get(f.key)) for f in self.fields],
        }


class ConfigSchemaRegistry:
    """配置 Schema 注册表"""

    _instance: Optional["ConfigSchemaRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._groups: Dict[str, ConfigGroupDefinition] = {}
        self._field_map: Dict[str, ConfigFieldDefinition] = {}
        self._initialize_default_schemas()

    def _initialize_default_schemas(self):
        """初始化默认的配置 Schema"""
        # ========== 通用配置 ==========
        general_group = ConfigGroupDefinition(
            key="general",
            label="通用配置",
            description="Amaidesu 核心配置",
            icon="Setting",
            order=10,
        )
        general_group.add_field(
            ConfigFieldDefinition(
                key="general.platform_id",
                label="平台标识符",
                field_type="string",
                description="Amaidesu 在 MaiCore 中注册的平台标识符",
                default="amaidesu",
                required=True,
            )
        )
        self.register_group(general_group)

        # ========== 人设配置 ==========
        persona_group = ConfigGroupDefinition(
            key="persona",
            label="VTuber 人设",
            description="VTuber 的性格和说话风格配置",
            icon="User",
            order=20,
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.bot_name",
                label="VTuber 名字",
                field_type="string",
                description="VTuber 的名字",
                default="麦麦",
                required=True,
            )
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.personality",
                label="性格描述",
                field_type="string",
                description="性格描述（50字以内）",
                default="活泼开朗，有些调皮，喜欢和观众互动",
                validation={"max_length": 50},
            )
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.style_constraints",
                label="说话风格",
                field_type="string",
                description="指导 LLM 生成回复的风格",
                default="口语化，使用网络流行语，避免机械式回复，适当使用emoji",
            )
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.user_name",
                label="用户称呼",
                field_type="string",
                description="对观众的称呼",
                default="大家",
            )
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.max_response_length",
                label="回复长度限制",
                field_type="integer",
                description="回复的最大字数",
                default=50,
                validation={"min": 10, "max": 500},
            )
        )
        persona_group.add_field(
            ConfigFieldDefinition(
                key="persona.emotion_intensity",
                label="情感表达强度",
                field_type="integer",
                description="情感表达强度 (1-10, 1=平淡, 10=丰富)",
                default=7,
                validation={"min": 1, "max": 10},
            )
        )
        self.register_group(persona_group)

        # ========== LLM 配置 ==========
        llm_group = ConfigGroupDefinition(
            key="llm",
            label="LLM 配置",
            description="大语言模型配置",
            icon="ChatDotRound",
            order=30,
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.client",
                label="LLM 客户端",
                field_type="select",
                description="LLM 客户端类型",
                default="openai",
                options=["openai"],
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.model",
                label="模型名称",
                field_type="string",
                description="使用的模型名称",
                default="gpt-4",
                required=True,
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.temperature",
                label="温度参数",
                field_type="float",
                description="生成温度 (0.0-2.0)",
                default=0.2,
                validation={"min": 0.0, "max": 2.0},
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.api_key",
                label="API Key",
                field_type="string",
                description="API 密钥（可使用环境变量）",
                default="your-api-key",
                sensitive=True,
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.base_url",
                label="API 端点",
                field_type="string",
                description="自定义 API 端点",
                default="https://api.openai.com/v1",
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.max_tokens",
                label="最大 Token 数",
                field_type="integer",
                description="生成的最大 Token 数",
                default=1024,
                validation={"min": 1, "max": 32000},
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.max_retries",
                label="最大重试次数",
                field_type="integer",
                description="请求失败时的最大重试次数",
                default=3,
                validation={"min": 0, "max": 10},
            )
        )
        llm_group.add_field(
            ConfigFieldDefinition(
                key="llm.retry_delay",
                label="重试延迟",
                field_type="float",
                description="重试间隔时间（秒）",
                default=1.0,
                validation={"min": 0.0, "max": 60.0},
            )
        )
        self.register_group(llm_group)

        # ========== MaiCore 配置 ==========
        maicore_group = ConfigGroupDefinition(
            key="maicore",
            label="MaiCore 连接",
            description="MaiCore WebSocket 服务器配置",
            icon="Connection",
            order=40,
        )
        maicore_group.add_field(
            ConfigFieldDefinition(
                key="maicore.host",
                label="服务器地址",
                field_type="string",
                description="MaiCore WebSocket 服务器地址",
                default="127.0.0.1",
            )
        )
        maicore_group.add_field(
            ConfigFieldDefinition(
                key="maicore.port",
                label="服务器端口",
                field_type="integer",
                description="MaiCore WebSocket 服务器端口",
                default=8000,
                validation={"min": 1, "max": 65535},
            )
        )
        maicore_group.add_field(
            ConfigFieldDefinition(
                key="maicore.token",
                label="认证 Token",
                field_type="string",
                description="如果 MaiCore 需要认证",
                default="",
                sensitive=True,
            )
        )
        self.register_group(maicore_group)

        # ========== 日志配置 ==========
        logging_group = ConfigGroupDefinition(
            key="logging",
            label="日志配置",
            description="日志系统配置",
            icon="Document",
            order=50,
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.enabled",
                label="启用文件日志",
                field_type="boolean",
                description="是否启用文件日志",
                default=True,
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.format",
                label="日志格式",
                field_type="select",
                description="日志格式",
                default="jsonl",
                options=["jsonl", "text"],
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.directory",
                label="日志目录",
                field_type="string",
                description="日志文件目录（相对路径）",
                default="logs",
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.level",
                label="日志级别",
                field_type="select",
                description="最小日志级别",
                default="INFO",
                options=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.rotation",
                label="轮转触发条件",
                field_type="string",
                description="文件轮转触发条件",
                default="10 MB",
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.retention",
                label="保留时间",
                field_type="string",
                description="日志保留时间",
                default="7 days",
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.compression",
                label="压缩格式",
                field_type="select",
                description="日志压缩格式",
                default="zip",
                options=["zip", "gz", "tar", ""],
            )
        )
        logging_group.add_field(
            ConfigFieldDefinition(
                key="logging.split_by_session",
                label="按会话分割",
                field_type="boolean",
                description="是否按会话分割日志文件",
                default=False,
            )
        )
        self.register_group(logging_group)

        # ========== 上下文配置 ==========
        context_group = ConfigGroupDefinition(
            key="context",
            label="上下文管理",
            description="对话上下文管理配置",
            icon="ChatLineRound",
            order=60,
        )
        context_group.add_field(
            ConfigFieldDefinition(
                key="context.storage_type",
                label="存储类型",
                field_type="select",
                description="存储类型",
                default="memory",
                options=["memory", "file"],
            )
        )
        context_group.add_field(
            ConfigFieldDefinition(
                key="context.max_messages_per_session",
                label="最大消息数",
                field_type="integer",
                description="每个会话保留的最大消息数",
                default=50,
                validation={"min": 10, "max": 1000},
            )
        )
        context_group.add_field(
            ConfigFieldDefinition(
                key="context.max_sessions",
                label="最大会话数",
                field_type="integer",
                description="最大会话数",
                default=100,
                validation={"min": 1, "max": 10000},
            )
        )
        context_group.add_field(
            ConfigFieldDefinition(
                key="context.session_timeout_seconds",
                label="会话超时时间",
                field_type="integer",
                description="会话超时时间（秒）",
                default=3600,
                validation={"min": 60, "max": 86400},
            )
        )
        context_group.add_field(
            ConfigFieldDefinition(
                key="context.enable_persistence",
                label="启用持久化",
                field_type="boolean",
                description="启用持久化（暂未实现）",
                default=False,
            )
        )
        self.register_group(context_group)

        # ========== Dashboard 配置 ==========
        dashboard_group = ConfigGroupDefinition(
            key="dashboard",
            label="Dashboard 配置",
            description="Web Dashboard 配置",
            icon="Monitor",
            order=70,
        )
        dashboard_group.add_field(
            ConfigFieldDefinition(
                key="dashboard.enabled",
                label="启用 Dashboard",
                field_type="boolean",
                description="是否启用 Dashboard",
                default=True,
            )
        )
        dashboard_group.add_field(
            ConfigFieldDefinition(
                key="dashboard.host",
                label="监听地址",
                field_type="string",
                description="Dashboard 监听地址",
                default="127.0.0.1",
            )
        )
        dashboard_group.add_field(
            ConfigFieldDefinition(
                key="dashboard.port",
                label="监听端口",
                field_type="integer",
                description="Dashboard 监听端口",
                default=60214,
                validation={"min": 1, "max": 65535},
            )
        )
        dashboard_group.add_field(
            ConfigFieldDefinition(
                key="dashboard.max_history_messages",
                label="最大历史消息数",
                field_type="integer",
                description="WebSocket 推送的最大历史消息数",
                default=1000,
                validation={"min": 100, "max": 10000},
            )
        )
        dashboard_group.add_field(
            ConfigFieldDefinition(
                key="dashboard.websocket_heartbeat",
                label="WebSocket 心跳间隔",
                field_type="integer",
                description="WebSocket 心跳间隔（秒）",
                default=30,
                validation={"min": 5, "max": 300},
            )
        )
        self.register_group(dashboard_group)

        # ========== HTTP 服务器配置 ==========
        http_group = ConfigGroupDefinition(
            key="http_server",
            label="HTTP 服务器",
            description="HTTP 回调服务器配置",
            icon="Link",
            order=80,
        )
        http_group.add_field(
            ConfigFieldDefinition(
                key="http_server.enable",
                label="启用 HTTP 服务器",
                field_type="boolean",
                description="是否启用 HTTP 服务器",
                default=True,
            )
        )
        http_group.add_field(
            ConfigFieldDefinition(
                key="http_server.host",
                label="监听地址",
                field_type="string",
                description="HTTP 服务器监听地址",
                default="127.0.0.1",
            )
        )
        http_group.add_field(
            ConfigFieldDefinition(
                key="http_server.port",
                label="监听端口",
                field_type="integer",
                description="HTTP 服务器监听端口",
                default=8080,
                validation={"min": 1, "max": 65535},
            )
        )
        http_group.add_field(
            ConfigFieldDefinition(
                key="http_server.callback_path",
                label="回调路径",
                field_type="string",
                description="回调路径",
                default="/maicore_callback",
            )
        )
        self.register_group(http_group)

        # ========== EventBus 配置 ==========
        eventbus_group = ConfigGroupDefinition(
            key="event_bus",
            label="事件总线",
            description="EventBus 配置",
            icon="DataLine",
            order=90,
        )
        eventbus_group.add_field(
            ConfigFieldDefinition(
                key="event_bus.enable_validation",
                label="启用数据验证",
                field_type="boolean",
                description="是否启用事件数据验证（建议仅 debug 模式开启）",
                default=False,
            )
        )
        self.register_group(eventbus_group)

        logger.info(f"已初始化 {len(self._groups)} 个配置分组")

    def register_group(self, group: ConfigGroupDefinition):
        """注册配置分组"""
        self._groups[group.key] = group
        for field in group.fields:
            self._field_map[field.key] = field
        logger.debug(f"注册配置分组: {group.key}")

    def get_group(self, key: str) -> Optional[ConfigGroupDefinition]:
        """获取配置分组"""
        return self._groups.get(key)

    def get_all_groups(self) -> List[ConfigGroupDefinition]:
        """获取所有配置分组"""
        return sorted(self._groups.values(), key=lambda g: g.order)

    def get_field(self, key: str) -> Optional[ConfigFieldDefinition]:
        """获取配置字段"""
        return self._field_map.get(key)

    def get_schema_for_config(
        self,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        根据当前配置生成 Schema

        Args:
            config: 当前配置字典

        Returns:
            包含分组和字段 Schema 的字典
        """
        groups = []
        for group in self.get_all_groups():
            group_dict = group.to_dict(config)
            groups.append(group_dict)

        return {
            "groups": groups,
            "version": "1.0.0",
        }


def get_schema_registry() -> ConfigSchemaRegistry:
    """获取 Schema 注册表单例"""
    return ConfigSchemaRegistry()
