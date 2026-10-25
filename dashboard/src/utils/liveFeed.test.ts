// liveFeed.ts 直播时间线纯逻辑的测试
//
// 覆盖三条主线：
//   1. WS 事件 → ShowEntry 的映射规则（toEntry 及各 from* 分支、取值助手）
//   2. 事件流折叠（buildLiveEntries 的轮回填/隐藏/限长）
//   3. 视图层合成行（思考行归并、会话模式折叠 buildChatRows）

import { describe, expect, it } from 'vitest';

import type { WsMessageType } from '@/types';
import {
  MAX_ENTRIES,
  type FeedEvent,
  agentGroupOf,
  buildChatRows,
  buildLiveEntries,
  buildThinkingRow,
  batchSizeOf,
  callerSourceLabel,
  chatProcessSummary,
  decisionDetail,
  formatAmount,
  fromDecision,
  guidanceOf,
  isChatProcessKind,
  isConversationEntry,
  isRoundLinkable,
  isSilentDecision,
  makeEntry,
  mergeEntriesByTime,
  pickToolText,
  plannerMsOf,
  rawOf,
  relativeTime,
  replyMsOf,
  summarizeToolArgs,
  toEntry,
  toGameEntry,
  toolArgPills,
  userLabel,
  type EntryKind,
  type ShowEntry,
} from './liveFeed';

let seq = 0;

/** 构造 FeedEvent（events store 入口形状：WebSocketMessage + 去重 id）。
 *  type 允许合成词汇（unknown.type 类），显式断言 = 线上字符串按契约收口 */
function evt(type: string, data: Record<string, unknown>, tsMs = 1000): FeedEvent {
  seq += 1;
  return { id: `e${seq}`, type: type as WsMessageType, timestamp_ms: tsMs, data };
}

describe('通用取值助手', () => {
  it('formatAmount：整数原样，小数保留两位', () => {
    expect(formatAmount(50)).toBe('50');
    expect(formatAmount(49.5)).toBe('49.50');
  });

  it('pickToolText：按 TOOL_TEXT_KEYS 优先级取首个非空文本', () => {
    expect(pickToolText({ speech_text: '优先', text: '其次' })).toBe('优先');
    expect(pickToolText({ text: '其次', summary: '兜底' })).toBe('其次');
    expect(pickToolText({ summary: '兜底' })).toBe('兜底');
    expect(pickToolText({ unrelated: 1 })).toBe('');
    expect(pickToolText('not-a-record')).toBe('');
  });
});

describe('summarizeToolArgs / toolArgPills', () => {
  it('紧凑摘要 key="value" 逗号连接，字符串带引号', () => {
    expect(summarizeToolArgs({ text: '你好', n: 2 })).toBe('text="你好", n=2');
  });

  it('对象值压 JSON 片段', () => {
    expect(summarizeToolArgs({ opts: { a: 1 } })).toBe('opts={"a":1}');
  });

  it('字符串值超 48 字截断补省略号', () => {
    const text = summarizeToolArgs({ long: 'x'.repeat(60) });
    expect(text).toBe(`long="${'x'.repeat(48)}…"`);
  });

  it('总长超 200 截断补省略号（单值先按 48 字截断，需多键堆过阈值）', () => {
    const args: Record<string, string> = {};
    for (let i = 0; i < 10; i += 1) args[`k${i}`] = 'x'.repeat(60);
    const text = summarizeToolArgs(args);
    expect(text.length).toBe(201);
    expect(text.endsWith('…')).toBe(true);
  });

  it('非对象入参回空串', () => {
    expect(summarizeToolArgs('text')).toBe('');
    expect(summarizeToolArgs(null)).toBe('');
  });

  it('药丸上限 6 枚，超出合并为计数药丸', () => {
    const args = { a: 1, b: 2, c: 3, d: 4, e: 5, f: 6, g: 7, h: 8 };
    const pills = toolArgPills(args);
    expect(pills).toHaveLength(7);
    expect(pills[6]).toEqual({ key: '+2', value: '其余参数见折叠面板' });
  });

  it('6 枚以内不产生计数药丸；非对象入参无药丸', () => {
    expect(toolArgPills({ a: 1 })).toEqual([{ key: 'a', value: '1' }]);
    expect(toolArgPills('text')).toEqual([]);
  });
});

describe('userLabel / callerSourceLabel', () => {
  it('昵称优先，其次 #id，最后匿名观众', () => {
    expect(userLabel({ name: 'Alice' })).toBe('Alice');
    expect(userLabel({ id: 'u1' })).toBe('#u1');
    expect(userLabel({})).toBe('匿名观众');
    expect(userLabel('x')).toBe('匿名观众');
  });

  it('caller_source 折叠到人类可读标签，其他原样保留', () => {
    expect(callerSourceLabel('planner-react')).toBe('主播决策');
    expect(callerSourceLabel('minecraft-react')).toBe('游戏 Agent');
    expect(callerSourceLabel('minecraft-handoff')).toBe('游戏 Agent');
    expect(callerSourceLabel('web-search')).toBe('web-search');
    expect(callerSourceLabel('')).toBe('');
  });
});

describe('makeEntry 默认值与头像首字', () => {
  it('缺省字段补空值，initial 取 actor 首字并大写', () => {
    const entry = makeEntry({ id: 'e1', kind: 'danmaku', tsMs: 1, actor: 'alice', text: 'hi' });
    expect(entry.initial).toBe('A');
    expect(entry.note).toBe('');
    expect(entry.failed).toBe(false);
    expect(entry.speak).toBe(false);
    expect(entry.argPills).toEqual([]);
    expect(entry.detail).toBe(null);
  });

  it('initial 跳过 # 前缀；空 actor 回 ?', () => {
    expect(makeEntry({ id: 'e', kind: 'danmaku', tsMs: 1, actor: '#abc', text: '' }).initial).toBe(
      'A',
    );
    expect(makeEntry({ id: 'e', kind: 'danmaku', tsMs: 1, text: '' }).initial).toBe('?');
  });
});

describe('agentGroupOf 三类归属', () => {
  const entry = (kind: Parameters<typeof agentGroupOf>[0]['kind'], source = '') =>
    makeEntry({ id: 'e', kind, tsMs: 1, text: '', source });

  it('speech/decision/verdict/stage 恒主播组', () => {
    expect(agentGroupOf(entry('speech'))).toBe('streamer');
    expect(agentGroupOf(entry('decision'))).toBe('streamer');
    expect(agentGroupOf(entry('verdict'))).toBe('streamer');
    expect(agentGroupOf(entry('stage'))).toBe('streamer');
  });

  it('thinking 按来源归类', () => {
    expect(agentGroupOf(entry('thinking', '游戏 Agent'))).toBe('game');
    expect(agentGroupOf(entry('thinking', ''))).toBe('streamer');
  });

  it('tool 按 source 归类：主播决策/游戏 Agent/minecraft 前缀，其余回主播', () => {
    expect(agentGroupOf(entry('tool', '主播决策'))).toBe('streamer');
    expect(agentGroupOf(entry('tool', '游戏 Agent'))).toBe('game');
    expect(agentGroupOf(entry('tool', 'minecraft-handoff'))).toBe('game');
    expect(agentGroupOf(entry('tool', 'web-search'))).toBe('streamer');
    expect(agentGroupOf(entry('tool', ''))).toBe('streamer');
  });

  it('game 归游戏组；弹幕等其余归房间组', () => {
    expect(agentGroupOf(entry('game'))).toBe('game');
    expect(agentGroupOf(entry('danmaku'))).toBe('room');
    expect(agentGroupOf(entry('gift'))).toBe('room');
  });
});

describe('room.message → 条目（message_type 判别）', () => {
  it('弹幕：正文/消息 ID/注入标记', () => {
    const entry = toEntry(
      evt('room.message', {
        user: { name: 'Alice' },
        content: '你好',
        message_id: 'm1',
        simulated: true,
      }),
    );
    expect(entry).toMatchObject({
      kind: 'danmaku',
      actor: 'Alice',
      text: '你好',
      messageId: 'm1',
      simulated: true,
    });
  });

  it('礼物：名称与数量；gift 缺失回退摘要', () => {
    const entry = toEntry(
      evt('room.message', {
        user: { name: 'Alice' },
        message_type: 'gift',
        gift: { name: '小星星', count: 3 },
      }),
    );
    expect(entry).toMatchObject({ kind: 'gift', text: '送出 小星星 ×3', badge: '礼物' });

    const fallback = toEntry(
      evt('room.message', { user: { name: 'Alice' }, message_type: 'gift', content: '送礼' }),
    );
    expect(fallback?.text).toBe('送礼');
  });

  it('SC：金额徽标，整数与两位小数两种格式', () => {
    const entry = toEntry(
      evt('room.message', {
        user: { name: 'Alice' },
        message_type: 'super_chat',
        sc: { amount: 50 },
      }),
    );
    expect(entry).toMatchObject({ kind: 'super_chat', badge: 'SC', money: '¥50' });

    const entry2 = toEntry(
      evt('room.message', {
        user: { name: 'Alice' },
        message_type: 'super_chat',
        sc: { amount: 49.5 },
      }),
    );
    expect(entry2?.money).toBe('¥49.50');
  });

  it('进场：无消息 ID', () => {
    const entry = toEntry(evt('room.message', { user: { name: 'Alice' }, message_type: 'enter' }));
    expect(entry).toMatchObject({ kind: 'enter', text: 'Alice 进入直播间', messageId: '' });
  });

  it('message_type 缺省按弹幕处理', () => {
    const entry = toEntry(evt('room.message', { user: { name: 'Alice' }, content: 'hi' }));
    expect(entry?.kind).toBe('danmaku');
  });
});

describe('tool.result.* → 工具卡', () => {
  it('正文承载入参摘要，入参缺失回退结果文本', () => {
    const withArgs = toEntry(
      evt('tool.result.speak', {
        tool_name: 'speak',
        status: 'ok',
        arguments: { text: '你好' },
        result: { speech_text: '说出来的话' },
      }),
    );
    expect(withArgs).toMatchObject({ kind: 'tool', actor: 'speak', text: 'text="你好"' });
    expect(withArgs?.argPills).toEqual([{ key: 'text', value: '你好' }]);

    const withoutArgs = toEntry(
      evt('tool.result.speak', {
        tool_name: 'speak',
        status: 'ok',
        result: { speech_text: '说出来的话' },
      }),
    );
    expect(withoutArgs?.text).toBe('说出来的话');
  });

  it('status 恒定成败徽标：失败带错误 note，成功无 note', () => {
    const failed = toEntry(
      evt('tool.result.speak', { tool_name: 'speak', status: 'error', error_message: '超时' }),
    );
    expect(failed).toMatchObject({ failed: true, badge: '失败', note: '超时' });

    const ok = toEntry(evt('tool.result.speak', { tool_name: 'speak', status: 'ok' }));
    expect(ok).toMatchObject({ failed: false, badge: '成功', note: '' });
  });

  it('无 status 徽标留空，避免抢视觉', () => {
    const entry = toEntry(evt('tool.result.speak', { tool_name: 'speak' }));
    expect(entry?.badge).toBe('');
  });

  it('tool_name 缺失从事件 type 抠名；speak 工具 speak 标记为真', () => {
    const entry = toEntry(evt('tool.result.speak', { status: 'ok' }));
    expect(entry).toMatchObject({ actor: 'speak', speak: true });
  });

  it('caller_source 映射到来源标签；detail 承载原始三字段', () => {
    const entry = toEntry(
      evt('tool.result.web_search', {
        status: 'ok',
        caller_source: 'planner-react',
        arguments: { q: '天气' },
        result: { text: '晴' },
        error_message: '',
      }),
    );
    expect(entry?.source).toBe('主播决策');
    expect(entry?.detail).toEqual({
      args: { q: '天气' },
      result: { text: '晴' },
      error_message: '',
    });
  });

  it('游戏 Agent 来源透传', () => {
    const entry = toEntry(
      evt('tool.result.minecraft_todo', { status: 'ok', caller_source: 'minecraft-handoff' }),
    );
    expect(entry?.source).toBe('游戏 Agent');
  });
});

describe('streamer.speech → 发言卡', () => {
  it('发言/情绪/回应链/轮次/LLM 请求指针', () => {
    const entry = toEntry(
      evt('streamer.speech', {
        text: '大家好',
        emotion: 'happy',
        reply_to_message_id: 'm1',
        round_id: 'r1',
        llm_request_id: 'req1',
      }),
    );
    expect(entry).toMatchObject({
      kind: 'speech',
      actor: '主播',
      text: '大家好',
      note: 'happy',
      speak: true,
      replyTo: 'm1',
      roundId: 'r1',
      llmRequestId: 'req1',
    });
  });
});

describe('planner.decision → 决策卡四态', () => {
  it('失败态：徽标失败、正文为错误、failed 置真', () => {
    const entry = fromDecision('d1', 1, { error: 'LLM 超时', round_id: 'r1' });
    expect(entry).toMatchObject({
      kind: 'decision',
      actor: '决策',
      badge: '失败',
      text: 'LLM 超时',
      failed: true,
      note: '',
      roundId: 'r1',
    });
  });

  it('回应态：正文为主题摘要，note 留空', () => {
    const entry = fromDecision('d1', 1, {
      should_reply: true,
      topic_summary: '聊聊皮肤',
      round_id: 'r1',
    });
    expect(entry).toMatchObject({ badge: '回应', text: '聊聊皮肤', note: '' });
  });

  it('普通沉默：带主题时正文点名主题', () => {
    const withTopic = fromDecision('d1', 1, { should_reply: false, topic_summary: '聊天气' });
    expect(withTopic).toMatchObject({ badge: '沉默', text: '决定不回应（聊天气）' });

    const withoutTopic = fromDecision('d1', 1, { should_reply: false });
    expect(withoutTopic.text).toBe('决定不回应');
  });

  it('手动触发标记 simulated', () => {
    expect(fromDecision('d1', 1, { trigger_reason: 'dashboard:debug_test' }).simulated).toBe(true);
    expect(fromDecision('d1', 1, {}).simulated).toBe(false);
  });
});

describe('planner.verdict / streamer.stage / live 边界', () => {
  it('裁决卡：回应徽标，主题缺失回退"决定发言"', () => {
    const entry = toEntry(evt('planner.verdict', { topic_summary: '回个招呼', round_id: 'r1' }));
    expect(entry).toMatchObject({
      kind: 'verdict',
      badge: '回应',
      text: '回个招呼',
      roundId: 'r1',
    });

    const bare = toEntry(evt('planner.verdict', {}));
    expect(bare?.text).toBe('决定发言');
  });

  it('阶段卡：STAGE_LABEL 映射 + 未知阶段透传 + running 可发言', () => {
    const planning = toEntry(evt('streamer.stage', { stage: 'planning' }));
    expect(planning).toMatchObject({ kind: 'stage', text: '阶段：决策中' });

    const running = toEntry(evt('streamer.stage', { stage: 'idle', agent_state: 'running' }));
    expect(running).toMatchObject({ text: '阶段：空闲', speak: true });

    const unknown = toEntry(evt('streamer.stage', { stage: 'weird' }));
    expect(unknown?.text).toBe('阶段：weird');

    const empty = toEntry(evt('streamer.stage', {}));
    expect(empty?.text).toBe('阶段：阶段变化');
  });

  it('场次开启/结束：badge 取来源，空场次丢弃有标记', () => {
    const started = toEntry(evt('live.started', { source: 'manual', title: '首播' }));
    expect(started).toMatchObject({
      kind: 'boundary',
      text: '场次开启',
      badge: 'manual',
      note: '首播',
    });

    const ended = toEntry(evt('live.ended', { empty_discarded: true, reason: '无人观看' }));
    expect(ended).toMatchObject({ text: '场次结束', badge: '空场次已丢弃', note: '无人观看' });
  });
});

describe('rundown.changed / game.*', () => {
  it('环节切换：序号标注与完成判别，by 三档徽标', () => {
    const switching = toEntry(
      evt('rundown.changed', { segment_title: '闲聊', index: 2, total: 5, by: 'agent' }),
    );
    expect(switching).toMatchObject({
      kind: 'rundown',
      text: '闲聊',
      note: '环节 2/5',
      badge: 'Agent',
    });

    const finished = toEntry(
      evt('rundown.changed', { index: 5, total: 5, by: 'human', segment_title: '' }),
    );
    expect(finished).toMatchObject({ text: '流程单完成', note: '', badge: '手动' });

    const bySystem = toEntry(evt('rundown.changed', { by: 'system' }));
    expect(bySystem?.badge).toBe('系统');

    const blankTitle = toEntry(evt('rundown.changed', { segment_title: '  ', index: 3, total: 5 }));
    expect(blankTitle).toMatchObject({ text: '（未命名环节）' });
  });

  it('语音合成失败行：错误文案带原因与原文', () => {
    const entry = toEntry(
      evt('tts.utterance.failed', {
        speech_text: '大家好呀',
        engine: 'gptsovits',
        error_message: 'connection refused',
      }),
    );
    expect(entry).toMatchObject({
      kind: 'tts',
      text: '大家好呀',
      note: '语音合成失败：connection refused',
      badge: '合成失败',
      failed: true,
    });
  });

  it('里程碑：message + game·scene 元信息', () => {
    const entry = toEntry(
      evt('game.milestone', { message: '下了矿井', game: 'Minecraft', scene: '主世界' }),
    );
    expect(entry).toMatchObject({
      kind: 'milestone',
      text: '下了矿井',
      note: 'Minecraft · 主世界',
    });
  });

  it('toGameEntry：report 双档徽标（escalation 升级提醒）', () => {
    const normal = toGameEntry(
      evt('game.report', {
        game: 'Minecraft',
        scene: '矿井',
        message: '汇报',
        report_kind: 'summary',
      }),
    );
    expect(normal).toMatchObject({
      kind: 'game',
      badge: '汇报',
      note: '矿井 · 交付总结',
      source: '游戏 Agent',
      actor: 'Minecraft',
    });

    const escalation = toGameEntry(
      evt('game.report', { scene: '矿井', message: '卡住', report_kind: 'escalation' }),
    );
    expect(escalation).toMatchObject({ badge: '升级提醒', note: '矿井 · 需要主播决策' });
  });

  it('toGameEntry：attention_required/error 徽标，error 标 failed；非 game.* 返回 null', () => {
    const attention = toGameEntry(evt('game.attention_required', { message: '需要看一眼' }));
    expect(attention?.badge).toBe('需要关注');

    const error = toGameEntry(evt('game.error', { message: '崩了' }));
    expect(error).toMatchObject({ badge: '游戏错误', failed: true });

    expect(toGameEntry(evt('room.message', { content: 'hi' }))).toBe(null);
  });

  it('toEntry：未识别事件（system.* 等）不进时间线', () => {
    expect(toEntry(evt('system.hello', {}))).toBe(null);
  });
});

describe('决策卡 detail 取值助手', () => {
  const entry = fromDecision('d1', 1, {
    should_reply: false,
    reply_guidance: '别接这个话',
    batch: [{}, {}, {}],
    planner_duration_ms: 1200,
    reply_duration_ms: 800,
    planner_raw: 'RAW',
  });

  it('isSilentDecision：未回应且未失败即静默', () => {
    expect(isSilentDecision(entry)).toBe(true);
    expect(isSilentDecision(fromDecision('d', 1, { error: 'x' }))).toBe(false);
  });

  it('guidanceOf/batchSizeOf/plannerMsOf/replyMsOf/rawOf 各取一字段', () => {
    expect(guidanceOf(entry)).toBe('别接这个话');
    expect(batchSizeOf(entry)).toBe(3);
    expect(batchSizeOf(fromDecision('d', 1, { batch: 'not-array' }))).toBe(0);
    expect(plannerMsOf(entry)).toBe(1200);
    expect(replyMsOf(entry)).toBe(800);
    expect(rawOf(entry)).toBe('RAW');
    expect(decisionDetail(entry)).toBe(entry.detail);
  });
});

describe('buildLiveEntries 折叠规则', () => {
  it('verdict 先到、decision 后到：合并为单裁决卡，decision 不再成卡', () => {
    const events = [
      evt('planner.verdict', { topic_summary: '回个招呼', round_id: 'r1' }, 1000),
      evt(
        'planner.decision',
        { should_reply: true, topic_summary: '回个招呼', round_id: 'r1', llm_request_id: 'req9' },
        2000,
      ),
    ];
    const entries = buildLiveEntries(events, new Set());
    expect(entries).toHaveLength(1);
    expect(entries[0]).toMatchObject({ kind: 'verdict', roundId: 'r1', llmRequestId: 'req9' });
    expect(decisionDetail(entries[0]).should_reply).toBe(true);
  });

  it('失败 decision 回填：裁决卡改判失败并带上错误文案', () => {
    const events = [
      evt('planner.verdict', { topic_summary: '回个招呼', round_id: 'r1' }, 1000),
      evt('planner.decision', { error: '炸了', round_id: 'r1' }, 2000),
    ];
    const [entry] = buildLiveEntries(events, new Set());
    expect(entry).toMatchObject({ kind: 'verdict', failed: true, badge: '失败', text: '炸了' });
  });

  it('simulated 标记可从轮末 decision 补回裁决卡', () => {
    const events = [
      evt('planner.verdict', { topic_summary: 'x', round_id: 'r1' }, 1000),
      evt(
        'planner.decision',
        { should_reply: true, round_id: 'r1', trigger_reason: 'dashboard:debug_test' },
        2000,
      ),
    ];
    const [entry] = buildLiveEntries(events, new Set());
    expect(entry.simulated).toBe(true);
  });

  it('无先行 verdict 的 decision 照常成卡', () => {
    const events = [evt('planner.decision', { should_reply: false, round_id: 'r2' }, 1000)];
    const entries = buildLiveEntries(events, new Set());
    expect(entries).toHaveLength(1);
    expect(entries[0].kind).toBe('decision');
  });

  it('hiddenIds 里的事件被跳过', () => {
    const hidden = evt('room.message', { user: { name: 'A' }, content: '被清空' }, 1000);
    const kept = evt('room.message', { user: { name: 'B' }, content: '保留' }, 2000);
    const entries = buildLiveEntries([hidden, kept], new Set([hidden.id]));
    expect(entries).toHaveLength(1);
    expect(entries[0].text).toBe('保留');
  });

  it(`超出 ${MAX_ENTRIES} 条时保尾丢弃最旧`, () => {
    const events = Array.from({ length: MAX_ENTRIES + 5 }, (_, i) =>
      evt('room.message', { user: { name: `u${i}` }, content: `c${i}` }, 1000 + i),
    );
    const entries = buildLiveEntries(events, new Set());
    expect(entries).toHaveLength(MAX_ENTRIES);
    expect(entries[0].text).toBe('c5');
    expect(entries[MAX_ENTRIES - 1].text).toBe(`c${MAX_ENTRIES + 4}`);
  });
});

describe('relativeTime', () => {
  it('秒差换算为短标签', () => {
    expect(relativeTime(10_000, 4_000)).toBe('6s 前');
    expect(relativeTime(120_000, 60_000)).toBe('1m 前');
  });

  it('未来时间戳钳到 0，显示"刚刚"', () => {
    expect(relativeTime(1_000, 9_000)).toBe('刚刚');
  });
});

describe('思考行合成与归并', () => {
  it('id 由轮次/段归属/段号派生且稳定；三种 phase 各有标签', () => {
    const planner = buildThinkingRow({
      roundId: 'r1',
      phase: 'planner',
      step: 2,
      tsMs: 100,
      text: 't',
    });
    expect(planner).toMatchObject({
      id: 'think:r1:planner:2',
      actor: '思考 · 步骤 2',
      kind: 'thinking',
      roundId: 'r1',
      source: '',
    });

    const replyer = buildThinkingRow({
      roundId: 'r1',
      phase: 'replyer',
      step: 1,
      tsMs: 100,
      text: 't',
    });
    expect(replyer.actor).toBe('生成思考');

    const minecraft = buildThinkingRow({
      roundId: 'r1',
      phase: 'minecraft',
      step: 1,
      tsMs: 100,
      text: 't',
    });
    expect(minecraft).toMatchObject({ actor: '游戏 Agent · 思考', source: '游戏 Agent' });
  });

  it('mergeEntriesByTime：同毫秒思考行在前，乱序思考行就地排序，余量追加', () => {
    const entries = [
      makeEntry({ id: 'e1', kind: 'danmaku', tsMs: 1000, text: 'e1' }),
      makeEntry({ id: 'e2', kind: 'danmaku', tsMs: 3000, text: 'e2' }),
    ];
    const rows = [
      buildThinkingRow({ roundId: 'r', phase: 'planner', step: 2, tsMs: 1500, text: 'b' }),
      buildThinkingRow({ roundId: 'r', phase: 'planner', step: 1, tsMs: 500, text: 'a' }),
    ];
    const merged = mergeEntriesByTime(entries, rows);
    expect(merged.map(e => e.id)).toEqual(['think:r:planner:1', 'e1', 'think:r:planner:2', 'e2']);
  });

  it('mergeEntriesByTime：同毫秒思考行先于事件（思考先于同刻落地的事件）', () => {
    const entries = [makeEntry({ id: 'e1', kind: 'danmaku', tsMs: 1000, text: '' })];
    const rows = [
      buildThinkingRow({ roundId: 'r', phase: 'replyer', step: 1, tsMs: 1000, text: '' }),
    ];
    expect(mergeEntriesByTime(entries, rows).map(e => e.id)).toEqual(['think:r:replyer:1', 'e1']);
  });
});

describe('buildChatRows 会话模式折叠', () => {
  const danmaku = (id: string) => makeEntry({ id, kind: 'danmaku', tsMs: 1, text: id });
  const speech = (id: string) => makeEntry({ id, kind: 'speech', tsMs: 1, text: id });
  const tool = (id: string) => makeEntry({ id, kind: 'tool', tsMs: 1, text: id });
  const stage = (id: string) => makeEntry({ id, kind: 'stage', tsMs: 1, text: id });

  it('观众气泡按原序铺在发言前，过程行折叠成发言后的过程条', () => {
    const rows = buildChatRows([danmaku('d1'), danmaku('d2'), tool('t1'), speech('s1')], new Set());
    expect(rows.map(e => e.id)).toEqual(['d1', 'd2', 's1', 'chat-process:s1']);
    expect(rows[3]).toMatchObject({ kind: 'process_group', text: '1 工具', note: '1' });
  });

  it('多类型过程行按出现顺序计数', () => {
    const rows = buildChatRows(
      [stage('t1'), stage('t2'), tool('t3'), speech('s1'), tool('t4'), speech('s2')],
      new Set(),
    );
    expect(rows.map(e => e.id)).toEqual(['s1', 'chat-process:s1', 's2', 'chat-process:s2']);
    expect(rows[1].text).toBe('2 阶段 · 1 工具');
  });

  it('展开的组把过程行按原时序铺回过程条之后', () => {
    const rows = buildChatRows(
      [tool('t1'), stage('t2'), speech('s1')],
      new Set(['chat-process:s1']),
    );
    expect(rows.map(e => e.id)).toEqual(['s1', 'chat-process:s1', 't1', 't2']);
  });

  it('无发言的过程行自成静默组，绝不丢行', () => {
    const rows = buildChatRows([danmaku('d1'), tool('t1'), stage('t2')], new Set());
    expect(rows.map(e => e.id)).toEqual(['d1', 'chat-process:silent-1']);
    expect(rows[1]).toMatchObject({ kind: 'process_group', text: '1 工具 · 1 阶段', note: '2' });
  });

  it('发言之后无发言衔接的尾部过程行自成静默组（组 id 不挂发言）', () => {
    const rows = buildChatRows([speech('abc'), tool('t1')], new Set(['chat-process:abc']));
    expect(rows.map(e => e.id)).toEqual(['abc', 'chat-process:silent-1']);
    expect(rows[1]).toMatchObject({ kind: 'process_group', text: '1 工具', note: '1' });
  });
});

describe('isChatProcessKind / chatProcessSummary', () => {
  it('过程类型折叠、观众与发言不折叠', () => {
    expect(isChatProcessKind('tool')).toBe(true);
    expect(isChatProcessKind('thinking')).toBe(true);
    expect(isChatProcessKind('danmaku')).toBe(false);
    expect(isChatProcessKind('speech')).toBe(false);
    expect(isChatProcessKind('super_chat')).toBe(false);
  });

  it('空过程列表摘要为空串', () => {
    expect(chatProcessSummary([])).toBe('');
  });
});

describe('isRoundLinkable', () => {
  /** 构造最小可用的行（仅用到 kind 与 roundId） */
  function row(kind: ShowEntry['kind'], roundId = ''): ShowEntry {
    return makeEntry({ id: `x-${(seq += 1)}`, kind, tsMs: 1000, text: 't', roundId });
  }

  it('四类带轮次 ID 的行可深链：决策/裁决/工具/发言', () => {
    for (const kind of ['decision', 'verdict', 'tool', 'speech'] as const) {
      expect(isRoundLinkable(row(kind, 'rnd_1_1'))).toBe(true);
    }
  });

  it('无轮次 ID 的同类行不可点', () => {
    for (const kind of ['decision', 'verdict', 'tool', 'speech'] as const) {
      expect(isRoundLinkable(row(kind))).toBe(false);
    }
  });

  it('阶段/游戏/弹幕行不带轮次跳转语义', () => {
    expect(isRoundLinkable(row('stage', 'rnd_1_1'))).toBe(false);
    expect(isRoundLinkable(row('game', 'rnd_1_1'))).toBe(false);
    expect(isRoundLinkable(row('danmaku', 'rnd_1_1'))).toBe(false);
  });
});

describe('递话行（agent.prompted）', () => {
  it('受理事件 → 递话行：来源折叠、内容为主文、目标进 note', () => {
    const entry = toEntry(
      evt('agent.prompted', { target: 'minecraft', content: '先去东侧看看', source: 'operator' }),
    );
    expect(entry).not.toBeNull();
    expect(entry!.kind).toBe('prompt');
    expect(entry!.actor).toBe('运营');
    expect(entry!.text).toBe('先去东侧看看');
    expect(entry!.note).toBe('minecraft');
    expect(entry!.roundId).toBe('');
  });

  it('跨 Agent 递话：来源显示发起 Agent 注册名', () => {
    const entry = toEntry(
      evt('agent.prompted', { target: 'minecraft', content: '注意东侧', source: 'streamer' }),
    );
    expect(entry!.actor).toBe('streamer');
    expect(isChatProcessKind('prompt')).toBe(true);
  });
});

describe('响应卡（agent.replied）', () => {
  it('响应事实 → 决定卡形态条目：正文主文、round/step/请求指针齐全、归游戏组', () => {
    const entry = toEntry(
      evt('agent.replied', {
        agent: 'minecraft',
        content: '目标解释：用切石机把石头加工成石砖',
        round_id: 'mc_abc123456789',
        step: 3,
        model: 'deepseek-flash',
        llm_request_id: 'req_1',
      }),
    );
    expect(entry).not.toBeNull();
    expect(entry!.kind).toBe('agent_reply');
    expect(entry!.text).toBe('目标解释：用切石机把石头加工成石砖');
    expect(entry!.roundId).toBe('mc_abc123456789');
    expect(entry!.llmRequestId).toBe('req_1');
    expect(entry!.source).toBe('游戏 Agent');
    expect(entry!.detail).toMatchObject({ step: 3, model: 'deepseek-flash' });
    expect(agentGroupOf(entry!)).toBe('game');
  });

  it('正文为空回退事件摘要；会话模式折叠为过程行', () => {
    const entry = toEntry(evt('agent.replied', { agent: 'minecraft', content: '' }));
    expect(entry!.kind).toBe('agent_reply');
    expect(entry!.text).not.toBe('');
    expect(isChatProcessKind('agent_reply')).toBe(true);
  });
});

describe('isConversationEntry 简洁档口径', () => {
  it('对话内容与结构行可见：发言/弹幕/礼物/SC/场次边界', () => {
    for (const kind of ['speech', 'danmaku', 'gift', 'super_chat', 'boundary'] as EntryKind[]) {
      expect(isConversationEntry(makeEntry({ id: 'e', kind, tsMs: 1, text: '' }))).toBe(true);
    }
  });

  it('系统事件行两个档位都显示（运营事件不豁免就等于看不到）', () => {
    expect(isConversationEntry(makeEntry({ id: 'e', kind: 'system', tsMs: 1, text: '' }))).toBe(
      true,
    );
  });

  it('管线诊断卡整卡排除：工具/决策/裁决/阶段/思考/游戏上报/里程碑等', () => {
    for (const kind of [
      'tool',
      'decision',
      'verdict',
      'stage',
      'tts',
      'thinking',
      'game',
      'milestone',
    ] as EntryKind[]) {
      expect(isConversationEntry(makeEntry({ id: 'e', kind, tsMs: 1, text: '' }))).toBe(false);
    }
  });
});

describe('core.* 系统事件 → system 行', () => {
  it('core.error：红色失败行，正文取 message，错误源入 note', () => {
    const entry = toEntry(
      evt('core.error', {
        message: 'B 站应用启动失败（code=1）',
        data: { collector: 'bili_danmaku' },
      }),
    );
    expect(entry).not.toBeNull();
    expect(entry!.kind).toBe('system');
    expect(entry!.failed).toBe(true);
    expect(entry!.text).toBe('B 站应用启动失败（code=1）');
    expect(entry!.badge).toBe('系统错误');
  });

  it('core.startup / core.shutdown：中性运营标记行，message 缺省走默认文案', () => {
    const up = toEntry(evt('core.startup', { message: 'Amaidesu 组件装配完成' }));
    expect(up).not.toBeNull();
    expect(up!.kind).toBe('system');
    expect(up!.failed).toBe(false);
    expect(up!.text).toBe('Amaidesu 组件装配完成');
    expect(up!.badge).toBe('启动');

    const down = toEntry(evt('core.shutdown', {}));
    expect(down).not.toBeNull();
    expect(down!.text).toBe('服务已停止');
    expect(down!.badge).toBe('关闭');
  });

  it('system 行不属于会话过程行（会话模式不被折叠）', () => {
    expect(isChatProcessKind('system')).toBe(false);
  });
});

describe('room.message guard → 上舰行', () => {
  it('层级徽标 + 金瓜子换算金额（1000 金瓜子 = 1 元）', () => {
    const entry = toEntry(
      evt('room.message', {
        user: { name: 'Alice' },
        message_type: 'guard',
        content: 'Alice 开通了舰长',
        guard: { guard_level: 3, guard_num: 1, guard_unit: '月', total_price: 138000 },
      }),
    );
    expect(entry).toMatchObject({
      kind: 'guard',
      badge: '上舰·舰长',
      money: '¥138',
      actor: 'Alice',
    });
  });

  it('guard 缺失或层级未知：兜底徽标，正文回退事件摘要', () => {
    const entry = toEntry(evt('room.message', { user: { name: 'Bob' }, message_type: 'guard' }));
    expect(entry).toMatchObject({ kind: 'guard', badge: '上舰' });
    expect(entry?.text).not.toBe('');
  });

  it('上舰是付费行为：进简洁档，与礼物/SC 同级', () => {
    const entry = toEntry(evt('room.message', { user: { name: 'Alice' }, message_type: 'guard' }));
    expect(entry && isConversationEntry(entry)).toBe(true);
  });
});

describe('game.body.* → 身体事件轻量行', () => {
  it('遇袭：徽标 + 摘要正文 + 攻击者/命中数附注，归游戏 Agent 组', () => {
    const entry = toEntry(
      evt('game.body.attacked', {
        game: 'minecraft',
        kind: 'attacked',
        summary: '正在被僵尸攻击',
        attacker: 'minecraft:zombie',
        hits: 3,
      }),
    );
    expect(entry).toMatchObject({
      kind: 'game_body',
      badge: '遇袭',
      text: '正在被僵尸攻击',
      note: '来源 minecraft:zombie · 命中 3 次',
      failed: false,
    });
    expect(entry && agentGroupOf(entry)).toBe('game');
  });

  it('阵亡：红色醒目（failed），attack_ended/respawned 等各配徽标', () => {
    const died = toEntry(
      evt('game.body.died', { game: 'minecraft', kind: 'died', summary: '死了' }),
    );
    expect(died).toMatchObject({ kind: 'game_body', badge: '阵亡', failed: true });

    const ended = toEntry(evt('game.body.attack_ended', { kind: 'attack_ended', summary: '解除' }));
    expect(ended?.badge).toBe('遇袭解除');
    const respawn = toEntry(evt('game.body.respawned', { kind: 'respawned', summary: '重生' }));
    expect(respawn?.badge).toBe('重生');
    const reflex = toEntry(
      evt('game.body.reflex_started', { kind: 'reflex_started', summary: '应激' }),
    );
    expect(reflex?.badge).toBe('应激开始');
    const dim = toEntry(
      evt('game.body.dimension_changed', { kind: 'dimension_changed', summary: '下界' }),
    );
    expect(dim?.badge).toBe('维度切换');
    const need = toEntry(
      evt('game.body.need_unhandled', { kind: 'need_unhandled', summary: '需求' }),
    );
    expect(need?.badge).toBe('需求未处理');
  });

  it('unknown 兜底：原文展示不丢弃', () => {
    const entry = toEntry(
      evt('game.body.unknown', {
        kind: 'unknown',
        summary: '未知遭遇',
        source_event_type: 'weird_event',
      }),
    );
    expect(entry).toMatchObject({ kind: 'game_body', badge: '未知', text: '未知遭遇' });
  });

  it('不进简洁档（过程性动态，观众视角折叠）', () => {
    const entry = toEntry(evt('game.body.attacked', { kind: 'attacked', summary: 'x' }));
    expect(entry && isConversationEntry(entry)).toBe(false);
    expect(entry && isChatProcessKind(entry.kind)).toBe(true);
  });
});

describe('game.chat.received → 游戏内聊天行', () => {
  it('玩家聊天：actor=说话者，私聊带徽标；归游戏 Agent 组', () => {
    const entry = toEntry(
      evt('game.chat.received', {
        game: 'minecraft',
        kind: 'player',
        sender: 'Steve',
        content: '麦麦你在干嘛呀',
        private: true,
      }),
    );
    expect(entry).toMatchObject({
      kind: 'game_chat',
      actor: 'Steve',
      text: '麦麦你在干嘛呀',
      badge: '私聊',
    });
    expect(entry && agentGroupOf(entry)).toBe('game');
    expect(entry && isChatProcessKind(entry.kind)).toBe(false);
  });

  it('系统消息：sender 为空回退"系统消息"，折叠数附注', () => {
    const entry = toEntry(
      evt('game.chat.received', { kind: 'system', content: 'Server restart', suppressed: 2 }),
    );
    expect(entry).toMatchObject({
      kind: 'game_chat',
      actor: '系统消息',
      badge: '系统',
      note: '另有 2 条被折叠',
    });
  });

  it('不进简洁档', () => {
    const entry = toEntry(
      evt('game.chat.received', { kind: 'player', sender: 'A', content: 'hi' }),
    );
    expect(entry && isConversationEntry(entry)).toBe(false);
  });
});

describe('tool.health.* → 健康跃迁系统行', () => {
  it('熔断（open）：warning 样式（failed），带工具名与原因', () => {
    const entry = toEntry(
      evt('tool.health.maicraft_speak', {
        tool_name: 'maicraft_speak',
        provider: 'maicraft',
        state: 'open',
        failure_count: 3,
        last_error: 'MCP server 未连接',
      }),
    );
    expect(entry).toMatchObject({
      kind: 'tool_health',
      text: '工具 maicraft_speak 已熔断摘除',
      badge: '熔断',
      failed: true,
      note: 'MCP server 未连接',
    });
    // 归 room 组（始终可见），不进简洁档
    expect(entry && agentGroupOf(entry)).toBe('room');
    expect(entry && isConversationEntry(entry)).toBe(false);
  });

  it('恢复（closed）：普通系统行', () => {
    const entry = toEntry(
      evt('tool.health.maicraft_speak', {
        tool_name: 'maicraft_speak',
        state: 'closed',
        last_error: '',
      }),
    );
    expect(entry).toMatchObject({
      kind: 'tool_health',
      text: '工具 maicraft_speak 已恢复可用',
      badge: '恢复',
      failed: false,
      note: '',
    });
  });
});

describe('tts.utterance.started/finished → 发言行播报态回填', () => {
  it('按 utterance_id 回填既有发言行，不新增条目', () => {
    const speech = evt(
      'streamer.speech',
      {
        utterance_id: 'utt_1',
        text: '大家好',
        emotion: 'happy',
        emotion_intensity: 0.5,
      },
      1000,
    );
    const started = evt('tts.utterance.started', { utterance_id: 'utt_1' }, 1100);
    const finished = evt(
      'tts.utterance.finished',
      { utterance_id: 'utt_1', duration_ms: 2000 },
      3000,
    );
    const entries = buildLiveEntries([speech, started, finished], new Set());
    expect(entries).toHaveLength(1);
    expect(entries[0].kind).toBe('speech');
    expect(entries[0].ttsState).toBe('done');
  });

  it('started 与 finished 分别给出播报中/已播完', () => {
    const mid = buildLiveEntries(
      [
        evt(
          'streamer.speech',
          { utterance_id: 'u', text: 'x', emotion: 'neutral', emotion_intensity: 0.5 },
          1000,
        ),
        evt('tts.utterance.started', { utterance_id: 'u' }, 1100),
      ],
      new Set(),
    );
    expect(mid[0].ttsState).toBe('speaking');
  });

  it('utterance_id 无匹配发言行时静默丢弃，不产生新行', () => {
    const entries = buildLiveEntries(
      [evt('tts.utterance.started', { utterance_id: 'nope' })],
      new Set(),
    );
    expect(entries).toHaveLength(0);
  });
});
