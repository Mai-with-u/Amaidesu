// 共享事件摘要工具
//
// Dashboard 速率面板、Collectors/Agents 实时流均需要把 WebSocketMessage
// 折算成"一行可读的运维摘要"。把规则集中到一处可避免三处各自维护带来的
// 行为漂移。摘要规则：
//   - room.message.* → "{message_type} · {user.name/id}: {content 截断}"
//   - tool.result.<name> → "{tool_name} · {status} [+ error tail]"
//   - agenda.* → "{action} {item.label}"
//   - planner.* → 1-2 个最有意义的 kv（active/next 或 timeline_summary 截断）
//   - game.* → "{message}" + scene；live.* → session/platform 摘要
//   - core.* → message 或 event 名
//   - 其余：原样返回事件 type
//
// 注意：事件 timestamp 是 Unix 秒（与 Dashboard/LiveObserver 既有用法一致），
// 调用方如需展示相对时间请自行换算。

/**
 * 按业务族抽取事件的人类可读摘要。
 *
 * @param type 事件 type（如 `room.message.danmaku`）
 * @param data 事件 data 载荷（任意可 JSON 反序列化对象）
 * @returns 单行摘要（已做长度截断；省略号 `…` 收尾）
 */
export function summarizeEvent(type: string, data: unknown): string {
  const d = (data ?? {}) as Record<string, unknown>;

  if (type.startsWith('room.message.')) {
    const messageType = pickString(d.message_type, 12) || type.replace('room.message.', '');
    const user = (d.user as { name?: string; id?: string } | undefined) ?? null;
    const userLabel = user?.name || (user?.id ? `#${user.id}` : '匿名');
    const content = pickString(d.content, 24);
    return content ? `${messageType} · ${userLabel}: ${content}` : `${messageType} · ${userLabel}`;
  }

  if (type.startsWith('tool.result.')) {
    const toolName = pickString(d.tool_name, 16) || type.replace('tool.result.', '');
    const status = pickString(d.status, 8) || '—';
    const errTail = pickString(d.error_message, 24);
    return errTail ? `${toolName} · ${status} · ${errTail}` : `${toolName} · ${status}`;
  }

  if (type.startsWith('agenda.')) {
    const action = pickString(d.action, 8);
    const item = (d.item as { label?: string } | undefined) ?? null;
    const label = item?.label ? truncate(item.label, 18) : '';
    return label ? `${action} ${label}` : action || type;
  }

  if (type.startsWith('planner.')) {
    const item = (d.agenda_item as { active?: string; next?: string } | undefined) ?? null;
    const summary = pickString(d.timeline_summary, 24);
    const active = item?.active ? truncate(item.active, 14) : '';
    const next = item?.next ? truncate(item.next, 14) : '';
    const parts = [active, next].filter(Boolean);
    if (parts.length) return parts.join(' → ');
    return summary || type;
  }

  if (type.startsWith('game.')) {
    const message = pickString(d.message, 32);
    const scene = pickString(d.scene, 14);
    return scene ? `${message} @ ${scene}` : message || type;
  }

  if (type.startsWith('live.')) {
    const session = pickString(d.live_session_id, 16);
    const platform = pickString(d.platform, 10);
    if (platform && session) return `${platform} · ${session}`;
    return session || platform || type;
  }

  if (type.startsWith('core.')) {
    const message = pickString(d.message, 24) || pickString(d.event, 16);
    return message || type;
  }

  return type;
}

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return `${text.slice(0, maxLen)}…`;
}

function pickString(value: unknown, maxLen = 24): string {
  if (typeof value !== 'string' || value.length === 0) return '';
  return truncate(value, maxLen);
}
