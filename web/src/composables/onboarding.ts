/** 首启动线与下一步算法（纯函数，可单测；数据全部来自已有 session/stats）
 *
 * 下一步顺序由业务动线决定（人名杠杆最大），**不由百分比决定**——
 * 人名永远是最低百分比，但不先定人名，对话翻译的不一致成本更高。
 */

export interface CountLike {
  total: number
  translated: number
}

export interface SessionLike {
  hasTranslator: boolean
  currentProject: string
  stats: {
    dialogue?: CountLike
    ui?: CountLike
    names?: CountLike
  }
}

export type NextStep = 'model' | 'project' | 'names' | 'ui' | 'dialogue'
  | 'markup' | 'export' | 'done'

export interface StepMeta {
  key: NextStep
  title: string
  desc: string
  cta: string
  route?: string
}

export const NEXT_STEP_META: Record<NextStep, StepMeta> = {
  model: {
    key: 'model', title: '配置 AI 模型',
    desc: '翻译质量与成本都由它决定；DeepSeek / GPT / Claude 等，填 API Key 即可。',
    cta: '配置模型', route: '/settings?new=1',
  },
  project: {
    key: 'project', title: '新建项目',
    desc: '选择游戏文件夹或上传 zip，自动解包、反编译、入库。',
    cta: '新建项目', route: '/projects?dialog=create',
  },
  names: {
    key: 'names', title: '人名与角色',
    desc: '人名会注入之后所有对话翻译，先定主要角色译名可显著减少不一致。',
    cta: '去翻译人名', route: '/names',
  },
  ui: {
    key: 'ui', title: 'UI 字符串',
    desc: '菜单、按钮等界面文字，量小见效快。',
    cta: '去翻译字符串', route: '/strings',
  },
  dialogue: {
    key: 'dialogue', title: '对话翻译',
    desc: '建议先试译 10 条确认效果，再批量推进，避免一次性消耗大量 token。',
    cta: '去翻译对话', route: '/dialogue',
  },
  markup: {
    key: 'markup', title: '修订标记违规译文',
    desc: '有译文破坏了插值/标签，导出会保留这些条目的原文——先修掉再导出。',
    cta: '去导出页处理', route: '/export',
  },
  export: {
    key: 'export', title: '导出游戏',
    desc: '全部就绪。导出前会自动校验标记与未翻译条目。',
    cta: '去导出', route: '/export',
  },
  done: {
    key: 'done', title: '本项目已完成',
    desc: '全部译文就绪、可导出成品；出新版游戏时可用「更新版本」增量升级。',
    cta: '打开导出目录',
  },
}

/** 计算下一步：model → project → names → ui → dialogue → markup → export → done */
export function nextStep(s: SessionLike, markupCount = 0): NextStep {
  if (!s.hasTranslator) return 'model'
  if (!s.currentProject) return 'project'
  const n = s.stats.names
  if (n?.total && n.translated < n.total) return 'names'
  const u = s.stats.ui
  if (u?.total && u.translated < u.total) return 'ui'
  const d = s.stats.dialogue
  if (d?.total && d.translated < d.total) return 'dialogue'
  if (markupCount > 0) return 'markup'
  return 'export'
}

export interface ChecklistItem {
  key: string
  title: string
  desc: string
  done: boolean
}

/** 项目内新手检查单（建议而非门禁；步骤 4 故意是「试译 10 条」而非翻完） */
export function projectChecklist(s: SessionLike, hasPackages: boolean): ChecklistItem[] {
  const d = s.stats.dialogue
  const n = s.stats.names
  return [
    { key: 'model', title: '配置 AI 模型', desc: '约 2 分钟', done: s.hasTranslator },
    { key: 'project', title: '打开/新建项目', desc: '自动解包、反编译、入库', done: !!s.currentProject },
    { key: 'names', title: '人名与角色', desc: '先翻译主要角色即可', done: !n?.total || n.translated >= n.total },
    { key: 'trial', title: '试译 10 条对话', desc: '先看效果再批量，避免一次性烧 token', done: (d?.translated ?? 0) > 0 },
    { key: 'export', title: '导出游戏', desc: '导出前自动校验标记与未翻译', done: hasPackages },
  ]
}

// ---- 关闭状态（localStorage；单机工具，不跨设备同步） ----

const dismissedKey = (project: string) => `rt.onboarding.${project}.dismissed`

export function isChecklistDismissed(project: string): boolean {
  try { return localStorage.getItem(dismissedKey(project)) === '1' } catch { return false }
}

export function setChecklistDismissed(project: string, value: boolean): void {
  try {
    if (value) localStorage.setItem(dismissedKey(project), '1')
    else localStorage.removeItem(dismissedKey(project))
  } catch { /* 隐私模式等：忽略 */ }
}
