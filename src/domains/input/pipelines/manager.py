from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass
from enum import Enum
import importlib
import inspect
import os
import sys
import time
from typing import Dict, List, Optional, Any, Type, Protocol, TYPE_CHECKING, runtime_checkable

from maim_message import MessageBase
from src.core.utils.logger import get_logger
from src.core.utils.config import load_component_specific_config, merge_component_configs

if TYPE_CHECKING:
    from .amaidesu_core import AmaidesuCore


# ==================== TextPipeline 接口（新架构） ====================


class PipelineErrorHandling(str, Enum):
    """Pipeline 错误处理策略"""

    CONTINUE = "continue"  # 记录日志，继续执行下一个 Pipeline
    STOP = "stop"  # 停止执行，抛出异常
    DROP = "drop"  # 丢弃消息，不执行后续 Pipeline


@dataclass
class PipelineStats:
    """Pipeline 统计信息"""

    processed_count: int = 0  # 处理次数
    dropped_count: int = 0  # 丢弃次数
    error_count: int = 0  # 错误次数
    total_duration_ms: float = 0  # 总处理时间（毫秒）

    @property
    def avg_duration_ms(self) -> float:
        """平均处理时间（毫秒）"""
        if self.processed_count == 0:
            return 0.0
        return self.total_duration_ms / self.processed_count


class PipelineException(Exception):
    """Pipeline 处理异常"""

    def __init__(
        self,
        pipeline_name: str,
        message: str,
        original_error: Optional[Exception] = None,
    ):
        self.pipeline_name = pipeline_name
        self.message = message
        self.original_error = original_error
        super().__init__(f"[{pipeline_name}] {message}")


@runtime_checkable
class TextPipeline(Protocol):
    """
    文本处理管道协议（3域架构：Input Domain 的文本预处理）

    用于在 InputLayer (RawData → NormalizedMessage) 中处理文本，
    如限流、敏感词过滤、文本清理、相似消息过滤等。

    位置：
        - InputLayer.normalize() 方法内部调用
        - 在创建 NormalizedMessage 之前对文本进行预处理
        - 可返回 None 表示丢弃该消息
    """

    priority: int
    enabled: bool
    error_handling: PipelineErrorHandling
    timeout_seconds: float

    async def process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        处理文本

        Args:
            text: 待处理的文本
            metadata: 元数据（如 user_id、source 等）

        Returns:
            处理后的文本，或 None 表示丢弃该消息
        """
        ...

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息（名称、版本等）"""
        ...

    def get_stats(self) -> PipelineStats:
        """获取统计信息"""
        ...

    def reset_stats(self) -> None:
        """重置统计信息"""
        ...


class TextPipelineBase(ABC):
    """
    TextPipeline 基类

    提供默认实现，子类只需实现 _process() 方法。
    """

    priority: int = 500
    enabled: bool = True
    error_handling: PipelineErrorHandling = PipelineErrorHandling.CONTINUE
    timeout_seconds: float = 5.0

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 TextPipeline

        Args:
            config: Pipeline 配置
        """
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self._stats = PipelineStats()

        # 从配置中读取可选参数
        self.priority = config.get("priority", self.priority)
        self.enabled = config.get("enabled", self.enabled)
        self.timeout_seconds = config.get("timeout_seconds", self.timeout_seconds)

        error_handling_str = config.get("error_handling", self.error_handling.value)
        if isinstance(error_handling_str, str):
            try:
                self.error_handling = PipelineErrorHandling(error_handling_str)
            except ValueError:
                self.logger.warning(f"无效的 error_handling 值: {error_handling_str}，使用默认值 CONTINUE")
                self.error_handling = PipelineErrorHandling.CONTINUE

    async def process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """处理文本（包装 _process 并记录统计）"""
        start_time = time.time()
        try:
            result = await self._process(text, metadata)
            duration_ms = (time.time() - start_time) * 1000
            self._stats.processed_count += 1
            self._stats.total_duration_ms += duration_ms
            return result
        except Exception:
            self._stats.error_count += 1
            raise

    @abstractmethod
    async def _process(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        实际处理文本（子类实现）

        Args:
            text: 待处理的文本
            metadata: 元数据

        Returns:
            处理后的文本，或 None 表示丢弃
        """
        pass

    def get_info(self) -> Dict[str, Any]:
        """获取 Pipeline 信息"""
        return {
            "name": self.__class__.__name__,
            "priority": self.priority,
            "enabled": self.enabled,
            "error_handling": self.error_handling.value,
            "timeout_seconds": self.timeout_seconds,
        }

    def get_stats(self) -> PipelineStats:
        """获取统计信息"""
        return self._stats

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = PipelineStats()


# ==================== MessagePipeline（旧架构，保持向后兼容） ====================


class MessagePipeline(ABC):
    """
    消息管道基类，用于在消息发送到 MaiCore 前进行处理。
    所有的管道都应该继承此类，并实现 process_message 方法。
    """

    # 默认优先级，数值越小优先级越高。实际优先级由配置决定。
    priority = 1000

    def __init__(self, config: Dict[str, Any]):
        """
        初始化管道。

        Args:
            config: 该管道的合并后配置字典。
        """
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.core: Optional[AmaidesuCore] = None  # 添加core属性的类型注解
        # 管道实现可以在这里从 self.config 中读取自己的配置项
        # 例如:
        # self.rate_limit = self.config.get("rate_limit", 60)
        # self.enabled_feature = self.config.get("feature_x_enabled", False)
        # self.logger.debug(f"管道 '{self.__class__.__name__}' 使用配置初始化: {self.config}")

    @abstractmethod
    async def process_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        处理消息的抽象方法，子类必须实现此方法。

        Args:
            message: 要处理的 MessageBase 对象

        Returns:
            处理后的 MessageBase 对象，如果返回 None 则表示该消息应被丢弃（不再继续处理）
        """
        pass

    async def on_connect(self) -> None:
        """
        当 AmaidesuCore 成功连接到 MaiCore 时调用的钩子方法。
        子类可以重写此方法以在连接建立时执行初始化操作。

        默认实现为空操作。
        """
        self.logger.debug("管道已连接")

    async def on_disconnect(self) -> None:
        """
        当 AmaidesuCore 与 MaiCore 断开连接时调用的钩子方法。
        子类可以重写此方法以在连接断开时执行清理操作。

        默认实现为空操作。
        """
        self.logger.debug("管道已断开")


class PipelineManager:
    """
    管道管理器，负责加载、排序和执行管道。

    3域架构中的Pipeline位置：

    **MessagePipeline（旧架构，保持向后兼容）**：
        - 处理 MessageBase（MaiCore 格式）
        - 用于 MaiCoreDecisionProvider 的 inbound/outbound 消息处理
        - 在 AmaidesuCore 中管理

    **TextPipeline（新架构，推荐使用）**：
        - 处理 text + metadata
        - 用于 InputLayer (Input Domain: 输入域) 中的文本预处理
        - 在 RawData → NormalizedMessage 转换过程中处理文本
        - 示例：限流、相似文本过滤、敏感词过滤
    """

    def __init__(self, core=None):
        # MessagePipeline（旧架构）
        self._inbound_pipelines: List[MessagePipeline] = []
        self._outbound_pipelines: List[MessagePipeline] = []
        self._inbound_sorted: bool = True
        self._outbound_sorted: bool = True

        # TextPipeline（新架构）
        self._text_pipelines: List[TextPipeline] = []
        self._text_pipelines_sorted: bool = True
        self._text_pipeline_lock = asyncio.Lock()

        self.logger = get_logger("PipelineManager")
        self.core = core  # 保存core引用，用于为管道设置core属性

    def _register_pipeline(self, pipeline: MessagePipeline, direction: str) -> None:
        """
        根据方向注册一个消息管道。

        Args:
            pipeline: MessagePipeline 的实例。
            direction: 管道方向 ("inbound" 或 "outbound")。
        """
        # 为管道设置core引用
        if self.core is not None:
            pipeline.core = self.core

        if direction == "inbound":
            self._inbound_pipelines.append(pipeline)
            self._inbound_sorted = False
        else:  # 默认 "outbound"
            self._outbound_pipelines.append(pipeline)
            self._outbound_sorted = False

        self.logger.info(f"管道已注册: {pipeline.__class__.__name__} (方向: {direction}, 优先级: {pipeline.priority})")

    def _ensure_outbound_sorted(self) -> None:
        """确保出站管道列表按优先级排序"""
        if not self._outbound_sorted:
            self._outbound_pipelines.sort(key=lambda x: x.priority)
            self._outbound_sorted = True
            pipe_info = ", ".join([f"{p.__class__.__name__}({p.priority})" for p in self._outbound_pipelines])
            self.logger.debug(f"出站管道已排序: {pipe_info}")

    def _ensure_inbound_sorted(self) -> None:
        """确保入站管道列表按优先级排序"""
        if not self._inbound_sorted:
            self._inbound_pipelines.sort(key=lambda x: x.priority)
            self._inbound_sorted = True
            pipe_info = ", ".join([f"{p.__class__.__name__}({p.priority})" for p in self._inbound_pipelines])
            self.logger.debug(f"入站管道已排序: {pipe_info}")

    async def process_outbound_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        按优先级顺序通过所有出站管道处理消息。

        Args:
            message: 要处理的 MessageBase 对象

        Returns:
            处理后的 MessageBase 对象，如果任何管道返回 None 则返回 None
        """
        self._ensure_outbound_sorted()

        current_message = message
        for pipeline in self._outbound_pipelines:
            if current_message is None:
                self.logger.info(f"消息被前序管道丢弃，终止于出站管道 {pipeline.__class__.__name__} 之前")
                return None

            try:
                self.logger.debug(
                    f"出站管道 {pipeline.__class__.__name__} 开始处理消息: {message.message_info.message_id}"
                )
                current_message = await pipeline.process_message(current_message)
                if current_message is None:
                    self.logger.info(
                        f"消息 {message.message_info.message_id} 被出站管道 {pipeline.__class__.__name__} 丢弃"
                    )
                    return None
            except Exception as e:
                self.logger.error(f"出站管道 {pipeline.__class__.__name__} 处理消息时出错: {e}", exc_info=True)

        return current_message

    async def process_inbound_message(self, message: MessageBase) -> Optional[MessageBase]:
        """
        按优先级顺序通过所有入站管道处理消息。

        Args:
            message: 要处理的 MessageBase 对象

        Returns:
            处理后的 MessageBase 对象，如果任何管道返回 None 则返回 None
        """
        self._ensure_inbound_sorted()

        current_message = message
        for pipeline in self._inbound_pipelines:
            if current_message is None:
                self.logger.info(f"消息被前序管道丢弃，终止于入站管道 {pipeline.__class__.__name__} 之前")
                return None

            try:
                self.logger.debug(
                    f"入站管道 {pipeline.__class__.__name__} 开始处理消息: {message.message_info.message_id}"
                )
                current_message = await pipeline.process_message(current_message)
                if current_message is None:
                    self.logger.info(
                        f"消息 {message.message_info.message_id} 被入站管道 {pipeline.__class__.__name__} 丢弃"
                    )
                    return None
            except Exception as e:
                self.logger.error(f"入站管道 {pipeline.__class__.__name__} 处理消息时出错: {e}", exc_info=True)

        return current_message

    # ==================== TextPipeline 方法（新架构） ====================

    def register_text_pipeline(self, pipeline: TextPipeline) -> None:
        """
        注册一个 TextPipeline

        Args:
            pipeline: TextPipeline 实例
        """
        self._text_pipelines.append(pipeline)
        self._text_pipelines_sorted = False

        info = pipeline.get_info()
        self.logger.info(
            f"TextPipeline 已注册: {info['name']} (priority={info['priority']}, enabled={info['enabled']})"
        )

    def _ensure_text_pipelines_sorted(self) -> None:
        """确保 TextPipeline 列表按优先级排序"""
        if not self._text_pipelines_sorted:
            self._text_pipelines.sort(key=lambda p: p.priority)
            self._text_pipelines_sorted = True
            pipe_info = ", ".join([f"{p.get_info()['name']}({p.priority})" for p in self._text_pipelines])
            self.logger.debug(f"TextPipeline 已排序: {pipe_info}")

    async def process_text(self, text: str, metadata: Dict[str, Any]) -> Optional[str]:
        """
        按优先级顺序通过所有启用的 TextPipeline 处理文本

        这是 InputLayer (Input Domain: 输入域) 中的文本预处理入口点。
        在 RawData → NormalizedMessage 转换过程中调用。

        Args:
            text: 待处理的文本
            metadata: 元数据（如 user_id、source 等）

        Returns:
            处理后的文本，如果任何 Pipeline 返回 None 则返回 None（表示丢弃）

        Raises:
            PipelineException: 当某个 Pipeline 错误处理策略为 STOP 时抛出
        """
        if not self._text_pipelines:
            return text  # 没有 TextPipeline，直接返回原文本

        async with self._text_pipeline_lock:
            self._ensure_text_pipelines_sorted()

            current_text = text

            for pipeline in self._text_pipelines:
                if not pipeline.enabled:
                    continue

                info = pipeline.get_info()
                pipeline_name = info["name"]

                try:
                    # 记录开始时间
                    start_time = time.time()

                    # 带超时的处理（current_text 在此处保证非 None，因为 None 会提前返回）
                    assert current_text is not None
                    result = await asyncio.wait_for(
                        pipeline.process(current_text, metadata),
                        timeout=pipeline.timeout_seconds,
                    )
                    current_text = result

                    duration_ms = (time.time() - start_time) * 1000

                    # 如果返回 None，丢弃消息
                    if current_text is None:
                        self.logger.debug(f"TextPipeline {pipeline_name} 丢弃了消息 (耗时 {duration_ms:.2f}ms)")
                        stats = pipeline.get_stats()
                        stats.dropped_count += 1
                        return None

                    self.logger.debug(f"TextPipeline {pipeline_name} 处理完成 (耗时 {duration_ms:.2f}ms)")

                except asyncio.TimeoutError as timeout_error:
                    error = PipelineException(pipeline_name, f"处理超时 ({pipeline.timeout_seconds}s)")
                    self.logger.error(f"TextPipeline 超时: {error}")

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        raise error from timeout_error
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续执行下一个 Pipeline

                except PipelineException:
                    raise  # 直接向上抛出

                except Exception as e:
                    error = PipelineException(pipeline_name, f"处理失败: {e}", original_error=e)
                    self.logger.error(f"TextPipeline 错误: {error}", exc_info=True)

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        raise error from e
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续执行下一个 Pipeline

            return current_text

    def get_text_pipeline_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有 TextPipeline 的统计信息

        Returns:
            {pipeline_name: {processed_count, dropped_count, error_count, avg_duration_ms}}
        """
        result = {}
        for pipeline in self._text_pipelines:
            info = pipeline.get_info()
            stats = pipeline.get_stats()
            result[info["name"]] = {
                "processed_count": stats.processed_count,
                "dropped_count": stats.dropped_count,
                "error_count": stats.error_count,
                "avg_duration_ms": stats.avg_duration_ms,
                "enabled": info["enabled"],
                "priority": info["priority"],
            }
        return result

    # ==================== MessagePipeline 加载（旧架构） ====================

    async def load_pipelines(
        self, pipeline_base_dir: str = "src/domains/input/pipelines", root_config_pipelines_section: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        扫描并加载指定目录下的所有管道。
        采用新的配置结构，从根配置的 [pipelines.pipeline_name_snake] 获取优先级和全局覆盖。

        Args:
            pipeline_base_dir: 管道包的基础目录，默认为 "src/domains/input/pipelines"
            root_config_pipelines_section: 根配置文件中 'pipelines' 部分的字典。
                                           例如：config.get('pipelines', {})
        """
        self.logger.info(f"开始从目录加载管道: {pipeline_base_dir}")
        pipeline_dir_abs = os.path.abspath(pipeline_base_dir)

        if not os.path.isdir(pipeline_dir_abs):
            self.logger.warning(f"管道目录不存在: {pipeline_dir_abs}，跳过管道加载。")
            return

        # 将 src 目录（通常是管道目录的父目录）添加到 sys.path
        src_dir = os.path.dirname(pipeline_dir_abs)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
            self.logger.debug(f"已将目录添加到 sys.path: {src_dir}")

        if root_config_pipelines_section is None:
            root_config_pipelines_section = {}
            self.logger.warning("未提供根配置中的 'pipelines' 部分，所有管道将无法加载。")
            return

        loaded_pipeline_count = 0

        # 遍历根配置中定义的管道
        for pipeline_name_snake, pipeline_global_settings in root_config_pipelines_section.items():
            if not isinstance(pipeline_global_settings, dict):
                self.logger.warning(f"管道 '{pipeline_name_snake}' 在根配置中的条目格式不正确 (应为字典), 跳过。")
                continue

            priority = pipeline_global_settings.get("priority")
            if not isinstance(priority, int):
                self.logger.info(  # 使用info级别，因为这可能是用户故意禁用管道的方式
                    f"管道 '{pipeline_name_snake}' 在根配置中 'priority' 缺失或无效，视为禁用，跳过加载。"
                )
                continue

            # --- 新增：确定管道方向 ---
            # 默认为 "outbound" 以保持向后兼容
            direction = pipeline_global_settings.get("direction", "outbound").lower()
            if direction not in ["inbound", "outbound"]:
                self.logger.warning(
                    f"管道 '{pipeline_name_snake}' 的方向配置 '{direction}' 无效，将默认为 'outbound'。"
                )
                direction = "outbound"

            self.logger.debug(f"管道 '{pipeline_name_snake}' 方向设置为: {direction}")

            pipeline_package_path = os.path.join(pipeline_dir_abs, pipeline_name_snake)

            # 检查预期的管道目录和文件是否存在
            if not (
                os.path.isdir(pipeline_package_path)
                and os.path.exists(os.path.join(pipeline_package_path, "__init__.py"))
                and os.path.exists(os.path.join(pipeline_package_path, "pipeline.py"))
            ):
                self.logger.warning(
                    f"管道 '{pipeline_name_snake}' 在根配置中已启用 (priority={priority})，"
                    f"但在 '{pipeline_package_path}' 未找到有效的包结构 (需要 __init__.py 和 pipeline.py)，跳过。"
                )
                continue

            # 1. 提取全局覆盖配置 (排除 'priority' 和 'direction' 键)
            global_override_config = {
                k: v for k, v in pipeline_global_settings.items() if k not in ["priority", "direction"]
            }
            self.logger.debug(f"管道 '{pipeline_name_snake}' 的全局覆盖配置: {global_override_config}")

            # 2. 加载管道自身的独立配置
            pipeline_specific_config = load_component_specific_config(
                pipeline_package_path, pipeline_name_snake, "管道"
            )

            # 3. 合并配置：全局覆盖配置优先
            final_pipeline_config = merge_component_configs(
                pipeline_specific_config, global_override_config, pipeline_name_snake, "管道"
            )
            # self.logger.debug(f"管道 '{pipeline_name_snake}' 合并后的最终配置: {final_pipeline_config}") # 此日志现在由 merge_component_configs 处理

            # 4. 导入并实例化管道
            try:
                module_import_path = f"pipelines.{pipeline_name_snake}.pipeline"
                self.logger.debug(f"尝试导入管道模块: {module_import_path}")
                module = importlib.import_module(module_import_path)

                expected_class_name = "".join(word.title() for word in pipeline_name_snake.split("_")) + "Pipeline"
                pipeline_class: Optional[Type[MessagePipeline]] = None

                for name, obj in inspect.getmembers(module):
                    if inspect.isclass(obj) and issubclass(obj, MessagePipeline) and obj != MessagePipeline:
                        if name == expected_class_name:
                            pipeline_class = obj
                            break

                if pipeline_class:
                    # 直接在实例上设置优先级，因为基类构造函数不处理它
                    # 而 MessagePipeline 类本身的 priority 是默认值
                    pipeline_instance = pipeline_class(config=final_pipeline_config)
                    pipeline_instance.priority = priority  # 设置实例的优先级，用于排序

                    self._register_pipeline(pipeline_instance, direction)  # 使用新的注册方法
                    loaded_pipeline_count += 1
                    # self.logger.info(f"成功加载并设置管道: {pipeline_class.__name__} (来自 {pipeline_name_snake}/pipeline.py, 优先级: {priority})") # register_pipeline 已记录
                else:
                    self.logger.error(f"在模块 '{module_import_path}' 中未找到预期的管道类 '{expected_class_name}'。")

            except ImportError as e:
                self.logger.error(f"导入管道模块 '{module_import_path}' 失败: {e}", exc_info=True)
            except Exception as e:
                self.logger.error(f"加载或实例化管道 '{pipeline_name_snake}' 时发生错误: {e}", exc_info=True)

        if loaded_pipeline_count > 0:
            self.logger.info(f"管道加载完成，共加载 {loaded_pipeline_count} 个启用的管道。")
        else:
            self.logger.warning("未加载任何启用的管道。请检查根配置文件 [pipelines] 部分和管道目录结构。")

        self._ensure_inbound_sorted()
        self._ensure_outbound_sorted()

        self.logger.info(
            f"所有管道加载完成, 入站: {len(self._inbound_pipelines)} 个, 出站: {len(self._outbound_pipelines)} 个"
        )

        # 如果有core引用，为所有已加载的管道设置core属性
        if self.core is not None:
            self._set_core_for_all_pipelines()

    def _set_core_for_all_pipelines(self):
        """为所有已加载的管道设置core引用"""
        all_pipelines = self._inbound_pipelines + self._outbound_pipelines
        for pipeline in all_pipelines:
            pipeline.core = self.core
        self.logger.debug(f"已为 {len(all_pipelines)} 个管道设置core引用")

    # ==================== TextPipeline 加载（新架构） ====================

    async def load_text_pipelines(
        self, pipeline_base_dir: str = "src/domains/input/pipelines", root_config_pipelines_section: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        扫描并加载 TextPipeline 类型的管道。

        TextPipeline 用于 InputLayer (Input Domain) 中的文本预处理。
        与 MessagePipeline 不同，TextPipeline 不区分 inbound/outbound，统一按 priority 顺序执行。

        3域架构中的位置：
            - InputLayer.normalize() 方法内部调用
            - 在 RawData → NormalizedMessage 转换过程中处理文本
            - 示例：限流、相似文本过滤、敏感词过滤

        Args:
            pipeline_base_dir: 管道包的基础目录，默认为 "src/domains/input/pipelines"
            root_config_pipelines_section: 根配置文件中 'pipelines' 部分的字典
        """
        self.logger.info(f"开始从目录加载 TextPipeline: {pipeline_base_dir}")
        pipeline_dir_abs = os.path.abspath(pipeline_base_dir)

        if not os.path.isdir(pipeline_dir_abs):
            self.logger.warning(f"管道目录不存在: {pipeline_base_dir}，跳过 TextPipeline 加载。")
            return

        # 将 src 目录添加到 sys.path（如果尚未添加）
        src_dir = os.path.dirname(pipeline_dir_abs)
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
            self.logger.debug(f"已将目录添加到 sys.path: {src_dir}")

        if root_config_pipelines_section is None:
            root_config_pipelines_section = {}
            self.logger.warning("未提供根配置中的 'pipelines' 部分，所有 TextPipeline 将无法加载。")
            return

        loaded_pipeline_count = 0

        # 遍历根配置中定义的管道
        for pipeline_name_snake, pipeline_global_settings in root_config_pipelines_section.items():
            if not isinstance(pipeline_global_settings, dict):
                self.logger.warning(f"管道 '{pipeline_name_snake}' 在根配置中的条目格式不正确 (应为字典), 跳过。")
                continue

            priority = pipeline_global_settings.get("priority")
            if not isinstance(priority, int):
                # TextPipeline 可能通过priority启用，也可能禁用
                self.logger.debug(
                    f"管道 '{pipeline_name_snake}' 在根配置中 'priority' 缺失或无效，跳过 TextPipeline 加载。"
                )
                continue

            pipeline_package_path = os.path.join(pipeline_dir_abs, pipeline_name_snake)

            # 检查管道目录结构
            if not (
                os.path.isdir(pipeline_package_path)
                and os.path.exists(os.path.join(pipeline_package_path, "__init__.py"))
                and os.path.exists(os.path.join(pipeline_package_path, "pipeline.py"))
            ):
                continue

            # 提取全局覆盖配置（排除 'priority' 和 'direction' 键）
            global_override_config = {
                k: v for k, v in pipeline_global_settings.items() if k not in ["priority", "direction"]
            }

            # 加载管道自身的独立配置
            pipeline_specific_config = load_component_specific_config(
                pipeline_package_path, pipeline_name_snake, "管道"
            )

            # 合并配置：全局覆盖配置优先
            final_pipeline_config = merge_component_configs(
                pipeline_specific_config, global_override_config, pipeline_name_snake, "管道"
            )

            # 导入并查找 TextPipelineBase 子类
            try:
                module_import_path = f"pipelines.{pipeline_name_snake}.pipeline"
                self.logger.debug(f"尝试导入管道模块: {module_import_path}")
                module = importlib.import_module(module_import_path)

                expected_class_name = "".join(word.title() for word in pipeline_name_snake.split("_")) + "TextPipeline"
                pipeline_class: Optional[Type[TextPipeline]] = None

                # 查找 TextPipelineBase 子类
                for name, obj in inspect.getmembers(module):
                    # 检查是否是 TextPipelineBase 的子类（间接检查 Protocol）
                    if (
                        inspect.isclass(obj)
                        and hasattr(obj, "__bases__")
                        and any(base.__name__ == "TextPipelineBase" for base in obj.__bases__)
                    ):
                        if name == expected_class_name:
                            pipeline_class = obj
                            break

                if pipeline_class:
                    # 实例化管道
                    pipeline_instance = pipeline_class(config=final_pipeline_config)
                    pipeline_instance.priority = priority

                    # 注册到 TextPipeline 列表
                    self.register_text_pipeline(pipeline_instance)
                    loaded_pipeline_count += 1
                else:
                    # 未找到 TextPipeline，可能该管道是 MessagePipeline，跳过
                    self.logger.debug(f"模块 '{module_import_path}' 中未找到 TextPipeline，跳过。")

            except ImportError as e:
                self.logger.error(f"导入管道模块 '{module_import_path}' 失败: {e}", exc_info=True)
            except Exception as e:
                self.logger.error(f"加载 TextPipeline '{pipeline_name_snake}' 时发生错误: {e}", exc_info=True)

        if loaded_pipeline_count > 0:
            self.logger.info(f"TextPipeline 加载完成，共加载 {loaded_pipeline_count} 个管道。")
        else:
            self.logger.info("未加载任何 TextPipeline。")

    async def notify_connect(self) -> None:
        """当 AmaidesuCore 连接时，按优先级顺序通知所有管道。"""
        self.logger.debug("正在按顺序通知管道连接...")
        self._ensure_inbound_sorted()
        self._ensure_outbound_sorted()

        all_pipelines = self._inbound_pipelines + self._outbound_pipelines
        for pipeline in all_pipelines:
            try:
                await pipeline.on_connect()
            except Exception as e:
                self.logger.error(f"管道 {pipeline.__class__.__name__} 的 on_connect 钩子出错: {e}", exc_info=True)

    async def notify_disconnect(self) -> None:
        """当 AmaidesuCore 断开连接时，按优先级顺序通知所有管道。"""
        self.logger.debug("正在按顺序通知管道断开连接...")
        self._ensure_inbound_sorted()
        self._ensure_outbound_sorted()

        all_pipelines = self._inbound_pipelines + self._outbound_pipelines
        for pipeline in all_pipelines:
            try:
                await pipeline.on_disconnect()
            except Exception as e:
                self.logger.error(f"管道 {pipeline.__class__.__name__} 的 on_disconnect 钩子出错: {e}", exc_info=True)
