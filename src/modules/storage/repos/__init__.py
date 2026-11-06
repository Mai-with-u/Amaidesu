"""按表域切分的存储仓储。

每个仓储持有 ``SQLiteConnectionManager``（构造注入），只暴露自己表域的
领域方法；消费者按需注入 1-3 个仓储，不经过任何宽门面。
"""

from __future__ import annotations

from src.modules.storage.repos._base import BaseRepo
from src.modules.storage.repos.archive import ArchiveRepo
from src.modules.storage.repos.chat import ChatRepo
from src.modules.storage.repos.commitments import CommitmentRepo
from src.modules.storage.repos.events import EventRepo
from src.modules.storage.repos.game_creatures import GameCreatureRepo
from src.modules.storage.repos.llm import LLMRepo
from src.modules.storage.repos.memory_cue_log import MemoryCueLogRepo
from src.modules.storage.repos.memory_index import MemoryIndexRepo
from src.modules.storage.repos.memory_review import MemoryReviewRepo
from src.modules.storage.repos.rundown_instances import RundownInstanceRepo
from src.modules.storage.repos.rundown_transitions import RundownTransitionRepo
from src.modules.storage.repos.rundowns import RundownRepo
from src.modules.storage.repos.sessions import SessionRepo
from src.modules.storage.repos.sim import SimRepo
from src.modules.storage.repos.topics import TopicRepo
from src.modules.storage.repos.viewer_identity import ViewerIdentityRepo
from src.modules.storage.repos.viewers import ViewerRepo

__all__ = [
    "ArchiveRepo",
    "BaseRepo",
    "ChatRepo",
    "CommitmentRepo",
    "EventRepo",
    "GameCreatureRepo",
    "LLMRepo",
    "MemoryCueLogRepo",
    "MemoryIndexRepo",
    "MemoryReviewRepo",
    "RundownInstanceRepo",
    "RundownRepo",
    "RundownTransitionRepo",
    "SessionRepo",
    "SimRepo",
    "TopicRepo",
    "ViewerIdentityRepo",
    "ViewerRepo",
]
