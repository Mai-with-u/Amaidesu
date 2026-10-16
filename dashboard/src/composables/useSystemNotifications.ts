/**
 * 系统级事件 → WebUI 右上角显式通知（运营触达面）。
 *
 * 订阅 core.error（错误告警）/ core.shutdown（服务关闭）/ core.startup（服务启动）
 * 三类系统事件，经共享 wsClient 派发 ElNotification（约 8 秒自动消失）。
 * 连接与重连完全交给 websocket store（不自绘重连循环）；本组合式只做
 * 消息过滤、节流与弹窗。
 *
 * 节流规则（防错误风暴刷屏）：
 * - 相同正文 60 秒窗口内只弹一次（去重）
 * - 全局滑动窗口每分钟最多弹 5 条，超限静默丢弃；被抑制后的下一条
 *   正文末尾附「（此前已抑制 N 条）」提示
 *
 * 生命周期语义：挂载在 AppLayout（无 keep-alive 包裹，整会话单实例），
 * setup 时订阅、组件卸载时退订并清理去重窗口——订阅类组合式按激活态启停，
 * 卸载即彻底停（无后台残留计时器）。
 */

import { onScopeDispose } from 'vue';
import { ElNotification } from 'element-plus';
import { useWebSocketStore } from '@/stores';
import type { WebSocketMessage } from '@/types';

/** 相同正文去重窗口（毫秒） */
const DEDUP_WINDOW_MS = 60_000;
/** 全局滑动窗口上限：每分钟最多弹出的通知条数 */
const MAX_PER_WINDOW = 5;
/** 全局滑动窗口宽度（毫秒） */
const RATE_WINDOW_MS = 60_000;
/** 通知自动消失时长（毫秒） */
const NOTIFICATION_DURATION_MS = 8000;

/** 提取 payload 的人类可读正文（core.* 载荷的 message 字段） */
function messageText(data: Record<string, unknown>): string {
  const message = data.message;
  return typeof message === 'string' && message.length > 0 ? message : '';
}

export function useSystemNotifications(): void {
  const wsStore = useWebSocketStore();

  /** 相同正文 → 上次弹出时刻（毫秒） */
  const lastShownByMessage = new Map<string, number>();
  /** 全局滑动窗口内各次弹出的时刻 */
  const shownAt: number[] = [];
  /** 因超出窗口上限被静默丢弃的条数（随下一条可见通知提示） */
  let suppressedCount = 0;
  /** 去重/限流窗口清理计时器（卸载时清除） */
  let sweepTimer: ReturnType<typeof setInterval> | null = null;

  function pruneWindows(now: number): void {
    for (const [text, shownMs] of lastShownByMessage) {
      if (now - shownMs >= DEDUP_WINDOW_MS) lastShownByMessage.delete(text);
    }
    while (shownAt.length > 0 && now - shownAt[0] >= RATE_WINDOW_MS) {
      shownAt.shift();
    }
  }

  function notify(kind: 'error' | 'warning' | 'success', title: string, text: string): void {
    ElNotification({
      title,
      message: text,
      type: kind,
      duration: NOTIFICATION_DURATION_MS,
    });
  }

  function handleMessage(msg: WebSocketMessage): void {
    const now = Date.now();
    pruneWindows(now);

    if (msg.type === 'core.error') {
      const text = messageText(msg.data);
      if (!text) return;
      // 去重优先于限流：重复正文即使有额度也不再弹
      const lastShownMs = lastShownByMessage.get(text);
      if (lastShownMs !== undefined && now - lastShownMs < DEDUP_WINDOW_MS) return;
      if (shownAt.length >= MAX_PER_WINDOW) {
        suppressedCount += 1;
        return;
      }
      lastShownByMessage.set(text, now);
      shownAt.push(now);
      const suffix = suppressedCount > 0 ? `（此前已抑制 ${suppressedCount} 条）` : '';
      suppressedCount = 0;
      notify('error', '系统错误', `${text}${suffix}`);
      return;
    }

    if (msg.type === 'core.shutdown') {
      notify('warning', '服务已关闭', messageText(msg.data) || 'Amaidesu 正在关闭');
      return;
    }

    if (msg.type === 'core.startup') {
      notify('success', '服务已启动', messageText(msg.data) || 'Amaidesu 已就绪');
    }
  }

  // 周期清扫窗口，防长会话下 Map / 数组无界增长；卸载时一并清除
  sweepTimer = setInterval(() => pruneWindows(Date.now()), DEDUP_WINDOW_MS);

  wsStore.subscribe(handleMessage);

  onScopeDispose(() => {
    wsStore.unsubscribe(handleMessage);
    if (sweepTimer !== null) {
      clearInterval(sweepTimer);
      sweepTimer = null;
    }
  });
}
