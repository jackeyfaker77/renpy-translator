/** 语义设计 token：全站唯一的色值来源（图表除外，见 components/graph-theme.ts）
 *
 * 命名按语义（surface/text/state），不按色值——新增颜色一律走这里，
 * 页面只允许组合 token，不允许再造灰阶。色值与 v2 调研报告 §8.1 对应。
 */
export const t = {
  surface0: '#101014',          // 页面底
  surface1: '#18181c',          // 卡片/表头
  surface2: '#1f1f24',          // 弹窗/浮层
  surface3: '#26262c',          // hover/选中底
  border: 'rgba(255,255,255,0.12)',   // 分隔线/边框
  text1: 'rgba(255,255,255,0.92)',    // 主文字
  text2: 'rgba(255,255,255,0.68)',    // 次要文字
  text3: 'rgba(255,255,255,0.45)',    // 弱化/占位
  primary: '#63e2b7',           // 主操作
  primaryHover: '#7be8c4',
  primaryPressed: '#4fc9a0',
  info: '#70c0e8',              // 提示/链接
  warning: '#f2c97d',           // 警告
  error: '#e88080',             // 错误/危险
  warningBg: 'rgba(242,201,125,0.12)',  // 警告底（如上下文高亮）
  errorBg: 'rgba(232,128,128,0.12)',    // 错误底
  focus: 'rgba(99,226,183,0.45)',       // 键盘焦点环
  mark: '#ffca28',              // 行内标记高亮（diff/mark 底色）
  markFg: '#000',               // 标记上的文字色
} as const
