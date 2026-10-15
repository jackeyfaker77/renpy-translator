<script setup lang="ts">
/** 全局上传进度对话框：zip 上传是前端传输，服务端任务在上传完成后才
 *  建立。顶栏任务列表点开本对话框——进度 + 可中断；关闭不中断上传。 */
import { computed } from 'vue'
import { NAlert, NButton, NModal, NSpace, NTag, NText } from 'naive-ui'
import { useUploadsStore } from '../stores/uploads'
import ProgressLine from './ProgressLine.vue'

const uploadsStore = useUploadsStore()

const task = computed(() => uploadsStore.dialogTask)
const running = computed(() => task.value?.status === 'uploading')

function onCancel() {
  if (task.value) uploadsStore.cancel(task.value.id)
}

function onClose() {
  uploadsStore.closeDialog()
}
</script>

<template>
  <n-modal
    :show="!!task"
    preset="card"
    :title="task?.label || '上传任务'"
    style="width: min(460px, 96vw)"
    :mask-closable="false"
    @update:show="(v: boolean) => { if (!v) onClose() }"
  >
    <n-space v-if="task" vertical>
      <n-space align="center">
        <n-tag :type="running ? 'info' : task.status === 'cancelled' ? 'warning' : 'error'"
               size="small">
          {{ running ? '上传中' : task.status === 'cancelled' ? '已取消' : '失败' }}
        </n-tag>
        <span style="font-size: 13px; color: #aaa">
          {{ running ? '本地传输，大文件需等待；关闭对话框不会中断上传' : '' }}
        </span>
      </n-space>

      <progress-line :value="task.pct" :processing="running"
                     :status="running ? 'default' : 'error'" />

      <n-alert v-if="!running && task.error" type="warning" title="上传未完成">
        {{ task.error }}
      </n-alert>

      <n-text v-if="running" depth="3" style="font-size: 12px">
        中断后不会创建项目/任务，可回表单重新发起。
      </n-text>
    </n-space>
    <template #footer>
      <n-space justify="end">
        <n-button v-if="running" type="error" quaternary @click="onCancel">
          取消上传
        </n-button>
        <n-button @click="onClose">{{ running ? '关闭（后台继续）' : '关闭' }}</n-button>
      </n-space>
    </template>
  </n-modal>
</template>
