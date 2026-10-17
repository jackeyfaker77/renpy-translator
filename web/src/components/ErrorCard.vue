<script setup lang="ts">
/** 统一错误块：每条错误至少给三个出口——复制 / 打开对应页面 / 重新发起
 *  （可重发 kind 由 jobMeta 白名单决定，未知 kind 不瞎猜） */
import { useRouter } from 'vue-router'
import { NAlert, NButton, NSpace, useMessage } from 'naive-ui'
import { api, copyWithFeedback, toastError, toastOk } from '../api/client'
import { useJobsStore, type JobView } from '../stores/jobs'
import { jobKindMeta, jobReissue } from '../lib/jobMeta'

const props = defineProps<{ job: JobView }>()
const emit = defineEmits<{ handled: [] }>()

const jobsStore = useJobsStore()
const router = useRouter()
const message = useMessage()

const meta = () => jobKindMeta(props.job.kind || '')
const reissue = () => jobReissue(props.job.kind || '', api.post)

async function onReissue() {
  const fn = reissue()
  if (!fn) return
  try {
    const data = await fn() as { job_id: string | null; message?: string }
    if (!data?.job_id) {
      message.info(data?.message || '没有需要处理的内容')
    } else {
      jobsStore.track(data.job_id, undefined, true)
      toastOk(message, '已重新发起任务')
      emit('handled')
    }
  } catch (e) {
    toastError(message, e)
  }
}

function gotoPage() {
  const route = meta().route
  if (!route) return
  jobsStore.closeDialog(props.job.id)
  emit('handled')
  void router.push(route)
}
</script>

<template>
  <n-alert type="error" :title="`任务失败${job.status === 'interrupted' ? '（服务重启中断）' : ''}`">
    <pre style="white-space: pre-wrap; font-size: 12px; max-height: 200px; overflow: auto; margin: 0">{{ job.error }}</pre>
    <n-space size="small" style="margin-top: 8px">
      <n-button size="tiny" @click="copyWithFeedback(message, job.error || '')">
        复制错误信息
      </n-button>
      <n-button v-if="meta().route" size="tiny" @click="gotoPage">打开页面</n-button>
      <n-button v-if="job.status === 'failed' && reissue()" size="tiny"
                @click="onReissue">重新发起</n-button>
    </n-space>
  </n-alert>
</template>
