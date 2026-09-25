// 直播时间线共享逻辑
//
// 直播控制台（views/LiveObserver.vue）与未来的首页都需要把 WS 事件流
// 折叠成"一行可渲染的视图条目"（ShowEntry），并按相同规则把决策/裁决
// 按轮次回填成单卡。把这些纯逻辑从控制台抽出放到此处：
//   - 控制台不再承载业务规则，可专心做注入面板、滚动跟随、回看模式
//   - 首页可直接复用 FeedTimeline 组件，传入 buildLiveEntries 的输出
//
// 注意：所有时间戳以 Unix 毫秒为内部单位（与后端 timestamp_ms 一致）；
// 调用方如需展示相对时间请使用 relativeTime。

import { summarizeEvent } from './eventSummary';
import type { WebSocketMessage } from '@/types';
import { formatDurationShort } from '@/utils/format';

// 常量

export const MAX_ENTRIES = 400;
/** 正文回退字段候选：入参缺失的旧事件从结果载荷抠可读文本（按优先级） */
export const TOOL_TEXT_KEYS = [
  'speech_text',
  'text',
  'speech',
  'content',
  'message',
  'summary',
] as const;

export const STAGE_LABEL: Record<string, string> = {
  planning: '决策中',
  replying: '生成中',
  idle: '空闲',
};

// 类型

export type EntryKind =
  | 'danmaku'
  | 'gift'
  | 'super_chat'
  | 'enter'
  | 'speech'
  | 'tool'
  | 'rundown'
  | 'milestone'
  | 'verdict'
  | 'decision'
  | 'stage'
  | 'boundary'
  | 'game'
  /** 会话模式的过程折叠条（合成展示条目，不对应任何事件） */
  | 'process_group'
  /** 思考行：ReAct 各步/生成段思考流（视图层合成条目，不对应事件；流式期间文本原地增长） */
  | 'thinking';

/** 事件缓冲条目：events store 在 WebSocketMessage 上补了去重 id */
export type FeedEvent = WebSocketMessage & { id: string };

/** 时间线条目（view-ready，模板不再碰原始 payload） */
export interface ShowEntry {
  id: string;
  kind: EntryKind;
  /** Unix 毫秒（与后端 timestamp_ms 同单位） */
  tsMs: number;
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
  /** 来源标签文本（工具调用的归属 Agent / 游戏 Agent 上报）。空串表示无来源 */
  source: string;
  /** 工具卡参数药丸。空数组表示无入参或入参非对象，正文回退 text */
  argPills: ToolArgPill[];
}

/** 单步思考段（与工具卡时间交织的实时思考流片段） */
export interface ThinkingStep {
  step: number;
  text: string;
  /** 段首增量到达时刻（Unix 毫秒，取 WS 信封 timestamp_ms；思考行时间线定位用） */
  tsMs: number;
}

/** 工具卡参数药丸（view-ready：键 + 截断后的值文本） */
export interface ToolArgPill {
  key: string;
  value: string;
}

// 通用取值助手

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

/** 入参值文本：字符串超长省略，对象/数组压 JSON 片段，其余原样 */
function argValueText(value: unknown): string {
  if (typeof value === 'string') {
    return value.length > 48 ? `${value.slice(0, 48)}…` : value;
  }
  if (value === null || typeof value === 'object') {
    const json = JSON.stringify(value) ?? 'null';
    return json.length > 80 ? `${json.slice(0, 80)}…` : json;
  }
  return String(value);
}

/** 入参单值渲染（文本摘要用）：字符串带引号 */
function renderArgValue(value: unknown): string {
  const text = argValueText(value);
  return typeof value === 'string' ? `"${text}"` : text;
}

/** 入参紧凑摘要（key="value" 逗号连接）——无药丸时的正文回退，完整 JSON 留给折叠面板 */
export function summarizeToolArgs(args: unknown): string {
  if (!isRecord(args)) return '';
  const joined = Object.entries(args)
    .map(([key, value]) => `${key}=${renderArgValue(value)}`)
    .join(', ');
  return joined.length > 200 ? `${joined.slice(0, 200)}…` : joined;
}

/** 参数药丸（上限 6 枚，超出合并为计数药丸）——入参轮廓一眼可扫，全文留给折叠面板 */
export function toolArgPills(args: unknown): ToolArgPill[] {
  if (!isRecord(args)) return [];
  const entries = Object.entries(args);
  const pills = entries.slice(0, 6).map(([key, value]) => ({ key, value: argValueText(value) }));
  if (entries.length > 6) {
    pills.push({ key: `+${entries.length - 6}`, value: '其余参数见折叠面板' });
  }
  return pills;
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

/** 时间线条目归到三类 Agent 组之一：'streamer' 主播管线 / 'game' 游戏 Agent / 'room' 房间事件。
 *  工具条目按 source 归类：主播决策→streamer，游戏 Agent→game，其他有值 source 按前缀 minecraft 判 game 否则 streamer；
 *  speech/decision/verdict/stage→streamer；thinking 按来源归类（minecraft 段→game，主播段→streamer）；其余 kind→room */
export type AgentGroup = 'streamer' | 'game' | 'room';

export function agentGroupOf(entry: ShowEntry): AgentGroup {
  if (entry.kind === 'game') return 'game';
  if (entry.kind === 'thinking') {
    return entry.source === '游戏 Agent' ? 'game' : 'streamer';
  }
  if (
    entry.kind === 'speech' ||
    entry.kind === 'decision' ||
    entry.kind === 'verdict' ||
    entry.kind === 'stage'
  ) {
    return 'streamer';
  }
  if (entry.kind === 'tool') {
    if (entry.source === '主播决策') return 'streamer';
    if (entry.source === '游戏 Agent') return 'game';
    if (entry.source && entry.source.startsWith('minecraft')) return 'game';
    return 'streamer';
  }
  return 'room';
}

// 事件 → 时间线条目

export function makeEntry(base: {
  id: string;
  kind: EntryKind;
  tsMs: number;
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
  source?: string;
  argPills?: ToolArgPill[];
}): ShowEntry {
  const actor = base.actor ?? '';
  return {
    id: base.id,
    kind: base.kind,
    tsMs: base.tsMs,
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
    source: base.source ?? '',
    argPills: base.argPills ?? [],
  };
}

/** 观众行为流：room.message（RoomMessagePayload 扁平载荷，message_type 判别） */
function fromRoomMessage(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const tsMs = event.timestamp_ms;
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
      tsMs,
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
      tsMs,
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
      tsMs,
      actor,
      text: `${actor} 进入直播间`,
    });
  }

  return makeEntry({
    id: event.id,
    kind: 'danmaku',
    tsMs,
    actor,
    text: fallback(),
    messageId,
  });
}

/** 工具调用来源归一（caller_source → 显示文本）。planner-react / minecraft-* 折叠到人类可读标签，其他原样保留 */
export function callerSourceLabel(source: string): string {
  if (source === 'planner-react') return '主播决策';
  if (source === 'minecraft-react' || source === 'minecraft-handoff') return '游戏 Agent';
  return source;
}

/** 主播动作：tool.result.*（ToolResultPayload）；按 caller_source 区分归属 Agent
 * detail 字段承载原始入参/结果/错误文本——给前端"参数/结果"折叠面板做数据源；
 * arguments 字段由后端并行新增（ToolResultPayload.arguments），事件里缺失时取 null。
 */
function fromToolResult(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const toolName = str(data.tool_name) || event.type.slice('tool.result.'.length) || 'tool';
  const status = str(data.status);
  const failed = status === 'error';
  const spoken = pickToolText(data.result);
  // 正文承载入参（对齐主流 agent：工具卡主体是"调了什么"）；药丸优先，入参缺失时回退结果文本
  const argsSummary = summarizeToolArgs(data.arguments);
  const argPills = toolArgPills(data.arguments);
  const source = callerSourceLabel(str(data.caller_source));
  // 状态徽标与来源徽标拆双槽：有 status 时恒为成功/失败，无 status 留空避免抢视觉
  const badge = status ? (failed ? '失败' : '成功') : '';
  // detail 用对象承载三个原始字段：args（入参）/ result（结果）/ error_message（错误文本）
  // 模板里按需渲染，折叠面板仅在 args/result/error_message 任一非空时才显示
  const errorText = str(data.error_message);
  const detail: Record<string, unknown> = {
    args: data.arguments ?? null,
    result: data.result ?? null,
    error_message: errorText,
  };
  return makeEntry({
    id: event.id,
    kind: 'tool',
    tsMs: event.timestamp_ms,
    actor: toolName,
    // 正文只在有实质内容时出现；成败已由徽标承载，不重复成行
    text: argsSummary || spoken,
    note: failed ? errorText : '',
    badge,
    failed,
    speak: toolName === 'speak',
    roundId: str(data.round_id),
    source,
    detail,
    argPills,
  });
}

/** 主播发言：streamer.speech（StreamerSpeechPayload）。
 * llm_request_id 指向表达生成的 Replyer 请求（与决策卡上的 Planner 请求互补），
 * 回应卡据此懒取 Token/缓存/模型统计 */
function fromSpeech(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const emotion = str(data.emotion);
  return makeEntry({
    id: event.id,
    kind: 'speech',
    tsMs: event.timestamp_ms,
    actor: '主播',
    text: str(data.text) || summarizeEvent(event.type, data),
    note: emotion,
    speak: true,
    replyTo: str(data.reply_to_message_id),
    roundId: str(data.round_id),
    llmRequestId: str(data.llm_request_id),
  });
}

/** 决策记录：planner.decision（PlannerDecisionPayload）。
 * 决策卡只承载裁决语义（决定回应什么话题/为何沉默）；发言文本由 streamer.speech 发言卡承载 */
export function fromDecision(id: string, tsMs: number, data: Record<string, unknown>): ShowEntry {
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
    tsMs,
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
function fromVerdict(id: string, tsMs: number, data: Record<string, unknown>): ShowEntry {
  return makeEntry({
    id,
    kind: 'verdict',
    tsMs,
    actor: '决策',
    text: str(data.topic_summary) || '决定发言',
    badge: '回应',
    roundId: str(data.round_id),
    replyTo: str(data.reply_to_message_id),
    detail: data,
  });
}

/** 阶段状态：streamer.stage（StreamerStagePayload） */
export function fromStage(id: string, tsMs: number, data: Record<string, unknown>): ShowEntry {
  const stage = str(data.stage);
  const running = str(data.agent_state) === 'running';
  const label = STAGE_LABEL[stage] ?? (stage || '阶段变化');
  return makeEntry({
    id,
    kind: 'stage',
    tsMs,
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
    tsMs: event.timestamp_ms,
    text: started ? '场次开启' : '场次结束',
    badge: started ? str(data.source) : bool(data.empty_discarded) ? '空场次已丢弃' : '',
    note: title || reason,
  });
}

/** 环节切换：rundown.changed（RundownChangedPayload） */
function fromRundown(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const index = typeof data.index === 'number' ? data.index : null;
  const total = typeof data.total === 'number' ? data.total : null;
  const finished = index != null && total != null && index >= total;
  const by = typeof data.by === 'string' ? data.by : 'agent';
  return makeEntry({
    id: event.id,
    kind: 'rundown',
    tsMs: event.timestamp_ms,
    text: str(data.segment_title) || (finished ? '流程单完成' : '环节切换'),
    note: finished ? '' : index != null && total != null ? `环节 ${index}/${total}` : '',
    badge: by === 'human' ? '手动' : by === 'system' ? '系统' : 'Agent',
  });
}

/** 里程碑：game.milestone（GamePayload） */
function fromMilestone(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const meta = [str(data.game), str(data.scene)].filter(Boolean).join(' · ');
  return makeEntry({
    id: event.id,
    kind: 'milestone',
    tsMs: event.timestamp_ms,
    text: str(data.message) || summarizeEvent(event.type, data),
    note: meta,
  });
}

/** game.* 事件 → ShowEntry（GamePayload，event_type 判别）。
 *  非 game.* 事件返回 null；game.milestone 不走此函数（旧路径在 LiveObserver 本地维护） */
export function toGameEntry(event: FeedEvent): ShowEntry | null {
  if (!event.type.startsWith('game.')) return null;
  const data = isRecord(event.data) ? event.data : {};
  const eventType = str(data.event_type) || event.type.slice('game.'.length);
  const reportKind = str(data.report_kind);
  // 按 event_type 决定徽标语义；report 在 escalation 时升级为"升级提醒"
  let badge = '';
  if (eventType === 'report') badge = reportKind === 'escalation' ? '升级提醒' : '汇报';
  else if (eventType === 'attention_required') badge = '需要关注';
  else if (eventType === 'error') badge = '游戏错误';
  else if (eventType === 'milestone') badge = '里程碑';
  // 次要文案：scene 优先；report 时再叠加 report_kind 上下文
  const noteParts: string[] = [];
  const scene = str(data.scene);
  if (scene) noteParts.push(scene);
  if (eventType === 'report' && reportKind) {
    noteParts.push(reportKind === 'escalation' ? '需要主播决策' : '交付总结');
  }
  return makeEntry({
    id: event.id,
    kind: 'game',
    tsMs: event.timestamp_ms,
    actor: str(data.game) || eventType,
    text: str(data.message) || summarizeEvent(event.type, data),
    note: noteParts.join(' · '),
    badge,
    failed: eventType === 'error',
    source: '游戏 Agent',
  });
}

/** 非控制台事件（system.* 等）返回 null，不进时间线 */
export function toEntry(event: FeedEvent): ShowEntry | null {
  const data = isRecord(event.data) ? event.data : {};
  // WS 广播把 4 种 room.message.* 统一为 "room.message"，种类由 payload.message_type 判别
  if (event.type === 'room.message') return fromRoomMessage(event, data);
  if (event.type === 'streamer.speech') return fromSpeech(event, data);
  if (event.type === 'planner.verdict') return fromVerdict(event.id, event.timestamp_ms, data);
  if (event.type === 'planner.decision') return fromDecision(event.id, event.timestamp_ms, data);
  if (event.type === 'streamer.stage') return fromStage(event.id, event.timestamp_ms, data);
  if (event.type === 'live.started' || event.type === 'live.ended')
    return fromLiveBoundary(event, data);
  if (event.type.startsWith('tool.result.')) return fromToolResult(event, data);
  if (event.type === 'rundown.changed') return fromRundown(event, data);
  // game.milestone 旧路径由 LiveObserver 本地处理；其他 game.*（report/attention_required/error）统一走 toGameEntry
  if (event.type === 'game.milestone') return fromMilestone(event, data);
  if (
    event.type === 'game.report' ||
    event.type === 'game.attention_required' ||
    event.type === 'game.error'
  )
    return toGameEntry(event);
  return null;
}

// 决策卡取值助手（detail 字段安全读取）

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

// 纯逻辑：实时事件 → 时间线条目（含 verdict/decision 按轮回填）

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

// 思考行：视图层从思考流旁路状态合成（不进事件 store，不回看——ADR-008 边界不变）

/** 思考行输入段：视图层从思考流旁路状态提取（时间戳取段首 WS 信封时刻） */
export interface ThinkingSegmentInput {
  /** 决策轮次 ID */
  roundId: string;
  /** 段归属：planner（按 ReAct 步分段）/ replyer（表达生成，恒一段）/ minecraft */
  phase: string;
  /** 段号：planner 为步号；replyer 恒 1 */
  step: number;
  /** 段首增量到达时刻（Unix 毫秒） */
  tsMs: number;
  /** 已累积的思考文本 */
  text: string;
}

/** 思考段 → 时间线合成条目。id 由轮次/段归属/段号派生且稳定——流式增量到达时
 * 同 id 原地刷新文本，不产生新行 */
export function buildThinkingRow(seg: ThinkingSegmentInput): ShowEntry {
  const label =
    seg.phase === 'replyer'
      ? '生成思考'
      : seg.phase === 'minecraft'
        ? '游戏 Agent · 思考'
        : `思考 · 步骤 ${seg.step}`;
  return makeEntry({
    id: `think:${seg.roundId}:${seg.phase}:${seg.step}`,
    kind: 'thinking',
    tsMs: seg.tsMs,
    actor: label,
    text: seg.text,
    roundId: seg.roundId,
    // minecraft 段标注来源供 agentGroupOf 归入游戏 Agent 组（时间线过滤的依据）
    source: seg.phase === 'minecraft' ? '游戏 Agent' : '',
  });
}

/** 事件条目与思考行按时间归并（事件流本身升序，思考行副本就地排序）。
 *  同毫秒思考行在前——思考先于同刻落地的事件（思考结束才有工具结果/裁决）。 */
export function mergeEntriesByTime(entries: ShowEntry[], thinkingRows: ShowEntry[]): ShowEntry[] {
  const rows = [...thinkingRows].sort((a, b) => a.tsMs - b.tsMs);
  const merged: ShowEntry[] = [];
  let i = 0;
  let j = 0;
  while (i < entries.length && j < rows.length) {
    if (rows[j].tsMs <= entries[i].tsMs) {
      merged.push(rows[j]);
      j += 1;
    } else {
      merged.push(entries[i]);
      i += 1;
    }
  }
  merged.push(...entries.slice(i));
  merged.push(...rows.slice(j));
  return merged;
}

// 相对时间标签

/**
 * 时间线条目的相对时间戳（如"刚刚/12s 前/3m 前/2h 前/1d 前"）。
 * 调用方传入当前 Unix 毫秒时刻（FeedTimeline 内部 1s tick 维护）即可。
 */
export function relativeTime(nowMs: number, tsMs: number): string {
  return formatDurationShort(Math.max(0, Math.floor((nowMs - tsMs) / 1000)));
}

// 会话模式：对话优先的行序（过程行折叠成每轮一条过程条）

/** 会话模式里会被折叠进过程条的过程行类型 */
const CHAT_PROCESS_KINDS: ReadonlySet<EntryKind> = new Set<EntryKind>([
  'tool',
  'decision',
  'verdict',
  'stage',
  'game',
  'milestone',
  'rundown',
  'boundary',
  'thinking',
]);

/** 过程条合成条目 id 前缀（与事件条目 id 区分，避免 key 冲突） */
const CHAT_GROUP_PREFIX = 'chat-process:';

const CHAT_PROCESS_LABEL: Record<string, string> = {
  tool: '工具',
  decision: '决策',
  verdict: '决策',
  stage: '阶段',
  game: '游戏 Agent',
  milestone: '里程碑',
  rundown: '环节',
  boundary: '场次',
  thinking: '思考',
};

/** 该条目在会话模式是否属于过程行（折叠进过程条） */
export function isChatProcessKind(kind: EntryKind): boolean {
  return CHAT_PROCESS_KINDS.has(kind);
}

/** 过程条摘要：按类型计数（如「2 阶段 · 3 工具 · 1 决策」） */
export function chatProcessSummary(process: ShowEntry[]): string {
  const counts = new Map<string, number>();
  for (const entry of process) {
    const label = CHAT_PROCESS_LABEL[entry.kind];
    if (!label) continue;
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return Array.from(counts, ([label, n]) => `${n} ${label}`).join(' · ');
}

/**
 * 会话模式行序：观众气泡与主播发言保持原时序，每轮的过程行折叠成一条挂在
 * 发言之后的过程条；展开的组把过程行按原时序铺回，折叠时这些行不进入渲染。
 *
 * 分组按位置（以主播发言为界）而非 roundId——真实的 ``streamer.stage`` 事件
 * 不带 ``round_id``（fromStage 未映射），按 roundId 分组会把阶段行孤立。
 * 无发言的过程行（静默轮、尾部残留）自成一组，绝不丢行。
 */
export function buildChatRows(
  entries: ShowEntry[],
  expandedGroups: ReadonlySet<string>,
): ShowEntry[] {
  const rows: ShowEntry[] = [];
  let pending: ShowEntry[] = [];
  let seq = 0;

  /** 把缓冲里的观众消息按原序铺回，只给过程行留在缓冲里等结算 */
  const takeAudience = () => {
    rows.push(...pending.filter(entry => !CHAT_PROCESS_KINDS.has(entry.kind)));
    pending = pending.filter(entry => CHAT_PROCESS_KINDS.has(entry.kind));
  };

  /** 结算一个过程组；speech 为 null 表示这组没有发言（静默轮 / 尾部残留） */
  const flushGroup = (speech: ShowEntry | null) => {
    const process = pending;
    pending = [];
    if (speech) rows.push(speech);
    if (process.length === 0) return;
    seq += 1;
    const key = `${CHAT_GROUP_PREFIX}${speech ? speech.id : `silent-${seq}`}`;
    rows.push(
      makeEntry({
        id: key,
        kind: 'process_group',
        tsMs: process[process.length - 1].tsMs,
        text: chatProcessSummary(process),
        note: String(process.length),
      }),
    );
    if (expandedGroups.has(key)) rows.push(...process);
  };

  for (const entry of entries) {
    if (entry.kind === 'speech') {
      takeAudience();
      flushGroup(entry);
    } else {
      pending.push(entry);
    }
  }
  takeAudience();
  flushGroup(null);
  return rows;
}
