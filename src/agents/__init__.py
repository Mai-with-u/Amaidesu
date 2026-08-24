"""
Amaidesu 业务 Agent 包（v2.0.0 / Wave 7）

按架构 §1.52 定案：
- ``src/agents/`` 只放"主体"（有自我/目标的 Agent）
- 框架基础设施在 ``src/modules/``（agents/agents/*、tools/、events/、...）

具体 Agent 子包：
- ``streamer`` — 主播 Agent（Wave 6）
- ``game.text_adv`` — 文字冒险游戏 Agent（Wave 7 示例）
- 未来新增游戏 = 新增 ``game.<name>/`` 子包 + 配置 ``[agents.game] engine = "<name>"``，
  框架零改动（§1.49 范式验证目标）
"""
