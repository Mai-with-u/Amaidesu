import { describe, expect, it } from 'vitest';
import {
  ARCHIVE_TITLE_MAX_LENGTH,
  CLOSE_QUOTE_MAX_LENGTH,
  archiveLineTags,
  commitmentQuoteState,
  commitmentStatusLabel,
  commitmentStatusQuery,
  commitmentStatusTagType,
  commitmentTargetLabel,
  gameSaveLabel,
  hasLaterSettledSession,
  pinnedOverflowHint,
  quoteFitsSpeech,
  reviewStateLabel,
  settleAttemptsExhausted,
  settleAttemptsText,
  settleStateLabel,
  snapshotChangeLabel,
  unregisteredKindLabel,
  validateArchiveTitle,
  validateCloseQuote,
} from './ledgerView';

describe('承诺状态展示', () => {
  it('三种状态各有中文标签与对应颜色档', () => {
    expect(commitmentStatusLabel('open')).toBe('未了结');
    expect(commitmentStatusLabel('closed')).toBe('已了结');
    expect(commitmentStatusLabel('void')).toBe('误记');
    expect(commitmentStatusTagType('open')).toBe('warning');
    expect(commitmentStatusTagType('closed')).toBe('success');
    expect(commitmentStatusTagType('void')).toBe('info');
  });

  it('筛选值换算：全部不发 status 参数，其余原样透传', () => {
    expect(commitmentStatusQuery('all')).toBeUndefined();
    expect(commitmentStatusQuery('open')).toBe('open');
    expect(commitmentStatusQuery('closed')).toBe('closed');
    expect(commitmentStatusQuery('void')).toBe('void');
  });
});

describe('承诺对谁', () => {
  it('有观众昵称用昵称，读不到昵称用内部 ID 兜底', () => {
    expect(commitmentTargetLabel({ viewer_id: 7, viewer_name: '阿澈' })).toBe('阿澈');
    expect(commitmentTargetLabel({ viewer_id: 7, viewer_name: null })).toBe('观众 #7');
  });

  it('没有具体对象时显示“对全场”', () => {
    expect(commitmentTargetLabel({ viewer_id: null, viewer_name: null })).toBe('对全场');
  });
});

describe('承诺原话状态', () => {
  it('有原话就是 quoted', () => {
    expect(commitmentQuoteState({ quote: '我下周唱', created_session_id: 3 })).toBe('quoted');
    expect(commitmentQuoteState({ quote: '手写的原话', created_session_id: null })).toBe('quoted');
  });

  it('手工登记的承诺没有原话是 manual', () => {
    expect(commitmentQuoteState({ quote: '', created_session_id: null })).toBe('manual');
  });

  it('场次登记的承诺没原话只可能是台词被删，显示“原句已删除”', () => {
    expect(commitmentQuoteState({ quote: '', created_session_id: 3 })).toBe('deleted');
  });
});

describe('了结原话校验', () => {
  it('上限 200 字，边界内通过、超长给提示', () => {
    expect(CLOSE_QUOTE_MAX_LENGTH).toBe(200);
    expect(validateCloseQuote('')).toBeNull();
    expect(validateCloseQuote('好'.repeat(200))).toBeNull();
    expect(validateCloseQuote('好'.repeat(201))).toBe('最多 200 字');
  });
});

describe('整理状态展示', () => {
  it('已整理 / 待整理', () => {
    expect(settleStateLabel(null)).toBe('待整理');
    expect(settleStateLabel(1_700_000_000_000)).toBe('已整理');
  });

  it('自动重试进度与用满判定', () => {
    expect(settleAttemptsText(1, 3)).toBe('重试 1/3');
    expect(settleAttemptsText(2, 0)).toBe('重试 2');
    expect(settleAttemptsExhausted(2, 3)).toBe(false);
    expect(settleAttemptsExhausted(3, 3)).toBe(true);
    expect(settleAttemptsExhausted(3, 0)).toBe(false);
  });

  it('未登记条目按类型区分承诺与档案', () => {
    expect(unregisteredKindLabel('commitment')).toBe('承诺');
    expect(unregisteredKindLabel('archive')).toBe('档案');
  });

  it('之后已有整理好的正式场时才算"较早的一场"', () => {
    const items = [
      { live_session_id: 3, started_at_ms: 3_000, settled_at_ms: null },
      { live_session_id: 2, started_at_ms: 2_000, settled_at_ms: 2_500 },
      { live_session_id: 1, started_at_ms: 1_000, settled_at_ms: 1_500 },
    ];
    expect(hasLaterSettledSession(items[2], items)).toBe(true);
    expect(hasLaterSettledSession(items[1], items)).toBe(false);
    expect(hasLaterSettledSession(items[0], items)).toBe(false);
  });

  it('档案快照改动按操作给中文标签', () => {
    expect(snapshotChangeLabel('drop')).toBe('去掉');
    expect(snapshotChangeLabel('replace')).toBe('替换');
    expect(snapshotChangeLabel('evicted')).toBe('挤出');
    expect(snapshotChangeLabel('refused')).toBe('没采纳');
  });
});

describe('游戏与存档标签', () => {
  it('有名字写名字、没名字写编号前 8 位、没记存档只写游戏、不挂游戏不出标签', () => {
    expect(gameSaveLabel({ game: 'minecraft', save_id: 'b', save_name: '新档' })).toBe(
      'minecraft「新档」',
    );
    expect(gameSaveLabel({ game: 'minecraft', save_id: '0123456789abcdef', save_name: '' })).toBe(
      'minecraft「编号 01234567」',
    );
    expect(gameSaveLabel({ game: 'minecraft', save_id: '', save_name: '' })).toBe('minecraft');
    expect(gameSaveLabel({ game: null, save_id: '', save_name: '' })).toBeNull();
  });
});

describe('记忆页审核', () => {
  it('档案行标签：在不在快照里、稳定的事只在快照里才标、人放回与人改过各一个', () => {
    const base = { in_snapshot: false, lasting: false, pinned: false, human_locked: false };
    expect(archiveLineTags(base).map(tag => tag.label)).toEqual(['不在快照里']);
    expect(archiveLineTags({ ...base, lasting: true }).map(tag => tag.label)).toEqual([
      '不在快照里',
    ]);
    expect(
      archiveLineTags({ in_snapshot: true, lasting: true, pinned: true, human_locked: true }).map(
        tag => tag.label,
      ),
    ).toEqual(['下一场记得', '稳定的事', '人放回的', '人改过']);
  });

  it('放回、新增的行超过常驻简报的 12 行才提示', () => {
    expect(pinnedOverflowHint(12)).toBeNull();
    expect(pinnedOverflowHint(13)).toContain('13 行');
  });

  it('原话要是那句台词的逐字片段，连续空白压成一个空格的写法也认', () => {
    expect(quoteFitsSpeech('就叫 团子', '以后这只猫就叫  团子了')).toBe(true);
    expect(quoteFitsSpeech('这只猫', '以后这只猫就叫团子了')).toBe(true);
    expect(quoteFitsSpeech('这只狗', '以后这只猫就叫团子了')).toBe(false);
    expect(quoteFitsSpeech('  ', '以后这只猫就叫团子了')).toBe(false);
  });

  it('标题不能为空、不能超过上限；审核状态分待审与已审', () => {
    expect(validateArchiveTitle(' 狼叫阿黄 ')).toBeNull();
    expect(validateArchiveTitle('   ')).toBe('标题不能为空');
    expect(validateArchiveTitle('长'.repeat(ARCHIVE_TITLE_MAX_LENGTH + 1))).toContain('最多');
    expect(reviewStateLabel(null)).toBe('待审');
    expect(reviewStateLabel(5)).toBe('已审');
  });
});
