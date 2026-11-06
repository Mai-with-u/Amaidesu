import { describe, expect, it } from 'vitest';
import type { PlayerItem } from '@/types';
import {
  deletePlayerWarning,
  formerNamesText,
  interactionsText,
  linkedPlayersDeleteNote,
  linkedViewerLabel,
  linkViewerWarning,
  matchesPlayerQuery,
  offlineSavesNote,
  savesText,
} from './playersView';

function player(overrides: Partial<PlayerItem> = {}): PlayerItem {
  return {
    id: 1,
    game: 'minecraft',
    uuid: 'uuid-a',
    name: 'Alex',
    former_names: ['Steve'],
    saves: [],
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

describe('见过他的存档', () => {
  const saves = [
    { save_id: 'aaaaaaaabbbb', save_name: '', online_mode: 'on' as const },
    { save_id: 'w2', save_name: '测试服', online_mode: 'off' as const },
    { save_id: 'w3', save_name: '老家', online_mode: 'unknown' as const },
  ];

  it('列出存档，没名字的用编号前 8 位；没记下存档为空', () => {
    expect(savesText(saves)).toBe('见过他的存档：编号 aaaaaaaa、测试服、老家');
    expect(savesText([])).toBe('');
  });

  it('只点名离线模式的存档，写清认人只认得名字；一个都没有为空', () => {
    expect(offlineSavesNote(saves)).toBe('「测试服」离线模式：按名字认人，改名算新人，冒名认不出');
    expect(offlineSavesNote(saves.filter(save => save.online_mode !== 'off'))).toBe('');
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

describe('确认关联与删观众的文案', () => {
  it('确认关联点名玩家与观众昵称，昵称读不到只写编号', () => {
    expect(linkViewerWarning(player(), { viewer_id: 12, user_name: '阿杰' })).toContain(
      '确认玩家「Alex」（minecraft）就是观众「阿杰」（#12）？',
    );
    expect(linkViewerWarning(player(), { viewer_id: 12, user_name: '' })).toContain(
      '就是观众 #12？',
    );
  });

  it('删观众时点名一并删掉的玩家，会不会再记跟着不记录名单的勾选走', () => {
    const linked = [{ player_id: 3, game: 'minecraft', name: 'Alex' }];
    expect(linkedPlayersDeleteNote([], true)).toBe('没有在记忆页确认过关联的游戏玩家。');
    expect(linkedPlayersDeleteNote(linked, true)).toContain('「Alex」（minecraft）一并删除');
    expect(linkedPlayersDeleteNote(linked, true)).toContain('不再记录');
    expect(linkedPlayersDeleteNote(linked, false)).toContain('会重新记录');
  });
});
