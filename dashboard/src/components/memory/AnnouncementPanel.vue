<template>
  <section class="panel announcement-panel" aria-label="直播间公告">
    <header class="panel-head">
      <div class="head-text">
        <div class="panel-title">直播间公告</div>
        <p class="panel-hint">
          按当前保留期配置拼好的告知文本，贴进直播间简介即可；保留期改了，点“刷新”会跟着变。
        </p>
      </div>
      <div class="head-actions">
        <el-input
          v-model="contact"
          size="small"
          class="contact-input"
          placeholder="联系方式（会写进公告）"
        />
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
        <el-button size="small" type="primary" @click="copy">复制公告</el-button>
      </div>
    </header>

    <p v-if="loadError" class="panel-error">{{ loadError }}</p>
    <el-input
      v-else
      v-loading="loading"
      :model-value="announcement"
      type="textarea"
      :rows="8"
      readonly
      class="announcement-text"
    />
  </section>
</template>

<script setup lang="ts">
/**
 * 直播间公告面板 —— 按保留期配置拼出观众告知文本，供运营者一键复制
 *
 * 文本口径固定（处理的信息种类、保存期限、删除与撤回方式），保留期从
 * 配置合并视图读取（raw_days / viewer_absence_days，0 = 不自动删除）；联系方式
 * 没有配置项，由运营者在输入框里补。文本变化只取决于配置与联系方式，不订阅
 * 事件、无定时器，不需要 keep-alive 生命周期处理。
 */
import { computed, onMounted, ref } from 'vue';
import { ElMessage } from 'element-plus';
import { configApi } from '@/api';
import { getApiErrorMessage } from '@/utils/apiError';
import { composeAnnouncement, readRetention } from '@/utils/announcement';

const retention = ref({ rawDays: 0, viewerAbsenceDays: 0 });
const contact = ref('');
const loading = ref(false);
const loadError = ref('');

const announcement = computed(() =>
  composeAnnouncement({ ...retention.value, contact: contact.value }),
);

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = '';
  try {
    const response = await configApi.getConfig();
    retention.value = readRetention(response.data?.config);
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '保留期配置加载失败');
  } finally {
    loading.value = false;
  }
}

async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(announcement.value);
    ElMessage.success('公告已复制');
  } catch {
    // 非安全上下文或浏览器拒绝剪贴板权限时退回手动复制，不打断页面
    ElMessage.warning('复制失败，请手动选中公告文本复制');
  }
}

onMounted(() => {
  void load();
});
</script>

<style scoped>
.announcement-panel {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-sm);
  padding: 14px 16px;
  border: 1px solid var(--border-color-light);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.panel-head {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-md);
}

.head-text {
  flex: 1;
  min-width: 0;
}

.panel-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--text-primary);
}

.panel-hint {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.head-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-sm);
  flex-shrink: 0;
}

.contact-input {
  width: 200px;
}

.panel-error {
  margin: 0;
  font-size: 12px;
  color: var(--color-danger);
}

.announcement-text {
  font-size: 13px;
}

.announcement-text :deep(.el-textarea__inner) {
  white-space: pre-wrap;
  line-height: 1.7;
}

@media (max-width: 860px) {
  .panel-head {
    flex-direction: column;
  }
  .head-actions {
    width: 100%;
    flex-wrap: wrap;
  }
  .contact-input {
    width: 100%;
  }
}
</style>
