/**
 * 导演命令面板：把散落在各页面的常用干预动作收进一个键盘呼出的命令列表，
 * 供场控在不离开当前页面的前提下"说一句就办"。本组合式只负责动作注册表、
 * 关键词过滤与键盘选择的状态机；界面渲染归 CommandPalette.vue。
 *
 * keep-alive 生命周期语义：热键监听归组件层（CommandPalette.vue）持有——
 * 面板挂在 App.vue（keep-alive 之外），监听随组件 mounted/unmounted 增删，
 * 本组合式不注册任何全局监听，因此无 deactivated 泄漏问题；onKeydown 是
 * 纯状态机，测试与组件层可分别驱动。
 */

import { computed, ref, watch } from 'vue';
import { ElMessage, ElMessageBox } from 'element-plus';
import { getApiErrorMessage } from '@/utils/apiError';
import { confirmAction } from '@/utils/confirmAction';
import { agentsApi, rundownApi, streamerApi, tasksApi } from '@/api';

/** 幕后提醒的目标：命令面板的主播组动作都递给她 */
const STREAMER_AGENT_NAME = 'streamer';

/** 键盘事件最小形状（组件层传 window KeyboardEvent，测试传假体，均可） */
export interface PaletteKeyEvent {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  /** 阻止默认行为（呼出键与方向键会触发浏览器快捷行为，须拦下） */
  preventDefault(): void;
}

/** 动作执行前需要的文本输入请求（title 为弹窗标题，initial 为输入框预填文案） */
export interface CommandInputRequest {
  title: string;
  placeholder: string;
  initial?: string;
}

/** 传给动作执行函数的交互能力集合（由组件层装配，测试可替换） */
export interface CommandRunContext {
  /** 弹文本输入框；用户取消时 resolve null */
  input: (request: CommandInputRequest) => Promise<string | null>;
  /** 弹确认框；resolve true 表示用户确认 */
  confirm: (message: string, title: string) => Promise<boolean>;
  /** 页面跳转（导航组动作专用；组件层包 router.push） */
  navigate: (path: string) => void;
}

/** 注册表中的一个动作：标签面向场控用"动作 + 对象"措辞，禁系统术语 */
export interface CommandAction {
  key: string;
  label: string;
  /** 分组名（主播 / 流程单 / 任务 / 导航），同组相邻展示 */
  group: string;
  /** 参与关键词匹配的补充词（如路由路径、英文别名），不展示 */
  keywords?: string;
  /** 危险动作：执行前必须过 confirm（渲染层可据此标红） */
  danger?: boolean;
  run: (ctx: CommandRunContext) => Promise<void>;
}

/** 过滤结果的一个分组 */
export interface CommandGroupView {
  group: string;
  items: CommandAction[];
}

/** 默认输入/确认交互：走 element-plus 弹窗与统一确认 helper（navigate 由调用方补齐） */
function defaultRunContext(): Omit<CommandRunContext, 'navigate'> {
  return {
    input: async (request: CommandInputRequest) => {
      try {
        const { value } = await ElMessageBox.prompt(request.placeholder, request.title, {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          inputValue: request.initial ?? '',
        });
        return value;
      } catch {
        return null;
      }
    },
    confirm: (message: string, title: string) => confirmAction(message, title),
  };
}

/** 驱动一次流程单控制并按后端回执提示（success=false 时 message 带拒绝原因） */
async function runRundownControl(action: 'pause' | 'resume' | 'next'): Promise<void> {
  const response = await rundownApi.control({ action });
  if (response.data.success) {
    ElMessage.success(
      action === 'pause' ? '流程单已暂停' : action === 'resume' ? '流程单已继续' : '已切到下一环节',
    );
  } else {
    ElMessage.error(response.data.message || '流程单控制未生效');
  }
}

/** 按任务编号或说明关键词在"进行中"账本里找唯一任务；找不到/多个命中时提示并返回 null */
async function findRunningTask(keyword: string): Promise<{
  task_id: string;
  instruction: string;
  owner_agent: string;
} | null> {
  const snapshot = await tasksApi.listTasks();
  const needle = keyword.trim().toLowerCase();
  const matches = snapshot.data.running.filter(
    task =>
      task.task_id.toLowerCase().includes(needle) ||
      task.instruction.toLowerCase().includes(needle),
  );
  if (matches.length === 0) {
    ElMessage.warning('没有找到匹配的进行中任务——可换说明里的关键词，或去任务页看任务编号');
    return null;
  }
  if (matches.length > 1) {
    ElMessage.warning(`找到 ${matches.length} 个匹配任务，请换更精确的关键词（如完整任务编号）`);
    return null;
  }
  return matches[0];
}

/** 默认动作注册表：全部复用 api/index.ts 既有函数，一个都不新增 */
export function createDefaultActions(): CommandAction[] {
  return [
    // —— 主播组 ——
    {
      key: 'streamer.trigger-proactive',
      label: '催一下主播主动发言',
      group: '主播',
      keywords: 'proactive trigger 主动',
      run: async () => {
        const response = await streamerApi.triggerProactive({});
        if (response.data.success) {
          ElMessage.success(response.data.message || '已递上话头——主播经限流判定后开口');
        } else {
          ElMessage.error(response.data.message || '催促未生效');
        }
      },
    },
    {
      key: 'streamer.toggle-proactive',
      label: '打开或关掉主播主动发言',
      group: '主播',
      keywords: 'proactive toggle 开关',
      run: async () => {
        const status = await streamerApi.getStatus();
        const current = status.data.config?.proactive_enabled ?? false;
        const response = await streamerApi.toggleProactive({ enabled: !current });
        ElMessage.success(
          response.data.message || `主动发言已${response.data.enabled ? '打开' : '关掉'}`,
        );
      },
    },
    {
      key: 'streamer.force-reply',
      label: '让主播立刻回应当前话题',
      group: '主播',
      keywords: 'force reply 强制回应',
      run: async () => {
        // 与干预输入条一致：留空 = 主播自由发挥，后台执行，结果落时间线决策卡
        const response = await streamerApi.testDecision({ forced: true, proactive: true });
        if (response.data.success) {
          if (response.data.plan?.should_reply) {
            ElMessage.success('主播已回应（详见时间线决策卡）');
          } else {
            ElMessage.info('本轮主播未回应（详见时间线决策卡）');
          }
        } else {
          ElMessage.error(response.data.message || '回应未执行成功');
        }
      },
    },
    {
      key: 'streamer.prompt',
      label: '给主播递一句幕后提醒',
      group: '主播',
      keywords: 'prompt 提醒 递话',
      run: async ctx => {
        const text = await ctx.input({
          title: '给主播的幕后提醒',
          placeholder: '想提醒主播的话（必达送达）',
        });
        if (!text) return;
        await agentsApi.promptAgent(STREAMER_AGENT_NAME, text);
        ElMessage.success('已递到主播手里——下个决策窗她一定看到');
      },
    },
    // —— 流程单组 ——
    {
      key: 'rundown.pause',
      label: '暂停流程单',
      group: '流程单',
      keywords: 'rundown pause',
      run: async () => runRundownControl('pause'),
    },
    {
      key: 'rundown.resume',
      label: '继续流程单',
      group: '流程单',
      keywords: 'rundown resume',
      run: async () => runRundownControl('resume'),
    },
    {
      key: 'rundown.next',
      label: '流程单切到下一环节',
      group: '流程单',
      keywords: 'rundown next',
      run: async () => runRundownControl('next'),
    },
    {
      key: 'rundown.goto',
      label: '流程单跳到指定环节',
      group: '流程单',
      keywords: 'rundown goto 跳转',
      run: async ctx => {
        const raw = await ctx.input({
          title: '跳到第几个环节？',
          placeholder: '输入环节序号（从 1 数起）',
        });
        if (!raw) return;
        const seq = Number.parseInt(raw.trim(), 10);
        if (!Number.isInteger(seq) || seq < 1) {
          ElMessage.warning('请输入从 1 数起的环节序号');
          return;
        }
        const state = await rundownApi.getState();
        const segment = state.data.segments[seq - 1];
        if (!segment) {
          ElMessage.warning(`当前流程单共 ${state.data.segments.length} 个环节，没有第 ${seq} 个`);
          return;
        }
        const response = await rundownApi.control({ action: 'goto', segment_id: segment.id });
        if (response.data.success) {
          ElMessage.success(`已跳到「${segment.title}」`);
        } else {
          ElMessage.error(response.data.message || '跳转未生效');
        }
      },
    },
    // —— 任务组 ——
    {
      key: 'tasks.cancel',
      label: '取消一个进行中的任务',
      group: '任务',
      keywords: 'cancel task 取消',
      danger: true,
      run: async ctx => {
        const keyword = await ctx.input({
          title: '取消哪个任务？',
          placeholder: '输入任务编号，或任务说明里的关键词',
        });
        if (!keyword) return;
        const task = await findRunningTask(keyword);
        if (!task) return;
        const confirmed = await ctx.confirm(
          `确定取消任务「${task.instruction}」（${task.task_id}）吗？任务会被立刻停手，已做的部分不会回退。`,
          '取消任务',
        );
        if (!confirmed) return;
        await agentsApi.cancelAgentTask(task.owner_agent, task.task_id);
        ElMessage.success('已取消该任务');
      },
    },
    {
      key: 'tasks.urge',
      label: '催一下执行任务的 Agent',
      group: '任务',
      keywords: 'urge task 催办',
      run: async ctx => {
        const keyword = await ctx.input({
          title: '催办哪个任务？',
          placeholder: '输入任务编号，或任务说明里的关键词',
          initial: '请加快推进手头正在做的任务；如有阻塞，说明卡在哪里。',
        });
        if (!keyword) return;
        const task = await findRunningTask(keyword);
        if (!task) return;
        // 关键词与催办文案合并在同一次输入里不够清晰：再取一次输入专门编辑催办文案
        const text = await ctx.input({
          title: `催办「${task.instruction}」——递给 ${task.owner_agent} 的话`,
          placeholder: '催办文案（会作为幕后提醒必达送达）',
          initial: '请加快推进手头正在做的任务；如有阻塞，说明卡在哪里。',
        });
        if (!text) return;
        await agentsApi.promptAgent(task.owner_agent, text);
        ElMessage.success(`已把催办递给 ${task.owner_agent}`);
      },
    },
    // —— 导航组（标签与路径对齐 router/index.ts）——
    ...(
      [
        { label: '去总览页', path: '/', keywords: 'dashboard 首页 总览' },
        { label: '去直播控制台', path: '/live', keywords: 'live 直播' },
        { label: '去 Agent 一览页', path: '/agents', keywords: 'agents' },
        { label: '去采集器页', path: '/collectors', keywords: 'collectors' },
        { label: '去工具页', path: '/tools', keywords: 'tools' },
        { label: '去记忆页', path: '/memory', keywords: 'memory 记忆' },
        { label: '去观众页', path: '/viewers', keywords: 'viewers 观众' },
        { label: '去事件页', path: '/eventlog', keywords: 'event 事件' },
        { label: '去日志页', path: '/logs', keywords: 'logs 日志' },
        { label: '去 LLM 用量页', path: '/llm/usage', keywords: 'llm usage 用量' },
        { label: '去流程单编排页', path: '/outline', keywords: 'outline rundown 编排' },
        { label: '去设置页', path: '/settings', keywords: 'settings 设置' },
      ] satisfies { label: string; path: string; keywords: string }[]
    ).map(
      (entry): CommandAction => ({
        key: `nav.${entry.path}`,
        label: entry.label,
        group: '导航',
        keywords: entry.keywords,
        run: async ctx => {
          ctx.navigate(entry.path);
        },
      }),
    ),
  ];
}

export function useCommandPalette(options: {
  /** 动作注册表（组件层传入默认表 + navigate 能力，测试传假体） */
  actions: CommandAction[];
  /** 页面跳转能力（组件层包 router.push，测试可断言调用） */
  navigate: (path: string) => void;
}) {
  const { actions, navigate } = options;

  const visible = ref(false);
  const query = ref('');
  /** 键盘选择的下标（指向过滤后的扁平列表） */
  const activeIndex = ref(0);

  /** 执行上下文：默认弹窗交互 + 页面跳转（导航动作需要） */
  const runContext: CommandRunContext = {
    ...defaultRunContext(),
    navigate,
  };

  // 关键词过滤：对标签与补充关键词做不区分大小写的包含匹配
  const filteredActions = computed<CommandAction[]>(() => {
    const needle = query.value.trim().toLowerCase();
    if (!needle) return actions;
    return actions.filter(
      action =>
        action.label.toLowerCase().includes(needle) ||
        (action.keywords ?? '').toLowerCase().includes(needle),
    );
  });

  /** 按注册表顺序分组展示（组名只在首个成员出现时建立） */
  const groupedActions = computed<CommandGroupView[]>(() => {
    const groups: CommandGroupView[] = [];
    for (const action of filteredActions.value) {
      const last = groups[groups.length - 1];
      if (last && last.group === action.group) {
        last.items.push(action);
      } else {
        groups.push({ group: action.group, items: [action] });
      }
    }
    return groups;
  });

  /** 扁平列表：键盘上下移动与回车执行都按这个顺序 */
  const flatActions = computed<CommandAction[]>(() =>
    groupedActions.value.flatMap(group => group.items),
  );

  function open(): void {
    visible.value = true;
  }

  function close(): void {
    visible.value = false;
  }

  function toggle(): void {
    visible.value = !visible.value;
  }

  // 打开时清空上一轮的搜索与选择，避免残留状态迷惑下一轮
  watch(visible, isOpen => {
    if (isOpen) {
      query.value = '';
      activeIndex.value = 0;
    }
  });

  async function execute(action: CommandAction): Promise<void> {
    close();
    try {
      await action.run(runContext);
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, '动作执行失败'));
    }
  }

  /**
   * 全局键盘状态机：组件层把 window keydown 原样转发进来。
   * 未打开时只认 Ctrl/Cmd+K 呼出；打开后处理 Esc 关闭、上下选择、回车执行，
   * 其余按键（含输入框打字）放行给输入框的 v-model。
   */
  function onKeydown(event: PaletteKeyEvent): void {
    const isToggleKey = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k';
    if (!visible.value) {
      if (isToggleKey) {
        event.preventDefault();
        open();
      }
      return;
    }
    const items = flatActions.value;
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      if (items.length > 0) activeIndex.value = (activeIndex.value + 1) % items.length;
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (items.length > 0) {
        activeIndex.value = (activeIndex.value - 1 + items.length) % items.length;
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const action = items[activeIndex.value];
      if (action) void execute(action);
    } else if (isToggleKey) {
      event.preventDefault();
      close();
    }
  }

  return {
    visible,
    query,
    activeIndex,
    filteredActions,
    groupedActions,
    flatActions,
    open,
    close,
    toggle,
    execute,
    onKeydown,
  };
}
