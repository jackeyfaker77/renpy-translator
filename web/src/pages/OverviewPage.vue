<script setup lang="ts">
/** 项目概览：现在到哪了、下一步做什么、最近发生了什么
 *  （下一步卡/检查单/最近任务由后续步骤补齐，本版先落统计数据与空态） */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NEmpty, NProgress, NSpace, NText } from 'naive-ui'
import PageHeader from '../components/PageHeader.vue'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()
const router = useRouter()

const progressRows = computed(() => {
  const s = session.stats
  return [
    { label: '人名与角色', st: s.names },
    { label: 'UI 字符串', st: s.ui },
    { label: '对话', st: s.dialogue },
  ].filter((r) => !!r.st?.total)
})
</script>

<template>
  <div>
    <page-header :title="session.currentProject || '项目概览'" />

    <n-empty v-if="!session.currentProject" description="未打开项目">
      <template #extra>
        <n-button type="primary" @click="router.push('/projects')">前往项目库</n-button>
      </template>
    </n-empty>

    <n-space v-else vertical size="medium">
      <n-card size="small" title="进度">
        <n-empty v-if="!progressRows.length" description="还没有可翻译的内容" size="small" />
        <n-space v-else vertical style="width: 100%">
          <div v-for="r in progressRows" :key="r.label"
               style="display: flex; align-items: center; gap: 12px">
            <n-text style="width: 90px">{{ r.label }}</n-text>
            <n-progress type="line" :percentage="Math.round((r.st!.translated / r.st!.total) * 100)"
                        style="flex: 1" />
            <n-text depth="3" style="font-size: 12px; width: 110px; text-align: right">
              {{ r.st!.translated }}/{{ r.st!.total }}
            </n-text>
          </div>
        </n-space>
      </n-card>
      <!-- TODO 步骤 3：下一步卡 / 新手检查单 / 最近任务 / 项目操作 -->
    </n-space>
  </div>
</template>
