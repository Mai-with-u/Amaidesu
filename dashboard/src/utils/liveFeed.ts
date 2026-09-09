// 直播时间线共享逻辑
//
// 直播控制台（views/LiveObserver.vue）与未来的首页都需要把 WS 事件流
// 折叠成"一行可渲染的视图条目"（ShowEntry），并按相同规则把决策/裁决
// 按轮次回填成单卡。把这些纯逻辑从控制台抽出放到此处：
//   - 控制台不再承载业务规则，可专心做注入面板、滚动跟随、回看模式
//   - 首页可直接复用 FeedTimeline 组件，传入 buildLiveEntries 的输出
//
// 注意：所有时间戳以 Unix 秒为内部单位（后端事件 timestamp 为秒；
// toSeconds 把毫秒值兜底换算）；调用方如需展示相对时间请使用 relativeTime。

import { summarizeEvent } from './eventSummary';
import type { WebSocketMessage } from '@/types';

// ============================================================
// 常量
// ============================================================

export const MAX_ENTRIES = 400;
/** 结果载荷里可作"主播说了什么"的字段候选（按优先级） */
export const TOOL_TEXT_KEYS = [
  'speech_text',
  'text',
  'speech',
  'content',
  'message',
  'summary',
] as const;

export const AGENDA_ACTION_LABEL: Record<string, string> = {
  done: '已完成',
  schedule: '已改期',
  insert: '新增环节',
};

export const STAGE_LABEL: Record<string, string> = {
  planning: '决策中',
  replying: '生成中',
  idle: '空闲',
};

// ============================================================
// 类型
// ============================================================

export type EntryKind =
  | 'danmaku'
  | 'gift'
  | 'super_chat'
  | 'enter'
  | 'speech'
  | 'tool'
  | 'agenda'
  | 'milestone'
  | 'verdict'
  | 'decision'
  | 'stage'
  | 'boundary';

/** 事件缓冲条目：events store 在 WebSocketMessage 上补了去重 id */
export type FeedEvent = WebSocketMessage & { id: string };

/** 时间线条目（view-ready，模板不再碰原始 payload） */
export interface ShowEntry {
  id: string;
  kind: EntryKind;
  /** Unix 秒（后端事件 timestamp 为秒，毫秒亦兼容） */
  tsSec: number;
  /** 观众昵称 / 工具名 / 环节名 */
  actor: string;
  /** 主体文案 */
  text: string;
  /** 次要文案（错误信息 / 环节备注 / 游戏场景 / 阶段补充） */
  note: string;
  /** 类型徽标（礼物 / SC / 失败 / 回应 / 沉默 / 环节动作） */
  badge: string;
  /** 金额强调（¥50） */
  money: string;
  failed: boolean;
  speak: boolean;
  /** 头像首字 */
  initial: string;
  /** 决策轮次 ID（planner.decision；发言/工具结果经它成组） */
  roundId: string;
  /** 回复关联键（所回复弹幕的 message_id） */
  replyTo: string;
  /** 观众消息自身 ID（弹幕 / SC / 礼物；enter 为空）。决策/发言卡通过 replyTo 反查本字段定位被回复弹幕 */
  messageId: string;
  /** 决策卡附加字段（置信度/耗时/原始输出/请求历史指针等） */
  detail: Record<string, unknown> | null;
  /** LLM 请求历史指针（决策卡"完整请求"链接） */
  llmRequestId: string;
}

/** 单步思考段（与工具卡时间交织的实时思考流片段） */
export interface ThinkingStep {
  step: number;
  text: string;
}

// ============================================================
// 通用取值助手
// ============================================================

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function str(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

export function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function bool(value: unknown): boolean {
  return value === true;
}

/** 时间戳归一到 Unix 秒（后端为秒；毫秒值兜底换算） */
export function toSeconds(value: number): number {
  return value > 1e12 ? value / 1000 : value;
}

export function formatAmount(amount: number): string {
  return Number.isInteger(amount) ? String(amount) : amount.toFixed(2);
}

export function pickToolText(result: unknown): string {
  if (!isRecord(result)) return '';
  for (const key of TOOL_TEXT_KEYS) {
    const text = str(result[key]);
    if (text) return text;
  }
  return '';
}

/** RoomMessageUser → 展示名（与 summarizeEvent 的取名口径一致） */
export function userLabel(value: unknown): string {
  if (!isRecord(value)) return '匿名观众';
  const name = str(value.name);
  if (name) return name;
  const id = str(value.id);
  return id ? `#${id}` : '匿名观众';
}

function initialOf(actor: string): string {
  const chars = Array.from(actor.replace(/^#/, ''));
  return chars.length > 0 ? chars[0].toUpperCase() : '?';
}

// ============================================================
// 事件 → 时间线条目
// ============================================================

export function makeEntry(base: {
  id: string;
  kind: EntryKind;
  tsSec: number;
  actor?: string;
  text: string;
  note?: string;
  badge?: string;
  money?: string;
  failed?: boolean;
  speak?: boolean;
  roundId?: string;
  replyTo?: string;
  messageId?: string;
  detail?: Record<string, unknown> | null;
  llmRequestId?: string;
}): ShowEntry {
  const actor = base.actor ?? '';
  return {
    id: base.id,
    kind: base.kind,
    tsSec: base.tsSec,
    actor,
    text: base.text,
    note: base.note ?? '',
    badge: base.badge ?? '',
    money: base.money ?? '',
    failed: base.failed ?? false,
    speak: base.speak ?? false,
    initial: initialOf(actor),
    roundId: base.roundId ?? '',
    replyTo: base.replyTo ?? '',
    messageId: base.messageId ?? '',
    detail: base.detail ?? null,
    llmRequestId: base.llmRequestId ?? '',
  };
}

/** 观众行为流：room.message（RoomMessagePayload 扁平载荷，message_type 判别） */
function fromRoomMessage(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const tsSec = toSeconds(event.timestamp);
  const actor = userLabel(data.user);
  const content = str(data.content);
  const fallback = () => content || summarizeEvent(event.type, data);
  const messageType = str(data.message_type) || 'danmaku';
  const messageId = str(data.message_id);

  if (messageType === 'gift') {
    const gift = isRecord(data.gift) ? data.gift : null;
    const giftName = gift ? str(gift.name) : '';
    const count = (gift ? num(gift.count) : null) ?? 1;
    return makeEntry({
      id: event.id,
      kind: 'gift',
      tsSec,
      actor,
      text: giftName ? `送出 ${giftName} ×${count}` : fallback(),
      badge: '礼物',
      messageId,
    });
  }

  if (messageType === 'super_chat') {
    const sc = isRecord(data.sc) ? data.sc : null;
    const amount = sc ? num(sc.amount) : null;
    return makeEntry({
      id: event.id,
      kind: 'super_chat',
      tsSec,
      actor,
      text: fallback(),
      badge: 'SC',
      money: amount != null ? `¥${formatAmount(amount)}` : '',
      messageId,
    });
  }

  if (messageType === 'enter') {
    return makeEntry({
      id: event.id,
      kind: 'enter',
      tsSec,
      actor,
      text: `${actor} 进入直播间`,
    });
  }

  return makeEntry({
    id: event.id,
    kind: 'danmaku',
    tsSec,
    actor,
    text: fallback(),
    messageId,
  });
}

/** 主播动作：tool.result.*（ToolResultPayload）；按 caller_source 区分归属 Agent */
function fromToolResult(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const toolName = str(data.tool_name) || event.type.slice('tool.result.'.length) || 'tool';
  const status = str(data.status);
  const failed = status === 'error';
  const spoken = pickToolText(data.result);
  const statusText = status ? (failed ? '执行失败' : '执行完成') : '';
  const source = str(data.caller_source);
  const caller =
    source === 'planner-react' ? '主播决策' : source === 'minecraft-react' ? '游戏 Agent' : source;
  return makeEntry({
    id: event.id,
    kind: 'tool',
    tsSec: toSeconds(event.timestamp),
    actor: toolName,
    text: spoken || statusText || summarizeEvent(event.type, data),
    note: failed ? str(data.error_message) : '',
    badge: failed ? '失败' : caller,
    failed,
    speak: toolName === 'speak',
    roundId: str(data.round_id),
  });
}

/** 主播发言：streamer.speech（StreamerSpeechPayload） */
function fromSpeech(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const emotion = str(data.emotion);
  return makeEntry({
    id: event.id,
    kind: 'speech',
    tsSec: toSeconds(event.timestamp),
    actor: '主播',
    text: str(data.text) || summarizeEvent(event.type, data),
    note: emotion,
    speak: true,
    replyTo: str(data.reply_to_message_id),
    roundId: str(data.round_id),
  });
}

/** 决策记录：planner.decision（PlannerDecisionPayload）。
 * 决策卡只承载裁决语义（决定回应什么话题/为何沉默）；发言文本由 streamer.speech 发言卡承载 */
export function fromDecision(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  const error = str(data.error);
  const shouldReply = bool(data.should_reply);
  const silentReason = str(data.silent_reason);
  const badge = error ? '失败' : shouldReply ? '回应' : silentReason ? '静默·压制' : '沉默';
  const text = error
    ? error
    : shouldReply
      ? str(data.topic_summary) || '决定发言'
      : silentReason === 'low_confidence'
        ? 'LLM 想回应，但置信度过低——被裁决压制为静默'
        : str(data.topic_summary)
          ? `决定不回应（${str(data.topic_summary)}）`
          : '决定不回应';
  const note = shouldReply && !error ? '' : error ? '' : str(data.error_message);
  return makeEntry({
    id,
    kind: 'decision',
    tsSec,
    actor: '决策',
    text,
    note,
    badge,
    failed: Boolean(error),
    roundId: str(data.round_id),
    replyTo: str(data.reply_to_message_id),
    detail: data,
    llmRequestId: str(data.llm_request_id),
  });
}

/** 裁决卡：planner.verdict（reply 调用时刻的即时裁决；轮末 decision 按轮回填统计） */
function fromVerdict(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  return makeEntry({
    id,
    kind: 'verdict',
    tsSec,
    actor: '决策',
    text: str(data.topic_summary) || '决定发言',
    badge: '回应',
    roundId: str(data.round_id),
    replyTo: str(data.reply_to_message_id),
    detail: data,
  });
}

/** 阶段状态：streamer.stage（StreamerStagePayload） */
export function fromStage(id: string, tsSec: number, data: Record<string, unknown>): ShowEntry {
  const stage = str(data.stage);
  const running = str(data.agent_state) === 'running';
  const label = STAGE_LABEL[stage] ?? (stage || '阶段变化');
  return makeEntry({
    id,
    kind: 'stage',
    tsSec,
    text: `阶段：${label}`,
    note: str(data.detail),
    speak: running,
  });
}

/** 场次边界：live.started / live.ended */
function fromLiveBoundary(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const started = event.type === 'live.started';
  const title = str(data.title);
  const reason = str(data.reason);
  return makeEntry({
    id: event.id,
    kind: 'boundary',
    tsSec: toSeconds(event.timestamp),
    text: started ? '场次开启' : '场次结束',
    badge: started ? str(data.source) : bool(data.empty_discarded) ? '空场次已丢弃' : '',
    note: title || reason,
  });
}

/** 环节推进：agenda.update（AgendaPayload） */
function fromAgenda(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const item = isRecord(data.item) ? data.item : {};
  const action = str(data.action);
  return makeEntry({
    id: event.id,
    kind: 'agenda',
    tsSec: toSeconds(event.timestamp),
    text: str(item.label) || summarizeEvent(event.type, data) || '未命名环节',
    note: str(item.note),
    badge: AGENDA_ACTION_LABEL[action] ?? action,
  });
}

/** 里程碑：game.milestone（GamePayload） */
function fromMilestone(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const meta = [str(data.game), str(data.scene)].filter(Boolean).join(' · ');
  return makeEntry({
    id: event.id,
    kind: 'milestone',
    tsSec: toSeconds(event.timestamp),
    text: str(data.message) || summarizeEvent(event.type, data),
    note: meta,
  });
}

/** 非控制台事件（system.* 等）返回 null，不进时间线 */
export function toEntry(event: FeedEvent): ShowEntry | null {
  const data = isRecord(event.data) ? event.data : {};
  // WS 广播把 4 种 room.message.* 统一为 "room.message"，种类由 payload.message_type 判别
  if (event.type === 'room.message') return fromRoomMessage(event, data);
  if (event.type === 'streamer.speech') return fromSpeech(event, data);
  if (event.type === 'planner.verdict')
    return fromVerdict(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'planner.decision')
    return fromDecision(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'streamer.stage') return fromStage(event.id, toSeconds(event.timestamp), data);
  if (event.type === 'live.started' || event.type === 'live.ended')
    return fromLiveBoundary(event, data);
  if (event.type.startsWith('tool.result.')) return fromToolResult(event, data);
  if (event.type === 'agenda.update') return fromAgenda(event, data);
  if (event.type === 'game.milestone') return fromMilestone(event, data);
  return null;
}

// ============================================================
// 决策卡取值助手（detail 字段安全读取）
// ============================================================

export function decisionDetail(entry: ShowEntry): Record<string, unknown> {
  return isRecord(entry.detail) ? entry.detail : {};
}

export function isSilentDecision(entry: ShowEntry): boolean {
  const detail = decisionDetail(entry);
  return !bool(detail.should_reply) && !entry.failed;
}

export function confidenceLabel(entry: ShowEntry): string {
  const value = num(decisionDetail(entry).confidence);
  return value != null ? `置信 ${value.toFixed(2)}` : '';
}

export function guidanceOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).reply_guidance);
}

export function batchSizeOf(entry: ShowEntry): number {
  const batch = decisionDetail(entry).batch;
  return Array.isArray(batch) ? batch.length : 0;
}

export function plannerMsOf(entry: ShowEntry): number | null {
  return num(decisionDetail(entry).planner_duration_ms);
}

export function replyMsOf(entry: ShowEntry): number | null {
  return num(decisionDetail(entry).reply_duration_ms);
}

export function rawOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).planner_raw);
}

// ============================================================
// 纯逻辑：实时事件 → 时间线条目（含 verdict/decision 按轮回填）
// ============================================================

/**
 * 把 events store 的事件流折叠成时间线条目。
 *
 * - 跳过 hiddenIds 里被清空水位标记的事件；
 * - verdict（回复调用时刻）按 roundId 缓存，命中 decision（轮末统计回填）
 *   时把统计/失败信息合并进裁决卡，decision 自身不再成卡；
 * - 末尾按 MAX_ENTRIES 限长保尾，丢弃最旧条目。
 *
 * 暂停逻辑由调用方的 watch 控制（暂停时跳过本函数），保持本函数纯。
 */
export function buildLiveEntries(events: FeedEvent[], hiddenIds: Set<string>): ShowEntry[] {
  const next: ShowEntry[] = [];
  // 轮次 → 已渲染的裁决卡；decision 到达时把统计回填进裁决卡而非新增重复卡
  const verdictByRound = new Map<string, ShowEntry>();
  for (const event of events) {
    if (hiddenIds.has(event.id)) continue;
    const entry = toEntry(event);
    if (!entry) continue;
    if (entry.kind === 'verdict' && entry.roundId) {
      verdictByRound.set(entry.roundId, entry);
    } else if (entry.kind === 'decision' && entry.roundId) {
      const verdict = verdictByRound.get(entry.roundId);
      if (verdict) {
        // decision 的统计/失败信息回填裁决卡；decision 自身不再成卡
        verdict.detail = decisionDetail(entry) || verdict.detail;
        verdict.llmRequestId = entry.llmRequestId || verdict.llmRequestId;
        verdict.failed = entry.failed;
        if (entry.failed) {
          verdict.text = entry.text;
          verdict.badge = '失败';
          verdict.note = entry.note;
        }
        continue;
      }
    }
    next.push(entry);
  }
  return next.slice(-MAX_ENTRIES);
}

// ============================================================
// 相对时间标签
// ============================================================

/**
 * 时间线条目的相对时间戳（如"刚刚/12s 前/3m 前/2h 前/1d 前"）。
 * 调用方传入当前 Unix 秒（FeedTimeline 内部 1s tick 维护）即可。
 */
export function relativeTime(nowSec: number, tsSec: number): string {
  const diff = Math.max(0, Math.floor(nowSec - tsSec));
  if (diff < 5) return '刚刚';
  if (diff < 60) return `${diff}s 前`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
  return `${Math.floor(diff / 86400)}d 前`;
}
