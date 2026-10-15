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

/* 模态框长列表统一约定：
   - n-modal 一律 width: min(Xpx, 96vw)，窄窗不横向溢出；
   - 长列表/长内容包进 .modal-scroll——只滚内容区，标题与页脚固定可见；
   - 不要给整张卡设 overflow（标题/按钮会跟着滚走）。
   高度用视口占比，替代各处写死的 px/vh。 */
.modal-scroll {
  max-height: 60vh;
  overflow-y: auto;
}
</style>
