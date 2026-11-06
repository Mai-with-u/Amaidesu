/**
 * 记忆页"游戏里的人"的展示逻辑（互动条数、曾用名、关联观众、搜索）
 *
 * 组件模板只消费这里的结果，行为由单元测试直接断言。
 */

import type { PlayerInteractionKind, PlayerItem, PlayerLinkedViewer } from '@/types';

/** 互动种类的展示顺序与说法：打招呼按场数计（一场只记一次），其余按条数计 */
const INTERACTION_TEXT: ReadonlyArray<readonly [PlayerInteractionKind, (n: number) => string]> = [
  ['greet', n => `打招呼 ${n} 场`],
  ['gift', n => `送东西 ${n} 次`],
  ['spar', n => `切磋 ${n} 次`],
  ['together', n => `一起做事 ${n} 次`],
  ['helped', n => `帮过忙 ${n} 次`],
];

/**
 * 互动条数一行字："打招呼 3 场 · 送东西 1 次"。
 * 按固定顺序排，条数为 0 或没出现的种类不写；一条都没有时说明还没有互动记录（只在公屏说过话也算这种）。
 */
export function interactionsText(counts: PlayerItem['interactions']): string {
  const parts = INTERACTION_TEXT.filter(([kind]) => (counts[kind] ?? 0) > 0).map(([kind, text]) =>
    text(counts[kind] ?? 0),
  );
  return parts.length > 0 ? parts.join(' · ') : '还没有互动记录';
}

/** 曾用名："以前叫 Steve、Notch"；没改过名为空串（模板据此不渲染） */
export function formerNamesText(names: readonly string[]): string {
  return names.length > 0 ? `以前叫 ${names.join('、')}` : '';
}

/** 关联观众的标签：有昵称写"昵称（#编号）"，昵称读不到只写编号 */
export function linkedViewerLabel(viewer: PlayerLinkedViewer): string {
  return viewer.user_name ? `${viewer.user_name}（#${viewer.viewer_id}）` : `#${viewer.viewer_id}`;
}

/**
 * 按名字找玩家：现名或任一曾用名包含关键字就算（不分大小写）。
 * 运营者想找的常常是他以前的名字（观众说"那个叫 Steve 的"），所以曾用名也要能搜到；关键字为空全部保留。
 */
export function matchesPlayerQuery(item: PlayerItem, query: string): boolean {
  const keyword = query.trim().toLowerCase();
  if (!keyword) return true;
  return [item.name, ...item.former_names].some(name => name.toLowerCase().includes(keyword));
}

/** 删玩家前的确认文案：删什么、换成什么、关联的观众不动 */
export function deletePlayerWarning(item: PlayerItem): string {
  return (
    `删除玩家「${item.name}」？他的互动、公屏聊天和对他的承诺一起删除，` +
    '她的台词、档案和游戏记录里他的名字（含以前的名字）换成“一位玩家”；' +
    'UUID 进不记录名单，之后他再出现也不再记。关联的观众不动。此操作不可恢复。'
  );
}
