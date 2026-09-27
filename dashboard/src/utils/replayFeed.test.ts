// replayFeed.ts 回看时间线纯映射的测试
//
// 覆盖：REST 场次时间线明细 → ShowEntry 的逐分支映射
//   1. 消息类（danmaku / enter / gift / super_chat / speech）
//   2. 事件类（planner.decision / streamer.stage / live.* / rundown.changed /
//      game.milestone / game.report）
//   3. 未知事件类型跳过、id 规则（rp- 前缀 + 序号）

import { describe, expect, it } from 'vitest';

import { buildReplayEntries } from '@/utils/replayFeed';
import { fromDecision, fromStage } from '@/utils/liveFeed';
import type { SessionTimelineItem } from '@/types';

function item(
  partial: Partial<SessionTimelineItem> & { kind: string; ts_ms: number },
): SessionTimelineItem {
  return { ...partial } as SessionTimelineItem;
}

describe('buildReplayEntries：消息类明细', () => {
  it('danmaku：actor 取 user_name、缺省匿名，携带 message_id 与 simulated', () => {
    const entries = buildReplayEntries([
      item({ kind: 'danmaku', ts_ms: 100, user_name: '小明', content: '主播好', message_id: 'm1' }),
      item({ kind: 'danmaku', ts_ms: 200, content: '路过的观众', simulated: true }),
    ]);
    expect(entries).toHaveLength(2);
    expect(entries[0]).toMatchObject({
      kind: 'danmaku',
      tsMs: 100,
      actor: '小明',
      text: '主播好',
      messageId: 'm1',
    });
    expect(entries[1]).toMatchObject({ actor: '匿名观众', text: '路过的观众', simulated: true });
  });

  it('enter：合成"进入直播间"文案', () => {
    const entries = buildReplayEntries([item({ kind: 'enter', ts_ms: 100, user_name: '小红' })]);
    expect(entries[0]).toMatchObject({ kind: 'enter', text: '小红 进入直播间' });
  });

  it('gift：文案含名称与数量、badge 礼物', () => {
    const entries = buildReplayEntries([
      item({ kind: 'gift', ts_ms: 100, user_name: '土豪', gift_name: '火箭', gift_count: 3 }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'gift',
      actor: '土豪',
      text: '送出 火箭 ×3',
      badge: '礼物',
    });
  });

  it('super_chat：金额格式化为 ¥ 前缀、badge SC', () => {
    const entries = buildReplayEntries([
      item({ kind: 'super_chat', ts_ms: 100, user_name: '老板', content: '上热门', amount: 52 }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'super_chat',
      text: '上热门',
      badge: 'SC',
      money: '¥52',
    });
  });

  it('speech：actor 固定主播、speak 置位、回复引用透传', () => {
    const entries = buildReplayEntries([
      item({ kind: 'speech', ts_ms: 100, text: '大家好呀', reply_to_message_id: 'm9' }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'speech',
      actor: '主播',
      text: '大家好呀',
      speak: true,
      replyTo: 'm9',
    });
  });
});

describe('buildReplayEntries：事件类明细', () => {
  it('planner.decision / streamer.stage 与实时路径同一构造函数', () => {
    const data = { stage: 'planning', agent_state: 'running' };
    const decisionData = { round_id: 'r1' };
    const entries = buildReplayEntries([
      item({ kind: 'event', ts_ms: 100, event_type: 'planner.decision', data: decisionData }),
      item({ kind: 'event', ts_ms: 200, event_type: 'streamer.stage', data }),
    ]);
    expect(entries).toEqual([
      fromDecision('rp-100-0', 100, decisionData),
      fromStage('rp-200-1', 200, data),
    ]);
  });

  it('live.started / live.ended → boundary 条目', () => {
    const entries = buildReplayEntries([
      item({ kind: 'event', ts_ms: 100, event_type: 'live.started', data: { title: '周五场' } }),
      item({ kind: 'event', ts_ms: 200, event_type: 'live.ended', data: { reason: '手动关闭' } }),
    ]);
    expect(entries[0]).toMatchObject({ kind: 'boundary', text: '场次开启', note: '周五场' });
    expect(entries[1]).toMatchObject({ kind: 'boundary', text: '场次结束', note: '手动关闭' });
  });

  it('rundown.changed：环节进度标注与完成态', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'rundown.changed',
        data: { index: 1, total: 3, segment_title: '唱歌', by: 'human' },
      }),
      item({
        kind: 'event',
        ts_ms: 200,
        event_type: 'rundown.changed',
        data: { index: 3, total: 3, by: 'agent' },
      }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'rundown',
      text: '唱歌',
      note: '环节 1/3',
      badge: '手动',
    });
    expect(entries[1]).toMatchObject({
      kind: 'rundown',
      text: '流程单完成',
      note: '流程单已全部完成',
      badge: 'Agent',
    });
  });

  it('game.milestone → milestone 条目（note 拼 game · scene）', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'game.milestone',
        data: { message: '获得钻石', game: 'Minecraft', scene: '矿区' },
      }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'milestone',
      text: '获得钻石',
      note: 'Minecraft · 矿区',
    });
  });

  it('game.report → game 条目（与实时路径共用 toGameEntry）', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'game.report',
        data: { report_kind: 'escalation', message: '库存告急' },
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: 'game', badge: '升级提醒' });
  });

  it('未知事件类型跳过不产出条目', () => {
    const entries = buildReplayEntries([
      item({ kind: 'event', ts_ms: 100, event_type: 'system.heartbeat', data: {} }),
    ]);
    expect(entries).toHaveLength(0);
  });
});

describe('buildReplayEntries：通用规则', () => {
  it('id 规则：rp-<ts>-<序号>，输出保持入参顺序', () => {
    const entries = buildReplayEntries([
      item({ kind: 'danmaku', ts_ms: 100, content: 'a' }),
      item({ kind: 'danmaku', ts_ms: 100, content: 'b' }),
    ]);
    expect(entries.map(e => e.id)).toEqual(['rp-100-0', 'rp-100-1']);
  });

  it('空输入返回空列表', () => {
    expect(buildReplayEntries([])).toEqual([]);
  });
});
