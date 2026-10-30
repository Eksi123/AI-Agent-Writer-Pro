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
    /** v1030：小说项目菜单 [重命名] → [编辑] */
    edit: '编辑',
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
    invalidJson: 'JSON 格式不正确',
  },
  sidebar: {
    newNovel: '新建小说',
    deleteNovel: '删除小说',
    projects: '小说项目',
    settings: '通用设置',
    aiTools: 'AI 设置',
    /** v1028：左侧栏不再展示空提示，仅主页面展示 */
    noProjects: '还没有小说项目，点击「新建小说」开始创作',
    chaptersUnit: '章',
    wordsUnit: '字',
    author: '作者',
    genre: '题材',
    noAuthorGenre: '暂无作者与题材',
  },
  novel: {
    createTitle: '新建小说',
    titleLabel: '书名',
    /** v1029：书名左上角用 * 标记必填；输入框内不再有任何提示词 */
    titlePlaceholder: '',
    titleRuleHint: '书籍名称不允许为空，不允许全部为阿拉伯数字，不允许出现#$%等特殊字符，可以出现标点符号',
    authorLabel: '作者',
    genreLabel: '题材',
    /** v1029：题材改为勾选标签，最少 1 个、最多 3 个 */
    genreHint: '最少选择 1 个，最多选择 3 个',
    genreRequired: '请至少选择一个题材',
    genreTooMany: '题材最多只能选择 3 个',
    synopsisLabel: '简介',
    /** v1029：简介输入框内不再有任何提示词 */
    synopsisPlaceholder: '',
    deleteConfirmTitle: '删除小说',
    deleteConfirm: '删除后项目会移入回收站（data/trash/），列表不再显示。确定删除「{title}」吗？',
    duplicateTitle: '复制小说',
    duplicateLabel: '副本书名',
    created: '小说「{title}」创建成功',
    deleted: '小说已删除（软删除，可在 data/trash/ 恢复）',
    /** v1030：编辑小说成功（名称 / 题材 / 简介） */
    edited: '已保存修改',
    editTitle: '编辑小说',
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
    wordCount: '字数',
    unsaved: '有未保存的修改',
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
    /** v1029：顺序不再由大纲派生，改为「插入」直接指定位置 */
    orderHint: '卷章顺序可直接调整：用「插入」指定序号插入，其后各项自动顺延。',
    gotoOutline: '去大纲编辑',
    newVolume: '新建卷',
    newChapter: '新建章',
    volumeTitleLabel: '卷名',
    chapterTitleLabel: '章名',
    /** v1029：弹窗内输入框不要提示词 */
    titlePlaceholder: '',
    summaryLabel: '卷简介',
    deleteVolumeTitle: '删除卷',
    deleteVolumeConfirm:
      '删除卷「{title}」会级联删除其下 {count} 个章节（共约 {words} 字），且不可撤销。确定删除吗？',
    deleteChapterTitle: '删除章',
    deleteChapterConfirm: '确定删除章节「{title}」吗？此操作不可撤销。',
    /** v1029：删空卷的二次确认文案 */
    deleteLastChapterConfirm:
      '「{title}」是本卷最后一章，删除后该卷「{volume}」也会一并删除。此操作不可撤销，确定吗？',
    /** v1029：插入弹窗 */
    insertVolumeTitle: '插入小说卷',
    insertChapterTitle: '插入小说章节',
    insertBeforeVolume: '往前插入小说卷',
    insertAfterVolume: '往后插入小说卷',
    insertBeforeChapter: '往前插入小说章节',
    insertAfterChapter: '往后插入小说章节',
    ordinalLabel: '卷序号',
    chapterOrdinalLabel: '章节序号',
    ordinalHint: '正整数，不可与已有{kind}序号重复；1 = 插到最前面',
    renameDialogTitle: '重命名',
    /** v1029：重命名弹窗同时含卷名（默认填当前卷名 = 不修改） */
    renameVolumeHint: '默认不修改卷名',
    appendedHint: '不填序号则追加到末尾；填序号则插入到该位置（其后各项自动顺延）。',
    noMoveHint: '章节不支持跨卷移动：需换卷请在目标卷下新建同名章并复制正文。',
    volumeCreated: '卷「{title}」已创建',
    chapterCreated: '章「{title}」已创建',
    volumeInserted: '已在第 {ordinal} 位插入卷「{title}」',
    chapterInserted: '已在第 {ordinal} 位插入章「{title}」',
    volumeDeleted: '卷已删除（含 {count} 个章节）',
    chapterDeleted: '章已删除',
    /** v1029：删空卷时的提示 */
    chapterDeletedVolumeGone: '章已删除，该卷已无章节，卷「{volume}」一并删除',
    renamedOk: '已重命名',
    chaptersToBeDeleted: '将被删除的章节',
    /** v1029：编辑模式主页面右上角的卷章操作 */
    editNoTarget: '请先在目录中选择要操作的卷或章节',
    deleteOutlineDenied: '大纲是整本书的创作参考，不能在此删除；如需修改请在大纲编辑器中编辑。',
  },
  outline: {
    title: '大纲',
    /** v1029：大纲是创作参考，保存不再同步卷章顺序 */
    saved: '大纲已保存',
    validateOk: '大纲校验通过（{volumes} 卷 / {chapters} 章）',
    validateIssues: '大纲校验发现 {count} 条提示',
    warnings: '警告',
    errors: '错误',
    missing: '缺失清单',
    unregistered: '未在大纲登记（大纲仅作参考，不影响卷章结构）',
    mismatches: '名称不一致',
    linePrefix: '第 {line} 行',
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
    themeEye: '护眼',
    fontSize: '正文字号',
    fontFamily: '字体',
    fontSerif: '思源宋体',
    fontHei: '思源黑体',
    fontKai: '霞鹜文楷',
    fontSystem: '系统默认',
    /** v1030：新增自动保存开关；间隔标签去掉「（秒）」，提示改文案 */
    autosaveEnabled: '自动保存',
    autosaveInterval: '自动保存间隔',
    autosaveHint: '编辑模式下辅助主动保存，时长单位为秒',
    saved: '设置已保存',
    resetDefault: '恢复默认设置',
    resetConfirmTitle: '恢复默认设置',
    resetConfirm: '将把主题、字号、字体、自动保存间隔等全部恢复为默认值，确定吗？',
    resetDone: '已恢复默认设置',
    back: '返回',
  },
  aiTools: {
    /** v1028：AI 工具 → AI 设置 */
    title: 'AI 设置',
    /** v1028：大模型 API → 大模型 */
    tabLlm: '大模型',
    tabSubagents: '写作子代理',
    tabSkills: '写作技能',
    providerName: '名称',
    /** v1028：类型 → 协议类型（三种） */
    providerType: '协议类型',
    /** v1028：API 地址 → Base Url */
    baseUrl: 'Base Url',
    apiKeyEnv: '密钥名',
    apiKeyValue: '密钥API',
    apiKeyValuePlaceholder: '保存后写入系统级环境变量，仅显示掩码',
    apiKeyConfigured: '已配置',
    apiKeyMissing: '未配置密钥',
    /** v1028：添加 Provider → 添加大模型（右上角） */
    addProvider: '添加大模型',
    /** v1028：大模型级常用参数 */
    /** v1029：超时时长 → Time Out */
    timeoutSec: 'Time Out',
    temperature: 'Temperature',
    maxTokens: 'Max Token',
    tempRangeHint: '取值范围 {range}（不包括 0）',
    /** v1029：不再需要「模型 ID」——名称就等于模型 ID */
    maxContext: '上下文窗口',
    isDefault: '设为默认',
    testConnection: '测试连接',
    testOk: '连接成功（{ms} ms）',
    testFail: '连接失败',
    deleteProviderConfirm: '删除「{name}」后其配置与模型将一并移除。确定吗？',
    /** v1028 文案 */
    noProviders: '还没有配置任何大模型。添加后即可与 AI 写作助手对话。',
    savedProvider: '大模型配置已保存',
    deletedProvider: '已删除',
    requiredAll: '所有配置项均为必填',
  },
  subagents: {
    /** v1028：顶部提示改为启用/停用按钮的悬浮说明 */
    enableHint: '子代理供主代理按需委派（策划 / 撰写 / 润色 / 连贯性 / 资料检索）；停用后主代理不再委派。',
    enabledLabel: '启用',
    disabledLabel: '停用',
    resetDefault: '恢复默认',
    resetConfirmTitle: '恢复默认',
    resetConfirm: '恢复「{name}」为默认配置？你的自定义提示词与技能选择将被清除。',
    resetDone: '已恢复默认配置',
    add: '新建子代理',
    builtin: '内置',
    custom: '自建',
    nameLabel: '名称',
    namePlaceholder: '如my-writer',
    descLabel: '描述',
    promptLabel: '系统提示词',
    skillsLabel: '可用技能',
    skillsPlaceholder: '从写作技能中选择（支持多选与全选）',
    selectAll: '全选',
    /** v1028：内置技能 = Agent 基础工具，展示名不带描述 */
    builtinToolsLabel: '内置工具',
    requiredNameDescPrompt: '名称、描述、系统提示词均为必填',
  },
  skills: {
    add: '新建技能',
    builtin: '内置',
    custom: '自建',
    nameLabel: '名称',
    descLabel: '描述',
    bodyLabel: '技能正文',
    view: '查看',
    edit: '编辑',
    /** v1028：自建技能只包含提示词，正文必填 */
    requiredNameDescBody: '名称、描述、技能正文均为必填',
    deleteConfirm: '删除技能「{name}」会同时移除其物化目录。确定吗？',
  },
  agent: {
    /** v1028：AI 助手 → AI 写作助手 */
    panelTitle: 'AI写作助手',
    panelTooltip: '来试试AI写作功能吧，它可以实现创作小说、润色大纲、检验剧情、剧情汇总等等功能',
    expand: '展开 AI 写作助手',
    collapse: '收起 AI 会话框',
    /** v1029：右栏可向左拉伸（最多到主页面的 1/3） */
    resizeHint: '按住拖动可调整会话框宽度（最宽到主页面的 1/3）',
    /** v1029：头部展示当前会话数量 + 当前小说项目 token 使用量（无模型提示已删除） */
    sessionCount: '{n}个会话',
    /** v1029：头部 token 用量带单位（x.yK Token） */
    tokenTotal: '{n}K Token',
    tokenUsage: '{used}K / {window}K',
    tokenUsageOnly: '已用 {used}K tokens',
    sessionLabel: '会话',
    newSession: '新建会话',
    renameSession: '重命名会话',
    deleteSession: '删除会话',
    exportSession: '导出会话',
    deleteSessionConfirm: '删除会话会同时删除其消息记录（不影响小说内容）。确定吗？',
    /** v1028：空态提示 */
    noNovel: '还没有小说项目哦~',
    noSession: '快点来开启AI写作吧~',
    backToList: '返回会话列表',
    sessionListTitle: '会话记录',
    inputPlaceholder: '描述你的需求，例如：帮我扩写第三章的打斗场面…',
    send: '发送',
    stop: '停止',
    running: 'AI 正在思考…',
    /** v1028：运行中点击发送 → 三种处理方式（参考 copilot） */
    sendAnyway: '停止并发送',
    steer: '通过消息引导发送',
    queue: '添加到队列',
    regenerate: '重新生成',
    copyMessage: '复制',
    usage: '用量：入 {in} / 出 {out} tokens',
    planTitle: '计划',
    gateStage: '门禁阶段',
    subagentLane: '子代理',
    fileChanged: 'AI 修改了文件：{path}',
    approvalTitle: '需要你审批',
    approvalRisk: '高危操作',
    approvalTool: '工具',
    approvalArgs: '参数',
    approvalReason: '原因',
    approve: '批准',
    editThenApprove: '编辑后批准',
    reject: '拒绝',
    respond: '回复补充',
    editedArgsLabel: '修改后的参数（JSON）',
    rejectMessageLabel: '拒绝理由（会转告给 AI）',
    respondMessageLabel: '补充说明（会作为工具结果返回给 AI）',
    consultTitle: 'AI 需要你决定',
    consultOption: '选择',
    consultText: '输入',
    consultSendOption: '提交选择',
    consultSendText: '提交说明',
    cancelledRun: '运行已取消',
    runFailed: '运行出错：{msg}',
    modelForThisMessage: '本次使用的模型',
    useSessionModel: '会话默认',
    /** v1028：输入框内的 + / 工具 / Model */
    addContext: '添加内容',
    ctxOutline: '大纲',
    ctxLocalFile: '本地文件',
    /** v1030：添加文件页（小说大纲或章节 / 本地文件 / 最近打开的章节） */
    ctxNovelContent: '小说项目大纲或章节',
    pickerSearchPlaceholder: '搜索小说大纲或章节…',
    pickerNoMatch: '没有匹配的大纲或章节',
    /** v1030：小说项目二级目录为空时的居中提示 */
    pickerNoNovels: '还没有小说项目',
    pickerRecent: '最近打开的章节',
    tools: '工具',
    /** v1029：工具弹窗（写作子代理 / 写作技能） */
    toolsWritingSubagents: '写作子代理',
    toolsWritingSkills: '写作技能',
    model: 'Model',
    modelOff: 'Model Off',
    contextUsage: '上下文使用情况',
    /** v1029（1.4）：Model Off 右侧「剩余上下文百分比」= 1 − 已占用token ÷ 窗口总容量 */
    contextRemaining: '{n}%',
    /** v1029：未配置大模型时的提示（只在模型弹窗内出现；会话框上方不再提示） */
    noModelsHint: '尚未配置大模型',
    generateChapter: '生成本章',
  },
  health: {
    backendOffline: '后端未连接（请确认 uvicorn 已在 127.0.0.1:8000 运行）',
    fts5Degraded: 'FTS5 不可用，检索已降级为 LIKE 模糊匹配',
  },
} as const

export type I18nKey = string

type Dict = Record<string, unknown>

/**
 * v1029：可勾选的题材标签（新建小说弹窗）。最多勾选 3 个、最少 1 个。
 * 与后端 `novel.yaml` 的 `genre`（字符串）对应——多选用「、」连接存储，单值直接存。
 */
export const GENRE_OPTIONS: readonly string[] = [
  '玄幻',
  '仙侠',
  '武侠',
  '都市',
  '架空历史',
  '奇幻',
  '科幻',
  '悬疑',
  '游戏',
  '军事',
  '历史',
  '现实',
  '言情',
  '同人',
]

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
