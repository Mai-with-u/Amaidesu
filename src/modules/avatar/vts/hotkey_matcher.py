"""
HotkeyMatcher - VTS 热键匹配器

负责 VTS 热键列表获取、按名解析与触发。
"""

import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

from src.modules.logging import get_logger


class HotkeyMatcher:
    """VTS 热键匹配器"""

    def __init__(
        self,
        *,
        logger_name: str,
        is_connected: Callable[[], bool],
        vts_request: Callable[..., Coroutine[Any, Any, Any]],
    ):
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected
        self._vts_request = vts_request

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
        if not self._is_connected() or not self._vts_request:
            self.logger.warning("VTS未连接，跳过加载热键列表")
            return
        try:
            request_msg = self._vts_request.vts_request.requestHotKeyList()
            response = await self._vts_request(request_msg)
            if response and response.get("data") and "availableHotkeys" in response["data"]:
                hotkeys = response["data"]["availableHotkeys"]
                self.hotkey_list = hotkeys
                self.hotkey_list_last_update = time.time()
                self.logger.info(f"成功加载 {len(hotkeys)} 个热键")
            else:
                self.logger.warning(f"获取热键列表失败: {response}")
        except Exception as e:
            self.logger.error(f"获取热键列表失败: {e}")

    async def trigger_hotkey(self, hotkey_id: str) -> bool:
        if not self._is_connected():
            self.logger.warning(f"VTS未连接，无法触发热键: {hotkey_id}")
            return False
        try:
            self.logger.debug(f"触发热键: {hotkey_id}")
            request_msg = self._vts_request.vts_request.requestTriggerHotKey(hotkeyID=hotkey_id)
            response = await self._vts_request(request_msg)
            if response and response.get("messageType") == "HotkeyTriggerResponse":
                self.logger.debug(f"热键 {hotkey_id} 触发成功")
                return True
            self.logger.warning(f"热键 {hotkey_id} 触发失败: {response}")
            return False
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")
            return False
