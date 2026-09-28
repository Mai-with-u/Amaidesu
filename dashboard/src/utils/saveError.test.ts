import { describe, expect, it } from 'vitest';
import { attributeValidationError } from './saveError';
import type { PendingChange } from '@/types/settings';

function change(key: string): PendingChange {
  return { key, oldValue: null, newValue: null, field: { key, label: key } as never };
}

// 真实 422 报文样例（agents.agents.enabled 提交非法值，2026-09-28 冒烟取证）
const SAMPLE_422 = `配置校验失败 [agents.toml] Schema 校验失败: 1 validation error for AgentsConfig
enabled.1
  Input should be 'streamer', 'minecraft' or 'text_adv' [type=literal_error, input_value='bogus_agent', input_type=str]
    For further information visit https://errors.pydantic.dev/2.12/v/literal_error`;

describe('attributeValidationError', () => {
  it('按叶子字段名归属到待保存变更的 key', () => {
    const changes = [
      change('agents.agents.enabled'),
      change('agents.agents.streamer.persona.bot_name'),
    ];
    const [err] = attributeValidationError(SAMPLE_422, changes);
    expect(err.key).toBe('agents.agents.enabled');
  });

  it('loc 的数字索引段不参与匹配', () => {
    // loc 形如 items.1.name 时叶子名取 name
    const detail = `配置校验失败 [model.toml] Schema 校验失败: 1 validation error for ModelConfig
items.1.name
  Input should be a valid string [type=string_type, input_value=123]`;
    const changes = [change('model.llm_models')];
    const [err] = attributeValidationError(detail, changes);
    // 叶子名 name 与 llm_models 不匹配 → 无 key，仅消息
    expect(err.key).toBe('');
    expect(err.message).toContain('Input should be a valid string');
  });

  it('匹配不到变更时不带 key，消息保留且剥离 pydantic 链接行', () => {
    const [err] = attributeValidationError(SAMPLE_422, []);
    expect(err.key).toBe('');
    expect(err.message).not.toContain('errors.pydantic.dev');
    expect(err.message).toContain("Input should be 'streamer', 'minecraft' or 'text_adv'");
  });

  it('无可解析 loc 时返回不带 key 的原始消息', () => {
    const [err] = attributeValidationError('配置校验失败 [infra.toml] 完全无法解析的消息', []);
    expect(err.key).toBe('');
    expect(err.message).toBe('配置校验失败 [infra.toml] 完全无法解析的消息');
  });

  it('同名字段取首个待保存变更', () => {
    const changes = [change('infra.dashboard.port'), change('tools.tools.studio.obs.port')];
    const [err] = attributeValidationError(
      `配置校验失败 [infra.toml] Schema 校验失败: 1 validation error for InfraConfig
port
  Input should be a valid integer`,
      changes,
    );
    expect(err.key).toBe('infra.dashboard.port');
  });
});
