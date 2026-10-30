/**
 * 显式命名校验（前端侧，与后端 `app/services/naming.py` 同规则）。
 *
 * v1029 需求 5-(1.5)：卷名 / 章节名的命名规则**与书名一致**——
 * 不允许为空、不允许全部为阿拉伯数字、不允许出现 `#$%` 等特殊字符，可以出现标点符号。
 */

/** 书名禁用的特殊字符（与后端 `_SPECIAL_CHARS` 保持一致） */
const SPECIAL_CHARS = /[#\$%&*+=<>@\^|~`\\]/

/** 归一化：全角空格转半角、压缩连续空白、去首尾 */
export function normalizeName(raw: string): string {
  return (raw || '').replace(/\u3000/g, ' ').replace(/\s+/g, ' ').trim()
}

/**
 * 校验书名 / 卷名 / 章名（同一套规则）。
 * @param kind 名称种类，用于错误文案（书名 / 卷名 / 章名）
 * @returns 错误信息；合法时返回空串
 */
export function validateName(raw: string, kind = '书籍名称'): string {
  const name = normalizeName(raw)
  if (!name) return `${kind}不允许为空`

  const digits = name.replace(/\s/g, '')
  if (digits && /^\d+$/.test(digits)) return `${kind}不允许全部为阿拉伯数字`

  const bad = [...new Set(name.split('').filter((c) => SPECIAL_CHARS.test(c)))]
  if (bad.length) return `${kind}不允许出现特殊字符 ${bad.join(' ')}（可以出现标点符号）`

  return ''
}

export const validateBookTitle = (raw: string): string => validateName(raw, '书籍名称')
export const validateVolumeName = (raw: string): string => validateName(raw, '卷名')
export const validateChapterName = (raw: string): string => validateName(raw, '章节名')

/**
 * 校验插入序号：正整数且不超出 1..N+1（N = 当前已有数量；N+1 = 追加到末尾）。
 * @returns 错误信息；合法时返回空串
 */
export function validateOrdinal(raw: number | string, count: number, kind = '序号'): string {
  const n = typeof raw === 'string' ? Number.parseInt(raw.trim(), 10) : raw
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1) return `${kind}必须是正整数`
  if (n > count + 1) return `${kind}超出范围（当前共 ${count} 个，可填 1~${count + 1}）`
  return ''
}
