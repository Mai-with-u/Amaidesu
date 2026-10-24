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

/**
 * 递归遍历字段树收集搜索命中：children 是树形分组，properties 是字典式键值
 * （来自 object 类型）。命中即收集；seen 在「全组」粒度按 key 去重——已命中的
 * key 不重复收集，但其子级分支仍要继续遍历（子级有各自命中的字段）。
 */
export function collectMatchingFields(
  fields: ConfigFieldSchema[],
  q: string,
  seen: Set<string>,
  out: ConfigFieldSchema[],
): void {
  for (const f of fields) {
    if (!seen.has(f.key) && fieldMatches(f, q)) {
      seen.add(f.key);
      out.push(f);
    }
    if (f.children && f.children.length > 0) {
      collectMatchingFields(f.children, q, seen, out);
    }
    if (f.properties) {
      collectMatchingFields(Object.values(f.properties), q, seen, out);
    }
  }
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
