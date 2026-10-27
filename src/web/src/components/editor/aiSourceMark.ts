/**
 * AI 来源标记（Tiptap Mark 扩展，手册「三、(3)【正文】」：AI 写入段落带来源标记）。
 *
 * 被 AI 改写接受的文本包一层 `<span data-ai-source="{mode}" title="...">`，
 * 视觉上以左边框/底色区分；点击标记可回滚为原文（原文存 mark 的 original 属性）。
 */
import { Mark, mergeAttributes } from '@tiptap/core'

export interface AiSourceOptions {
  HTMLAttributes: Record<string, unknown>
}

declare module '@tiptap/core' {
  interface Commands<ReturnType> {
    aiSource: {
      /** 给当前选区打 AI 来源标记（mode + 原文，用于回滚） */
      setAiSource: (attrs: { mode: string; original: string }) => ReturnType
      /** 移除当前选区的 AI 来源标记（回滚） */
      unsetAiSource: () => ReturnType
    }
  }
}

export const AiSource = Mark.create<AiSourceOptions>({
  name: 'aiSource',

  addOptions() {
    return { HTMLAttributes: {} }
  },

  addAttributes() {
    return {
      mode: {
        default: 'rewrite',
        parseHTML: (el) => el.getAttribute('data-ai-mode') ?? 'rewrite',
        renderHTML: (attrs) => ({ 'data-ai-mode': attrs.mode }),
      },
      original: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-ai-original') ?? '',
        renderHTML: () => ({}),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-ai-source]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'span',
      mergeAttributes(this.options.HTMLAttributes, { 'data-ai-source': 'true' }, HTMLAttributes),
      0,
    ]
  },

  addCommands() {
    return {
      setAiSource:
        (attrs) =>
        ({ commands }) =>
          commands.setMark(this.name, attrs),
      unsetAiSource:
        () =>
        ({ commands }) =>
          commands.unsetMark(this.name),
    }
  },
})
