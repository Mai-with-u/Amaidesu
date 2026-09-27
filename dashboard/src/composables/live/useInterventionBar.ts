/**
 * 干预输入条的状态与传输：三个发送模式 = 你以"幕后场控"身份对直播间的
 * 三种操作（注入弹幕 / 强制回应 / 幕后提醒）。
 * 输入交互归共享组件 InterventionInput，这里持有模式与发送传输；
 * 发送完成后的输入框复位经 settle 回调交还页面（组件实例归页面持有）。
 */

import { ref } from 'vue';
import { ElMessage } from 'element-plus';
import { agentsApi, debugApi, streamerApi } from '@/api';

/** 主播 Agent 注册名：干预输入条面向的就是她（递话通道按名寻址） */
const STREAMER_AGENT_NAME = 'streamer';

export interface SendMode {
  key: 'danmaku' | 'force' | 'nudge';
  label: string;
  /** 模式说明：下拉选项与输入条下方提示共用 */
  desc: string;
  placeholder: string;
}

const SEND_MODES: SendMode[] = [
  {
    key: 'danmaku',
    label: '注入弹幕',
    desc: '假装一名观众发弹幕，走与真实弹幕完全相同的链路——主播自然反应，可能要等几秒、也可能不理你',
    placeholder: '弹幕内容（观众昵称在下方填写，可选）',
  },
  {
    key: 'force',
    label: '强制回应',
    desc: '不排队不限流：把文字直接交给主播立即开跑，结果落时间线决策卡；留空则主播自由发挥',
    placeholder: '给主播的文字（立即开跑；留空 = 主播自由发挥）',
  },
  {
    key: 'nudge',
    label: '幕后提醒',
    desc: '把话直接递给主播：文字必达（进她下个决策的参考材料），她会被提前唤醒来看——说不说、怎么说由她自己定',
    placeholder: '给主播的提醒（必达送达）',
  },
];

export function useInterventionBar(options: { settle: () => Promise<void> }) {
  const { settle } = options;

  const activeMode = ref<SendMode['key']>('danmaku');

  function onModeChange(modeKey: string): void {
    if (modeKey === 'danmaku' || modeKey === 'force' || modeKey === 'nudge') {
      activeMode.value = modeKey;
    }
  }

  const sending = ref(false);
  /** 注入弹幕模式的观众昵称（可选，跨发送保留——方便扮演同一位观众连发） */
  const injectNickname = ref('');
  /** 在途的强制回应决策轮数：后台执行期间在状态 chip 上显示"主播正在想…" */
  const forcePending = ref(0);

  async function onInterventionSend(modeKey: string, text: string): Promise<void> {
    if (sending.value) return;
    if (modeKey === 'danmaku' && !text) {
      ElMessage.warning('请填写弹幕内容');
      return;
    }
    if (modeKey === 'nudge' && !text) {
      ElMessage.warning('请填写提醒内容（必达递话需要说明提醒什么）');
      return;
    }
    sending.value = true;
    try {
      if (modeKey === 'danmaku') {
        const response = await debugApi.injectMessage({
          source: injectNickname.value.trim() || '测试观众',
          text,
        });
        if (response.data.success) {
          ElMessage.success('已注入——主播自然反应中，可能要等、也可能不理');
        } else {
          ElMessage.error(response.data.error || '注入失败');
          return;
        }
      } else if (modeKey === 'force') {
        // 后台执行：决策可能耗时数十秒，不等返回——输入框立即可继续用，
        // 在途状态由 forcePending chip 承载，结果落时间线决策卡
        const payload = {
          batch: text ? [{ nickname: '调试观众', text }] : undefined,
          forced: true,
          proactive: text ? undefined : true,
        };
        forcePending.value += 1;
        void streamerApi
          .testDecision(payload)
          .then(response => {
            if (response.data.success) {
              const error = response.data.error ?? null;
              if (error) {
                ElMessage.warning(`决策轮已结束：${error}（详见时间线决策卡）`);
              } else if (response.data.plan?.should_reply) {
                ElMessage.success('已回应（详见时间线决策卡）');
              } else {
                ElMessage.info('本轮未回应（详见时间线决策卡）');
              }
            } else {
              ElMessage.error(response.data.message || '测试执行失败');
            }
          })
          .catch((error: unknown) => {
            ElMessage.error(error instanceof Error ? error.message : '测试执行失败');
          })
          .finally(() => {
            forcePending.value = Math.max(0, forcePending.value - 1);
          });
      } else {
        // 幕后提醒 → 运营递话通道：必达（进下个决策参考块 + 顺带敲门催醒）；
        // 纯催话/限流测试走 API（trigger-proactive），界面不再默认暴露
        await agentsApi.promptAgent(STREAMER_AGENT_NAME, text);
        ElMessage.success('已递到主播手里——下个决策窗她一定看到，并已顺带敲了敲门');
      }
      await settle();
    } catch (error) {
      ElMessage.error(extractHttpError(error, `${modeKey === 'nudge' ? '递话' : '发送'}失败`));
    } finally {
      sending.value = false;
    }
  }

  return {
    SEND_MODES,
    activeMode,
    sending,
    injectNickname,
    forcePending,
    onModeChange,
    onInterventionSend,
  };
}

/** 从 axios 错误中提取后端中文 detail（409 拒收 / 404 / 422 均为中文） */
function extractHttpError(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const data = (error as { response?: { data?: { detail?: unknown } } }).response?.data;
    if (data && typeof data.detail === 'string') return data.detail;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}
