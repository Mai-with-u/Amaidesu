"""
HotkeyMatcher - VTS 热键匹配器

负责 VTS 热键列表获取、触发与 LLM 辅助匹配。
"""

import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional

from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.prompts.manager import PromptManager


LLM_AVAILABLE = False
try:
    import openai  # noqa: F401

    LLM_AVAILABLE = True
except ImportError:
    pass


class HotkeyMatcher:
    """VTS 热键匹配器（含 LLM 辅助）"""

    def __init__(
        self,
        *,
        logger_name: str,
        is_connected: Callable[[], bool],
        vts_request: Callable[..., Coroutine[Any, Any, Any]],
        prompt_service: Optional["PromptManager"] = None,
        openai_client: Optional[Any] = None,
        llm_model: str = "gpt-4o-mini",
        llm_temperature: float = 0.7,
        llm_max_tokens: int = 50,
        llm_matching_enabled: bool = False,
    ):
        self.logger = get_logger(logger_name)
        self._is_connected = is_connected
        self._vts_request = vts_request
        self._prompt_service = prompt_service
        self._openai_client = openai_client
        self._llm_model = llm_model
        self._llm_temperature = llm_temperature
        self._llm_max_tokens = llm_max_tokens
        self._llm_matching_enabled = llm_matching_enabled

        self.hotkey_list: List[Dict[str, Any]] = []
        self.hotkey_list_last_update: float = 0.0

    def find_by_name(self, prefix: str, suffix: str = "") -> Optional[str]:
        if not self.hotkey_list:
            return None
        target_name = f"{prefix}_{suffix}" if suffix else prefix
        for hotkey in self.hotkey_list:
            if hotkey.get("name") == target_name:
                return hotkey.get("hotkeyID")
        for hotkey in self.hotkey_list:
            name = hotkey.get("name", "")
            if name.startswith(prefix):
                if not suffix or suffix in name:
                    return hotkey.get("hotkeyID")
        return None

    async def load_hotkeys(self) -> None:
        if not self._is_connected() or not self._vts_request:
            self.logger.warning("VTS未连接，跳过加载热键列表")
            return
        try:
            response = await self._vts_request.requestHotKeyList()
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
            request_msg = self._vts_request.requestTriggerHotKey(hotkeyID=hotkey_id)
            response = await self._vts_request(request_msg)
            if response and response.get("messageType") == "HotkeyTriggerResponse":
                self.logger.debug(f"热键 {hotkey_id} 触发成功")
                return True
            self.logger.warning(f"热键 {hotkey_id} 触发失败: {response}")
            return False
        except Exception as e:
            self.logger.error(f"触发热键失败: {hotkey_id}: {e}")
            return False

    async def find_best_match_with_llm(self, text: str) -> Optional[str]:
        if not self._llm_matching_enabled or not LLM_AVAILABLE:
            return None
        if not self.hotkey_list:
            self.logger.warning("热键列表为空，无法使用LLM匹配")
            return None

        hotkey_str = "\n".join([f"- {hotkey.get('name', '')}" for hotkey in self.hotkey_list])

        if not self._prompt_service:
            self.logger.debug("prompt_service 未注入,LLM hotkey matching 降级为关闭")
            return None
        prompt = self._prompt_service.render("output/vts_hotkey", text=text, hotkey_list_str=hotkey_str)

        try:
            if not self._openai_client:
                self.logger.warning("LLM客户端未初始化")
                return None

            response = self._openai_client.chat.completions.create(
                model=self._llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self._llm_temperature,
                max_tokens=self._llm_max_tokens,
            )

            if response and response.choices:
                selected_hotkey = response.choices[0].message.content.strip()
                if selected_hotkey != "NONE" and selected_hotkey in [
                    hotkey.get("name", "") for hotkey in self.hotkey_list
                ]:
                    self.logger.debug(f"LLM为文本'{text[:30]}...'选择了热键: {selected_hotkey}")
                    return selected_hotkey
                self.logger.debug(f"LLM认为文本'{text[:30]}...'没有合适的热键匹配")
                return None
            self.logger.warning("LLM API返回了无效响应")
            return None
        except Exception as e:
            self.logger.error(f"LLM匹配热键失败: {e}")
            return None
