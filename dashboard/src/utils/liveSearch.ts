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
import { isGameTool } from './liveLanes';

/** 片段的一段：hit=true 的段是关键词本身 */
export interface SnippetPart {
  text: string;
  hit: boolean;
}

/** 命中分类：按"你在找什么"归的六桶（不按条目种类细分——kind 有 20+ 种、
 *  事件名近 60 种，直接展示或按它们过滤都会碎成一排杂名） */
export type LiveHitCategory = 'audience' | 'speech' | 'agent' | 'game' | 'lead' | 'system';

/** 分类的中文短名（清单 tag 列与过滤 chips 共用） */
export const HIT_CATEGORY_LABEL: Record<LiveHitCategory, string> = {
  audience: '观众',
  speech: '发言',
  agent: '决策调用',
  game: '游戏',
  lead: '主控',
  system: '系统',
};

/** 一条搜索结果 */
export interface SearchHit {
  /** 唯一标识（条目 id / 记录 id / 服务端条目的合成 id） */
  id: string;
  /** 命中时刻（Unix 毫秒） */
  tsMs: number;
  /** 类型标签（弹幕 / 发言 / 工具…；决策轨迹档是请求结果） */
  tag: string;
  /** 所属分类（过滤 chips 的数据源） */
  category: LiveHitCategory;
  /** 主体（说话人 / 工具名 / 载荷字段名 / 事件名；给不出主体的条目是空串） */
  actor: string;
  /** 命中片段（hit=true 的段是关键词本身）。关键词不在返回内容里的条目
   *  放整段原文或一句说明，全程 hit=false */
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
  lead_change: '主控',
  lead_suggestion: '建议',
  lead_commentary: '主控解说',
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

// ---------------------------------------------------------------------------
// 分类：三类命中来源（本地条目 / 服务端明细行 / 服务端事件）归到同一套五桶
// ---------------------------------------------------------------------------

/** 本地条目 → 分类。工具与思考行按调用来源分侧（与双轨归属同一判据）；
 *  递话语义上是下个决策参考块的输入，归决策调用 */
export function entryCategoryOf(entry: ShowEntry): LiveHitCategory {
  switch (entry.kind) {
    case 'danmaku':
    case 'gift':
    case 'super_chat':
    case 'guard':
    case 'enter':
      return 'audience';
    case 'speech':
      return 'speech';
    case 'game':
    case 'game_chat':
    case 'game_body':
    case 'others_things':
    case 'milestone':
    case 'agent_reply':
    case 'delegate_receipt':
      return 'game';
    case 'rundown':
    case 'boundary':
    case 'stage':
    case 'tts':
    case 'tool_health':
    case 'system':
      return 'system';
    case 'lead_change':
    case 'lead_suggestion':
    case 'lead_commentary':
      // 主控域条目独立成桶：谁在主控是运营者单独会翻的问题，不混进系统流水
      return 'lead';
    default:
      // tool / thinking / prompt / verdict / decision：工具按来源分侧，其余属决策过程
      return isGameTool(entry) ? 'game' : 'agent';
  }
}

/** 服务端事件 → 分类：按事件名前缀归桶；工具结果看载荷里的 tool_name 分侧。
 *  未知事件名兜底进系统（机器行宁可多归系统，不冒充实内容） */
export function eventItemCategoryOf(
  eventType: string,
  data: Record<string, unknown>,
): LiveHitCategory {
  if (eventType.startsWith('room.')) return 'audience';
  if (eventType === 'streamer.speech') return 'speech';
  if (eventType.startsWith('planner.')) return 'agent';
  if (eventType === 'agent.prompted') return 'agent';
  if (eventType === 'agent.replied') return 'game';
  if (eventType.startsWith('agent.lead.')) return 'lead';
  if (eventType.startsWith('game.') || eventType.startsWith('task.')) return 'game';
  if (eventType.startsWith('tool.')) {
    const toolName = String(data.tool_name ?? '');
    return /^(minecraft|maicraft)_/.test(toolName) ? 'game' : 'agent';
  }
  return 'system';
}

/** 服务端事件的清单标签：能细到具体动作就细（决策/裁决/工具/递话…），
 *  认不出的回落到桶名——tag 列不出现英文事件名原文 */
export function eventTagLabel(eventType: string, category: LiveHitCategory): string {
  if (eventType.startsWith('room.')) return '弹幕';
  if (eventType === 'streamer.speech') return '发言';
  if (eventType === 'streamer.stage') return '阶段';
  if (eventType.startsWith('streamer.rundown')) return '环节';
  if (eventType === 'planner.decision') return '决策';
  if (eventType === 'planner.verdict') return '裁决';
  if (eventType.startsWith('tool.result.')) return '工具';
  if (eventType.startsWith('tool.health.')) return '工具健康';
  if (eventType === 'agent.prompted') return '递话';
  if (eventType === 'agent.replied') return 'Agent 步骤';
  if (eventType === 'agent.lead.changed') return '主控';
  if (eventType === 'agent.lead.suggestion') return '建议';
  if (eventType === 'agent.lead.commentary') return '主控解说';
  if (eventType === 'task.changed') return '任务';
  if (eventType.startsWith('game.')) return '游戏';
  if (eventType.startsWith('tts.utterance.')) return '播报';
  if (eventType.startsWith('live.')) return '场次';
  return HIT_CATEGORY_LABEL[category];
}

/** 服务端命中条目 → 分类（明细行按 kind 直查，事件条目走前缀规则） */
export function itemCategoryOf(item: SessionTimelineItem): LiveHitCategory {
  const kind = String(item.kind ?? '');
  if (kind === 'event') {
    return eventItemCategoryOf(
      String(item.event_type ?? ''),
      (item.data ?? {}) as Record<string, unknown>,
    );
  }
  switch (kind) {
    case 'danmaku':
    case 'guard':
    case 'enter':
    case 'gift_row':
    case 'super_chat_row':
      return 'audience';
    case 'speech':
      return 'speech';
    default:
      return 'system';
  }
}

/** 各分类的命中计数（固定桶序，只出数量大于 0 的桶——过滤 chips 的数据源） */
export function countByCategory(
  hits: SearchHit[],
): Array<{ key: LiveHitCategory; label: string; count: number }> {
  const order: LiveHitCategory[] = ['audience', 'speech', 'agent', 'game', 'lead', 'system'];
  const counts = new Map<LiveHitCategory, number>();
  for (const hit of hits) counts.set(hit.category, (counts.get(hit.category) ?? 0) + 1);
  return order
    .filter(key => (counts.get(key) ?? 0) > 0)
    .map(key => ({ key, label: HIT_CATEGORY_LABEL[key], count: counts.get(key) ?? 0 }));
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
    category: entryCategoryOf(entry),
    actor: entry.actor,
    parts: entrySnippet(entry, keyword),
    entry,
  };
}

/** 载荷里的一处位置：字段路径与这一处的文本（字符串值的原文，或键名） */
interface PayloadLocation {
  /** 字段路径：嵌套对象用点连接，数组元素带下标 */
  label: string;
  text: string;
}

/** 载荷里第一处 pick 认可的位置（保持键的顺序，逐层深入）。pick 返回这一处
 *  要展示的文本，null 表示这一处不算；数组元素没有键名，key 传 null */
function findInPayload(
  data: Record<string, unknown>,
  pick: (key: string | null, value: unknown) => string | null,
  prefix = '',
): PayloadLocation | null {
  for (const [key, value] of Object.entries(data)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const picked = pick(key, value);
    if (picked !== null) return { label: path, text: picked };
    if (Array.isArray(value)) {
      for (let i = 0; i < value.length; i += 1) {
        const itemPath = `${path}[${i}]`;
        const item = value[i];
        const itemPicked = pick(null, item);
        if (itemPicked !== null) return { label: itemPath, text: itemPicked };
        if (item && typeof item === 'object') {
          const nested = findInPayload(item as Record<string, unknown>, pick, itemPath);
          if (nested) return nested;
        }
      }
      continue;
    }
    if (value && typeof value === 'object') {
      const nested = findInPayload(value as Record<string, unknown>, pick, path);
      if (nested) return nested;
    }
  }
  return null;
}

/** 整场检索判定命中、但条目返回的内容里找不到关键词时，片段位显示的说明 */
const KEYWORD_NOT_IN_RETURNED_TEXT = '整场检索命中，但返回的内容里不含关键词';

/** 不标高亮的片段：整段原文照放；原文为空时放说明 */
function unmarkedSnippet(text: string): SnippetPart[] {
  return [{ text: text || KEYWORD_NOT_IN_RETURNED_TEXT, hit: false }];
}

/** 事件条目的主体与命中片段。关键词的位置按信息量从多到少找：载荷里的
 *  字符串值（片段带上下文）→ 载荷里的键名 → 事件名；三处都没有时主体留空，
 *  片段位放说明。needle 是去掉首尾空白并转成小写的关键词 */
function eventActorAndSnippet(
  eventType: string,
  data: Record<string, unknown>,
  needle: string,
): { actor: string; parts: SnippetPart[] } {
  const byValue = findInPayload(data, (_key, value) =>
    typeof value === 'string' && value.toLowerCase().includes(needle) ? value : null,
  );
  if (byValue) return { actor: byValue.label, parts: hitSnippet(byValue.text, needle) };
  const byKey = findInPayload(data, (key, _value) =>
    key !== null && key.toLowerCase().includes(needle) ? key : null,
  );
  if (byKey) return { actor: byKey.label, parts: hitSnippet(byKey.text, needle) };
  if (eventType.toLowerCase().includes(needle)) {
    return { actor: eventType, parts: hitSnippet(eventType, needle) };
  }
  return { actor: '', parts: unmarkedSnippet('') };
}

/** 明细行的命中片段：正文命中取正文，其次发送者昵称；两处都对不上时取正文
 *  且不标高亮。对不上通常是因为关键词只在发送者昵称里，而昵称不一定随条目
 *  返回（主播发言行只带正文） */
function detailRowSnippet(text: string, name: string, needle: string): SnippetPart[] {
  if (text.toLowerCase().includes(needle)) return hitSnippet(text, needle);
  if (name.toLowerCase().includes(needle)) return hitSnippet(name, needle);
  return unmarkedSnippet(text);
}

/** 服务端命中（时间线条目形状）→ 结果行。条目是否命中由服务端的整场检索
 *  判定，返回几条这里就建几行：服务端还按事件摘要匹配、并且掌握未被截断的
 *  载荷，这两处前端拿不到，所以不在"前端找不到关键词"时丢条目 */
export function hitFromItem(item: SessionTimelineItem, keyword: string): SearchHit | null {
  const needle = keyword.trim().toLowerCase();
  if (!needle) return null;
  const kind = String(item.kind ?? '');
  const tsMs = Number(item.ts_ms ?? 0);

  if (kind === 'event') {
    const data = (item.data ?? {}) as Record<string, unknown>;
    const eventType = String(item.event_type ?? '');
    const category = eventItemCategoryOf(eventType, data);
    const { actor, parts } = eventActorAndSnippet(eventType, data, needle);
    return {
      id: `srv-${tsMs}-${eventType}`,
      tsMs,
      tag: eventTagLabel(eventType, category),
      category,
      actor,
      parts,
    };
  }

  // 正文候选按条目形状取：发言/弹幕/SC 是 text|content，礼物行是 gift_name
  const text = String(item.text ?? item.content ?? item.gift_name ?? '');
  const name = String(item.user_name ?? '');
  return {
    id: `srv-${tsMs}-${kind}`,
    tsMs,
    tag: kindShortLabel(
      kind === 'gift_row' ? 'gift' : kind === 'super_chat_row' ? 'super_chat' : kind,
    ),
    category: itemCategoryOf(item),
    actor: name || '—',
    parts: detailRowSnippet(text, name, needle),
  };
}

/** 整场检索返回的一批条目 → 结果行清单，顺序不变。hitFromItem 只在关键词
 *  为空时返回 null，所以非空关键词下返回几条、清单就有几行 */
export function hitsFromItems(items: SessionTimelineItem[], keyword: string): SearchHit[] {
  return items
    .map(item => hitFromItem(item, keyword))
    .filter((hit): hit is SearchHit => hit !== null);
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

/** 请求记录 → 结果行（决策轨迹档；记录都是决策轮，固定归决策调用——
 *  该档的过滤 chips 不渲染，这个值只为接口完整） */
export function hitFromRecord(record: RequestRecord, keyword: string): SearchHit | null {
  const message = recordHitMessage(record, keyword);
  if (!message) return null;
  return {
    id: record.id,
    tsMs: record.tsMs,
    tag: record.outcomeLabel || '请求',
    category: 'agent',
    actor: record.trigger || '—',
    parts: hitSnippet(message.text, keyword),
    record,
    message,
  };
}
