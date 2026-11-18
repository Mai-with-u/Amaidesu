// @vitest-environment happy-dom
// 小部件管理页测试
//
// 覆盖：表单值收敛（coerceValue）各类型分支、脏判断（isModelDirty）、
// 状态文案（statusTextFor）分支、danmaku 表单字段构成（有 font_size、
// 无 min_importance、无 enabled 行），以及组件级行为——头部启用开关与
// 状态文案联动、keep-alive 重进页面时未保存改动的确认保护。

import { mount, type VueWrapper } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue';
import ElementPlus from 'element-plus';
import { createPinia, PiniaVuePlugin } from 'pinia';

import Widgets, {
  coerceValue,
  FIELD_DEFS,
  isModelDirty,
  statusTextFor,
  type WidgetFieldDef,
} from './Widgets.vue';
import { configApi } from '@/api';
import { confirmAction } from '@/utils/confirmAction';

vi.mock('@/api', () => ({
  configApi: {
    getConfig: vi.fn(),
    saveBatch: vi.fn(),
    restart: vi.fn(),
  },
}));

vi.mock('@/utils/confirmAction', () => ({
  confirmAction: vi.fn(),
}));

const CONFIG = {
  dashboard: {
    danmaku_widget: {
      enabled: true,
      show_danmaku: true,
      font_size: 20,
      max_messages: 40,
      min_importance: 0.7,
    },
    subtitle_widget: {
      enabled: false,
      font_size: 40,
      font_color: '#ffffff',
      background_color: 'rgba(0,0,0,0.5)',
      border_color: '#ff8800',
      position: 'top',
    },
    game2048_widget: { enabled: true },
  },
};

function mountPage(): VueWrapper {
  return mount(Widgets, {
    global: {
      plugins: [ElementPlus, PiniaVuePlugin, createPinia()],
    },
  });
}

/** KeepAlive 宿主：toggle() 在挂载/卸载间切换 Widgets 页，触发 onActivated */
function mountKeepAliveHost(): { wrapper: VueWrapper; toggle: () => void } {
  const show = ref(true);
  const Host = defineComponent({
    setup() {
      return () => h(KeepAlive, () => (show.value ? h(Widgets) : h('div', '占位页')));
    },
  });
  const wrapper = mount(Host, {
    global: {
      plugins: [ElementPlus, PiniaVuePlugin, createPinia()],
    },
  });
  return {
    wrapper,
    toggle: (): void => {
      show.value = !show.value;
    },
  };
}

async function flushRenders(): Promise<void> {
  await nextTick();
  await nextTick();
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(configApi.getConfig).mockResolvedValue({
    data: { config: CONFIG },
  } as never);
  vi.mocked(configApi.saveBatch).mockResolvedValue({
    data: { success: true, message: '', requires_restart: false },
  } as never);
  vi.mocked(confirmAction).mockResolvedValue(true);
});

describe('coerceValue', () => {
  const switchDef: WidgetFieldDef = { key: 'a', label: '', type: 'switch', fallback: false };
  const numberDef: WidgetFieldDef = { key: 'b', label: '', type: 'number', fallback: 16 };
  const textDef: WidgetFieldDef = { key: 'c', label: '', type: 'text', fallback: '#ffffff' };

  it('switch 只认布尔 true，其余一律 false', () => {
    expect(coerceValue(switchDef, true)).toBe(true);
    expect(coerceValue(switchDef, false)).toBe(false);
    expect(coerceValue(switchDef, 'true')).toBe(false);
    expect(coerceValue(switchDef, undefined)).toBe(false);
  });

  it('number 只认数字，缺项与非数字回退默认值', () => {
    expect(coerceValue(numberDef, 20)).toBe(20);
    expect(coerceValue(numberDef, undefined)).toBe(16);
    expect(coerceValue(numberDef, '20')).toBe(16);
  });

  it('text 收非空字符串，空串与缺项回退默认值', () => {
    expect(coerceValue(textDef, '#000000')).toBe('#000000');
    expect(coerceValue(textDef, '')).toBe('#ffffff');
    expect(coerceValue(textDef, undefined)).toBe('#ffffff');
  });
});

describe('isModelDirty', () => {
  it('模型与快照不一致为脏，一致为净', () => {
    const model = { font_size: 20 };
    const snapshot = JSON.stringify(model);
    expect(isModelDirty(model, snapshot)).toBe(false);
    expect(isModelDirty({ font_size: 24 }, snapshot)).toBe(true);
  });

  it('无模型或无快照（尚未加载/读取失败）视为不脏', () => {
    expect(isModelDirty(undefined, '{}')).toBe(false);
    expect(isModelDirty({ font_size: 20 }, undefined)).toBe(false);
  });
});

describe('statusTextFor', () => {
  it('未知/启用/停用三种文案，停用提示指向本页开关', () => {
    expect(statusTextFor(undefined)).toBe('状态未知');
    expect(statusTextFor(true)).toBe('已启用');
    expect(statusTextFor(false)).toBe('已停用（开启后保存并重启生效）');
  });
});

describe('FIELD_DEFS 表单字段构成', () => {
  it('danmaku 表单有 font_size（约束对齐后端 Schema），无 min_importance 与 enabled 行', () => {
    const keys = FIELD_DEFS.danmaku!.map(f => f.key);
    expect(keys).toContain('font_size');
    expect(keys).not.toContain('min_importance');
    expect(keys).not.toContain('enabled');
    const fontSize = FIELD_DEFS.danmaku!.find(f => f.key === 'font_size')!;
    expect(fontSize.min).toBe(10);
    expect(fontSize.max).toBe(72);
    expect(fontSize.fallback).toBe(16);
  });

  it('subtitle 表单无 enabled 行，2048 无表单字段（只有头部开关）', () => {
    expect(FIELD_DEFS.subtitle!.map(f => f.key)).not.toContain('enabled');
    expect(FIELD_DEFS.game2048).toEqual([]);
  });
});

describe('页面组件', () => {
  it('详情头部渲染启用开关，弹幕表单有字号无最小重要性', async () => {
    const wrapper = mountPage();
    await flushRenders();
    // 头部开关存在（表单字段表里没有 enabled 行，开关只在标题行）
    const header = wrapper.find('.detail-header');
    expect(header.find('.el-switch').exists()).toBe(true);
    const labels = wrapper.findAll('.form-label').map(l => l.text());
    expect(labels).toContain('弹幕字号');
    expect(labels).not.toContain('最小重要性');
    expect(labels).not.toContain('启用小部件');
  });

  it('已启用时状态文案为已启用；切到停用的小部件提示本页开关可开', async () => {
    const wrapper = mountPage();
    await flushRenders();
    expect(wrapper.find('.status-line').text()).toBe('已启用');
    await wrapper.findAll('.widget-item')[1]!.trigger('click');
    expect(wrapper.find('.status-line').text()).toBe('已停用（开启后保存并重启生效）');
  });

  it('预览卡渲染 iframe，来源是带 preview=1 的嵌入页网址', async () => {
    const wrapper = mountPage();
    await flushRenders();
    const frame = wrapper.find('iframe.preview-frame');
    expect(frame.exists()).toBe(true);
    expect(frame.attributes('src')).toContain('/widget/danmaku?preview=1');
  });

  it('拨头部开关产生未保存改动，保存时把 enabled 写进整段写回值', async () => {
    const wrapper = mountPage();
    await flushRenders();
    expect(wrapper.find('.dirty-hint').exists()).toBe(false);
    await wrapper.find('.detail-header .el-switch').trigger('click');
    await nextTick();
    expect(wrapper.find('.dirty-hint').exists()).toBe(true);
    await wrapper.find('.form-actions button').trigger('click');
    await nextTick();
    expect(configApi.saveBatch).toHaveBeenCalledTimes(1);
    const change = vi.mocked(configApi.saveBatch).mock.calls[0]![0]![0]!;
    expect(change.key).toBe('infra.dashboard.danmaku_widget');
    expect((change.value as Record<string, unknown>).enabled).toBe(false);
  });

  it('2048 无表单字段但仍有配置区与保存按钮（保存头部开关）', async () => {
    const wrapper = mountPage();
    await flushRenders();
    await wrapper.findAll('.widget-item')[3]!.trigger('click');
    expect(wrapper.find('.form-grid').exists()).toBe(false);
    expect(wrapper.find('.form-actions button').exists()).toBe(true);
  });

  // 首次挂载即命中 onMounted + onActivated（Vue 的 KeepAlive 首挂也触发 activated），
  // 基线是 2 次读取；重进页面的增量由守卫逻辑决定
  it('keep-alive 重进页面：无脏改动时静默重读磁盘', async () => {
    const { toggle } = mountKeepAliveHost();
    await flushRenders();
    expect(configApi.getConfig).toHaveBeenCalledTimes(2);
    toggle();
    await nextTick();
    toggle();
    await flushRenders();
    expect(confirmAction).not.toHaveBeenCalled();
    expect(configApi.getConfig).toHaveBeenCalledTimes(3);
  });

  it('keep-alive 重进页面：确认取消则保留脏现场不重读', async () => {
    const { wrapper, toggle } = mountKeepAliveHost();
    await flushRenders();
    await wrapper.find('.detail-header .el-switch').trigger('click');
    vi.mocked(confirmAction).mockResolvedValue(false);
    toggle();
    await nextTick();
    toggle();
    await flushRenders();
    expect(confirmAction).toHaveBeenCalledTimes(1);
    expect(configApi.getConfig).toHaveBeenCalledTimes(2);
    // 现场（开关草稿）未被动过：重新出现且仍是脏态
    expect(wrapper.find('.dirty-hint').exists()).toBe(true);
  });

  it('keep-alive 重进页面：确认放弃改动则重新读取', async () => {
    const { wrapper, toggle } = mountKeepAliveHost();
    await flushRenders();
    await wrapper.find('.detail-header .el-switch').trigger('click');
    vi.mocked(confirmAction).mockResolvedValue(true);
    toggle();
    await nextTick();
    toggle();
    await flushRenders();
    expect(configApi.getConfig).toHaveBeenCalledTimes(3);
  });
});
