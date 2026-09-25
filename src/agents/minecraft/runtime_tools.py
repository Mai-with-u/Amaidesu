"""Minecraft 玩家在任务内读取工作证据、让出执行的局部工具声明。"""

from src.modules.tools.models import ToolSpec


def build_observation_spec() -> ToolSpec:
    """给模型一个只读历史证据的入口，补读正文不需要再次向游戏查询同一资料。"""
    return ToolSpec(
        name="observation",
        provider="minecraft",
        kind="sync",
        description=(
            "读取本任务已经取得的观察。省略 ref 分页列出证据索引，可用 query 搜索全部历史的来源或请求。"
            "指定 ref 后用 path（JSON Pointer）选字段；正文默认每页 4000 字符，索引默认 20 项，复制 next_offset 续页。"
            "source=request 可找回完整输入。Mod 的 omitted/detail_path/resource_uri 引用需按对应的 Mod 入口补读。"
            "这是历史证据，不证明当前库存、位置或现场仍未变化；需要刷新时正常调用 Mod。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "观察返回的原文引用"},
                "source": {
                    "type": "string",
                    "enum": ["result", "request"],
                    "default": "result",
                    "description": "读取原始结果或当时的完整工具参数",
                },
                "path": {"type": "string", "default": "", "description": "JSON Pointer，如 /data/content"},
                "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 16000,
                    "description": "正文最多 16000 字符；索引最多 50 项。省略采用小页。",
                },
            },
            "additionalProperties": False,
        },
    )


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
