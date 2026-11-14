// Settings 页 schema 树的纯逻辑：搜索命中收集、点路径值读写、字段树查找。
// 从 Settings.vue 抽出，供视图层复用与单元测试；此处不做任何 store 访问，
// 需要的状态（值树、分组列表）一律由参数显式传入。

import type { ConfigFieldSchema, ConfigGroupSchema } from '@/types/settings';

/** 字段是否命中搜索词：标签、key、描述任一包含即命中（大小写不敏感，q 已预转小写） */
export function fieldMatches(f: ConfigFieldSchema, q: string): boolean {
  return (
    f.label.toLowerCase().includes(q) ||
    f.key.toLowerCase().includes(q) ||
    (f.description?.toLowerCase().includes(q) ?? false)
  );
}

/** 搜索面板的单条命中：命中字段 + 所属文件 + 分组链（祖先容器 label，不含自身） */
export interface SearchHit {
  field: ConfigFieldSchema;
  fileKey: string;
  /** 祖先容器 label 链（如 ["主播 Agent", "上下文"]），按从外到内排序 */
  path: string[];
}

/** 在一棵字段树中收集命中及其分组链（path 由递归携带，调用方无需感知） */
function collectHits(
  fields: ConfigFieldSchema[],
  q: string,
  path: string[],
  fileKey: string,
  seen: Set<string>,
  out: SearchHit[],
): void {
  for (const f of fields) {
    if (f.children && f.children.length > 0) {
      collectHits(f.children, q, [...path, f.label || f.key], fileKey, seen, out);
    }
    if (f.properties) {
      collectHits(Object.values(f.properties), q, path, fileKey, seen, out);
    }
    if (f.items?.fields) {
      // 对象数组容器在 UI 中是一等卡片，其 label 计入分组链
      collectHits(f.items.fields, q, [...path, f.label || f.key], fileKey, seen, out);
    }
    // 叶子（与容器）都可命中；容器命中时不重复收集其子级命中链上已有的 key
    if (!seen.has(f.key) && fieldMatches(f, q)) {
      seen.add(f.key);
      out.push({ field: f, fileKey, path });
    }
  }
}

/**
 * 全局搜索：跨所有分组收集命中，产出搜索面板数据（字段 + 文件 + 分组链）。
 * limit 限制返回条数；超出部分丢弃并由调用方按 total 判断是否提示截断。
 */
export function buildSearchResults(
  groups: ConfigGroupSchema[],
  q: string,
  limit = 50,
): { hits: SearchHit[]; total: number } {
  const seen = new Set<string>();
  const hits: SearchHit[] = [];
  for (const group of groups) {
    collectHits(group.fields, q, [], group.file_name ?? '', seen, hits);
  }
  return { hits: hits.slice(0, limit), total: hits.length };
}

/** 按点路径从值树读取：任一中间层缺失返回 undefined */
export function findFieldValue(values: unknown, key: string): unknown {
  const keys = key.split('.');
  let current: unknown = values;
  for (const k of keys) {
    if (current && typeof current === 'object' && k in current) {
      current = (current as Record<string, unknown>)[k];
    } else {
      return undefined;
    }
  }
  return current;
}

/**
 * 不可变点路径写入：返回新值树，原对象各级都不被改写。中间层缺失时补空对象，
 * 与原视图实现保持一致（路径上出现非对象值也覆盖为空对象）。
 */
export function updateFieldValueImmutably(
  values: Record<string, unknown>,
  key: string,
  value: unknown,
): Record<string, unknown> {
  const keys = key.split('.');
  const newValues = { ...values };
  let current: Record<string, unknown> = newValues;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (!current[k] || typeof current[k] !== 'object') {
      current[k] = {};
    }
    current[k] = { ...(current[k] as Record<string, unknown>) };
    current = current[k] as Record<string, unknown>;
  }
  current[keys[keys.length - 1]] = value;
  return newValues;
}

/** 在 schema 全字段树中按 key 查找字段：children / properties / items.fields 三型下钻 */
export function findFieldInSchema(
  fields: ConfigFieldSchema[],
  key: string,
): ConfigFieldSchema | undefined {
  for (const f of fields) {
    if (f.key === key) return f;
    if (f.children) {
      const found = findFieldInSchema(f.children, key);
      if (found) return found;
    }
    if (f.properties) {
      const found = findFieldInSchema(Object.values(f.properties), key);
      if (found) return found;
    }
    if (f.items?.fields) {
      const found = findFieldInSchema(f.items.fields, key);
      if (found) return found;
    }
  }
  return undefined;
}

/** 字段是否为高级字段（x-ui-advanced）；找不到字段时按非高级处理 */
export function isAdvancedField(key: string, groups: ConfigGroupSchema[]): boolean {
  for (const group of groups) {
    const found = findFieldInSchema(group.fields, key);
    if (found) return found.advanced === true;
  }
  return false;
}
