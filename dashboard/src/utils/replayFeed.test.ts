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
    // 完成态形状与实时 fromRundown 对齐（note 留空，完成语义由 text 承载）
    expect(entries[1]).toMatchObject({
      kind: 'rundown',
      text: '流程单完成',
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

  it('tool.result.* → tool 条目（入参药丸/成败徽标与实时同管线）', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'tool.result.minecraft_glance',
        data: {
          tool_name: 'minecraft_glance',
          status: 'success',
          arguments: { target: 'chest' },
          result: '箱子里有 3 组铁锭',
          round_id: 'r1',
        },
      }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'tool',
      actor: 'minecraft_glance',
      badge: '成功',
      roundId: 'r1',
    });
  });

  it('planner.verdict → verdict 条目', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'planner.verdict',
        data: { topic_summary: '聊玩什么', round_id: 'r2' },
      }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'verdict',
      text: '聊玩什么',
      badge: '回应',
      roundId: 'r2',
    });
  });

  it('verdict + decision 同轮：decision 统计回填裁决卡（轮次合并与实时一致）', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'planner.verdict',
        data: { topic_summary: '聊玩什么', round_id: 'r3' },
      }),
      item({
        kind: 'event',
        ts_ms: 200,
        event_type: 'planner.decision',
        data: {
          should_reply: true,
          topic_summary: '聊玩什么',
          round_id: 'r3',
          llm_request_id: 'req-1',
        },
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: 'verdict', roundId: 'r3', llmRequestId: 'req-1' });
  });

  it('agent.prompted / agent.replied → prompt / agent_reply 条目', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 100,
        event_type: 'agent.prompted',
        data: { source: 'operator', content: '去挖矿吧', target: 'minecraft' },
      }),
      item({
        kind: 'event',
        ts_ms: 200,
        event_type: 'agent.replied',
        data: {
          agent: 'minecraft',
          content: '开始挖矿',
          round_id: 'g1',
          step: 2,
          llm_request_id: 'req-2',
        },
      }),
    ]);
    expect(entries[0]).toMatchObject({
      kind: 'prompt',
      actor: '运营',
      text: '去挖矿吧',
      note: 'minecraft',
    });
    expect(entries[1]).toMatchObject({
      kind: 'agent_reply',
      text: '开始挖矿',
      llmRequestId: 'req-2',
    });
  });

  it('事件与明细行按时间归并（同一毫秒事件在前）', () => {
    const entries = buildReplayEntries([
      item({ kind: 'danmaku', ts_ms: 100, content: '弹幕', user_name: '观众' }),
      item({
        kind: 'event',
        ts_ms: 50,
        event_type: 'planner.decision',
        data: { should_reply: false },
      }),
    ]);
    expect(entries.map(e => e.tsMs)).toEqual([50, 100]);
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

describe('buildReplayEntries：上舰明细行', () => {
  it('guard：出上舰卡（badge 上舰、text 取人读描述），不再兜底为弹幕', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'guard',
        ts_ms: 100,
        user_name: '舰长哥',
        content: '开通舰队 1个月',
        message_id: 'g1',
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      kind: 'guard',
      actor: '舰长哥',
      text: '开通舰队 1个月',
      badge: '上舰',
      messageId: 'g1',
    });
  });
});

describe('buildReplayEntries：tts 播报态回看呈现', () => {
  const speechItem = item({
    kind: 'event',
    ts_ms: 100,
    event_type: 'streamer.speech',
    data: { text: '大家好呀', utterance_id: 'utt_1' },
  });

  it('utterance 记录存在：started/finished 回填发言卡播报态，不新增行', () => {
    const entries = buildReplayEntries([
      speechItem,
      item({
        kind: 'event',
        ts_ms: 150,
        event_type: 'tts.utterance.started',
        data: { utterance_id: 'utt_1' },
      }),
      item({
        kind: 'event',
        ts_ms: 200,
        event_type: 'tts.utterance.finished',
        data: { utterance_id: 'utt_1' },
      }),
    ]);
    // 两端点不产生独立条目：仅发言卡一张
    const speechEntries = entries.filter(e => e.kind === 'speech');
    expect(speechEntries).toHaveLength(1);
    // finished 后置最终态为已播完
    expect(speechEntries[0].ttsState).toBe('done');
  });

  it('utterance 记录缺失：发言卡播报态为空（诊断流缺 tts 记录时静默降级）', () => {
    const entries = buildReplayEntries([speechItem]);
    const speech = entries.find(e => e.kind === 'speech');
    expect(speech?.ttsState).toBe('');
  });

  it('failed 记录：渲染为红色合成失败告警行', () => {
    const entries = buildReplayEntries([
      item({
        kind: 'event',
        ts_ms: 120,
        event_type: 'tts.utterance.failed',
        data: { utterance_id: 'utt_2', speech_text: '这句话', error_message: 'TTS 服务未启动' },
      }),
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({
      kind: 'tts',
      text: '这句话',
      note: '语音合成失败：TTS 服务未启动',
      failed: true,
    });
  });
});
