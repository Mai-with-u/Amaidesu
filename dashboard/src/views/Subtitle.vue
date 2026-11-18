<template>
  <div class="subtitle-container" :style="containerStyle">
    <div v-if="isPreview && widgetDisabled" class="preview-disabled-banner">
      字幕小部件已停用，到管理页开启并重启后生效
    </div>
    <div v-else-if="isPreview && !everReceivedSubtitle" class="preview-placeholder">
      字幕小部件预览——主播说话后此处显示实时字幕
    </div>
    <div v-if="subtitle" class="subtitle-area" :style="areaStyle">
      <div v-if="replyQuote" class="reply-quote" :style="replyQuoteStyle">
        <span class="reply-quote-name">{{ replyQuote.name }}</span>
        <span class="reply-quote-text">{{ replyQuote.text }}</span>
      </div>
      <span class="subtitle-text" :style="textStyle">{{ subtitle }}</span>
    </div>
    <WidgetStatusBadge :status="status" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';

import { configApi } from '@/api';
import { useRawWebSocket } from '@/composables/useRawWebSocket';
import { isPreviewMode } from '@/composables/useWidgetCatalog';
import WidgetStatusBadge from '@/components/widget/WidgetStatusBadge.vue';

/**
 * 字幕嵌入页（供 OBS/直播姬浏览器源捕获的全屏透明页）。
 *
 * 样式与位置由后端随每条字幕下发（字号/颜色/背景/边框/位置，来源是
 * infra [dashboard] 的字幕小部件配置）。字幕跟随语音生命周期显示：
 * 开始说话时出现、说完由清空消息撤下——**不按 duration_ms 自动隐藏**，
 * 直播画面上字幕中途消失等于不可见（payload 里的 duration_ms 是
 * 无消费者的遗留字段，留待配置域清理）。后端随帧带下 reply_user_name /
 * reply_text（本句在回复的那条观众发言）时，在主字幕上方渲染引用行，
 * 观众能看到主播正在回谁的哪条发言；随字幕消息整体出现/撤下。
 * 断线提示复用 WidgetStatusBadge（预览态醒目、嵌入态淡化）。
 */

interface SubtitleData {
  type: string;
  text: string;
  duration_ms?: number;
  font_size?: number;
  font_color?: string;
  background_color?: string;
  border_color?: string;
  position?: string;
  reply_user_name?: string | null;
  reply_text?: string | null;
}

type SubtitlePosition = 'top' | 'bottom' | 'center';

const subtitle = ref<string>('');
const fontSize = ref<number>(32);
const fontColor = ref<string>('#ffffff');
const backgroundColor = ref<string>('rgba(0,0,0,0.45)');
const borderColor = ref<string>('#ff8800');
const position = ref<SubtitlePosition>('bottom');
const replyQuote = ref<{ name: string; text: string } | null>(null);
/** 是否收到过至少一条字幕：预览态空态占位据此撤下（字幕被清空不算"从未收到"） */
const everReceivedSubtitle = ref(false);
/** 预览态（管理页"打开"带 ?preview=1）才显示占位与停用提示；嵌入态保持全透明 */
const isPreview = isPreviewMode();
/** 预览态停用判定：false 才显示横幅，读取失败按"未知"处理（不显示横幅，占位照旧） */
const widgetDisabled = ref(false);

const containerStyle = computed(() => ({
  alignItems:
    position.value === 'top' ? 'flex-start' : position.value === 'center' ? 'center' : 'flex-end',
  paddingBottom: position.value === 'bottom' ? '30px' : '0',
  paddingTop: position.value === 'top' ? '30px' : '0',
}));

const areaStyle = computed(() => ({
  padding: '12px 30px',
  background: backgroundColor.value,
  borderLeft: `3px solid ${borderColor.value}`,
  borderRadius: '6px',
  backdropFilter: 'blur(4px)',
}));

const textStyle = computed(() => ({
  fontSize: `${fontSize.value}px`,
  fontWeight: 'bold',
  color: fontColor.value,
  textShadow:
    '-2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 0 10px rgba(0,0,0,0.8)',
  letterSpacing: '2px',
  lineHeight: '1.4',
}));

const replyQuoteStyle = computed(() => ({
  fontSize: `${Math.max(18, Math.round(fontSize.value * 0.6))}px`,
  color: fontColor.value,
  opacity: '0.8',
  textShadow:
    '-2px -2px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, 2px 2px 0 #000, 0 0 10px rgba(0,0,0,0.8)',
}));

const { status } = useRawWebSocket('/ws/widget/subtitle', {
  onMessage: event => {
    try {
      const data: SubtitleData = JSON.parse(event.data);
      if (data.type === 'subtitle') {
        subtitle.value = data.text || '';
        everReceivedSubtitle.value = true;
        if (data.font_size) fontSize.value = data.font_size;
        if (data.font_color) fontColor.value = data.font_color;
        if (data.background_color) backgroundColor.value = data.background_color;
        if (data.border_color) borderColor.value = data.border_color;
        if (data.position === 'top' || data.position === 'bottom' || data.position === 'center') {
          position.value = data.position;
        }
        if (data.reply_user_name && data.reply_text) {
          replyQuote.value = { name: data.reply_user_name, text: data.reply_text };
        } else {
          replyQuote.value = null;
        }
      }
    } catch (e) {
      console.error('解析消息失败:', e);
    }
  },
});

// 预览态停用判定：只读配置做展示，不改字幕数据流。读取失败按"未知"处理
if (isPreview) {
  void (async () => {
    try {
      const resp = await configApi.getConfig();
      const cfg = (resp.data?.config ?? {}) as Record<string, unknown>;
      const dashboard = cfg['dashboard'];
      const widget =
        dashboard && typeof dashboard === 'object'
          ? (dashboard as Record<string, unknown>)['subtitle_widget']
          : undefined;
      const enabled =
        widget && typeof widget === 'object'
          ? (widget as Record<string, unknown>)['enabled']
          : undefined;
      widgetDisabled.value = enabled === false;
    } catch (error) {
      console.error('读取字幕小部件配置失败', error);
    }
  })();
}
</script>

<style>
html,
body,
#app {
  background: transparent !important;
}
</style>

<style scoped>
.subtitle-container {
  width: 100%;
  height: 100vh;
  background: transparent !important;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  box-sizing: border-box;
  font-family: 'PingFang SC', 'Microsoft YaHei', 'Noto Sans CJK SC', sans-serif;
}

.subtitle-area {
  text-align: center;
  z-index: 100;
}

/* 以下两条只在预览态渲染（嵌入态不进模板分支），样式轻量、不遮全屏 */
.preview-placeholder {
  color: rgba(128, 128, 128, 0.85);
  font-size: 18px;
}

.preview-disabled-banner {
  padding: 10px 18px;
  border-radius: 6px;
  background: rgba(255, 136, 0, 0.85);
  color: #fff;
  font-size: 18px;
  font-weight: bold;
}

.reply-quote {
  margin-bottom: 6px;
  max-width: 72vw;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: normal;
}

.reply-quote-name {
  font-weight: bold;
  margin-right: 0.4em;
}

.reply-quote-text {
  font-weight: normal;
}
</style>
