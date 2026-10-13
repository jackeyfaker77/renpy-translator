/**
 * 帮助内容：与 docs/使用说明.md 同源（构建时 ?raw 打入前端包）。
 * 说明书是唯一内容源——改说明书即同步全部页面帮助，不会漂移。
 *
 * 章节按「## N. 标题」切分；pageHelp 用章节号取内容。
 * 渲染用轻量 markdown→HTML（标题/列表/粗体/行内代码/表格/围栏代码）；
 * 截图引用（docs/screenshots/…）在应用内不显示，整行丢弃。
 */
import manual from '../../docs/使用说明.md?raw'

interface Section {
  num: string   // 章节号（'1'..'12'）或特殊键（'overview'）
  title: string
  body: string  // markdown 正文（不含标题行）
}

function splitSections(md: string): Section[] {
  const out: Section[] = []
  // 统一换行：仓库文件可能是 CRLF，行尾 \r 会让 ^…$ 正则失配
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let cur: Section | null = null
  const buf: string[] = []
  const flush = () => {
    if (cur) out.push({ ...cur, body: buf.join('\n').trim() })
  }
  for (const line of lines) {
    const m = /^## (.+)$/.exec(line)
    if (m) {
      flush()
      buf.length = 0
      const title = m[1].trim()
      const numMatch = /^(\d+)\./.exec(title)
      cur = { num: numMatch ? numMatch[1] : title, title, body: '' }
    } else if (cur) {
      buf.push(line)
    }
  }
  flush()
  return out
}

const SECTIONS = splitSections(manual)

/** 按章节号取帮助内容；无内容返回空串 */
export function pageHelp(num: string): { title: string; html: string } | null {
  const sec = SECTIONS.find((s) => s.num === num)
  if (!sec) return null
  return { title: sec.title, html: renderMd(sec.body) }
}

// ---- 轻量 markdown 渲染（仅说明书用到的语法）----

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function inline(s: string): string {
  let t = esc(s)
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  return t
}

function renderMd(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let inCode = false
  let codeBuf: string[] = []
  let listStack: ('ul' | 'ol')[] = []
  let tableBuf: string[] = []

  const closeLists = () => {
    while (listStack.length) html.push(`</${listStack.pop()}>`)
  }
  const flushTable = () => {
    if (!tableBuf.length) return
    const rows = tableBuf
      .filter((l) => !/^\|[\s:-]+\|$/.test(l.trim()))  // 分隔行
      .map((l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()))
    if (rows.length) {
      html.push('<table>')
      rows.forEach((cells, i) => {
        html.push('<tr>' + cells.map((c) =>
          i === 0 ? `<th>${inline(c)}</th>` : `<td>${inline(c)}</td>`
        ).join('') + '</tr>')
      })
      html.push('</table>')
    }
    tableBuf = []
  }

  for (const line of lines) {
    if (inCode) {
      if (line.startsWith('```')) {
        html.push(`<pre><code>${esc(codeBuf.join('\n'))}</code></pre>`)
        codeBuf = []
        inCode = false
      } else {
        codeBuf.push(line)
      }
      continue
    }
    if (line.startsWith('```')) {
      closeLists(); flushTable()
      inCode = true
      continue
    }
    // 截图行整体丢弃（应用内不显示截图）
    if (/^!\[/.test(line.trim())) continue
    if (line.trim() === '---') {
      closeLists(); flushTable()
      html.push('<hr/>')
      continue
    }
    if (line.trim().startsWith('|')) {
      closeLists()
      tableBuf.push(line)
      continue
    }
    flushTable()

    const li = /^(\s*)([-*]|\d+\.)\s+(.*)$/.exec(line)
    if (li) {
      const depth = Math.floor(li[1].length / 2)
      const kind = li[2].match(/[-*]/) ? 'ul' : 'ol'
      while (listStack.length > depth + 1) {
        html.push(`</${listStack.pop()}>`)
      }
      if (listStack.length === depth) {
        // 深度推进时补开中间层（说明书最多两层）
        while (listStack.length < depth) {
          html.push('<ul>')
          listStack.push('ul')
        }
        html.push(`<${kind}>`)
        listStack.push(kind)
      }
      html.push(`<li>${inline(li[3])}</li>`)
      continue
    }
    closeLists()

    const h = /^(#{3,4})\s+(.*)$/.exec(line)
    if (h) {
      html.push(`<h4>${inline(h[2])}</h4>`)
      continue
    }
    if (line.trim().startsWith('> ')) {
      html.push(`<blockquote>${inline(line.trim().slice(2))}</blockquote>`)
      continue
    }
    if (line.trim()) {
      html.push(`<p>${inline(line)}</p>`)
    }
  }
  if (inCode) html.push(`<pre><code>${esc(codeBuf.join('\n'))}</code></pre>`)
  closeLists(); flushTable()
  return html.join('\n')
}
