/**
 * 字数统计：与后端 `services/files.count_words` 同口径——
 * 去除 markdown 标记（# > * ` - [ ] ! |）与全部空白后按字符计。
 * 编辑模式工具栏位置标签与底部字数条共用，保证编辑时实时刷新且与后端一致。
 */
export function countWords(text: string): number {
  if (!text) return 0
  const cleaned = text.replace(/[#>*`\-[\]!|]/g, '').replace(/\s+/g, '')
  return cleaned.length
}
