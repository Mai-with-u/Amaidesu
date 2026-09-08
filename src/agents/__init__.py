"""
Amaidesu 业务 Agent 包

包定位：
- ``src/agents/`` 只放"主体"（有自我/目标的 Agent）
- 框架基础设施在 ``src/modules/``（agents/agents/*、tools/、events/、...）

每个 Agent 自包含一份顶级子包：
- ``streamer`` — 主播 Agent
- ``minecraft`` — Minecraft 游戏 Agent
- ``text_adv`` — 文字冒险游戏 Agent

Agent 间没有分类层（无"game / custom"父包）；驱动方式的差异落在每个
Agent 自己的实现里，不体现在包结构。
"""
