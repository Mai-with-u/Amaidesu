"""注入边界术语脱敏——minecraft 侧状态进入 streamer 注入链时去掉内部标识噪音。

游戏任务号、观测引用这类标识符是 minecraft Agent 内部的检索地址，进入主播的
LLM 上下文对决策没有信息量（同一执行者手上只有最近一个任务、观察原文已随正文
给出），只会被模型原样念给观众。此前防线押在表达侧提示词的"不念内部术语"上，
本模块把转换前移到注入构造层：标识符在边界处即替换为人类可读描述，同时保留
对决策有用的信息（任务在进行、观察存在），只去编号噪音。
"""

from __future__ import annotations

import re

__all__ = ["sanitize_internal_terms"]

# 委派任务号（如 deleg_1791384781841_1）：数字段长度不定，按前缀整体匹配
_TASK_ID_PATTERN = re.compile(r"deleg_[0-9]+(?:_[0-9]+)*")
# 观测引用（如 obs_f794817a_fc2e9d31aabbccdd）：scope 与摘要段都是十六进制
_OBS_REF_PATTERN = re.compile(r"obs_[0-9a-f]{6,}(?:_[0-9a-f]{6,})*")
# 物品/方块的命名空间噪音前缀（如 simulated:honey_glue → honey_glue）：
# "simulated:" 是数据管道标记，物品本名保留给模型与观众
_SIMULATED_PREFIX_PATTERN = re.compile(r"\bsimulated:")

_TASK_REPLACEMENT = "一个游戏任务"
_OBS_REPLACEMENT = "一次现场观察"


def sanitize_internal_terms(text: str) -> str:
    """把注入文本中的内部标识符替换为人类可读描述；无匹配时原样返回。"""
    if not text:
        return text
    sanitized = _TASK_ID_PATTERN.sub(_TASK_REPLACEMENT, text)
    sanitized = _OBS_REF_PATTERN.sub(_OBS_REPLACEMENT, sanitized)
    sanitized = _SIMULATED_PREFIX_PATTERN.sub("", sanitized)
    return sanitized
