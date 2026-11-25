<!--
  组件管理页共享详情头：名称 + 状态标签 + 类型条 + 描述 + 操作区。

  采集器 / Agent / 工具三页共用的主从骨架右半部分头部。状态标签的颜色
  三态映射（运行=success / 已停=warning / 未启用=info）由页面算好传入；
  启停按钮、下拉菜单等页面特有操作全部走 actions 插槽，配置入口等
  链接走 links 插槽。
-->
<template>
  <header class="md-detail-header">
    <div class="md-detail-title-block">
      <div class="md-detail-title-row">
        <h1 class="md-detail-name">{{ name }}</h1>
        <el-tag
          v-if="statusLabel"
          :size="statusSize"
          :type="statusType ?? 'info'"
          effect="dark"
          class="md-status-tag"
        >
          {{ statusLabel }}
        </el-tag>
        <span v-if="typeChip" class="md-type-chip">{{ typeChip }}</span>
      </div>
      <p class="md-detail-description">{{ description || '（暂无描述）' }}</p>
      <div v-if="$slots.links" class="md-detail-links">
        <slot name="links" />
      </div>
    </div>
    <div class="md-detail-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    name: string;
    /** 状态标签文案；不传不渲染标签 */
    statusLabel?: string;
    statusType?: 'success' | 'warning' | 'info' | 'danger';
    statusSize?: 'default' | 'large';
    /** 名称旁的类型条文案（如"类型：采集器"）；不传不渲染 */
    typeChip?: string;
    description?: string;
  }>(),
  {
    statusLabel: undefined,
    statusType: 'info',
    statusSize: 'default',
    typeChip: undefined,
    description: '',
  },
);
</script>
