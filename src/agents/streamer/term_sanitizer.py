"""注入边界术语脱敏——minecraft 侧状态进入 streamer 注入链时去掉内部标识噪音。

游戏任务号、观测引用、目标编号、能力名、图纸编号这类标识符是 minecraft Agent 内部的检索地址，进入主播的
LLM 上下文对决策没有信息量（同一执行者手上只有最近一个任务、观察原文已随正文
给出），只会被模型原样念给观众。仅靠表达侧提示词的"不念内部术语"拦截
不可靠，本模块把转换前移到注入构造层：标识符在边界处即替换为人类可读描述，同时保留
对决策有用的信息（任务在进行、观察存在），只去编号噪音。
"""

from __future__ import annotations

import re

__all__ = ["sanitize_internal_terms"]


def _goal_result(match: "re.Match[str]") -> str:
    """进展备注抬头 → 中文结果加冒号（"没做成："），编号与能力名不留。"""
    status = match.group("status") or ""
    return f"{_GOAL_RESULT_WORDS.get(status, _GOAL_RESULT_UNKNOWN)}："


# 委派任务号（如 deleg_1791384781841_1）：数字段长度不定，按前缀整体匹配
_TASK_ID_PATTERN = re.compile(r"deleg_[0-9]+(?:_[0-9]+)*")
# 观测引用（如 obs_f794817a_fc2e9d31aabbccdd）：scope 与摘要段都是十六进制
_OBS_REF_PATTERN = re.compile(r"obs_[0-9a-f]{6,}(?:_[0-9a-f]{6,})*")
# 物品/方块的命名空间噪音前缀（如 simulated:honey_glue → honey_glue）：
# "simulated:" 是数据管道标记，物品本名保留给模型与观众
_SIMULATED_PREFIX_PATTERN = re.compile(r"\bsimulated:")

# 游戏侧进展备注的抬头（如"游戏目标 550（maicraft:design）done："）：目标编号和能力名是游戏那一面的
# 记账地址，去掉；结果留着、换成中文——整段删掉的话，没做成、取消了的一步读起来就像做成了
_GOAL_HEADER_PATTERN = re.compile(r"游戏目标\s*\d+\s*[（(][^）)]*[）)]\s*(?P<status>[a-z_]+|结束)?\s*[：:]\s*")
# 跟踪的目标查不到了（多半是换了世界）："游戏目标 12 不在了：原因"
_GOAL_GONE_PATTERN = re.compile(r"游戏目标\s*\d+\s*不在了\s*[：:]\s*")
# 目标结果 → 她看得懂的说法；Mod 的 done / partial / failed / cancelled 与委派账的 succeeded / stalled，
# 没写结果（游戏那边记成"结束"）或不认识的一律说"结束了"
_GOAL_RESULT_WORDS = {
    "done": "做完了",
    "succeeded": "做完了",
    "partial": "做了一半",
    "failed": "没做成",
    "cancelled": "取消了",
    "stalled": "停着没动",
}
_GOAL_RESULT_UNKNOWN = "结束了"
_GOAL_GONE_WORDS = "这一步没了"
# 步骤里打头的能力名（如"第 1 步（maicraft:obtain，从箱子取海带）"）：括号里只留这一步在做什么。
# 只认能力的 maicraft: 前缀，物品 ID（"拿到了（minecraft:iron_ingot，6 个）"）不碰，否则物品名就没了
_STEP_ABILITY_PATTERN = re.compile(r"(?<=[（(])maicraft:[a-z][a-z0-9_]*[，,]\s*")
# 图纸、存档一类的 UUID（如"存成图纸 0a58eb9a-aedc-…"）：念出来是一串乱码
_UUID_PATTERN = re.compile(r"\s*\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")

_TASK_REPLACEMENT = "一个游戏任务"
_OBS_REPLACEMENT = "一次现场观察"


def sanitize_internal_terms(text: str) -> str:
    """把注入文本中的内部标识符替换为人类可读描述；无匹配时原样返回。"""
    if not text:
        return text
    sanitized = _TASK_ID_PATTERN.sub(_TASK_REPLACEMENT, text)
    sanitized = _OBS_REF_PATTERN.sub(_OBS_REPLACEMENT, sanitized)
    sanitized = _SIMULATED_PREFIX_PATTERN.sub("", sanitized)
    # 目标编号、能力名、UUID：第 58 场"游戏目标 550（maicraft:design）done：……存成图纸 0a58…"
    # 原样进了【我手头在游戏里做的事】，她跟着念了一晚上"又先量一遍、画图纸"
    sanitized = _GOAL_HEADER_PATTERN.sub(_goal_result, sanitized)
    sanitized = _GOAL_GONE_PATTERN.sub(f"{_GOAL_GONE_WORDS}：", sanitized)
    sanitized = _STEP_ABILITY_PATTERN.sub("", sanitized)
    sanitized = _UUID_PATTERN.sub("", sanitized)
    return sanitized
