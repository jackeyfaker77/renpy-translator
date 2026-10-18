/** 可定时任务类型：展示名 + 参数表单形态（细粒度与直接执行一致） */

export type ParamKind = 'scope' | 'mode' | 'shell' | 'chunk' | 'none'

export const KIND_META: Record<string, { label: string; paramKind: ParamKind }> = {
  'texts.translate-all.dialogue': { label: '对话批量翻译', paramKind: 'scope' },
  'texts.translate-all.ui': { label: 'UI 字符串翻译', paramKind: 'scope' },
  'names.translate-all.both': { label: '人名翻译+分析', paramKind: 'mode' },
  'names.translate-all.name': { label: '人名翻译', paramKind: 'mode' },
  'names.translate-all.analyze': { label: '角色分析', paramKind: 'mode' },
  'export.game': { label: '导出游戏', paramKind: 'shell' },
  'embedded.scan': { label: '提取内嵌文本', paramKind: 'none' },
  'ui.hints-rebuild': { label: '重建字符串上下文', paramKind: 'none' },
  'graph.story-build': { label: '构建剧情图', paramKind: 'none' },
  'graph.relations-build': { label: '构建关系图', paramKind: 'none' },
  'texts.failed-retry.dialogue': { label: '失败条目重试（对话）', paramKind: 'chunk' },
  'texts.failed-retry.ui': { label: '失败条目重试（字符串）', paramKind: 'chunk' },
  'project.export-zip': { label: '导出项目包', paramKind: 'none' },
}

export type ScheduleType = 'delay' | 'at' | 'window'

export const SCHEDULE_TYPE_OPTIONS = [
  { label: '倒计时', value: 'delay' },
  { label: '时间点', value: 'at' },
  { label: '时间段', value: 'window' },
]

export const REPEAT_OPTIONS = [
  { label: '仅一次', value: 'once' },
  { label: '每天循环', value: 'daily' },
]

/** 各类型的默认参数 */
export function defaultParams(paramKind: ParamKind): Record<string, unknown> {
  switch (paramKind) {
    case 'scope': return { filter_mode: 'all', search: '', character: '' }
    case 'mode': return { mode: 'both' }
    case 'shell': return { shell: true }
    case 'chunk': return { chunk_size: 10 }
    default: return {}
  }
}

export function kindLabel(kind: string): string {
  return KIND_META[kind]?.label || kind
}
