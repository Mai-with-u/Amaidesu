"""
Mainosaba InputProvider - 从游戏画面采集文本数据

该Provider从《魔法少女的魔女裁判》游戏画面截取屏幕，
使用VLM识别游戏文本，并产生RawData供后续处理。
"""

import asyncio
import base64
import io
import time
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

import pyautogui
from PIL import ImageGrab
from pydantic import Field, field_validator

from src.modules.config.schemas.base import BaseProviderConfig
from src.modules.events.event_bus import EventBus
from src.modules.logging import get_logger
from src.modules.prompts import get_prompt_manager
from src.modules.types.base.input_provider import InputProvider
from src.modules.types.base.raw_data import RawData


class ControlMethod(Enum):
    """游戏控制方式枚举"""

    MOUSE_CLICK = "mouse_click"  # 鼠标单击
    ENTER_KEY = "enter_key"  # Enter键
    SPACE_KEY = "space_key"  # 空格键


class MainosabaInputProvider(InputProvider):
    """
    Mainosaba InputProvider - 从游戏画面采集文本数据

    职责：
    - 截取游戏画面
    - 使用VLM识别游戏文本
    - 产生RawData供后续处理
    """

    class ConfigSchema(BaseProviderConfig):
        """Mainosaba游戏画面采集输入Provider配置"""

        type: Literal["mainosaba"] = "mainosaba"
        full_screen: bool = Field(default=True, description="全屏截图")
        game_region: Optional[List[int]] = Field(default=None, description="游戏区域 [x1, y1, x2, y2]")
        check_interval: int = Field(default=1, description="检查间隔（秒）", ge=1)
        screenshot_min_interval: float = Field(default=0.5, description="最小截图间隔（秒）", ge=0.1)
        response_timeout: int = Field(default=10, description="响应超时（秒）", ge=1)
        control_method: Literal["mouse_click", "enter_key", "space_key"] = Field(
            default="mouse_click", description="游戏控制方式"
        )
        click_position: List[int] = Field(default_factory=lambda: [1920 // 2, 1080 // 2], description="点击位置 [x, y]")

        @field_validator("game_region")
        @classmethod
        def validate_game_region(cls, v: Optional[List[int]]) -> Optional[List[int]]:
            """验证游戏区域格式"""
            if v is not None:
                if len(v) != 4:
                    raise ValueError("game_region必须包含4个值 [x1, y1, x2, y2]")
                if any(x < 0 for x in v):
                    raise ValueError("game_region坐标不能为负数")
            return v

        @field_validator("click_position")
        @classmethod
        def validate_click_position(cls, v: List[int]) -> List[int]:
            """验证点击位置格式"""
            if len(v) != 2:
                raise ValueError("click_position必须包含2个值 [x, y]")
            if any(x < 0 for x in v):
                raise ValueError("click_position坐标不能为负数")
            return v

    def __init__(self, config: Dict[str, Any], vlm_client=None, event_bus: Optional[EventBus] = None):
        super().__init__(config)
        self.logger = get_logger("MainosabaInputProvider")
        self.vlm_client = vlm_client
        self.event_bus = event_bus

        # 游戏配置
        self.typed_config = self.ConfigSchema(**config)
        self.full_screen = self.typed_config.full_screen
        self.game_region = self.typed_config.game_region
        self.check_interval = self.typed_config.check_interval
        self.screenshot_min_interval = self.typed_config.screenshot_min_interval

        # 状态
        self.last_screenshot_time = 0
        self.last_game_text = ""
        self.waiting_for_response = False
        self.last_message_time = 0

    async def _collect_data(self) -> AsyncIterator[RawData]:
        """采集游戏文本数据"""
        self.logger.info("开始采集游戏文本数据...")

        while self.is_running:
            try:
                # 检查是否在等待回应
                if self.waiting_for_response:
                    # 检查是否超时
                    if time.time() - self.last_message_time > self.typed_config.response_timeout:
                        self.logger.info("等待回应超时，继续游戏")
                        await self.advance_game()
                        self.waiting_for_response = False
                else:
                    # 正常监听游戏文本
                    self.logger.debug("开始截屏识别...")
                    game_text = await self.capture_and_recognize()
                    self.logger.debug(f"识别完成，结果: {game_text[:50] if game_text else 'None'}...")

                    if game_text is None:
                        self.logger.debug("未识别到有效文本，继续监听")
                    elif game_text == self.last_game_text:
                        self.logger.debug("识别到的文本与上次相同，跳过")
                    else:
                        self.logger.info(f"检测到新游戏文本: {game_text[:50]}...")
                        # 产生RawData
                        yield RawData(
                            source="mainosaba_game",
                            content=game_text,
                            data_type="text",
                            timestamp=time.time(),
                            metadata={
                                "platform": "mainosaba",
                                "source": "manosaba_game",
                                "maimcore_reply_probability_gain": 1.5,
                            },
                        )
                        self.last_game_text = game_text
                        self.last_message_time = time.time()
                        self.waiting_for_response = True

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"游戏监听循环出错: {e}", exc_info=True)
                await asyncio.sleep(5)  # 出错时等待5秒再继续

        self.logger.info("游戏文本采集结束")

    async def capture_and_recognize(self) -> Optional[str]:
        """截取屏幕并识别游戏文本"""
        try:
            # 检查截屏频率限制
            current_time = time.time()
            if current_time - self.last_screenshot_time < self.screenshot_min_interval:
                return None

            # 截取全屏或指定区域
            if self.full_screen or self.game_region is None:
                screenshot = ImageGrab.grab()  # 全屏截图
            else:
                screenshot = ImageGrab.grab(bbox=tuple(self.game_region))  # 指定区域截图

            self.last_screenshot_time = current_time

            # 转换为base64用于API调用
            img_buffer = io.BytesIO()
            screenshot.save(img_buffer, format="PNG")
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode()

            # 调用VLM API识别文本
            game_text = await self.recognize_game_text(img_base64)
            return game_text

        except Exception as e:
            self.logger.error(f"截屏识别出错: {e}", exc_info=True)
            return None

    async def recognize_game_text(self, image_base64: str) -> Optional[str]:
        """识别游戏截图中的文本（使用 VLM）"""
        if not self.vlm_client:
            self.logger.warning("VLM 客户端未初始化，无法识别文本")
            return None

        try:
            # 构建 prompt（从集中管理的模板获取）
            prompt = get_prompt_manager().get_raw("input/mainosaba_ocr")

            # 构建图像 URL（LLMClient 支持 base64 格式）
            image_data_url = f"data:image/png;base64,{image_base64}"

            # 调用 VLM（自动处理 HTTP 请求、错误、Token 统计）
            result = await self.vlm_client.vision_completion(prompt=prompt, images=image_data_url, max_tokens=500)

            if not result["success"]:
                self.logger.error(f"VLM识别失败: {result.get('error')}")
                return None

            content = result["content"].strip()

            # 过滤无效响应
            if content in ["无对话文本", "没有对话文本", "No dialogue text", ""]:
                return None

            self.logger.debug(f"VLM识别结果: {content[:100]}...")
            return content

        except Exception as e:
            self.logger.error(f"识别游戏文本出错: {e}", exc_info=True)
            return None

    async def advance_game(self) -> None:
        """推进游戏到下一句对话"""
        try:
            control_method_str = self.typed_config.control_method
            try:
                control_method = ControlMethod(control_method_str)
            except ValueError:
                self.logger.warning(f"未知的控制方式: {control_method_str}，使用默认值 mouse_click")
                control_method = ControlMethod.MOUSE_CLICK

            click_position = self.typed_config.click_position

            if control_method == ControlMethod.MOUSE_CLICK:
                # 鼠标点击指定位置
                x, y = click_position
                pyautogui.click(x, y)
                self.logger.info(f"已点击位置 ({x}, {y}) 推进游戏")
            elif control_method == ControlMethod.ENTER_KEY:
                # 按Enter键
                pyautogui.press("enter")
                self.logger.info("已按Enter键推进游戏")
            elif control_method == ControlMethod.SPACE_KEY:
                # 按空格键
                pyautogui.press("space")
                self.logger.info("已按空格键推进游戏")

            # 等待一下让游戏界面更新
            await asyncio.sleep(0.5)

        except Exception as e:
            self.logger.error(f"推进游戏出错: {e}", exc_info=True)
