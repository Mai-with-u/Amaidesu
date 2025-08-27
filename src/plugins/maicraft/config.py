from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any
from src.utils.logger import get_logger


class LLMConfig(BaseModel):
    """LLM配置模型"""

    model: str = Field(default="gpt-4o-mini", description="LLM模型名称")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    base_url: Optional[str] = Field(default=None, description="API基础URL")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="温度参数") 
    max_tokens: int = Field(default=1024, ge=100, le=8000, description="最大token数")

    @field_validator("temperature")
    @classmethod
    def validate_temperature(cls, v):
        """验证温度参数"""
        if not 0.0 <= v <= 2.0:
            raise ValueError("温度参数必须在0.0到2.0之间")
        return v

class LLMConfigFast(BaseModel):
    """LLM配置模型"""
    model: str = Field(default="gpt-4o-mini", description="LLM模型名称")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    base_url: Optional[str] = Field(default=None, description="API基础URL")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="温度参数") 
    max_tokens: int = Field(default=1024, ge=100, le=8000, description="最大token数")

class VLMConfig(BaseModel):
    """VLM配置模型"""
    model: str = Field(default="gpt-4o-mini", description="VLM模型名称")
    api_key: Optional[str] = Field(default=None, description="API密钥")
    base_url: Optional[str] = Field(default=None, description="API基础URL")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0, description="温度参数") 
    max_tokens: int = Field(default=1024, ge=100, le=8000, description="最大token数")

class AgentModeConfig(BaseModel):
    """Agent模式配置模型"""

    mode: str = Field(default="mai_agent", description="Agent模式")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        """验证Agent模式"""
        valid_modes = ["mai_agent","default_agent"]
        if v not in valid_modes:
            raise ValueError(f"不支持的Agent模式: {v}。支持的模式: {valid_modes}")
        return v

class AgentConfig(BaseModel):
    """Agent配置模型"""

    enabled: bool = Field(default=True, description="是否启用Agent")
    session_id: str = Field(default="maicraft_default", description="会话ID")
    max_steps: int = Field(default=50, ge=1, le=100, description="最大步骤数")
    tick_seconds: float = Field(default=8.0, ge=1.0, le=60.0, description="执行间隔(秒)")
    report_each_step: bool = Field(default=True, description="是否报告每个步骤")

    @field_validator("max_steps")
    @classmethod
    def validate_max_steps(cls, v):
        """验证最大步骤数"""
        if v > 100:
            raise ValueError("max_steps不能超过100")
        return v

    @field_validator("tick_seconds")
    @classmethod
    def validate_tick_seconds(cls, v):
        """验证执行间隔"""
        if v < 1.0 or v > 60.0:
            raise ValueError("tick_seconds必须在1.0到60.0之间")
        return v


class LangChainConfig(BaseModel):
    """LangChain配置模型"""

    max_token_limit: int = Field(default=4000, ge=1000, le=8000, description="最大token限制")
    verbose: bool = Field(default=False, description="是否启用详细日志")
    early_stopping_method: str = Field(default="generate", description="早期停止方法")
    handle_parsing_errors: bool = Field(default=True, description="是否处理解析错误")

    @field_validator("early_stopping_method")
    @classmethod
    def validate_early_stopping_method(cls, v):
        """验证早期停止方法"""
        valid_methods = ["generate", "force", "none"]
        if v not in valid_methods:
            raise ValueError(f"不支持的早期停止方法: {v}。支持的方法: {valid_methods}")
        return v


class ErrorDetectionConfig(BaseModel):
    """错误检测配置模型"""

    mode: str = Field(default="full_json", description="错误检测模式: full_json 或 custom_keys")
    error_keys: Dict[str, Any] = Field(
        default={"success": False, "ok": False, "error": True, "failed": True}, description="错误检测字段映射"
    )
    error_message_keys: list = Field(
        default=["error_message", "error", "message", "reason"], description="错误消息字段列表"
    )
    error_code_keys: list = Field(default=["error_code", "code", "status_code"], description="错误代码字段列表")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v):
        """验证错误检测模式"""
        valid_modes = ["full_json", "custom_keys"]
        if v not in valid_modes:
            raise ValueError(f"不支持的错误检测模式: {v}。支持的模式: {valid_modes}")
        return v


class MaicraftConfig(BaseModel):
    """Maicraft插件配置模型"""

    llm: LLMConfig = Field(default_factory=LLMConfig, description="LLM配置")
    llm_fast: LLMConfigFast = Field(default_factory=LLMConfigFast, description="LLM快速配置")
    vlm: VLMConfig = Field(default_factory=VLMConfig, description="VLM配置")
    agent_mode: AgentModeConfig = Field(default_factory=AgentModeConfig, description="Agent模式配置")
    agent: AgentConfig = Field(default_factory=AgentConfig, description="Agent配置")
    langchain: LangChainConfig = Field(default_factory=LangChainConfig, description="LangChain配置")
    error_detection: ErrorDetectionConfig = Field(default_factory=ErrorDetectionConfig, description="错误检测配置")

    @field_validator("agent_mode")
    @classmethod
    def validate_agent_mode_config(cls, v):
        """验证Agent模式配置"""
        return v

    @field_validator("agent")
    @classmethod
    def validate_agent_config(cls, v):
        """验证Agent配置"""
        if v.max_steps > 100:
            raise ValueError("max_steps不能超过100")
        return v

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.dict()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MaicraftConfig":
        """从字典创建配置"""
        return cls(**data)

    def validate_and_log(self) -> bool:
        """验证配置并记录日志"""
        logger = get_logger("MaicraftConfig")
        try:
            # 验证配置
            self.validate(self)
            logger.info("[配置验证] 配置验证通过")

            # 记录配置信息
            logger.info(f"[配置验证] LLM模型: {self.llm.model}")
            logger.info(f"[配置验证] Agent启用: {self.agent.enabled}")
            logger.info(f"[配置验证] 最大步骤: {self.agent.max_steps}")
            logger.info(f"[配置验证] 执行间隔: {self.agent.tick_seconds}秒")
            logger.info(f"[配置验证] 最大Token: {self.langchain.max_token_limit}")

            return True

        except Exception as e:
            logger.error(f"[配置验证] 配置验证失败: {e}")
            return False


def load_config_from_dict(config_data: Dict[str, Any]) -> MaicraftConfig:
    """从字典加载配置"""
    logger = get_logger("ConfigLoader")
    try:
        config = MaicraftConfig.from_dict(config_data)
        if config.validate_and_log():
            logger.info("[配置加载] 配置加载成功")
            return config
        else:
            raise ValueError("配置验证失败")
    except Exception as e:
        logger.error(f"[配置加载] 配置加载失败: {e}")
        raise


def create_default_config() -> MaicraftConfig:
    """创建默认配置"""
    return MaicraftConfig()
