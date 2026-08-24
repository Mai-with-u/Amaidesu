"""文字冒险游戏内部状态（§1.31 内容状态内部自由）

按架构 §1.31 + §1.49 第 4 面定案：
- 内容特有状态（剧情节点 / 选项列表 / 历史栈）= Agent 包内部自由
- 框架**不**为内容状态提供 ORM / 数据库表（仅按 Agent 名字空间隔离状态写入口）
- 其它 Agent（包括主播 Agent）通过 ``game.*`` 事件或主动 query 工具读

本模块只实现 Wave 7 范式验证所需的最小状态机：
- 剧情段（``scene_id`` / ``scene_text``）
- 选项列表（``options``：id → label）
- 历史（最近 N 步选择）
- 上次感知哈希（去重）
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class TextAdvOption:
    """文字冒险游戏的一个可选选项"""

    option_id: str
    label: str
    # 选项触发的内容引擎输入（kind + payload）
    # 简化示例：默认 kind="key" key="enter"（推进剧情）；具体游戏可扩展
    advance_kind: str = "key"
    advance_key: str = "enter"
    advance_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TextAdvGameAgentState:
    """文字冒险游戏 Agent 内部状态（§1.31 内容状态）

    Attributes:
        scene_id: 当前剧情段 ID
        scene_text: 当前剧情文本
        options: 当前可选列表
        history: 已选历史（每条 = option_id）
        last_screen_hash: 上次感知到的屏幕文本哈希（去重）
        last_decision: 上次决策（测试可断言）
    """

    scene_id: str = "start"
    scene_text: str = ""
    options: List[TextAdvOption] = field(default_factory=list)
    history: List[str] = field(default_factory=list)
    last_screen_hash: str = ""
    last_decision: str = ""

    # ---- 操作 ----

    def apply_screen_text(self, text: str) -> bool:
        """应用新感知到的屏幕文本；返回是否真有变化（False = 重复跳过）。"""
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if h == self.last_screen_hash:
            return False
        self.last_screen_hash = h
        self.scene_text = text
        return True

    def set_options(self, options: List[TextAdvOption]) -> None:
        """更新当前选项（去重保留 id）。"""
        # 简化：直接替换；生产可保留旧选项 id 映射
        self.options = list(options)

    def pick_default_option(self) -> Optional[TextAdvOption]:
        """选默认选项（Wave 7 简化策略 = 第一个）。

        真实游戏 Agent 可用 LLM 做更聪明选择；本示例使用确定性策略以保证
        "perception → advance → loop" 在测试环境可断言。

        Returns:
            选中的选项；无选项时返回 None
        """
        if not self.options:
            return None
        chosen = self.options[0]
        self.last_decision = chosen.option_id
        self.history.append(chosen.option_id)
        return chosen

    def to_dict(self) -> Dict[str, Any]:
        """导出（测试 / 状态查询）。"""
        return {
            "scene_id": self.scene_id,
            "scene_text": self.scene_text,
            "options": [
                {
                    "option_id": o.option_id,
                    "label": o.label,
                    "advance_kind": o.advance_kind,
                    "advance_key": o.advance_key,
                }
                for o in self.options
            ],
            "history": list(self.history),
            "last_decision": self.last_decision,
        }


__all__ = ["TextAdvOption", "TextAdvGameAgentState"]
