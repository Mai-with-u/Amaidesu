// 配置字段渲染的纯逻辑：值校验、控件形态判定、占位文案、定长元组拆合。
// 从 FieldRenderer.vue / ArrayEditor.vue 抽出，供单元测试；只依赖字段 Schema
// 与值本身，不做任何组件状态或 store 访问。

import type { ConfigFieldSchema } from '@/types/settings';

/** 必填或五类约束（min/max/min_length/max_length/pattern）校验，通过返回 null，失败返回提示文案 */
export function validateValue(field: ConfigFieldSchema, value: unknown): string | null {
  // 必填检查
  if (field.required && (value === null || value === undefined || value === '')) {
    return '此字段为必填项';
  }

  if (field.validation) {
    const { min, max, min_length, max_length, pattern } = field.validation;

    if (field.type === 'integer' || field.type === 'float') {
      if (min !== undefined && (value as number) < min) {
        return `最小值为 ${min}`;
      }
      if (max !== undefined && (value as number) > max) {
        return `最大值为 ${max}`;
      }
    }

    if (field.type === 'string') {
      const str = value as string;
      if (min_length !== undefined && str.length < min_length) {
        return `最少 ${min_length} 个字符`;
      }
      if (max_length !== undefined && str.length > max_length) {
        return `最多 ${max_length} 个字符`;
      }
      if (pattern) {
        const regex = new RegExp(pattern);
        if (!regex.test(str)) {
          return '格式不正确';
        }
      }
    }
  }

  return null;
}

/**
 * 是否用文本域渲染长文本：显式 widget 声明优先（textarea / input）；无声明按值
 * 形态判断——描述长度与输入形态无关（短枚举值配长描述会被误判成文本域）。
 */
export function isLongTextOf(field: ConfigFieldSchema, value: unknown): boolean {
  if (field.widget === 'textarea') return true;
  if (field.widget === 'input') return false;
  if (field.type !== 'string') return false;
  const text = String(value ?? '');
  return text.includes('\n') || text.length > 80 || (field.validation?.max_length ?? 0) > 100;
}

/** 占位提示：默认值加「默认」前缀，与已填值区分；敏感字段与缺省默认值不展示 */
export function defaultPlaceholder(field: ConfigFieldSchema): string {
  if (field.sensitive) return '';
  const d = field.default;
  if (d === null || d === undefined || d === '') return '';
  return `默认 ${String(d)}`;
}

/** 定长元组拆分：数组值对齐标记长度，缺位/非数值以 undefined 占位（输入框留空） */
export function tupleSplit(value: unknown, length: number): (number | undefined)[] {
  const arr = Array.isArray(value) ? value : [];
  return Array.from({ length }, (_, i) =>
    typeof arr[i] === 'number' ? (arr[i] as number) : undefined,
  );
}

/** 定长元组合并：全填齐才产出数值数组，任一空缺回退 null（= 未指定） */
export function tupleJoin(values: (number | undefined)[]): number[] | null {
  const complete = values.every(v => typeof v === 'number');
  return complete ? values.map(v => v as number) : null;
}

/** 已修改判定：与原实现保持一致的 JSON 序列化比较（键序敏感，非深比较） */
export function isEqualJSON(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** 数组/对象项新增时的类型默认值表（未知类型回退空串） */
export function getDefaultValueForType(type?: string): unknown {
  switch (type) {
    case 'string':
      return '';
    case 'integer':
    case 'float':
      return 0;
    case 'boolean':
      return false;
    case 'object':
      return {};
    case 'array':
      return [];
    default:
      return '';
  }
}
