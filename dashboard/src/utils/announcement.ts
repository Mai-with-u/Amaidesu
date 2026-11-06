/**
 * 直播间公告文本：由前端按已读到的保留期配置拼出，后端不另加接口。
 *
 * 公告要把处理的信息种类、保存期限与观众自行删除的方式说清楚；保留期改了，
 * 公告跟着变。保留期只管数据库与其备份，运行日志不在它的管辖内，所以日志里的
 * 残留一律写"可联系运营者处理"，不承诺天数。
 */

export interface AnnouncementRetention {
  /** 弹幕、付费身份列、话题摘要、请求正文与迁移前备份的保留天数；0 = 不自动删除 */
  rawDays: number;
  /** 长期不来的观众行的清理天数；0 = 不自动删除 */
  viewerAbsenceDays: number;
}

export interface AnnouncementOptions extends AnnouncementRetention {
  /** 运营者要公示的联系方式；空串时公告里留出补填提示 */
  contact: string;
}

/** 从 GET /api/v1/config 的合并视图里读保留期（键缺失或不是正整数按 0 处理） */
export function readRetention(config: Record<string, unknown> | undefined): AnnouncementRetention {
  const section = config?.['retention'];
  const obj = section && typeof section === 'object' ? (section as Record<string, unknown>) : {};
  const readDays = (key: string): number => {
    const value = obj[key];
    return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
  };
  return { rawDays: readDays('raw_days'), viewerAbsenceDays: readDays('viewer_absence_days') };
}

/** 拼直播间公告全文；返回可直接贴进直播间简介的文本（逐条一行） */
export function composeAnnouncement(options: AnnouncementOptions): string {
  const rawRetention = options.rawDays > 0 ? `最多 ${options.rawDays} 天` : '长期保存';
  const viewerRetention =
    options.viewerAbsenceDays > 0 ? `（保留到最后一次来访后 ${options.viewerAbsenceDays} 天）` : '';
  const residual =
    options.rawDays > 0
      ? `数据库备份里的残留最多保留 ${options.rawDays} 天；运行日志里可能仍有残留，可联系运营者处理。`
      : '备份与日志可能仍有残留，可联系运营者处理。';
  const contact = options.contact.trim() || '（由运营者补填）';
  return [
    '本主播是 AI。',
    `为互动会保存弹幕（${rawRetention}）。`,
    `发过弹幕的观众，平台编号、昵称记录和累计打赏额会保留，并记录来过几场${viewerRetention}。`,
    '她自己说过的话长期保存。',
    `发“忘了我”会立即停止记录你，下播后删除关于你的记录（最迟 24 小时），以后也不再记录；删除前发“开玩笑的”可以撤回。${residual}`,
    `联系方式：${contact}`,
  ].join('\n');
}
