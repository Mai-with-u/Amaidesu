"""LLM 调用观测出库 - 一次调用的旁路观测编排

一次 LLM 调用（成功或失败）的观测出库：上下文分段估算 + 两账落库
（``llm_usage`` / ``llm_requests``，经 observation 模块既有入口）+ 请求
正文出库到诊断文件 + 每模型上下文水位快照。全部是旁路观测——任何
落库/出库失败只告警，不阻断调用链。

与调用编排（选模型、超时、重试、故障切换）零共享：编排方在成功/失败
出口各调一次 :meth:`CallObserver.record_call`，本类自己完成其余一切。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.modules.diagnostics.writer import DiagnosticsWriter
from src.modules.llm.context_meter import ContextBreakdown, estimate_breakdown, extract_request_sections
from src.modules.llm.observation import record_call_outcome
from src.modules.llm.payload import Response, ToolCall
from src.modules.logging import get_logger
from src.modules.storage.repos import LLMRepo

__all__ = ["CallObserver"]


def _tool_calls_to_protocol(tool_calls: List[ToolCall]) -> List[Dict[str, Any]]:
    """中立 ToolCall → OpenAI 嵌套 dict 形态（诊断文件 ``tool_calls`` 字段的落盘形状）。

    诊断正文按 ``request_id`` 与库行关联、由 Dashboard 读侧回查渲染，该文件
    形态是既有读侧契约（``{"id", "type", "function": {"name", "arguments"}}``，
    arguments 为对象），与喂回多轮 ReAct 历史的形态一致，不随引擎内部契约
    归一改变。
    """
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": tc.arguments},
            **(
                {"raw_arguments": tc.raw_arguments, "arguments_error": tc.arguments_error}
                if tc.arguments_error is not None
                else {}
            ),
        }
        for tc in tool_calls
    ]


class CallObserver:
    """一次 LLM 调用的观测出库：两账落库 + 诊断正文出库 + 上下文水位快照。

    依赖全部构造器注入：仓储（llm_usage/llm_requests 落库）、场次解析器、
    诊断写侧（logs/llm_requests/ 小时流与 llm_context 快照）、模型索引
    （模型引用名 → provider 归属）与价格表（费用计算唯一口径）。模型索引
    与价格表按引用注入，LLMManager.setup 重建表时原地更新，引用始终有效。
    """

    def __init__(
        self,
        llm_repo: Optional[LLMRepo],
        live_session_resolver: Optional[Callable[[], Optional[int]]],
        diagnostics_writer: Optional[DiagnosticsWriter],
        model_index: Dict[str, Tuple[Dict[str, Any], str]],
        model_prices: Dict[str, Dict[str, Any]],
    ) -> None:
        self.logger = get_logger("CallObserver")
        # 注入后每次成功调用旁路写一条 llm_usage（失败降级不阻断调用）；None 时不落库
        self._llm_repo = llm_repo
        # 注入后每次落库解析当前直播场次主键写入 llm_usage.live_session_id；
        # None（未注入或无进行中场次）落 NULL = 场间消耗。解析失败只告警
        self._live_session_resolver = live_session_resolver
        # 注入后每次调用（成功/失败）把请求正文出库到诊断文件（logs/llm_requests/
        # 按小时流）；None 时不写文件。fire-and-forget 旁路，失败不阻断调用链
        self._diagnostics_writer = diagnostics_writer
        # 模型引用名 -> (模型配置, provider 注册名)，落库时反查 provider 归属（按引用共享）
        self._model_index = model_index
        # 价格表（{model_identifier: {price_in, price_out, ...}}），费用计算唯一口径（按引用共享）
        self._model_prices = model_prices

    async def record_call(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        request_type: str,
        result: Response,
        kwargs: Dict[str, Any],
        start_time: float,
    ) -> None:
        """一次调用（成功或失败）的观测出库单一入口。

        成功调用先估算上下文分段占用（监控口径，失败返回 None 不阻断调用链）。
        其后两账落账（经 observation ``record_call_outcome``：成功且带 usage 时
        ``llm_usage`` + ``llm_requests`` 同事务，失败行 success=False 与无
        usage 的成功调用只写请求明细）；诊断正文出库（成功/失败都写——失败
        请求的上下文正是排障的第一手材料）。任何落账失败只告警，不阻断调用链。

        Args:
            request_id: 请求标识（库行与诊断正文关联键）
            profile_name: LLM 用途 profile 名
            model_name: 引擎侧模型引用名（落账键）
            request_type: 调用入口（generate / generate_vision / generate_audio）
            result: 本次调用的响应（成功或失败）
            kwargs: 实际发起调用的参数（请求参数快照的唯一输入）
            start_time: 调用起点（``time.time()`` 秒，耗时按差值换算毫秒）
        """
        breakdown: Optional[ContextBreakdown] = None
        if result.success:
            breakdown = self._build_context_breakdown(
                request_id=request_id,
                profile_name=profile_name,
                model_name=model_name,
                result=result,
                kwargs=kwargs,
            )
        await self._record_call_outcome(
            request_id=request_id,
            profile_name=profile_name,
            model_name=model_name,
            request_type=request_type,
            result=result,
            kwargs=kwargs,
            start_time=start_time,
            breakdown=breakdown,
        )

    async def _record_call_outcome(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        request_type: str,
        result: Response,
        kwargs: Dict[str, Any],
        start_time: float,
        breakdown: Optional[ContextBreakdown] = None,
    ) -> None:
        """出账落库（经 observation 既有入口，不自行写表）+ 诊断正文出库。"""
        request_params = self._build_request_params(kwargs)
        if self._llm_repo is not None:
            try:
                provider_name = "unknown"
                if model_name in self._model_index:
                    _, provider_name = self._model_index[model_name]
                # 场次归属：注入了解析器时取当前显式场次主键；无进行中场次落
                # NULL = 场间消耗（归因口径与 live_sessions 主键对齐）
                live_session_id: Optional[int] = None
                if self._live_session_resolver is not None:
                    try:
                        live_session_id = self._live_session_resolver()
                    except Exception as exc:  # noqa: BLE001 - 归属解析失败不阻断落账
                        self.logger.warning(f"llm_usage 场次归属解析失败（落 NULL）: {exc}")
                        live_session_id = None
                await record_call_outcome(
                    self._llm_repo,
                    request_id=request_id,
                    profile_name=profile_name,
                    # 落账键必须用引擎自己的模型引用名：上游/网关会在响应的 model
                    # 字段里回自己的别名（如实测 sheep 网关回 gemini-3.8-flash-n），
                    # 响应名一旦入库会污染计价查表、模型筛选与费用归因口径
                    model_name=model_name or "unknown",
                    request_type=request_type,
                    provider_name=provider_name,
                    model_prices=self._model_prices,
                    success=result.success,
                    error=result.error,
                    response_content=result.content,
                    latency_ms=int((time.time() - start_time) * 1000),
                    request_params=request_params,
                    usage=result.usage.model_dump() if result.usage is not None else None,
                    live_session_id=live_session_id,
                )
            except Exception as exc:  # noqa: BLE001 记账旁路：失败不阻断调用链
                self.logger.warning(f"LLM 调用出账落库失败: {exc}", exc=True)
        self._write_request_payload(
            request_id=request_id,
            profile_name=profile_name,
            model_name=model_name or "unknown",
            request_params=request_params,
            result=result,
            breakdown=breakdown,
        )

    @staticmethod
    def _build_request_params(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """从调用 kwargs 提取请求参数快照（两账落库与请求历史共用）。

        请求参数快照是下游观测（预览摘要 / 图像计数 / 诊断正文）的唯一
        输入，因此这里产出**唯一归一化形状**：messages 恒为中立 payload
        dump（文本与图像片段在 ``parts``，不出现 OpenAI ``content`` 键），
        全部从入站归一化后的 :class:`GenerateRequest` 取值。system 不在
        messages 里，单独快照，否则提示词预览缺主提示词。

        思考强度档位（reasoning_effort）按白名单语义入快照：None / 缺省 /
        空串均不进快照（避免把"未配置"误显示为"显式置 None"），单次调用
        覆盖值优先于请求内档位。``request`` 缺失（非经 generate 入口的
        直连调用）返回空快照，下游按无消息处理。
        """
        request = kwargs.get("request")
        if request is None:
            return {}
        request_params: Dict[str, Any] = {
            "system": request.system,
            "messages": [m.model_dump() for m in request.messages],
            "tools": [t.model_dump() for t in request.tools] or None,
            # 生成参数只从请求对象取：Engine 已在单点定序（显式参数 > 请求字段 >
            # profile 档默认值）并写进请求对象
            "temperature": request.temperature,
        }
        if isinstance(request.reasoning_effort, str) and request.reasoning_effort:
            request_params["reasoning_effort"] = request.reasoning_effort
        if request.strict_tool_arguments:
            request_params["strict_tool_arguments"] = True
        return {k: v for k, v in request_params.items() if v is not None}

    def _build_context_breakdown(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        result: Response,
        kwargs: Dict[str, Any],
    ) -> Optional[ContextBreakdown]:
        """估算本次调用的上下文分段占用（监控口径，失败返回 None 不阻断调用链）。"""
        sections = extract_request_sections(kwargs)
        if sections is None:
            return None
        api_prompt_tokens = result.usage.prompt_tokens if result.usage is not None else 0
        try:
            return estimate_breakdown(
                request_id=request_id,
                profile_name=profile_name,
                model_name=model_name,
                system=sections.system,
                messages=sections.messages,
                tools=sections.tools,
                system_parts=sections.system_parts,
                api_prompt_tokens=api_prompt_tokens,
            )
        except Exception as exc:  # noqa: BLE001 估算属旁路观测，任何异常只降级
            self.logger.warning(f"上下文分段估算失败（本次跳过）: {exc}")
            return None

    def _write_request_payload(
        self,
        *,
        request_id: str,
        profile_name: str,
        model_name: str,
        request_params: Dict[str, Any],
        result: Response,
        breakdown: Optional[ContextBreakdown] = None,
    ) -> None:
        """请求正文出库：单请求一条诊断记录进 ``logs/llm_requests/`` 小时流。

        与库统计行经 ``request_id`` 关联（读侧按库行 ``timestamp_ms`` 定位
        小时文件回查）。fire-and-forget：writer 只入队即返回；未注入或失败
        只告警，不阻断调用链。成功路径同时覆写每模型最新上下文水位快照。
        """
        if self._diagnostics_writer is None:
            return
        timestamp_ms = int(time.time() * 1000)
        payload: Dict[str, Any] = {
            "request_id": request_id,
            "timestamp_ms": timestamp_ms,
            "profile_name": profile_name,
            "model_name": model_name,
            "success": result.success,
            "request_params": request_params,
            "response_content": result.content,
            "reasoning_content": result.reasoning_content,
            "tool_calls": _tool_calls_to_protocol(result.tool_calls),
            "usage_raw_json": result.usage_raw_json,
            "breakdown": breakdown.model_dump() if breakdown is not None else None,
        }
        try:
            self._diagnostics_writer.append("llm_requests", payload)
            if breakdown is not None:
                self._update_context_breakdown_snapshot(breakdown)
        except Exception as exc:  # noqa: BLE001 出库旁路，失败不阻断调用链
            self.logger.warning(f"请求正文出库失败: {exc}")

    def _update_context_breakdown_snapshot(self, breakdown: ContextBreakdown) -> None:
        """覆写每模型最新上下文水位快照（``logs/llm_context/latest.json``）。"""
        if self._diagnostics_writer is None:
            return
        try:
            snapshot = self._diagnostics_writer.read_snapshot("llm_context", "latest.json")
            models = snapshot.get("models") if isinstance(snapshot, dict) else None
            merged = dict(models) if isinstance(models, dict) else {}
            entry = breakdown.model_dump()
            # timestamp_ms 供快照消费方（水位悬停接口）组响应行；breakdown 本体无时刻字段
            entry["timestamp_ms"] = int(time.time() * 1000)
            merged[breakdown.model_name or "unknown"] = entry
            self._diagnostics_writer.write_snapshot("llm_context", "latest.json", {"models": merged})
        except Exception as exc:  # noqa: BLE001 快照旁路，失败不阻断调用链
            self.logger.warning(f"上下文水位快照写入失败: {exc}")
