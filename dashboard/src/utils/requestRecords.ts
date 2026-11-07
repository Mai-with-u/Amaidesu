// 请求记录：把时间线条目折算成「一次 LLM 请求 = 一条记录」的结构。
//
// 为什么需要这层：时间线是按事件类型发的广播流（决策/工具/思考/发言各成一条），
// 而读者排查问题时要回答的是另一类问题——这一次请求她收到了什么、做了什么、
// 最后输出了什么。这两者是多对一关系：一条记录由若干条目拼成，条目本身不表达
// 谁在前谁在后。
//
// 这里只做结构，不做渲染：卡片视角与紧凑调试视角都从同一份 RequestRecord 渲染，
// 差异只在于每行显示多少内容，不各自解析事件（否则两套判据迟早不一致）。

import { decisionDetail, isSilentDecision, pickToolText, summarizeToolArgs } from './liveFeed';
import { str } from './guards';
import type { ShowEntry } from './liveFeed';

/** 消息在请求里的角色：与主流 agent 记录一致（user 输入 / assistant 产出 / tool 结果） */
export type RequestRole = 'user' | 'assistant' | 'tool';

/** 消息种类决定行首标签与缩进 */
export type RequestMessageKind = 'batch' | 'think' | 'call' | 'result' | 'final' | 'note';

export interface RequestMessage {
  id: string;
  role: RequestRole;
  kind: RequestMessageKind;
  tsMs: number;
  /** 行首标签（弹幕 / 思考 / 调用 / 结果 / 发言 / 说明） */
  label: string;
  /** 单行摘要；完整内容在 payload 里 */
  text: string;
  payload?: unknown;
  /** 工具名（仅 call / result 行） */
  toolName?: string;
  failed?: boolean;
}

export interface RequestRecord {
  id: string;
  /** 决策轮次 ID；无轮次的零散条目为空串 */
  roundId: string;
  /** 是否由无轮次条目拼成（这类记录只代表"这段时间她说的话"） */
  loose: boolean;
  /** 记录首条消息的时间 */
  tsMs: number;
  /** 规划耗时（毫秒）；无决策条目时为 null */
  durationMs: number | null;
  /** 触发原因（弹幕 / 主动 / 强制 / 调试） */
  trigger: string;
  outcome: 'reply' | 'silent' | 'failed' | 'none';
  outcomeLabel: string;
  toolCount: number;
  messages: RequestMessage[];
  /** LLM 请求历史指针（记录头的「完整请求」入口） */
  llmRequestId: string;
}

/** 行序：输入在前，产出在后，结果紧跟调用——不按时间戳排 */
const KIND_ORDER: Record<RequestMessageKind, number> = {
  batch: 0,
  think: 1,
  call: 2,
  result: 3,
  note: 4,
  final: 5,
};

const KIND_LABEL: Record<RequestMessageKind, string> = {
  batch: '弹幕',
  think: '思考',
  call: '调用',
  result: '结果',
  note: '说明',
  final: '发言',
};

/** 触发原因转成人话：载荷里的是机器标识（proactive / dashboard:debug_test …） */
export function triggerLabel(raw: string): string {
  if (!raw) return '';
  if (raw === 'proactive') return '主动';
  if (raw === 'forced') return '强制';
  if (raw === 'dashboard:debug_test') return '调试';
  if (raw.startsWith('proactive:')) return `主动·${raw.slice('proactive:'.length)}`;
  return raw;
}

function push(list: RequestMessage[], message: RequestMessage): void {
  list.push(message);
}

/** 决策/裁决条目贡献：本批输入（弹幕）+ 这一次 reply 调用 */
function acceptDecision(entry: ShowEntry, messages: RequestMessage[]): void {
  const detail = decisionDetail(entry);
  const batch = Array.isArray(detail.batch) ? detail.batch : [];
  batch.forEach((item, index) => {
    if (!item || typeof item !== 'object') return;
    const row = item as Record<string, unknown>;
    const name = str(row.user_name) || '匿名观众';
    const text = str(row.text);
    push(messages, {
      id: `${entry.id}-batch-${index}`,
      role: 'user',
      kind: 'batch',
      tsMs: entry.tsMs,
      label: KIND_LABEL.batch,
      text: `${name}：${text}`,
      payload: row,
    });
  });

  if (entry.kind === 'verdict') {
    // verdict = reply 工具被调用的那一刻，记录体里就是这次调用本身
    const topic = str(detail.topic_summary);
    const guidance = str(detail.reply_guidance);
    push(messages, {
      id: `${entry.id}-call`,
      role: 'assistant',
      kind: 'call',
      tsMs: entry.tsMs,
      label: KIND_LABEL.call,
      text: topic || guidance,
      toolName: 'reply',
      payload: detail,
    });
    return;
  }

  if (entry.failed) {
    push(messages, {
      id: `${entry.id}-note`,
      role: 'assistant',
      kind: 'note',
      tsMs: entry.tsMs,
      label: KIND_LABEL.note,
      text: entry.note || entry.text || '这一轮失败',
      failed: true,
    });
    return;
  }
  if (isSilentDecision(entry)) {
    const reason = str(detail.silent_reason) || entry.text || '没有开口';
    push(messages, {
      id: `${entry.id}-note`,
      role: 'assistant',
      kind: 'note',
      tsMs: entry.tsMs,
      label: KIND_LABEL.note,
      text: reason,
    });
  }
}

/** 工具条目的入参（载荷里 args 字段；缺失时回退整份载荷） */
function toolArgsOf(entry: ShowEntry): unknown {
  if (entry.detail && typeof entry.detail === 'object' && 'args' in entry.detail) {
    return (entry.detail as Record<string, unknown>).args;
  }
  return entry.detail;
}

/** 结果载荷的可读预览：优先字段文本，其次 JSON 片段（maicraft 系列只回 obj/ok，没有文本字段） */
function resultPreview(result: unknown): string {
  if (result === null || result === undefined) return '完成';
  if (Array.isArray(result)) return `${result.length} 项`;
  if (typeof result === 'object') {
    try {
      const json = JSON.stringify(result);
      return json && json !== '{}' ? (json.length > 80 ? `${json.slice(0, 80)}…` : json) : '完成';
    } catch {
      return '完成';
    }
  }
  return String(result);
}

/** 工具条目的结果（载荷里 result 字段） */
function toolResultOf(entry: ShowEntry): unknown {
  if (entry.detail && typeof entry.detail === 'object' && 'result' in entry.detail) {
    return (entry.detail as Record<string, unknown>).result;
  }
  return entry.detail;
}

/** 单个条目 → 一到多条消息（工具卡拆成"调用 + 结果"两行，与 agent 记录的形状一致） */
function messagesOf(entry: ShowEntry): RequestMessage[] {
  const messages: RequestMessage[] = [];
  switch (entry.kind) {
    case 'verdict':
    case 'decision':
      acceptDecision(entry, messages);
      break;
    case 'speech':
      push(messages, {
        id: entry.id,
        role: 'assistant',
        kind: 'final',
        tsMs: entry.tsMs,
        label: KIND_LABEL.final,
        text: entry.text,
        payload: entry.detail ?? undefined,
      });
      break;
    case 'tool': {
      push(messages, {
        id: `${entry.id}-call`,
        role: 'assistant',
        kind: 'call',
        tsMs: entry.tsMs,
        label: KIND_LABEL.call,
        text: entry.text || summarizeToolArgs(toolArgsOf(entry)),
        toolName: entry.actor,
        payload: entry.detail ?? undefined,
      });
      push(messages, {
        id: `${entry.id}-result`,
        role: 'tool',
        kind: 'result',
        tsMs: entry.tsMs,
        label: KIND_LABEL.result,
        text: pickToolText(toolResultOf(entry)) || resultPreview(toolResultOf(entry)),
        toolName: entry.actor,
        failed: entry.failed,
        payload: entry.detail ?? undefined,
      });
      break;
    }
    case 'thinking':
      push(messages, {
        id: entry.id,
        role: 'assistant',
        kind: 'think',
        tsMs: entry.tsMs,
        label: KIND_LABEL.think,
        text: entry.text,
      });
      break;
    default:
      // 阶段、系统、TTS、环节、观众消息等不属于某次请求的内容：由其它视图承担
      break;
  }
  return messages;
}

function outcomeOf(
  messages: RequestMessage[],
  decision: ShowEntry | null,
): RequestRecord['outcome'] {
  // 裁决事件本身就是「调用了 reply」的时刻，即使发言还没生成也算回应
  if (messages.some(m => m.kind === 'final' || (m.kind === 'call' && m.toolName === 'reply'))) {
    return 'reply';
  }
  if (decision?.failed) return 'failed';
  if (decision && isSilentDecision(decision)) return 'silent';
  return 'none';
}

const OUTCOME_LABEL: Record<RequestRecord['outcome'], string> = {
  reply: '回应',
  silent: '沉默',
  failed: '失败',
  none: '',
};

function buildRecord(key: string, entries: ShowEntry[], loose: boolean): RequestRecord | null {
  const messages: RequestMessage[] = [];
  let decision: ShowEntry | null = null;
  for (const entry of entries) {
    if (entry.kind === 'verdict' || entry.kind === 'decision') decision = entry;
    messages.push(...messagesOf(entry));
  }
  if (messages.length === 0) return null;

  messages.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.tsMs - b.tsMs);
  const first = entries[0];
  const detail = decision ? decisionDetail(decision) : {};
  const outcome = outcomeOf(messages, decision);
  return {
    id: key,
    roundId: loose ? '' : key,
    loose,
    tsMs: Math.min(...entries.map(entry => entry.tsMs)),
    durationMs: decision ? Number(detail.planner_duration_ms) || null : null,
    trigger: decision ? triggerLabel(str(detail.trigger_reason)) : '',
    outcome,
    outcomeLabel: OUTCOME_LABEL[outcome],
    toolCount: messages.filter(message => message.kind === 'call').length,
    messages,
    llmRequestId: decision?.llmRequestId || first.llmRequestId || '',
  };
}

/**
 * 条目流 → 请求记录。
 *
 * 有 round_id 的条目归到同一条记录；没有 round_id 的相邻条目合成一条「零散」记录
 * （代表"这段时间里她说的话，没有经过一次完整决策"）。
 */
export function buildRequestRecords(entries: ShowEntry[]): RequestRecord[] {
  const records: RequestRecord[] = [];
  const buckets = new Map<string, ShowEntry[]>();
  const order: string[] = [];
  const looseGroups: ShowEntry[][] = [];

  for (const entry of entries) {
    if (!entry.roundId) {
      const last = looseGroups[looseGroups.length - 1];
      const lastTs = last ? last[last.length - 1].tsMs : 0;
      // 与上一条零散条目间隔超过 5 分钟就另起一条，避免整场拼成一条
      if (last && entry.tsMs - lastTs < 5 * 60 * 1000) last.push(entry);
      else looseGroups.push([entry]);
      continue;
    }
    const bucket = buckets.get(entry.roundId);
    if (bucket) bucket.push(entry);
    else {
      buckets.set(entry.roundId, [entry]);
      order.push(entry.roundId);
    }
  }

  for (const roundId of order) {
    const group = buckets.get(roundId) ?? [];
    const record = buildRecord(roundId, group, false);
    if (record) records.push(record);
  }
  looseGroups.forEach((group, index) => {
    const record = buildRecord(`loose-${index}`, group, true);
    if (record) records.push(record);
  });

  return records.sort((a, b) => a.tsMs - b.tsMs);
}

/** 记录里某类消息的条数（记录头汇总用） */
export function countKind(record: RequestRecord, kind: RequestMessageKind): number {
  return record.messages.filter(message => message.kind === kind).length;
}
