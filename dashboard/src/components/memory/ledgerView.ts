/**
 * 记忆账本的展示逻辑（承诺状态、对谁、整理状态文案）
 *
 * 记忆页与场次复盘页共用：组件模板只消费这里的结果，筛选值到接口参数的换算
 * 也收在这里，行为由单元测试直接断言。
 */

import type {
  ArchiveLineItem,
  CandidateKind,
  CandidateStatus,
  CommitmentItem,
  CommitmentStatus,
  NearCommitment,
  SettleSessionItem,
  SnapshotChangeAction,
  UnregisteredKind,
} from '@/types';

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
  if (kind === 'candidate') return '观众候选';
  return kind === 'commitment' ? '承诺' : '档案';
}

/** 观众候选类型 → 页面标签 */
export function candidateKindLabel(kind: CandidateKind): string {
  return kind === 'claimed_missing' ? '她说欠着的' : '观众定下的';
}

/** 观众候选状态 → 页面标签 */
export function candidateStatusLabel(status: CandidateStatus): string {
  const labels: Record<CandidateStatus, string> = {
    pending: '待审',
    approved: '已批准',
    rejected: '已驳回',
  };
  return labels[status] ?? status;
}

/** "她说欠着的"旁边那一行：账上最接近的几条（约#N「标题」已了结）；一条不沾时提醒可能是她编的 */
export function nearestCommitmentsText(nearest: NearCommitment[] | undefined): string {
  if (!nearest || nearest.length === 0)
    return '账上没有沾边的承诺：可能是漏登记，也可能是她顺着观众的话编的';
  const items = nearest.map(
    item => `约#${item.id}「${item.what}」${item.status === 'closed' ? '（已了结）' : ''}`,
  );
  return `账上最接近的：${items.join('、')}`;
}

/** 整理报告里观众候选的一句话：列了几条（分两类）、因个人信息丢了几条；都没有返回 null */
export function candidateReportText(
  listed: Partial<Record<CandidateKind, number>> | undefined,
  personalDropped: number | undefined,
): string | null {
  const parts: string[] = [];
  const defined = listed?.viewer_defined ?? 0;
  const claimed = listed?.claimed_missing ?? 0;
  if (defined) parts.push(`观众定下的 ${defined} 条`);
  if (claimed) parts.push(`她说欠着的 ${claimed} 条`);
  const head = parts.length ? `列进待审区：${parts.join('、')}` : '';
  const tail = personalDropped ? `含观众个人信息丢掉 ${personalDropped} 条` : '';
  return [head, tail].filter(Boolean).join('；') || null;
}

/** 档案快照改动 → 页面标签 */
export function snapshotChangeLabel(action: SnapshotChangeAction): string {
  const labels: Record<SnapshotChangeAction, string> = {
    drop: '去掉',
    replace: '替换',
    evicted: '挤出',
    refused: '没采纳',
  };
  return labels[action];
}

/** 常驻简报至多列 12 行档案：人放回、新增的行超过这个数，她本场的新进展会被挤出简报 */
export const SNAPSHOT_BRIEF_LINES = 12;

/** 档案行标题上限（与后端审核仓储一致，和整理写的行同一上限） */
export const ARCHIVE_TITLE_MAX_LENGTH = 30;

/** 新增档案行的原话上限（与后端审核仓储一致） */
export const ARCHIVE_QUOTE_MAX_LENGTH = 80;

/** 档案行标题校验：通过返回 null，否则返回提示文案（ElMessageBox 的 inputValidator 直接消费） */
export function validateArchiveTitle(value: string): string | null {
  const title = value.trim();
  if (!title) return '标题不能为空';
  return title.length <= ARCHIVE_TITLE_MAX_LENGTH ? null : `最多 ${ARCHIVE_TITLE_MAX_LENGTH} 字`;
}

/**
 * 原话是不是那句台词的逐字片段：原文与连续空白压成一个空格后的写法都认（和后端、整理同一口径）。
 */
export function quoteFitsSpeech(quote: string, content: string): boolean {
  const text = quote.trim();
  if (!text) return false;
  return content.includes(text) || content.split(/\s+/).join(' ').includes(text);
}

/** 档案行的标签：在不在最新快照里、稳定的事、人放回的、人改过的 */
export function archiveLineTags(
  line: Pick<ArchiveLineItem, 'in_snapshot' | 'lasting' | 'pinned' | 'human_locked'>,
): { label: string; type: 'success' | 'info' | 'warning' | 'primary' }[] {
  const tags: { label: string; type: 'success' | 'info' | 'warning' | 'primary' }[] = [];
  tags.push(
    line.in_snapshot
      ? { label: '下一场记得', type: 'success' }
      : { label: '不在快照里', type: 'info' },
  );
  if (line.in_snapshot && line.lasting) tags.push({ label: '稳定的事', type: 'primary' });
  if (line.pinned) tags.push({ label: '人放回的', type: 'warning' });
  if (line.human_locked) tags.push({ label: '人改过', type: 'warning' });
  return tags;
}

/** 放回、新增的行太多时的提示；没超过简报行数返回 null */
export function pinnedOverflowHint(pinnedCount: number): string | null {
  if (pinnedCount <= SNAPSHOT_BRIEF_LINES) return null;
  return `最新快照里人放回、新增的行已有 ${pinnedCount} 行，超过常驻简报的 ${SNAPSHOT_BRIEF_LINES} 行：她本场的新进展会被挤出简报，删掉或合并一些。`;
}

/** 审核状态标签：待审 / 已审 */
export function reviewStateLabel(reviewedAtMs: number | null): string {
  return reviewedAtMs === null ? '待审' : '已审';
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

/**
 * 这一场之后是否已经有整理好的正式场。
 *
 * 有的话，重新整理这一场只重算它自己的档案快照：它原来的档案行会从之后几场的快照里撤掉，
 * 新写的不会进去（之后几场不重放）。确认框据此提醒运营者，要把结果带到简报里，得按时间顺序
 * 把之后几场也依次重新整理。
 */
export function hasLaterSettledSession(
  row: Pick<SettleSessionItem, 'live_session_id' | 'started_at_ms'>,
  items: readonly Pick<SettleSessionItem, 'live_session_id' | 'started_at_ms' | 'settled_at_ms'>[],
): boolean {
  return items.some(
    item =>
      item.live_session_id !== row.live_session_id &&
      item.settled_at_ms !== null &&
      item.started_at_ms > row.started_at_ms,
  );
}
