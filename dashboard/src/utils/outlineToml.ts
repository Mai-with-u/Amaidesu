/**
 * 大纲 TOML 序列化工具
 *
 * 任务 13: Dashboard 在线编辑页 - 把编辑后的 StreamOutline 对象序列化为 TOML 文本。
 *
 * 设计决策
 * --------
 * - 不引入新依赖:前端无 toml 库,本工具只覆盖大纲数据所需的子集(string / integer / string-array / inline-table-array)。
 * - 字段顺序与 `data/outlines/live.toml` 一致,保持可读性。
 * - 与后端 `StreamOutline` Pydantic 模型字段名严格对齐,否则保存后解析失败。
 *
 * 限制
 * ----
 * - 字符串引号:基本 TOML 字面量字符串(`"..."`),含必要转义(`\n` / `\t` / `\\` / `\"`)。
 *   不支持多行字符串(`"""..."""`),因此 `task_description` 不会保留换行。
 * - 数值:`duration_ms` / `min_duration_ms` 用十进制整数。
 * - 不支持嵌套表(大纲数据扁平,无需)。
 */

import type { OutlineBranchData, OutlineSegmentData } from '@/types';

export interface OutlineForSerialize {
  outline_id: string;
  title: string;
  fallback_segment_id?: string | null;
  segments: OutlineSegmentData[];
}

/** 转义 TOML 基本字符串中的特殊字符 */
function escapeTomlString(s: string): string {
  return s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t');
}

/** 渲染 key_points 数组(多行,可读性优于 inline) */
function renderStringArray(items: string[]): string {
  if (!items || items.length === 0) return '[]';
  return '[\n' + items.map(k => `    "${escapeTomlString(k)}",`).join('\n') + '\n]';
}

/** 渲染 branches 数组(inline table,匹配 `data/outlines/gaming.toml` 风格) */
function renderBranches(branches: OutlineBranchData[]): string {
  if (!branches || branches.length === 0) return '[]';
  return branches
    .map(
      b =>
        `    { branch_id = "${escapeTomlString(b.branch_id)}", description = "${escapeTomlString(b.description)}", target_segment_id = "${escapeTomlString(b.target_segment_id)}" }`,
    )
    .join(',\n');
}

/** 序列化单个 segment 块 */
function renderSegment(seg: OutlineSegmentData): string {
  const lines: string[] = [];
  lines.push('[[segments]]');
  lines.push(`id = "${escapeTomlString(seg.id)}"`);
  lines.push(`title = "${escapeTomlString(seg.title)}"`);
  lines.push(`task_description = "${escapeTomlString(seg.task_description)}"`);
  lines.push(`duration_ms = ${Number(seg.duration_ms) || 0}`);
  if (seg.min_duration_ms != null && seg.min_duration_ms !== undefined) {
    lines.push(`min_duration_ms = ${Number(seg.min_duration_ms)}`);
  }
  if (seg.key_points && seg.key_points.length > 0) {
    lines.push('key_points = ' + renderStringArray(seg.key_points));
  }
  if (seg.branches && seg.branches.length > 0) {
    lines.push('branches = [\n' + renderBranches(seg.branches) + ',\n]');
  }
  return lines.join('\n');
}

/** 把 StreamOutline 对象序列化为 TOML 文本 */
export function serializeOutlineToToml(outline: OutlineForSerialize): string {
  const lines: string[] = [];
  lines.push(`outline_id = "${escapeTomlString(outline.outline_id)}"`);
  lines.push(`title = "${escapeTomlString(outline.title)}"`);
  if (outline.fallback_segment_id != null && outline.fallback_segment_id !== '') {
    lines.push(`fallback_segment_id = "${escapeTomlString(outline.fallback_segment_id)}"`);
  }
  for (const seg of outline.segments) {
    lines.push('');
    lines.push(renderSegment(seg));
  }
  // TOML 文件以换行符结尾
  return lines.join('\n') + '\n';
}
