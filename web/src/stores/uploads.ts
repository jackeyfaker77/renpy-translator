/** 上传任务 store：大文件 zip 上传的前端传输阶段（服务端任务在上传
 *  完成后才建立）。与后台任务同款交互：进度在对话框里可见（表单对话框
 *  内联 / 顶栏任务列表点开的全局对话框），对话框一关任务转入顶栏
 *  「后台任务」列表继续跑，可随时中断（XHR abort）。 */
import { defineStore } from 'pinia'
import { api, isUploadCancelled } from '../api/client'

export type UploadKind = 'create' | 'import' | 'update'

export interface UploadView {
  id: string
  kind: UploadKind
  /** 展示名：上传 xxx.zip（新建项目） */
  label: string
  /** 0..1 */
  pct: number
  status: 'uploading' | 'failed' | 'cancelled'
  error: string
  /** 进度是否在某个对话框里可见（false = 转入顶栏任务列表） */
  dialogOpen: boolean
}

/** abort 句柄（函数）不进响应式 state，放模块级表 */
const aborts = new Map<string, () => void>()

export const useUploadsStore = defineStore('uploads', {
  state: () => ({
    tasks: new Map<string, UploadView>(),
    seq: 0,
    /** 全局上传进度对话框展示的任务 id（顶栏任务列表点开） */
    dialogId: '' as string,
  }),
  getters: {
    /** 顶栏后台任务列表：进行中且没在对话框里展示的上传 */
    backgroundTasks(state): UploadView[] {
      return [...state.tasks.values()]
        .filter((t) => t.status === 'uploading' && !t.dialogOpen)
    },
    dialogTask(state): UploadView | null {
      return state.tasks.get(state.dialogId) ?? null
    },
  },
  actions: {
    /** 某类表单对话框对应的在传任务（内联进度用；终态不返回） */
    ofKind(kind: UploadKind): UploadView | null {
      for (const t of this.tasks.values()) {
        if (t.kind === kind && t.status === 'uploading') return t
      }
      return null
    },

    /** 启动上传：返回响应体；用户中断以 UPLOAD_CANCELLED 拒绝（非错误）。
     *  成功即退场（服务端任务对话框 jobs 接管）；失败/中断在对话框里
     *  留场 8 秒供查看，无人看则即时清。 */
    async upload<T>(kind: UploadKind, label: string,
                    url: string, form: FormData): Promise<T> {
      const id = `up-${++this.seq}`
      // set 后取回：拿到响应式代理，pct 更新才触发视图
      this.tasks.set(id, {
        id, kind, label, pct: 0, status: 'uploading', error: '',
        dialogOpen: true,
      })
      const view = this.tasks.get(id)!
      const handle = api.postFormProgress<T>(url, form, (pct) => {
        view.pct = pct
      })
      aborts.set(id, handle.abort)
      try {
        const data = await handle.promise
        this.tasks.delete(id)
        return data
      } catch (e) {
        view.status = isUploadCancelled(e) ? 'cancelled' : 'failed'
        view.error = view.status === 'cancelled'
          ? '已取消上传'
          : (e instanceof Error ? e.message : String(e))
        if (!view.dialogOpen) this.tasks.delete(id)
        else setTimeout(() => {
          if (this.tasks.get(id) !== view || view.status === 'uploading') return
          this.tasks.delete(id)
        }, 8000)
        throw e
      } finally {
        aborts.delete(id)
      }
    },

    /** 中断上传（XHR abort；服务端表单解析随断开作废，不留半截任务） */
    cancel(id: string) {
      aborts.get(id)?.()
    },

    /** 表单对话框（新建/更新/导入）开合：开 = 内联进度可见，关 = 转顶栏 */
    setDialogOpen(kind: UploadKind, open: boolean) {
      const t = this.ofKind(kind)
      if (t) t.dialogOpen = open
    },

    /** 顶栏任务列表点开全局进度对话框 */
    openDialog(id: string) {
      this.dialogId = id
      const t = this.tasks.get(id)
      if (t) t.dialogOpen = true
    },

    closeDialog() {
      const id = this.dialogId
      this.dialogId = ''
      const t = this.tasks.get(id)
      if (!t) return
      t.dialogOpen = false
      // 终态任务随对话框关闭清场（进行中的转顶栏继续跑）
      if (t.status !== 'uploading') this.tasks.delete(id)
    },
  },
})
