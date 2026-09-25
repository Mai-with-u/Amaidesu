<template>
  <div class="feed-timeline" :class="{ 'is-compact': compact, 'is-chat': isChat }">
    <div v-if="entries.length === 0" class="stage-empty">
      <el-icon class="stage-empty-icon"><Monitor /></el-icon>
      <p class="stage-empty-text">{{ emptyText }}</p>
    </div>

    <ol v-else class="feed">
      <li v-for="entry in feedRows" :key="entry.id" class="feed-row" :class="rowAlignClass(entry)">
        <!-- 会话模式的过程折叠条（合成条目）：挂在主播发言之后，点击展开该轮过程 -->
        <button
          v-if="entry.kind === 'process_group'"
          type="button"
          class="chat-process-strip"
          :aria-expanded="expandedChatGroups.has(entry.id)"
          @click="toggleChatGroup(entry.id)"
        >
          <span class="chat-process-summary">{{ entry.note }} 个过程记录 · {{ entry.text }}</span>
          <span class="grow" />
          <span class="chat-process-action">{{
            expandedChatGroups.has(entry.id) ? '收起' : '展开'
          }}</span>
          <span class="chat-process-arrow" aria-hidden="true">▸</span>
        </button>

        <!-- 环节推进 / 场次边界：横贯分隔行 -->
        <div v-else-if="entry.kind === 'rundown' || entry.kind === 'boundary'" class="beat">
          <span class="beat-rule" aria-hidden="true" />
          <span class="beat-body">
            <span class="beat-eyebrow">{{ entry.kind === 'boundary' ? '场次' : '环节' }}</span>
            <span class="beat-label">{{ entry.text }}</span>
            <span v-if="entry.badge" class="beat-action">{{ entry.badge }}</span>
            <span v-if="entry.note" class="beat-note">{{ entry.note }}</span>
          </span>
          <span class="beat-rule" aria-hidden="true" />
          <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
        </div>

        <!-- 里程碑：庆祝行 -->
        <div v-else-if="entry.kind === 'milestone'" class="milestone">
          <div class="milestone-body">
            <p class="milestone-text">{{ entry.text }}</p>
            <p v-if="entry.note" class="milestone-meta mono">{{ entry.note }}</p>
          </div>
          <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
        </div>

        <!-- 阶段状态：安静单行（决策管线在做什么/卡在哪） -->
        <div v-else-if="entry.kind === 'stage'" class="whisper">
          <span class="whisper-dot" aria-hidden="true" />
          <span class="whisper-text" :class="{ 'is-running': entry.speak }">
            {{ entry.text }}<template v-if="entry.note"> · {{ entry.note }}</template>
          </span>
          <span class="grow" />
          <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
        </div>

        <!-- 进场：安静单行 -->
        <div v-else-if="entry.kind === 'enter'" class="whisper">
          <span class="whisper-dot" aria-hidden="true" />
          <span class="whisper-text">{{ entry.text }}</span>
          <span class="grow" />
          <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
        </div>

        <!-- 思考行：ReAct 各步/生成段思考流（视图层合成条目）。
             默认折叠只留单行预览，流式期间预览实时增长；展开看全文 -->
        <details v-else-if="entry.kind === 'thinking'" class="think">
          <summary>
            <span class="think-caret" aria-hidden="true">▸</span>
            <span class="think-label">{{ entry.actor }}</span>
            <span class="think-preview">{{ entry.text }}</span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
          </summary>
          <pre class="think-body">{{ entry.text }}</pre>
        </details>

        <!-- 决策卡：verdict（实时裁决）+ decision（沉默/失败轮或统计回填后）共用 -->
        <div
          v-else-if="entry.kind === 'decision' || entry.kind === 'verdict'"
          class="decision"
          :class="{ 'is-failed': entry.failed, 'is-silent': isSilentDecision(entry) }"
        >
          <div class="act-head">
            <span class="act-kind">决策</span>
            <code v-if="entry.roundId" class="d-round mono">{{ entry.roundId }}</code>
            <span v-if="confidenceLabel(entry)" class="d-conf mono">{{
              confidenceLabel(entry)
            }}</span>
            <span
              v-if="entry.badge"
              class="act-badge"
              :class="{ 'is-silent-badge': isSilentDecision(entry) }"
            >
              {{ entry.badge }}
            </span>
            <span
              v-if="entry.simulated"
              class="act-source"
              title="由「立即决策测试」手动驱动，非真实弹幕触发"
            >
              测试
            </span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
          </div>
          <div v-if="entry.replyTo" class="reply-quote">
            <template v-if="replyQuoteOf(entry)">
              <span class="reply-quote-name">{{ replyQuoteOf(entry)!.actor }}</span>
              <span class="reply-quote-text">{{ replyQuoteOf(entry)!.text }}</span>
            </template>
            <span v-else class="reply-quote-fallback">回复了一条弹幕</span>
          </div>
          <p class="act-text">{{ entry.text }}</p>
          <p v-if="guidanceOf(entry)" class="d-guidance">{{ guidanceOf(entry) }}</p>
          <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
          <div class="d-meta">
            <span v-if="batchSizeOf(entry) > 0" class="mono">批次 {{ batchSizeOf(entry) }} 条</span>
            <span v-if="plannerMsOf(entry)" class="mono">决策 {{ plannerMsOf(entry) }}ms</span>
            <span v-if="replyMsOf(entry)" class="mono">生成 {{ replyMsOf(entry) }}ms</span>
            <span
              v-if="cacheLabelOf(entry)"
              class="d-pill d-pill--cache"
              :title="cacheTitleOf(entry)"
            >
              {{ cacheLabelOf(entry) }}
            </span>
            <span
              v-if="tokensLabelOf(entry)"
              class="d-pill d-pill--token"
              :title="tokensTitleOf(entry)"
            >
              Token {{ tokensLabelOf(entry) }}
            </span>
            <span
              v-if="modelNameOf(entry)"
              class="d-pill d-pill--model"
              :title="modelNameOf(entry)"
            >
              {{ modelNameOf(entry) }}
            </span>
            <span
              v-if="ctxRatioOf(entry) !== null"
              class="d-pill d-pill--ctx"
              :class="{ 'is-warn': (ctxRatioOf(entry) ?? 0) > 0.8 }"
              :title="ctxTitleOf(entry)"
            >
              <span class="d-pill-ctx-bar">
                <span class="d-pill-ctx-bar-fill" :style="ctxBarStyleOf(entry)"></span>
              </span>
              上下文 {{ ctxLabelOf(entry) }}
            </span>
            <a
              v-if="entry.llmRequestId"
              class="d-link"
              :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
              @click.stop
            >
              完整请求 ↗
            </a>
            <details v-if="rawOf(entry)" class="d-raw">
              <summary>原始输出</summary>
              <pre class="mono">{{ rawOf(entry) }}</pre>
            </details>
          </div>
        </div>

        <!-- 工具调用卡（tool.result.*）：状态徽标（成功/失败）+ 来源徽标（主播决策/游戏 Agent）拆双槽
             工具卡一律中性底——状态由左边线色 + 徽标承载，避免高频工具行刷成警报墙 -->
        <div
          v-else-if="entry.kind === 'tool'"
          class="act is-tool-neutral"
          :class="{
            'is-failed': entry.failed,
            'is-speak': entry.speak,
            'is-agent-streamer': agentGroupOf(entry) === 'streamer',
            'is-agent-game': agentGroupOf(entry) === 'game',
          }"
        >
          <div class="act-head">
            <span class="act-kind">工具调用</span>
            <code class="act-tool mono">{{ entry.actor }}</code>
            <!-- 状态徽标：成功/失败。compact 模式下成功徽标隐藏（失败照常显示） -->
            <span
              v-if="entry.badge && (!compact || entry.failed)"
              class="act-badge"
              :class="{ 'is-success-badge': entry.badge === '成功' }"
            >
              {{ entry.badge }}
            </span>
            <!-- 来源徽标（中性色，与状态徽标区分不抢视觉） -->
            <span v-if="entry.source" class="act-source">{{ entry.source }}</span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
          </div>
          <!-- 参数药丸：入参轮廓一眼可扫；入参为空/非对象时回退纯文本正文 -->
          <div v-if="entry.argPills.length > 0" class="act-args">
            <span v-for="pill in entry.argPills" :key="pill.key" class="arg-pill">
              <span class="arg-key">{{ pill.key }}</span>
              <span class="arg-value">{{ pill.value }}</span>
            </span>
          </div>
          <p v-else-if="entry.text" class="act-text">{{ entry.text }}</p>
          <p v-if="entry.note" class="act-note">{{ entry.note }}</p>
          <!-- 参数/结果折叠区：compact 下不渲染（首页保持紧凑）；detail 为空时整段不渲染。
               JSON 树与复制交互对齐 LLM 历史页（vue-json-pretty + 剪贴板） -->
          <details
            v-if="!compact && hasToolDetail(entry)"
            class="t-payload"
            :class="{ 'is-failed': entry.failed }"
            @toggle="onPayloadToggle(entry.id, $event)"
          >
            <summary>参数 / 结果</summary>
            <!-- 载荷懒渲染：展开过才挂载（details 折叠态下子节点仍会进 DOM，
                 大 JSON 树在高频重渲染的时间线里是主要渲染成本） -->
            <template v-if="openedPayloads.has(entry.id)">
              <!-- 失败时错误文本优先展示（开发者定位异常最直接的线索） -->
              <div v-if="entry.failed && toolErrorOf(entry)" class="t-payload-block">
                <div class="t-payload-head">
                  <span class="t-payload-tag t-payload-tag--error">错误</span>
                  <el-icon class="copy-icon" title="复制" @click="copyText(toolErrorOf(entry))">
                    <CopyDocument />
                  </el-icon>
                </div>
                <pre class="t-payload-text mono">{{ toolErrorOf(entry) }}</pre>
              </div>
              <div v-if="hasToolArgs(entry)" class="t-payload-block">
                <div class="t-payload-head">
                  <span class="t-payload-tag">参数</span>
                  <el-icon
                    class="copy-icon"
                    title="复制 JSON"
                    @click="copyJson(toolDetail(entry).args)"
                  >
                    <CopyDocument />
                  </el-icon>
                </div>
                <div class="t-payload-json">
                  <VueJsonPretty
                    :data="toJsonData(toolDetail(entry).args)"
                    theme="dark"
                    show-line
                  />
                </div>
              </div>
              <div v-if="hasToolResult(entry)" class="t-payload-block">
                <div class="t-payload-head">
                  <span class="t-payload-tag">结果</span>
                  <el-icon
                    class="copy-icon"
                    title="复制 JSON"
                    @click="copyJson(toolDetail(entry).result)"
                  >
                    <CopyDocument />
                  </el-icon>
                </div>
                <div class="t-payload-json">
                  <VueJsonPretty
                    :data="toJsonData(toolDetail(entry).result)"
                    theme="dark"
                    show-line
                  />
                </div>
              </div>
            </template>
          </details>
        </div>

        <!-- 主播发言（streamer.speech）：表达语义 + Replyer 思考回看。
             统计胶囊取 Replyer 表达请求（决策卡上是 Planner 请求，二者互补）：
             缓存/Token/模型懒取 + 完整请求链接；无指针的旧事件不渲染该行 -->
        <div v-else-if="entry.kind === 'speech'" class="act is-speech">
          <div class="act-head">
            <span class="act-kind act-kind--speech">主播</span>
            <span v-if="entry.note" class="act-emotion">{{ entry.note }}</span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
          </div>
          <div v-if="entry.replyTo" class="reply-quote">
            <template v-if="replyQuoteOf(entry)">
              <span class="reply-quote-name">{{ replyQuoteOf(entry)!.actor }}</span>
              <span class="reply-quote-text">{{ replyQuoteOf(entry)!.text }}</span>
            </template>
            <span v-else class="reply-quote-fallback">回复了一条弹幕</span>
          </div>
          <p class="act-text">{{ entry.text }}</p>
          <div v-if="entry.llmRequestId" class="d-meta">
            <span
              v-if="cacheLabelOf(entry)"
              class="d-pill d-pill--cache"
              :title="cacheTitleOf(entry)"
            >
              {{ cacheLabelOf(entry) }}
            </span>
            <span
              v-if="tokensLabelOf(entry)"
              class="d-pill d-pill--token"
              :title="tokensTitleOf(entry)"
            >
              Token {{ tokensLabelOf(entry) }}
            </span>
            <span
              v-if="modelNameOf(entry)"
              class="d-pill d-pill--model"
              :title="modelNameOf(entry)"
            >
              {{ modelNameOf(entry) }}
            </span>
            <span
              v-if="ctxRatioOf(entry) !== null"
              class="d-pill d-pill--ctx"
              :class="{ 'is-warn': (ctxRatioOf(entry) ?? 0) > 0.8 }"
              :title="ctxTitleOf(entry)"
            >
              <span class="d-pill-ctx-bar">
                <span class="d-pill-ctx-bar-fill" :style="ctxBarStyleOf(entry)"></span>
              </span>
              上下文 {{ ctxLabelOf(entry) }}
            </span>
            <a
              class="d-link"
              :href="`/llm/history?request_id=${encodeURIComponent(entry.llmRequestId)}`"
              @click.stop
            >
              完整请求 ↗
            </a>
          </div>
        </div>

        <!-- 游戏 Agent 上报（game.* / 走 toGameEntry）：绿色系左边线，act 变体 -->
        <div
          v-else-if="entry.kind === 'game'"
          class="act is-agent-game"
          :class="{ 'is-failed': entry.failed }"
        >
          <div class="act-head">
            <span class="act-kind act-kind--game">游戏 Agent</span>
            <span
              v-if="entry.badge"
              class="act-badge"
              :class="{ 'is-success-badge': !entry.failed }"
            >
              {{ entry.badge }}
            </span>
            <span class="grow" />
            <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
          </div>
          <p class="act-text">{{ entry.text }}</p>
          <p v-if="entry.note" class="act-note is-game-note">{{ entry.note }}</p>
        </div>

        <!-- 观众发声：弹幕 / 礼物 / SC -->
        <div v-else class="chat" :class="`chat--${entry.kind}`">
          <span class="avatar" aria-hidden="true">{{ entry.initial }}</span>
          <div class="bubble">
            <div class="bubble-head">
              <span class="who" :title="entry.actor">{{ entry.actor }}</span>
              <span v-if="entry.badge" class="chip">{{ entry.badge }}</span>
              <span v-if="entry.simulated" class="chip" title="来自控制台注入的模拟消息">
                模拟
              </span>
              <span v-if="entry.money" class="money mono">{{ entry.money }}</span>
              <span class="grow" />
              <time class="stamp mono">{{ relativeTime(nowMs, entry.tsMs) }}</time>
            </div>
            <p class="say">{{ entry.text }}</p>
          </div>
        </div>

        <!-- 会话布局的主播侧头像：置于条件链末尾避免打断 v-else-if 链；
             与左侧观众头像呼应，撑起发言行右对齐的对话感 -->
        <span
          v-if="entry.kind === 'speech' && isChat"
          class="avatar avatar--host"
          aria-hidden="true"
          >主</span
        >
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
/**
 * 直播时间线视图（直播控制台 + 首页共用）
 *
 * 输入：已折叠的 ShowEntry[]（顺序即展示顺序——上游负责按时间归并，
 *      控制台把思考行合成进事件条目流，首页只取事件条目）
 * 渲染：所有控制台当前支持的行类型——环节/边界、里程碑、阶段、进场、
 *      思考行、决策/裁决、工具调用、主播发言、弹幕/礼物/SC 气泡。
 * 布局：timeline 单列沿脊线；chat 会话模式（观众左 / 主播右 / 过程行居中），
 *      同一套行卡片只换对齐方式，内容渲染不分叉。
 *
 * 控制台独占能力（暂停/清空、注入面板、滚动跟随）留在 LiveObserver；
 * 本组件只负责"按条目渲染"，对上游数据来源无要求，可被任何 Vue 页面复用。
 */
import { computed, ref, watch } from 'vue';
import { CopyDocument, Monitor } from '@element-plus/icons-vue';
import VueJsonPretty from 'vue-json-pretty';
import 'vue-json-pretty/lib/styles.css';
import { llmApi } from '@/api';
import {
  agentGroupOf,
  batchSizeOf,
  buildChatRows,
  confidenceLabel,
  guidanceOf,
  isChatProcessKind,
  isSilentDecision,
  plannerMsOf,
  rawOf,
  relativeTime,
  replyMsOf,
  type ShowEntry,
} from '@/utils/liveFeed';
import { useNowTick } from '@/composables/useNowTick';

interface Props {
  /** 已折叠好的时间线条目（顺序即展示顺序） */
  entries: ShowEntry[];
  /** 紧凑模式：用于首页缩略展示，缩短行距/字号 */
  compact?: boolean;
  /** 行布局：timeline=单列沿脊线的时间线；chat=会话模式（观众左 / 主播右 / 过程行居中） */
  layout?: 'timeline' | 'chat';
  /** entries 为空时的提示语 */
  emptyText?: string;
}

const props = withDefaults(defineProps<Props>(), {
  compact: false,
  layout: 'timeline',
  emptyText: '静候消息与决策',
});

const isChat = computed(() => props.layout === 'chat');

/** 会话模式下已展开过程条的组（键为过程条合成条目的 id） */
const expandedChatGroups = ref<Set<string>>(new Set());

/** 渲染行序：会话模式把每轮过程折叠成一条过程条；时间线模式原样透传 */
const feedRows = computed<ShowEntry[]>(() =>
  isChat.value ? buildChatRows(props.entries, expandedChatGroups.value) : props.entries,
);

function toggleChatGroup(key: string): void {
  const next = new Set(expandedChatGroups.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedChatGroups.value = next;
}

/** 会话布局的行对齐类：观众消息靠左、主播发言靠右、过程行与折叠条随主播侧右对齐；
 *  时间线布局不加任何对齐修饰 */
function rowAlignClass(entry: ShowEntry): string {
  if (!isChat.value) return '';
  if (entry.kind === 'process_group') return 'row-strip';
  if (entry.kind === 'speech') return 'row-right';
  if (isChatProcessKind(entry.kind)) return 'row-process';
  return '';
}

// 1s tick：让相对时间标签（"刚刚 / 12s 前"）每秒刷新一次；独立维护不依赖父组件
const nowMs = useNowTick();

/** 决策/发言卡 LLM 统计徽标（llm_request_id → 输入/输出/缓存命中）。
 * planner.decision 只带请求指针不带 token 数，详情按 id 懒取；speech 带的是
 * Replyer 表达请求指针，同一套懒取。缓存口径取 hit/prompt_tokens 而非聚合的
 * hit/(hit+miss)：OpenAI 风格只上报 cached_tokens 不上报 miss，后者会算出假
 * 100%；命中为 0（含上游未上报）不渲染缓存徽标。 */
interface RoundTokenStats {
  promptTokens: number;
  completionTokens: number;
  hitTokens: number;
  /** 本轮 Planner 请求实际使用的模型标识（取自请求历史记录） */
  modelName: string;
}
const roundTokenStats = ref<Map<string, RoundTokenStats>>(new Map());
const tokenStatsFetching = new Set<string>();

/** 已展开过载荷折叠区的条目 id：展开时才挂载 JSON 树，收起后保留已挂载内容不卸载。
 * 声明在下方 immediate watch 之前——watch 首次同步执行就会读它 */
const openedPayloads = ref<Set<string>>(new Set());

async function fetchTokenStats(requestId: string): Promise<void> {
  tokenStatsFetching.add(requestId);
  try {
    const response = await llmApi.getRequestById(requestId);
    const record = response.data;
    if (!record) return;
    const prompt = record.usage?.prompt_tokens ?? 0;
    if (prompt > 0) {
      roundTokenStats.value.set(requestId, {
        promptTokens: prompt,
        completionTokens: record.usage?.completion_tokens ?? 0,
        hitTokens: record.cache_hit_tokens ?? 0,
        modelName: record.model_name ?? '',
      });
    }
  } catch (e) {
    console.warn(`[FeedTimeline] 决策卡 token 统计获取失败: ${requestId}`, e);
  } finally {
    tokenStatsFetching.delete(requestId);
  }
}

watch(
  () => props.entries,
  entries => {
    const visible = new Set<string>();
    for (const entry of entries) {
      // 真实流程里 decision 轮末会合并进先到的 verdict 卡（llmRequestId 一并回填），
      // 独立 decision 卡只出现在无裁决的失败/静默轮，两种都要取数；
      // speech 卡带的是 Replyer 表达请求的指针（与决策卡的 Planner 请求互补），同样取数
      if (
        (entry.kind === 'decision' || entry.kind === 'verdict' || entry.kind === 'speech') &&
        entry.llmRequestId
      ) {
        visible.add(entry.llmRequestId);
      }
    }
    // 离场条目的徽标顺手清掉，长直播下 map 不随轮次无限增长
    for (const key of roundTokenStats.value.keys()) {
      if (!visible.has(key)) roundTokenStats.value.delete(key);
    }
    for (const requestId of visible) {
      if (!roundTokenStats.value.has(requestId) && !tokenStatsFetching.has(requestId)) {
        void fetchTokenStats(requestId);
      }
    }
    if (visible.size > 0) void ensureContextWindows();
    // 懒渲染集合同步收缩：只保留当前在列条目，避免长会话下随事件 id 无限增长
    if (openedPayloads.value.size > 0) {
      const liveIds = new Set(entries.map(entry => entry.id));
      let pruned = false;
      const next = new Set<string>();
      for (const id of openedPayloads.value) {
        if (liveIds.has(id)) next.add(id);
        else pruned = true;
      }
      if (pruned) openedPayloads.value = next;
    }
  },
  { immediate: true },
);

function statsOf(entry: ShowEntry): RoundTokenStats | null {
  return entry.llmRequestId ? (roundTokenStats.value.get(entry.llmRequestId) ?? null) : null;
}

/** 万级以下直接显示，以上缩写为 k（d-meta 小字号场景，精确值在悬浮提示） */
function compactTokens(n: number): string {
  return n >= 10000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
}

/** 缓存徽标文案（"缓存 62%"）；未取到 / 上游未上报返回空串（调用方按 v-if 不渲染） */
function cacheLabelOf(entry: ShowEntry): string {
  const stats = statsOf(entry);
  if (!stats || stats.hitTokens <= 0) return '';
  return `缓存 ${Math.round((stats.hitTokens / stats.promptTokens) * 100)}%`;
}

/** 缓存徽标悬浮提示：绝对 token 数 */
function cacheTitleOf(entry: ShowEntry): string {
  const stats = statsOf(entry);
  if (!stats) return '';
  return `缓存命中 ${stats.hitTokens.toLocaleString()} / ${stats.promptTokens.toLocaleString()} tokens`;
}

/** token 徽标文案（"Token 75.4k/0.5k"，输入/输出） */
function tokensLabelOf(entry: ShowEntry): string {
  const stats = statsOf(entry);
  if (!stats) return '';
  return `${compactTokens(stats.promptTokens)}/${compactTokens(stats.completionTokens)}`;
}

/** token 徽标悬浮提示：精确值 */
function tokensTitleOf(entry: ShowEntry): string {
  const stats = statsOf(entry);
  if (!stats) return '';
  return `输入 ${stats.promptTokens.toLocaleString()} · 输出 ${stats.completionTokens.toLocaleString()} tokens`;
}

/** 模型名徽标：本轮 Planner 请求实际使用的模型标识（空串不渲染） */
function modelNameOf(entry: ShowEntry): string {
  return statsOf(entry)?.modelName ?? '';
}

/** 模型上下文窗口（model_name → token 总量，取自 GET /llm/usage 装配期快照）。
 * 整页只取一次——窗口来自 [[llm_models]].context_window 配置，改配置需重启应用，
 * 运行期不变。未配置（0）的模型不进映射，水位胶囊按 v-if 隐藏（与用量页一致）。 */
const contextWindows = ref<Map<string, number>>(new Map());
let contextWindowsLoading = false;
let contextWindowsLoaded = false;

async function ensureContextWindows(): Promise<void> {
  if (contextWindowsLoaded || contextWindowsLoading) return;
  contextWindowsLoading = true;
  try {
    const response = await llmApi.getUsage();
    const next = new Map<string, number>();
    for (const [model, stats] of Object.entries(response.data ?? {})) {
      if (stats.context_window > 0) next.set(model, stats.context_window);
    }
    contextWindows.value = next;
    contextWindowsLoaded = true;
  } catch (e) {
    console.warn('[FeedTimeline] 模型上下文窗口获取失败（下一条目到达时重试）', e);
  } finally {
    contextWindowsLoading = false;
  }
}

/** 本条目请求所用模型的上下文窗口（未配置/未加载返回 0） */
function contextWindowOf(entry: ShowEntry): number {
  const stats = statsOf(entry);
  if (!stats || !stats.modelName) return 0;
  return contextWindows.value.get(stats.modelName) ?? 0;
}

/** 上下文水位（prompt_tokens / context_window）；窗口未配置返回 null（胶囊不渲染） */
function ctxRatioOf(entry: ShowEntry): number | null {
  const stats = statsOf(entry);
  const win = contextWindowOf(entry);
  if (!stats || win <= 0) return null;
  return stats.promptTokens / win;
}

/** 上下文进度条填充宽度（钳到 100%） */
function ctxBarStyleOf(entry: ShowEntry): string {
  const ratio = ctxRatioOf(entry) ?? 0;
  return `width: ${Math.min(100, ratio * 100).toFixed(1)}%`;
}

/** 上下文徽标文案（"上下文 31%"） */
function ctxLabelOf(entry: ShowEntry): string {
  const ratio = ctxRatioOf(entry) ?? 0;
  return `${Math.round(ratio * 100)}%`;
}

/** 上下文徽标悬浮提示：分子/分母绝对值 */
function ctxTitleOf(entry: ShowEntry): string {
  const stats = statsOf(entry);
  const win = contextWindowOf(entry);
  return `本轮输入 ${(stats?.promptTokens ?? 0).toLocaleString()} / 窗口 ${win.toLocaleString()} tokens`;
}

/** 弹幕 message_id → 时间线条目（用于发言/决策卡回复引用反查）。
 * 仅索引观众消息类（弹幕 / SC / 礼物）；同一 ID 重复出现时取首次，时间线按 tsMs 正序遍历保证幂等。 */
const messageIndex = computed<Map<string, ShowEntry>>(() => {
  const map = new Map<string, ShowEntry>();
  for (const item of props.entries) {
    if (!item.messageId) continue;
    if (item.kind !== 'danmaku' && item.kind !== 'super_chat' && item.kind !== 'gift') continue;
    if (!map.has(item.messageId)) map.set(item.messageId, item);
  }
  return map;
});

/** 把决策/发言卡的 replyTo 反查成 QQ 风格引用块所需的两字段。
 * 未命中（时间线被清空、消息被水位裁剪、回看数据缺失 message_id 等）返回 null，调用方按占位降级渲染。 */
function replyQuoteOf(entry: ShowEntry): { actor: string; text: string } | null {
  const targetId = entry.replyTo;
  if (!targetId) return null;
  const target = messageIndex.value.get(targetId);
  if (!target) return null;
  return { actor: target.actor, text: target.text };
}

/** 工具卡的 detail 字段读取守卫——liveFeed.fromToolResult 总是写入 detail，但回看路径或老事件可能为 null */
function toolDetail(entry: ShowEntry): { args: unknown; result: unknown; error_message: string } {
  const detail = entry.detail;
  if (!detail) return { args: null, result: null, error_message: '' };
  return {
    args: detail.args ?? null,
    result: detail.result ?? null,
    error_message: typeof detail.error_message === 'string' ? detail.error_message : '',
  };
}

/** 折叠区可见性：args / result / error_message 任一非空即视为有可展示载荷 */
function hasToolDetail(entry: ShowEntry): boolean {
  const d = toolDetail(entry);
  return hasToolArgs(entry) || hasToolResult(entry) || (entry.failed && d.error_message.length > 0);
}
function hasPayloadValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (typeof value === 'string') return value.length > 0;
  if (typeof value === 'object' && !Array.isArray(value)) return Object.keys(value).length > 0;
  return true;
}
function hasToolArgs(entry: ShowEntry): boolean {
  return hasPayloadValue(toolDetail(entry).args);
}
function hasToolResult(entry: ShowEntry): boolean {
  return hasPayloadValue(toolDetail(entry).result);
}
function toolErrorOf(entry: ShowEntry): string {
  return toolDetail(entry).error_message;
}

/** vue-json-pretty 的 data 仅接受 JSON 结构；事件 payload 来自线上数据用 JSON 往返归一化
 * （剥掉 undefined / 函数等非 JSON 值），避免直接强转掩盖真实脏数据。
 * 序列化结果按载荷对象引用缓存（WeakMap）：同一事件的 args/result 在缓冲区里引用稳定，
 * 列表每次重渲染不必对全量载荷重复 JSON 往返——高频流式更新下的主要 CPU 开销来源 */
type JsonData = string | number | boolean | null | JsonData[] | { [key: string]: JsonData };
const jsonDataCache = new WeakMap<object, JsonData>();
function toJsonData(value: unknown): JsonData {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'object') return JSON.parse(JSON.stringify(value)) as JsonData;
  const cached = jsonDataCache.get(value);
  if (cached !== undefined) return cached;
  const parsed = JSON.parse(JSON.stringify(value)) as JsonData;
  jsonDataCache.set(value, parsed);
  return parsed;
}

/** details 原生 toggle 事件：首次展开时把条目 id 记入懒渲染集合 */
function onPayloadToggle(entryId: string, event: Event): void {
  if (!(event.target as HTMLDetailsElement).open) return;
  if (openedPayloads.value.has(entryId)) return;
  const next = new Set(openedPayloads.value);
  next.add(entryId);
  openedPayloads.value = next;
}

/** 复制交互与 LLM 历史页一致：剪贴板写入格式化 JSON；循环引用等异常落兜底文本 */
async function copyJson(data: unknown): Promise<void> {
  let text = '';
  try {
    text = JSON.stringify(data, null, 2);
  } catch {
    text = '[不可序列化]';
  }
  await navigator.clipboard.writeText(text);
}
async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text);
}
</script>

<style scoped>
/* 工具类：grow / mono 在行模板里被广泛使用，scoped 内保留副本       */
/* 空态                                                          */
.stage-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-sm);
  padding: 40px 0;
}
.stage-empty-icon {
  font-size: 44px;
  color: var(--border-color-dark);
}
.stage-empty-text {
  margin: 0;
  font-size: 13px;
  letter-spacing: 0.3px;
  color: var(--text-placeholder);
}

/* 流：单列居左时间轴脊线——所有条目沿轴排布，靠样式区分             */
.feed {
  position: relative;
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.feed::before {
  content: '';
  position: absolute;
  top: 4px;
  bottom: 4px;
  left: 14px;
  width: 1px;
  background: var(--border-color);
}

.feed-row {
  display: flex;
  flex-direction: column;
  animation: rowIn 0.22s cubic-bezier(0.33, 1, 0.68, 1);
}

@keyframes rowIn {
  from {
    opacity: 0;
    transform: translateY(5px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.stamp {
  font-size: 10px;
  color: var(--text-placeholder);
  flex-shrink: 0;
  white-space: nowrap;
}

/* 观众发声：气泡                                                */
.chat {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  max-width: 82%;
}

.avatar {
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 11px;
  font-weight: 700;
  background: var(--bg-card);
  border: 1px solid var(--color-collector);
  color: var(--color-collector);
  box-shadow: 0 0 0 3px var(--bg-card);
  z-index: 1;
}

.bubble {
  flex: 1;
  min-width: 0;
  padding: 7px 12px;
  border-radius: 4px 12px 12px 12px;
  background: var(--bg-hover);
}

.bubble-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.who {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-collector);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}

.chip {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.4px;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

.money {
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.say {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

/* --- 礼物：暖色高亮 --- */
.chat--gift {
  max-width: 88%;
}
.chat--gift .avatar {
  border-color: var(--color-warning);
  color: var(--color-warning);
}
.chat--gift .bubble {
  background: var(--color-warning-bg);
  border-left: 2px solid var(--color-warning);
  box-shadow: var(--shadow-sm);
}
.chat--gift .who {
  color: var(--color-warning);
}
.chat--gift .say {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

/* --- SC：最强高亮（暖色渐变 + 金额） --- */
.chat--super_chat {
  max-width: 92%;
}
.chat--super_chat .avatar {
  border-color: var(--color-danger);
  color: var(--color-danger);
}
.chat--super_chat .bubble {
  padding: 10px 14px;
  border-left: 3px solid var(--color-danger);
  background: linear-gradient(100deg, var(--color-danger-bg), var(--color-warning-bg));
  box-shadow: var(--shadow-md);
}
.chat--super_chat .who {
  font-size: 12px;
  color: var(--color-danger);
}
.chat--super_chat .chip {
  background: var(--color-danger);
  color: var(--text-inverse);
  border-color: var(--color-danger);
}
.chat--super_chat .money {
  font-size: 14px;
  color: var(--color-danger);
}
.chat--super_chat .say {
  font-size: 15px;
  font-weight: 500;
  line-height: 1.55;
  color: var(--text-primary);
}

/* 安静单行：进场 / 阶段状态                                      */
.whisper {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 18px;
}
/* 占位宽度与头像一致（28px），使圆点正落在时间轴脊线上 */
.whisper-dot {
  width: 28px;
  height: 12px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  z-index: 1;
}
.whisper-dot::before {
  content: '';
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-placeholder);
  box-shadow: 0 0 0 3px var(--bg-card);
}
.whisper-text {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.whisper-text.is-running {
  color: var(--color-agent);
}

/* 决策记录：居左宽卡——本轮为什么这么做                            */
.decision {
  margin-left: 38px; /* 28px 头像 + 10px 间距：与气泡体对齐 */
  max-width: 92%;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  background: var(--color-agent-bg);
  border-left: 3px solid var(--color-agent);
}
.decision.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.decision.is-silent {
  background: var(--bg-hover);
  border-left-color: var(--border-color-dark);
}

.act-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}

.act-kind {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.4px;
  color: var(--color-agent);
  flex-shrink: 0;
}
.act-kind--speech {
  color: var(--color-agent);
}
.act-emotion {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  color: var(--color-agent);
  background: var(--color-agent-bg);
  flex-shrink: 0;
}

.d-round {
  font-size: 10px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}

.d-conf {
  font-size: 10px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.act-badge {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  background: var(--color-danger);
  color: var(--text-inverse);
  flex-shrink: 0;
}
.act-badge.is-silent-badge {
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-dark);
}

.act-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}
.decision.is-failed .act-text {
  color: var(--color-danger);
}
.decision.is-silent .act-text {
  color: var(--text-secondary);
}

/* QQ 风格引用块：被回复弹幕的「昵称 + 原文」摘要 */
.reply-quote {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 6px 0 0;
  padding: 5px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  border-left: 3px solid var(--border-color-dark);
  min-width: 0;
}
.reply-quote-name {
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.reply-quote-text {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-regular);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-clamp: 2;
  overflow: hidden;
  word-break: break-word;
}
.reply-quote-fallback {
  font-size: 11px;
  font-style: italic;
  line-height: 1.5;
  color: var(--text-placeholder);
}

.d-guidance {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--text-secondary);
  word-break: break-word;
}

.d-speech {
  margin: 5px 0 0;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
  word-break: break-word;
}

.act-note {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-danger);
  word-break: break-word;
}

.d-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-placeholder);
}

.d-link {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-primary);
  text-decoration: none;
}
.d-link:hover {
  text-decoration: underline;
}

/* 统计胶囊：底色块让各项统计在元信息行里一眼可分（缓存=绿 / 用量=蓝 / 模型=中性描边 / 水位=蓝） */
.d-pill {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  flex-shrink: 0;
  cursor: default;
}
.d-pill--cache {
  background: var(--color-success-bg);
  color: var(--color-success);
}
.d-pill--token {
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
}
.d-pill--model {
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
}
/* 上下文水位胶囊：迷你进度条 + 百分比；>80% 转警告橙（色阶与用量页水位条一致） */
.d-pill--ctx {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: rgba(64, 158, 255, 0.12);
  color: var(--color-primary);
}
.d-pill--ctx.is-warn {
  background: var(--color-warning-bg);
  color: var(--color-warning);
}
.d-pill-ctx-bar {
  width: 28px;
  height: 4px;
  border-radius: 2px;
  background: rgba(127, 127, 127, 0.3);
  overflow: hidden;
}
.d-pill-ctx-bar-fill {
  display: block;
  height: 100%;
  border-radius: 2px;
  background: currentColor;
}

.d-raw {
  flex-basis: 100%;
}
.d-raw summary {
  cursor: pointer;
  font-size: 10px;
  color: var(--text-placeholder);
  user-select: none;
}
.d-raw pre {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-size: 10px;
  line-height: 1.5;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

/* 思考行：ReAct 各步/生成段思考流（默认折叠，预览行随流式增量增长） */
.think {
  margin-left: 38px; /* 28px 头像 + 10px 间距：与气泡体对齐 */
  max-width: 92%;
  min-width: 0;
}
.think summary {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 18px;
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.think summary::-webkit-details-marker {
  display: none;
}
.think-caret {
  font-size: 10px;
  color: var(--text-placeholder);
  transition: transform var(--transition-fast);
  flex-shrink: 0;
}
.think[open] .think-caret {
  transform: rotate(90deg);
}
.think-label {
  padding: 0 5px;
  border-radius: var(--radius-sm);
  background: var(--color-agent-bg);
  color: var(--color-agent);
  font-size: 9px;
  font-weight: 600;
  line-height: 16px;
  white-space: nowrap;
  flex-shrink: 0;
}
.think-preview {
  font-size: 11px;
  color: var(--text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.think[open] .think-preview {
  display: none;
}
.think-body {
  margin: 6px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--bg-hover);
  font-family: inherit;
  font-size: 10px;
  line-height: 1.6;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}

/* 主播动作：居左卡（工具结果 / 发言）                             */
.act {
  margin-left: 38px; /* 与决策卡同列对齐 */
  max-width: 92%;
  min-width: 240px;
  padding: 8px 12px;
  border-radius: var(--radius-md);
  background: var(--color-tool-bg);
  border-left: 2px solid var(--color-tool);
}
.act.is-speak {
  padding: 10px 14px;
  box-shadow: var(--shadow-sm);
}
.act.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}

/* 工具调用卡特例：失败/成功不再刷整行底色——工具调用是高频行，红底视觉噪音过大。
 * 状态由左边线色 + 徽标承载；卡体统一中性面板底（继承 .act 默认的 --color-tool-bg） */
.act.is-tool-neutral {
  background: var(--bg-card);
}
/* 工具卡正文（入参摘要）钳制两行：长值全文在"参数/结果"折叠面板，正文只保留调用轮廓 */
.act.is-tool-neutral .act-text {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}
/* 参数药丸：键灰值亮的圆角浅底块——入参轮廓一眼可扫，全文在"参数/结果"折叠面板 */
.act-args {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 2px 0 0;
}
.arg-pill {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  max-width: 100%;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  background: var(--bg-active);
  border: 1px solid var(--border-color-light);
  font-size: 11px;
  line-height: 1.7;
}
.arg-key {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-secondary);
}
.arg-key::after {
  content: '·';
  margin-left: 6px;
  color: var(--text-secondary);
}
.arg-value {
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 36ch;
}
.act.is-tool-neutral.is-failed {
  background: var(--bg-card);
  border-left-color: var(--color-danger);
  border-left-width: 2px;
}
.act.is-tool-neutral.is-agent-streamer,
.act.is-tool-neutral.is-agent-game {
  background: var(--bg-card);
}
.act.is-tool-neutral.is-agent-game.is-failed {
  background: var(--bg-card);
  border-left-color: var(--color-danger);
}
/* 成功无显式 is-success 类——沿用 .act 默认的 --color-tool 边线（绿色系）即可 */
.act.is-tool-neutral.is-speak {
  background: var(--bg-card);
}
.act.is-speech {
  background: var(--color-agent-bg);
  border-left: 2px solid var(--color-agent);
}

.act-tool {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 工具调用来源徽标（中性色，与成功/失败红绿徽标互不抢视觉） */
.act-source {
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 10px;
  font-weight: 600;
  background: var(--bg-active);
  color: var(--text-secondary);
  border: 1px solid var(--border-color-light);
  flex-shrink: 0;
}

/* 参数/结果折叠区：形态对齐 LLM 历史页（暗底 JSON 树 + 悬浮复制），
   summary 压到 11px 弱色，避免与卡片主文案争夺层级 */
.t-payload {
  margin-top: 6px;
}
.t-payload summary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.t-payload summary::-webkit-details-marker {
  display: none;
}
.t-payload summary::before {
  content: '▸';
  font-size: 10px;
  transition: transform 0.15s ease;
}
.t-payload[open] summary::before {
  transform: rotate(90deg);
}
.t-payload summary:hover {
  color: var(--text-primary);
}
.t-payload-block {
  margin-top: 6px;
}
.t-payload-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.t-payload-tag {
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--text-secondary);
}
.t-payload-tag--error {
  color: var(--color-danger);
}
.t-payload-head .copy-icon {
  font-size: 12px;
  color: var(--text-placeholder);
  cursor: pointer;
}
.t-payload-head .copy-icon:hover {
  color: var(--text-primary);
}
.t-payload-json {
  margin-top: 4px;
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
}
/* vue-json-pretty 的 key 不自带颜色（继承容器文字色，浅色主题下是深灰），
   深底上必须显式给高对比配色；字符串/数字用比库默认更亮的变体 */
.t-payload-json :deep(.vjs-tree) {
  color: #d4d4d4;
}
.t-payload-json :deep(.vjs-value-string) {
  color: #7ee787;
}
.t-payload-json :deep(.vjs-value-number),
.t-payload-json :deep(.vjs-value-boolean) {
  color: #79c0ff;
}
.t-payload-json :deep(.vjs-value-null) {
  color: #79c0ff;
}
.t-payload-text {
  margin: 4px 0 0;
  background: #1e1e1e;
  border-radius: var(--radius-md);
  padding: 8px 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--color-danger);
}

/* 成功徽标：复用 .act-badge 形状但改用安全色 */
.act-badge.is-success-badge {
  background: var(--color-success);
  color: var(--text-inverse);
}

/* 游戏 Agent 上报（kind='game'）：绿色系左边线 + 浅绿底，沿用 act 卡形态 */
.act.is-agent-game {
  background: var(--color-game-bg);
  border-left-color: var(--color-game);
}
.act.is-agent-game.is-failed {
  background: var(--color-danger-bg);
  border-left-color: var(--color-danger);
}
.act-kind--game {
  color: var(--color-game);
}
.act-note.is-game-note {
  color: var(--text-secondary);
}

.act.is-failed .act-kind {
  color: var(--color-danger);
}

.act.is-speak .act-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
}
.act.is-speak .act-text::before {
  content: '「';
  color: var(--color-tool);
}
.act.is-speak .act-text::after {
  content: '」';
  color: var(--color-tool);
}

/* 环节推进 / 场次边界：横贯分隔行                                 */
.beat {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 0;
}
.beat-rule {
  height: 1px;
  background: var(--color-rundown);
  opacity: 0.45;
}
.beat-rule:first-child {
  width: 24px;
  flex-shrink: 0;
}
.beat-rule:last-of-type {
  flex: 1;
}
.beat-body {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.beat-eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 1.6px;
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.beat-action {
  padding: 0 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: var(--color-rundown-bg);
  color: var(--color-rundown);
  flex-shrink: 0;
}
.beat-note {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 里程碑：庆祝行                                                */
.milestone {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 0;
  padding: 8px 14px;
  border-radius: var(--radius-md);
  background: var(--color-game-bg);
  border: 1px dashed var(--color-game);
}
.milestone-body {
  flex: 1;
  min-width: 0;
}
.milestone-text {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}
.milestone-meta {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--text-secondary);
}

/* 紧凑模式（首页缩略视图）                                        */
.feed-timeline.is-compact .feed {
  gap: 6px;
}
.feed-timeline.is-compact .feed-row {
  /* 取消逐行入场动画，避免首页连续刷新时频繁闪烁 */
  animation: none;
}
.feed-timeline.is-compact .chat,
.feed-timeline.is-compact .decision,
.feed-timeline.is-compact .act {
  max-width: 100%;
}
.feed-timeline.is-compact .say,
.feed-timeline.is-compact .act-text {
  font-size: 12px;
}
.feed-timeline.is-compact .stamp {
  font-size: 9px;
}

/* 会话布局：观众左 / 主播右 / 过程行居中——对话流优先的显示模式      */
.feed-timeline.is-chat .feed::before {
  display: none;
}
.feed-timeline.is-chat .chat {
  max-width: min(72%, 560px);
}
.feed-timeline.is-chat .feed-row.row-right {
  flex-direction: row;
  justify-content: flex-end;
  gap: 10px;
  align-items: flex-start;
}
.feed-timeline.is-chat .feed-row.row-right .act.is-speech {
  margin-left: 0;
  max-width: min(78%, 560px);
}
/* 会话模式：过程行与折叠条跟随主播气泡右对齐（右内边距对齐气泡内缘），
   卡片与折叠条按内容自适应宽度，不再居中也不再撑满整行 */
.feed-timeline.is-chat .feed-row.row-process,
.feed-timeline.is-chat .feed-row.row-strip {
  align-items: flex-end;
  padding-right: 38px; /* 28px 主播头像 + 10px 间距，与发言气泡内缘对齐 */
}
.feed-timeline.is-chat .feed-row.row-process .decision,
.feed-timeline.is-chat .feed-row.row-process .act {
  margin-left: 0;
  max-width: min(78%, 560px);
}

.chat-process-strip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
  max-width: min(78%, 560px);
  padding: 4px 12px;
  border: 1px solid var(--border-color-light);
  border-radius: 999px;
  background: var(--bg-hover);
  font-family: inherit;
  font-size: 11px;
  color: var(--text-secondary);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}
.chat-process-strip:hover {
  border-color: var(--color-agent);
  color: var(--text-primary);
}
.chat-process-strip[aria-expanded='true'] {
  background: var(--color-agent-bg);
  border-color: var(--color-agent);
  color: var(--color-agent);
}
.chat-process-action,
.chat-process-arrow {
  flex-shrink: 0;
}
.chat-process-action {
  font-size: 10px;
  color: var(--text-placeholder);
}
.chat-process-arrow {
  font-size: 10px;
  transition: transform var(--transition-fast);
}
.chat-process-strip[aria-expanded='true'] .chat-process-arrow {
  transform: rotate(90deg);
}
/* 主播侧头像：与观众头像同形，紫色系归到主播 */
.avatar--host {
  border-color: var(--color-agent);
  color: var(--color-agent);
  background: var(--color-agent-bg);
}

/* 窄屏                                                          */
@media (max-width: 860px) {
  .chat,
  .chat--gift,
  .chat--super_chat,
  .decision,
  .act {
    max-width: 100%;
    margin-left: 0;
  }
}
</style>
