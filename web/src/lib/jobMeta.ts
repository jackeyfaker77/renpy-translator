/** 任务 kind 的展示与导航元数据（任务中心 / 错误块 / 状态栏共用）
 *
 * 精确表优先，其次前缀规则；未知 kind 原样显示、不给「打开页面」
 * （避免瞎猜导航）。可重新发起的 kind 单列白名单——重发=按原语义
 * 重新 POST，不是通用能力。 */

export interface KindMeta {
  label?: string
  route?: string
}

const EXACT: Record<string, KindMeta> = {
  'project.create': { label: '创建项目', route: '/projects' },
  'project.import': { label: '导入项目', route: '/projects' },
  'project.update': { label: '更新版本', route: '/projects' },
  'project.delete': { label: '删除项目', route: '/projects' },
  'project.export-zip': { label: '导出项目包', route: '/projects' },
  'texts.translate-all.dialogue': { label: '对话翻译', route: '/dialogue' },
  'texts.translate-all.ui': { label: 'UI 字符串翻译', route: '/strings' },
  'texts.translate-page.dialogue': { label: '翻译本页（对话）', route: '/dialogue' },
  'texts.translate-page.ui': { label: '翻译本页（字符串）', route: '/strings' },
  'embedded.scan': { label: '提取内嵌文本', route: '/strings' },
  'ui.hints-rebuild': { label: '重建字符串上下文', route: '/strings' },
  'export.game': { label: '导出游戏', route: '/export' },
  'settings.sdk-download': { label: 'SDK 下载', route: '/settings' },
}

/** 前缀规则（mode 藏在 kind 尾部的、实验图谱类） */
function byPrefix(kind: string): KindMeta | undefined {
  if (kind.startsWith('names.translate-all.')) {
    return { label: '人名翻译与分析', route: '/names' }
  }
  if (kind.startsWith('texts.failed-retry.')) {
    const ct = kind.endsWith('.ui') ? 'ui' : 'dialogue'
    return { label: '失败条目重试', route: ct === 'ui' ? '/strings' : '/dialogue' }
  }
  if (kind.startsWith('graph.story')) return { label: '剧情分支图', route: '/story-graph' }
  if (kind.startsWith('graph.relation')) return { label: '人物关系图谱', route: '/relations' }
  return undefined
}

export function jobKindMeta(kind: string): KindMeta {
  return EXACT[kind] ?? byPrefix(kind) ?? {}
}

/** 可重新发起的白名单：返回发起函数，不在白名单返回 null */
export type ReissueFn = () => Promise<unknown>

export function jobReissue(
  kind: string,
  post: <T>(url: string, data?: unknown) => Promise<T>,
): ReissueFn | null {
  if (kind === 'texts.translate-all.dialogue') {
    return () => post('/api/current/texts/dialogue/translate-all')
  }
  if (kind === 'texts.translate-all.ui') {
    return () => post('/api/current/texts/ui/translate-all')
  }
  if (kind.startsWith('names.translate-all.')) {
    const mode = kind.split('.').pop() || 'both'
    return () => post('/api/current/names/translate-all', { mode })
  }
  return null
}
