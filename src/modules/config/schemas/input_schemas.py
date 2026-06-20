"""输入阶段参与者配置Schema定义

定义所有输入Collector的Pydantic配置模型。

注意：以下Collector已迁移到自管理Schema架构：
- ConsoleInputCollector: src/stages/input/collectors/console_input/console_input_collector.py
- BiliDanmakuCollector: src/stages/input/collectors/bili_danmaku/bili_danmaku_collector.py
- BiliDanmakuOfficialCollector: src/stages/input/collectors/bili_danmaku_official/bili_danmaku_official_collector.py
- BiliDanmakuOfficialMaiCraftCollector: src/stages/input/collectors/bili_danmaku_official_maicraft/bili_danmaku_official_maicraft_collector.py
- ReadPingmuCollector: src/stages/input/collectors/read_pingmu/read_pingmu_collector.py
- MainosabaCollector: src/stages/input/collectors/mainosaba/mainosaba_collector.py
"""

# 类型别名，用于导入（所有已迁移到自管理Schema）
InputConfig = ()
