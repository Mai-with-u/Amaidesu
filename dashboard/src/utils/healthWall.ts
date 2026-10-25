// 连接健康墙的呈现判定纯逻辑
//
// HealthWall 组件只负责取数与渲染，判定规则集中在此处共享可测：
//   - 域分组排序与命名（summarizeDomains）
//   - 整域最差状态（worstHealthState）：offline > degraded > unknown > ok
//   - 重连按钮可见性（isReconnectable）：仅后端声明支持手动重连的条目
//
// 纯函数、无时钟与 DOM 依赖，node 环境可直接结构断言。

import type { SystemHealthItem, SystemHealthWallResponse } from '@/types';

export type HealthState = SystemHealthItem['state'];

/** 域展示顺序：直播表达链路（语音/形象/OBS）在前，基础设施在后 */
export const HEALTH_DOMAIN_ORDER = [
  'tts',
  'avatar',
  'obs',
  'maibot',
  'tools_providers',
  'collectors',
  'agents',
] as const;

/** 域名 → 运营可读标题 */
export const HEALTH_DOMAIN_LABELS: Record<string, string> = {
  tts: '语音合成（TTS）',
  avatar: '虚拟形象平台',
  obs: 'OBS',
  maibot: 'MaiBot 桥接',
  tools_providers: '工具提供者',
  collectors: '采集器',
  agents: 'Agent',
};

/** 状态 → 状态点样式后缀（组件模板按 `is-<state>` 拼类名） */
export const HEALTH_STATE_CLASS: Record<HealthState, string> = {
  ok: 'ok',
  degraded: 'degraded',
  offline: 'offline',
  unknown: 'unknown',
};

const STATE_SEVERITY: Record<HealthState, number> = {
  ok: 0,
  unknown: 1,
  degraded: 2,
  offline: 3,
};

/** 一组条目的最差状态（空组按 unknown，避免空卡片误报正常） */
export function worstHealthState(items: SystemHealthItem[]): HealthState {
  let worst: HealthState = 'unknown';
  for (const item of items) {
    if (STATE_SEVERITY[item.state] > STATE_SEVERITY[worst]) worst = item.state;
  }
  return worst;
}

export interface HealthDomainGroup {
  domain: string;
  label: string;
  items: SystemHealthItem[];
  worst: HealthState;
}

/** 把响应整理成卡片列表：按固定域顺序、只出响应中出现的域（缺席域不铺空卡），未知域排尾 */
export function summarizeDomains(
  domains: SystemHealthWallResponse['domains'] | null | undefined,
): HealthDomainGroup[] {
  const known = HEALTH_DOMAIN_ORDER.filter(domain => domains && domain in domains);
  const extra = domains
    ? Object.keys(domains).filter(domain => !HEALTH_DOMAIN_ORDER.includes(domain as never))
    : [];
  return [...known, ...extra].map(domain => {
    const items = domains?.[domain] ?? [];
    return {
      domain,
      label: HEALTH_DOMAIN_LABELS[domain] ?? domain,
      items,
      worst: worstHealthState(items),
    };
  });
}

/** 重连按钮可见性：仅提供者自身声明支持手动重连的条目（无重连端点的域不显示） */
export function isReconnectable(item: SystemHealthItem): boolean {
  return item.supports_reconnect;
}
