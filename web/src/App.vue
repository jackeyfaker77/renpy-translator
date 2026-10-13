<script setup lang="ts">
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import AppInner from './AppInner.vue'
</script>

<template>
  <n-config-provider :theme="darkTheme" :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <app-inner />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
/* 全局滚动条：统一暗色细圆角，与 naive-ui n-scrollbar 观感一致。
   覆盖所有原生 overflow 区域（对话框、日志、表格、textarea 等），
   不再出现 Windows 系统亮色滚动条。
   只用 ::-webkit-scrollbar（应用跑在 WebView2 / Chrome / Edge）；
   不写 scrollbar-width——Chromium 两者并存时会禁用伪元素定制。 */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.16);
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.28);
}
::-webkit-scrollbar-corner {
  background: transparent;
}
</style>
