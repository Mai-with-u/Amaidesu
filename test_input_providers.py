"""
测试 InputLayer 数据流（自动化测试）

直接测试 InputLayer 的核心功能：
1. RawData 采集
2. NormalizedMessage 转换
3. 事件发布
"""

import asyncio
from src.core.event_bus import EventBus
from src.core.base.raw_data import RawData
from src.layers.input.input_layer import InputLayer
from src.utils.logger import get_logger


logger = get_logger("TestInput")


async def test_input_layer():
    """测试 InputLayer 的数据转换功能"""

    print("\n" + "="*60)
    print("[TEST] 测试 InputLayer 数据流")
    print("="*60 + "\n")

    # 创建事件总线
    event_bus = EventBus()

    # 创建 InputLayer
    input_layer = InputLayer(event_bus)
    await input_layer.setup()

    # 收集结果
    results = []

    # 监听 NormalizedMessage 事件
    @event_bus.on("normalization.message_ready", priority=50)
    async def on_message_ready(event_name: str, event_data: dict, source: str):
        """处理消息就绪事件"""
        message = event_data.get("message")
        if message:
            results.append(message)
            print(f"\n✅ 测试用例 {len(results)}:")
            print(f"   来源: {message.source}")
            print(f"   类型: {message.message_type}")
            print(f"   内容: {message.content}")
            print(f"   原始文本: {message.original_text}")

    # 测试用例
    test_cases = [
        {
            "name": "普通文本输入",
            "raw_data": RawData(
                content={"text": "你好，Amaidesu"},
                source="console_input",
                data_type="text"
            )
        },
        {
            "name": "弹幕消息",
            "raw_data": RawData(
                content={
                    "text": "主播好！",
                    "user_name": "测试用户",
                    "user_id": "12345"
                },
                source="bili_danmaku",
                data_type="danmaku"
            )
        },
        {
            "name": "礼物消息",
            "raw_data": RawData(
                content={
                    "user_name": "张三",
                    "gift_name": "小星星",
                    "gift_count": 10
                },
                source="bili_danmaku",
                data_type="gift"
            )
        },
        {
            "name": "空内容（应该被过滤）",
            "raw_data": RawData(
                content={},
                source="test",
                data_type="text"
            )
        }
    ]

    print(f"📋 准备运行 {len(test_cases)} 个测试用例\n")

    # 运行测试用例
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n📝 测试 {i}/{len(test_cases)}: {test_case['name']}")

        # 发布 RawData 事件
        await event_bus.emit(
            "perception.raw_data.generated",
            {"data": test_case["raw_data"]},
            source="TestInput"
        )

        # 等待处理完成
        await asyncio.sleep(0.2)

    # 等待所有事件处理完成
    await asyncio.sleep(1)

    # 显示统计结果
    print("\n" + "="*60)
    print("📊 测试结果统计")
    print("="*60)
    print(f"总测试用例: {len(test_cases)}")
    print(f"成功转换: {len(results)}")
    print(f"转换成功率: {len(results)/len(test_cases)*100:.1f}%")

    # 清理
    await input_layer.cleanup()

    print("\n✅ 测试完成！")

    return len(results) == len([tc for tc in test_cases if tc['raw_data'].content])  # 预期除空内容外都成功


async def test_console_input_provider():
    """测试 ConsoleInputProvider 的初始化（不运行数据采集）"""

    print("\n" + "="*60)
    print("🧪 测试 ConsoleInputProvider 初始化")
    print("="*60 + "\n")

    from src.layers.input.providers.console_input_provider import ConsoleInputProvider

    # 测试配置
    config = {
        "user_id": "test_user",
        "user_nickname": "测试用户"
    }

    try:
        # 创建 Provider
        provider = ConsoleInputProvider(config)
        print("✅ ConsoleInputProvider 创建成功")
        print(f"   用户ID: {provider.user_id}")
        print(f"   用户昵称: {provider.user_nickname}")

        # 测试属性
        assert provider.user_id == "test_user", "user_id 不匹配"
        assert provider.user_nickname == "测试用户", "user_nickname 不匹配"
        assert not provider.is_running, "初始状态应该是未运行"

        print("\n✅ ConsoleInputProvider 初始化测试通过")
        return True

    except Exception as e:
        print(f"\n❌ ConsoleInputProvider 初始化测试失败: {e}")
        return False


async def test_mock_danmaku_provider():
    """测试 MockDanmakuProvider 的初始化"""

    print("\n" + "="*60)
    print("🧪 测试 MockDanmakuProvider 初始化")
    print("="*60 + "\n")

    from src.layers.input.providers.mock_danmaku_provider import MockDanmakuProvider

    # 测试配置
    config = {
        "interval": 5,  # 每5秒发送一条弹幕
        "messages": ["测试消息1", "测试消息2", "测试消息3"]
    }

    try:
        # 创建 Provider
        provider = MockDanmakuProvider(config)
        print("✅ MockDanmakuProvider 创建成功")

        # 测试属性
        assert not provider.is_running, "初始状态应该是未运行"

        print("\n✅ MockDanmakuProvider 初始化测试通过")
        return True

    except Exception as e:
        print(f"\n❌ MockDanmakuProvider 初始化测试失败: {e}")
        return False


async def main():
    """运行所有测试"""

    print("\n🚀 开始测试 InputLayer 和 InputProvider\n")

    # 测试 1: InputLayer 数据流
    test1_passed = await test_input_layer()

    # 测试 2: ConsoleInputProvider 初始化
    test2_passed = await test_console_input_provider()

    # 测试 3: MockDanmakuProvider 初始化
    test3_passed = await test_mock_danmaku_provider()

    # 汇总结果
    print("\n" + "="*60)
    print("📋 测试汇总")
    print("="*60)
    print(f"✅ InputLayer 数据流测试: {'通过' if test1_passed else '失败'}")
    print(f"✅ ConsoleInputProvider 初始化: {'通过' if test2_passed else '失败'}")
    print(f"✅ MockDanmakuProvider 初始化: {'通过' if test3_passed else '失败'}")

    all_passed = test1_passed and test2_passed and test3_passed

    print(f"\n{'🎉 所有测试通过！' if all_passed else '⚠️  部分测试失败'}")

    return all_passed


if __name__ == "__main__":
    import sys
    try:
        passed = asyncio.run(main())
        sys.exit(0 if passed else 1)
    except KeyboardInterrupt:
        print("\n⚠️  测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试过程中出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
