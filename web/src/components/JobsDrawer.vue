<script setup lang="ts">
/** 任务中心抽屉：等待确认 / 进行中 / 按天历史 + 行操作
 *  数据：活跃任务走现有 SSE（jobs store），历史 GET /api/jobs?limit=50
 *  （仅打开抽屉/终态/手动刷新时拉取，不轮询）。 */
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NDrawer, NEmpty, NSpace, NSpin, NTag, NText,
  useMessage,
} from 'naive-ui'
import { api, copyWithFeedback, toastError, toastOk } from '../api/client'
import { useJobsStore, type JobRecord, type JobView } from '../stores/jobs'
import { useUploadsStore } from '../stores/uploads'
import { jobKindMeta, jobReissue } from '../lib/jobMeta'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const jobsStore = useJobsStore()
const uploadsStore = useUploadsStore()
const router = useRouter()
const message = useMessage()
const loading = ref(false)

type Row = Partial<JobRecord> & { id: string }

/** 活跃 + 历史合并去重（同 id 以活跃视图为准）+ 上传任务同列展示 */
const rows = computed<Row[]>(() => {
  const live = [...jobsStore.jobs.values()]
  const liveIds = new Set(live.map((j) => j.id))
  const hist: Row[] = jobsStore.history.filter((h) => !liveIds.has(h.id))
  const uploads: Row[] = uploadsStore.backgroundTasks.map((t) => ({
    id: 'up:' + t.id, kind: 'upload', label: `${t.label}（上传中）`,
    status: 'running', progress: t.pct, stage: 'zip 传输', payload: null,
    error: null,
  }))
  return [...uploads, ...live, ...hist]
})

const waiting = computed(() => rows.value.filter((r) => r.status === 'waiting_input'))
const running = computed(() => rows.value.filter((r) => r.status === 'running'))
const rest = computed(() => rows.value.filter(
  (r) => r.status !== 'waiting_input' && r.status !== 'running'))

/** 按天分组：today / yesterday / 更早 */
function dayKey(iso?: string): 'today' | 'yesterday' | 'earlier' {
  if (!iso) return 'earlier'
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return 'earlier'
  const now = new Date()
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  if (t >= startToday) return 'today'
  if (t >= startToday - 86400_000) return 'yesterday'
  return 'earlier'
}

const historyGroups = computed(() => {
  const groups: Record<string, Row[]> = { today: [], yesterday: [], earlier: [] }
  const sorted = [...rest.value].sort(
    (a, b) => Date.parse(b.created_at || '') - Date.parse(a.created_at || ''))
  for (const r of sorted) groups[dayKey(r.created_at)].push(r)
  return groups
})

const statusTag = (s?: string) =>
  (({ succeeded: 'success', failed: 'error', interrupted: 'warning',
      cancelled: 'default', waiting_input: 'warning' } as Record<string, 'success' | 'error' | 'warning' | 'default'>)[s ?? ''] ?? 'default')
const statusText = (s?: string) =>
  (({ succeeded: '完成', failed: '失败', interrupted: '中断', cancelled: '已取消',
      running: '进行中', waiting_input: '等待确认' } as Record<string, string>)[s ?? ''] ?? s ?? '')

function timeOf(iso?: string): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  return new Date(t).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function refresh() {
  loading.value = true
  await jobsStore.loadHistory()
  loading.value = false
}

// 打开抽屉：拉历史 + 标记已读
watch(() => props.show, (v) => {
  if (v) {
    void refresh()
    jobsStore.markTasksSeen()
  }
})

function openDetail(r: Row) {
  if (r.id.startsWith('up:')) {
    uploadsStore.openDialog(r.id.slice(3))
    emit('update:show', false)
    return
  }
  jobsStore.track(r.id, r as Partial<JobView>, true)
}

function openPage(r: Row) {
  const meta = jobKindMeta(r.kind || '')
  if (!meta.route) return
  void router.push(meta.route)
  emit('update:show', false)
}

function copyError(r: Row) {
  if (r.error) void copyWithFeedback(message, r.error)
}

async function reissue(r: Row) {
  const fn = jobReissue(r.kind || '', api.post)
  if (!fn) return
  try {
    const data = await fn() as { job_id: string | null; message?: string }
    if (!data?.job_id) {
      message.info(data?.message || '没有需要处理的内容')
    } else {
      jobsStore.track(data.job_id, undefined, true)
      toastOk(message, '已重新发起任务')
      emit('update:show', false)
    }
    await refresh()
  } catch (e) {
    toastError(message, e)
  }
}
</script>

<template>
  <n-drawer :show="props.show" :width="420" placement="right"
            @update:show="(v: boolean) => emit('update:show', v)">
    <n-drawer-content title="任务" closable>
      <n-space align="center" justify="space-between" style="margin-bottom: 8px">
        <n-text depth="3" style="font-size: 12px">关掉进度框后任务仍在，历史刷新后也在</n-text>
        <n-button size="tiny" quaternary :loading="loading" @click="refresh">刷新</n-button>
      </n-space>

      <!-- 等待你确认 -->
      <template v-if="waiting.length">
        <n-text strong style="display: block; margin: 10px 0 6px">
          等待你确认（{{ waiting.length }}）
        </n-text>
        <div v-for="r in waiting" :key="r.id" class="task-row">
          <n-tag size="tiny" type="warning">⚠</n-tag>
          <span class="task-label">{{ jobKindMeta(r.kind || '').label || r.label || '任务' }}</span>
          <n-button size="tiny" type="primary" @click="openDetail(r)">打开确认 →</n-button>
        </div>
      </template>

      <!-- 进行中 -->
      <template v-if="running.length">
        <n-text strong style="display: block; margin: 10px 0 6px">
          进行中（{{ running.length }}）
        </n-text>
        <div v-for="r in running" :key="r.id" class="task-row">
          <n-tag size="tiny" type="info">⟳</n-tag>
          <span class="task-label">
            {{ jobKindMeta(r.kind || '').label || r.label || '任务' }}
            <n-text depth="3" style="font-size: 12px">
              {{ Math.round((r.progress ?? 0) * 100) }}% · {{ r.stage || '' }}
            </n-text>
          </span>
          <n-button size="tiny" @click="openDetail(r)">详情</n-button>
        </div>
      </template>

      <!-- 历史（按天） -->
      <template v-for="group in ['today', 'yesterday', 'earlier']" :key="group">
        <template v-if="historyGroups[group].length">
          <n-text depth="3" style="display: block; margin: 12px 0 6px">
            {{ group === 'today' ? '今天' : group === 'yesterday' ? '昨天' : '更早' }}
          </n-text>
          <div v-for="r in historyGroups[group]" :key="r.id" class="task-row">
            <n-tag size="tiny" :type="statusTag(r.status)">{{ statusText(r.status) }}</n-tag>
            <span class="task-label">
              {{ jobKindMeta(r.kind || '').label || r.label || r.kind }}
              <n-text depth="3" style="font-size: 12px">{{ timeOf(r.created_at) }}</n-text>
            </span>
            <n-button size="tiny" quaternary @click="openDetail(r)">详情</n-button>
            <n-button v-if="jobKindMeta(r.kind || '').route" size="tiny" quaternary
                      @click="openPage(r)">页面</n-button>
            <n-button v-if="r.status === 'failed' && r.error" size="tiny" quaternary
                      @click="copyError(r)">复制</n-button>
            <n-button v-if="r.status === 'failed' && jobReissue(r.kind || '', api.post)"
                      size="tiny" quaternary @click="reissue(r)">重发</n-button>
          </div>
        </template>
      </template>

      <n-empty v-if="!rows.length && !loading" description="还没有任务记录"
               style="margin-top: 40px" />
      <n-spin v-if="loading" style="width: 100%; text-align: center; margin-top: 20px" />
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.task-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  margin-bottom: 6px;
  font-size: 13px;
}
.task-label {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
