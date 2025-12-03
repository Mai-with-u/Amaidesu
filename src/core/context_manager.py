from typing import Any, Dict, List, Optional, TypedDict
import asyncio

from src.utils.logger import get_logger


# --- 上下文提供者数据的类型定义 ---
class ContextProviderData(TypedDict):
    provider_name: str
    context_info: str
    priority: int
    tags: List[str]
    enabled: bool


class ContextManager:
    """
    管理和聚合可附加到提示的上下文信息。
    其他插件可以注册上下文提供者，发送消息的插件可以检索聚合的上下文。
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化上下文管理器

        Args:
            config: 上下文管理器的配置
        """
        self.logger = get_logger("ContextManager")
        self.config = config

        # 从配置中获取格式化和限制设置，支持嵌套结构
        self.formatting_config = config.get("formatting", {})
        self.limits_config = config.get("limits", {})

        # --- 存储上下文提供者 ---
        # Key: provider_name, Value: ContextProviderData
        self._context_providers: Dict[str, ContextProviderData] = {}

        # --- 配置值 ---
        self.enabled = config.get("enabled", True)
        self.separator = self.formatting_config.get("separator", "\\n").replace("\\n", "\n")
        self.add_provider_title = self.formatting_config.get("add_provider_title", False)
        self.title_separator = self.formatting_config.get("title_separator", ": ")
        self.default_max_length = self.limits_config.get("default_max_length", 5000)
        self.default_priority = self.limits_config.get("default_priority", 100)

        self.logger.info(f"上下文管理器初始化完成，启用状态: {self.enabled}")
        self.logger.debug(f"格式化配置: separator='{self.separator}', add_provider_title={self.add_provider_title}")
        self.logger.debug(
            f"限制配置: default_max_length={self.default_max_length}, default_priority={self.default_priority}"
        )

        if not self.enabled:
            self.logger.warning("ContextManager 在配置中已禁用。")

    def register_context_provider(
        self,
        provider_name: str,
        context_info: Any,
        priority: Optional[int] = None,
        tags: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> bool:
        """
        注册或更新上下文提供者。
        context_info 可以是字符串或返回字符串的异步函数。

        Args:
            provider_name: 提供者的唯一标识符（例如，"vts_actions"）。
            context_info: 要添加到提示中的实际上下文字符串。
            priority: 数字越小优先级越高（越早出现）。如果为 None，则使用默认值。
            tags: 用于过滤的标签列表（例如，["action", "vts"]）。
            enabled: 此提供者的上下文当前是否处于活动状态。

        Returns:
            如果注册/更新成功则为 True，否则为 False。
        """
        if not self.enabled:
            self.logger.warning(f"无法注册 '{provider_name}'，ContextManager 已禁用。")
            return False
        if not provider_name:
            self.logger.error("提供者名称不能为空。")
            return False

        resolved_priority = priority if priority is not None else self.default_priority
        resolved_tags = tags if tags is not None else []

        provider_data: ContextProviderData = {
            "provider_name": provider_name,
            "context_info": context_info,
            "priority": resolved_priority,
            "tags": resolved_tags,
            "enabled": enabled,
        }
        self._context_providers[provider_name] = provider_data
        self.logger.info(
            f"上下文提供者 '{provider_name}' 已注册/更新（优先级：{resolved_priority}，已启用：{enabled}）。"
        )

        # 修改调试日志以处理可调用对象
        if callable(context_info):
            context_repr = f"<callable: {getattr(context_info, '__name__', repr(context_info))}>"
        elif isinstance(context_info, str):
            context_repr = f"'{context_info[:100]}...'"
        else:
            context_repr = repr(context_info)
        self.logger.debug(f"'{provider_name}' 上下文：{context_repr}")

        return True

    def update_context_info(
        self, provider_name: str, context_info: Optional[str] = None, enabled: Optional[bool] = None
    ) -> bool:
        """
        更新现有上下文提供者的特定字段。

        Args:
            provider_name: 要更新的提供者的标识符。
            context_info: 新的上下文字符串（如果要更新）。
            enabled: 新的启用状态（如果要更新）。

        Returns:
            如果更新成功则为 True，如果找不到提供者或未指定更改则为 False。
        """
        if not self.enabled:
            self.logger.warning(f"无法更新 '{provider_name}'，ContextManager 已禁用。")
            return False
        if provider_name not in self._context_providers:
            self.logger.warning(f"无法更新不存在的提供者的上下文：'{provider_name}'")
            return False
        if context_info is None and enabled is None:
            self.logger.warning(f"未指定提供者的更新：'{provider_name}'")
            return False

        provider = self._context_providers[provider_name]
        updated = False
        if context_info is not None:
            provider["context_info"] = context_info
            self.logger.info(f"已更新 '{provider_name}' 的上下文信息。")
            self.logger.debug(f"新上下文：'{context_info[:100]}...'")
            updated = True
        if enabled is not None:
            provider["enabled"] = enabled
            self.logger.info(f"'{provider_name}' 的启用状态设置为 {enabled}。")
            updated = True

        return updated

    def unregister_context_provider(self, provider_name: str) -> bool:
        """移除上下文提供者。"""
        if provider_name in self._context_providers:
            del self._context_providers[provider_name]
            self.logger.info(f"上下文提供者 '{provider_name}' 已注销。")
            return True
        else:
            self.logger.warning(f"尝试注销不存在的提供者：'{provider_name}'")
            return False

    async def get_formatted_context(self, tags: Optional[List[str]] = None, max_length: Optional[int] = None) -> str:
        """
        从已启用的提供者检索并格式化聚合上下文，
        按优先级排序并可选择按标签过滤。
        处理字符串和异步可调用的 context_info。

        Args:
            tags: 如果提供，则仅包括匹配所有这些标签的提供者。
            max_length: 覆盖返回字符串的默认最大长度。

        Returns:
            包含格式化上下文的单个字符串，可能会被截断。
        """
        if not self.enabled:
            return ""

        target_max_length = max_length if max_length is not None else self.default_max_length

        # 1. 按启用状态和标签过滤提供者
        eligible_providers = []
        for provider in self._context_providers.values():
            if not provider["enabled"]:
                continue
            if tags:  # 检查提供者的标签中是否存在所有请求的标签
                if not all(tag in provider["tags"] for tag in tags):
                    continue
            eligible_providers.append(provider)

        # 2. 按优先级（升序）然后按名称（字母顺序稳定性）排序
        eligible_providers.sort(key=lambda p: (p["priority"], p["provider_name"]))
        self.logger.debug(f"上下文的合格提供者：{[p['provider_name'] for p in eligible_providers]}")

        # 3. 格式化并组合上下文字符串
        context_parts: List[str] = []
        current_length = 0
        separator_len = len(self.separator)

        for provider in eligible_providers:
            context_value: Optional[str] = None
            provider_name = provider["provider_name"]
            raw_context_info = provider["context_info"]

            # --- 获取上下文值（处理可调用对象）---
            if callable(raw_context_info):
                self.logger.debug(f"调用异步提供者：{provider_name}")
                try:
                    # 检查是否是协程函数（更健壮的方式）
                    if asyncio.iscoroutinefunction(raw_context_info):
                        context_value = await raw_context_info()
                    else:  # 如果不是 async def 但可调用（虽然我们期望是 async）
                        # 可以在这里决定是否支持同步可调用，或者直接报错/跳过
                        self.logger.warning(f"上下文提供者 '{provider_name}' 是可调用的但不是异步函数。跳过。")
                        continue
                except Exception as e:
                    self.logger.error(f"调用上下文提供者 '{provider_name}' 时出错：{e}", exc_info=True)
                    # 出错时可以跳过这个提供者
                    continue
            elif isinstance(raw_context_info, str):
                context_value = raw_context_info
            else:
                self.logger.warning(
                    f"提供者 '{provider_name}' 的 context_info 类型意外：{type(raw_context_info)}。跳过。"
                )
                continue

            # --- 使用获取到的 context_value 进行后续处理 ---
            if not context_value:  # 跳过空上下文（可能在调用可调用对象后）
                self.logger.debug(f"提供者 '{provider_name}' 返回空上下文。跳过。")
                continue

            prefix = ""
            if self.add_provider_title:
                prefix = f"{provider_name}{self.title_separator}"

            full_part = prefix + context_value
            part_len = len(full_part)

            # 添加前检查长度（如果不是第一部分，则包括分隔符）
            projected_length = current_length + part_len
            if context_parts:  # 如果不是第一部分，计算分隔符
                projected_length += separator_len

            if projected_length <= target_max_length:
                context_parts.append(full_part)
                current_length = projected_length
            else:
                # 尝试添加截断部分（如果可能）
                remaining_space = target_max_length - current_length
                if context_parts:  # 计算分隔符空间
                    remaining_space -= separator_len

                if remaining_space > 3:  # 需要给 "..." 留出空间
                    truncated_part = full_part[: remaining_space - 3] + "..."
                    context_parts.append(truncated_part)
                    self.logger.warning(f"来自 '{provider_name}' 的上下文因 max_length 而被截断。")
                else:
                    # 甚至截断部分的空间也不够，停止添加
                    self.logger.warning(f"来自 '{provider_name}' 的上下文因 max_length 而完全跳过。")
                    break  # 停止处理更多提供者

        self.logger.debug(f"连接前的最终上下文部分：{context_parts}")
        return self.separator.join(context_parts)
