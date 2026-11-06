// memoryCues 的测试：线索卡的卡类与来源叫法、一行分数说明、条目是否已删、来源筛选

import { describe, expect, it } from 'vitest';

import type { ReviewCueItem } from '@/types';
import {
  cueItemGone,
  cueKindLabel,
  cueScoreText,
  cueSourceLabel,
  cueSourceOptions,
  filterCuesBySource,
} from './memoryCues';

function cue(overrides: Partial<ReviewCueItem> = {}): ReviewCueItem {
  return {
    id: 1,
    pushed_at_ms: 1_700_000_000_000,
    card_kind: 'rule',
    label: '档#41',
    title: '女仆取名团子',
    archive_line_id: 41,
    commitment_id: null,
    sources: ['danmaku'],
    detail: {},
    self_triggered: false,
    used_at_ms: null,
    ...overrides,
  };
}

describe('memoryCues', () => {
  it('卡类与来源给中文叫法，没登记的原样显示', () => {
    expect(cueKindLabel('vector')).toBe('意思相近');
    expect(cueKindLabel('place')).toBe('走到地点');
    expect(cueSourceLabel('self')).toBe('她的台词');
    expect(cueSourceLabel('new_source')).toBe('new_source');
  });

  it.each([
    [{ keywords: ['团子', '芝麻'], how: ['exact'] }, '命中「团子」「芝麻」'],
    [{ cosine: 0.7123, rank: 2, fused: 0.03 }, '余弦 0.71，第 2 名'],
    [{ place: '北边农场', distance: 12 }, '「北边农场」 12 格'],
    [{}, ''],
  ])('分数说明 %o → %s', (detail, expected) => {
    expect(cueScoreText(cue({ detail }))).toBe(expected);
  });

  it('档案行与承诺卡标题为空即条目已删；老梗卡没有条目，不算删', () => {
    expect(cueItemGone(cue({ title: '' }))).toBe(true);
    expect(cueItemGone(cue())).toBe(false);
    expect(cueItemGone(cue({ archive_line_id: null, title: '', label: '老梗「倒立洗头」' }))).toBe(
      false,
    );
  });

  it('筛选项只列本场出现过的来源，按固定先后；空串筛选 = 全部', () => {
    const items = [
      cue({ id: 1, sources: ['place'] }),
      cue({ id: 2, sources: ['danmaku', 'self'] }),
      cue({ id: 3, sources: ['self'] }),
    ];
    expect(cueSourceOptions(items)).toEqual(['danmaku', 'self', 'place']);
    expect(filterCuesBySource(items, 'self').map(item => item.id)).toEqual([2, 3]);
    expect(filterCuesBySource(items, '').map(item => item.id)).toEqual([1, 2, 3]);
  });
});
