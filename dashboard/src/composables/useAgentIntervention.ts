/**
 * 干预发话的目标 → 模式 → 传输单一事实源：直播控制台与 Agent 页共用。
 *
 * 目标分三类：
 * - 主播（STREAMER_AGENT_NAME）：三种场控操作（注入弹幕 / 强制回应 / 幕后提醒），
 *   模式定义与传输全部复用 useInterventionBar，不在本文件复制第二份；
 * - minecraft（MINECRAFT_AGENT_NAME）：递话（prompt）/ 委派（delegate）两模式，
 *   走 agents 域 REST 接口；
 * - 其余目标（adv 等）：不支持收消息，模式为空数组——页面按空集合隐藏输入条。
 */
import { computed, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { agentsApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { useInterventionBar } from '@/composables/live/useInterventionBar';

/** 主播 Agent 注册名（干预输入条的主播目标取值；与 useInterventionBar 内部常量同源同值） */
export const STREAMER_AGENT_NAME = 'streamer';

/** 游戏 Agent 注册名（干预输入条的游戏目标取值；与后端 Agent 注册名一致） */
export const MINECRAFT_AGENT_NAME = 'minecraft';

/** 干预发话模式定义（与 InterventionInput 的 modes prop 形状一致） */
export interface AgentSendMode {
  key: string;
  label: string;
  /** 模式说明：下拉选项与输入条 title 共用 */
  desc: string;
  placeholder: string;
}

/** minecraft 目标的两种发话模式（文案沿用 Agents 页既有定义，不重写） */
const MINECRAFT_SEND_MODES: AgentSendMode[] = [
  {
    key: 'prompt',
    label: '递话',
    desc: '纯文本留言（插话/提醒）：不派新任务——任务执行中下一步吸收，挂起中被唤醒',
    placeholder: '给该 Agent 的留言（不派新任务）',
  },
  {
    key: 'delegate',
    label: '委派',
    desc: '派一项新工作：登记任务账本并送达目标，受理回执任务号，任务卡在此可见',
    placeholder: '工作指令（自然语言：目标与约束，不规定步骤）',
  },
];

export function useAgentIntervention(options: {
  /** 发送成功后复位输入条（提交给 InterventionInput.settle 的转发） */
  settle: () => Promise<void>;
  /** 委派受理成功后的页面回调（如刷新任务板） */
  onDelegated?: (target: string, taskId: string) => void;
}) {
  const bar = useInterventionBar(options);

  /** minecraft 目标发送的在途标记（主播目标的在途由内部 bar 的 sending 承载） */
  const minecraftSending = ref(false);

  /** 模式集合随目标切换：主播三模式 / minecraft 两模式 / 其余目标不支持 */
  function modesForTarget(target: string): AgentSendMode[] {
    if (target === STREAMER_AGENT_NAME) return bar.SEND_MODES;
    if (target === MINECRAFT_AGENT_NAME) return MINECRAFT_SEND_MODES;
    return [];
  }

  /** 在途状态合并：输入条只认一个 sending——主播走内部 bar，minecraft 走本地标记 */
  const sending = computed(() => bar.sending.value || minecraftSending.value);

  async function sendToTarget(target: string, modeKey: string, text: string): Promise<void> {
    if (target === STREAMER_AGENT_NAME) {
      await bar.onInterventionSend(modeKey, text);
      return;
    }
    if (target !== MINECRAFT_AGENT_NAME) return;
    if (minecraftSending.value) return;
    if (!text) {
      ElMessage.warning(modeKey === 'delegate' ? '请填写工作指令' : '请填写留言内容');
      return;
    }
    minecraftSending.value = true;
    try {
      if (modeKey === 'delegate') {
        const res = await agentsApi.delegateAgent(target, text);
        ElMessage.success(`已受理（任务号 ${res.data.task_id}）`);
        options.onDelegated?.(target, res.data.task_id);
      } else {
        await agentsApi.promptAgent(target, text);
        ElMessage.success('已递话——执行中的任务下一步会吸收，挂起中的会被唤醒');
      }
      await options.settle();
    } catch (error) {
      ElMessage.error(getApiErrorMessage(error, modeKey === 'delegate' ? '委派失败' : '递话失败'));
    } finally {
      minecraftSending.value = false;
    }
  }

  return {
    /** 主播目标的工具项状态（弹幕观众昵称 / 强制回应在途计数），页面插槽渲染用 */
    activeMode: bar.activeMode,
    injectNickname: bar.injectNickname,
    forcePending: bar.forcePending,
    onModeChange: bar.onModeChange,
    modesForTarget,
    sendToTarget,
    sending,
  };
}
