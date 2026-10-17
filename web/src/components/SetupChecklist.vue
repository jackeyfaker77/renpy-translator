<script setup lang="ts">
/** 新手检查单：建议而非门禁——每步可独立完成，关闭后可从帮助找回
 *  （首启空态与项目概览共用；完成态来自 session/stats，由父级计算传入） */
import { NButton, NCard, NText } from 'naive-ui'
import type { ChecklistItem } from '../composables/onboarding'

const props = defineProps<{
  items: ChecklistItem[]
  /** 当前进行中的步骤 key（高亮 + 显示 CTA） */
  currentKey?: string
  /** 当前步骤的按钮文案 */
  currentCta?: string
}>()

const emit = defineEmits<{
  action: [key: string]
  dismiss: []
}>()

const doneCount = () => props.items.filter((i) => i.done).length
</script>

<template>
  <n-card size="small" title="新手引导">
    <template #header-extra>
      <n-button size="tiny" quaternary @click="emit('dismiss')">不再显示</n-button>
    </template>
    <div style="display: flex; flex-direction: column; gap: 10px">
      <div v-for="(item, idx) in items" :key="item.key"
           style="display: flex; align-items: center; gap: 12px">
        <span :style="{
          width: '22px', height: '22px', borderRadius: '50%', flex: 'none',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '12px', fontWeight: 600,
          background: item.done ? 'var(--n-color-target, var(--rt-primary))' : 'rgba(255,255,255,0.08)',
          color: item.done ? 'var(--rt-surface0)' : 'rgba(255,255,255,0.68)',
        }">
          {{ item.done ? '✓' : idx + 1 }}
        </span>
        <div style="flex: 1; min-width: 0">
          <n-text :delete="item.done" :depth="item.done ? 3 : 1">{{ item.title }}</n-text>
          <n-text v-if="!item.done && item.desc" depth="3"
                  style="display: block; font-size: 12px">{{ item.desc }}</n-text>
        </div>
        <n-button v-if="item.key === currentKey && currentCta" size="small" type="primary"
                  @click="emit('action', item.key)">
          {{ currentCta }}
        </n-button>
      </div>
      <n-text v-if="doneCount() === items.length" depth="3" style="font-size: 12px">
        已完成 {{ doneCount() }}/{{ items.length }}，可以自由探索任何功能。
      </n-text>
    </div>
  </n-card>
</template>
