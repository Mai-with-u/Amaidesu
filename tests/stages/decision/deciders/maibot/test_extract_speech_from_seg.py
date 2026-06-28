"""
MaiBotDecider._extract_speech_from_seg 单元测试。

P0-2 修复回归:验证 MaiBot-v1.0.0 的 seglist 回复能被正确展开为文本。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from maim_message import Seg

from src.modules.events.event_bus import EventBus
from src.stages.decision.deciders.maibot.maibot_decider import MaiBotDecider


@pytest.fixture
def decider():
    """构造一个最小可用的 MaiBotDecider 实例。"""
    return MaiBotDecider(
        config={"type": "maibot", "host": "127.0.0.1", "port": 8000},
        event_bus=MagicMock(spec=EventBus),
    )


class TestExtractSpeechFromSeg:
    """_extract_speech_from_seg 方法的所有支持路径。"""

    def test_pure_text_seg_returns_text(self, decider):
        """纯 text 段: 直接返回 data 字符串。"""
        seg = Seg(type="text", data="你好")
        assert decider._extract_speech_from_seg(seg) == "你好"

    def test_seglist_with_single_text(self, decider):
        """seglist 包含 1 个 text: 返回该文本。"""
        seg = Seg(type="seglist", data=[Seg(type="text", data="hello")])
        assert decider._extract_speech_from_seg(seg) == "hello"

    def test_seglist_with_multiple_texts(self, decider):
        """seglist 包含多个 text: 用换行拼接。"""
        seg = Seg(type="seglist", data=[
            Seg(type="text", data="第一行"),
            Seg(type="text", data="第二行"),
            Seg(type="text", data="第三行"),
        ])
        assert decider._extract_speech_from_seg(seg) == "第一行\n第二行\n第三行"

    def test_seglist_with_mixed_types(self, decider):
        """seglist 包含 text + image: 只保留 text 段,过滤 image 占位。"""
        seg = Seg(type="seglist", data=[
            Seg(type="text", data="看这张图"),
            Seg(type="image", data="base64data"),
            Seg(type="text", data="很可爱"),
        ])
        assert decider._extract_speech_from_seg(seg) == "看这张图\n很可爱"

    def test_seglist_strips_placeholders_for_tts(self, decider):
        """seglist 中所有非 text 段都被过滤,无可朗读内容则返回 None。"""
        seg = Seg(type="seglist", data=[
            Seg(type="image", data="img1"),
            Seg(type="emoji", data="🙂"),
            Seg(type="voice", data="aud"),
        ])
        assert decider._extract_speech_from_seg(seg) is None

    def test_nested_seglist_recursion(self, decider):
        """嵌套 seglist: 递归展开所有层。"""
        seg = Seg(type="seglist", data=[
            Seg(type="seglist", data=[
                Seg(type="text", data="内层"),
            ]),
            Seg(type="text", data="外层"),
        ])
        assert decider._extract_speech_from_seg(seg) == "内层\n外层"

    def test_empty_seglist_returns_none(self, decider):
        """空 seglist: 返回 None,调用方应跳过 Intent 发布。"""
        seg = Seg(type="seglist", data=[])
        assert decider._extract_speech_from_seg(seg) is None

    def test_seglist_with_only_non_text(self, decider):
        """seglist 只含 image: 返回 None,无可朗读内容。"""
        seg = Seg(type="seglist", data=[Seg(type="image", data="x")])
        assert decider._extract_speech_from_seg(seg) is None

    def test_image_seg_returns_none(self, decider):
        """纯 image 段: 返回 None,调用方应跳过 Intent 发布。"""
        seg = Seg(type="image", data="base64data")
        assert decider._extract_speech_from_seg(seg) is None

    def test_emoji_seg_returns_none(self, decider):
        """emoji 段: 返回 None。"""
        seg = Seg(type="emoji", data="😀")
        assert decider._extract_speech_from_seg(seg) is None

    def test_voice_seg_returns_none(self, decider):
        """voice 段: 返回 None。"""
        seg = Seg(type="voice", data="audiodata")
        assert decider._extract_speech_from_seg(seg) is None

    def test_at_seg_returns_none(self, decider):
        """at 段: 返回 None(避免显示 @user_id 字符串,也不朗读)。"""
        seg = Seg(type="at", data="u123")
        assert decider._extract_speech_from_seg(seg) is None

    def test_unknown_seg_type_returns_none(self, decider):
        """未知 type: 返回 None,稳健性降级。"""
        seg = Seg(type="unknown_type", data="anything")
        assert decider._extract_speech_from_seg(seg) is None

    def test_text_with_non_string_data(self, decider):
        """text 段但 data 不是 str(异常情况): 返回 None 不崩溃。

        Seg 类型签名声明为 Union[str, List[Seg]],但 dataclass 不强制运行时类型校验,
        实际场景中 maim_message 库或上游 bug 可能传入 int/None/dict。
        spec 要求防御性降级,不应抛异常。
        """
        seg_int = Seg(type="text", data=123)  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_int) is None
        seg_none = Seg(type="text", data=None)  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_none) is None
        seg_dict = Seg(type="text", data={"k": "v"})  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_dict) is None

    def test_seglist_with_non_list_data(self, decider):
        """seglist 但 data 不是 list(异常情况): 返回 None 不崩溃。

        Seg 类型签名声明为 Union[str, List[Seg]],但 dataclass 不强制运行时类型校验,
        实际场景中 maim_message 库或上游 bug 可能传入 str/int/None。
        spec 要求防御性降级,不应抛异常。
        """
        seg_str = Seg(type="seglist", data="not a list")  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_str) is None
        seg_int = Seg(type="seglist", data=42)  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_int) is None
        seg_none = Seg(type="seglist", data=None)  # type: ignore[arg-type]
        assert decider._extract_speech_from_seg(seg_none) is None


class TestMaiBotV1CompatIntegration:
    """MaiBot-v1.0.0 出站格式的真实场景。"""

    def test_v1_seglist_with_three_segments(self, decider):
        """模拟 MaiBot-v1.0.0 最常见的回复格式: 1 个 seglist + 多段 text。"""
        msg_data = {
            "message_info": {
                "platform": "amaidesu",
                "message_id": "v1_msg_001",
                "time": 1729612345.0,
                "format_info": {"content_format": ["text"], "accept_format": ["text"]},
                "user_info": {
                    "platform": "amaidesu",
                    "user_id": "user_1",
                    "user_nickname": "tester",
                },
                "group_info": {
                    "platform": "amaidesu",
                    "group_id": "live_room",
                    "group_name": "直播间",
                },
            },
            "message_segment": {
                "type": "seglist",
                "data": [
                    {"type": "text", "data": "这是来自 MaiBot 的回复"},
                    {"type": "emoji", "data": "😊"},
                    {"type": "text", "data": "祝你有美好的一天"},
                ],
            },
            "raw_message": None,
        }

        from maim_message import MessageBase

        message = MessageBase.from_dict(msg_data)
        speech = decider._extract_speech_from_seg(message.message_segment)
        assert speech == "这是来自 MaiBot 的回复\n祝你有美好的一天"


class TestSpeechNoneSkipsIntent:
    """验证 speech=None 时 _process_maibot_message 不发布 Intent。"""

    @pytest.fixture
    def decider_with_mock_bus(self):
        """构造 MaiBotDecider 并 mock 掉 event_bus.emit。"""
        decider = MaiBotDecider(
            config={"type": "maibot", "host": "127.0.0.1", "port": 8000},
            event_bus=MagicMock(spec=EventBus),
        )
        decider._event_bus = MagicMock(spec=EventBus)
        decider._event_bus.emit = AsyncMock()
        return decider

    @staticmethod
    def _make_message_data(seg_dict: dict) -> dict:
        return {
            "message_info": {
                "platform": "amaidesu",
                "message_id": "skip_test",
                "time": 1729612345.0,
                "format_info": {"content_format": ["text"], "accept_format": ["text"]},
                "user_info": {
                    "platform": "amaidesu",
                    "user_id": "u1",
                    "user_nickname": "tester",
                },
                "group_info": {
                    "platform": "amaidesu",
                    "group_id": "live_room",
                    "group_name": "直播间",
                },
            },
            "message_segment": seg_dict,
            "raw_message": None,
        }

    @pytest.mark.asyncio
    async def test_pure_image_message_does_not_publish_intent(self, decider_with_mock_bus):
        """纯 image 消息(无 seglist 包裹)不应触发 Intent 发布。"""
        from maim_message import MessageBase

        msg_data = self._make_message_data({"type": "image", "data": "base64..."})
        message_dict = MessageBase.from_dict(msg_data).to_dict()
        await decider_with_mock_bus._process_maibot_message(message_dict)

        decider_with_mock_bus._event_bus.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_seglist_message_does_not_publish_intent(self, decider_with_mock_bus):
        """空 seglist 消息不应触发 Intent 发布。"""
        msg_data = self._make_message_data({"type": "seglist", "data": []})
        from maim_message import MessageBase

        message_dict = MessageBase.from_dict(msg_data).to_dict()
        await decider_with_mock_bus._process_maibot_message(message_dict)

        decider_with_mock_bus._event_bus.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_seglist_only_image_does_not_publish_intent(self, decider_with_mock_bus):
        """seglist 只含 image 不应触发 Intent 发布。"""
        msg_data = self._make_message_data({
            "type": "seglist",
            "data": [{"type": "image", "data": "x"}],
        })
        from maim_message import MessageBase

        message_dict = MessageBase.from_dict(msg_data).to_dict()
        await decider_with_mock_bus._process_maibot_message(message_dict)

        decider_with_mock_bus._event_bus.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_message_does_publish_intent(self, decider_with_mock_bus):
        """正常 text 消息应触发 Intent 发布(对照组,验证 mock 配置正确)。"""
        msg_data = self._make_message_data({"type": "text", "data": "正常消息"})
        from maim_message import MessageBase

        message_dict = MessageBase.from_dict(msg_data).to_dict()
        await decider_with_mock_bus._process_maibot_message(message_dict)

        decider_with_mock_bus._event_bus.emit.assert_called_once()