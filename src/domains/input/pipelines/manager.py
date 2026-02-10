import asyncio
import importlib
import inspect
import os
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, Type, runtime_checkable

from src.modules.config.config_utils import load_component_specific_config, merge_component_configs
from src.modules.logging import get_logger
from src.modules.types.base.pipeline_stats import PipelineStats

# ==================== PipelineContext（支持回滚的执行上下文） ====================


@dataclass
class PipelineContext:
    """
    管道执行上下文，支持回滚

    用于在管道执行失败时恢复到原始状态，防止数据损坏。
    问题#4修复：在CONTINUE模式下，管道失败后需要回滚副作用，避免文本处于部分修改状态。
    """

    original_text: str
    original_metadata: Dict[str, Any]
    rollback_actions: List[Callable] = field(default_factory=list)

    def add_rollback(self, action: Callable) -> None:
        """
        添加回滚动作

        Args:
            action: 回滚函数，可以是同步或异步函数
        """
        self.rollback_actions.append(action)

    async def rollback(self) -> None:
        """
        执行所有回滚动作（逆序执行）

        回滚动作按添加的逆序执行，确保正确的状态恢复。
        """
        logger = get_logger("PipelineContext")
        for action in reversed(self.rollback_actions):
            try:
                if asyncio.iscoroutinefunction(action):
                    await action()
                else:
                    action()
            except Exception as e:
                # 回滚失败不应该中断整体回滚流程
                logger.warning(f"回滚动作执行失败: {e}", exc_info=True)


# ==================== TextPipeline 接口（新架构） ====================


class PipelineErrorHandling(str, Enum):
    """Pipeline 错误处理策略"""

    CONTINUE = "continue"  # 记录日志，继续执行下一个 Pipeline
    STOP = "stop"  # 停止执行，抛出异常
    DROP = "drop"  # 丢弃消息，不执行后续 Pipeline


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

    用于在 InputDomain (RawData → NormalizedMessage) 中处理文本，
    如限流、敏感词过滤、文本清理、相似消息过滤等。

    位置：
        - InputDomain.normalize() 方法内部调用
        - 在创建 NormalizedMessage 之前对文本进行预处理
        - 可返回 None 表示丢弃该消息

    问题#4修复：process() 方法现在可以接收可选的 PipelineContext 参数，
    用于在管道失败时支持回滚操作。
    """

    priority: int
    enabled: bool
    error_handling: PipelineErrorHandling
    timeout_seconds: float

    async def process(
        self, text: str, metadata: Dict[str, Any], context: Optional["PipelineContext"] = None
    ) -> Optional[str]:
        """
        处理文本

        Args:
            text: 待处理的文本
            metadata: 元数据（如 user_id、source 等）
            context: 可选的执行上下文，用于支持回滚（问题#4新增）

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

    问题#4修复：process() 方法现在可以接收可选的 PipelineContext 参数，
    子类可以通过 context.add_rollback() 注册回滚动作。
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

    async def process(
        self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        """
        处理文本（包装 _process 并记录统计）

        问题#4修复：添加可选的 context 参数，传递给子类的 _process() 方法。

        Args:
            text: 待处理的文本
            metadata: 元数据
            context: 可选的执行上下文，用于支持回滚

        Returns:
            处理后的文本，或 None 表示丢弃
        """
        start_time = time.time()
        try:
            result = await self._process(text, metadata, context)
            duration_ms = (time.time() - start_time) * 1000
            self._stats.processed_count += 1
            self._stats.total_duration_ms += duration_ms
            return result
        except Exception:
            self._stats.error_count += 1
            raise

    @abstractmethod
    async def _process(
        self, text: str, metadata: Dict[str, Any], context: Optional[PipelineContext] = None
    ) -> Optional[str]:
        """
        实际处理文本（子类实现）

        问题#4修复：添加可选的 context 参数，子类可以使用 context.add_rollback()
        注册回滚动作，以便在管道失败时恢复状态。

        Args:
            text: 待处理的文本
            metadata: 元数据
            context: 可选的执行上下文，用于支持回滚

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


class InputPipelineManager:
    """
    输入管道管理器，负责 TextPipeline 的加载、排序和执行。

    TextPipeline 位置（3域架构）：
        - InputDomain: 在 RawData → NormalizedMessage 转换中处理文本
        - 调用点: TextNormalizer.normalize()
        - 功能: 限流、相似文本过滤、敏感词过滤等预处理

    架构：
        - 只管理 TextPipeline 类型的管道
        - 按 priority 顺序执行（数值小优先）
        - 支持启用/禁用、错误处理、超时控制
    """

    def __init__(self, core=None):
        # TextPipeline 列表
        self._text_pipelines: List[TextPipeline] = []
        self._text_pipelines_sorted: bool = True
        self._text_pipeline_lock = asyncio.Lock()

        self.logger = get_logger("InputPipelineManager")
        # 注意：完全移除 self.core = core（TextPipeline 不使用）

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

        这是 InputDomain (Input Domain: 输入域) 中的文本预处理入口点。
        在 RawData → NormalizedMessage 转换过程中调用。

        问题#4修复：使用 PipelineContext 支持回滚，防止管道失败时数据损坏。

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

            # 创建执行上下文，保存原始状态用于回滚
            context = PipelineContext(original_text=text, original_metadata=metadata.copy())
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
                        pipeline.process(current_text, metadata, context),
                        timeout=pipeline.timeout_seconds,
                    )
                    current_text = result

                    duration_ms = (time.time() - start_time) * 1000

                    # 如果返回 None，丢弃消息
                    if current_text is None:
                        self.logger.debug(f"TextPipeline {pipeline_name} 丢弃了消息 (耗时 {duration_ms:.2f}ms)")
                        stats = pipeline.get_stats()
                        stats.dropped_count += 1
                        # DROP模式：回滚所有副作用
                        await context.rollback()
                        return None

                    self.logger.debug(f"TextPipeline {pipeline_name} 处理完成 (耗时 {duration_ms:.2f}ms)")

                except asyncio.TimeoutError as timeout_error:
                    error = PipelineException(pipeline_name, f"处理超时 ({pipeline.timeout_seconds}s)")
                    self.logger.error(f"TextPipeline 超时: {error}")

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        # STOP模式：回滚所有副作用后抛出异常
                        await context.rollback()
                        raise error from timeout_error
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        # DROP模式：回滚所有副作用后丢弃消息
                        stats.dropped_count += 1
                        await context.rollback()
                        return None
                    else:
                        # CONTINUE模式：回滚当前管道的副作用，使用原始文本继续
                        await context.rollback()
                        current_text = context.original_text

                except PipelineException:
                    # PipelineException 直接向上抛出
                    raise

                except Exception as e:
                    error = PipelineException(pipeline_name, f"处理失败: {e}", original_error=e)
                    self.logger.error(f"TextPipeline 错误: {error}", exc_info=True)

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        # STOP模式：回滚所有副作用后抛出异常
                        await context.rollback()
                        raise error from e
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        # DROP模式：回滚所有副作用后丢弃消息
                        stats.dropped_count += 1
                        await context.rollback()
                        return None
                    else:
                        # CONTINUE模式：回滚当前管道的副作用，使用原始文本继续
                        await context.rollback()
                        current_text = context.original_text

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

    # ==================== TextPipeline 加载 ====================

    async def load_text_pipelines(
        self,
        pipeline_base_dir: str = "src/domains/input/pipelines",
        root_config_pipelines_section: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        扫描并加载 TextPipeline 类型的管道。

        TextPipeline 用于 InputDomain (Input Domain) 中的文本预处理。
        与 MessagePipeline 不同，TextPipeline 不区分 inbound/outbound，统一按 priority 顺序执行。

        3域架构中的位置：
            - InputDomain.normalize() 方法内部调用
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
