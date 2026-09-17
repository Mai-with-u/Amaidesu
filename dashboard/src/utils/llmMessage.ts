/**
 * LLM 请求历史消息的展示提取工具（仅适配中立契约新格式）。
 *
 * 库内消息为中立契约形状：`{role, parts, tool_calls, tool_call_id}`，
 * parts 片段为字符串或 `{type:"text",text}` / `{type:"image",image}`；
 * tool_calls 为扁平 `{id, name, arguments}`，arguments 已是对象。
 */

export interface PreviewToolCall {
  id?: string;
  name: string;
  /** arguments 统一为字符串（对象形态 JSON 序列化），展示层自行美化 */
  arguments: string;
}

export interface PreviewMessage {
  role: string;
  /** 正文文本；图像片段不进正文，以占位符呈现，避免 base64 灌入页面 */
  content: string;
  toolCallId?: string;
  toolCalls: PreviewToolCall[];
}

type RawRecord = Record<string, unknown>;

/** 单条消息正文：拼接 parts 文本片段，图像片段替换为占位符 */
export function messageText(message: RawRecord | undefined | null): string {
  if (!message || !Array.isArray(message.parts)) return '';
  const lines: string[] = [];
  for (const piece of message.parts) {
    if (typeof piece === 'string') {
      lines.push(piece);
    } else if (piece && typeof piece === 'object') {
      const part = piece as RawRecord;
      if (typeof part.text === 'string') {
        lines.push(part.text);
      } else if (part.type === 'image') {
        lines.push('[图片内容已省略]');
      }
    }
  }
  return lines.join('\n');
}

function normalizeToolCall(raw: RawRecord): PreviewToolCall {
  return {
    id: typeof raw.id === 'string' ? raw.id : undefined,
    name: typeof raw.name === 'string' ? raw.name : 'unknown',
    arguments:
      typeof raw.arguments === 'string'
        ? raw.arguments
        : JSON.stringify(raw.arguments ?? {}, null, 2),
  };
}

/** 库内消息记录 → 展示用统一形状；非对象输入退化为空正文 unknown 角色 */
export function normalizeMessage(raw: unknown): PreviewMessage {
  const message = (raw && typeof raw === 'object' ? raw : {}) as RawRecord;
  const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  return {
    role: typeof message.role === 'string' ? message.role : 'unknown',
    content: messageText(message),
    toolCallId: typeof message.tool_call_id === 'string' ? message.tool_call_id : undefined,
    toolCalls: toolCalls.map(normalizeToolCall),
  };
}
