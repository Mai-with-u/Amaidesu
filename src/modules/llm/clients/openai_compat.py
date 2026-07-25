"""OpenAI 兼容客户端的鉴权配置工具。"""

# pyright: reportDeprecated=false

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class OpenAICompatibleAuthType(str, Enum):
    """OpenAI 兼容接口支持的鉴权方式。"""

    BEARER = "bearer"
    HEADER = "header"
    QUERY = "query"
    NONE = "none"


@dataclass(slots=True)
class OpenAICompatibleClientConfig:
    """OpenAI 兼容客户端的基础配置。"""

    api_key: str
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict)
    default_query: dict[str, object] = field(default_factory=dict)


def normalize_openai_base_url(base_url: str) -> str:
    """去掉基础地址尾部斜杠，并在缺少协议时补全 http://。"""
    base_url = base_url.strip()
    if base_url and "://" not in base_url:
        base_url = "http://" + base_url
    return base_url.rstrip("/")


def _build_auth_header_value(prefix: str, api_key: str) -> str:
    """按需拼接鉴权请求头前缀与密钥。"""
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return api_key
    return f"{normalized_prefix} {api_key}"


def build_openai_compatible_client_config(
    provider_config: Dict[str, Any],
) -> OpenAICompatibleClientConfig:
    """根据字典形式的提供商配置构建 OpenAI 兼容客户端配置。"""
    api_key = str(provider_config.get("api_key", ""))
    auth_type = OpenAICompatibleAuthType(provider_config.get("auth_type", "bearer"))
    auth_header_name = str(provider_config.get("auth_header_name", "Authorization"))
    auth_header_prefix = str(provider_config.get("auth_header_prefix", "Bearer"))
    auth_query_name = str(provider_config.get("auth_query_name", "api_key"))
    default_headers = dict(provider_config.get("default_headers") or {})
    default_query: dict[str, object] = dict(provider_config.get("default_query") or {})
    client_api_key = api_key

    if auth_type == OpenAICompatibleAuthType.BEARER:
        if auth_header_name != "Authorization" or auth_header_prefix.strip() != "Bearer":
            client_api_key = ""
            default_headers[auth_header_name] = _build_auth_header_value(
                auth_header_prefix,
                api_key,
            )
    elif auth_type == OpenAICompatibleAuthType.HEADER:
        client_api_key = ""
        default_headers[auth_header_name] = _build_auth_header_value(
            auth_header_prefix,
            api_key,
        )
    elif auth_type == OpenAICompatibleAuthType.QUERY:
        client_api_key = ""
        default_query[auth_query_name] = api_key
    elif auth_type == OpenAICompatibleAuthType.NONE:
        client_api_key = ""

    return OpenAICompatibleClientConfig(
        api_key=client_api_key,
        base_url=normalize_openai_base_url(str(provider_config.get("base_url", ""))),
        default_headers=default_headers,
        default_query=default_query,
    )
