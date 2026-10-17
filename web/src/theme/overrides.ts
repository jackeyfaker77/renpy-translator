/** Naive UI 主题覆盖：把 token 层接到组件库的派生色上。
 *
 * 只对齐现有暗色观感，不换风格；自定义组件用 useThemeVars() 或直接 import t。
 * 注意：不要把 Naive 颜色改成 var(--x) 字符串——会破坏其内部 hover/suppl 派生。
 */
import type { GlobalThemeOverrides } from 'naive-ui'
import { t } from './tokens'

export const overrides: GlobalThemeOverrides = {
  common: {
    primaryColor: t.primary,
    primaryColorHover: t.primaryHover,
    primaryColorPressed: t.primaryPressed,
    primaryColorSuppl: t.primary,
    bodyColor: t.surface0,
    cardColor: t.surface1,
    modalColor: t.surface2,
    popoverColor: t.surface2,
    textColorBase: t.text1,
    textColor1: t.text1,
    textColor2: t.text2,
    textColor3: t.text3,
    borderColor: t.border,
    dividerColor: t.border,
    hoverColor: t.surface3,
    borderRadius: '6px',
    fontSize: '13px',
    heightSmall: '26px',
  },
  DataTable: {
    thColor: t.surface1,
    tdColor: t.surface0,
    thTextColor: t.text3,
    tdTextColor: t.text2,
    borderColor: t.border,
    thColorHover: t.surface3,
    tdColorHover: t.surface3,
  },
}
