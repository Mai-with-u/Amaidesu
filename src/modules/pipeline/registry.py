"""Pipeline 装饰器与注册表。

提供 @pipeline("name") 装饰器，stage 从基类自动推断。
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Dict, Tuple, Type

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.pipeline.base import Pipeline


logger = get_logger("PipelineRegistry")


# 全局注册表：key = (stage, name), value = Pipeline 类
PIPELINE_REGISTRY: Dict[Tuple[str, str], Type["Pipeline"]] = {}


class PipelineRegistrationError(Exception):
    """Pipeline 注册错误（重复注册、ABC、stage 推断失败等）。"""


def pipeline(name: str):
    """声明一个 Pipeline。stage 从基类自动推断。

    Args:
        name: Pipeline 注册名（在配置中使用的 key）

    Returns:
        装饰器函数

    Raises:
        PipelineRegistrationError:
            - 类未继承 Pipeline
            - 无法推断 stage（基类不是 InputPipeline 或 OutputPipeline）
            - 同一 (stage, name) 已注册

    Example:
        >>> from src.modules.pipeline import pipeline
        >>> @pipeline("rate_limit")
        ... class RateLimitInputPipeline(InputPipeline):
        ...     async def _process(self, msg):
        ...         return msg
    """

    def decorator(cls: Type["Pipeline"]) -> Type["Pipeline"]:
        from src.modules.pipeline.base import Pipeline

        if not inspect.isclass(cls):
            raise PipelineRegistrationError(f"@pipeline 只能装饰类，不能装饰 {type(cls).__name__}")

        if not isinstance(cls, type) or not issubclass(cls, Pipeline):
            raise PipelineRegistrationError(f"{cls.__name__} 必须继承 Pipeline（或 InputPipeline/OutputPipeline 子类）")

        # 跳过抽象类
        if inspect.isabstract(cls):
            logger.debug(f"跳过抽象类注册: {cls.__name__}")
            return cls

        # 从继承链推断 stage
        stage = _infer_stage(cls)
        if stage is None:
            raise PipelineRegistrationError(
                f"{cls.__name__} 必须继承 InputPipeline 或 OutputPipeline 才能推断 stage；"
                f"当前基类: {[b.__name__ for b in cls.__bases__]}"
            )

        # 检查重复注册
        key = (stage, name)
        if key in PIPELINE_REGISTRY:
            existing = PIPELINE_REGISTRY[key].__name__
            raise PipelineRegistrationError(
                f"Pipeline '{name}' 已在 stage='{stage}' 注册为 {existing}，不能重复注册为 {cls.__name__}"
            )

        PIPELINE_REGISTRY[key] = cls
        cls._pipeline_name = name  # type: ignore[attr-defined]
        cls._pipeline_stage = stage  # type: ignore[attr-defined]
        logger.info(f"Pipeline 已注册: ({stage}, {name}) -> {cls.__name__}")
        return cls

    return decorator


def _infer_stage(cls: Type) -> str | None:
    """从类继承链推断 stage。支持 InputPipeline/OutputPipeline 别名或 Pipeline[T] 直接继承。"""
    import typing
    from src.modules.types.base.normalized_message import NormalizedMessage
    from src.modules.types.intent import Intent

    for base in cls.__mro__:
        stage = getattr(base, "_stage_marker", None)
        if stage in ("input", "output"):
            return stage

    orig_bases = getattr(cls, "__orig_bases__", ())
    for orig_base in orig_bases:
        args = typing.get_args(orig_base)
        for arg in args:
            if arg is NormalizedMessage:
                return "input"
            if arg is Intent:
                return "output"
            if isinstance(arg, typing.ForwardRef):
                ref_name = arg.__forward_arg__
                if ref_name == "NormalizedMessage":
                    return "input"
                if ref_name == "Intent":
                    return "output"

    return None


def clear_registry() -> None:
    """清空注册表。仅供测试使用。"""
    PIPELINE_REGISTRY.clear()


# 阶段特化别名（用于 _infer_stage 识别）
class _StageMarker:
    """标记类的 stage，供装饰器推断使用。"""

    def __init__(self, stage: str):
        self.stage = stage


def _make_stage_alias(stage: str):
    """创建 InputPipeline 或 OutputPipeline 类型别名。

    这些别名有 _stage_marker 属性，供装饰器推断 stage。
    """
    from src.modules.pipeline.base import Pipeline

    if stage == "input":
        from src.modules.types.base.normalized_message import NormalizedMessage

        class InputPipeline(Pipeline[NormalizedMessage]):
            """Input 阶段 Pipeline。处理 NormalizedMessage。"""

            _stage_marker = "input"

        return InputPipeline
    elif stage == "output":
        from src.modules.types import Intent

        class OutputPipeline(Pipeline[Intent]):
            """Output 阶段 Pipeline。处理 Intent。"""

            _stage_marker = "output"

        return OutputPipeline
    else:
        raise ValueError(f"未知 stage: {stage}")


# 在模块级别创建别名
InputPipeline = _make_stage_alias("input")
OutputPipeline = _make_stage_alias("output")


__all__ = [
    "PIPELINE_REGISTRY",
    "PipelineRegistrationError",
    "pipeline",
    "clear_registry",
    "InputPipeline",
    "OutputPipeline",
]
