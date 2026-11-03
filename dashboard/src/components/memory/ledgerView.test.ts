import { describe, expect, it } from 'vitest';
import {
  commitmentStatusLabel,
  commitmentStatusQuery,
  commitmentStatusTagType,
  commitmentTargetLabel,
  settleAttemptsExhausted,
  settleAttemptsText,
  settleStateLabel,
  unregisteredKindLabel,
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
});
