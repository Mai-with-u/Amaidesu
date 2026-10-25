/**
 * 告警收件箱 store：把"需要人看一眼"的异常持久记账，错过不丢。
 *
 * 与 useSystemNotifications 分工：弹窗通知约 8 秒自动消失（即时触达面），
 * 本 store 是持久账本——所有入账条目保留到滚动上限，供顶栏铃铛随时翻查。
 * 两者各自订阅 WebSocket，互不依赖；本 store 在 store 初始化时订阅，
 * 与 Pinia 生命周期同寿（连接与重连由 websocket store 承担）。
 *
 * 入账来源（订阅面刻意收窄，避免把日志流水搬进收件箱）：
 * - core.error：系统级错误（组件启动失败等），error 级
 * - tts.utterance.failed：播报失败，观众侧可感知，error 级
 * - game.attention_required：游戏侧需要人接手，warning 级
 * 不收 log.entry：模块级 WARNING/ERROR 日志噪声大、常与上述事件重复报障，
 * 且无对应跳转页面，混入会稀释收件箱的信噪比。
 *
 * 去重合并口径：相同 title + source_page 在 60 秒窗口内重复入账，
 * 只累加 count 并刷新时刻，不新增条目；滚动上限 200 条，超出裁掉最旧。
 */

import { computed, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { useWebSocketStore } from './websocket';
import { EVENT } from '@/types/events';
import type { WebSocketMessage } from '@/types';

/** 相同 title + source_page 的去重合并窗口（毫秒） */
const MERGE_WINDOW_MS = 60_000;
/** 收件箱滚动上限，超出裁掉最旧条目 */
const MAX_ALERTS = 200;

/** 告警级别：error=故障需处理，warning=需留意 */
export type AlertLevel = 'error' | 'warning';

/** 告警账本条目 */
export interface AlertItem {
  id: string;
  level: AlertLevel;
  /** 一句话说明发生了什么（"动作 + 对象"视角） */
  title: string;
  /** 补充细节（来源载荷原文等），可为空串 */
  detail: string;
  /** 建议前往处理的页面路由路径 */
  source_page: string;
  /** 最近一次发生的时刻（Unix 毫秒；合并时刷新） */
  ts_ms: number;
  /** 是否已读 */
  read: boolean;
  /** 60 秒窗口内的发生次数（去重合并累加） */
  count: number;
}

/** 入账参数（id/read/count 由 store 生成与维护，调用方不传） */
export interface AlertInput {
  level: AlertLevel;
  title: string;
  detail?: string;
  source_page: string;
  ts_ms: number;
}

/** 取 payload 的人类可读正文（message / reason 字段，core.* 与 tts/game 载荷通用） */
function payloadText(data: Record<string, unknown>): string {
  for (const key of ['message', 'reason']) {
    const value = data[key];
    if (typeof value === 'string' && value.length > 0) return value;
  }
  return '';
}

/** 逐条消息 → 零或一条入账（返回 null 表示不入账）；集中维护事件到页面的映射 */
function toAlertInput(message: WebSocketMessage): AlertInput | null {
  if (message.kind !== 'event') return null;
  const text = payloadText(message.data);
  switch (message.type) {
    case EVENT.coreError:
      return text
        ? {
            level: 'error',
            title: text,
            detail: '',
            source_page: '/logs',
            ts_ms: message.timestamp_ms,
          }
        : null;
    case EVENT.ttsUtteranceFailed:
      return {
        level: 'error',
        title: text || '有一条播报没能发出来',
        detail: text ? '' : JSON.stringify(message.data),
        source_page: '/live',
        ts_ms: message.timestamp_ms,
      };
    case EVENT.gameAttentionRequired:
      return {
        level: 'warning',
        title: text || '游戏在等你接手处理',
        detail: text ? '' : JSON.stringify(message.data),
        source_page: '/live',
        ts_ms: message.timestamp_ms,
      };
    default:
      return null;
  }
}

export const useAlertsStore = defineStore('alerts', () => {
  const alerts = shallowRef<AlertItem[]>([]);
  const unreadCount = computed(() => alerts.value.filter(item => !item.read).length);

  /**
   * 入账一条告警：60 秒窗口内相同 title + source_page 合并为既有条目
   * （累加计数、刷新时刻、重新置为未读）；否则新增条目并按上限裁掉最旧。
   */
  function recordAlert(input: AlertInput): void {
    const current = alerts.value;
    const mergeKey = `${input.title}\u0000${input.source_page}`;
    for (let i = current.length - 1; i >= 0; i -= 1) {
      const item = current[i]!;
      if (input.ts_ms - item.ts_ms >= MERGE_WINDOW_MS) break;
      if (`${item.title}\u0000${item.source_page}` === mergeKey) {
        const merged: AlertItem = {
          ...item,
          ts_ms: input.ts_ms,
          count: item.count + 1,
          read: false,
        };
        const next = [...current];
        next[i] = merged;
        alerts.value = next;
        return;
      }
    }
    const entry: AlertItem = {
      id: `alert-${crypto.randomUUID()}`,
      level: input.level,
      title: input.title,
      detail: input.detail ?? '',
      source_page: input.source_page,
      ts_ms: input.ts_ms,
      read: false,
      count: 1,
    };
    const next =
      current.length >= MAX_ALERTS
        ? [...current.slice(current.length - MAX_ALERTS + 1), entry]
        : [...current, entry];
    alerts.value = next;
  }

  /** 标记单条已读 */
  function markRead(id: string): void {
    alerts.value = alerts.value.map(item => (item.id === id ? { ...item, read: true } : item));
  }

  /** 全部标记已读 */
  function markAllRead(): void {
    alerts.value = alerts.value.map(item => (item.read ? item : { ...item, read: true }));
  }

  // 订阅 WebSocket 事件流入账；store 与应用同寿，连接/重连由 websocket store 承担
  useWebSocketStore().subscribe(message => {
    const input = toAlertInput(message);
    if (input !== null) recordAlert(input);
  });

  return { alerts, unreadCount, recordAlert, markRead, markAllRead };
});
