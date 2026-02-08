"""输入Provider配置Schema定义

定义所有输入Provider的Pydantic配置模型。

注意：以下Provider已迁移到自管理Schema架构：
- ConsoleInputProvider: src/domains/input/providers/console_input/console_input_provider.py
- BiliDanmakuInputProvider: src/domains/input/providers/bili_danmaku/bili_danmaku_provider.py
- BiliDanmakuOfficialInputProvider: src/domains/input/providers/bili_danmaku_official/bili_official_provider.py
- BiliDanmakuOfficialMaiCraftInputProvider: src/domains/input/providers/bili_danmaku_official_maicraft/bili_official_maicraft_provider.py
- ReadPingmuInputProvider: src/domains/input/providers/read_pingmu/read_pingmu_provider.py
- MainosabaInputProvider: src/domains/input/providers/mainosaba/mainosaba_provider.py
"""

# 类型别名，用于导入（所有已迁移到自管理Schema）
InputProviderConfig = ()
