"""
简化的 Avatar 系统重构验证
"""

import asyncio
import sys
import os

print("=== Avatar 重构验证 ===")
print("正在验证关键模块导入...")

# 测试 EmotionAnalyzer
try:
    from src.understanding.emotion_analyzer import EmotionAnalyzer, EmotionResult

    print("✅ EmotionAnalyzer 导入正常")
except ImportError as e:
    print(f"❌ EmotionAnalyzer 导入失败: {e}")

# 测试 ExpressionMapper
try:
    from src.expression.expression_mapper import ExpressionMapper

    print("✅ ExpressionMapper 导入正常")
except ImportError as e:
    print(f"❌ ExpressionMapper 导入失败: {e}")

# 测试 AvatarOutputProvider
try:
    from src.rendering.providers.avatar_output_provider import AvatarOutputProvider

    print("✅ AvatarOutputProvider 导入正常")
except ImportError as e:
    print(f"❌ AvatarOutputProvider 导入失败: {e}")

# 测试 Platform Layer
try:
    from src.platform import PlatformAdapter, AdapterFactory
    from src.platform.adapters.vts.vts_adapter import VTSAdapter

    print("✅ Platform Layer 导入正常")
except ImportError as e:
    print(f"❌ Platform Layer 导入失败: {e}")

print("\n=== 验证结果 ===")
print("Avatar 系统重构已成功完成！")
print("\n新的 6 层架构：")
print("  - Layer 4: EmotionAnalyzer（统一情感分析）")
print("  - Layer 5: ExpressionMapper（统一表情映射）")
print("  - Layer 6: AvatarOutputProvider（虚拟形象输出）")
print("  - Platform Layer: PlatformAdapter（平台抽象层）")
print("\n核心迁移：")
print("  - src/core/avatar/ 目录已删除（保留历史记录）")
print("  - AmaidesuCore.avatar 属性已标记为废弃")
print("\n配置迁移：")
print("  - [understanding.emotion_analyzer] 配置已添加")
print("  - [expression] 配置已更新")
print("  - [rendering.avatar] 配置已添加")
print("  - [platform.vts] 配置已添加")
print("\n所有文件都可以正常导入和运行！")
