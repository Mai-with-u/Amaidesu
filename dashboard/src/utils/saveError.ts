/**
 * 保存失败错误的 key 归属：把后端 Schema 校验拒绝（HTTP 422）的单条中文
 * 消息映射回前端可定位的字段 key，供设置页错误面板跳转使用。
 */
import type { PendingChange } from '@/types/settings';

/** 从 422 消息剥离噪声行，提取叶子字段名并归属到待保存变更的 key。 */
export function attributeValidationError(
  detail: string,
  changes: PendingChange[],
): { key: string; message: string }[] {
  const cleaned = detail
    .split('\n')
    .filter(line => !line.includes('errors.pydantic.dev'))
    .join('\n')
    .trim();
  const locMatch = cleaned.match(/validation error for \w+\s*\n+([^\n]+)\n/);
  const leaf = locMatch
    ? locMatch[1]
        .split('.')
        .filter(seg => !/^\d+$/.test(seg))
        .pop()
    : undefined;
  const keyed = leaf ? changes.find(c => c.key.split('.').pop() === leaf) : undefined;
  return [{ key: keyed?.key ?? '', message: cleaned }];
}
