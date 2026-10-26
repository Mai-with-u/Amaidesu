/**
 * LLM 请求历史消息的展示提取工具（仅适配中立契约新格式）。
 *
 * 库内消息为中立契约形状：`{role, parts, tool_calls, tool_call_id}`，
 * parts 片段为字符串或 `{type:"text",text}` / `{type:"image",image}`；
 * tool_calls 为扁平 `{id, name, arguments}`，arguments 已是对象。
 *
 * 图像片段以 `images` 数组单独透出（数据仅在内存中流转），正文不含
 * base64；组件侧点击后才把单张渲染成 `<img>`，列表与初始渲染不灌图。
 */

export interface PreviewToolCall {
  id?: string;
  name: string;
  /** arguments 统一为字符串（对象形态 JSON 序列化），展示层自行美化 */
  arguments: string;
}

export interface PreviewImage {
  /** data URL（缺前缀的裸 base64 补 png 前缀兜底）；点击后才渲染进 DOM */
  data: string;
  /** base64 解码后的估算字节数，供占位符标注大小 */
  sizeBytes: number;
}

export interface PreviewMessage {
  role: string;
  /** 正文文本；图像片段不进正文，经 images 以点击查看的占位符呈现 */
  content: string;
  /** 消息里的图像片段（按出现顺序）；空数组表示无图 */
  images: PreviewImage[];
  toolCallId?: string;
  toolCalls: PreviewToolCall[];
}

type RawRecord = Record<string, unknown>;

/** base64 长度估算解码后字节数（忽略 padding 误差，标注大小足够） */
export function estimateBase64Bytes(data: string): number {
  const commaIndex = data.indexOf(',');
  const payload = data.startsWith('data:') && commaIndex >= 0 ? data.slice(commaIndex + 1) : data;
  return Math.floor((payload.length * 3) / 4);
}

/** CJK 字符集：汉字 / 假名 / 谚文 / 全角标点，分词器里普遍约 1 字 1 token */
const CJK_CHAR_RE = /[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]/g;

/** 粗估文本 token 数。供应商只在响应里回报一次总用量，没有逐消息精确计数，
 *  这里按"中文约 1 字/token、其他约 4 字符/token"粗估，用于定位体积大头，非精确值 */
export function estimateTextTokens(text: string): number {
  if (!text) return 0;
  const cjkCount = (text.match(CJK_CHAR_RE) ?? []).length;
  return Math.max(1, Math.round(cjkCount + (text.length - cjkCount) / 4));
}

/** 单条消息的粗估 token 数：正文 + 工具调用名与参数（图像 token 随模型与分辨率浮动，不计） */
export function estimateMessageTokens(message: PreviewMessage): number {
  let total = estimateTextTokens(message.content);
  for (const tc of message.toolCalls) {
    total += estimateTextTokens(tc.name) + estimateTextTokens(tc.arguments);
  }
  return total;
}

/** 图像片段 → PreviewImage；裸 base64（无 data: 前缀）补 png 前缀兜底 */
function toPreviewImage(raw: unknown): PreviewImage | null {
  if (typeof raw !== 'string' || raw.length === 0) return null;
  const data = raw.startsWith('data:') ? raw : `data:image/png;base64,${raw}`;
  return { data, sizeBytes: estimateBase64Bytes(data) };
}

/** 单条消息正文：拼接 parts 文本片段，图像片段走 images 单独透出 */
function messageText(message: RawRecord | undefined | null): string {
  if (!message || !Array.isArray(message.parts)) return '';
  const lines: string[] = [];
  for (const piece of message.parts) {
    if (typeof piece === 'string') {
      lines.push(piece);
    } else if (piece && typeof piece === 'object') {
      const part = piece as RawRecord;
      if (typeof part.text === 'string') {
        lines.push(part.text);
      }
    }
  }
  return lines.join('\n');
}

/** 收集 parts 里的图像片段（`{type:"image", image}`），非法形态跳过 */
function messageImages(message: RawRecord | undefined | null): PreviewImage[] {
  if (!message || !Array.isArray(message.parts)) return [];
  const images: PreviewImage[] = [];
  for (const piece of message.parts) {
    if (piece && typeof piece === 'object' && (piece as RawRecord).type === 'image') {
      const image = toPreviewImage((piece as RawRecord).image);
      if (image) images.push(image);
    }
  }
  return images;
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
    images: messageImages(message),
    toolCallId: typeof message.tool_call_id === 'string' ? message.tool_call_id : undefined,
    toolCalls: toolCalls.map(normalizeToolCall),
  };
}
