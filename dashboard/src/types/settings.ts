/**
 * Settings 页面类型定义
 */

// 字段类型枚举
export enum ConfigFieldType {
  STRING = 'string',
  INTEGER = 'integer',
  FLOAT = 'float',
  BOOLEAN = 'boolean',
  SELECT = 'select',
  ARRAY = 'array',
  OBJECT = 'object',
}

// 验证规则
export interface ValidationRule {
  min?: number;
  max?: number;
  min_length?: number;
  max_length?: number;
  pattern?: string;
  options?: string[];
}

// 配置字段 Schema
export interface ConfigFieldSchema {
  key: string;
  label: string;
  description?: string;
  type: ConfigFieldType;
  default?: unknown;
  value?: unknown;
  validation?: ValidationRule;
  properties?: Record<string, ConfigFieldSchema>;
  items?: ConfigFieldSchema;
  children?: ConfigFieldSchema[];
  required: boolean;
  sensitive: boolean;
  group?: string;
  readonly?: boolean;
}

// 配置分组 Schema
export interface ConfigGroupSchema {
  key: string;
  label: string;
  description?: string;
  icon?: string;
  fields: ConfigFieldSchema[];
  order: number;
  file_name?: string;
  file_label?: string;
}

// 配置 Schema 响应
export interface ConfigSchemaResponse {
  groups: ConfigGroupSchema[];
  version: string;
}

// 配置更新响应
export interface ConfigUpdateResponse {
  success: boolean;
  message: string;
  requires_restart?: boolean;
}

// 批量配置更新（与后端 POST /api/v1/config/batch 对齐）：单次请求携带多条变更，
// 全部成功才落盘；任一校验失败整批回退，磁盘零写入。
export interface ConfigBatchChange {
  key: string;
  value: unknown;
}

export interface ConfigBatchUpdateRequest {
  changes: ConfigBatchChange[];
}

export interface ConfigBatchChangeResult {
  key: string;
  success: boolean;
}

export interface ConfigBatchChangeError {
  key: string;
  message: string;
}

export interface ConfigBatchUpdateResponse {
  success: boolean;
  message: string;
  requires_restart?: boolean;
  results?: ConfigBatchChangeResult[];
  errors?: ConfigBatchChangeError[];
}

// 待保存的变更
export interface PendingChange {
  key: string;
  oldValue: unknown;
  newValue: unknown;
  field: ConfigFieldSchema;
}

// 表单状态
export interface FormState {
  isDirty: boolean;
  isValid: boolean;
  errors: Record<string, string>;
  pendingChanges: PendingChange[];
}
