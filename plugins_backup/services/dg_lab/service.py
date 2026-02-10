"""
DGLab 服务实现

提供 DG-LAB 硬件控制服务，支持电击强度、波形和持续时间控制。
"""

import asyncio
from typing import Any, Dict, Optional

try:
    import aiohttp
    from aiohttp import ClientConnectorError, ClientTimeout
except ImportError:
    aiohttp = None
    ClientTimeout = None
    ClientConnectorError = None

from pydantic import BaseModel, Field
from src.core.utils.logger import get_logger
from src.services.dg_lab.config import DGLabConfig, WaveformPreset


class ShockStatus(BaseModel):
    """电击状态"""

    is_running: bool = Field(default=False, description="是否正在运行")
    current_strength: int = Field(default=0, description="当前强度")
    current_waveform: str = Field(default="", description="当前波形")


class DGLabService:
    """
    DG-LAB 硬件控制服务

    这是一个共享服务，不是 Provider：
    - 不产生数据流
    - 不订阅事件
    - 提供共享 API 供其他组件调用

    职责：
    - 硬件连接管理（通过 HTTP API）
    - 参数编码/解码（强度、波形、通道）
    - 安全限制（最大强度、超时保护）

    生命周期：
    1. 创建实例（传入配置）
    2. setup() - 初始化 HTTP 会话
    3. trigger_shock() - 触发电击序列
    4. cleanup() - 清理资源

    Attributes:
        config: 服务配置
        is_initialized: 是否已初始化
    """

    def __init__(self, config: DGLabConfig):
        """
        初始化 DGLab 服务

        Args:
            config: 服务配置
        """
        self.config = config
        self.logger = get_logger("DGLabService")
        self.is_initialized = False

        # HTTP 会话
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._timeout: Optional[ClientTimeout] = None

        # 控制锁（确保同一时间只有一个 shock 序列在运行）
        self._control_lock: asyncio.Lock = asyncio.Lock()

        # 状态
        self._status = ShockStatus()

        # 检查依赖
        if aiohttp is None:
            self.logger.error("aiohttp 库未找到，请运行 `uv add aiohttp`。DGLabService 将无法正常工作。")

    async def setup(self) -> None:
        """
        初始化服务

        创建 HTTP 会话并连接到 DG-Lab API。
        """
        if self.is_initialized:
            self.logger.warning("DGLabService 已经初始化，跳过重复初始化")
            return

        if aiohttp is None:
            raise RuntimeError("aiohttp 未安装，无法初始化 DGLabService")

        self._timeout = ClientTimeout(total=self.config.request_timeout)
        self._http_session = aiohttp.ClientSession(timeout=self._timeout)

        self.is_initialized = True
        self.logger.info(
            f"DGLabService 初始化完成 (API: {self.config.api_base_url}, "
            f"最大强度: {self.config.max_strength}, "
            f"安全限制: {'启用' if self.config.enable_safety_limit else '禁用'})"
        )

    async def cleanup(self) -> None:
        """
        清理服务资源

        关闭 HTTP 会话。
        """
        if not self.is_initialized:
            return

        if self._http_session:
            await self._http_session.close()
            self._http_session = None
            self.logger.info("HTTP 会话已关闭")

        self.is_initialized = False
        self.logger.info("DGLabService 清理完成")

    async def trigger_shock(
        self,
        strength: Optional[int] = None,
        waveform: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> bool:
        """
        触发电击序列

        流程：
        1. 设置通道 A/B 的强度和波形
        2. 等待指定持续时间
        3. 将强度归零

        Args:
            strength: 强度 (0-200)，为 None 时使用配置默认值
            waveform: 波形预设，为 None 时使用配置默认值
            duration: 持续时间（秒），为 None 时使用配置默认值

        Returns:
            bool: 是否成功执行

        安全限制：
        - 如果启用安全限制，强度不会超过 max_strength
        - 如果已有电击序列在运行，本次调用会被忽略
        """
        if not self.is_initialized:
            self.logger.error("DGLabService 未初始化，无法触发电击")
            return False

        if self._control_lock.locked():
            self.logger.warning("电击序列正在进行中，本次触发被忽略")
            return False

        # 应用安全限制
        effective_strength = strength if strength is not None else self.config.default_strength
        if self.config.enable_safety_limit:
            effective_strength = min(effective_strength, self.config.max_strength)

        effective_waveform = waveform if waveform is not None else self.config.default_waveform
        effective_duration = duration if duration is not None else self.config.shock_duration_seconds

        # 验证波形
        if effective_waveform not in WaveformPreset.ALL:
            self.logger.error(f"无效的波形预设 '{effective_waveform}'，可选: {WaveformPreset.ALL}")
            return False

        async with self._control_lock:
            self._status.is_running = True
            self._status.current_strength = effective_strength
            self._status.current_waveform = effective_waveform

            self.logger.info(
                f"触发电击序列: 强度={effective_strength}, 波形='{effective_waveform}', 持续时间={effective_duration}s"
            )

            try:
                # 构建请求
                strength_url = f"{self.config.api_base_url}/control/strength"
                waveform_url = f"{self.config.api_base_url}/control/waveform"
                headers = {"Content-Type": "application/json"}

                # 初始设置（并行执行）
                initial_tasks = [
                    self._make_api_call(
                        strength_url,
                        {"channel": "a", "strength": effective_strength},
                        headers,
                        "设置通道 A 强度",
                    ),
                    self._make_api_call(
                        strength_url,
                        {"channel": "b", "strength": effective_strength},
                        headers,
                        "设置通道 B 强度",
                    ),
                    self._make_api_call(
                        waveform_url,
                        {"channel": "a", "preset": effective_waveform},
                        headers,
                        "设置通道 A 波形",
                    ),
                    self._make_api_call(
                        waveform_url,
                        {"channel": "b", "preset": effective_waveform},
                        headers,
                        "设置通道 B 波形",
                    ),
                ]

                results = await asyncio.gather(*initial_tasks, return_exceptions=True)

                # 检查是否有失败（False 或异常）
                if any(isinstance(r, Exception) or r is False for r in results):
                    self.logger.error("部分初始设置失败，取消电击序列")
                    return False

                # 等待持续时间
                await asyncio.sleep(effective_duration)

                # 强度归零（并行执行）
                self.logger.info("电击持续时间结束，将强度归零")
                reset_tasks = [
                    self._make_api_call(
                        strength_url,
                        {"channel": "a", "strength": 0},
                        headers,
                        "重置通道 A 强度",
                    ),
                    self._make_api_call(
                        strength_url,
                        {"channel": "b", "strength": 0},
                        headers,
                        "重置通道 B 强度",
                    ),
                ]

                reset_results = await asyncio.gather(*reset_tasks, return_exceptions=True)

                if any(isinstance(r, Exception) or r is False for r in reset_results):
                    self.logger.warning("部分重置操作失败，请手动检查设备状态")

                self.logger.info("电击序列完成")
                return True

            except Exception as e:
                self.logger.error(f"电击序列执行失败: {e}", exc_info=True)
                return False

            finally:
                self._status.is_running = False
                self._status.current_strength = 0
                self._status.current_waveform = ""

    async def _make_api_call(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        description: str,
    ) -> bool:
        """
        执行单个 HTTP API 调用

        Args:
            url: API URL
            payload: 请求负载
            headers: 请求头
            description: 操作描述（用于日志）

        Returns:
            bool: 是否成功
        """
        if not self._http_session:
            self.logger.error(f"{description} 失败: HTTP 会话未初始化")
            return False

        try:
            async with self._http_session.post(url, json=payload, headers=headers) as response:
                if response.status == 200:
                    self.logger.debug(f"API 调用成功: {description}")
                    return True
                else:
                    error_text = await response.text()
                    self.logger.error(f"API 调用失败: {description} (状态码: {response.status}, 响应: {error_text})")
                    return False

        except ClientConnectorError as e:
            self.logger.error(f"API 连接失败: {description}。无法连接到 {self.config.api_base_url}。错误: {e}")
            return False

        except asyncio.TimeoutError:
            self.logger.error(f"API 调用超时: {description}")
            return False

        except Exception as e:
            self.logger.error(f"API 调用时发生未知错误: {description}。错误: {e}", exc_info=True)
            return False

    def get_status(self) -> ShockStatus:
        """
        获取当前状态

        Returns:
            ShockStatus: 当前状态
        """
        return self._status.model_copy()

    def is_ready(self) -> bool:
        """
        检查服务是否就绪

        Returns:
            bool: 是否已初始化且可用
        """
        return self.is_initialized and self._http_session is not None
