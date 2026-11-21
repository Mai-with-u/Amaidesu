// liveSearch.ts：时间线内容检索的匹配、命中片段与结果行构造。
//
// 匹配是大小写不敏感的子串匹配（中文不分词）；命中行有两个来源——本地条目
// （带条目引用，跳转按条目 id）与服务端命中（只有时刻，跳转按时刻近邻），
// 测试把两条路的形状与匹配面（正文 / 备注 / 说话人 / 载荷）都钉住。

import { describe, expect, it } from 'vitest';

import {
  countByCategory,
  entryCategoryOf,
  entryMatches,
  entrySnippet,
  eventItemCategoryOf,
  eventTagLabel,
  hitFromEntry,
  hitFromItem,
  hitFromRecord,
  hitSnippet,
  matchEntries,
  recordHitMessage,
  recordMatches,
} from '@/utils/liveSearch';
import { makeEntry, type ShowEntry } from '@/utils/liveFeed';
import { buildRequestRecords } from '@/utils/requestRecords';

function entryOf(patch: Partial<ShowEntry> & { id: string; text: string }): ShowEntry {
  return makeEntry({
    kind: 'danmaku',
    tsMs: 1_000,
    actor: '路人甲',
    ...patch,
  } as Parameters<typeof makeEntry>[0]);
}

describe('entryMatches / matchEntries', () => {
  it('正文、备注、说话人参与匹配，大小写不敏感', () => {
    expect(entryMatches(entryOf({ id: 'a', text: '别破防了' }), '破防')).toBe(true);
    expect(entryMatches(entryOf({ id: 'b', text: 'say GG', actor: 'ViewerX' }), 'viewerx')).toBe(
      true,
    );
    expect(entryMatches(entryOf({ id: 'c', text: '正文无关', note: '备注里有线索' }), '线索')).toBe(
      true,
    );
    expect(entryMatches(entryOf({ id: 'd', text: '无关' }), '破防')).toBe(false);
  });

  it('空关键词与空白关键词都不命中', () => {
    expect(entryMatches(entryOf({ id: 'a', text: '任意' }), '')).toBe(false);
    expect(entryMatches(entryOf({ id: 'a', text: '任意' }), '   ')).toBe(false);
  });

  it('matchEntries 保持原顺序，只留命中的', () => {
    const entries = [
      entryOf({ id: 'a', text: '第一条破防' }),
      entryOf({ id: 'b', text: '无关' }),
      entryOf({ id: 'c', text: '破防二次' }),
    ];
    expect(matchEntries(entries, '破防').map(entry => entry.id)).toEqual(['a', 'c']);
    expect(matchEntries(entries, '')).toEqual([]);
  });
});

describe('hitSnippet', () => {
  it('关键词在开头：不丢前文也不多出省略号', () => {
    const parts = hitSnippet('破防了兄弟们', '破防');
    expect(parts[0]).toEqual({ text: '破防', hit: true });
    expect(parts[1]).toEqual({ text: '了兄弟们', hit: false });
    expect(parts.some(part => part.text.includes('…'))).toBe(false);
  });

  it('关键词在中间：两侧截断带省略号，关键词段命中', () => {
    // 前后各垫 20 字，保证关键词两侧都超出上下文宽度（14 字），截断必然发生
    const text = `${'甲'.repeat(20)}破防${'乙'.repeat(20)}`;
    const parts = hitSnippet(text, '破防');
    expect(parts.filter(part => part.hit).map(part => part.text)).toEqual(['破防']);
    expect(parts[0].text.startsWith('…')).toBe(true);
    expect(parts[parts.length - 1].text.endsWith('…')).toBe(true);
    // 截断只发生在关键词附近，不把整句都带出来
    const joined = parts.map(part => part.text).join('');
    expect(joined.length).toBeLessThan(text.length);
  });

  it('未命中时原样返回，不标记', () => {
    const parts = hitSnippet('完全无关', '破防');
    expect(parts).toEqual([{ text: '完全无关', hit: false }]);
  });
});

describe('entrySnippet / hitFromEntry', () => {
  it('命中片段正文优先，其次备注与说话人', () => {
    const hit = entryOf({ id: 'a', text: '正文里出现破防一词', note: '', actor: '破防君' });
    expect(entrySnippet(hit, '破防').some(part => part.text.includes('正文'))).toBe(true);

    const noteHit = entryOf({ id: 'b', text: '正文无关', note: '备注里有破防' });
    expect(entrySnippet(noteHit, '破防').some(part => part.text.includes('备注'))).toBe(true);
  });

  it('结果行带类型标签与说话人；badge 优先于种类短名', () => {
    const badged = hitFromEntry(entryOf({ id: 'a', text: '破防', badge: '礼物' }), '破防');
    expect(badged.tag).toBe('礼物');
    expect(badged.entry?.id).toBe('a');

    const plain = hitFromEntry(entryOf({ id: 'b', text: '破防' }), '破防');
    expect(plain.tag).toBe('弹幕');
  });
});

describe('hitFromItem（服务端命中 → 结果行）', () => {
  it('弹幕按正文命中；按昵称命中时片段取昵称', () => {
    const row = { kind: 'danmaku', ts_ms: 5_000, user_name: '观众A', content: '今天天气不错' };
    const byText = hitFromItem(row, '天气');
    expect(byText?.actor).toBe('观众A');
    expect(byText?.parts.some(part => part.text.includes('天气'))).toBe(true);

    const byName = hitFromItem(row, '观众A');
    expect(byName?.parts.some(part => part.text.includes('观众A'))).toBe(true);
  });

  it('gift_row 映射回礼物标签；speech 按 text 命中', () => {
    const gift = hitFromItem(
      { kind: 'gift_row', ts_ms: 6_000, user_name: '土豪B', gift_name: '小花花' },
      '小花花',
    );
    expect(gift?.tag).toBe('礼物');

    const speech = hitFromItem({ kind: 'speech', ts_ms: 7_000, text: '我们开始吧' }, '开始');
    expect(speech?.tag).toBe('发言');
  });

  it('事件条目按载荷内容命中，字段路径做主体；嵌套与数组都翻', () => {
    const nested = hitFromItem(
      {
        kind: 'event',
        ts_ms: 8_000,
        event_type: 'tool.result',
        data: { args: { instruction: '去挖点破防的矿' } },
      },
      '破防',
    );
    expect(nested?.actor).toBe('args.instruction');

    const inArray = hitFromItem(
      {
        kind: 'event',
        ts_ms: 9_000,
        event_type: 'planner.decision',
        data: { batch: [{ text: '弹幕里提到下播' }] },
      },
      '下播',
    );
    expect(inArray).not.toBeNull();
  });

  it('都不命中时返回 null', () => {
    expect(
      hitFromItem({ kind: 'danmaku', ts_ms: 1, user_name: 'x', content: '无关' }, '破防'),
    ).toBeNull();
    expect(hitFromItem({ kind: 'event', ts_ms: 1, event_type: 't', data: {} }, '破防')).toBeNull();
  });
});

describe('entryCategoryOf（本地条目 → 六桶，走 hitFromEntry 的 category 字段）', () => {
  it.each([
    ['danmaku', 'audience'],
    ['super_chat', 'audience'],
    ['gift', 'audience'],
    ['guard', 'audience'],
    ['enter', 'audience'],
    ['speech', 'speech'],
    ['decision', 'agent'],
    ['verdict', 'agent'],
    ['thinking', 'agent'],
    ['prompt', 'agent'],
    ['game', 'game'],
    ['game_body', 'game'],
    ['others_things', 'game'],
    ['milestone', 'game'],
    ['agent_reply', 'game'],
    ['delegate_receipt', 'game'],
    ['rundown', 'system'],
    ['lead_change', 'lead'],
    ['lead_suggestion', 'lead'],
    ['lead_commentary', 'lead'],
    ['stage', 'system'],
    ['tts', 'system'],
    ['tool_health', 'system'],
    ['system', 'system'],
  ] as const)('kind=%s → %s', (kind, expected) => {
    const hit = hitFromEntry(entryOf({ id: 'x', text: 't', kind }), 't');
    expect(hit.category).toBe(expected);
  });

  it('工具按调用来源分侧：游戏 Agent 来源进游戏桶，其余进决策调用（与双轨同一判据）', () => {
    expect(
      hitFromEntry(entryOf({ id: 'g', text: 't', kind: 'tool', source: '游戏 Agent' }), 't')
        .category,
    ).toBe('game');
    expect(
      hitFromEntry(entryOf({ id: 's', text: 't', kind: 'tool', source: '主播' }), 't').category,
    ).toBe('agent');
  });

  it('entryCategoryOf 与 hitFromEntry 的分类一致（同一判据两个入口）', () => {
    const entry = entryOf({ id: 'p', text: 't', kind: 'prompt' });
    expect(entryCategoryOf(entry)).toBe('agent');
  });
});

describe('eventItemCategoryOf / eventTagLabel（服务端事件 → 分类与中文标签）', () => {
  it.each([
    ['room.message', {}, 'audience'],
    ['streamer.speech', {}, 'speech'],
    ['planner.decision', {}, 'agent'],
    ['planner.verdict', {}, 'agent'],
    ['agent.prompted', {}, 'agent'],
    ['agent.replied', {}, 'game'],
    ['agent.lead.changed', {}, 'lead'],
    ['agent.lead.suggestion', {}, 'lead'],
    ['agent.lead.commentary', {}, 'lead'],
    ['game.body.hit', {}, 'game'],
    ['task.changed', {}, 'game'],
    ['tool.result.minecraft_glance', { tool_name: 'minecraft_glance' }, 'game'],
    ['tool.result.maicraft_execute', { tool_name: 'maicraft_execute' }, 'game'],
    ['tool.result.streamer_reply', { tool_name: 'streamer_reply' }, 'agent'],
    ['streamer.stage', {}, 'system'],
    ['tts.utterance.started', {}, 'system'],
    ['live.started', {}, 'system'],
    ['core.error', {}, 'system'],
    ['background.task.changed', {}, 'system'],
    ['unknown.event.name', {}, 'system'],
  ] as const)('%s → %s', (eventType, data, expected) => {
    expect(eventItemCategoryOf(eventType, data as Record<string, unknown>)).toBe(expected);
  });

  it('事件标签能细则细，认不出回落到桶名，不出现英文事件名原文', () => {
    expect(eventTagLabel('planner.verdict', 'agent')).toBe('裁决');
    expect(eventTagLabel('tool.result.maicraft_observe', 'game')).toBe('工具');
    expect(eventTagLabel('tts.utterance.started', 'system')).toBe('播报');
    expect(eventTagLabel('agent.lead.changed', 'lead')).toBe('主控');
    expect(eventTagLabel('agent.lead.suggestion', 'lead')).toBe('建议');
    expect(eventTagLabel('agent.lead.commentary', 'lead')).toBe('主控解说');
    expect(eventTagLabel('brand.new.event', 'system')).toBe('系统');
  });
});

describe('countByCategory（过滤 chips 的计数）', () => {
  it('按固定桶序只出非零桶，计数是全量口径', () => {
    const hits = [
      hitFromEntry(entryOf({ id: '1', text: 't', kind: 'danmaku' }), 't'),
      hitFromEntry(entryOf({ id: '2', text: 't', kind: 'speech', speak: true }), 't'),
      hitFromEntry(entryOf({ id: '3', text: 't', kind: 'speech', speak: true }), 't'),
      hitFromEntry(entryOf({ id: '4', text: 't', kind: 'system' }), 't'),
    ];
    expect(countByCategory(hits)).toEqual([
      { key: 'audience', label: '观众', count: 1 },
      { key: 'speech', label: '发言', count: 2 },
      { key: 'system', label: '系统', count: 1 },
    ]);
    expect(countByCategory([])).toEqual([]);
  });
});

describe('recordMatches / hitFromRecord（决策轨迹档）', () => {
  const records = buildRequestRecords([
    makeEntry({
      id: 's1',
      kind: 'speech',
      tsMs: 10_000,
      actor: '主播',
      text: '今天状态不好，别破防了',
      speak: true,
    }),
  ]);

  it('请求记录的消息文本命中即整条记录命中，片段取自命中消息', () => {
    expect(records).toHaveLength(1);
    const record = records[0];
    expect(recordMatches(record, '破防')).toBe(true);
    expect(recordMatches(record, '不存在的词')).toBe(false);

    const hit = hitFromRecord(record, '破防');
    expect(hit?.record).toBe(record);
    expect(hit?.message?.text).toContain('破防');
    expect(recordHitMessage(record, '状态')?.text).toContain('状态');
  });
});
