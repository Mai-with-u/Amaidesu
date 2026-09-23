"""Minecraft 玩家在任务内读取工作证据、让出执行的局部工具声明。"""

from src.modules.tools.models import ToolSpec


def build_wait_spec() -> ToolSpec:
    """让模型结束本批推理，后台监控继续等待真实游戏进展。"""
    return ToolSpec(
        name="wait",
        provider="minecraft",
        kind="sync",
        description=(
            "当前没有其他可推进事项时，单独调用本工具让出执行，等待已登记的后台任务。"
            "宿主负责等待和超时检查；任务完成、失败、需要决策或新指令到达后恢复原任务。"
            "不要再用 attention 长轮询消耗推理轮数。已有待开工产物或待决策任务时先处理它们。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "reason": {"type": "string", "minLength": 1, "description": "说明当前需要等待的依赖"},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    )
