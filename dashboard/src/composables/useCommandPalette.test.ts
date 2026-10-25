// useCommandPalette.ts 导演命令面板组合式的纯逻辑测试（node 环境，不挂载组件）
//
// 覆盖：中文关键词过滤（标签 + 补充关键词、不区分大小写）、按注册表顺序分组、
// 热键状态机（Ctrl/Cmd+K 呼出、Esc 关闭、上下循环选择、回车执行、再按呼出键关闭）、
// 默认动作注册表的执行映射（每个动作调到哪个 API、输入/确认交互的分支语义）。

import { effectScope, nextTick, type EffectScope } from 'vue';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ElMessage } from 'element-plus';
import type { AxiosResponse } from 'axios';
import { agentsApi, rundownApi, streamerApi, tasksApi } from '@/api';
import {
  createDefaultActions,
  useCommandPalette,
  type CommandAction,
  type CommandRunContext,
} from '@/composables/useCommandPalette';

vi.mock('@/api', async importOriginal => ({
  ...(await importOriginal<object>()),
  streamerApi: {
    getStatus: vi.fn(),
    toggleProactive: vi.fn(),
    testDecision: vi.fn(),
    triggerProactive: vi.fn(),
  },
  rundownApi: {
    getState: vi.fn(),
    control: vi.fn(),
  },
  tasksApi: { listTasks: vi.fn() },
  agentsApi: {
    promptAgent: vi.fn(),
    cancelAgentTask: vi.fn(),
  },
}));

// 组合式顶层 import element-plus（ElMessage / ElMessageBox），node 环境整体替身；
// 输入/确认交互在动作里经 ctx 注入，测试用脚本化 fakeCtx，不真走弹窗
vi.mock('element-plus', () => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { prompt: vi.fn() },
}));

vi.mock('@/utils/confirmAction', () => ({ confirmAction: vi.fn() }));
import { confirmAction } from '@/utils/confirmAction';

const messageMock = vi.mocked(ElMessage);
const confirmMock = vi.mocked(confirmAction);

const triggerProactiveMock = vi.mocked(streamerApi.triggerProactive);
const getStatusMock = vi.mocked(streamerApi.getStatus);
const toggleProactiveMock = vi.mocked(streamerApi.toggleProactive);
const testDecisionMock = vi.mocked(streamerApi.testDecision);
const rundownControlMock = vi.mocked(rundownApi.control);
const rundownGetStateMock = vi.mocked(rundownApi.getState);
const listTasksMock = vi.mocked(tasksApi.listTasks);
const promptAgentMock = vi.mocked(agentsApi.promptAgent);
const cancelAgentTaskMock = vi.mocked(agentsApi.cancelAgentTask);

/** axios 风格响应假体（各 API 都只读 data 字段） */
function resp<T>(data: T): AxiosResponse<T> {
  return { data } as unknown as AxiosResponse<T>;
}

/** 键盘事件假体（只需 key 与修饰位；preventDefault 记录调用） */
function keyEvent(key: string, mods: { ctrl?: boolean; meta?: boolean } = {}) {
  return {
    key,
    ctrlKey: mods.ctrl ?? false,
    metaKey: mods.meta ?? false,
    preventDefault: vi.fn(),
  };
}

/** 脚本化执行上下文：input 依次弹出自备队列，confirm 返回预设值 */
function fakeCtx(
  inputs: string[] = [],
  confirmed = true,
  navigate: (path: string) => void = vi.fn(),
): CommandRunContext {
  const queue = [...inputs];
  return {
    input: async () => queue.shift() ?? null,
    confirm: async () => confirmed,
    navigate,
  };
}

let scope: EffectScope;
/** 每用例独立的组合式实例（visible/query 等状态互不串扰） */
function makePalette(navigate: (path: string) => void = vi.fn()) {
  let instance: ReturnType<typeof useCommandPalette> | undefined;
  scope.run(() => {
    instance = useCommandPalette({ actions: createDefaultActions(), navigate });
  });
  return instance!;
}

function actionByKey(key: string): CommandAction {
  const action = createDefaultActions().find(item => item.key === key);
  expect(action, `动作 ${key} 应存在于默认注册表`).toBeTruthy();
  return action!;
}

beforeEach(() => {
  scope = effectScope();
  vi.clearAllMocks();
});

afterEach(() => {
  scope.stop();
});

describe('关键词过滤与分组', () => {
  it('空关键词返回全部动作并按注册表顺序分组', () => {
    const palette = makePalette();
    expect(palette.flatActions.value.length).toBe(createDefaultActions().length);
    expect(palette.groupedActions.value.map(group => group.group)).toEqual([
      '主播',
      '流程单',
      '任务',
      '导航',
    ]);
  });

  it('标签包含匹配（中文、不区分大小写）', () => {
    const palette = makePalette();
    palette.query.value = '催一下主播';
    const labels = palette.flatActions.value.map(item => item.label);
    expect(labels).toContain('催一下主播主动发言');
    expect(labels).not.toContain('暂停流程单');
  });

  it('补充关键词参与匹配（英文别名，大小写不敏感）', () => {
    const palette = makePalette();
    palette.query.value = 'RUNDOWN';
    const matched = palette.flatActions.value;
    expect(matched.length).toBeGreaterThan(0);
    // "rundown" 命中流程单组全部与导航组的编排页入口，不应混入主播/任务组
    expect(matched.every(item => item.group === '流程单' || item.group === '导航')).toBe(true);
    expect(matched.some(item => item.group === '流程单')).toBe(true);
  });

  it('无命中时列表为空', () => {
    const palette = makePalette();
    palette.query.value = '不存在的动作';
    expect(palette.flatActions.value).toHaveLength(0);
  });
});

describe('热键状态机', () => {
  it('Ctrl+K / Cmd+K 呼出，Esc 关闭', () => {
    const palette = makePalette();
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    expect(palette.visible.value).toBe(true);
    palette.onKeydown(keyEvent('Escape'));
    expect(palette.visible.value).toBe(false);
    palette.onKeydown(keyEvent('k', { meta: true }));
    expect(palette.visible.value).toBe(true);
    palette.onKeydown(keyEvent('Escape'));
    expect(palette.visible.value).toBe(false);
  });

  it('未打开时不响应方向键与回车', () => {
    const palette = makePalette();
    palette.onKeydown(keyEvent('ArrowDown'));
    palette.onKeydown(keyEvent('Enter'));
    expect(palette.visible.value).toBe(false);
    expect(triggerProactiveMock).not.toHaveBeenCalled();
  });

  it('打开时再按呼出键关闭，普通按键不改变可见性', () => {
    const palette = makePalette();
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    expect(palette.visible.value).toBe(false);
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    palette.onKeydown(keyEvent('a'));
    expect(palette.visible.value).toBe(true);
  });

  it('上下键在列表内循环选择', () => {
    const palette = makePalette();
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    expect(palette.activeIndex.value).toBe(0);
    palette.onKeydown(keyEvent('ArrowUp'));
    expect(palette.activeIndex.value).toBe(palette.flatActions.value.length - 1);
    palette.onKeydown(keyEvent('ArrowDown'));
    expect(palette.activeIndex.value).toBe(0);
    palette.onKeydown(keyEvent('ArrowDown'));
    expect(palette.activeIndex.value).toBe(1);
  });

  it('打开时清空上一轮搜索与选择，回车执行当前选中动作', async () => {
    triggerProactiveMock.mockResolvedValue(resp({ success: true, message: 'ok' }));
    const palette = makePalette();
    palette.query.value = '流程单'; // 上一轮残留
    palette.onKeydown(keyEvent('k', { ctrl: true }));
    await nextTick(); // 打开时清空残留走 watch，nextTick 后生效
    expect(palette.query.value).toBe('');
    expect(palette.activeIndex.value).toBe(0);
    palette.onKeydown(keyEvent('Enter'));
    await vi.waitFor(() => expect(triggerProactiveMock).toHaveBeenCalledTimes(1));
    expect(palette.visible.value).toBe(false);
  });
});

describe('主播组动作映射', () => {
  it('催一下主动发言 → trigger-proactive，按 success 分支提示', async () => {
    triggerProactiveMock.mockResolvedValue(resp({ success: true, message: '已递话头' }));
    await actionByKey('streamer.trigger-proactive').run(fakeCtx());
    expect(triggerProactiveMock).toHaveBeenCalledWith({});
    expect(messageMock.success).toHaveBeenCalledWith('已递话头');

    triggerProactiveMock.mockResolvedValue(resp({ success: false, message: '主播不在' }));
    await actionByKey('streamer.trigger-proactive').run(fakeCtx());
    expect(messageMock.error).toHaveBeenCalledWith('主播不在');
  });

  it('主动发言开关：先取当前态，取反后 toggle 并按后端回执提示', async () => {
    getStatusMock.mockResolvedValue(
      resp({ available: true, config: { proactive_enabled: false }, statistics: {} }),
    );
    toggleProactiveMock.mockResolvedValue(resp({ enabled: true, message: '已打开' }));
    await actionByKey('streamer.toggle-proactive').run(fakeCtx());
    expect(toggleProactiveMock).toHaveBeenCalledWith({ enabled: true });
    expect(messageMock.success).toHaveBeenCalledWith('已打开');
  });

  it('强制回应 → test-decision（后台驱动，按是否回应分支提示）', async () => {
    testDecisionMock.mockResolvedValue(resp({ success: true, plan: { should_reply: true } }));
    await actionByKey('streamer.force-reply').run(fakeCtx());
    expect(testDecisionMock).toHaveBeenCalledWith({ forced: true, proactive: true });
    expect(messageMock.success).toHaveBeenCalled();

    testDecisionMock.mockResolvedValue(resp({ success: true, plan: { should_reply: false } }));
    await actionByKey('streamer.force-reply').run(fakeCtx());
    expect(messageMock.info).toHaveBeenCalled();
  });

  it('幕后提醒：输入后递给主播 agent', async () => {
    promptAgentMock.mockResolvedValue(resp({ delivered: true }));
    await actionByKey('streamer.prompt').run(fakeCtx(['该聊聊礼物了']));
    expect(promptAgentMock).toHaveBeenCalledWith('streamer', '该聊聊礼物了');
    expect(messageMock.success).toHaveBeenCalled();
  });

  it('幕后提醒：取消输入则不发送', async () => {
    await actionByKey('streamer.prompt').run(fakeCtx([]));
    expect(promptAgentMock).not.toHaveBeenCalled();
  });
});

describe('流程单组动作映射', () => {
  it.each([
    ['rundown.pause', 'pause', '流程单已暂停'],
    ['rundown.resume', 'resume', '流程单已继续'],
    ['rundown.next', 'next', '已切到下一环节'],
  ] as const)('%s → control(%s)', async (key, action, successText) => {
    rundownControlMock.mockResolvedValue(resp({ success: true, message: '', snapshot: null }));
    await actionByKey(key).run(fakeCtx());
    expect(rundownControlMock).toHaveBeenCalledWith({ action });
    expect(messageMock.success).toHaveBeenCalledWith(successText);
  });

  it('控制被拒（success=false）时按后端 message 提示', async () => {
    rundownControlMock.mockResolvedValue(
      resp({ success: false, message: '当前不在运行中', snapshot: null }),
    );
    await actionByKey('rundown.pause').run(fakeCtx());
    expect(messageMock.error).toHaveBeenCalledWith('当前不在运行中');
  });

  it('跳转指定环节：序号换算 segment_id 后 goto', async () => {
    rundownGetStateMock.mockResolvedValue(
      resp({
        available: true,
        message: null,
        snapshot: null,
        transitions: [],
        segments: [
          {
            id: 'seg_a',
            title: '开场',
            task_description: '',
            expected_ms: 1,
            min_duration_ms: null,
            notes: null,
          },
          {
            id: 'seg_b',
            title: '闲聊',
            task_description: '',
            expected_ms: 1,
            min_duration_ms: null,
            notes: null,
          },
        ],
        config: { rundown_id: 'r1' },
      }),
    );
    rundownControlMock.mockResolvedValue(resp({ success: true, message: '', snapshot: null }));
    await actionByKey('rundown.goto').run(fakeCtx(['2']));
    expect(rundownControlMock).toHaveBeenCalledWith({ action: 'goto', segment_id: 'seg_b' });
    expect(messageMock.success).toHaveBeenCalledWith('已跳到「闲聊」');
  });

  it('跳转序号越界或非法时提示且不发起控制', async () => {
    rundownGetStateMock.mockResolvedValue(
      resp({
        available: true,
        message: null,
        snapshot: null,
        transitions: [],
        segments: [],
        config: { rundown_id: 'r1' },
      }),
    );
    await actionByKey('rundown.goto').run(fakeCtx(['9']));
    await actionByKey('rundown.goto').run(fakeCtx(['abc']));
    expect(rundownControlMock).not.toHaveBeenCalled();
    expect(messageMock.warning).toHaveBeenCalledTimes(2);
  });
});

describe('任务组动作映射', () => {
  function snapshot(running: { task_id: string; instruction: string; owner_agent: string }[]) {
    return {
      running: running.map(task => ({
        ...task,
        status: 'running',
        initiator: 'operator',
        executor: task.owner_agent,
        summary: '',
        created_at_ms: 1,
        updated_at_ms: 2,
      })),
      finished: [],
    };
  }

  it('取消任务：唯一命中 + 确认 → cancelAgentTask(owner)，且动作标记危险', async () => {
    listTasksMock.mockResolvedValue(
      resp(snapshot([{ task_id: 't1', instruction: '整理歌单', owner_agent: 'streamer' }])),
    );
    confirmMock.mockResolvedValue(true);
    cancelAgentTaskMock.mockResolvedValue(resp({ cancelled: true }));

    const action = actionByKey('tasks.cancel');
    expect(action.danger).toBe(true);
    await action.run(fakeCtx(['歌单'], true));

    expect(cancelAgentTaskMock).toHaveBeenCalledWith('streamer', 't1');
    expect(messageMock.success).toHaveBeenCalledWith('已取消该任务');
  });

  it('取消任务：确认拒绝则不动 API', async () => {
    listTasksMock.mockResolvedValue(
      resp(snapshot([{ task_id: 't1', instruction: '整理歌单', owner_agent: 'streamer' }])),
    );
    await actionByKey('tasks.cancel').run(fakeCtx(['t1'], false));
    expect(cancelAgentTaskMock).not.toHaveBeenCalled();
  });

  it('取消任务：零命中或多命中都不发起取消', async () => {
    listTasksMock.mockResolvedValue(
      resp(
        snapshot([
          { task_id: 't1', instruction: '整理歌单', owner_agent: 'streamer' },
          { task_id: 't2', instruction: '整理房间', owner_agent: 'streamer' },
        ]),
      ),
    );
    await actionByKey('tasks.cancel').run(fakeCtx(['整理']));
    expect(messageMock.warning).toHaveBeenCalled();
    expect(cancelAgentTaskMock).not.toHaveBeenCalled();

    await actionByKey('tasks.cancel').run(fakeCtx(['不存在']));
    expect(messageMock.warning).toHaveBeenCalledTimes(2);
    expect(cancelAgentTaskMock).not.toHaveBeenCalled();
  });

  it('催办任务：唯一命中后对 owner 发可改的预置提醒文案', async () => {
    listTasksMock.mockResolvedValue(
      resp(snapshot([{ task_id: 't7', instruction: '打扫直播间', owner_agent: 'minecraft' }])),
    );
    promptAgentMock.mockResolvedValue(resp({ delivered: true }));

    await actionByKey('tasks.urge').run(fakeCtx(['t7', '快点干完']));

    expect(promptAgentMock).toHaveBeenCalledWith('minecraft', '快点干完');
    expect(messageMock.success).toHaveBeenCalled();
  });
});

describe('导航组动作映射', () => {
  it('每个导航动作跳到对应路由（key 的路径段）', async () => {
    const navActions = createDefaultActions().filter(item => item.group === '导航');
    expect(navActions.length).toBeGreaterThanOrEqual(10);
    for (const action of navActions) {
      const navigate = vi.fn();
      const path = action.key.replace('nav.', '');
      await action.run(fakeCtx([], true, navigate));
      expect(navigate).toHaveBeenCalledWith(path);
    }
  });
});

describe('执行异常路径', () => {
  it('动作抛错时以错误消息提示而非向上抛', async () => {
    const palette = makePalette();
    const failing: CommandAction = {
      key: 'test.fail',
      label: '会失败的动作',
      group: '主播',
      run: async () => {
        throw new Error('网络炸了');
      },
    };
    await expect(palette.execute(failing)).resolves.toBeUndefined();
    expect(messageMock.error).toHaveBeenCalled();
  });
});
