/** 任务入口 composable：api.post 拿 job_id → track → 终态 watch → 刷新会话。
 *  每个 jobId 独立 watch（触发后自动 stop），替代页面共用一个 watch 源的写法。
 *  终态通知规则（任务中心三渠道之一）：toast 只用于「已经结束」——
 *  成功 2.5s；失败 8s 且带「查看任务」（打开任务中心，不承担唯一入口）。 */
import { computed, h, ref, watch } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import { toastError } from '../api/client'
import { useJobsStore, JOB_TERMINAL_STATUS } from '../stores/jobs'
import { useSessionStore } from '../stores/session'

interface JobStartResponse {
  job_id: string | null
  message?: string
}

interface RunJobOptions {
  /** 后端返回 job_id=null（无待处理内容）时的提示，默认展示 data.message */
  emptyMessage?: string
}

export function useJobTask() {
  const message = useMessage()
  const jobsStore = useJobsStore()
  const session = useSessionStore()

  const activeJobId = ref('')
  const isRunning = computed(() => {
    const job = activeJobId.value ? jobsStore.jobs.get(activeJobId.value) : undefined
    return !!job && !JOB_TERMINAL_STATUS.includes(job.status)
  })

  /** 发起任务并跟踪；终态时自动 session.refresh() 后回调 onTerminal(status) */
  async function runJob(
    request: () => Promise<JobStartResponse>,
    onTerminal?: (status: string) => void | Promise<void>,
    options: RunJobOptions = {},
  ): Promise<void> {
    let data: JobStartResponse
    try {
      data = await request()
    } catch (e) {
      toastError(message, e)
      return
    }
    if (!data.job_id) {
      message.info(data.message ?? options.emptyMessage ?? '没有待处理的内容')
      return
    }
    const jobId = data.job_id
    activeJobId.value = jobId
    jobsStore.track(jobId)
    const stop = watch(
      () => jobsStore.jobs.get(jobId)?.status,
      async (status) => {
        if (status && JOB_TERMINAL_STATUS.includes(status)) {
          stop()
          await session.refresh()
          await onTerminal?.(status)
          toastTerminal(jobId, status)
        }
      })
  }

  /** 终态 toast：已结束的简报；失败带「查看任务」打开任务中心 */
  function toastTerminal(jobId: string, status: string) {
    const label = jobsStore.jobs.get(jobId)?.label || '任务'
    if (status === 'succeeded') {
      message.success(`${label} 已完成`, { duration: 2500 })
    } else if (status === 'failed') {
      message.error(`${label} 失败`, {
        duration: 8000,
        render: () => h(NButton, {
          size: 'tiny', quaternary: true, type: 'error',
          onClick: () => window.dispatchEvent(new CustomEvent('rt:open-jobs')),
        }, () => '查看任务 →'),
      })
    } else if (status === 'interrupted') {
      message.warning(`${label} 被中断（服务重启）`, { duration: 5000 })
    } else if (status === 'cancelled') {
      message.info(`${label} 已取消`, { duration: 2500 })
    }
  }

  return { activeJobId, isRunning, runJob }
}
