import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const card = readFileSync(new URL('./ViewerIdentityCard.vue', import.meta.url), 'utf-8');
const list = readFileSync(new URL('../../views/Viewers.vue', import.meta.url), 'utf-8');

describe('观众隐私入口源码契约', () => {
  it('身份卡提供待删除撤回与带不记录名单选项的删除', () => {
    expect(card).toContain('forget_requested_at_ms');
    expect(card).toContain('viewersApi.retractForget');
    expect(card).toContain('viewersApi.remove(props.detail.viewer_id, alsoAddToForgetList.value)');
    expect(card).toContain("emit('deleted')");
  });

  it('观众列表标出待删除并支持解除不记录名单', () => {
    expect(list).toContain('待删除');
    expect(list).toContain('viewersApi.getForgetListCount');
    expect(list).toContain('viewersApi.removeFromForgetList');
  });
});
