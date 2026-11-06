import { describe, expect, it } from 'vitest';
import type { PlayerItem } from '@/types';
import {
  deletePlayerWarning,
  formerNamesText,
  interactionsText,
  linkedViewerLabel,
  matchesPlayerQuery,
} from './playersView';

function player(overrides: Partial<PlayerItem> = {}): PlayerItem {
  return {
    id: 1,
    game: 'minecraft',
    uuid: 'uuid-a',
    name: 'Alex',
    former_names: ['Steve'],
    first_seen_ms: 1_000,
    last_seen_ms: 2_000,
    interactions: {},
    linked_viewers: [],
    forget_requested_at_ms: null,
    ...overrides,
  };
}

describe('互动条数', () => {
  it('按固定顺序写，打招呼按场数，0 条的种类不写', () => {
    expect(interactionsText({ spar: 2, greet: 3, gift: 0, helped: 1 })).toBe(
      '打招呼 3 场 · 切磋 2 次 · 帮过忙 1 次',
    );
  });

  it('一条都没有时说明还没有互动记录', () => {
    expect(interactionsText({})).toBe('还没有互动记录');
  });
});

describe('名字与关联观众', () => {
  it('曾用名用顿号连起来，没改过名为空', () => {
    expect(formerNamesText(['Steve', 'Notch'])).toBe('以前叫 Steve、Notch');
    expect(formerNamesText([])).toBe('');
  });

  it('关联观众有昵称写昵称和编号，读不到昵称只写编号', () => {
    expect(linkedViewerLabel({ viewer_id: 7, user_name: '阿杰', confirmed_at_ms: 1 })).toBe(
      '阿杰（#7）',
    );
    expect(linkedViewerLabel({ viewer_id: 7, user_name: '', confirmed_at_ms: 1 })).toBe('#7');
  });

  it('按现名或曾用名搜，不分大小写；关键字为空全留', () => {
    const alex = player();
    expect(matchesPlayerQuery(alex, 'steve')).toBe(true);
    expect(matchesPlayerQuery(alex, ' ALE ')).toBe(true);
    expect(matchesPlayerQuery(alex, 'bob')).toBe(false);
    expect(matchesPlayerQuery(alex, '  ')).toBe(true);
  });
});

describe('删玩家确认', () => {
  it('说清删什么、名字换成一位玩家、进不记录名单、关联观众不动', () => {
    const text = deletePlayerWarning(player());
    expect(text).toContain('「Alex」');
    expect(text).toContain('一位玩家');
    expect(text).toContain('不记录名单');
    expect(text).toContain('关联的观众不动');
  });
});
