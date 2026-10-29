"""
HotkeyMatcher - VTS 热键匹配器

负责 VTS 热键列表获取、按名解析与触发。
"""

import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from src.modules.logging import get_logger

# VTS 请求入口：(消息类型, 数据) → 回复的 data；VTS 报错时抛异常
VtsRequest = Callable[[str, Optional[Dict[str, Any]]], Awaitable[Dict[str, Any]]]


class HotkeyMatcher:
    """VTS 热键匹配器"""

    def __init__(
        self,
        *,
        logger_name: str,
        is_connected: Callable[[], bool],
        request: VtsRequest,
    ) -> None:
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected
        self._request = request

        self.hotkey_list: List[Dict[str, Any]] = []
        self.hotkey_list_last_update: float = 0.0

    def find_by_name(self, name: str) -> Optional[str]:
        """按热键名解析 hotkeyID：精确匹配优先，前缀匹配兜底。"""
        if not self.hotkey_list:
            return None
        for hotkey in self.hotkey_list:
            if hotkey.get("name") == name:
                return hotkey.get("hotkeyID")
        for hotkey in self.hotkey_list:
            if str(hotkey.get("name", "")).startswith(name):
                return hotkey.get("hotkeyID")
        return None

    async def load_hotkeys(self) -> None:
        """拉取当前模型的热键清单（连接建立与换模型时调用）。"""
        if not self._is_connected():
            self.logger.warning("VTS未连接，跳过加载热键列表")
            return
        try:
            data = await self._request("HotkeysInCurrentModelRequest", None)
            hotkeys = data.get("availableHotkeys")
            if isinstance(hotkeys, list):
                self.hotkey_list = hotkeys
                self.hotkey_list_last_update = time.time()
                self.logger.info(f"成功加载 {len(hotkeys)} 个热键")
            else:
                self.logger.warning(f"获取热键列表失败: {data}")
        except Exception as e:
            self.logger.error(f"获取热键列表失败: {e}")

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        """按 hotkeyID 触发一个热键；未连接或 VTS 报错时返回 False。"""
        if not self._is_connected():
            self.logger.warning(f"VTS未连接，无法触发热键: {hotkey_id}")
            return False
        try:
            self.logger.debug(f"触发热键: {hotkey_id}")
            await self._request("HotkeyTriggerRequest", {"hotkeyID": hotkey_id})
            return True
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")
            return False
