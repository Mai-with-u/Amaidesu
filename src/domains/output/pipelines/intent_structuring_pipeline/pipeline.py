"""Intent Structuring Pipeline Implementation"""

import json
from typing import TYPE_CHECKING, Optional

from src.domains.output.pipelines.base import OutputPipelineBase
from src.modules.llm.manager import ClientType

if TYPE_CHECKING:
    from src.modules.types import Intent


class IntentStructuringPipeline(OutputPipelineBase):
    """使用 LLM 将 Intent 结构化为 provider 参数"""

    priority = 100

    async def _process(self, intent: "Intent") -> Optional["Intent"]:
        if intent.structured_params:
            self.logger.debug("Intent 已有结构化参数，跳过")
            return intent

        if not self.context:
            self.logger.warning("缺少 OutputPipelineContext，无法结构化")
            return intent

        capability_registry = self.context.capability_registry
        llm_service = self.context.llm_service
        prompt_service = self.context.prompt_service

        if not all([capability_registry, llm_service, prompt_service]):
            self.logger.warning("缺少必要服务，无法结构化")
            return intent

        try:
            capabilities = capability_registry.get_all()
            provider_capabilities = {
                cap.provider_name: {"emotions": cap.emotions, "actions": cap.actions} for cap in capabilities
            }

            prompt = prompt_service.render(
                "output/intent_structuring",
                emotion=intent.emotion or "neutral",
                action=intent.action or "",
                provider_capabilities=provider_capabilities,
            )

            response = await llm_service.chat(
                prompt=prompt,
                client_type=ClientType.LLM_FAST,
            )

            if not response.success or not response.content:
                self.logger.warning(f"LLM 调用失败: {response.error}")
                return intent

            parsed = json.loads(response.content)
            intent.structured_params = parsed
            self.logger.debug(f"Intent 结构化完成: {parsed}")

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON 解析失败: {e}")
        except Exception as e:
            self.logger.error(f"结构化失败: {e}", exc_info=True)

        return intent
