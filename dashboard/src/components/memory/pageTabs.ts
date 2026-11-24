/**
 * 记忆账本页的页内 tab：键表、显示名与深链解析
 *
 * tab 键写进 route query 的 `tab` 参数（?tab=archive 直达场次档案）；
 * 解析对未知值一律回退默认 tab，保证外链拼错也不落空白页。
 */

export const MEMORY_TAB_KEYS = [
  'commitment',
  'candidates',
  'settle',
  'archive',
  'players',
  'player-candidates',
  'announcement',
] as const;

export type MemoryTabKey = (typeof MEMORY_TAB_KEYS)[number];

export const DEFAULT_MEMORY_TAB: MemoryTabKey = 'commitment';

export const MEMORY_TAB_LABELS: Record<MemoryTabKey, string> = {
  commitment: '承诺',
  candidates: '观众候选',
  settle: '整理',
  archive: '档案',
  players: '玩家',
  'player-candidates': '自述',
  announcement: '公告',
};

/** route query 的 tab 值 → 合法 tab 键；缺省或拼错回退默认 tab */
export function resolveMemoryTab(raw: unknown): MemoryTabKey {
  const value = String(raw ?? '');
  return (MEMORY_TAB_KEYS as readonly string[]).includes(value)
    ? (value as MemoryTabKey)
    : DEFAULT_MEMORY_TAB;
}
