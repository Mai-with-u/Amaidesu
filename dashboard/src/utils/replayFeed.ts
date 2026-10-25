/**
 * 回看时间线的纯映射：REST 场次时间线明细（SessionTimelineItem）→ ShowEntry 列表。
 *
 * 条目构造与实时路径单点同源：明细行（danmaku / gift / super_chat / speech /
 * enter）在本地映射为同一套 ShowEntry；事件条目（kind="event"）还原成 FeedEvent
 * 喂给实时同管线 buildLiveEntries（toEntry 分发 + verdict/decision 轮次合并），
 * 两路结果按时间归并——回看与实时共用同一套卡片构造，卡面永不漂移。
 */

import {
  buildLiveEntries,
  formatAmount,
  makeEntry,
  mergeEntriesByTime,
  type FeedEvent,
  type ShowEntry,
} from '@/utils/liveFeed';
import { isRecord, num, str } from '@/utils/guards';
import type { SessionTimelineItem, WsMessageType } from '@/types';

/** 场次时间线明细 → 展示条目（入参顺序即输出顺序；id 带 rp- 前缀与实时条目区分） */
export function buildReplayEntries(items: SessionTimelineItem[]): ShowEntry[] {
  const detailEntries: ShowEntry[] = [];
  const feedEvents: FeedEvent[] = [];
  items.forEach((entry, index) => {
    const id = `rp-${entry.ts_ms}-${index}`;
    if (entry.kind === 'event') {
      const data = isRecord(entry.data) ? entry.data : {};
      // REST 边界：event_type 是线上字符串，词汇由后端契约测试守护，此处显式收口
      feedEvents.push({
        id,
        type: str(entry.event_type) as WsMessageType,
        timestamp_ms: entry.ts_ms,
        data,
      });
      return;
    }
    if (entry.kind === 'speech') {
      detailEntries.push(
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
      detailEntries.push(
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
      detailEntries.push(
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
    detailEntries.push(
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
  return mergeEntriesByTime(detailEntries, buildLiveEntries(feedEvents, new Set<string>()));
}
