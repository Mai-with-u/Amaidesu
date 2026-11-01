<template>
  <el-card class="speak-test" shadow="never">
    <template #header>
      <div class="card-header">
        <span>口型试播</span>
        <el-tag :type="ttsAvailable ? 'success' : 'info'" size="small">
          {{ ttsAvailable ? 'TTS 已装配' : 'TTS 引擎未装配' }}
        </el-tag>
      </div>
    </template>

    <p class="card-tip">
      输入一句话经扬声器试说：嘴随声音开合，走的是和主播真实发言同一条口型链路。试播不携带情绪，
      表情不会跟着变（想连表情一起看，先在配方编辑里试表情）。
      <span v-if="manual" class="manual-hint"
        >当前手动摆姿中，嘴部通道被滑条覆盖，先关掉再看口型。</span
      >
    </p>

    <div class="speak-row">
      <el-input
        v-model="text"
        placeholder="输入要试说的一句话"
        :disabled="!ttsAvailable"
        @keyup.enter="speak"
      />
      <el-button type="primary" :disabled="!ttsAvailable" :loading="speaking" @click="speak">
        试说一句
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
/**
 * 口型试播：在调校页直接试一句 TTS，看形象嘴型与元音口型。
 *
 * 复用调试页的试说端点（POST /debug/tts/test，play_on_device=true）——那条
 * 路径经 TTS 引擎 handle_speech 完整生产链路，音频分接口型分析器，与真实
 * 发言同一条嘴链路，本组件不做任何新后端。
 *
 * 生命周期语义（keep-alive）：无定时器、无订阅；TTS 装配状态在挂载与每次
 * 页面激活时各查一次（装配可以在页面缓存期间被改），查询失败按未装配处理，
 * 试说按钮的错误提示里仍会给出后端原因。
 */
import { computed, onActivated, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { debugApi } from '@/api';
import type { TtsStatusResponse } from '@/types';
import { getApiErrorMessage } from '@/utils/apiError';

defineProps<{ manual?: boolean }>();

const text = ref('');
const ttsStatus = ref<TtsStatusResponse | null>(null);
const speaking = ref(false);

const ttsAvailable = computed(() => ttsStatus.value?.available ?? false);

async function loadStatus(): Promise<void> {
  try {
    const { data } = await debugApi.getTtsStatus();
    ttsStatus.value = data;
  } catch {
    ttsStatus.value = null;
  }
}

async function speak(): Promise<void> {
  const content = text.value.trim();
  if (!content) {
    ElMessage.warning('请先输入要试说的一句话');
    return;
  }
  speaking.value = true;
  try {
    const { data } = await debugApi.testTts({ text: content, play_on_device: true });
    if (data.success) {
      ElMessage.success('已试说，看形象的嘴');
    } else {
      ElMessage.error(data.error || '试说失败');
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '试说失败'));
  } finally {
    speaking.value = false;
  }
}

onMounted(() => void loadStatus());
onActivated(() => void loadStatus());
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.manual-hint {
  color: var(--el-color-warning);
}

.speak-row {
  display: flex;
  gap: 8px;
}
</style>
