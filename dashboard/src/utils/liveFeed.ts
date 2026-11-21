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
import { bool, isRecord, num, str } from './guards';
import type { LeadHandoverBrief, WebSocketMessage } from '@/types';
import {
  EVENT,
  EVENT_FAMILY,
  isCoreFamilyEvent,
  isGameBodyEvent,
  isGameFamilyEvent,
  isToolHealthEvent,
  isToolResultEvent,
} from '@/types/events';
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
  | 'guard'
  | 'enter'
  | 'speech'
  | 'tool'
  | 'rundown'
  | 'milestone'
  | 'verdict'
  | 'decision'
  | 'stage'
  | 'tts'
  | 'boundary'
  | 'game'
  /** 游戏内聊天（game.chat.received）：游戏世界里别人说的话，与 B 站弹幕视觉区分 */
  | 'game_chat'
  /** 游戏身体事件（game.body.*）：AI 玩家遭遇的轻量小行 */
  | 'game_body'
  /** 动别人东西/拿别人家吃的的叙事小卡：挂在 game.body.unknown（或同义专名
   *  事件 game.body.others_things_touched / game.body.others_food_taken）上的
   *  富叙事，双行专有展示，不混进占位"未知"单行 */
  | 'others_things'
  /** 工具健康跃迁（tool.health.*）：熔断/恢复系统行（独立于 kind='system'，
   *  因简洁档口径不同——guard 进简洁档、tool.health 不进） */
  | 'tool_health'
  /** 递话受理（agent.prompted）：运营/跨 Agent 留言送达目标 */
  | 'prompt'
  /** Agent 每步响应（agent.replied）：命令驱动型 Agent 中间步骤的行动说明，决定卡形态 */
  | 'agent_reply'
  /** 收活卡（视图层合成条目，不对应事件）：游戏 Agent 按 task.changed 受理回执认领
   *  某次派活的轻量小卡，点击跳回主播列对应的 framework_delegate 卡 */
  | 'delegate_receipt'
  /** 思考行：ReAct 各步/生成段思考流（视图层合成条目，不对应事件；流式期间文本原地增长） */
  | 'thinking'
  /** 主控权切换（agent.lead.changed）：主播主控与游戏主控之间的交接分隔行 */
  | 'lead_change'
  /** 非强杀建议转达回执（agent.lead.suggestion）：运营提醒转给自主主控的游戏
   *  Agent 的送达结果行（后台事实，不进观众视角） */
  | 'lead_suggestion'
  /** 主控方自主解说事实（agent.lead.commentary）：游戏主控向观众解说的受理/播出回执
   *  （后台事实，不进观众视角——观众视角已有润色后的台词行） */
  | 'lead_commentary'
  /** 系统事件行（core.error / core.startup / core.shutdown）：运营必看，两个视图档位都显示 */
  | 'system';

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
  /** 决策卡附加字段（耗时/原始输出/请求历史指针等） */
  detail: Record<string, unknown> | null;
  /** LLM 请求历史指针（决策卡"完整请求"链接） */
  llmRequestId: string;
  /** 来源标签文本（工具调用的归属 Agent / 游戏 Agent 上报）。空串表示无来源 */
  source: string;
  /** 调试来源标记：弹幕来自控制台注入（simulated）、决策由「立即决策测试」手动驱动 */
  simulated: boolean;
  /** 工具卡参数药丸。空数组表示无入参或入参非对象，正文回退 text */
  argPills: ToolArgPill[];
  /** TTS 全链路关联键（streamer.speech.utterance_id）；tts.utterance.* 经它回填播报态 */
  utteranceId: string;
  /** TTS 播报态：speaking=开始出声 / done=播放完成 / failed=发声失败 /
   *  skipped=未播出（被丢弃/打断）；空串=无信号（未启用 TTS 等）。
   *  由 buildLiveEntries 按 utteranceId 就地回填，不新增时间线行 */
  ttsState: '' | 'speaking' | 'done' | 'failed' | 'skipped';
  /** TTS 未播出的原因（tts.utterance.dropped 的 reason），仅 ttsState=skipped 时带，tooltip 用 */
  ttsReason?: string;
  /** 表达优先级（streamer.speech.priority，0-3；缺省视为常态，不标徽标） */
  priority?: number;
  /** 表达来源标签（streamer.speech.source，如 game_commentary=主控游戏自主解说） */
  speechSource?: string;
  /** 游戏侧任务终态：maicraft_execute 卡与 task.changed 任务窗口按 goal_id 配对后
   *  回填（statusLabel=终态中文短名，durationMs=受理到终态耗时，failed=失败类终态）。
   *  未配对为 null；由 liveLanes.annotateTaskEnds 写入，通用时间线不依赖此字段 */
  taskEnd?: TaskEndBadge | null;
  /** 收拢到本卡名下的进度查询卡（maicraft_goal operation=get 且 goal_id 相等）。
   *  只由 liveLanes.collapseGoalPolls 写到 execute 卡上，其他条目无此字段 */
  polls?: ShowEntry[];
  /** 收活卡的跳转目标：配对到的 framework_delegate 条目 id。只由
   *  liveLanes.buildDelegateReceipts 写到 delegate_receipt 条目上，其他条目无此字段 */
  delegateEntryId?: string;
  /** 收活卡标注的派活时刻（配对到的 framework_delegate 条目时间，Unix 毫秒） */
  delegateTsMs?: number;
  /** 交接简报（agent.lead.changed 的 handover_brief 防御解析产物）。收回方向
   *  （brief 为 null）与旧事件（字段缺失）不带此字段，切换卡据此省略展开入口 */
  brief?: LeadHandoverBrief | null;
}

/** maicraft_execute 卡上的任务终态徽标取值 */
export interface TaskEndBadge {
  statusLabel: string;
  /** 受理到终态的耗时（毫秒）；拿不到终态时刻为 null */
  durationMs: number | null;
  failed: boolean;
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

// maicraft_execute 专用卡面解析（纯函数，ToolCard 按 actor 分流时调用）

/** maicraft_execute 工具的 actor 取值：工具名历史上出现过 maicraft/minecraft 两种拼写 */
const MINECRAFT_EXECUTE_ACTORS: ReadonlySet<string> = new Set([
  'maicraft_execute',
  'minecraft_execute',
]);

/** 该工具条目是否走 maicraft_execute 专用卡面（actor = 工具名，来自 fromToolResult） */
export function isMinecraftExecuteEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && MINECRAFT_EXECUTE_ACTORS.has(entry.actor);
}

/** goal 的 ability 名去掉 maicraft: 前缀（前缀是包名，卡面上只留能力名） */
function stripAbilityPrefix(ability: string): string {
  return ability.replace(/^maicraft:/, '');
}

/** 单步行动（goal.steps 成员） */
export interface MinecraftExecuteStep {
  ability: string;
  purpose: string;
}

/** maicraft_execute 卡面的取值结果：字段缺失时为空串/空数组，渲染层按空值跳过 */
export interface MinecraftExecuteSummary {
  /** 目标能力名（已去 maicraft: 前缀） */
  ability: string;
  /** 人类写的意图句（goal.purpose，卡面主文案） */
  purpose: string;
  /** 从 target/parameters 提取的要点（坐标 / 物品·数量 / 操作名），多段用 · 连接 */
  keyPoints: string;
  /** 入参 dry_run=true 时为真（渲染灰色"预演"徽标） */
  dryRun: boolean;
  /** 异步受理状态：running=执行中 / finished=已完成；缺失或未知为空串 */
  state: string;
  /** 完成时的结果总结（result.data.result.summary），running 时为空 */
  summary: string;
  /** 分步计划（goal.steps），无则为空数组 */
  steps: MinecraftExecuteStep[];
}

/** target 字段 → 要点文本：position 给坐标，其余取 kind/name 等可读值 */
function targetKeyPoint(target: unknown): string {
  if (!isRecord(target)) return '';
  const x = num(target.x);
  const y = num(target.y);
  const z = num(target.z);
  if (x != null && y != null && z != null) return `坐标 ${x},${y},${z}`;
  return str(target.name) || str(target.kind) || str(target.item) || '';
}

/** parameters 字段 → 要点文本：识别"物品·数量"组合，其余原样跳过（完整内容在载荷区） */
function parametersKeyPoint(parameters: unknown): string {
  if (!isRecord(parameters)) return '';
  const item = str(parameters.item) || str(parameters.name);
  const count = num(parameters.count) ?? num(parameters.amount);
  if (item && count != null) return `${item} ×${count}`;
  return item || '';
}

/** maicraft_execute 的入参与结果 → 卡面取值。args/result 任一非对象时字段留空，
 *  调用方（ToolCard）以 ability/purpose 均空判定退回通用卡面 */
export function maicraftExecuteSummary(args: unknown, result: unknown): MinecraftExecuteSummary {
  const empty: MinecraftExecuteSummary = {
    ability: '',
    purpose: '',
    keyPoints: '',
    dryRun: false,
    state: '',
    summary: '',
    steps: [],
  };
  if (!isRecord(args)) return empty;
  const goal = isRecord(args.goal) ? args.goal : {};
  const dryRun = bool(args.dry_run);

  const keyPointParts = [targetKeyPoint(goal.target), parametersKeyPoint(goal.parameters)]
    .filter(Boolean)
    .map(part => part.trim())
    .filter(part => part.length > 0);

  // steps 每步 {ability, purpose}，ability 同样去前缀；非对象成员跳过
  const rawSteps = Array.isArray(goal.steps) ? goal.steps : [];
  const steps: MinecraftExecuteStep[] = rawSteps.flatMap(step => {
    if (!isRecord(step)) return [];
    const ability = stripAbilityPrefix(str(step.ability));
    const purpose = str(step.purpose);
    return ability || purpose ? [{ ability, purpose }] : [];
  });

  // 结果形状：{ok, data:{state, result:{summary, ...}}}；data 或内层 result 缺失时留空
  let state = '';
  let summary = '';
  if (isRecord(result)) {
    const data = isRecord(result.data) ? result.data : {};
    state = str(data.state);
    const inner = isRecord(data.result) ? data.result : {};
    summary = str(inner.summary);
  }

  return {
    ability: stripAbilityPrefix(str(goal.ability)),
    purpose: str(goal.purpose),
    keyPoints: keyPointParts.join(' · '),
    dryRun,
    state,
    summary,
    steps,
  };
}

// maicraft_goal（进度查询/回答/取消等目标操作）与 execute 卡的 goal_id 关联助手

/** maicraft_goal 工具的 actor 取值 */
export function isMinecraftGoalEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && entry.actor === 'maicraft_goal';
}

/** maicraft_execute 卡携带的 goal_id：结果载荷 result.data.goal_id（后端 ledger_task_id
 *  即 `maicraft-goal-<goal_id>` 的配对键）。缺失或非数字为 null */
export function minecraftExecuteGoalId(entry: ShowEntry): number | null {
  if (!isMinecraftExecuteEntry(entry)) return null;
  const result = entry.detail?.result;
  if (!isRecord(result)) return null;
  const data = isRecord(result.data) ? result.data : {};
  const goalId = num(data.goal_id);
  return goalId ?? null;
}

/** maicraft_goal 调用携带的 goal_id（入参 args.goal_id）；缺失或非数字为 null */
export function minecraftGoalIdOf(entry: ShowEntry): number | null {
  const args = entry.detail?.args;
  if (!isRecord(args)) return null;
  const goalId = num(args.goal_id);
  return goalId ?? null;
}

/** maicraft_goal 调用的 operation 入参（get/answer/cancel/…）；缺失为空串 */
export function minecraftGoalOperationOf(entry: ShowEntry): string {
  const args = entry.detail?.args;
  return isRecord(args) ? str(args.operation) : '';
}

// framework_delegate 专用卡面解析（主播 Agent 派活给游戏 Agent 的工具调用）

/** framework_delegate 工具的 actor 判定（actor = 工具名，来自 fromToolResult） */
export function isFrameworkDelegateEntry(entry: ShowEntry): boolean {
  return entry.kind === 'tool' && entry.actor === 'framework_delegate';
}

/** 是否走 framework_delegate 专用卡面：失败卡不进（走通用错误路径），与 maicraft_execute 同款处理 */
export function isFrameworkDelegateCard(entry: ShowEntry): boolean {
  return isFrameworkDelegateEntry(entry) && !entry.failed;
}

/** 派活入参里的指令原文（args.instruction）；framework_delegate 专用卡面与
 *  游戏列收活卡的指令摘要共用同一读取 */
export function frameworkDelegateInstruction(args: unknown): string {
  if (!isRecord(args)) return '';
  return str(args.instruction);
}

/** framework_delegate 卡面的取值结果：字段缺失时为空串/null，渲染层按空值跳过 */
export interface FrameworkDelegateSummary {
  /** 目标 Agent 名（args.agent，头行"→ 名字"） */
  targetAgent: string;
  /** 派活指令原文（args.instruction，卡面主文案） */
  instruction: string;
  /** 执行方复述（result.own_words）；与指令字面相同时留空（不渲染复述行） */
  ownWords: string;
  /** 目标受理标记（result.accepted） */
  accepted: boolean;
  /** 排队受理（result.queued）：目标手上有事，指令进了待办队列 */
  queued: boolean;
  /** 队列位次（result.queue_position），非数字为 null */
  queuePosition: number | null;
  /** 排队说明（result.note，卡面单行截断，全文在载荷区） */
  queueNote: string;
}

/** framework_delegate 的入参与结果 → 卡面取值。结果字段在线上样本位于 result 顶层，
 *  个别事件包一层 data 时穿透读取；入参非对象时各字段留空 */
export function frameworkDelegateSummary(args: unknown, result: unknown): FrameworkDelegateSummary {
  const a = isRecord(args) ? args : {};
  let r: Record<string, unknown> = isRecord(result) ? result : {};
  if (r.accepted === undefined && isRecord(r.data)) r = r.data;
  const instruction = frameworkDelegateInstruction(a);
  const ownWords = str(r.own_words);
  return {
    targetAgent: str(a.agent),
    instruction,
    ownWords: ownWords && ownWords !== instruction ? ownWords : '',
    accepted: bool(r.accepted),
    queued: bool(r.queued),
    queuePosition: num(r.queue_position),
    queueNote: str(r.note),
  };
}

/** framework_delegate 卡携带的任务号（结果载荷 result.task_id；个别事件包一层 data 时
 *  穿透读取）。缺失为空串。收活卡按它与 task.changed 受理回执的 task_id 做身份配对 */
export function frameworkDelegateTaskId(entry: ShowEntry): string {
  if (!isFrameworkDelegateEntry(entry)) return '';
  const result = entry.detail?.result;
  if (!isRecord(result)) return '';
  const source = result.task_id !== undefined ? result : isRecord(result.data) ? result.data : {};
  return str(source.task_id);
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
  if (entry.kind === 'agent_reply') return 'game';
  // 游戏域事件流：身体事件、叙事小卡与游戏内聊天都是游戏 Agent 采集的游戏世界事实
  if (entry.kind === 'game_body') return 'game';
  if (entry.kind === 'others_things') return 'game';
  if (entry.kind === 'game_chat') return 'game';
  if (entry.kind === 'thinking') {
    return entry.source === '游戏 Agent' ? 'game' : 'streamer';
  }
  if (
    entry.kind === 'speech' ||
    entry.kind === 'decision' ||
    entry.kind === 'verdict' ||
    entry.kind === 'stage' ||
    entry.kind === 'tts'
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

/** 可深链到 Agent 页决策轮的行：四类带轮次 ID 的行（决策/裁决/工具/发言）。
 *  stage 不带 round_id、game 行无轮次可跳，均不在列 */
export function isRoundLinkable(entry: ShowEntry): boolean {
  return (
    Boolean(entry.roundId) &&
    (entry.kind === 'decision' ||
      entry.kind === 'verdict' ||
      entry.kind === 'tool' ||
      entry.kind === 'speech')
  );
}

/** 简洁档（观众视角）可见的条目类型：对话内容本身 + 给阅读分段的结构行。
 *  工具/决策/裁决/阶段/思考/游戏上报/里程碑等管线与诊断卡整卡排除——观众在
 *  直播里看不到它们（实测游戏场次里程碑可达数十张，放进简洁档即淹没对话） */
export const CONVERSATION_KINDS: ReadonlySet<EntryKind> = new Set([
  'speech',
  'danmaku',
  'gift',
  'super_chat',
  // 上舰是付费行为，与礼物/SC 同级——观众视角应与付费消息同样可见
  'guard',
  'boundary',
  // 系统事件是运营事件（错误 / 服务启停），不豁免进"简洁也要看"集合就等于看不到
  'system',
]);

/** 简洁档过滤判定（控制台简洁档 / 未来观众面复用同一口径） */
export function isConversationEntry(entry: ShowEntry): boolean {
  return CONVERSATION_KINDS.has(entry.kind);
}

/**
 * 观众视角可见的条目：观众在直播间里能看到的那些。
 *
 * 判据是"这段内容是否出现在观众面前"，不是"谁产生的"——弹幕、礼物、她的发言、
 * 游戏侧公开进展（里程碑、公开聊天）、环节切换与场次边界都在内；
 * 工具调用、决策、思考、阶段推进、给游戏 Agent 的指令、世界事件流水（game/game_body）
 * 都只发生在后台。世界事件在观众眼里就是画面与她复述的那句话，不需要再发一条卡。
 */
export const AUDIENCE_KINDS: ReadonlySet<EntryKind> = new Set([
  'speech',
  'danmaku',
  'gift',
  'super_chat',
  'guard',
  'enter',
  'game_chat',
  'milestone',
  'rundown',
  'boundary',
]);

/** 观众视角判定：后台过程（工具/决策/思考/阶段）与游戏内私聊不进这条流 */
export function isAudienceEntry(entry: ShowEntry): boolean {
  if (!AUDIENCE_KINDS.has(entry.kind)) return false;
  return !(entry.kind === 'game_chat' && entry.badge === '私聊');
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
  simulated?: boolean;
  argPills?: ToolArgPill[];
  utteranceId?: string;
  priority?: number;
  speechSource?: string;
  /** 交接简报（仅 lead_change 交权方向携带，见 ShowEntry.brief） */
  brief?: LeadHandoverBrief | null;
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
    simulated: base.simulated ?? false,
    argPills: base.argPills ?? [],
    utteranceId: base.utteranceId ?? '',
    priority: base.priority,
    speechSource: base.speechSource,
    brief: base.brief,
    ttsState: '',
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
      simulated: bool(data.simulated),
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
      simulated: bool(data.simulated),
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

  // 上舰：付费行为与礼物/SC 同级（对齐卡片形态），层级/金额从 guard 结构化字段取
  if (messageType === 'guard') {
    const guard = isRecord(data.guard) ? data.guard : null;
    const level = guard ? (num(guard.guard_level) ?? 0) : 0;
    const levelLabel = level === 1 ? '总督' : level === 2 ? '提督' : level === 3 ? '舰长' : '';
    const total = guard ? (num(guard.total_price) ?? 0) : 0;
    // 金瓜子口径：1000 金瓜子 = 1 元（与 RoomMessagePayload 契约一致）
    const money = total > 0 ? `¥${formatAmount(total / 1000)}` : '';
    const period =
      guard && num(guard.guard_num)
        ? ` ${num(guard.guard_num)}${str(guard.guard_unit) || '月'}`
        : '';
    return makeEntry({
      id: event.id,
      kind: 'guard',
      tsMs,
      actor,
      text: fallback() || `开通${levelLabel || '舰队'}${period}`,
      badge: levelLabel ? `上舰·${levelLabel}` : '上舰',
      money,
      messageId,
      simulated: bool(data.simulated),
    });
  }

  return makeEntry({
    id: event.id,
    kind: 'danmaku',
    tsMs,
    actor,
    text: fallback(),
    messageId,
    simulated: bool(data.simulated),
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
  const toolName =
    str(data.tool_name) || event.type.slice(EVENT_FAMILY.toolResult.length) || 'tool';
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
    // TTS 全链路关联键：tts.utterance.started/finished 据此回填播报态
    utteranceId: str(data.utterance_id),
    // 表达优先级/来源：发言卡据此标非常态徽标（游戏解说/紧急/高光）
    priority: typeof data.priority === 'number' ? data.priority : undefined,
    speechSource: str(data.source) || undefined,
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
    simulated: str(data.trigger_reason) === 'dashboard:debug_test',
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
    simulated: str(data.trigger_reason) === 'dashboard:debug_test',
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
  const started = event.type === EVENT.liveStarted;
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
    text: str(data.segment_title) || (finished ? '流程单完成' : '（未命名环节）'),
    note: finished ? '' : index != null && total != null ? `环节 ${index + 1}/${total}` : '',
    badge: by === 'human' ? '手动' : by === 'system' ? '系统' : 'Agent',
  });
}

/** 语音合成失败：tts.utterance.failed（UtteranceFailedPayload）。
 *  运营者据此看懂主播"静默跳句"的原因（冷启动服务未启动等）；不改后端放弃语义 */
function fromTtsFailed(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const error = str(data.error_message) || '原因未知';
  return makeEntry({
    id: event.id,
    kind: 'tts',
    tsMs: event.timestamp_ms,
    actor: '主播',
    text: str(data.speech_text),
    note: `语音合成失败：${error}`,
    badge: '合成失败',
    failed: true,
  });
}

/** 系统事件：core.error / core.startup / core.shutdown。
 *  error 是显性失败行（红色，参照 TTS 失败行先例）；启停是中性运营标记行 */
export function fromSystemEvent(event: FeedEvent, data: Record<string, unknown>): ShowEntry | null {
  if (event.type === EVENT.coreError) {
    const message = str(data.message) || '原因未知';
    return makeEntry({
      id: event.id,
      kind: 'system',
      tsMs: event.timestamp_ms,
      text: message,
      note: str(data.collector),
      badge: '系统错误',
      failed: true,
    });
  }
  if (event.type === EVENT.coreStartup) {
    return makeEntry({
      id: event.id,
      kind: 'system',
      tsMs: event.timestamp_ms,
      text: str(data.message) || '服务已启动',
      badge: '启动',
    });
  }
  if (event.type === EVENT.coreShutdown) {
    return makeEntry({
      id: event.id,
      kind: 'system',
      tsMs: event.timestamp_ms,
      text: str(data.message) || '服务已停止',
      badge: '关闭',
    });
  }
  return null;
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

/** 游戏上报种类 → 徽标；没列出的种类按普通汇报显示 */
const REPORT_BADGE: Record<string, string> = { escalation: '升级提醒', undelivered: '没交付' };
/** 游戏上报种类 → 次要文案；没列出的种类按交付总结显示 */
const REPORT_NOTE: Record<string, string> = {
  escalation: '需要主播决策',
  undelivered: '停了但没交付结果',
};

/** game.* 事件 → ShowEntry（GamePayload，event_type 判别）。
 *  非 game.* 事件返回 null；game.milestone 不走此函数（旧路径在 LiveObserver 本地维护） */
export function toGameEntry(event: FeedEvent): ShowEntry | null {
  if (!isGameFamilyEvent(event.type)) return null;
  const data = isRecord(event.data) ? event.data : {};
  const eventType = str(data.event_type) || event.type.slice('game.'.length);
  const reportKind = str(data.report_kind);
  // 按 event_type 决定徽标语义；report 在 escalation 时升级为"升级提醒"，
  // undelivered 是游戏侧没交付就停了（系统代发），单列"没交付"，不混进交付汇报
  let badge = '';
  if (eventType === 'report') badge = REPORT_BADGE[reportKind] ?? '汇报';
  else if (eventType === 'attention_required') badge = '需要关注';
  else if (eventType === 'error') badge = '游戏错误';
  else if (eventType === 'milestone') badge = '里程碑';
  // 次要文案：scene 优先；report 时再叠加 report_kind 上下文
  const noteParts: string[] = [];
  const scene = str(data.scene);
  if (scene) noteParts.push(scene);
  if (eventType === 'report' && reportKind) {
    noteParts.push(REPORT_NOTE[reportKind] ?? '交付总结');
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
    // body_events：上报随带的原始身体事件清单（GamePayload 契约，空 = 无），
    // 上报卡渲染成缩进小列表；数组形态保证后端缺字段/脏字段时不炸
    detail: { body_events: Array.isArray(data.body_events) ? data.body_events : [] },
    source: '游戏 Agent',
  });
}

/** 递话受理：agent.prompted（运营/跨 Agent 留言送达目标）。
 *  actor = 发起方（operator 折叠为"运营"），note 承载目标 Agent 名 */
function fromPrompt(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const source = str(data.source);
  return makeEntry({
    id: event.id,
    kind: 'prompt',
    tsMs: event.timestamp_ms,
    actor: source === 'operator' ? '运营' : source || '未知',
    text: str(data.content) || summarizeEvent(event.type, data),
    note: str(data.target),
  });
}

/** Agent 每步响应：agent.replied（命令驱动型 Agent 中间步骤的行动说明）。
 *  决定卡形态渲染（llmRequestId 供缓存/Token/上下文胶囊懒取），roundId 与
 *  思考流/工具卡同轮关联键 */
function fromAgentReply(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  return makeEntry({
    id: event.id,
    kind: 'agent_reply',
    tsMs: event.timestamp_ms,
    actor: '游戏 Agent',
    text: str(data.content) || summarizeEvent(event.type, data),
    roundId: str(data.round_id),
    detail: { step: num(data.step) ?? 0, model: str(data.model) },
    llmRequestId: str(data.llm_request_id),
    source: '游戏 Agent',
  });
}

/** others_things 叙事的来源种类：payload.source_event_type（旧事件 game.body.unknown
 *  按它判别）与专名事件名末段（game.body.others_things_touched 等）共用同一组词 */
export const OTHERS_THINGS_SOURCES: ReadonlySet<string> = new Set([
  'others_things_touched',
  'others_food_taken',
]);

/** 来源种类 → 徽标实词 */
const OTHERS_THINGS_BADGE: Record<string, string> = {
  others_things_touched: '动了别人的东西',
  others_food_taken: '拿了别人家的吃的',
};

/** 身体事件是否为 others_things 叙事：专名事件名或 payload.source_event_type
 *  任一命中即算——前端判定不依赖后端专名事件的上线顺序 */
export function isOthersThingsBodyEvent(eventType: string, data: Record<string, unknown>): boolean {
  const kind = eventType.startsWith(EVENT_FAMILY.gameBody)
    ? eventType.slice(EVENT_FAMILY.gameBody.length)
    : '';
  return OTHERS_THINGS_SOURCES.has(kind) || OTHERS_THINGS_SOURCES.has(str(data.source_event_type));
}

/** 游戏身体事件：game.body.*（BodyEventPayload，kind 为判别字段）。
 *  轻量小行（对齐 whisper 级别），死亡行红色醒目；summary 已是叙事化中文事实 */
function fromBodyEvent(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const kind = str(data.kind) || event.type.slice(EVENT_FAMILY.gameBody.length);
  // others_things 叙事走专有双行小卡：实词徽标 + summary 全文（不截断），
  // 不与占位"未知"单行混排（那类单行截断会把富叙事的认账口径切没）
  if (isOthersThingsBodyEvent(event.type, data)) {
    const source =
      OTHERS_THINGS_SOURCES.has(kind) && kind
        ? kind
        : str(data.source_event_type) || event.type.slice(EVENT_FAMILY.gameBody.length);
    return makeEntry({
      id: event.id,
      kind: 'others_things',
      tsMs: event.timestamp_ms,
      actor: str(data.game) || '游戏',
      text: str(data.summary),
      badge: OTHERS_THINGS_BADGE[source] ?? '',
      source: '游戏 Agent',
    });
  }
  const badge =
    kind === 'attacked'
      ? '遇袭'
      : kind === 'attack_ended'
        ? '遇袭解除'
        : kind === 'died'
          ? '阵亡'
          : kind === 'respawned'
            ? '重生'
            : kind === 'reflex_started'
              ? '应激开始'
              : kind === 'reflex_finished'
                ? '应激结束'
                : kind === 'dimension_changed'
                  ? '维度切换'
                  : kind === 'need_unhandled'
                    ? '需求未处理'
                    : '未知';
  // 有攻击者/命中数就带上（payload 契约：无证据字段为空串/0）
  const noteParts: string[] = [];
  const attacker = str(data.attacker);
  if (attacker) noteParts.push(`来源 ${attacker}`);
  const hits = num(data.hits);
  if (hits != null && hits > 0) noteParts.push(`命中 ${hits} 次`);
  return makeEntry({
    id: event.id,
    kind: 'game_body',
    tsMs: event.timestamp_ms,
    actor: str(data.game) || '游戏',
    text: str(data.summary) || summarizeEvent(event.type, data),
    note: noteParts.join(' · '),
    badge,
    failed: kind === 'died',
    source: '游戏 Agent',
  });
}

/** 游戏内聊天：game.chat.received（GameChatPayload）。
 *  chat 行形态（actor=游戏内说话者），徽标区分私聊/系统消息，与 B 站弹幕视觉区分；
 *  公屏流由采集器采集、主播与任务中的游戏 Agent 双侧共享感知，source 标中性真实来源“游戏公屏”
 *  （双轨分栏与分组仍按 kind === 'game_chat' 归入游戏栏，不受 source 文案变更影响）。 */
function fromGameChat(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const isPrivate = bool(data.private);
  const kind = str(data.kind) || 'player';
  const suppressed = num(data.suppressed) ?? 0;
  return makeEntry({
    id: event.id,
    kind: 'game_chat',
    tsMs: event.timestamp_ms,
    actor: str(data.sender) || (kind === 'system' ? '系统消息' : '游戏内玩家'),
    text: str(data.content) || summarizeEvent(event.type, data),
    badge: isPrivate ? '私聊' : kind === 'system' ? '系统' : '游戏内',
    note: suppressed > 0 ? `另有 ${suppressed} 条被折叠` : '',
    source: '游戏公屏',
  });
}

/** 工具健康跃迁：tool.health.*（ToolHealthPayload，仅 open/closed 跃迁时发射）。
 *  熔断 → 警告系统行（failed 承载红色），恢复 → 普通系统行 */
function fromToolHealth(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const toolName =
    str(data.tool_name) || event.type.slice(EVENT_FAMILY.toolHealth.length) || 'tool';
  const open = str(data.state) === 'open';
  const failureCount = num(data.failure_count) ?? 0;
  const lastError = str(data.last_error);
  return makeEntry({
    id: event.id,
    kind: 'tool_health',
    tsMs: event.timestamp_ms,
    text: open ? `工具 ${toolName} 已熔断摘除` : `工具 ${toolName} 已恢复可用`,
    note: open ? lastError || `连续失败 ${failureCount} 次` : '',
    badge: open ? '熔断' : '恢复',
    failed: open,
  });
}

/** 主控方名称 → 中文主控角色标签（streamer 固定为「主播主控」，其余为「游戏主控（<agent>）」） */
export function formatLeadRoleLabel(agent: string): string {
  const normalized = agent.trim();
  if (!normalized || normalized === 'streamer') return '主播主控';
  return `游戏主控（${normalized}）`;
}

/** 动态主从切权原因码 → 中文说明（对照后端 LeadCoordinator / StreamerAgent / MinecraftAgent 实际发出的原因码） */
const LEAD_REASON_LABELS: Record<string, string> = {
  initial: '初始主播主控',
  manual_override: '导演手动切换',
  request_game_lead: '请求游戏主控',
  reclaim_streamer_lead: '收回主播主控',
  live_started: '开播同步主控',
  live_ended: '直播场次结束',
  rundown_changed: '流程单环节切换',
  periodic_sync: '状态周期同步',
  streamer_stop: '主播 Agent 停止',
  lead_mode_streamer_only: '已设为固定主播主控',
  mcp_disconnected: '游戏连接断开，自动收回',
  mcp_reconnected: '游戏连接恢复，重新评估主控',
  agent_ready: '游戏 Agent 就绪，重新评估主控',
  llm_unavailable: '模型不可用，自动收回',
  llm_unsendable: '模型请求无法发送，自动收回',
  llm_exception: '模型连续异常，自动收回',
  agent_stopped: '游戏 Agent 已停止，自动收回',
  game_unavailable: '游戏 Agent 不可用，自动收回',
};

/** LLM 致命故障类型 → 中文短名（用于 llm_fatal:<detail> 前缀原因码的友好展示） */
const LEAD_LLM_FATAL_KIND_LABELS: Record<string, string> = {
  balance: '余额不足',
  auth: '鉴权失败',
  unreachable: '服务不可达',
};

const LEAD_LLM_FATAL_PREFIX = 'llm_fatal:';
const LEAD_FALLBACK_REFUSE_PREFIX = 'fallback_after_refuse:';

/** 动态主从切权原因码 → 中文说明；覆盖固定原因码与动态前缀码，未知非空文案原样透传 */
export function formatLeadReasonLabel(reason: string): string {
  const trimmed = reason.trim();
  if (!trimmed) return '';
  if (trimmed in LEAD_REASON_LABELS) return LEAD_REASON_LABELS[trimmed];
  if (trimmed.startsWith(LEAD_LLM_FATAL_PREFIX)) {
    const detail = trimmed.slice(LEAD_LLM_FATAL_PREFIX.length).trim();
    if (!detail) return '模型致命故障，自动收回';
    const detailLabel = LEAD_LLM_FATAL_KIND_LABELS[detail] ?? detail;
    return `模型致命故障（${detailLabel}），自动收回`;
  }
  if (trimmed.startsWith(LEAD_FALLBACK_REFUSE_PREFIX)) {
    const detail = trimmed.slice(LEAD_FALLBACK_REFUSE_PREFIX.length).trim();
    return detail ? `目标拒收（${detail}），退回主播主控` : '目标拒收，退回主播主控';
  }
  return trimmed;
}

/** 主控权切换：agent.lead.changed（AgentLeadChangedPayload）。
 *  中文展示切换方向（如「主控权切换：主播主控 → 游戏主控（minecraft）」）、触发方式与原因/交接摘要 */
function fromLeadChanged(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const previousLead = str(data.previous_lead);
  const leadAgent = str(data.lead_agent) || (str(data.mode) === 'game' ? 'game' : 'streamer');
  const toLabel = formatLeadRoleLabel(leadAgent);
  const direction = previousLead ? `${formatLeadRoleLabel(previousLead)} → ${toLabel}` : toLabel;
  const manualOverride = str(data.manual_override);
  const badge = manualOverride && manualOverride !== 'auto' ? '手动' : '自动';
  const noteParts = [formatLeadReasonLabel(str(data.reason)), str(data.handover_summary)].filter(
    Boolean,
  );
  return makeEntry({
    id: event.id,
    kind: 'lead_change',
    tsMs: event.timestamp_ms,
    actor: leadAgent,
    text: `主控权切换：${direction}`,
    note: noteParts.join(' · '),
    badge,
    detail: data,
    brief: handoverBriefOf(data.handover_brief),
  });
}

/** agent.lead.changed 载荷的 handover_brief → 交接简报对象（契约字段全部防御解析：
 *  字符串守卫去空白、数组守卫逐项过滤、数字守卫兜 null）。null 或全空字段返回
 *  null——切换卡据此不渲染展开入口 */
export function handoverBriefOf(raw: unknown): LeadHandoverBrief | null {
  if (!isRecord(raw)) return null;
  const stringList = (value: unknown): string[] =>
    Array.isArray(value) ? value.map(item => str(item)).filter(item => item.length > 0) : [];
  const brief: LeadHandoverBrief = {
    segment_id: str(raw.segment_id),
    segment_title: str(raw.segment_title),
    segment_goal: str(raw.segment_goal),
    expected_ms: num(raw.expected_ms) ?? 0,
    save_name: str(raw.save_name),
    memory_brief: str(raw.memory_brief),
    operator_notes: stringList(raw.operator_notes),
    recent_speech: stringList(raw.recent_speech),
  };
  const allEmpty =
    !brief.segment_id &&
    !brief.segment_title &&
    !brief.segment_goal &&
    brief.expected_ms === 0 &&
    !brief.save_name &&
    !brief.memory_brief &&
    brief.operator_notes.length === 0 &&
    brief.recent_speech.length === 0;
  return allEmpty ? null : brief;
}

/** 非强杀建议转达回执：agent.lead.suggestion（运营提醒转给自主主控的游戏
 *  Agent 的送达结果）。安静单行：正文=建议原文，actor=送达对象，徽标注明
 *  送达与否，note 带受理原因与来源（operator=运营转达） */
function fromLeadSuggestion(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const accepted = bool(data.accepted);
  const target = str(data.target) || str(data.lead_agent) || '游戏 Agent';
  const reason = str(data.reason);
  const from = str(data.source);
  const noteParts = [reason, from === 'operator' ? '运营转达' : from].filter(Boolean);
  return makeEntry({
    id: event.id,
    kind: 'lead_suggestion',
    tsMs: event.timestamp_ms,
    actor: target,
    text: str(data.content),
    badge: accepted ? '已转达' : '未送达',
    note: noteParts.join(' · '),
    detail: data,
  });
}

/** 主控方自主解说回执的受理徽标：accepted+spoken=已播出 / accepted=已受理 /
 *  节流=已记录·节流 / 其余=未受理 */
export function leadCommentaryBadge(data: Record<string, unknown>): string {
  if (bool(data.accepted)) return bool(data.spoken) ? '已播出' : '已受理';
  if (str(data.reason) === 'throttled_recorded') return '已记录·节流';
  return '未受理';
}

/** 主控方自主解说事实：agent.lead.commentary（游戏主控向观众解说的受理/播出回执）。
 *  安静单行（后台事实，不进观众视角——观众看到的是润色后的台词行）：
 *  正文=解说摘要，actor=游戏名实名，徽标注明受理结果，note 带紧急度与原因 */
function fromLeadCommentary(event: FeedEvent, data: Record<string, unknown>): ShowEntry {
  const urgency = str(data.urgency);
  const reason = str(data.reason);
  const correction = bool(data.correction);
  const noteParts = [urgency === 'high' ? '急' : '', reason, correction ? '纠偏' : ''].filter(
    Boolean,
  );
  return makeEntry({
    id: event.id,
    kind: 'lead_commentary',
    tsMs: event.timestamp_ms,
    actor: str(data.game) || '游戏',
    text: str(data.summary) || summarizeEvent(event.type, data),
    badge: leadCommentaryBadge(data),
    note: noteParts.join(' · '),
    detail: data,
    source: '游戏 Agent',
  });
}

/** 非控制台事件（未知 type 等）返回 null，不进时间线 */
export function toEntry(event: FeedEvent): ShowEntry | null {
  const data = isRecord(event.data) ? event.data : {};
  if (event.type === EVENT.agentPrompted) return fromPrompt(event, data);
  if (event.type === EVENT.agentReplied) return fromAgentReply(event, data);
  if (event.type === EVENT.agentLeadChanged) return fromLeadChanged(event, data);
  if (event.type === EVENT.agentLeadSuggestion) return fromLeadSuggestion(event, data);
  if (event.type === EVENT.agentLeadCommentary) return fromLeadCommentary(event, data);
  // room.message.* 族折叠广播，成员由 payload.message_type 判别
  if (event.type === EVENT.roomMessage) return fromRoomMessage(event, data);
  if (event.type === EVENT.streamerSpeech) return fromSpeech(event, data);
  if (event.type === EVENT.plannerVerdict) return fromVerdict(event.id, event.timestamp_ms, data);
  if (event.type === EVENT.plannerDecision) return fromDecision(event.id, event.timestamp_ms, data);
  if (event.type === EVENT.streamerStage) return fromStage(event.id, event.timestamp_ms, data);
  if (event.type === EVENT.liveStarted || event.type === EVENT.liveEnded)
    return fromLiveBoundary(event, data);
  if (isToolResultEvent(event.type)) return fromToolResult(event, data);
  if (event.type === EVENT.rundownChanged) return fromRundown(event, data);
  if (event.type === EVENT.ttsUtteranceFailed) return fromTtsFailed(event, data);
  // TTS 播报态两端点不新增行：buildLiveEntries 按 utterance_id 回填到既有发言行
  if (event.type === EVENT.ttsUtteranceStarted || event.type === EVENT.ttsUtteranceFinished)
    return null;
  // 丢弃（被高优打断/过期作废等）也不新增行：同样由 buildLiveEntries 回填到既有发言行
  if (event.type === EVENT.ttsUtteranceDropped) return null;
  if (isToolHealthEvent(event.type)) return fromToolHealth(event, data);
  if (isGameBodyEvent(event.type)) return fromBodyEvent(event, data);
  if (event.type === EVENT.gameChatReceived) return fromGameChat(event, data);
  if (isCoreFamilyEvent(event.type)) return fromSystemEvent(event, data);
  // game.milestone 旧路径由 LiveObserver 本地处理；其他 game.*（report/attention_required/error）统一走 toGameEntry
  if (event.type === EVENT.gameMilestone) return fromMilestone(event, data);
  if (
    event.type === EVENT.gameReport ||
    event.type === EVENT.gameAttentionRequired ||
    event.type === EVENT.gameError
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

export function guidanceOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).reply_guidance);
}

/** 话题摘要（topic_summary）：Planner 以主播第一人称写的"打算说什么"，决策卡上作带标签的次行展示 */
export function topicOf(entry: ShowEntry): string {
  return str(decisionDetail(entry).topic_summary);
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
  // utterance_id → 已渲染的发言行；tts.utterance.started/finished 据此回填播报态
  const speechByUtterance = new Map<string, ShowEntry>();
  for (const event of events) {
    if (hiddenIds.has(event.id)) continue;
    // TTS 播报态：就地改写目标发言行，不产生新条目；发言行不在列（被裁剪/未渲染）则丢弃
    if (event.type === EVENT.ttsUtteranceStarted || event.type === EVENT.ttsUtteranceFinished) {
      const utteranceId = str(isRecord(event.data) ? event.data.utterance_id : '');
      const speech = utteranceId ? speechByUtterance.get(utteranceId) : undefined;
      if (speech) speech.ttsState = event.type === EVENT.ttsUtteranceStarted ? 'speaking' : 'done';
      continue;
    }
    // failed/dropped 同样回填播报态：failed 落到下方 toEntry 另出独立失败卡；
    // dropped 是"没播出"（被高优打断/过期作废等，不产出新行，只结算发言行状态）
    if (event.type === EVENT.ttsUtteranceFailed || event.type === EVENT.ttsUtteranceDropped) {
      const utteranceId = str(isRecord(event.data) ? event.data.utterance_id : '');
      const speech = utteranceId ? speechByUtterance.get(utteranceId) : undefined;
      if (speech) {
        speech.ttsState = event.type === EVENT.ttsUtteranceFailed ? 'failed' : 'skipped';
        if (event.type === EVENT.ttsUtteranceDropped) {
          speech.ttsReason =
            str(isRecord(event.data) ? event.data.reason : '') || '语句被丢弃，未播出';
        }
      }
      if (event.type === EVENT.ttsUtteranceDropped) continue;
    }
    const entry = toEntry(event);
    if (!entry) continue;
    if (entry.kind === 'speech' && entry.utteranceId) {
      speechByUtterance.set(entry.utteranceId, entry);
    } else if (entry.kind === 'verdict' && entry.roundId) {
      verdictByRound.set(entry.roundId, entry);
    } else if (entry.kind === 'decision' && entry.roundId) {
      const verdict = verdictByRound.get(entry.roundId);
      if (verdict) {
        // decision 的统计/失败信息回填裁决卡；decision 自身不再成卡。
        // simulated 一并回填：verdict 载荷不带 trigger_reason，测试标记只能在轮末补上
        verdict.detail = decisionDetail(entry) || verdict.detail;
        verdict.llmRequestId = entry.llmRequestId || verdict.llmRequestId;
        verdict.failed = entry.failed;
        verdict.simulated = verdict.simulated || entry.simulated;
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

// 思考行：视图层从思考流旁路状态合成（不进事件 store、不回看，旁路边界不变）

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

/**
 * 新增条目数：以上一帧末条 id 为锚，找不到锚点则视为全新。
 *
 * 两处贴底跟随共用同一口径——单列时间线（页面级滚动容器）与双轨后台视角
 * （每列各有滚动容器）都要靠它判断"来了多少新东西"。
 */
export function countAddedEntries(next: ShowEntry[], prev: ShowEntry[]): number {
  const anchor = prev.length > 0 ? prev[prev.length - 1].id : null;
  if (!anchor) return next.length;
  const index = next.findIndex(entry => entry.id === anchor);
  return index === -1 ? next.length : next.length - 1 - index;
}

// 相对时间标签

/**
 * 时间线条目的相对时间戳（如"刚刚/12s 前/3m 前/2h 前/1d 前"）。
 * 调用方传入当前 Unix 毫秒时刻（FeedTimeline 内部 1s tick 维护）即可。
 */
export function relativeTime(nowMs: number, tsMs: number): string {
  return formatDurationShort(Math.max(0, Math.floor((nowMs - tsMs) / 1000)));
}
