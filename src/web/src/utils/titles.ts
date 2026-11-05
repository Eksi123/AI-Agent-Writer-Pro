/**
 * 卷 / 章标题展示与序号工具。
 *
 * 目录与卷章树统一按「第X卷 卷名」「第X章 章节名」展示：
 * - 标题已自带序号前缀（如「第一卷 风起」）→ 原样展示；
 * - 标题只有卷名（如「风起」）→ 按所在序号补前缀。
 *
 * 纯展示逻辑，不改写后端数据：卷章顺序由「插入」维护。
 */

const CN_DIGITS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九']

/** 阿拉伯数字 → 中文数字（支持 0–9999，足够卷章序号使用） */
export function toChineseNumber(n: number): string {
  if (!Number.isFinite(n) || n < 0) return String(n)
  if (n < 10) return CN_DIGITS[n]
  if (n < 20) return `十${n % 10 ? CN_DIGITS[n % 10] : ''}`
  if (n < 100) {
    const tens = Math.floor(n / 10)
    return `${CN_DIGITS[tens]}十${n % 10 ? CN_DIGITS[n % 10] : ''}`
  }
  if (n < 1000) {
    const hundreds = Math.floor(n / 100)
    const rest = n % 100
    return `${CN_DIGITS[hundreds]}百${rest ? (rest < 10 ? `零${CN_DIGITS[rest]}` : toChineseNumber(rest)) : ''}`
  }
  if (n < 10000) {
    const thousands = Math.floor(n / 1000)
    const rest = n % 1000
    return `${CN_DIGITS[thousands]}千${rest ? (rest < 100 ? `零${toChineseNumber(rest)}` : toChineseNumber(rest)) : ''}`
  }
  return String(n)
}

const CN_NUM = '零一二三四五六七八九十百千万两〇壹贰叁肆伍陆柒捌玖拾佰仟'

/** 已有序号前缀：「第X卷」「第X章」「第X回」… */
const ORDINAL_PREFIX = new RegExp(`^第\\s*[${CN_NUM}\\d]+\\s*[卷章回节篇部集话話]`)

/** 标题已带序号前缀时原样返回，否则补上「第{index}卷/章 」前缀 */
export function formatOrdinalTitle(title: string, index: number, kind: '卷' | '章'): string {
  const name = (title || '').trim()
  if (ORDINAL_PREFIX.test(name)) return name
  const no = index > 0 ? index : 1
  return name ? `第${toChineseNumber(no)}${kind} ${name}` : `第${toChineseNumber(no)}${kind}`
}

/** 卷标题展示：第X卷 卷名（X 取 sort_order，缺省用数组下标 +1） */
export function formatVolumeTitle(
  title: string,
  sortOrder: number | null | undefined,
  index: number,
): string {
  const order = sortOrder != null && sortOrder > 0 ? sortOrder : index + 1
  return formatOrdinalTitle(title, order, '卷')
}

/** 章标题展示：第X章 章节名（X 取 sort_order，缺省用数组下标 +1） */
export function formatChapterTitle(
  title: string,
  sortOrder: number | null | undefined,
  index: number,
): string {
  const order = sortOrder != null && sortOrder > 0 ? sortOrder : index + 1
  return formatOrdinalTitle(title, order, '章')
}

/** 去掉「第X卷」「第X章」前缀，只留卷名/章名（重命名弹窗默认值用） */
export function stripOrdinalPrefix(title: string): string {
  return (title || '').replace(ORDINAL_PREFIX, '').replace(/^[\s·\-—_]+/, '').trim()
}

/** 中文数字 → 阿拉伯数字（支持 零–九千九百九十九） */
export function chineseNumberToArabic(cn: string): number | null {
  const s = (cn || '').trim()
  if (!s) return null
  if (/^\d+$/.test(s)) return Number.parseInt(s, 10)

  const digitOf = (ch: string): number => {
    const i = CN_DIGITS.indexOf(ch)
    if (i >= 0) return i
    const alt: Record<string, number> = {
      壹: 1, 贰: 2, 叁: 3, 肆: 4, 伍: 5, 陆: 6, 柒: 7, 捌: 8, 玖: 9, 两: 2, 〇: 0,
    }
    return alt[ch] ?? -1
  }
  const unitOf = (ch: string): number => {
    const units: Record<string, number> = { 十: 10, 拾: 10, 百: 100, 佰: 100, 千: 1000, 仟: 1000 }
    return units[ch] ?? 0
  }

  let total = 0
  let section = 0
  let digit = 0
  let seenUnit = false
  for (const ch of s) {
    if (ch === '万') {
      total = (total + section + (digit || 0)) * 10000
      section = 0
      digit = 0
      seenUnit = true
      continue
    }
    const unit = unitOf(ch)
    if (unit) {
      // 「十二」这类省略前导一的写法按 1 计
      const base = digit || (seenUnit || total ? digit : 1)
      section += (base || 1) * unit
      digit = 0
      seenUnit = true
      continue
    }
    const v = digitOf(ch)
    if (v < 0) return null
    digit = v
  }
  return total + section + digit
}

/** 从标题里解析已有序号（「第三章 风起」→ 3）；无序号返回 null */
export function parseOrdinal(title: string): number | null {
  const m = (title || '').match(new RegExp(`^第\\s*([${CN_NUM}\\d]+)\\s*[卷章回节篇部集话話]`))
  if (!m) return null
  return chineseNumberToArabic(m[1])
}

/** 目录节点的大纲标题：「小说名称_大纲」 */
export function formatOutlineNode(novelTitle: string): string {
  const title = (novelTitle || '').trim()
  return title ? `${title}_大纲` : '大纲'
}
