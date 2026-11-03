/**
 * 记忆账本的展示逻辑（承诺状态、对谁、整理状态文案）
 *
 * 记忆页与场次复盘页共用：组件模板只消费这里的结果，筛选值到接口参数的换算
 * 也收在这里，行为由单元测试直接断言。
 */

import type { CommitmentItem, CommitmentStatus, UnregisteredKind } from '@/types';

/** 承诺筛选值：all = 全部（与后端 status 参数的空值语义一致） */
export type CommitmentStatusFilter = CommitmentStatus | 'all';

/** 承诺状态 → 页面标签 */
export const COMMITMENT_STATUS_LABELS: Record<CommitmentStatus, string> = {
  open: '未了结',
  closed: '已了结',
  void: '误记',
};

/** 承诺状态 → 标签颜色（未了结待办用醒目色，误记弱化） */
export function commitmentStatusLabel(status: CommitmentStatus): string {
  return COMMITMENT_STATUS_LABELS[status] ?? status;
}

export function commitmentStatusTagType(status: CommitmentStatus): 'warning' | 'success' | 'info' {
  if (status === 'closed') return 'success';
  if (status === 'void') return 'info';
  return 'warning';
}

/** 对谁：有观众昵称用昵称，没有具体对象就是"对全场" */
export function commitmentTargetLabel(
  item: Pick<CommitmentItem, 'viewer_id' | 'viewer_name'>,
): string {
  if (item.viewer_id === null) return '对全场';
  return item.viewer_name || `观众 #${item.viewer_id}`;
}

/** 筛选值 → listCommitments 的 status 参数（全部不发该参数） */
export function commitmentStatusQuery(
  filter: CommitmentStatusFilter,
): CommitmentStatus | undefined {
  return filter === 'all' ? undefined : filter;
}

/** 未登记条目类型 → 页面标签 */
export function unregisteredKindLabel(kind: UnregisteredKind): string {
  return kind === 'commitment' ? '承诺' : '档案';
}

/** 整理状态标签：已整理 / 待整理 */
export function settleStateLabel(settledAtMs: number | null): string {
  return settledAtMs === null ? '待整理' : '已整理';
}

/** 自动重试进度："重试 1/3"；上限未知时只报次数 */
export function settleAttemptsText(attempts: number, maxAttempts: number): string {
  return maxAttempts > 0 ? `重试 ${attempts}/${maxAttempts}` : `重试 ${attempts}`;
}

/** 自动重试是否用满（用满后调度不再挑这一场，需要人工点"重新整理"） */
export function settleAttemptsExhausted(attempts: number, maxAttempts: number): boolean {
  return maxAttempts > 0 && attempts >= maxAttempts;
}
