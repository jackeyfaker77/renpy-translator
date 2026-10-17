<script setup lang="ts">
/** 就地提示：挂在真实页面顶部的上下文下一步（关闭后按 key 记住） */
import { ref } from 'vue'
import { NAlert } from 'naive-ui'

const props = defineProps<{
  text: string
  /** localStorage 记忆键（同 key 不再显示） */
  storageKey: string
}>()

const visible = ref(!readDismissed(props.storageKey))

function readDismissed(key: string): boolean {
  try { return localStorage.getItem(key) === '1' } catch { return false }
}

function onClose() {
  visible.value = false
  try { localStorage.setItem(props.storageKey, '1') } catch { /* 忽略 */ }
}
</script>

<template>
  <n-alert v-if="visible" type="info" :show-icon="false" closable
           style="margin-bottom: 10px; font-size: 12px"
           @close="onClose">
    {{ text }}
  </n-alert>
</template>
