<script setup lang="ts">
/** 跨项目导入已翻译文段：选源项目 → 分组预览（直接导入/冲突对比/疑似匹配）→ 落库
 *
 * 冲突（双方译文不同）逐条对比选择 + 批量操作；模糊匹配（相似但差距较大）
 * 一律人工确认，默认不导入。两段式：apply 带预览 digest，数据变化需重预览。
 */
import { computed, h, reactive, ref, watch } from 'vue'
import {
  NButton, NCheckbox, NCheckboxGroup, NDataTable, NEmpty, NModal, NRadioButton,
  NRadioGroup, NSelect, NSpace, NTag, NText, useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import { api, toastError, toastOk } from '../api/client'
import { useSessionStore } from '../stores/session'

type ImportType = 'dialogue' | 'ui' | 'names'

const props = defineProps<{
  show: boolean
  presetTypes: ImportType[]
}>()
const emit = defineEmits<{ 'update:show': [boolean]; imported: [] }>()

const message = useMessage()
const session = useSessionStore()

const TYPE_LABELS: Record<ImportType, string> = {
  dialogue: '对话', ui: 'UI 字符串', names: '人名',
}

interface PreviewRow {
  type: ImportType
  key: number
  original: string
  source_original: string
  current: string
  incoming: string
  score: number
  kind: string
}

const step = ref<1 | 2>(1)
const projects = ref<Array<{ label: string; value: string }>>([])
const source = ref('')
const types = ref<ImportType[]>([])
const loading = ref(false)
const digest = ref('')
const direct = ref<PreviewRow[]>([])
const conflict = ref<PreviewRow[]>([])
const review = ref<PreviewRow[]>([])
const same = ref<Record<string, number>>({})
// 选择状态：direct/review 用勾选；conflict 用 'source' | 'keep'
const picked = reactive<Record<string, boolean>>({})
const conflictChoice = reactive<Record<string, 'source' | 'keep'>>({})

const keyOf = (r: PreviewRow) => `${r.type}:${r.key}`

watch(() => props.show, async (v) => {
  if (!v) return
  step.value = 1
  source.value = ''
  types.value = [...props.presetTypes]
  digest.value = ''
  direct.value = conflict.value = review.value = []
  same.value = {}
  try {
    const data = await api.get<{ projects: Array<{ name: string }> }>(
      '/api/current/import/from-projects')
    projects.value = data.projects.map((p) => ({ label: p.name, value: p.name }))
  } catch (e) {
    toastError(message, e)
  }
})

function close() {
  if (!loading.value) emit('update:show', false)
}

async function preview() {
  if (!source.value) {
    message.warning('请选择源项目')
    return
  }
  if (!types.value.length) {
    message.warning('请选择内容类型')
    return
  }
  loading.value = true
  try {
    const data = await api.post<{
      direct: PreviewRow[]; conflict: PreviewRow[]; review: PreviewRow[]
      same: Record<string, number>; digest: string
    }>('/api/current/import/from-project/preview', {
      source_project: source.value, types: types.value,
    })
    direct.value = data.direct
    conflict.value = data.conflict
    review.value = data.review
    same.value = data.same || {}
    digest.value = data.digest
    for (const r of direct.value) picked[keyOf(r)] = true       // 默认导入
    for (const r of review.value) picked[keyOf(r)] = false      // 疑似默认不导
    for (const r of conflict.value) conflictChoice[keyOf(r)] = 'keep'
    step.value = 2
  } catch (e) {
    toastError(message, e)
  } finally {
    loading.value = false
  }
}

const selectedCount = computed(() =>
  direct.value.filter((r) => picked[keyOf(r)]).length
  + review.value.filter((r) => picked[keyOf(r)]).length
  + conflict.value.filter((r) => conflictChoice[keyOf(r)] === 'source').length)

function batchDirect(v: boolean) {
  for (const r of direct.value) picked[keyOf(r)] = v
}
function batchConflict(v: 'source' | 'keep') {
  for (const r of conflict.value) conflictChoice[keyOf(r)] = v
}
function batchReview(v: boolean, minScore = 0) {
  for (const r of review.value) {
    if (r.score >= minScore) picked[keyOf(r)] = v
  }
}

async function apply() {
  const selections = [
    ...direct.value.filter((r) => picked[keyOf(r)]),
    ...review.value.filter((r) => picked[keyOf(r)]),
    ...conflict.value.filter((r) => conflictChoice[keyOf(r)] === 'source'),
  ].map((r) => ({ type: r.type, key: r.key }))
  if (!selections.length) {
    message.warning('没有选择任何条目')
    return
  }
  loading.value = true
  try {
    const data = await api.post<{ counts: Record<string, number> }>(
      '/api/current/import/from-project/apply', {
        source_project: source.value, types: types.value,
        expected_digest: digest.value, selections,
      })
    const n = Object.values(data.counts).reduce((a, b) => a + b, 0)
    toastOk(message, `已导入 ${n} 条译文`)
    emit('update:show', false)
    emit('imported')
    await session.refresh()
  } catch (e) {
    toastError(message, e)
  } finally {
    loading.value = false
  }
}

const conflictColumns = computed<DataTableColumns<PreviewRow>>(() => [
  { title: '原文', key: 'original', ellipsis: { tooltip: true },
    render: (r) => h('span', { style: 'white-space: pre-wrap' }, r.original) },
  { title: '当前译文', key: 'current', ellipsis: { tooltip: true } },
  { title: `源项目译文（${source.value}）`, key: 'incoming', ellipsis: { tooltip: true } },
  {
    title: '采用', key: 'choice', width: 190,
    render: (r) => h(NRadioGroup, {
      value: conflictChoice[keyOf(r)],
      'onUpdate:value': (v: 'source' | 'keep') => { conflictChoice[keyOf(r)] = v },
    }, () => [
      h(NRadioButton, { value: 'source' }, () => '用源'),
      h(NRadioButton, { value: 'keep' }, () => '保留当前'),
    ]),
  },
])
</script>

<template>
  <n-modal :show="props.show" preset="card" title="从其他项目导入译文"
           style="width: min(1080px, 96vw)" :mask-closable="false"
           @update:show="(v: boolean) => !v && close()">
    <div class="modal-scroll">
    <!-- 第一步：选源 -->
    <n-space v-if="step === 1" vertical>
      <n-space align="center">
        <n-text>源项目</n-text>
        <n-select v-model:value="source" style="width: 280px"
                  :options="projects" placeholder="选择要复用译文的项目" />
      </n-space>
      <n-space align="center">
        <n-text>内容类型</n-text>
        <n-checkbox-group v-model:value="types">
          <n-space>
            <n-checkbox v-for="(label, t) in TYPE_LABELS" :key="t"
                        :value="t" :label="label" />
          </n-space>
        </n-checkbox-group>
      </n-space>
      <n-text depth="3" style="font-size: 12px">
        按原文匹配复用源项目的已翻译条目（术语表不在范围内）。
        归一化会吸收标记/变量名/空白差异；相似但不确定的进「疑似匹配」由你筛选。
      </n-text>
    </n-space>

    <!-- 第二步：分组预览 -->
    <n-space v-else vertical>
      <n-space align="center">
        <n-tag size="small" type="success">可直接导入 {{ direct.length }}</n-tag>
        <n-tag size="small" type="warning">冲突 {{ conflict.length }}</n-tag>
        <n-tag size="small">疑似匹配 {{ review.length }}</n-tag>
        <n-text depth="3" style="font-size: 12px">
          相同跳过 {{ Object.values(same).reduce((a, b) => a + b, 0) }} 条 · 已选 {{ selectedCount }} 条
        </n-text>
      </n-space>

      <template v-if="direct.length">
        <n-space align="center">
          <n-text strong>可直接导入（当前未译）</n-text>
          <n-button size="tiny" quaternary @click="batchDirect(true)">全选</n-button>
          <n-button size="tiny" quaternary @click="batchDirect(false)">全不选</n-button>
        </n-space>
        <div v-for="r in direct" :key="keyOf(r)" class="row">
          <n-checkbox v-model:checked="picked[keyOf(r)]" />
          <span class="orig">{{ r.original }}</span>
          <span class="inc">→ {{ r.incoming }}</span>
        </div>
      </template>

      <template v-if="conflict.length">
        <n-space align="center">
          <n-text strong>冲突对比（双方译文不同，默认保留当前）</n-text>
          <n-button size="tiny" quaternary @click="batchConflict('source')">全部用源</n-button>
          <n-button size="tiny" quaternary @click="batchConflict('keep')">全部保留当前</n-button>
        </n-space>
        <n-data-table size="small" :columns="conflictColumns" :data="conflict"
                      :row-key="(r: PreviewRow) => keyOf(r)" :max-height="240" />
      </template>

      <template v-if="review.length">
        <n-space align="center">
          <n-text strong>疑似匹配（相似但差距较大，请逐条确认）</n-text>
          <n-button size="tiny" quaternary @click="batchReview(true, 0.9)">≥90% 全部导入</n-button>
          <n-button size="tiny" quaternary @click="batchReview(true)">全部导入</n-button>
          <n-button size="tiny" quaternary @click="batchReview(false)">全部不导</n-button>
        </n-space>
        <div v-for="r in review" :key="keyOf(r)" class="row">
          <n-checkbox v-model:checked="picked[keyOf(r)]" />
          <n-tag size="tiny" style="margin: 0 6px">{{ (r.score * 100).toFixed(0) }}%</n-tag>
          <div style="min-width: 0">
            <div class="orig">{{ r.original }}</div>
            <div class="orig">{{ r.source_original }}</div>
            <div class="inc">→ {{ r.incoming }}</div>
          </div>
        </div>
      </template>

      <n-empty v-if="!direct.length && !conflict.length && !review.length"
               description="没有可导入的条目" />
    </n-space>
    </div>

    <template #footer>
      <n-space v-if="step === 1" justify="end">
        <n-button @click="close">取消</n-button>
        <n-button type="primary" :loading="loading" @click="preview">预览</n-button>
      </n-space>
      <n-space v-else justify="end">
        <n-button @click="step = 1">返回</n-button>
        <n-button type="primary" :loading="loading" :disabled="!selectedCount"
                  @click="apply">
          导入 {{ selectedCount }} 条
        </n-button>
      </n-space>
    </template>
  </n-modal>
</template>

<style scoped>
.row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 3px 0;
  font-size: 12px;
}
.orig {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  flex: 1;
  min-width: 0;
}
.inc {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  flex: 1;
  min-width: 0;
  opacity: 0.85;
}
</style>
