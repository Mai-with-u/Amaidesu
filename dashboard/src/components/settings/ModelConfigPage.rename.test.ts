// @vitest-environment happy-dom
// 模型注册表的改名流程守护
//
// 用户报的现象：模型配置页改不了新加模型的名称。根因是名称输入框只给值、
// 不接更新事件——Element Plus 每次击键后都会把输入框里的文字重置回原值，
// 击键留不住，失焦时交出去的也是旧值，改名等于没发生。
//
// 这里按页面真实流程走一遍：＋添加模型 → 改名称 → 名称进配置。

import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';
import ElementPlus from 'element-plus';
import { ConfigFieldType, type ConfigFieldSchema } from '@/types/settings';
import ModelConfigPage from './ModelConfigPage.vue';

vi.mock('@/api', () => ({
  llmApi: { getEmbeddingStatus: vi.fn().mockResolvedValue({ ready: false, text: '未启用' }) },
}));

vi.mock('@/utils/confirmAction', () => ({
  confirmAction: vi.fn().mockResolvedValue(true),
}));

const MODEL_ITEM_FIELDS: ConfigFieldSchema[] = [
  {
    key: 'name',
    label: '模型名称',
    type: ConfigFieldType.STRING,
    default: 'default',
    required: true,
    sensitive: false,
  },
  {
    key: 'model_identifier',
    label: '模型标识',
    type: ConfigFieldType.STRING,
    default: 'gpt-4o-mini',
    required: false,
    sensitive: false,
  },
  {
    key: 'api_provider',
    label: '所属提供商',
    type: ConfigFieldType.STRING,
    default: 'default',
    required: false,
    sensitive: false,
  },
  {
    key: 'price_in',
    label: '输入单价',
    type: ConfigFieldType.FLOAT,
    default: 0,
    required: false,
    sensitive: false,
  },
];

const PROVIDER_ITEM_FIELDS: ConfigFieldSchema[] = [
  {
    key: 'name',
    label: '提供商名称',
    type: ConfigFieldType.STRING,
    default: 'default',
    required: true,
    sensitive: false,
  },
  {
    key: 'base_url',
    label: 'API 端点',
    type: ConfigFieldType.STRING,
    default: '',
    required: false,
    sensitive: false,
  },
];

const fields: ConfigFieldSchema[] = [
  {
    key: 'model.llm_providers',
    label: '提供商',
    type: ConfigFieldType.ARRAY,
    required: false,
    sensitive: false,
    items: {
      key: 'item',
      label: '提供商',
      type: ConfigFieldType.OBJECT,
      required: false,
      sensitive: false,
      fields: PROVIDER_ITEM_FIELDS,
    },
  },
  {
    key: 'model.llm_models',
    label: '模型',
    type: ConfigFieldType.ARRAY,
    required: false,
    sensitive: false,
    items: {
      key: 'item',
      label: '模型',
      type: ConfigFieldType.OBJECT,
      required: false,
      sensitive: false,
      fields: MODEL_ITEM_FIELDS,
    },
  },
  {
    key: 'model.llm_profiles',
    label: '用途',
    type: ConfigFieldType.OBJECT,
    required: false,
    sensitive: false,
    children: [
      {
        key: 'model.llm_profiles.planner',
        label: 'planner',
        type: ConfigFieldType.OBJECT,
        required: false,
        sensitive: false,
        children: [
          {
            key: 'model.llm_profiles.planner.model_list',
            label: '模型名单',
            type: ConfigFieldType.ARRAY,
            default: [],
            required: false,
            sensitive: false,
          },
        ],
      },
    ],
  },
];

/** 假配置存储：与页面真实存储同构（点分路径寻址、数组整值写回） */
function createStore() {
  const store = reactive({
    model: {
      llm_providers: [{ name: 'sheep', base_url: 'https://llm.example.com' }],
      llm_models: [
        {
          name: 'deepseek-flash',
          model_identifier: 'deepseek-flash',
          api_provider: 'sheep',
          price_in: 1,
        },
      ],
      llm_profiles: {
        planner: { model_list: ['deepseek-flash'] },
      },
    },
  });

  function getValue(key: string): unknown {
    return key
      .split('.')
      .reduce<unknown>(
        (acc, part) =>
          acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined,
        store,
      );
  }

  function updateValue(field: ConfigFieldSchema, value: unknown): void {
    const parts = field.key.split('.');
    let node = store as unknown as Record<string, unknown>;
    for (const part of parts.slice(0, -1)) {
      node = node[part] as Record<string, unknown>;
    }
    node[parts[parts.length - 1]] = value;
  }

  return { store, getValue, updateValue };
}

function mountPage() {
  const { store, getValue, updateValue } = createStore();
  const wrapper = mount(ModelConfigPage, {
    props: {
      fields,
      getValue,
      updateValue,
      getOriginal: () => undefined,
      getChangeCount: () => 0,
      showAdvanced: false,
      detailKey: null,
    },
    global: { plugins: [ElementPlus] },
  });
  return { wrapper, store };
}

/** 详情形态里的名称输入框 */
function nameInput(wrapper: ReturnType<typeof mountPage>['wrapper']) {
  return wrapper.find('.entity-detail .name-field input');
}

describe('模型注册表改名', () => {
  it('新加模型后跳进详情，改名称写回配置', async () => {
    const { wrapper, store } = mountPage();

    // ＋添加模型 → 数组追加并发出 navigate（设置壳据其把 detailKey 切到新条目）
    const addButton = wrapper.findAll('.zone-foot button').find(b => b.text().includes('添加模型'));
    expect(addButton).toBeDefined();
    await addButton!.trigger('click');
    await wrapper.vm.$nextTick();

    expect(store.model.llm_models).toHaveLength(2);
    expect(store.model.llm_models[1].name).toBe('new-model');
    const nav = wrapper.emitted('navigate')?.at(-1)?.[0] as string;
    expect(nav).toBe('model.llm_models@new-model');

    await wrapper.setProps({ detailKey: nav });
    await wrapper.vm.$nextTick();

    const input = nameInput(wrapper);
    expect(input.exists()).toBe(true);

    // 击键：文字要留住（Element Plus 单向绑定会把每次击键重置回原值）
    (input.element as HTMLInputElement).value = 'Mai-Replier';
    await input.trigger('input');
    expect((input.element as HTMLInputElement).value).toBe('Mai-Replier');

    // 失焦：名称写回配置，并发出 navigate 指向新名（详情键按名字寻址，壳随之跟进）
    await input.trigger('blur');
    await wrapper.vm.$nextTick();

    expect(store.model.llm_models[1].name).toBe('Mai-Replier');
    const renamedNav = wrapper.emitted('navigate')?.at(-1)?.[0] as string;
    expect(renamedNav).toBe('model.llm_models@Mai-Replier');
    await wrapper.setProps({ detailKey: renamedNav });
    await wrapper.vm.$nextTick();
    // 详情形态里名字只在输入框 value 中（不在文本节点），读元素值断言
    const renamedInput = nameInput(wrapper);
    expect((renamedInput.element as HTMLInputElement).value).toBe('Mai-Replier');
  });

  it('名称清空时拒绝写入并保留原名', async () => {
    const { wrapper, store } = mountPage();

    const addButton = wrapper.findAll('.zone-foot button').find(b => b.text().includes('添加模型'));
    await addButton!.trigger('click');
    await wrapper.vm.$nextTick();
    const nav = wrapper.emitted('navigate')?.at(-1)?.[0] as string;
    await wrapper.setProps({ detailKey: nav });
    await wrapper.vm.$nextTick();

    const input = nameInput(wrapper);
    (input.element as HTMLInputElement).value = '';
    await input.trigger('input');
    await input.trigger('blur');
    await wrapper.vm.$nextTick();

    expect(store.model.llm_models[1].name).toBe('new-model');
  });
});
