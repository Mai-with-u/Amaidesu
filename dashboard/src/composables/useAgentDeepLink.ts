/**
 * Agent 页深链（``/agents?agent=<name>&round=<round_id>``）的解析与消费。
 *
 * 直播控制台时间线行点击跳入 Agent 页对应决策轮：URL 是唯一契约，
 * 拼装（agentRoundLocation）与解析（parseAgentDeepLink）都收敛在本文件。
 * round 缺失 = 只选中 Agent 不定位高亮；round 过期/不存在由页面静默降级。
 */
import { useRoute, useRouter } from 'vue-router';
import type { LocationQuery } from 'vue-router';

/** 深链参数（round 为空串 = 只选中 Agent 不高亮） */
export interface AgentDeepLink {
  agent: string;
  round: string;
}

/** 时间线行点击的跳转目标（与 parseAgentDeepLink 对偶） */
export function agentRoundLocation(
  agent: string,
  roundId: string,
): { path: string; query: { agent: string; round: string } } {
  return { path: '/agents', query: { agent, round: roundId } };
}

/** 解析深链 query：agent 缺失或为空 → null；round 缺失 → 只选 Agent */
export function parseAgentDeepLink(query: LocationQuery): AgentDeepLink | null {
  const agent = String(query.agent ?? '').trim();
  if (!agent) return null;
  return { agent, round: String(query.round ?? '').trim() };
}

/** 读一次当前路由的深链参数并立即清参（replace）——刷新/前进后退不重复触发选中与高亮 */
export function useAgentDeepLink() {
  const route = useRoute();
  const router = useRouter();

  function consumeDeepLink(): AgentDeepLink | null {
    const link = parseAgentDeepLink(route.query);
    if (!link) return null;
    const query = { ...route.query };
    delete query.agent;
    delete query.round;
    void router.replace({ query });
    return link;
  }

  return { consumeDeepLink };
}
