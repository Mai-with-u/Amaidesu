/**
 * 系统级事件 → WebUI 右上角显式通知（运营触达面）。
 *
 * 订阅三类事件，经共享 wsClient 派发 ElNotification（error/warning 不自动
 * 消失、需手动关闭；success 等信息类约 8 秒自动消失）：
 * - core.error（错误告警）——唯一真实发布者是低频的采集器连接失败
 * - background.task.changed（后台任务终态）——任务失败弹错误通知
 *   （标题+失败原因）、任务完成弹成功通知（标题+用时）；running 载荷
 *   不弹（在跑状态由底栏 BackgroundTaskBar 常驻展示，无需打扰）
 * - live.started（开播）——开播后查一次下播整理状态，若上一场的记忆还没
 *   整理完则弹 warning 通知，说明整理完成前主播用的是旧一场的记忆
 * 连接与重连完全交给 websocket store（不自绘重连循环）；本组合式只做
 * 消息过滤、节流与弹窗。
 *
 * 不再订阅 core.startup / core.shutdown：两者与浏览器连接时机结构性错配
 * （启动事件早于浏览器连上、关闭事件与断连同时发生），订阅以来从未真正
 * 弹出过——通知面只保留能到达浏览器的源。
 *
 * 节流规则（防错误风暴刷屏，只作用于 core.error 这一路）：
 * - 相同正文 60 秒窗口内只弹一次（去重）
 * - 全局滑动窗口每分钟最多弹 5 条，超限静默丢弃；被抑制后的下一条
 *   正文末尾附「（此前已抑制 N 条）」提示
 * 开播提示每场只发一次，天然低频，不走这套节流
 *
 * 生命周期语义：挂载在 AppLayout（无 keep-alive 包裹，整会话单实例），
 * setup 时订阅、组件卸载时退订并清理去重窗口——订阅类组合式按激活态启停，
 * 卸载即彻底停（无后台残留计时器）。
 */

import { onScopeDispose } from 'vue';
import { formatElapsedMs } from '@/utils/format';
import { ElNotification } from 'element-plus';
import { useWebSocketStore } from '@/stores';
import { ledgerApi } from '@/api';
import { EVENT } from '@/types/events';
import type { BackgroundTask } from '@/types/events';
import type { WebSocketMessage } from '@/types';

/** 相同正文去重窗口（毫秒） */
const DEDUP_WINDOW_MS = 60_000;
/** 全局滑动窗口上限：每分钟最多弹出的通知条数 */
const MAX_PER_WINDOW = 5;
/** 全局滑动窗口宽度（毫秒） */
const RATE_WINDOW_MS = 60_000;
/** 通知自动消失时长（毫秒）：信息/成功类用；error/warning 不自动消失（见 notify） */
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
      // 错误/警告类必须手动关闭（duration 0）：故障持续期间自动消失会让
      // 运营者错过告警；信息/成功类保持自动消失
      duration: kind === 'error' || kind === 'warning' ? 0 : NOTIFICATION_DURATION_MS,
    });
  }

  /**
   * 开播后检查上一场的下播整理是否做完：没做完则弹 warning，说明主播
   * 当前记忆还是旧一场的基线。查询失败不打扰运营者（静默跳过）。
   */
  async function checkPendingSettle(): Promise<void> {
    try {
      const response = await ledgerApi.getSettleState();
      const pending = response.data.pending_count;
      if (pending > 0) {
        notify(
          'warning',
          '记忆整理未完成',
          `已开播，但上一场的记忆还没整理完（${pending} 场待整理），整理完成前主播的记忆是旧一场的`,
        );
      }
    } catch {
      // 查询失败不在通知面暴露：无法判定状态时保持沉默
    }
  }

  function handleMessage(msg: WebSocketMessage): void {
    const now = Date.now();
    pruneWindows(now);

    if (msg.type === EVENT.liveStarted) {
      void checkPendingSettle();
      return;
    }

    if (msg.type === EVENT.coreError) {
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

    if (msg.type === EVENT.backgroundTaskChanged) {
      const task = msg.data as unknown as BackgroundTask;
      const elapsed = formatElapsedMs(Date.now() - task.started_at_ms);
      if (task.state === 'failed') {
        notify('error', `后台任务失败：${task.title}`, task.failure_reason || '未提供失败原因');
      } else if (task.state === 'finished') {
        notify('success', `后台任务完成：${task.title}`, `用时 ${elapsed}`);
      }
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
