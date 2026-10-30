/**
 * 题材标签数据源（v1030续）。
 *
 * - 内置 28 个**最主流**的网文题材（对齐起点 / 番茄 / 晋江的男频、女频主分类与热门流派）；
 * - 允许用户通过弹窗「+」新增自定义题材，★自定义题材**只在本次新建/编辑弹窗内有效**，
 *   弹窗提交（创建或保存小说）后即清空、不落 localStorage；
 *   自定义题材名最多 5 个字，与内置题材一起参与勾选（★仍受「不少于 1 个、不多于 3 个」约束）。
 */
import { ref } from 'vue'

/** 内置题材：固定 28 个，顺序 = 展示顺序 */
export const BUILTIN_GENRES: readonly string[] = [
  // 男频主分类（12）
  '玄幻',
  '奇幻',
  '西幻',
  '武侠',
  '仙侠',
  '都市',
  '军事',
  '历史',
  '游戏',
  '科幻',
  '悬疑',
  '轻小说',
  // 女频主分类（8）
  '古代言情',
  '现代言情',
  '幻想言情',
  '浪漫青春',
  '婚恋情缘',
  '纯爱',
  '同人',
  '百合',
  // 热门流派（8）
  '穿越',
  '重生',
  '系统流',
  '无限流',
  '末世',
  '修真',
  '灵异',
  '种田流',
]

/** 内置题材数量上限（需求：最多显示 28 个） */
export const BUILTIN_GENRE_COUNT = 28

/** 自定义题材名长度上限（v1030续：不能超过 5 个字） */
export const CUSTOM_GENRE_MAX_LEN = 5

/**
 * 自定义题材（响应式）：新增后立即驱动界面出现新标签。
 * ★不持久化——只在当前弹窗生命周期内存在，提交后由 clearCustomGenres() 清掉。
 */
export const customGenres = ref<string[]>([])

/** 全部可勾选题材 = 内置 28 个 + 自定义 */
export const allGenres = ref<string[]>([...BUILTIN_GENRES])

function refresh(): void {
  allGenres.value = [...BUILTIN_GENRES, ...customGenres.value]
}

/** 题材名规范化：压缩空白、去首尾；自定义题材沿用书名规则里被允许的字符集 */
export function normalizeGenre(raw: string): string {
  return (raw || '').replace(/\u3000/g, ' ').replace(/\s+/g, '').trim()
}

const SPECIAL_CHARS = /[#\$%&*+=<>@\^|~`\\]/

/**
 * 校验自定义题材名。
 * @returns 错误信息；合法时返回空串
 */
export function validateGenreName(raw: string): string {
  const name = normalizeGenre(raw)
  if (!name) return '题材名不允许为空'
  if (name.length > CUSTOM_GENRE_MAX_LEN) return `题材名过长（最多 ${CUSTOM_GENRE_MAX_LEN} 个字）`
  if (SPECIAL_CHARS.test(name)) return '题材名不允许出现特殊字符'
  if (BUILTIN_GENRES.includes(name) || customGenres.value.includes(name)) return '该题材已存在'
  return ''
}

/** 新增自定义题材：成功返回归一化后的名称，失败返回空串（★仅本次弹窗有效，不落盘） */
export function addCustomGenre(raw: string): string {
  const name = normalizeGenre(raw)
  if (validateGenreName(name)) return ''
  customGenres.value = [...customGenres.value, name]
  refresh()
  return name
}

/**
 * 清空全部自定义题材。
 * ★v1030续：自定义题材只在本次「新建/编辑小说」弹窗内有效，弹窗提交成功后调用本方法清掉，
 * 下次打开弹窗只保留内置 28 个题材（不保留自定义）。
 */
export function clearCustomGenres(): void {
  if (!customGenres.value.length) return
  customGenres.value = []
  refresh()
}
