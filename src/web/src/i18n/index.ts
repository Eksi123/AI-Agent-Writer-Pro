/**
 * 中文文案集中管理（手册铁律 7：UI 默认中文，便于后续双语扩展）。
 * 所有界面文案一律走 t('key')，不在组件里硬编码中文字符串。
 */

export const zh = {
  app: {
    title: 'AI Writer Pro',
    subtitle: '大纲驱动的 AI 小说写作工作台',
  },
  common: {
    confirm: '确定',
    cancel: '取消',
    save: '保存',
    saved: '已保存',
    saving: '保存中…',
    delete: '删除',
    rename: '重命名',
    duplicate: '复制',
    export: '导出',
    create: '新建',
    close: '关闭',
    retry: '重试',
    loading: '加载中…',
    empty: '暂无内容',
    required: '此项为必填',
    unknownError: '操作失败，请稍后重试',
    copySuccess: '已复制到剪贴板',
  },
  sidebar: {
    newNovel: '新建小说',
    deleteNovel: '删除小说',
    projects: '小说项目',
    settings: '通用设置',
    aiTools: 'AI 工具',
    noProjects: '还没有小说项目，点击上方「新建小说」开始创作',
    chaptersUnit: '章',
    wordsUnit: '字',
  },
  novel: {
    createTitle: '新建小说',
    titleLabel: '书名',
    titlePlaceholder: '请填写具体书名（不允许「未命名小说」「第 1 卷」等默认名）',
    authorLabel: '作者名',
    genreLabel: '题材',
    synopsisLabel: '简介',
    renameTitle: '重命名小说',
    deleteConfirmTitle: '删除小说',
    deleteConfirm: '删除后项目会移入回收站（data/trash/），列表不再显示。确定删除「{title}」吗？',
    duplicateTitle: '复制小说',
    duplicateLabel: '副本书名',
    created: '小说「{title}」创建成功',
    deleted: '小说已删除（软删除，可在 data/trash/ 恢复）',
    renamed: '已重命名，大纲文件同步更新',
    namingRejected: '书名不合法：不允许空名、默认名或纯序号名',
  },
  reader: {
    modeRead: '阅读模式',
    modeEdit: '编辑模式',
    toc: '目录',
    outlineNode: '大纲',
    prevChapter: '上一章',
    nextChapter: '下一章',
    nightMode: '夜间模式',
    progressRestored: '已定位到上次阅读位置',
    emptyChapter: '本章还没有正文，切换到编辑模式开始撰写。',
  },
  editor: {
    tabContent: '正文',
    tabVolumes: '卷章',
    wordCount: '字数',
    autosaveOn: '自动保存已开启（{sec} 秒）',
    autosaveOff: '自动保存已关闭',
    lastSaved: '上次保存 {time}',
    unsaved: '有未保存的修改',
    saveNow: '立即保存',
    aiRewrite: 'AI 改写',
    aiExpand: 'AI 扩写',
    aiCondense: 'AI 缩写',
    aiPolish: 'AI 润色',
    finalize: '标记定稿',
    unfinalize: '取消定稿',
    revisions: '修订历史',
    restoreRevision: '回滚到此版本',
    restoreConfirm: '回滚会用该快照覆盖当前正文（当前内容会先存一份快照）。确定继续吗？',
    restored: '已回滚到所选版本',
  },
  volumeChapter: {
    title: '卷章管理',
    orderHint: '卷章顺序由大纲小节先后决定，此处不可拖动重排。',
    gotoOutline: '去大纲调整',
    newVolume: '新建卷',
    newChapter: '新建章',
    volumeTitleLabel: '卷名',
    chapterTitleLabel: '章名',
    titlePlaceholder: '必填，不允许「第 1 卷」「第 1 章」等默认名或纯序号名',
    summaryLabel: '卷简介',
    deleteVolumeTitle: '删除卷',
    deleteVolumeConfirm:
      '删除卷「{title}」会级联删除其下 {count} 个章节（共约 {words} 字），且不可撤销。确定删除吗？',
    deleteChapterTitle: '删除章',
    deleteChapterConfirm: '确定删除章节「{title}」吗？此操作不可撤销。',
    appendedHint: '新建的卷/章会追加到末尾；如需插到中间，请在大纲中调整小节顺序。',
    noMoveHint: '章节不支持跨卷移动：需换卷请在目标卷下新建同名章并复制正文。',
    volumeCreated: '卷「{title}」已创建',
    chapterCreated: '章「{title}」已创建',
    volumeDeleted: '卷已删除（含 {count} 个章节）',
    chapterDeleted: '章已删除',
    renamedOk: '已重命名',
    chaptersToBeDeleted: '将被删除的章节',
  },
  outline: {
    title: '大纲',
    edit: '编辑',
    preview: '预览',
    saved: '大纲已保存，卷章顺序已同步',
    orderSynced: '顺序回填：卷 {volumes} 项、章 {chapters} 项',
    validateOk: '大纲校验通过（{volumes} 卷 / {chapters} 章）',
    validateIssues: '大纲校验发现 {count} 条提示',
    warnings: '警告',
    errors: '错误',
    missing: '缺失清单',
    unregistered: '未在大纲登记（顺序保持并追加末尾）',
    mismatches: '名称不一致',
    linePrefix: '第 {line} 行',
    editorHint: '请保持层级：# 为卷（或附录），## 为章；简介行以「- 简介：」开头。',
  },
  conflict: {
    title: '内容冲突',
    message: 'AI 或其它端已修改本章，请选择如何处理。',
    viewDiff: '查看差异',
    merge: '合并',
    overwrite: '覆盖',
    overwritten: '已用本地内容覆盖（基于最新版本）',
    serverVersion: '服务端版本',
    localVersion: '本地版本',
    lastWriter: '最后修改方',
    writerManual: '手动编辑',
    writerAgent: 'AI',
  },
  export: {
    title: '导出小说',
    formatLabel: '正文格式',
    fmtMarkdown: 'Markdown（各章 md 集合）',
    fmtTxt: 'TXT（整本纯文本）',
    fmtEpub: 'EPUB 3',
    fmtMobi: 'MOBI（需安装 calibre）',
    includeOutline: '附带大纲原 markdown 文件',
    start: '开始导出',
    pending: '排队中',
    running: '导出中… {progress}%',
    done: '导出完成',
    failed: '导出失败',
    download: '下载',
    calibreMissing: '未检测到 calibre，MOBI 选项不可用。安装 calibre 后即可导出。',
  },
  settings: {
    title: '通用设置',
    theme: '主题',
    themeLight: '浅色',
    themeDark: '深色',
    themeSystem: '跟随系统',
    primaryColor: '主题色',
    fontSize: '正文字号',
    fontFamily: '字体',
    fontSerif: '宋体',
    fontHei: '黑体',
    fontKai: '楷体',
    readingWidth: '阅读页宽（字符）',
    autosaveInterval: '自动保存间隔（秒）',
    fallbackCity: '回落城市',
    fallbackCityHint: '浏览器定位失败或被拒绝时使用；定位坐标不入库。',
    petIdleMinutes: '宠物打瞌睡阈值（分钟无正文变更）',
    saved: '设置已保存',
    back: '返回工作台',
  },
  aiTools: {
    title: 'AI 工具',
    comingM3: '大模型 Provider / 子代理 / 技能 / MCP 管理在 M3 里程碑交付。',
  },
  agent: {
    panelTitle: 'AI 助手',
    comingM3: 'Agent 会话面板在 M3 里程碑交付（SSE 流式 + 审批卡片 + 消息树分支）。',
    expand: '展开 AI 面板',
    collapse: '收起 AI 面板',
  },
  health: {
    backendOffline: '后端未连接（请确认 uvicorn 已在 127.0.0.1:8000 运行）',
    fts5Degraded: 'FTS5 不可用，检索已降级为 LIKE 模糊匹配',
  },
} as const

export type I18nKey = string

type Dict = Record<string, unknown>

/** 简单插值：t('novel.created', { title: 'x' }) → 小说「x」创建成功 */
export function t(key: string, vars?: Record<string, string | number>): string {
  const parts = key.split('.')
  let node: unknown = zh as Dict
  for (const p of parts) {
    if (node && typeof node === 'object' && p in (node as Dict)) {
      node = (node as Dict)[p]
    } else {
      return key
    }
  }
  if (typeof node !== 'string') return key
  if (!vars) return node
  return node.replace(/\{(\w+)\}/g, (_m, name: string) =>
    name in vars ? String(vars[name]) : `{${name}}`,
  )
}
