/**
 * Trace 全链路追踪类型定义
 *
 * 对应后端 /api/v1/traces 接口返回的数据结构，用于追踪单条消息从
 * Input → Decision → Output 的全链路处理过程。
 */

/** 单条输入消息（Input 阶段采集结果） */
export interface TraceMessage {
  text: string;
  source: string;
  data_type: string;
  /** 毫秒时间戳（Unix epoch ms） */
  timestamp_ms: number;
  user_id?: string | null;
  user_nickname?: string | null;
}

/** Intent 情绪（与后端 IntentEmotion 保持一致） */
export interface TraceEmotion {
  name: string;
  /** 0 ~ 1 */
  intensity: number;
}

/** Intent 动作（全限定名 `<handler>.<local_action>`） */
export interface TraceAction {
  name: string;
  parameters: Record<string, unknown>;
}

/** 阶段耗时条目（Decision / Output 通用） */
export interface TraceStageTiming {
  timestamp: number;
  elapsed_ms: number;
}

/** 决策阶段产物（Decision 阶段产物） */
export interface TraceDecision extends TraceStageTiming {
  decider: string;
  speech: string;
  emotion?: TraceEmotion | null;
  action?: TraceAction | null;
}

/** 输出处理条目（Output 阶段由每个 Handler 产出） */
export interface TraceOutput extends TraceStageTiming {
  handler: string;
  speech: string;
  action?: TraceAction | null;
}

/** 单条消息的完整链路追踪数据 */
export interface Trace {
  message_id: string;
  message: TraceMessage;
  decision: TraceDecision | null;
  outputs: TraceOutput[];
  /** 从消息进入到全部 Output 完成的总耗时（毫秒） */
  total_elapsed_ms: number;
}

/** 列表接口响应 */
export interface TraceListResponse {
  traces: Trace[];
  total: number;
}

/** 详情接口响应（trace 可能为 null 表示未找到） */
export interface TraceDetailResponse {
  trace: Trace | null;
  error?: string;
}
