<script setup lang="ts">
/** 统一页头：标题 + 作用域副标题 + 本页帮助 + 动作槽
 *  （各页散落的 <h2> + page-help 行收编于此，动作区由步骤 4 接管） */
import { NText } from 'naive-ui'
import PageHelp from './PageHelp.vue'

defineProps<{
  title: string
  subtitle?: string
  helpSection?: string
}>()
</script>

<template>
  <div class="page-header">
    <h2 style="margin: 0">{{ title }}</h2>
    <n-text v-if="subtitle" depth="3" style="font-size: 12px">{{ subtitle }}</n-text>
    <span style="flex: 1" />
    <page-help v-if="helpSection" :section="helpSection" />
    <slot name="actions" />
  </div>
</template>

<style scoped>
/* 不带 margin-bottom：页头常内嵌在工具栏 n-space 行里（align="center"），
   自带下边距会把标题顶得比按钮高、视觉不平齐；页面自己控行距 */
.page-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
