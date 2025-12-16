"""
MainosabaPlugin - 《魔法少女的魔女裁判》游戏助手插件

该插件通过截取屏幕、调用VLM模型识别游戏台词，
发送给MaiBot后等待任意回应（或超时），然后自动继续游戏。
"""

import asyncio
import time
import io
import base64
import traceback
from typing import Optional, Dict, Any
from PIL import ImageGrab
import pyautogui
import aiohttp
from src.core.plugin_manager import BasePlugin
from maim_message import MessageBase, BaseMessageInfo, UserInfo, Seg, FormatInfo


class MainosabaPlugin(BasePlugin):
    """《魔法少女的魔女裁判》游戏助手插件"""

    def __init__(self, core, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)
        self.plugin_name = "MainosabaPlugin"

        # VLM API配置
        self.vlm_api_url = plugin_config.get("vlm_api_url", "")
        self.vlm_api_key = plugin_config.get("vlm_api_key", "")
        self.vlm_model = plugin_config.get("vlm_model", "Pro/Qwen/Qwen2.5-VL-7B-Instruct")

        # 游戏配置
        self.full_screen = plugin_config.get("full_screen", True)  # 是否全屏截图
        self.game_region = plugin_config.get("game_region", None)  # [x1, y1, x2, y2] 如果为None则全屏
        self.response_timeout = plugin_config.get("response_timeout", 10)  # 秒
        self.check_interval = plugin_config.get("check_interval", 1)  # 秒

        # 控制配置
        self.auto_click = plugin_config.get("auto_click", True)
        self.click_position = plugin_config.get("click_position", [1920 // 2, 1080 // 2])  # 默认屏幕中心
        self.use_enter_key = plugin_config.get("use_enter_key", False)

        # 运行状态
        self.is_running = False
        self.last_screenshot_time = 0
        self.last_game_text = ""
        self.waiting_for_response = False
        self.last_message_time = 0
        self.monitor_task: Optional[asyncio.Task] = None

        # HTTP会话
        self.http_session: Optional[aiohttp.ClientSession] = None

        self.logger.info(f"{self.plugin_name} 插件初始化完成")

    async def setup(self) -> None:
        """插件启动时的设置"""
        # 创建HTTP会话，设置较长的超时时间以支持VLM图片处理
        # connect: 连接超时 10秒
        # total: 总超时 120秒（VLM处理图片可能需要较长时间）
        timeout = aiohttp.ClientTimeout(connect=10, total=120)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        self.logger.info("aiohttp.ClientSession 已创建（连接超时:10s, 总超时:120s）")

        # 注册消息处理器来检测MaiBot回应
        self.core.register_websocket_handler("*", self.handle_maicore_message)

        # 启动游戏监听任务
        self.is_running = True
        self.monitor_task = asyncio.create_task(self.game_monitor_loop())

        self.logger.info(f"{self.plugin_name} 插件启动完成")

    async def cleanup(self) -> None:
        """插件清理资源"""
        self.is_running = False
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass

        # 关闭HTTP会话
        if self.http_session:
            await self.http_session.close()
            self.logger.info("aiohttp.ClientSession 已关闭")
            self.http_session = None

        self.logger.info(f"{self.plugin_name} 插件清理完成")

    async def handle_maicore_message(self, message: MessageBase):
        """处理来自MaiCore的消息，检测MaiBot回应"""
        try:
            # 如果正在等待MaiBot回应，检测到任何消息就继续游戏
            if self.waiting_for_response:
                # 忽略自己发送的游戏消息，避免循环
                if (
                    message.message_info
                    and message.message_info.additional_config
                    and message.message_info.additional_config.get("source") == "manosaba_game"
                ):
                    return

                self.logger.info("检测到MaiBot回应，继续游戏")
                self.waiting_for_response = False
                await self.advance_game()

        except Exception as e:
            self.logger.error(f"处理消息时出错: {e}")

    async def game_monitor_loop(self) -> None:
        """游戏监听主循环"""
        self.logger.info("开始游戏监听循环")

        while self.is_running:
            try:
                # 检查是否在等待回应
                if self.waiting_for_response:
                    # 检查是否超时
                    if time.time() - self.last_message_time > self.response_timeout:
                        self.logger.info("等待回应超时，继续游戏")
                        await self.advance_game()
                        self.waiting_for_response = False
                else:
                    # 正常监听游戏文本
                    game_text = await self.capture_and_recognize()
                    if game_text and game_text != self.last_game_text:
                        self.logger.info(f"检测到新游戏文本: {game_text[:50]}...")
                        await self.send_game_text_to_maibot(game_text)
                        self.last_game_text = game_text
                        self.last_message_time = time.time()
                        self.waiting_for_response = True

                await asyncio.sleep(self.check_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"游戏监听循环出错: {e}")
                await asyncio.sleep(5)  # 出错时等待5秒再继续

        self.logger.info("游戏监听循环结束")

    async def capture_and_recognize(self) -> Optional[str]:
        """截取屏幕并识别游戏文本"""
        try:
            # 检查截屏频率限制
            current_time = time.time()
            if current_time - self.last_screenshot_time < 0.5:  # 最小0.5秒间隔
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
            game_text = await self.call_vlm_api(img_base64)
            return game_text

        except Exception as e:
            self.logger.error(f"截屏识别出错: {e}")
            return None

    async def call_vlm_api(self, image_base64: str) -> Optional[str]:
        """调用VLM API识别游戏文本"""
        try:
            if not self.vlm_api_key or not self.vlm_api_url:
                self.logger.warning(f"VLM API配置不完整，无法识别文本: {self.vlm_api_key} {self.vlm_api_url}")
                self.logger.warning("VLM API配置不完整，无法识别文本")
                return None

            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.vlm_api_key}"}

            data = {
                "model": self.vlm_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """请识别这张游戏截图中的对话文本。
这是《魔法少女的魔女裁判》游戏的界面。

请仔细识别并提取游戏中的对话内容，只返回角色说的台词文本。
如果是系统提示或界面元素，请忽略。
如果没有对话文本，请返回"无对话文本"。

请以纯文本形式返回识别到的对话内容。""",
                            },
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                        ],
                    }
                ],
                "max_tokens": 500,
            }

            if not self.http_session:
                self.logger.error("HTTP会话未初始化")
                return None

            # 添加调试日志
            self.logger.debug(f"开始调用VLM API: {self.vlm_api_url}")
            self.logger.debug(f"图片Base64长度: {len(image_base64)}")

            async with self.http_session.post(self.vlm_api_url, headers=headers, json=data) as response:
                self.logger.debug(f"收到响应，状态码: {response.status}")

                if response.status == 200:
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"].strip()

                    # 过滤无效响应
                    if content in ["无对话文本", "没有对话文本", "No dialogue text", ""]:
                        return None

                    self.logger.info(f"VLM识别结果: {content[:100]}...")
                    return content
                else:
                    error_text = await response.text()
                    self.logger.error(f"VLM API调用失败: {response.status} - {error_text}")
                    return None

        except asyncio.TimeoutError:
            self.logger.error("调用VLM API超时")
            self.logger.error(f"完整错误信息:\n{traceback.format_exc()}")
            return None
        except aiohttp.ClientError as e:
            self.logger.error(f"调用VLM API网络错误: {type(e).__name__} - {e}")
            self.logger.error(f"完整错误信息:\n{traceback.format_exc()}")
            return None
        except Exception as e:
            self.logger.error(f"调用VLM API出错: {type(e).__name__} - {e}")
            self.logger.error(f"完整错误信息:\n{traceback.format_exc()}")
            return None

    async def send_game_text_to_maibot(self, game_text: str) -> None:
        """将游戏文本发送给MaiBot"""
        try:
            # 直接发送游戏原文，不做任何加工
            content = game_text

            timestamp = time.time()
            message_id = f"manosaba_game_{int(timestamp * 1000)}"

            # 创建用户信息（游戏角色）
            game_user_info = UserInfo(
                platform=self.core.platform,
                user_id="game_character",
                user_nickname="游戏角色",
                user_cardname="Mainosaba",
            )

            # 创建格式信息
            format_info = FormatInfo(content_format=["text"], accept_format=["text"])

            # 附加配置，用于标识这是游戏消息
            additional_config = {
                "source": "manosaba_game",
                "sender_name": "游戏角色",
                "maimcore_reply_probability_gain": 1.5,  # 提高回复概率
            }

            # 创建消息信息
            message_info = BaseMessageInfo(
                platform=self.core.platform,
                message_id=message_id,
                time=timestamp,
                user_info=game_user_info,
                group_info=None,
                template_info=None,
                format_info=format_info,
                additional_config=additional_config,
            )

            # 创建消息段
            message_segment = Seg(type="text", data=content)

            # 创建最终消息
            message = MessageBase(message_info=message_info, message_segment=message_segment, raw_message=content)

            # 发送消息到MaiCore
            await self.core.send_to_maicore(message)
            self.logger.info(f"已发送游戏文本给MaiBot: {game_text[:50]}...")

        except Exception as e:
            self.logger.error(f"发送游戏文本到MaiBot出错: {e}")

    async def advance_game(self) -> None:
        """推进游戏到下一句对话"""
        try:
            if self.auto_click:
                # 鼠标点击指定位置
                x, y = self.click_position
                pyautogui.click(x, y)
                self.logger.info(f"已点击位置 ({x}, {y}) 推进游戏")

            if self.use_enter_key:
                # 按Enter键
                pyautogui.press("enter")
                self.logger.info("已按Enter键推进游戏")

            # 等待一下让游戏界面更新
            await asyncio.sleep(0.5)

        except Exception as e:
            self.logger.error(f"推进游戏出错: {e}")


# 插件入口点
plugin_entrypoint = MainosabaPlugin
