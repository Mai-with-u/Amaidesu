"""
输出管道管理器 - Output Domain 参数预处理管道管理

管理 OutputPipeline 的加载、排序和执行。

3域架构中的位置：
- OutputDomain: 在 ExpressionParameters → OutputProvider 转换中处理参数
- 调用点: FlowCoordinator._on_intent_ready() 方法
- 功能: 敏感词过滤、参数验证、参数转换等
"""

import asyncio
import importlib
import inspect
import os
import sys
import time
from typing import Any, Dict, List, Optional

from src.core.utils.logger import get_logger
from src.core.utils.config import load_component_specific_config, merge_component_configs
from src.domains.output.parameters.render_parameters import ExpressionParameters
from .base import OutputPipeline, PipelineErrorHandling, PipelineException


class OutputPipelineManager:
    """
    输出管道管理器

    负责 OutputPipeline 的加载、排序和执行。

    3域架构中的位置：
        - OutputDomain: 在 ExpressionParameters → OutputProvider 转换中处理参数
        - 调用点: FlowCoordinator._on_intent_ready() 方法
        - 功能: 敏感词过滤、参数验证、参数转换等
    """

    def __init__(self):
        self._pipelines: List[OutputPipeline] = []
        self._pipelines_sorted: bool = True
        self._pipeline_lock = asyncio.Lock()
        self.logger = get_logger("OutputPipelineManager")

    def register_pipeline(self, pipeline: OutputPipeline) -> None:
        """
        注册一个 OutputPipeline

        Args:
            pipeline: OutputPipeline 实例
        """
        self._pipelines.append(pipeline)
        self._pipelines_sorted = False

        info = pipeline.get_info()
        self.logger.info(
            f"OutputPipeline 已注册: {info['name']} (priority={info['priority']}, enabled={info['enabled']})"
        )

    def _ensure_pipelines_sorted(self) -> None:
        """确保 Pipeline 列表按优先级排序"""
        if not self._pipelines_sorted:
            self._pipelines.sort(key=lambda p: p.priority)
            self._pipelines_sorted = True
            pipe_info = ", ".join([f"{p.get_info()['name']}({p.priority})" for p in self._pipelines])
            self.logger.debug(f"OutputPipeline 已排序: {pipe_info}")

    async def process(self, params: ExpressionParameters) -> Optional[ExpressionParameters]:
        """
        按优先级顺序通过所有启用的 OutputPipeline 处理参数

        这是 OutputDomain (Output Domain) 中的参数预处理入口点。
        在 ExpressionGenerator.generate() 之后、发布事件之前调用。

        Args:
            params: 待处理的 ExpressionParameters

        Returns:
            处理后的 ExpressionParameters，如果任何 Pipeline 返回 None 则返回 None（表示丢弃）

        Raises:
            PipelineException: 当某个 Pipeline 错误处理策略为 STOP 时抛出
        """
        if not self._pipelines:
            return params  # 没有 OutputPipeline，直接返回原参数

        async with self._pipeline_lock:
            self._ensure_pipelines_sorted()

            current_params = params

            for pipeline in self._pipelines:
                if not pipeline.enabled:
                    continue

                info = pipeline.get_info()
                pipeline_name = info["name"]

                try:
                    # 记录开始时间
                    start_time = time.time()

                    # 带超时的处理（current_params 在此处保证非 None，因为 None 会提前返回）
                    assert current_params is not None
                    result = await asyncio.wait_for(
                        pipeline.process(current_params),
                        timeout=pipeline.timeout_seconds,
                    )
                    current_params = result

                    duration_ms = (time.time() - start_time) * 1000

                    # 如果返回 None，丢弃输出
                    if current_params is None:
                        self.logger.debug(f"OutputPipeline {pipeline_name} 丢弃了输出 (耗时 {duration_ms:.2f}ms)")
                        stats = pipeline.get_stats()
                        stats.dropped_count += 1
                        return None

                    self.logger.debug(f"OutputPipeline {pipeline_name} 处理完成 (耗时 {duration_ms:.2f}ms)")

                except asyncio.TimeoutError as timeout_error:
                    error = PipelineException(pipeline_name, f"处理超时 ({pipeline.timeout_seconds}s)")
                    self.logger.error(f"OutputPipeline 超时: {error}")

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
                    self.logger.error(f"OutputPipeline 错误: {error}", exc_info=True)

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        raise error from e
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续执行下一个 Pipeline

            return current_params

    async def process_parameters(self, parameters, metadata: Dict[str, Any]) -> Optional:
        """
        按优先级顺序通过所有启用的 OutputPipeline 处理参数

        这是 OutputDomain (Output Domain) 中的参数预处理入口点。
        在 FlowCoordinator._on_intent_ready() 方法中调用。

        Args:
            parameters: 待处理的 ExpressionParameters
            metadata: 元数据（如 intent、source 等）

        Returns:
            处理后的 ExpressionParameters，如果任何 Pipeline 返回 None 则返回 None（表示丢弃）

        Raises:
            PipelineException: 当某个 Pipeline 错误处理策略为 STOP 时抛出
        """
        if not self._pipelines:
            return parameters  # 没有 OutputPipeline，直接返回原参数

        async with self._pipeline_lock:
            self._ensure_pipelines_sorted()

            current_parameters = parameters

            for pipeline in self._pipelines:
                if not pipeline.enabled:
                    continue

                info = pipeline.get_info()
                pipeline_name = info["name"]

                try:
                    # 记录开始时间
                    start_time = time.time()

                    # 带超时的处理（current_parameters 在此处保证非 None，因为 None 会提前返回）
                    assert current_parameters is not None
                    result = await asyncio.wait_for(
                        pipeline.process(current_parameters, metadata),
                        timeout=pipeline.timeout_seconds,
                    )
                    current_parameters = result

                    duration_ms = (time.time() - start_time) * 1000

                    # 如果返回 None，丢弃消息
                    if current_parameters is None:
                        self.logger.debug(f"OutputPipeline {pipeline_name} 丢弃了消息 (耗时 {duration_ms:.2f}ms)")
                        stats = pipeline.get_stats()
                        stats.dropped_count += 1
                        return None

                    self.logger.debug(f"OutputPipeline {pipeline_name} 处理完成 (耗时 {duration_ms:.2f}ms)")

                except asyncio.TimeoutError as timeout_error:
                    error = PipelineException(pipeline_name, f"处理超时 ({pipeline.timeout_seconds}s)")
                    self.logger.error(f"OutputPipeline 超时: {error}")

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
                    self.logger.error(f"OutputPipeline 错误: {error}", exc_info=True)

                    stats = pipeline.get_stats()
                    stats.error_count += 1

                    if pipeline.error_handling == PipelineErrorHandling.STOP:
                        raise error from e
                    elif pipeline.error_handling == PipelineErrorHandling.DROP:
                        stats.dropped_count += 1
                        return None
                    # CONTINUE: 继续执行下一个 Pipeline

            return current_parameters

    def get_pipeline_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有 OutputPipeline 的统计信息

        Returns:
            {pipeline_name: {processed_count, dropped_count, error_count, avg_duration_ms}}
        """
        result = {}
        for pipeline in self._pipelines:
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

    async def load_output_pipelines(
        self,
        pipeline_base_dir: str = "src/domains/output/pipelines",
        root_config_pipelines_section: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        扫描并加载 OutputPipeline 类型的管道。

        OutputPipeline 用于 OutputDomain (Output Domain) 中的参数预处理。

        3域架构中的位置：
            - FlowCoordinator._on_intent_ready() 方法内部调用
            - 在 ExpressionGenerator.generate() 之后处理参数
            - 示例：敏感词过滤、参数验证、参数转换

        Args:
            pipeline_base_dir: 管道包的基础目录，默认为 "src/domains/output/pipelines"
            root_config_pipelines_section: 根配置文件中 'pipelines' 部分的字典
        """
        self.logger.info(f"开始从目录加载 OutputPipeline: {pipeline_base_dir}")
        pipeline_dir_abs = os.path.abspath(pipeline_base_dir)

        if not os.path.isdir(pipeline_dir_abs):
            self.logger.warning(f"管道目录不存在: {pipeline_base_dir}，跳过 OutputPipeline 加载。")
            return

        # 将 src 目录添加到 sys.path（如果尚未添加）
        src_dir = os.path.dirname(pipeline_dir_abs)
        domains_dir = os.path.dirname(src_dir)
        if domains_dir not in sys.path:
            sys.path.insert(0, domains_dir)
            self.logger.debug(f"已将目录添加到 sys.path: {domains_dir}")

        if root_config_pipelines_section is None:
            root_config_pipelines_section = {}
            self.logger.warning("未提供根配置中的 'pipelines' 部分，所有 OutputPipeline 将无法加载。")
            return

        loaded_pipeline_count = 0

        # 遍历根配置中定义的管道
        for pipeline_name_snake, pipeline_global_settings in root_config_pipelines_section.items():
            if not isinstance(pipeline_global_settings, dict):
                self.logger.warning(f"管道 '{pipeline_name_snake}' 在根配置中的条目格式不正确 (应为字典), 跳过。")
                continue

            # 检查是否有 output 子配置（用于区分 input/output pipelines）
            if "output" not in pipeline_global_settings:
                # 如果没有 output 子配置，跳过（可能是 input pipeline）
                continue

            output_settings = pipeline_global_settings["output"]
            if not isinstance(output_settings, dict):
                self.logger.warning(f"管道 '{pipeline_name_snake}' 的 'output' 配置格式不正确 (应为字典), 跳过。")
                continue

            priority = output_settings.get("priority")
            if not isinstance(priority, int):
                self.logger.debug(f"管道 '{pipeline_name_snake}' 在根配置中 'output.priority' 缺失或无效，跳过。")
                continue

            # 检查是否启用
            enabled = output_settings.get("enabled", True)
            if not enabled:
                self.logger.debug(f"管道 '{pipeline_name_snake}' 已禁用，跳过加载。")
                continue

            pipeline_package_path = os.path.join(pipeline_dir_abs, pipeline_name_snake)

            # 检查管道目录结构
            if not (
                os.path.isdir(pipeline_package_path)
                and os.path.exists(os.path.join(pipeline_package_path, "__init__.py"))
                and os.path.exists(os.path.join(pipeline_package_path, "pipeline.py"))
            ):
                continue

            # 提取全局覆盖配置（排除 'priority'、'enabled' 和 'output' 键）
            global_override_config = {
                k: v for k, v in output_settings.items() if k not in ["priority", "enabled", "output"]
            }

            # 加载管道自身的独立配置
            pipeline_specific_config = load_component_specific_config(
                pipeline_package_path, pipeline_name_snake, "管道"
            )

            # 合并配置：全局覆盖配置优先
            final_pipeline_config = merge_component_configs(
                pipeline_specific_config, global_override_config, pipeline_name_snake, "管道"
            )

            # 导入并查找 OutputPipelineBase 子类
            try:
                module_import_path = f"src.domains.output.pipelines.{pipeline_name_snake}.pipeline"
                self.logger.debug(f"尝试导入管道模块: {module_import_path}")
                module = importlib.import_module(module_import_path)

                expected_class_name = (
                    "".join(word.title() for word in pipeline_name_snake.split("_")) + "OutputPipeline"
                )
                pipeline_class = None

                # 查找 OutputPipelineBase 子类
                for name, obj in inspect.getmembers(module):
                    if (
                        inspect.isclass(obj)
                        and hasattr(obj, "__bases__")
                        and any(base.__name__ == "OutputPipelineBase" for base in obj.__bases__)
                    ):
                        if name == expected_class_name:
                            pipeline_class = obj
                            break

                if pipeline_class:
                    # 实例化管道
                    pipeline_instance = pipeline_class(config=final_pipeline_config)
                    pipeline_instance.priority = priority

                    # 注册到 OutputPipeline 列表
                    self.register_pipeline(pipeline_instance)
                    loaded_pipeline_count += 1
                else:
                    self.logger.debug(f"模块 '{module_import_path}' 中未找到 OutputPipeline，跳过。")

            except ImportError as e:
                self.logger.error(f"导入管道模块 '{module_import_path}' 失败: {e}", exc_info=True)
            except Exception as e:
                self.logger.error(f"加载 OutputPipeline '{pipeline_name_snake}' 时发生错误: {e}", exc_info=True)

        if loaded_pipeline_count > 0:
            self.logger.info(f"OutputPipeline 加载完成，共加载 {loaded_pipeline_count} 个管道。")
        else:
            self.logger.info("未加载任何 OutputPipeline。")
