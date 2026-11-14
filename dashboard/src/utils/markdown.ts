/**
 * Markdown 渲染（marked + DOMPurify）
 *
 * 技能正文、更新日志、LLM 历史消息等后端给出的 Markdown 文本的统一渲染出口：
 * marked 解析（GFM 表格与任务清单、单换行成段），DOMPurify 净化后返回可信 HTML。
 * 调用方经 v-html 挂载（需配 eslint-disable vue/no-v-html），样式由调用方
 * 按 `.markdown-body :deep(...)` 自行接。
 */

import DOMPurify from 'dompurify';
import { marked } from 'marked';

marked.setOptions({ gfm: true, breaks: true });

/** 渲染 Markdown 文本为净化后的 HTML；空输入返回空串（不产出空 <p>） */
export function renderMarkdown(text: string): string {
  if (!text.trim()) return '';
  const html = marked.parse(text, { async: false });
  return DOMPurify.sanitize(typeof html === 'string' ? html : '');
}
