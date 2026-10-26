"""模型连通性测试服务

模型页"测试"按钮的数据面：按提供商名从已保存配置解析连接参数，对模型
标识发起一次最小聊天请求，验证端点可达、密钥有效、模型可响应三件事。

前端只报名字不传凭据：连接参数与密钥以落盘配置为唯一权威（改完先保存
再测试），密钥全程不出服务端。不复用 OpenAIClient / Engine：测试要回答
的是"这份配置此刻通不通"，装配态客户端可能落后于刚保存的编辑（model 域
不走热应用），引擎的空响应校验也不适用（探测请求 max_tokens 很小，正文
为空但 finish_reason=length 属正常可达）。与生产调用共享同一条 compat
适配（鉴权方式 / 默认 header 与 query 均生效）。
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

from openai import (
    APITimeoutError,
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    RateLimitError,
)

from src.modules.dashboard.schemas.llm import LLMModelTestResponse
from src.modules.llm.clients.openai.compat import build_openai_compatible_client_config
from src.modules.logging import get_logger

if TYPE_CHECKING:
    from src.modules.config.service import ConfigService

logger = get_logger("ModelTestService")

# 探测请求的输出上限：只验证"模型能响应"，不追求拿到正文；过小（=1）会
# 在思考型模型上立刻触发截断，16 足够覆盖探针语义且成本可忽略
_PROBE_MAX_TOKENS = 16

# 失败原因携带的服务端响应体长度上限（与客户端错误消息同口径的展示考量）
_MAX_ERROR_DETAIL_CHARS = 500


def _server_error_detail(exc: BaseException) -> str:
    """提取服务端错误响应文本（压成单行、超长截断），供失败原因携带定位上下文。"""
    body = getattr(exc, "body", None)
    text = " ".join(str(body).split()) if body else " ".join(str(exc).split())
    if len(text) > _MAX_ERROR_DETAIL_CHARS:
        text = text[:_MAX_ERROR_DETAIL_CHARS] + "…（截断）"
    return text


def _probe_client(provider_cfg: Dict[str, Any], timeout_ms: int) -> AsyncOpenAI:
    """按已保存的提供商配置构造一次性探测客户端（不重试：重试会让时延失真）。"""
    client_config = build_openai_compatible_client_config(provider_cfg)
    return AsyncOpenAI(
        # 密钥未配置时与生产同口径以占位符兜底（本地端点普遍不校验），
        # 需要真实 Key 的端点会在响应里以 401 暴露
        api_key=client_config.api_key or "sk-dummy",
        base_url=client_config.base_url or None,
        default_headers=client_config.default_headers or None,
        default_query=client_config.default_query or None,
        timeout=timeout_ms / 1000,
        max_retries=0,
    )


def find_saved_provider(config_service: Optional["ConfigService"], name: str) -> Optional[Dict[str, Any]]:
    """按名取已保存的提供商条目（连接参数与密钥的唯一权威来源）。

    测试以落盘配置为准：前端表单里的敏感字段是 GET /config 的遮蔽占位，
    不作为请求输入；未注入配置服务或落盘无同名提供商返回 None（端点据此
    给"先保存再测试"的明确提示）。
    """
    if config_service is None or name.strip() == "":
        return None
    providers = config_service.get_file_section("model").get("llm_providers", [])
    for entry in providers:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip() == name.strip():
            return entry
    return None


async def test_model_connectivity(
    provider_cfg: Dict[str, Any],
    model: str,
    timeout_ms: int,
) -> LLMModelTestResponse:
    """对给定连接参数发起最小聊天请求，返回可达性与失败原因。

    业务级失败（密钥错误、模型不存在、端点不可达等）以 ``ok=False`` + 平实
    中文原因返回而非 HTTP 错误——对"测试"动作而言这些都是正常测出的结果。
    """
    base_url = str(provider_cfg.get("base_url") or "").strip()
    if not base_url:
        return LLMModelTestResponse(ok=False, error="API 端点未配置（base_url 为空）")

    client = _probe_client(provider_cfg, timeout_ms)
    started = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=_PROBE_MAX_TOKENS,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        finish_reason = response.choices[0].finish_reason if response.choices else None
        logger.info(f"模型连通性测试通过: model={model}, 端点={base_url}, 时延={latency_ms}ms")
        return LLMModelTestResponse(
            ok=True,
            latency_ms=latency_ms,
            model=response.model,
            finish_reason=finish_reason,
        )
    except asyncio.CancelledError:
        raise
    except (APITimeoutError, asyncio.TimeoutError):
        logger.warning(f"模型连通性测试超时: model={model}, 端点={base_url}", exc=True)
        return LLMModelTestResponse(ok=False, error=f"请求超时（{timeout_ms}ms 内无响应），端点不可达或服务过慢")
    except APIConnectionError as e:
        logger.warning(f"模型连通性测试连接失败: model={model}, 端点={base_url}", exc=True)
        return LLMModelTestResponse(
            ok=False, error=f"网络连接失败，请检查端点地址与网络可达性（{_server_error_detail(e)}）"
        )
    except RateLimitError as e:
        logger.warning(f"模型连通性测试被限流: model={model}, 端点={base_url}", exc=True)
        return LLMModelTestResponse(ok=False, error=f"请求过于频繁（HTTP 429）：{_server_error_detail(e)}")
    except APIStatusError as e:
        detail = _server_error_detail(e)
        logger.warning(f"模型连通性测试被拒绝: model={model}, 端点={base_url}, HTTP {e.status_code}", exc=True)
        if e.status_code in (401, 403):
            return LLMModelTestResponse(ok=False, error=f"鉴权失败（HTTP {e.status_code}），请检查 API 密钥：{detail}")
        if e.status_code == 404:
            return LLMModelTestResponse(
                ok=False,
                error=f"端点或模型不存在（HTTP 404），请核对端点路径与模型标识：{detail}",
            )
        return LLMModelTestResponse(ok=False, error=f"服务端拒绝请求（HTTP {e.status_code}）：{detail}")
    except UnicodeEncodeError:
        # 密钥/Header 含非 ASCII 字符时 httpx 构造请求头即抛；多为把界面上的
        # "已设置"占位或其他非密钥文本当成了密钥手工填入
        logger.warning(f"模型连通性测试请求编码失败: model={model}, 端点={base_url}", exc=True)
        return LLMModelTestResponse(
            ok=False, error="请求构造失败：密钥或自定义 Header 含非 ASCII 字符，请检查是否误填了占位文案或非密钥文本"
        )
    except Exception as e:
        logger.exception(f"模型连通性测试异常: model={model}, 端点={base_url}: {e}")
        return LLMModelTestResponse(ok=False, error=f"测试执行失败：{type(e).__name__}: {e}")
    finally:
        await client.close()
