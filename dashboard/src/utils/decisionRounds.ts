/**
 * 决策轮归组：把事件缓冲里的一轮决策折叠成"一次决定一张卡"的视图模型。
 *
 * ``planner.decision`` 是每轮的锚（轮次内的一切都以 round_id 关联）：
 * 触发原因、批消息、说或不说（含静默原因）、发言、置信度、错误、
 * Planner 原始输出与 LLM 请求指针都在这条事件里；挂同轮的 ``tool.result.*``
 * 补充本轮调用了哪些工具。没有决策锚的工具结果（如 minecraft 的工具调用）
 * 不成卡——本模块只回答"主播这轮为什么这么做"。
 *
 * 纯函数、无副作用：输入事件缓冲（升序），输出卡片按时间降序（新轮在前）。
 * 字段缺失优雅退化为 undefined，渲染层自行省略。
 */

import type { WebSocketMessage } from '@/types';
import { bool, isRecord, num, pickToolText, str, summarizeToolArgs } from './liveFeed';

/** 事件输入形状：与 events store 条目一致（WS 消息 + 去重 id） */
export type DecisionEvent = WebSocketMessage & { id: string };

/** 本批消息条目（决策轮看到的弹幕摘要） */
export interface DecisionRoundBatchItem {
  sender: string;
  content: string;
  /** 消息种类：决策批目前只收弹幕 */
  type: string;
}

/** 本轮工具调用条目 */
export interface DecisionRoundToolItem {
  name: string;
  status: string;
  failed: boolean;
  /** 入参摘要（key=value 紧凑形态） */
  argsSummary: string;
  /** 结果摘要（可读文本优先，非文本载荷回退 JSON 片段） */
  resultSummary: string;
  error: string;
}

/** 三段耗时（毫秒；未发生的阶段缺省） */
export interface DecisionRoundDurations {
  plannerMs?: number;
  replyMs?: number;
  totalMs?: number;
}

/** 一轮决策的卡片视图模型（主播视图"一次决定一张卡"的数据面） */
export interface DecisionRoundCard {
  roundId: string;
  timestampMs: number;
  /** 触发原因（机器可读码，原值展示） */
  triggerReason?: string;
  /** 主动发言轮（无弹幕批次） */
  proactive?: boolean;
  batch: DecisionRoundBatchItem[];
  shouldReply: boolean;
  /** 静默原因（shouldReply=false 时可读；low_confidence 等七种码） */
  silentReason?: string;
  confidence?: number;
  speech?: string;
  emotion?: string;
  error?: string;
  tools: DecisionRoundToolItem[];
  plannerRaw?: string;
  /** Planner LLM 请求历史指针（"看它发给 AI 的原文"跳转用） */
  llmRequestId?: string;
  durations?: DecisionRoundDurations;
}

/** 决策批目前只收弹幕（PlannerBatchItem），种类恒为 danmaku */
const BATCH_ITEM_TYPE = 'danmaku';

function readBatch(value: unknown): DecisionRoundBatchItem[] {
  if (!Array.isArray(value)) return [];
  return value.map(item =>
    isRecord(item)
      ? {
          sender: str(item.user_name) || str(item.user_id),
          content: str(item.text),
          type: BATCH_ITEM_TYPE,
        }
      : { sender: '', content: '', type: BATCH_ITEM_TYPE },
  );
}

function readDurations(data: Record<string, unknown>): DecisionRoundDurations | undefined {
  const plannerMs = num(data.planner_duration_ms) ?? 0;
  const replyMs = num(data.reply_duration_ms) ?? 0;
  const totalMs = num(data.total_duration_ms) ?? 0;
  if (plannerMs <= 0 && replyMs <= 0 && totalMs <= 0) return undefined;
  return {
    plannerMs: plannerMs > 0 ? plannerMs : undefined,
    replyMs: replyMs > 0 ? replyMs : undefined,
    totalMs: totalMs > 0 ? totalMs : undefined,
  };
}

/** 结果摘要：可读文本优先（pickToolText 的键序），否则压 JSON 片段；空载荷回空串 */
function readResultSummary(result: unknown): string {
  const text = pickToolText(result);
  if (text) return text;
  if (isRecord(result) && Object.keys(result).length > 0) {
    const json = JSON.stringify(result) ?? '';
    return json.length > 80 ? `${json.slice(0, 80)}…` : json;
  }
  return '';
}

function readTool(event: DecisionEvent, data: Record<string, unknown>): DecisionRoundToolItem {
  const status = str(data.status);
  return {
    name: str(data.tool_name) || event.type.slice('tool.result.'.length) || 'tool',
    status,
    failed: status === 'error',
    argsSummary: summarizeToolArgs(data.arguments),
    resultSummary: readResultSummary(data.result),
    error: str(data.error_message),
  };
}

/**
 * 把事件缓冲折叠成决策轮卡片。
 *
 * 两趟扫描：先收集全部 planner.decision 锚，再把 tool.result.* 按 round_id
 * 挂到锚上——真实时序里工具结果先于轮末的 decision 事件到达，单趟会漏挂。
 * 没有锚的工具结果直接忽略；缺 round_id 的锚用事件 id 兜底建卡（不与轮次串卡）。
 */
export function groupDecisionRounds(events: ReadonlyArray<DecisionEvent>): DecisionRoundCard[] {
  const cards = new Map<string, DecisionRoundCard>();
  for (const event of events) {
    if (event.type !== 'planner.decision') continue;
    const data = isRecord(event.data) ? event.data : {};
    const roundId = str(data.round_id);
    const card: DecisionRoundCard = {
      roundId,
      timestampMs: event.timestamp_ms,
      triggerReason: str(data.trigger_reason) || undefined,
      proactive: bool(data.proactive) || undefined,
      batch: readBatch(data.batch),
      shouldReply: bool(data.should_reply),
      silentReason: str(data.silent_reason) || undefined,
      confidence: num(data.confidence) ?? undefined,
      speech: str(data.speech) || undefined,
      emotion: str(data.emotion) || undefined,
      error: str(data.error) || undefined,
      tools: [],
      plannerRaw: str(data.planner_raw) || undefined,
      llmRequestId: str(data.llm_request_id) || undefined,
      durations: readDurations(data),
    };
    cards.set(roundId || `event:${event.id}`, card);
  }
  for (const event of events) {
    if (!event.type.startsWith('tool.result.')) continue;
    const data = isRecord(event.data) ? event.data : {};
    const roundId = str(data.round_id);
    if (!roundId) continue;
    const card = cards.get(roundId);
    if (!card) continue;
    card.tools.push(readTool(event, data));
  }
  return Array.from(cards.values()).sort((a, b) => b.timestampMs - a.timestampMs);
}
