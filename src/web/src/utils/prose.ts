/**
 * 正文 Markdown ↔ 编辑器 HTML 双向转换。
 *
 * 背景：章节文件与 `editorStore.draft` 的存储格式统一为 **Markdown**（与服务端一致），
 * 而 Tiptap 的 `content` 只接受 HTML/JSON——旧实现把 Markdown 原文当 HTML 灌入，
 * 所有换行被折叠成一个段落，导致编辑模式与阅读模式（markdown-it 渲染）显示不一致。
 *
 * - 载入：`markdownToHtml`（markdown-it + DOMPurify，与阅读模式同配置）；
 * - 保存：`editorJsonToMarkdown`（Tiptap doc JSON → Markdown，覆盖 StarterKit 节点集）。
 */
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

/** Markdown → 安全 HTML（编辑器初始/换章载入用） */
export function markdownToHtml(text: string): string {
  return DOMPurify.sanitize(md.render(text || ''))
}

// ---------------------------------------------------------------------------
// Tiptap doc JSON → Markdown
// ---------------------------------------------------------------------------

type Node = { type: string; attrs?: Record<string, unknown>; content?: Node[]; text?: string; marks?: { type: string }[] }

function inlineText(nodes: Node[] | undefined): string {
  if (!nodes) return ''
  return nodes
    .map((n) => {
      if (n.type === 'text') {
        let s = n.text ?? ''
        const marks = n.marks ?? []
        if (marks.some((m) => m.type === 'bold')) s = `**${s}**`
        if (marks.some((m) => m.type === 'italic')) s = `*${s}*`
        if (marks.some((m) => m.type === 'strike')) s = `~~${s}~~`
        if (marks.some((m) => m.type === 'code')) s = `\`${s}\``
        return s
      }
      if (n.type === 'hardBreak') return '\n'
      if (n.type === 'paragraph') return inlineText(n.content)
      return inlineText(n.content)
    })
    .join('')
}

function blockToMd(node: Node): string[] {
  switch (node.type) {
    case 'heading': {
      const level = Number(node.attrs?.level ?? 1)
      return [`${'#'.repeat(Math.min(6, Math.max(1, level)))} ${inlineText(node.content)}`]
    }
    case 'paragraph':
      return [inlineText(node.content)]
    case 'bulletList':
      return [(node.content ?? []).map((li) => `- ${listItemText(li)}`).join('\n')]
    case 'orderedList':
      return [
        (node.content ?? [])
          .map((li, i) => `${i + 1}. ${listItemText(li)}`)
          .join('\n'),
      ]
    case 'codeBlock':
      return ['```', inlineText(node.content), '```']
    case 'blockquote':
      return [(node.content ?? []).map((p) => `> ${inlineText(p.content)}`).join('\n')]
    case 'horizontalRule':
      return ['---']
    default:
      return [inlineText(node.content)]
  }
}

function listItemText(li: Node): string {
  return (li.content ?? []).map((p) => inlineText(p.content)).join('\n  ')
}

/** Tiptap doc JSON → Markdown（保存/回退栈用） */
export function editorJsonToMarkdown(doc: Node | null | undefined): string {
  if (!doc || !Array.isArray(doc.content)) return ''
  const blocks = doc.content.flatMap(blockToMd)
  const joined = blocks.join('\n\n').replace(/\n{3,}/g, '\n\n').trim()
  return joined ? `${joined}\n` : ''
}
