"""按表域切分的存储仓储。

每个仓储持有 ``SQLiteConnectionManager``（构造注入），只暴露自己表域的
领域方法；消费者按需注入 1-3 个仓储，不经过任何宽门面。
"""

from __future__ import annotations

from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.chat import ChatRepo
from src.modules.storage.repos.events import EventRepo
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.repos.rundowns import RundownRepo
from src.modules.storage.repos.sessions import SessionRepo
from src.modules.storage.repos.sim import SimRepo
from src.modules.storage.repos.topics import TopicRepo
from src.modules.storage.repos.viewers import ViewerRepo

__all__ = [
    "BaseRepo",
    "ChatRepo",
    "EventRepo",
    "LLMRepo",
    "RundownRepo",
    "SessionRepo",
    "SimRepo",
    "TopicRepo",
    "ViewerRepo",
]
