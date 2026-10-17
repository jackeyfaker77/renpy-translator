<script setup lang="ts">
/** 文本翻译页（UI 字符串 / 对话共用，contentType 区分） */
import { computed, h, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  NButton, NCard, NCheckbox, NDataTable, NDropdown, NEmpty, NInput, NModal,
  NPagination, NPopconfirm, NSelect, NSpace, NSwitch, NText, NInputGroup,
  useDialog, useMessage,
} from 'naive-ui'
import type { DataTableColumns, DataTableSortState } from 'naive-ui'
import { BookOutline, CodeSlashOutline, DownloadOutline, LanguageOutline, LocateOutline, RefreshOutline, ShareOutline, SparklesOutline, WarningOutline } from '@vicons/ionicons5'
import { api, toastError, toastOk } from '../api/client'
import { renderIcon } from '../components/icons'
import ExportTextsDialog from '../components/ExportTextsDialog.vue'
import FailedBatchesDialog from '../components/FailedBatchesDialog.vue'
import ImportFromProjectDialog from '../components/ImportFromProjectDialog.vue'
import ImportTextsDialog from '../components/ImportTextsDialog.vue'
import PageHeader from '../components/PageHeader.vue'
import { useJobTask } from '../composables/useJobTask'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ contentType: 'ui' | 'dialogue' }>()
const message = useMessage()
const session = useSessionStore()
const { runJob } = useJobTask()

interface Row {
  id: number
  character?: string
  original_text: string
  translated_text: string
  label?: string
  file_path?: string
  line_number?: number
  context_hint?: string
}

const rows = ref<Row[]>([])
const total = ref(0)
const loading = ref(false)
const query = reactive({
  page: 0, size: 50, filter_mode: 'all', search: '', character: '',
  sort_by: '', sort_order: 'asc' as 'asc' | 'desc',
})
const characters = ref<Array<{ label: string; value: string }>>([])
const translatingIds = ref<Set<number>>(new Set())
const failedBatchCount = ref(0)
const failedDialogVisible = ref(false)
const crossImportVisible = ref(false)

const isDialogue = computed(() => props.contentType === 'dialogue')
const title = computed(() => (isDialogue.value ? '对话翻译' : '字符串翻译'))
let loadVersion = 0
onUnmounted(() => { loadVersion++ })

async function load() {
  const version = ++loadVersion
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: String(query.page), size: String(query.size),
      filter_mode: query.filter_mode, search: query.search,
      character: query.character,
      sort_by: query.sort_by, sort_order: query.sort_order,
    })
    const data = await api.get<{ rows: Row[]; total: number }>(
      `/api/current/texts/${props.contentType}?${params}`, AbortSignal.timeout(30000))
    if (version !== loadVersion) return
    rows.value = data.rows
    total.value = data.total
    void loadFailedCount()
  } catch (e) {
    if (version === loadVersion) toastError(message, e)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

async function loadFailedCount() {
  const version = loadVersion
  try {
    const data = await api.get<{ count: number }>(
      `/api/current/texts/${props.contentType}/failed-batches/count`, AbortSignal.timeout(30000))
    if (version === loadVersion) failedBatchCount.value = data.count
  } catch { /* 计数失败不挡主流程 */ }
}

async function loadCharacters() {
  if (!isDialogue.value) return
  const data = await api.get<{ characters: string[]; variable_map: Record<string, string> }>(
    `/api/current/texts/${props.contentType}/characters`)
  characters.value = [
    { label: '全部角色', value: '' },
    ...data.characters.map((v) => ({
      label: data.variable_map[v] ? `${data.variable_map[v]} (${v})` : v,
      value: v,
    })),
  ]
}

// 独立编辑窗：输入状态不参与表格列重建，中文输入/失焦不触发保存。
const editRow = ref<Row | null>(null)
const editingText = ref('')
const saving = ref(false)
function startEdit(row: Row) {
  editRow.value = row
  editingText.value = row.translated_text || ''
}
async function saveEdit() {
  if (!editRow.value || saving.value) return
  saving.value = true
  try {
    await api.patch(`/api/current/texts/${props.contentType}/${editRow.value.id}`, {
      translated_text: editingText.value,
    })
    editRow.value.translated_text = editingText.value
    editRow.value = null
    toastOk(message, '译文已保存')
    await load()
  } catch (e) {
    toastError(message, e)
  } finally {
    saving.value = false
  }
}
function editKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault()
    saveEdit()
  }
}

// 行内一键清空译文：空串 PATCH 即清空，后端会把 is_translated 回退 0。
async function clearText(row: Row) {
  try {
    await api.patch(`/api/current/texts/${props.contentType}/${row.id}`, {
      translated_text: '',
    })
    row.translated_text = ''
    toastOk(message, '译文已清空')
    await load()
  } catch (e) {
    toastError(message, e)
  }
}

// ---- 多选清空：勾选跨页保持，可全选筛选结果 / 本页全选 / 逐行手选 ----
const selectedIds = ref<number[]>([])
const selectedSet = computed(() => new Set(selectedIds.value))

function toggleRow(id: number, v: boolean) {
  const s = new Set(selectedIds.value)
  if (v) s.add(id)
  else s.delete(id)
  selectedIds.value = [...s]
}

const pageChecked = computed(() =>
  rows.value.length > 0 && rows.value.every((r) => selectedSet.value.has(r.id)))
const pageIndeterminate = computed(() =>
  !pageChecked.value && rows.value.some((r) => selectedSet.value.has(r.id)))

function togglePage(v: boolean) {
  const s = new Set(selectedIds.value)
  for (const r of rows.value) {
    if (v) s.add(r.id)
    else s.delete(r.id)
  }
  selectedIds.value = [...s]
}

async function selectAllFiltered() {
  try {
    const params = new URLSearchParams({
      filter_mode: query.filter_mode, search: query.search,
      character: query.character,
    })
    const data = await api.get<{ ids: number[] }>(
      `/api/current/texts/${props.contentType}/ids?${params}`)
    selectedIds.value = data.ids
    toastOk(message, `已选中 ${data.ids.length} 条（全部筛选结果，含未显示页）`)
  } catch (e) {
    toastError(message, e)
  }
}

async function clearSelected() {
  if (!selectedIds.value.length) return
  try {
    const data = await api.post<{ cleared: number }>(
      `/api/current/texts/${props.contentType}/clear`,
      { ids: selectedIds.value })
    toastOk(message, `已清空 ${data.cleared} 条译文`)
    selectedIds.value = []
    await load()
  } catch (e) {
    toastError(message, e)
  }
}

function resetSelection() {
  selectedIds.value = []
}

function searchKeydown(e: KeyboardEvent) {
  // 确认输入法候选字时不触发搜索和表格刷新。
  if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return
  query.page = 0
  selectedIds.value = []  // 搜索范围变了，旧选择作废
  void load()
}

// ---- 翻译模式（深度/轻量/纯批量 + 细粒度开关，存项目 meta） ----
interface TranslateFlags {
  term_extract: boolean
  glossary: boolean
  style_guide: boolean
  character_profile: 'full' | 'advice_only' | 'off'
  context: boolean
}
const translatePreset = ref('deep')
const translateFlags = ref<TranslateFlags>({
  term_extract: true, glossary: true, style_guide: true,
  character_profile: 'full', context: true,
})
const tuneOpen = ref(false)
const savingOptions = ref(false)

const presetLabels: Record<string, string> = {
  deep: '深度', light: '轻量', bulk: '纯批量', custom: '自定义',
}
const presetHints: Record<string, string> = {
  deep: '全效果：人物特征 + 术语提取 + 风格指南 + 上下文（质量优先）',
  light: '轻量：仅术语表与「翻译建议」注入（成本减半）',
  bulk: '纯批量：无任何附加效果（最快最省）',
  custom: '自定义：手动微调了开关',
}
const presetOptions = computed(() => [
  { label: '深度', value: 'deep' },
  { label: '轻量', value: 'light' },
  { label: '纯批量', value: 'bulk' },
  ...(translatePreset.value === 'custom'
    ? [{ label: '自定义', value: 'custom' }] : []),
])
const profileOptions = [
  { label: '全量', value: 'full' },
  { label: '仅翻译建议', value: 'advice_only' },
  { label: '关闭', value: 'off' },
]

async function loadTranslateOptions() {
  try {
    const data = await api.get<{ preset: string; flags: TranslateFlags }>(
      '/api/current/translate-options')
    translatePreset.value = data.preset
    translateFlags.value = data.flags
  } catch { /* 无项目/接口失败不挡页面 */ }
}

async function applyPreset(v: string) {
  if (v === 'custom' || savingOptions.value) return
  savingOptions.value = true
  try {
    const data = await api.put<{ preset: string; flags: TranslateFlags }>(
      '/api/current/translate-options', { preset: v })
    translatePreset.value = data.preset
    translateFlags.value = data.flags
    toastOk(message, `翻译模式：${presetLabels[v] || v}`)
  } catch (e) {
    toastError(message, e)
  } finally {
    savingOptions.value = false
  }
}

async function setFlag(key: keyof TranslateFlags, value: boolean | string) {
  if (savingOptions.value) return
  savingOptions.value = true
  try {
    const data = await api.put<{ preset: string; flags: TranslateFlags }>(
      '/api/current/translate-options', { flags: { [key]: value } })
    translatePreset.value = data.preset
    translateFlags.value = data.flags
  } catch (e) {
    toastError(message, e)
    await loadTranslateOptions()
  } finally {
    savingOptions.value = false
  }
}

// 字面替换：当前筛选范围的全部页；预览签名防止后台翻译变化后误改。
interface ReplacementPreview {
  count: number
  occurrences: number
  digest: string
  samples: Array<{ id: number; original_text: string; before: string; after: string }>
}
const replaceVisible = ref(false)
const replaceForm = reactive({ find: '', replacement: '' })
const replacePreview = ref<ReplacementPreview | null>(null)
const replacing = ref(false)
watch(() => [replaceForm.find, replaceForm.replacement, query.search,
  query.filter_mode, query.character, props.contentType], () => { replacePreview.value = null })
async function replaceTexts(apply = false) {
  if (replacing.value || !replaceForm.find || (apply && !replacePreview.value)) return
  replacing.value = true
  const request = {
    ...replaceForm, filter_mode: query.filter_mode,
    search: query.search, character: query.character,
    expected_digest: apply ? replacePreview.value!.digest : '',
  }
  try {
    const data = await api.post<ReplacementPreview>(
      `/api/current/texts/${props.contentType}/replace`, request)
    if (apply) {
      toastOk(message, `已替换 ${data.count} 条译文中的 ${data.occurrences} 处文字`)
      replacePreview.value = null
      await load()
    } else {
      replacePreview.value = data
    }
  } catch (e) {
    replacePreview.value = null
    toastError(message, e)
  } finally {
    replacing.value = false
  }
}

// ---- 导出翻译文本（快照当前筛选，避免导出期间改动造成歧义） ----
const exportVisible = ref(false)
const importVisible = ref(false)
const exportFilter = ref<{
  content_type: 'dialogue' | 'ui'; filter_mode: string; search: string; character: string
} | null>(null)
function openExport() {
  exportFilter.value = {
    content_type: props.contentType,
    filter_mode: query.filter_mode,
    search: query.search,
    character: query.character,
  }
  exportVisible.value = true
}
function openImport() {
  exportFilter.value = {
    content_type: props.contentType,
    filter_mode: query.filter_mode,
    search: query.search,
    character: query.character,
  }
  importVisible.value = true
}
async function onImported() {
  await load()
  await session.refresh()
}

// ---- 服务端排序（单列受控） ----
const sortableKeys = computed(() =>
  isDialogue.value ? ['original_text', 'character'] : ['original_text'])

function onSorterChange(sorter: DataTableSortState | null) {
  if (sorter && sorter.order && sortableKeys.value.includes(String(sorter.columnKey))) {
    query.sort_by = String(sorter.columnKey)
    query.sort_order = sorter.order === 'descend' ? 'desc' : 'asc'
  } else {
    query.sort_by = ''
    query.sort_order = 'asc'
  }
  query.page = 0
  load()
}

/** 列的受控排序态 */
function sortOrderOf(key: string): 'ascend' | 'descend' | false {
  if (query.sort_by !== key) return false
  return query.sort_order === 'desc' ? 'descend' : 'ascend'
}

// ---- 单条翻译 ----
async function translateOne(row: Row) {
  translatingIds.value.add(row.id)
  try {
    const data = await api.post<{ translated_text: string }>(
      `/api/current/texts/${props.contentType}/${row.id}/translate`)
    row.translated_text = data.translated_text
    await session.refresh()
  } catch (e) {
    toastError(message, e)
  } finally {
    translatingIds.value.delete(row.id)
  }
}

// ---- 批量翻译（任务）：终态后刷新表格与统计；有失败条目时自动打开核验对话框 ----
async function onTranslateJobDone() {
  await load()
  await loadFailedCount()
  if (failedBatchCount.value > 0) failedDialogVisible.value = true
}

async function translatePage() {
  await runJob(
    () => api.post(`/api/current/texts/${props.contentType}/translate-page`, { ...query }),
    onTranslateJobDone,
    { emptyMessage: '本页没有待翻译的内容' })
}

// ---- 全部翻译二级菜单：全部 / 当前筛选 / 指定角色，启动前先确认 ----
//（任务取消要等当前批次结束，点错代价大——确认框是主要防呆）
const dialog = useDialog()
const roleModalVisible = ref(false)
const rolePick = ref('')

const roleOptions = computed(() =>
  characters.value.filter((c) => c.value))

const translateMenuOptions = computed(() => [
  { key: 'all', label: '全部翻译（所有未翻译）' },
  { key: 'filtered', label: `翻译当前筛选（共 ${total.value} 条）`, disabled: total.value === 0 },
  ...(isDialogue.value ? [{ key: 'role', label: '翻译指定角色…' }] : []),
])

function confirmTranslate(title: string, content: string, request: () => Promise<{ job_id: string | null; message?: string }>, emptyMessage: string) {
  dialog.warning({
    title,
    content,
    positiveText: '开始翻译',
    negativeText: '取消',
    onPositiveClick: () => runJob(request, onTranslateJobDone, { emptyMessage }),
  })
}

function onTranslateMenu(key: string) {
  if (key === 'all') {
    confirmTranslate(
      '全部翻译',
      '将翻译所有未翻译的文本。任务较大，且取消需等待当前批次结束——点错请尽快在进度条里停止。',
      () => api.post(`/api/current/texts/${props.contentType}/translate-all`),
      '没有待翻译的内容')
  } else if (key === 'filtered') {
    confirmTranslate(
      '翻译当前筛选',
      `将翻译当前筛选结果（共 ${total.value} 条）中未翻译的部分。确定开始？`,
      () => api.post(`/api/current/texts/${props.contentType}/translate-all`, {
        filter_mode: query.filter_mode, search: query.search, character: query.character,
      }),
      '筛选范围内没有待翻译的内容')
  } else if (key === 'role') {
    rolePick.value = ''
    roleModalVisible.value = true
  }
}

async function startRoleTranslate() {
  if (!rolePick.value) {
    message.warning('请选择角色')
    return
  }
  roleModalVisible.value = false
  await runJob(
    () => api.post(`/api/current/texts/${props.contentType}/translate-all`, {
      character: rolePick.value,
    }),
    onTranslateJobDone,
    { emptyMessage: '该角色没有待翻译的台词' })
}

// ---- 上下文对话框 ----
const contextVisible = ref(false)
const contextRows = ref<{ before: Row[]; after: Row[] }>({ before: [], after: [] })
const contextTarget = ref<Row | null>(null)

async function showContext(row: Row) {
  try {
    contextRows.value = await api.get(
      `/api/current/texts/${props.contentType}/${row.id}/context?n=5`)
    contextTarget.value = row
    contextVisible.value = true
  } catch (e) {
    toastError(message, e)
  }
}

// ---- 重建上下文（UI 字符串） ----
async function rebuildHints() {
  await runJob(
    () => api.post(`/api/current/texts/ui/hints/rebuild`),
    () => load())
}

// ---- 提取内嵌文本（UI 字符串） ----
async function extractEmbedded() {
  await runJob(
    () => api.post('/api/current/embedded/scan'),
    () => load())
}

// ---- 风格指南（对话） ----
const guideVisible = ref(false)
const guideText = ref('')
const guideGenerating = ref(false)

async function openStyleGuide() {
  const data = await api.get<{ style_guide: string }>('/api/current/style-guide')
  guideText.value = data.style_guide
  guideVisible.value = true
}

async function generateGuide() {
  guideGenerating.value = true
  try {
    const data = await api.post<{ style_guide: string }>(
      '/api/current/style-guide/generate')
    guideText.value = data.style_guide
    toastOk(message, '风格指南已生成，可编辑后保存')
  } catch (e) {
    toastError(message, e)
  } finally {
    guideGenerating.value = false
  }
}

async function saveGuide() {
  await api.put('/api/current/style-guide', { style_guide: guideText.value })
  toastOk(message, '风格指南已保存，后续翻译将按此风格执行')
  guideVisible.value = false
}

// ---- 表格列 ----
const columns = computed<DataTableColumns<Row>>(() => {
  const cols: DataTableColumns<Row> = [
    {
      key: '_sel', width: 42, align: 'center',
      // 表头勾选 = 本页全选；行勾选跨页保持（selectedIds 不随翻页清空）
      title: () => h(NCheckbox, {
        checked: pageChecked.value,
        indeterminate: pageIndeterminate.value,
        'onUpdate:checked': (v: boolean) => togglePage(v),
      }),
      render: (r) => h(NCheckbox, {
        checked: selectedSet.value.has(r.id),
        'onUpdate:checked': (v: boolean) => toggleRow(r.id, v),
      }),
    },
    { title: '#', key: 'id', width: 60 },
  ]
  if (isDialogue.value) {
    cols.push({ title: '角色', key: 'character', width: 90,
                sorter: true, sortOrder: sortOrderOf('character'),
                render: (r) => r.character || '旁白' })
  }
  cols.push({
    title: '原文', key: 'original_text', ellipsis: { tooltip: true },
    sorter: true, sortOrder: sortOrderOf('original_text'),
    render: (r) => h('span', { style: 'white-space: pre-wrap' }, r.original_text),
  })
  cols.push({
    title: '译文（点击编辑）', key: 'translated_text',
    render: (r) => {
      return h('span', {
        style: `cursor: text; display: block; min-height: 20px; white-space: pre-wrap; ${r.translated_text ? '' : 'color: #666'}`,
        onClick: () => startEdit(r),
      }, r.translated_text || '（点击输入译文）')
    },
  })
  cols.push({
    title: '操作', key: 'actions', width: 185,
    render: (r) => h(NSpace, { size: 4 }, () => [
      h(NButton, {
        size: 'tiny', type: 'primary', quaternary: true,
        loading: translatingIds.value.has(r.id),
        onClick: () => translateOne(r),
      }, () => 'AI翻译'),
      h(NButton, {
        size: 'tiny', quaternary: true, onClick: () => showContext(r),
      }, () => '上下文'),
      ...(r.translated_text ? [h(NPopconfirm, {
        onPositiveClick: () => clearText(r),
      }, {
        trigger: () => h(NButton, {
          size: 'tiny', type: 'error', quaternary: true,
        }, () => '清空'),
        default: () => '确定清空该条译文？清空后可重新翻译。',
      })] : []),
    ]),
  })
  return cols
})

onMounted(() => {
  load()
  loadCharacters()
  void loadTranslateOptions()
})

watch(() => [query.filter_mode, query.character], () => {
  query.page = 0
  selectedIds.value = []  // 筛选范围变了，旧选择作废
  load()
})
</script>

<template>
  <div>
    <n-space align="center" style="width: 100%; margin-bottom: 12px" wrap>
      <page-header :title="title" :help-section="isDialogue ? '5,7' : '4,7'" />
      <n-dropdown :options="translateMenuOptions" @select="onTranslateMenu">
        <n-button size="small" type="primary" :render-icon="renderIcon(LanguageOutline)">全部翻译 ▾</n-button>
      </n-dropdown>
      <n-button size="small" @click="translatePage">翻译本页</n-button>
      <n-button size="small" @click="replacePreview = null; replaceVisible = true">查找并替换译文</n-button>
      <n-button size="small" :render-icon="renderIcon(ShareOutline)" @click="openExport">导出</n-button>
      <n-button size="small" :render-icon="renderIcon(DownloadOutline)" @click="openImport">导入</n-button>
      <n-button size="small" @click="crossImportVisible = true">从其他项目导入</n-button>
      <n-button
        v-if="failedBatchCount > 0" size="small" type="warning"
        :render-icon="renderIcon(WarningOutline)" @click="failedDialogVisible = true"
      >失败条目 ({{ failedBatchCount }})</n-button>
      <n-button v-if="!isDialogue" size="small" :render-icon="renderIcon(LocateOutline)" @click="rebuildHints">重建上下文</n-button>
      <n-button v-if="!isDialogue" size="small" type="warning" :render-icon="renderIcon(CodeSlashOutline)" @click="extractEmbedded">提取内嵌文本</n-button>
      <n-button v-if="isDialogue" size="small" :render-icon="renderIcon(BookOutline)" @click="openStyleGuide">风格指南</n-button>
      <n-button size="small" quaternary :loading="loading" :render-icon="renderIcon(RefreshOutline)" @click="load">刷新</n-button>
    </n-space>

    <n-text v-if="isDialogue" depth="3" style="display: block; margin-bottom: 10px; font-size: 12px">
      人名和角色分析可逐步补充，不必全员完成即可翻译；建议先设定主要角色的译名，减少人名不一致。
    </n-text>

    <n-space align="center" style="margin-bottom: 10px" wrap>
      <n-text depth="3" style="font-size: 12px">翻译模式</n-text>
      <n-select
        :value="translatePreset" size="small" style="width: 110px"
        :options="presetOptions" :disabled="savingOptions"
        @update:value="applyPreset"
      />
      <n-button size="small" quaternary @click="tuneOpen = !tuneOpen">
        {{ tuneOpen ? '收起微调' : '微调' }}
      </n-button>
      <n-text depth="3" style="font-size: 12px">{{ presetHints[translatePreset] }}</n-text>
    </n-space>

    <n-card v-if="tuneOpen" size="small" style="margin-bottom: 10px">
      <n-space align="center" wrap>
        <n-space align="center">
          <n-text style="font-size: 12px">术语提取</n-text>
          <n-switch :value="translateFlags.term_extract" size="small"
                    :disabled="savingOptions"
                    @update:value="(v: boolean) => setFlag('term_extract', v)" />
        </n-space>
        <n-space align="center">
          <n-text style="font-size: 12px">术语表注入</n-text>
          <n-switch :value="translateFlags.glossary" size="small"
                    :disabled="savingOptions"
                    @update:value="(v: boolean) => setFlag('glossary', v)" />
        </n-space>
        <n-space align="center">
          <n-text style="font-size: 12px">风格指南</n-text>
          <n-switch :value="translateFlags.style_guide" size="small"
                    :disabled="savingOptions"
                    @update:value="(v: boolean) => setFlag('style_guide', v)" />
        </n-space>
        <n-space align="center">
          <n-text style="font-size: 12px">角色特征</n-text>
          <n-select
            :value="translateFlags.character_profile" size="small" style="width: 120px"
            :options="profileOptions" :disabled="savingOptions"
            @update:value="(v: string) => setFlag('character_profile', v)"
          />
        </n-space>
        <n-space align="center">
          <n-text style="font-size: 12px">上下文</n-text>
          <n-switch :value="translateFlags.context" size="small"
                    :disabled="savingOptions"
                    @update:value="(v: boolean) => setFlag('context', v)" />
        </n-space>
      </n-space>
    </n-card>

    <n-space align="center" style="margin-bottom: 10px" wrap>
      <n-select
        v-model:value="query.filter_mode" size="small" style="width: 130px"
        :options="[
          { label: '全部', value: 'all' },
          { label: '未翻译', value: 'untranslated' },
          { label: '已翻译', value: 'translated' },
        ]"
      />
      <n-select
        v-if="isDialogue"
        v-model:value="query.character" size="small" style="width: 200px"
        :options="characters" placeholder="角色筛选"
      />
      <n-input-group style="width: 280px">
        <n-input
          v-model:value="query.search" size="small" placeholder="搜索原文/译文"
          @keydown="searchKeydown"
        />
        <n-button size="small" @click="query.page = 0; resetSelection(); load()">搜索</n-button>
      </n-input-group>
      <n-button size="small" quaternary @click="selectAllFiltered">全选筛选结果</n-button>
      <n-text depth="3" style="font-size: 12px">共 {{ total }} 条</n-text>
    </n-space>

    <n-space v-if="selectedIds.length" align="center" style="margin-bottom: 10px">
      <n-text strong style="color: #63e2b7">已选 {{ selectedIds.length }} 条</n-text>
      <n-popconfirm @positive-click="clearSelected">
        <template #trigger>
          <n-button size="small" type="warning">清空所选译文</n-button>
        </template>
        确认清空所选 {{ selectedIds.length }} 条译文？清空后回到「未翻译」，可重新翻译。
      </n-popconfirm>
      <n-button size="small" quaternary @click="resetSelection">取消选择</n-button>
    </n-space>

    <n-data-table
      :columns="columns" :data="rows" :loading="loading"
      :row-key="(r: Row) => r.id" size="small" :scroll-x="900"
      :render-empty="() => h(NEmpty, { description: '暂无文本' })"
      @update:sorter="onSorterChange"
    />

    <n-space justify="end" style="margin-top: 10px">
      <n-pagination
        :page="query.page + 1"
        :item-count="total"
        :page-size="query.size"
        @update:page="(p: number) => { query.page = p - 1; load() }"
      />
    </n-space>

    <n-modal v-model:show="roleModalVisible" preset="card" title="翻译指定角色"
             style="width: min(420px, 96vw)">
      <n-space vertical>
        <n-text depth="3" style="font-size: 12px">
          只翻译该角色的未翻译台词，其他角色与页面筛选都不受影响。
        </n-text>
        <n-select v-model:value="rolePick" :options="roleOptions" filterable
                  placeholder="选择角色" />
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button @click="roleModalVisible = false">取消</n-button>
          <n-button type="primary" :disabled="!rolePick" @click="startRoleTranslate">
            开始翻译
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal :show="!!editRow" preset="card" title="编辑译文"
             style="width: min(960px, 96vw)" :mask-closable="false" :close-on-esc="false"
             :closable="false">
      <n-space vertical>
        <n-text depth="3">原文 · #{{ editRow?.id }}</n-text>
        <div style="white-space: pre-wrap; overflow-wrap: anywhere; max-height: 25vh; overflow: auto">{{ editRow?.original_text }}</div>
        <n-input v-model:value="editingText" type="textarea" autofocus :disabled="saving"
                 :autosize="{ minRows: 8, maxRows: 18 }" @keydown="editKeydown" />
        <n-text depth="3">Enter 换行，Ctrl / ⌘ + Enter 保存。保存失败时保留草稿。</n-text>
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button :disabled="saving" @click="editRow = null">取消</n-button>
          <n-button type="primary" :loading="saving" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="replaceVisible" preset="card" title="查找并替换译文"
             style="width: min(960px, 96vw)" :mask-closable="false" :closable="!replacing"
             :close-on-esc="!replacing">
      <n-space vertical>
        <n-text>范围：当前{{ title }}的筛选结果（所有页）。只替换译文，按字面匹配、区分大小写。</n-text>
        <n-input v-model:value="replaceForm.find" :disabled="replacing" placeholder="查找，例如：阿米" />
        <n-input v-model:value="replaceForm.replacement" :disabled="replacing" placeholder="替换为，例如：亚美（留空即删除匹配文字）" />
        <n-text v-if="replacePreview">匹配 {{ replacePreview.count }} 条译文，共 {{ replacePreview.occurrences }} 处。下方预览前 30 条，请检查人名、变量和标签是否会被误改。</n-text>
        <div v-if="replacePreview" class="modal-scroll">
          <div v-for="sample in replacePreview.samples" :key="sample.id"
               style="padding: 10px 0; border-bottom: 1px solid #555; white-space: pre-wrap; overflow-wrap: anywhere">
            <div>#{{ sample.id }} · {{ sample.original_text }}</div>
            <div>替换前：{{ sample.before }}</div>
            <div>替换后：{{ sample.after }}</div>
          </div>
        </div>
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button :disabled="replacing" @click="replaceVisible = false">关闭</n-button>
          <n-button :loading="replacing" :disabled="!replaceForm.find" @click="replaceTexts()">预览</n-button>
          <n-button type="primary" :loading="replacing" :disabled="!replacePreview?.count"
                    @click="replaceTexts(true)">确认替换 {{ replacePreview?.count || 0 }} 条</n-button>
        </n-space>
      </template>
    </n-modal>

    <!-- 上下文对话框 -->
    <n-modal v-model:show="contextVisible" preset="card" title="上下文" style="width: min(640px, 96vw)">
      <div v-if="contextTarget" style="font-size: 13px">
        <div v-for="r in contextRows.before" :key="'b' + r.id" style="color: #888; padding: 2px 0">
          {{ r.character || '旁白' }}: {{ r.original_text }}
          <span v-if="r.translated_text" style="color: #6a6">→ {{ r.translated_text }}</span>
        </div>
        <div style="background: #4a4420; padding: 4px 6px; border-radius: 4px; margin: 4px 0">
          <b>{{ contextTarget.character || '旁白' }}: {{ contextTarget.original_text }}</b>
        </div>
        <div v-for="r in contextRows.after" :key="'a' + r.id" style="color: #888; padding: 2px 0">
          {{ r.character || '旁白' }}: {{ r.original_text }}
          <span v-if="r.translated_text" style="color: #6a6">→ {{ r.translated_text }}</span>
        </div>
      </div>
    </n-modal>

    <!-- 导出翻译文本对话框 -->
    <export-texts-dialog
      v-model:show="exportVisible" :preset-types="[contentType]"
      :filter-scope="exportFilter"
    />

    <!-- 导入翻译文本对话框 -->
    <import-texts-dialog
      v-model:show="importVisible" :preset-types="[contentType]"
      :filter-scope="exportFilter" @imported="onImported"
    />

    <import-from-project-dialog
      v-model:show="crossImportVisible" :preset-types="[contentType]"
      @imported="onImported"
    />

    <!-- 失败批次核验对话框 -->
    <failed-batches-dialog
      v-model:show="failedDialogVisible" :content-type="contentType"
      @changed="load"
    />

    <!-- 风格指南对话框 -->
    <n-modal v-model:show="guideVisible" preset="card" title="翻译风格指南" style="width: min(720px, 96vw)">
      <n-input
        v-model:value="guideText" type="textarea" :autosize="{ minRows: 12, maxRows: 24 }"
        placeholder="描述译文的整体风格要求，AI 翻译时会遵循..."
      />
      <template #footer>
        <n-space justify="end">
          <n-button :loading="guideGenerating" :render-icon="renderIcon(SparklesOutline)" @click="generateGuide">AI 生成</n-button>
          <n-button @click="guideVisible = false">取消</n-button>
          <n-button type="primary" @click="saveGuide">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </div>
</template>
