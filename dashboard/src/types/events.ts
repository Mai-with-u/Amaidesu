/**
 * WS 协议类型词汇（前端唯一事实源）
 *
 * 镜像对象 = 后端 dashboard WS 桥接的广播词汇，不是 EventBus 事件全集：
 *   - src/modules/dashboard/websocket/broadcaster.py（订阅清单 + 折叠规则）
 *   - src/modules/events/event_type_map.py（折叠类型名常量）
 * 规则：直通事件的 type = EventBus 事件名；两个折叠例外——room.message.*
 * 五个成员折叠为 "room.message"、room.state.watched_count 折叠为 "room.state"，
 * 族内成员由载荷字段判别（payload.message_type 等）；通配族订阅
 * （tool.result.# / tool.health.# / game.body.# / game.chat.#）的 type 为
 * 具体事件名。
 *
 * 漂移守护：tests/dashboard/test_ws_event_vocabulary_contract.py 把本表与
 * broadcaster 订阅清单双向对账——后端新增/退役广播事件时该测试红，前端
 * 在此表过目（补键或显式决定不接）。未进 broadcaster 订阅清单的 EventBus
 * 事件不广播到 WS，不属于本表范围。
 */

/** 总线事件类型表（折叠族 + 直通精确名，值即 WS type 字段） */
const EVENT = {
  // —— 折叠族（广播时折叠，成员在载荷内判别）——
  roomMessage: 'room.message',
  roomState: 'room.state',

  // —— 直通：决策可观测 ——
  plannerDecision: 'planner.decision',
  plannerVerdict: 'planner.verdict',
  streamerStage: 'streamer.stage',

  // —— 直通：场次生命周期与发言 ——
  liveStarted: 'live.started',
  liveEnded: 'live.ended',
  streamerSpeech: 'streamer.speech',
  rundownChanged: 'rundown.changed',

  // —— 直通：游戏上报与游戏域事件流已知成员 ——
  gameReport: 'game.report',
  gameAttentionRequired: 'game.attention_required',
  gameError: 'game.error',
  gameMilestone: 'game.milestone',
  gameChatReceived: 'game.chat.received',

  // —— 直通：任务 / Agent 交互 / TTS 播报态 ——
  taskChanged: 'task.changed',
  agentPrompted: 'agent.prompted',
  agentReplied: 'agent.replied',
  ttsUtteranceStarted: 'tts.utterance.started',
  ttsUtteranceFinished: 'tts.utterance.finished',
  ttsUtteranceFailed: 'tts.utterance.failed',

  // —— 直通：系统生命周期 ——
  coreStartup: 'core.startup',
  coreShutdown: 'core.shutdown',
  coreError: 'core.error',

  // —— 直通：后台任务 ——
  backgroundTaskChanged: 'background.task.changed',
} as const;

export { EVENT };

/** 通配订阅族前缀（订阅面为族；WS type 为族内具体事件名，前端按前缀判别与切片） */
export const EVENT_FAMILY = {
  toolResult: 'tool.result.',
  toolHealth: 'tool.health.',
  gameBody: 'game.body.',
  gameChat: 'game.chat.',
} as const;

/** 通配族事件名：WS type 为该族下的具体事件名 */
export type ToolResultEvent = `${typeof EVENT_FAMILY.toolResult}${string}`;
export type ToolHealthEvent = `${typeof EVENT_FAMILY.toolHealth}${string}`;
export type GameBodyEvent = `${typeof EVENT_FAMILY.gameBody}${string}`;
export type GameChatEvent = `${typeof EVENT_FAMILY.gameChat}${string}`;

/** 后台任务状态（running=在跑；finished/failed=终态，收到即从面板移除） */
export type BackgroundTaskState = 'running' | 'finished' | 'failed';

/**
 * 后台任务一行（background.task.changed 载荷 / GET /api/v1/background-tasks 行）。
 * 发布方 = 后端 BackgroundTaskRegistry；finished/failed 事件照发，随后该任务从
 * 注册表移除（前端收到终态后把行从列表去掉，终态提示走通知）。
 */
export interface BackgroundTask {
  task_id: string;
  /** 给人看的标题，如「整理场次：2026-11-06 周五 22:22」 */
  title: string;
  state: BackgroundTaskState;
  /** 自由文本进度，如「第 2 笔/共 3 笔」「还缺 320 行」 */
  progress_text: string;
  /** 任务开始时刻（Unix 毫秒） */
  started_at_ms: number;
  /** 失败原因，仅 state=failed 时带 */
  failure_reason?: string;
  /** 当前这笔模型调用已输出的正文字数（仅流式调用期间大于 0，其他阶段缺省 0） */
  output_chars?: number;
  /** 当前这笔模型调用最近输出的文字尾巴（浮层实时预览用，非模型调用期间为空） */
  output_tail?: string;
}

/** 总线事件 type 全集（WS kind="event"：折叠族 + 直通精确名 + 通配族具体名） */
export type EventName =
  | (typeof EVENT)[keyof typeof EVENT]
  | ToolResultEvent
  | ToolHealthEvent
  | GameBodyEvent
  | GameChatEvent;

/** 观测流 type（kind="stream"，独立缓冲，不入事件通道） */
export const STREAM = {
  thinkingDelta: 'thinking.delta',
} as const;

/** 协议帧 type（非总线事件：历史引导 / 日志通道 / 传输层握手与心跳） */
export const PROTOCOL = {
  eventsHistory: 'events.history',
  logEntry: 'log.entry',
  connected: 'connected',
  subscribeResponse: 'subscribe_response',
  ping: 'ping',
} as const;

/** WS 消息类别（kind 字段全集；前端按它分流进不同缓冲） */
export type WsKind = 'event' | 'stream' | 'log' | 'transport';

/** WS 消息 type 字段全集（总线事件 + 观测流 + 协议帧） */
export type WsMessageType =
  | EventName
  | (typeof STREAM)[keyof typeof STREAM]
  | (typeof PROTOCOL)[keyof typeof PROTOCOL];

/**
 * 通配族判别守卫：前缀单源 + 类型收窄（收窄后可安全做前缀切片取族内后缀）。
 */
export function isToolResultEvent(type: WsMessageType): type is ToolResultEvent {
  return type.startsWith(EVENT_FAMILY.toolResult);
}

export function isToolHealthEvent(type: WsMessageType): type is ToolHealthEvent {
  return type.startsWith(EVENT_FAMILY.toolHealth);
}

export function isGameBodyEvent(type: WsMessageType): type is GameBodyEvent {
  return type.startsWith(EVENT_FAMILY.gameBody);
}

/** planner 族谓词（decision / verdict 两个成员的粗粒度归族） */
export function isPlannerEvent(type: WsMessageType): boolean {
  return type === EVENT.plannerDecision || type === EVENT.plannerVerdict;
}

/** game 族谓词（上报/里程碑/身体/聊天通配族的粗粒度归族） */
export function isGameFamilyEvent(type: WsMessageType): boolean {
  return (
    type === EVENT.gameReport ||
    type === EVENT.gameAttentionRequired ||
    type === EVENT.gameError ||
    type === EVENT.gameMilestone ||
    type === EVENT.gameChatReceived ||
    isGameBodyEvent(type) ||
    type.startsWith(EVENT_FAMILY.gameChat)
  );
}

/** live 族谓词（场次生命周期两端点） */
export function isLiveFamilyEvent(type: WsMessageType): boolean {
  return type === EVENT.liveStarted || type === EVENT.liveEnded;
}

/** core 族谓词（系统生命周期三点） */
export function isCoreFamilyEvent(type: WsMessageType): boolean {
  return type === EVENT.coreStartup || type === EVENT.coreShutdown || type === EVENT.coreError;
}
