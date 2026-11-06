// 直播间公告文本拼装的测试
//
// 覆盖：保留期默认 0 写"长期保存"、设了天数写"最多 N 天"；缺席观众清理只在
// 设了天数时附注；联系方式留空时给补填提示；从合并视图读保留期时缺键/坏值按 0。

import { describe, expect, it } from 'vitest';

import { composeAnnouncement, readRetention } from './announcement';

describe('readRetention', () => {
  it('从合并视图的 retention 段读两个天数', () => {
    expect(readRetention({ retention: { raw_days: 7, viewer_absence_days: 30 } })).toEqual({
      rawDays: 7,
      viewerAbsenceDays: 30,
    });
  });

  it('缺段、缺键或非正整数按 0（不自动删除）处理', () => {
    expect(readRetention(undefined)).toEqual({ rawDays: 0, viewerAbsenceDays: 0 });
    expect(readRetention({})).toEqual({ rawDays: 0, viewerAbsenceDays: 0 });
    expect(readRetention({ retention: { raw_days: -1, viewer_absence_days: 'x' } })).toEqual({
      rawDays: 0,
      viewerAbsenceDays: 0,
    });
  });
});

describe('composeAnnouncement', () => {
  it('默认保留期写长期保存，并提示残留可联系运营者', () => {
    const text = composeAnnouncement({ rawDays: 0, viewerAbsenceDays: 0, contact: '' });
    expect(text).toContain('本主播是 AI');
    expect(text).toContain('为互动会保存弹幕（长期保存）');
    expect(text).toContain('备份与日志可能仍有残留，可联系运营者处理');
    expect(text).toContain('联系方式：（由运营者补填）');
    expect(text).not.toContain('最多');
  });

  it('设了保留期时写具体天数，观众行附注缺席清理', () => {
    const text = composeAnnouncement({
      rawDays: 7,
      viewerAbsenceDays: 30,
      contact: 'QQ 群 123456',
    });
    expect(text).toContain('为互动会保存弹幕（最多 7 天）');
    expect(text).toContain('数据库备份里的残留最多保留 7 天');
    // 保留期不管运行日志：日志残留不承诺天数
    expect(text).toContain('运行日志里可能仍有残留，可联系运营者处理');
    expect(text).toContain('（保留到最后一次来访后 30 天）');
    expect(text).toContain('联系方式：QQ 群 123456');
  });

  it('保留期未设时观众行不附注、保留删除与撤回说明', () => {
    const text = composeAnnouncement({ rawDays: 0, viewerAbsenceDays: 0, contact: 'x' });
    expect(text).not.toContain('保留到最后一次来访后');
    expect(text).toContain('发“忘了我”会立即停止记录你');
    expect(text).toContain('删除前发“开玩笑的”可以撤回');
  });
});
