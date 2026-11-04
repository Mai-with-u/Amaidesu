/**
 * 记忆账本的展示逻辑（承诺状态、对谁、整理状态文案）
 *
 * 记忆页与场次复盘页共用：组件模板只消费这里的结果，筛选值到接口参数的换算
 * 也收在这里，行为由单元测试直接断言。
 */

import type { CommitmentItem, CommitmentStatus, UnregisteredKind } from '@/types';

/** 挂了游戏的条目共有的三个字段（承诺与档案行都有） */
export interface GameSaveFields {
  game: string | null;
  save_id: string;
  save_name: string;
}

/**
 * 游戏与存档标签：同一款游戏的不同存档是互不相干的世界，运营者纠错时要看得出是哪个存档里的事。
 * 有存档名写名字，没名字写编号前 8 位，没记存档（旧数据、当时没认出）只写游戏名；不挂游戏为 null。
 */
export function gameSaveLabel(item: GameSaveFields): string | null {
  if (!item.game) return null;
  if (item.save_name) return `${item.game}「${item.save_name}」`;
  if (item.save_id) return `${item.game}「编号 ${item.save_id.slice(0, 8)}」`;
  return item.game;
}

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

/** 了结原话长度上限（与后端 CommitmentCloseRequest.close_quote 的 max_length 一致） */
export const CLOSE_QUOTE_MAX_LENGTH = 200;

/** 了结原话校验：通过返回 null，超长返回提示文案（ElMessageBox 的 inputValidator 直接消费） */
export function validateCloseQuote(value: string): string | null {
  return value.length <= CLOSE_QUOTE_MAX_LENGTH ? null : `最多 ${CLOSE_QUOTE_MAX_LENGTH} 字`;
}

/** 承诺原话的三种状态：有原话 / 手工登记本就没有 / 原句已从本场台词里删掉 */
export type CommitmentQuoteState = 'quoted' | 'manual' | 'deleted';

/**
 * 承诺原话状态：手建承诺没有来源场次（created_session_id 为空），本就没有原话；
 * 场次整理登记的承诺原话为空，只可能是引用它的那句台词被删了——承诺保留，
 * 页面显示"原句已删除"。
 */
export function commitmentQuoteState(
  item: Pick<CommitmentItem, 'quote' | 'created_session_id'>,
): CommitmentQuoteState {
  if (item.quote) return 'quoted';
  return item.created_session_id === null ? 'manual' : 'deleted';
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
