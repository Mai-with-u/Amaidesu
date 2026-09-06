"""
Amaidesu 业务 Agent 包

包定位：
- ``src/agents/`` 只放"主体"（有自我/目标的 Agent）
- 框架基础设施在 ``src/modules/``（agents/agents/*、tools/、events/、...）

具体 Agent 子包：
- ``streamer`` — 主播 Agent
- ``game.text_adv`` — 文字冒险游戏 Agent
- 未来新增游戏 = 新增 ``game.<name>/`` 子包 + 配置 ``[agents.game] engine = "<name>"``，
  框架零改动
"""
