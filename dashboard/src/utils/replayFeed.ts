/**
 * 回看时间线的纯映射：REST 场次时间线明细（SessionTimelineItem）→ ShowEntry 列表。
 *
 * 实时路径的条目构造规则统一在 utils/liveFeed.ts（toEntry 及各 from* 分支）；
 * 回看路径拿到的是后端合并好的明细行（danmaku / gift / super_chat / speech /
 * enter / event），逐条映射为同一套 ShowEntry，保证同一内容两条路径渲染一致。
 * 事件类条目中的 game.*（report/attention_required/error）复用 liveFeed.toGameEntry，
 * 条目构造逻辑保持单点维护。
 */

import {
  formatAmount,
  fromDecision,
  fromStage,
  isRecord,
  makeEntry,
  num,
  str,
  toGameEntry,
  type FeedEvent,
  type ShowEntry,
} from '@/utils/liveFeed';
import type { SessionTimelineItem } from '@/types';

/** 场次时间线明细 → 展示条目（入参顺序即输出顺序；id 带 rp- 前缀与实时条目区分） */
export function buildReplayEntries(items: SessionTimelineItem[]): ShowEntry[] {
  const next: ShowEntry[] = [];
  items.forEach((entry, index) => {
    const id = `rp-${entry.ts_ms}-${index}`;
    if (entry.kind === 'event') {
      const data = isRecord(entry.data) ? entry.data : {};
      const type = str(entry.event_type);
      if (type === 'planner.decision') {
        next.push(fromDecision(id, entry.ts_ms, data));
      } else if (type === 'streamer.stage') {
        next.push(fromStage(id, entry.ts_ms, data));
      } else if (type === 'live.started' || type === 'live.ended') {
        next.push(
          makeEntry({
            id,
            kind: 'boundary',
            tsMs: entry.ts_ms,
            text: type === 'live.started' ? '场次开启' : '场次结束',
            note: str(data.title) || str(data.reason),
          }),
        );
      } else if (type === 'rundown.changed') {
        const index = typeof data.index === 'number' ? data.index : 0;
        const total = typeof data.total === 'number' ? data.total : 0;
        const finished = total > 0 && index >= total;
        next.push(
          makeEntry({
            id,
            kind: 'rundown',
            tsMs: entry.ts_ms,
            text: str(data.segment_title) || (finished ? '流程单完成' : '环节切换'),
            note: finished ? '流程单已全部完成' : `环节 ${index}/${total}`,
            badge: str(data.by) === 'human' ? '手动' : str(data.by) === 'system' ? '系统' : 'Agent',
          }),
        );
      } else if (type === 'game.milestone') {
        next.push(
          makeEntry({
            id,
            kind: 'milestone',
            tsMs: entry.ts_ms,
            text: str(data.message),
            note: [str(data.game), str(data.scene)].filter(Boolean).join(' · '),
          }),
        );
      } else if (
        type === 'game.report' ||
        type === 'game.attention_required' ||
        type === 'game.error'
      ) {
        // 实时路径已由共享层 toEntry→toGameEntry 自动入列；
        // 回看路径手工拼出 FeedEvent 调用同一函数，保持条目构造逻辑单点维护
        const gameEntry = toGameEntry({
          id,
          type,
          timestamp_ms: entry.ts_ms,
          data,
        } as FeedEvent);
        if (gameEntry) next.push(gameEntry);
      }
      return;
    }
    if (entry.kind === 'speech') {
      next.push(
        makeEntry({
          id,
          kind: 'speech',
          tsMs: entry.ts_ms,
          actor: '主播',
          text: str(entry.text),
          speak: true,
          replyTo: str(entry.reply_to_message_id),
        }),
      );
      return;
    }
    if (entry.kind === 'gift') {
      next.push(
        makeEntry({
          id,
          kind: 'gift',
          tsMs: entry.ts_ms,
          actor: str(entry.user_name) || '匿名观众',
          text: `送出 ${str(entry.gift_name)} ×${num(entry.gift_count) ?? 1}`,
          badge: '礼物',
          messageId: str(entry.message_id),
          simulated: entry.simulated === true,
        }),
      );
      return;
    }
    if (entry.kind === 'super_chat') {
      const amount = num(entry.amount);
      next.push(
        makeEntry({
          id,
          kind: 'super_chat',
          tsMs: entry.ts_ms,
          actor: str(entry.user_name) || '匿名观众',
          text: str(entry.content),
          badge: 'SC',
          money: amount != null ? `¥${formatAmount(amount)}` : '',
          messageId: str(entry.message_id),
          simulated: entry.simulated === true,
        }),
      );
      return;
    }
    // danmaku / enter
    next.push(
      makeEntry({
        id,
        kind: entry.kind === 'enter' ? 'enter' : 'danmaku',
        tsMs: entry.ts_ms,
        actor: str(entry.user_name) || '匿名观众',
        text:
          entry.kind === 'enter'
            ? `${str(entry.user_name) || '观众'} 进入直播间`
            : str(entry.content),
        messageId: str(entry.message_id),
        simulated: entry.simulated === true,
      }),
    );
  });
  return next;
}
