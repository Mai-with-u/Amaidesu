/**
 * 复盘页「本场线索卡」的展示文案（纯函数）
 *
 * 线索卡是代码每批推给决策层的旧事：对象开口、游戏开着两种承诺卡，加被动想起的
 * 走到地点、触发词、意思相近三路与老梗。这里把卡类、命中来源翻成运营者看得懂的
 * 叫法，并把分数明细压成一行说明；来源筛选也在这里做。
 */
import type { ReviewCueItem } from '@/types';

const KIND_LABELS: Record<string, string> = {
  viewer: '对象开口',
  game: '游戏开着',
  place: '走到地点',
  rule: '触发词',
  vector: '意思相近',
  joke: '老梗',
};

/** 来源的叫法；键的先后就是筛选项的先后 */
const SOURCE_LABELS: Record<string, string> = {
  danmaku: '观众弹幕',
  self: '她的台词',
  place: '游戏地点',
  situation: '游戏情境',
  game: '游戏开着',
};

export function cueKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

export function cueSourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source;
}

/** 一行分数说明：触发词写命中的词，意思相近写余弦与名次，地点写地点名与距离；没有明细为空串 */
export function cueScoreText(item: ReviewCueItem): string {
  const detail = item.detail ?? {};
  const keywords = Array.isArray(detail.keywords) ? detail.keywords.map(String) : [];
  if (keywords.length > 0) return `命中「${keywords.join('」「')}」`;
  if (typeof detail.cosine === 'number') {
    const rank = typeof detail.rank === 'number' ? `，第 ${detail.rank} 名` : '';
    return `余弦 ${detail.cosine.toFixed(2)}${rank}`;
  }
  if (typeof detail.place === 'string' && detail.place) {
    const distance = typeof detail.distance === 'number' ? ` ${detail.distance} 格` : '';
    return `「${detail.place}」${distance}`;
  }
  return '';
}

/** 指向的条目还在不在：档案行与承诺卡标题为空即条目已被删掉；老梗卡没有条目 */
export function cueItemGone(item: ReviewCueItem): boolean {
  return (item.archive_line_id !== null || item.commitment_id !== null) && !item.title;
}

/** 本场出现过的来源（筛选项），按固定先后 */
export function cueSourceOptions(items: ReviewCueItem[]): string[] {
  const present = new Set(items.flatMap(item => item.sources));
  return Object.keys(SOURCE_LABELS).filter(source => present.has(source));
}

/** 按来源筛：空串 = 全部 */
export function filterCuesBySource(items: ReviewCueItem[], source: string): ReviewCueItem[] {
  return source ? items.filter(item => item.sources.includes(source)) : items;
}
