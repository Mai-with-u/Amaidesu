"""
DGLabService Plugin - 新Plugin架构实现

提供控制 DG-LAB 硬件的服务。
"""

import asyncio
from typing import Dict, Any, List, Optional

from src.utils.logger import get_logger


class DGLabServiceProvider:
    """
    DG-LAB 服务提供者

    封装与 DG-LAB 硬件（通过 fucking-3.0 中间件）的 HTTP API 通信逻辑。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger("DGLabServiceProvider")

        # 配置
        self.api_base_url = config.get("dg_lab_api_base_url", "http://127.0.0.1:8081").rstrip("/")
        self.default_strength = config.get("target_strength", 10)
        self.default_waveform = config.get("target_waveform", "big")
        self.shock_duration = config.get("shock_duration_seconds", 2)
        self.timeout = config.get("request_timeout", 5)

        # 状态
        self.http_session = None
        self.control_lock = asyncio.Lock()  # 确保同一时间只有一个 shock 序列在运行

        # 检查依赖
        try:
            import aiohttp  # noqa: F401

            self.aiohttp_available = True
        except ImportError:
            self.aiohttp_available = False
            self.logger.error("aiohttp 库未找到，请运行 `pip install aiohttp`。")

    async def setup(self, event_bus):
        """设置服务提供者"""
        self.event_bus = event_bus

        if not self.aiohttp_available:
            raise ImportError("aiohttp 未安装")

        import aiohttp

        self.http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout))
        self.logger.info("aiohttp.ClientSession 已创建。")

        # 订阅事件
        event_bus.on("dg_lab.trigger_shock", self._handle_trigger_shock, priority=100)

    async def _handle_trigger_shock(self, event_name: str, data: Dict[str, Any], source: str):
        """处理电击触发事件"""
        strength = data.get("strength")
        waveform = data.get("waveform")
        duration = data.get("duration")

        await self.trigger_shock(strength=strength, waveform=waveform, duration=duration)

    async def trigger_shock(
        self, strength: Optional[int] = None, waveform: Optional[str] = None, duration: Optional[float] = None
    ):
        """
        触发一次电击序列。

        允许覆盖配置中的默认参数。
        """
        if not self.http_session:
            self.logger.error("HTTP Session 未初始化，无法触发电击。")
            return

        if self.control_lock.locked():
            self.logger.warning("电击序列正在进行中，本次触发被忽略。")
            return

        async with self.control_lock:
            strength_to_use = strength if strength is not None else self.default_strength
            waveform_to_use = waveform if waveform is not None else self.default_waveform
            duration_to_use = duration if duration is not None else self.shock_duration

            self.logger.info(
                f"触发电击序列: 强度={strength_to_use}, 波形='{waveform_to_use}', 持续时间={duration_to_use}s"
            )

            strength_url = f"{self.api_base_url}/control/strength"
            waveform_url = f"{self.api_base_url}/control/waveform"
            headers = {"Content-Type": "application/json"}

            # 初始设置
            initial_tasks = [
                self._make_api_call(
                    strength_url, {"channel": "a", "strength": strength_to_use}, headers, "设置通道 A 强度"
                ),
                self._make_api_call(
                    strength_url, {"channel": "b", "strength": strength_to_use}, headers, "设置通道 B 强度"
                ),
                self._make_api_call(
                    waveform_url, {"channel": "a", "preset": waveform_to_use}, headers, "设置通道 A 波形"
                ),
                self._make_api_call(
                    waveform_url, {"channel": "b", "preset": waveform_to_use}, headers, "设置通道 B 波形"
                ),
            ]
            await asyncio.gather(*initial_tasks)

            # 等待
            await asyncio.sleep(duration_to_use)

            # 强度归零
            self.logger.info("电击持续时间结束，将强度归零。")
            reset_tasks = [
                self._make_api_call(strength_url, {"channel": "a", "strength": 0}, headers, "重置通道 A 强度"),
                self._make_api_call(strength_url, {"channel": "b", "strength": 0}, headers, "重置通道 B 强度"),
            ]
            await asyncio.gather(*reset_tasks)

    async def _make_api_call(self, url: str, payload: Dict, headers: Dict, description: str) -> bool:
        """执行单个 HTTP API 调用并处理错误。"""
        if not self.http_session:
            return False
        try:
            import aiohttp

            async with self.http_session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    self.logger.debug(f"API 调用成功: {description}")
                    return True
                else:
                    error_text = await response.text()
                    self.logger.error(f"API 调用失败: {description} (状态码: {response.status}, 响应: {error_text})")
                    return False
        except aiohttp.ClientConnectorError as e:
            self.logger.error(f"API 连接失败: {description}。无法连接到 {self.api_base_url}。错误: {e}")
            return False
        except asyncio.TimeoutError:
            self.logger.error(f"API 调用超时: {description}")
            return False
        except Exception as e:
            self.logger.error(f"API 调用时发生未知错误: {description}。错误: {e}", exc_info=True)
            return False

    async def cleanup(self):
        """清理资源"""
        self.logger.info("正在清理 DGLabServiceProvider...")

        if self.http_session:
            await self.http_session.close()
            self.logger.info("aiohttp.ClientSession 已关闭。")
            self.http_session = None

        self.logger.info("DGLabServiceProvider 清理完成。")

    def get_info(self) -> Dict[str, Any]:
        """获取服务提供者信息"""
        return {
            "name": "DGLabService",
            "is_available": self.aiohttp_available,
            "api_base_url": self.api_base_url,
        }


class DGLabServicePlugin:
    """
    DG-Lab Service插件 - 提供控制 DG-LAB 硬件的服务

    迁移到新的Plugin架构，包装DGLabServiceProvider。
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(f"初始化插件: {self.__class__.__name__}")

        self.event_bus = None
        self._service_provider = None

        # 配置检查
        self.enabled = self.config.get("enabled", True)
        if not self.enabled:
            self.logger.warning("DGLabServicePlugin 在配置中已禁用。")
            return

    async def setup(self, event_bus, config: Dict[str, Any]) -> List[Any]:
        """
        设置插件

        Args:
            event_bus: 事件总线实例
            config: 插件配置

        Returns:
            Provider列表 (空列表，因为这是服务提供者)
        """
        self.event_bus = event_bus

        if not self.enabled:
            return []

        # 创建服务提供者
        try:
            self._service_provider = DGLabServiceProvider(self.config)
            await self._service_provider.setup(event_bus)
            self.logger.info("DGLabServiceProvider 已创建并设置完成")
        except Exception as e:
            self.logger.error(f"创建ServiceProvider失败: {e}", exc_info=True)
            return []

        # 发布服务可用事件
        await event_bus.emit(
            "service.registered",
            {"service_name": "dg_lab_control", "service": self._service_provider},
            "DGLabServicePlugin",
        )

        return []  # 服务提供者不返回Provider列表

    async def cleanup(self):
        """清理资源"""
        self.logger.info("开始清理 DGLabServicePlugin...")

        if self._service_provider:
            try:
                await self._service_provider.cleanup()
            except Exception as e:
                self.logger.error(f"清理ServiceProvider时出错: {e}", exc_info=True)

        self._service_provider = None
        self.logger.info("DGLabServicePlugin 清理完成")

    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        return {
            "name": "DGLabService",
            "version": "1.0.0",
            "author": "Amaidesu Team",
            "description": "DG-Lab服务插件，提供控制 DG-LAB 硬件的服务",
            "category": "software",
            "api_version": "1.0",
        }


# 插件入口点
plugin_entrypoint = DGLabServicePlugin
