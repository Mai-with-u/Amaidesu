// 时间线内容检索：匹配、命中片段与结果行构造（纯逻辑，与 liveFeed / liveLanes 同层）
//
// 搜索回答的是"这一场里出现过什么"：条目正文、备注、说话人（观众昵称 / 工具名）
// 都参与匹配。匹配是大小写不敏感的子串匹配——中文不分词，"破防"能命中
// "别破防了"，不做分词也不做模糊匹配。
//
// 命中行有两种来源：
// - 本地条目（已加载窗口内命中的条目）：直接是时间线条目
// - 服务端命中（整场检索返回的时间线条目形状）：id 与本地条目不同（分页下标不同），
//   因此跳转与标记都按时刻与内容走，不靠 id

import type { SessionTimelineItem } from '@/types';
import type { RequestMessage, RequestRecord } from './requestRecords';
import type { ShowEntry } from './liveFeed';

/** 片段的一段：hit=true 的段是关键词本身 */
export interface SnippetPart {
  text: string;
  hit: boolean;
}

/** 一条搜索结果 */
export interface SearchHit {
  /** 唯一标识（条目 id / 记录 id / 服务端条目的合成 id） */
  id: string;
  /** 命中时刻（Unix 毫秒） */
  tsMs: number;
  /** 类型标签（弹幕 / 发言 / 工具…；决策轨迹档是请求结果） */
  tag: string;
  /** 主体（说话人 / 工具名 / 载荷字段名） */
  actor: string;
  /** 命中片段 */
  parts: SnippetPart[];
  /** 本地条目（服务端命中没有本地条目，跳转按时刻走） */
  entry?: ShowEntry;
  /** 请求记录（决策轨迹档） */
  record?: RequestRecord;
  /** 命中的那条请求消息（决策轨迹档，用于打开详情） */
  message?: RequestMessage;
}

/** 条目种类的中文短名（结果清单当类型标签用；各卡片自己的标签不在这里重复） */
const KIND_SHORT_LABEL: Record<string, string> = {
  danmaku: '弹幕',
  gift: '礼物',
  super_chat: 'SC',
  guard: '上舰',
  enter: '进场',
  speech: '发言',
  tool: '工具',
  decision: '决策',
  verdict: '裁决',
  thinking: '思考',
  agent_reply: 'Agent 步骤',
  prompt: '递话',
  delegate_receipt: '收活',
  game: '游戏',
  game_chat: '游戏聊天',
  game_body: '游戏事件',
  others_things: '叙事',
  milestone: '里程碑',
  rundown: '环节',
  boundary: '边界',
  stage: '阶段',
  tts: '播报',
  tool_health: '工具健康',
  system: '系统',
};

/** 条目种类的中文短名（未知种类回落到原值） */
export function kindShortLabel(kind: string): string {
  return KIND_SHORT_LABEL[kind] ?? kind;
}

/** 条目参与匹配的文本：正文 + 备注 + 说话人 */
export function entrySearchText(entry: ShowEntry): string {
  return [entry.text, entry.note, entry.actor].filter(Boolean).join('\n');
}

/** 条目是否命中关键词（子串，大小写不敏感） */
export function entryMatches(entry: ShowEntry, keyword: string): boolean {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return false;
  return entrySearchText(entry).toLowerCase().includes(needle);
}

/** 一批条目里命中的那些（保持原顺序） */
export function matchEntries(entries: ShowEntry[], keyword: string): ShowEntry[] {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return [];
  return entries.filter(entry => entrySearchText(entry).toLowerCase().includes(needle));
}

/** 取包含关键词的一段文本，切成"前段 / 关键词 / 后段" */
export function hitSnippet(text: string, keyword: string, context = 14): SnippetPart[] {
  const needle = keyword.trim().toLowerCase();
  if (!needle || !text) return [];
  const at = text.toLowerCase().indexOf(needle);
  if (at < 0) return [{ text, hit: false }];
  const start = Math.max(0, at - context);
  const end = Math.min(text.length, at + needle.length + context);
  const parts: SnippetPart[] = [];
  // 前段：有省略号截断时补省略号；关键词就在开头时原文照放，不丢字
  if (start > 0) parts.push({ text: `…${text.slice(start, at)}`, hit: false });
  else if (at > 0) parts.push({ text: text.slice(0, at), hit: false });
  parts.push({ text: text.slice(at, at + needle.length), hit: true });
  const tailAt = at + needle.length;
  if (end > tailAt) {
    parts.push({ text: `${text.slice(tailAt, end)}${end < text.length ? '…' : ''}`, hit: false });
  }
  return parts;
}

/** 条目的命中片段：正文优先，其次备注，最后说话人 */
export function entrySnippet(entry: ShowEntry, keyword: string): SnippetPart[] {
  const needle = keyword.trim();
  if (!needle) return [];
  for (const field of [entry.text, entry.note, entry.actor]) {
    if (!field) continue;
    if (field.toLowerCase().includes(needle.toLowerCase())) return hitSnippet(field, needle);
  }
  return [];
}

/** 本地条目 → 结果行 */
export function hitFromEntry(entry: ShowEntry, keyword: string): SearchHit {
  return {
    id: entry.id,
    tsMs: entry.tsMs,
    tag: entry.badge || kindShortLabel(entry.kind),
    actor: entry.actor,
    parts: entrySnippet(entry, keyword),
    entry,
  };
}

/** 载荷里第一个命中关键词的字符串（连同它的字段路径） */
function payloadHit(
  data: Record<string, unknown>,
  keyword: string,
  prefix = '',
): { label: string; text: string } | null {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return null;
  for (const [key, value] of Object.entries(data)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') {
      if (value.toLowerCase().includes(needle)) return { label: path, text: value };
      continue;
    }
    if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i += 1) {
        const item = value[i];
        if (typeof item === 'string' && item.toLowerCase().includes(needle)) {
          return { label: `${path}[${i}]`, text: item };
        }
        if (item && typeof item === 'object') {
          const nested = payloadHit(item as Record<string, unknown>, keyword, `${path}[${i}]`);
          if (nested) return nested;
        }
      }
      continue;
    }
    if (value && typeof value === 'object') {
      const nested = payloadHit(value as Record<string, unknown>, keyword, path);
      if (nested) return nested;
    }
  }
  return null;
}

/** 服务端命中（时间线条目形状）→ 结果行 */
export function hitFromItem(item: SessionTimelineItem, keyword: string): SearchHit | null {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return null;
  const kind = String(item.kind ?? '');
  const tsMs = Number(item.ts_ms ?? 0);

  if (kind === 'event') {
    const data = (item.data ?? {}) as Record<string, unknown>;
    const found = payloadHit(data, needle);
    if (!found) return null;
    return {
      id: `srv-${tsMs}-${String(item.event_type ?? '')}`,
      tsMs,
      tag: String(item.event_type ?? '事件'),
      actor: found.label,
      parts: hitSnippet(found.text, needle),
    };
  }

  // 正文候选按条目形状取：发言/弹幕/SC 是 text|content，礼物行是 gift_name
  const text = String(item.text ?? item.content ?? item.gift_name ?? '');
  const name = String(item.user_name ?? '');
  const matchedText = text.toLowerCase().includes(needle);
  const matchedName = name.toLowerCase().includes(needle);
  if (!matchedText && !matchedName) return null;
  const parts = matchedText ? hitSnippet(text, needle) : hitSnippet(name, needle);
  return {
    id: `srv-${tsMs}-${kind}`,
    tsMs,
    tag: kindShortLabel(
      kind === 'gift_row' ? 'gift' : kind === 'super_chat_row' ? 'super_chat' : kind,
    ),
    actor: name || '—',
    parts,
  };
}

/** 请求记录里第一条命中关键词的消息 */
export function recordHitMessage(record: RequestRecord, keyword: string): RequestMessage | null {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return null;
  return record.messages.find(message => message.text.toLowerCase().includes(needle)) ?? null;
}

/** 请求记录是否命中关键词 */
export function recordMatches(record: RequestRecord, keyword: string): boolean {
  return recordHitMessage(record, keyword) !== null;
}

/** 请求记录 → 结果行（决策轨迹档） */
export function hitFromRecord(record: RequestRecord, keyword: string): SearchHit | null {
  const message = recordHitMessage(record, keyword);
  if (!message) return null;
  return {
    id: record.id,
    tsMs: record.tsMs,
    tag: record.outcomeLabel || '请求',
    actor: record.trigger || '—',
    parts: hitSnippet(message.text, keyword),
    record,
    message,
  };
}
