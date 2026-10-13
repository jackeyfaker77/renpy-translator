<script setup lang="ts">
/** 页面帮助：顶部右侧 ? 按钮，弹出说明书对应章节（与 docs/使用说明.md 同源）
 *  section 支持逗号分隔多章节（如 '3,7'） */
import { computed, ref } from 'vue'
import { NButton, NModal, NScrollbar, NText, NTooltip } from 'naive-ui'
import { HelpCircleOutline } from '@vicons/ionicons5'
import { pageHelp } from '../helpContent'

const props = defineProps<{ section: string }>()

const show = ref(false)
const help = computed(() => {
  const parts = props.section.split(',').map((s) => s.trim()).filter(Boolean)
  const items = parts.map((p) => pageHelp(p)).filter(Boolean)
  if (!items.length) return null
  return items as { title: string; html: string }[]
})
</script>

<template>
  <n-tooltip trigger="hover">
    <template #trigger>
      <n-button quaternary circle size="small" @click="show = true">
        <template #icon>
          <HelpCircleOutline />
        </template>
      </n-button>
    </template>
    使用说明
  </n-tooltip>

  <n-modal v-model:show="show" preset="card"
           :title="help?.map((h) => h.title).join(' · ') || '使用说明'"
           style="width: 720px; max-width: 92vw">
    <n-scrollbar style="max-height: 70vh">
      <template v-if="help">
        <template v-for="(h, i) in help" :key="h.title">
          <div v-if="i > 0" style="border-top: 1px solid rgba(255,255,255,0.12); margin: 16px 0" />
          <div class="help-body" v-html="h.html" />
        </template>
      </template>
      <n-text v-else depth="3">暂无本页说明</n-text>
    </n-scrollbar>
  </n-modal>
</template>

<style scoped>
.help-body :deep(h4) {
  margin: 14px 0 6px;
  font-size: 14px;
}
.help-body :deep(p) {
  margin: 6px 0;
  font-size: 13px;
  line-height: 1.7;
}
.help-body :deep(ul),
.help-body :deep(ol) {
  margin: 6px 0;
  padding-left: 22px;
  font-size: 13px;
  line-height: 1.7;
}
.help-body :deep(li) {
  margin: 2px 0;
}
.help-body :deep(code) {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 3px;
  padding: 0 4px;
  font-size: 12px;
}
.help-body :deep(pre) {
  background: rgba(255, 255, 255, 0.06);
  border-radius: 6px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.6;
}
.help-body :deep(pre code) {
  background: none;
  padding: 0;
}
.help-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid #e6a23c;
  background: rgba(230, 162, 60, 0.1);
  border-radius: 4px;
  font-size: 13px;
}
.help-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
}
.help-body :deep(th),
.help-body :deep(td) {
  border: 1px solid rgba(255, 255, 255, 0.18);
  padding: 4px 10px;
  text-align: left;
}
.help-body :deep(hr) {
  border: none;
  border-top: 1px solid rgba(255, 255, 255, 0.12);
  margin: 12px 0;
}
</style>
