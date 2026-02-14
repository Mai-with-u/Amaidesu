"""
输出管道管理器 - Output Domain Intent 处理管道管理

管理 OutputPipeline 的加载、排序和执行。

3域架构中的位置：
- OutputDomain: 在 Intent → OutputProvider 转换中处理 Intent
- 调用点: OutputProviderManager._on_decision_intent() 方法
- 功能: 敏感词过滤、Intent 验证、Intent 转换等
"""

import asyncio
import importlib
import inspect
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.modules.logging import get_logger

from .base import OutputPipeline, PipelineErrorHandling, PipelineException

# 类型检查时的导入
if TYPE_CHECKING:
    from src.modules.types import Intent


class OutputPipelineManager:
    """
    输出管道管理器

    负责 OutputPipeline 的加载、排序和执行。

    3域架构中的位置：
        - OutputDomain: 在 Intent → OutputProvider 转换中处理 Intent
        - 调用点: OutputProviderManager._on_decision_intent() 方法
        - 功能: 敏感词过滤、Intent 验证、Intent 转换等
    """

    def __init__(self):
        self._pipelines: List[OutputPipeline] = []
        self._pipelines_sorted: bool = True
        self._pipeline_lock = asyncio.Lock()
        self.logger = get_logger("OutputPipelineManager")

    def register_pipeline(self, pipeline: OutputPipeline) -> None:
        """注册一个 OutputPipeline"""
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

    async def process(self, intent: "Intent") -> Optional["Intent"]:
        """
        按优先级顺序通过所有启用的 OutputPipeline 处理 Intent

        这是 OutputDomain 中的 Intent 预处理入口点。
        在 OutputProviderManager._on_decision_intent() 中调用。

        Args:
            intent: 待处理的 Intent

        Returns:
            处理后的 Intent，如果任何 Pipeline 返回 None 则返回 None（表示丢弃）

        Raises:
            PipelineException: 当某个 Pipeline 错误处理策略为 STOP 时抛出
        """
        if not self._pipelines:
            return intent  # 没有 OutputPipeline，直接返回原 Intent

        async with self._pipeline_lock:
            self._ensure_pipelines_sorted()

            current_intent = intent

            for pipeline in self._pipelines:
                if not pipeline.enabled:
                    continue

                info = pipeline.get_info()
                pipeline_name = info["name"]

                try:
                    start_time = time.time()

                    # 带超时的处理
                    assert current_intent is not None
                    result = await asyncio.wait_for(
                        pipeline.process(current_intent),
                        timeout=pipeline.timeout_seconds,
                    )
                    current_intent = result

                    duration_ms = (time.time() - start_time) * 1000

                    # 如果返回 None，丢弃输出
                    if current_intent is None:
                        self.logger.debug(f"OutputPipeline {pipeline_name} 丢弃了 Intent (耗时 {duration_ms:.2f}ms)")
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

            return current_intent

    def get_pipeline_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 OutputPipeline 的统计信息"""
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

        OutputPipeline 用于 OutputDomain 中的 Intent 预处理。

        Args:
            pipeline_base_dir: 管道包的基础目录
            root_config_pipelines_section: 根配置文件中 'pipelines' 部分的字典
        """
        self.logger.info(f"开始从目录加载 OutputPipeline: {pipeline_base_dir}")
        pipeline_dir_abs = os.path.abspath(pipeline_base_dir)

        if not os.path.isdir(pipeline_dir_abs):
            self.logger.warning(f"管道目录不存在: {pipeline_base_dir}，跳过 OutputPipeline 加载。")
            return

        if root_config_pipelines_section is None:
            root_config_pipelines_section = {}
            self.logger.debug("未提供 Pipeline 配置，跳过加载。")
            return

        loaded_pipeline_count = 0

        # 遍历根配置中定义的管道
        for pipeline_name_snake, pipeline_global_settings in root_config_pipelines_section.items():
            if not isinstance(pipeline_global_settings, dict):
                continue

            # 检查是否有 output 子配置
            if "output" not in pipeline_global_settings:
                continue

            output_settings = pipeline_global_settings["output"]
            if not isinstance(output_settings, dict):
                continue

            priority = output_settings.get("priority")
            if not isinstance(priority, int):
                continue

            enabled = output_settings.get("enabled", True)
            if not enabled:
                continue

            pipeline_package_path = os.path.join(pipeline_dir_abs, pipeline_name_snake)

            if not (
                os.path.isdir(pipeline_package_path)
                and os.path.exists(os.path.join(pipeline_package_path, "__init__.py"))
                and os.path.exists(os.path.join(pipeline_package_path, "pipeline.py"))
            ):
                continue

            # 直接使用主配置中的管道配置
            final_pipeline_config = {k: v for k, v in output_settings.items() if k not in ["priority", "enabled"]}

            try:
                module_import_path = f"src.domains.output.pipelines.{pipeline_name_snake}.pipeline"
                self.logger.debug(f"尝试导入管道模块: {module_import_path}")
                module = importlib.import_module(module_import_path)

                expected_class_name = "".join(word.title() for word in pipeline_name_snake.split("_")) + "Pipeline"
                pipeline_class = None

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
                    pipeline_instance = pipeline_class(config=final_pipeline_config)
                    pipeline_instance.priority = priority
                    self.register_pipeline(pipeline_instance)
                    loaded_pipeline_count += 1
                else:
                    self.logger.debug(f"模块中未找到 OutputPipeline: {module_import_path}")

            except ImportError as e:
                self.logger.error(f"导入管道模块失败: {module_import_path} - {e}", exc_info=True)
            except Exception as e:
                self.logger.error(f"加载 OutputPipeline 时发生错误: {pipeline_name_snake} - {e}", exc_info=True)

        if loaded_pipeline_count > 0:
            self.logger.info(f"OutputPipeline 加载完成，共加载 {loaded_pipeline_count} 个管道。")
        else:
            self.logger.info("未加载任何 OutputPipeline。")
