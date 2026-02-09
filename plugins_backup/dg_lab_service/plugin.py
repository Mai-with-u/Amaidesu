# src/plugins/dg_lab_service/plugin.py

import asyncio
from typing import Any, Dict, Optional

try:
    import aiohttp
except ImportError:
    aiohttp = None

from src.core.plugin_manager import BasePlugin
from src.core.amaidesu_core import AmaidesuCore


class DGLabServicePlugin(BasePlugin):
    """
    提供一个用于控制 DG-LAB 硬件的服务。
    这个插件本身不监听任何消息，只提供一个可供其他插件调用的服务。
    """

    def __init__(self, core: AmaidesuCore, plugin_config: Dict[str, Any]):
        super().__init__(core, plugin_config)

        self.config = self.plugin_config

        if aiohttp is None:
            self.logger.error("aiohttp 库未找到，请运行 `pip install aiohttp`。DGLabServicePlugin 已禁用。")
            return

        # --- 获取配置值 ---
        self.api_base_url = self.config.get("dg_lab_api_base_url", "http://127.0.0.1:8081").rstrip("/")
        self.default_strength = self.config.get("target_strength", 10)
        self.default_waveform = self.config.get("target_waveform", "big")
        self.shock_duration = self.config.get("shock_duration_seconds", 2)
        self.timeout = aiohttp.ClientTimeout(total=self.config.get("request_timeout", 5))

        # --- 状态 ---
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.control_lock = asyncio.Lock()  # 确保同一时间只有一个 shock 序列在运行

    async def setup(self):
        await super().setup()

        if not aiohttp:
            self.logger.error("aiohttp 未安装，插件无法运行。")
            return

        self.http_session = aiohttp.ClientSession(timeout=self.timeout)
        self.logger.info("aiohttp.ClientSession 已创建。")

        # --- 将自身注册为服务 ---
        self.core.register_service("dg_lab_control", self)
        self.logger.info("已将自身注册为 'dg_lab_control' 服务。")

    async def cleanup(self):
        self.logger.info("正在清理 DGLabServicePlugin...")
        if self.http_session:
            await self.http_session.close()
            self.logger.info("aiohttp.ClientSession 已关闭。")
            self.http_session = None

        # 理论上服务也应该取消注册，但核心目前没有提供 unregister_service

        await super().cleanup()
        self.logger.info("DGLabServicePlugin 清理完成。")

    async def trigger_shock(
        self,
        strength: Optional[int] = None,
        waveform: Optional[str] = None,
        duration: Optional[float] = None,
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

            # --- 初始设置 ---
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

            # --- 等待 ---
            await asyncio.sleep(duration_to_use)

            # --- 强度归零 ---
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
