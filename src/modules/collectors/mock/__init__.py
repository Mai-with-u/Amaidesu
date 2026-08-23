# 模拟采集器（v2 / Wave 5 合并迁移）
# 合并自 src/stages/input/collectors/mock_danmaku/ + src/modules/simulator/
# 双模式：
#   - mode="jsonl"（默认）    —— JSONL 文件回放（mock_danmaku 行为）
#   - mode="simulator"        —— LLM 驱动人设池/节奏生成器/礼物生成器
# 数据文件随走：
#   - data/msg_default.jsonl          —— JSONL 模式素材
#   - data/simulator_gifts.toml       —— 礼物清单（运行时数据，可编辑）
#   - data/simulator_residents.toml   —— 常驻人设（运行时数据，可编辑）
# 文档：.omo/drafts/amaidesu-v2-migration.md §C
from .mock_collector import MockCollector

__all__ = ["MockCollector"]
