# 确定性 JSONL 回放器（v2.0.7+ / ADR-006）
# 唯一模式：JSONL 文件按速率回放（mock_danmaku 行为，零 LLM 依赖）
# 数据文件：data/msg_default.jsonl —— JSONL 模式素材
# 仿真（LLM 驱动生成式虚拟直播间）由 src/modules/simulator/SimulatorService 承担
# 文档：docs/architecture/adr/006-simulator-is-dev-infrastructure.md
from .mock_collector import MockCollector

__all__ = ["MockCollector"]
