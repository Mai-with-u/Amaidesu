"""本地诊断记录落盘（diagnostics）

定位：**本地留存、可丢弃的过程记录**——LLM 请求正文、事件流、TTS 逐句合成
记录等调试/复盘数据的统一落盘通道。与上传型数据收集（业界 telemetry 语义）
无关，命名取"本地诊断数据"之义。

形态按读模式划分：
- 海量追加流（事件 / TTS / LLM 请求正文）：``logs/<kind>/<YYYY-MM-DD>/<HH>.jsonl``
  按小时追加，一天 ≤24 个文件；读单条由库行 ``timestamp_ms`` 推导天+小时文件定位
- 每模型最新快照（上下文水位）：``logs/llm_context/latest.json`` 原子覆写，不过期

生命周期：正文文件按保留期滚动清理（默认 14 天，启动时 + 每天首次写入触发），
统计事实永远在 SQLite（``llm_usage`` / ``llm_requests`` 统计列），删正文不伤账。

写入为 fire-and-forget：后台线程串行落盘，队列满丢弃并计数告警，主调用链
零阻塞。进程正常停机经 ``stop()`` 冲刷队列；异常退出丢尾部属预期（best-effort
旁路，与思考流同一定位）。
"""

from src.modules.diagnostics.tts_recorder import TtsDiagnosticsRecorder
from src.modules.diagnostics.writer import DiagnosticsWriter

__all__ = ["DiagnosticsWriter", "TtsDiagnosticsRecorder"]
