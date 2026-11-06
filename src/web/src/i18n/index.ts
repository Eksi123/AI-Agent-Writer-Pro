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
    /** 小说项目菜单 [重命名] → [编辑] */
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
    /** 左侧栏不再展示空提示，仅主页面展示 */
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
    /** 书名左上角用 * 标记必填；输入框内不再有任何提示词 */
    titlePlaceholder: '',
    titleRuleHint: '书籍名称不允许为空，不允许全部为阿拉伯数字，不允许出现#$%等特殊字符，可以出现标点符号',
    genreLabel: '题材',
    /** 题材「最少 1 最多 3」改为隐藏逻辑，界面不再展示提示文案 */
    genreRequired: '请至少选择一个题材',
    genreTooMany: '题材最多只能选择 3 个',
    /** 题材末尾的「+」新增自定义题材（最多 5 个字，仅本次弹窗有效，提交后不保留）
     * 标签「题材名」改为「题材」，并去掉标签下方的提示语 */
    genreAddTitle: '自定义题材',
    genreAddLabel: '题材',
    genreAddOk: '题材「{name}」已添加',
    genreAddFailed: '题材添加失败：{reason}',
    synopsisLabel: '简介',
    /** 简介输入框内不再有任何提示词 */
    synopsisPlaceholder: '',
    deleteConfirmTitle: '删除小说',
    /** 文案改为「删除后《X》会移入回收站，但不建议这么做！确定删除吗？」 */
    deleteConfirm: '删除后《{title}》会移入回收站，但不建议这么做！确定删除吗？',
    duplicateTitle: '复制小说',
    duplicateLabel: '副本书名',
    created: '小说「{title}」创建成功',
    /** 删除即进回收站，提示与确认框口径一致（不再暴露 data/trash/ 内部路径） */
    deleted: '小说已移入回收站',
    /** 编辑小说成功（名称 / 题材 / 简介） */
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
    /** [回退编辑]（保存左侧 icon，最多回退 5 次） */
    undoEdit: '回退编辑',
    undoEditHint: '回退本章编辑（最多 5 次）',
    undoEmpty: '没有可回退的编辑',
    undoDone: '已回退一步编辑',
    /** 选区 AI 改写四按钮与定稿入口已移除（润色=选中文本加入对话） */
    revisions: '修订历史',
    restoreRevision: '回滚到此版本',
    restoreConfirm: '回滚会用该快照覆盖当前正文（当前内容会先存一份快照）。确定继续吗？',
    restored: '已回滚到所选版本',
  },
  volumeChapter: {
    title: '卷章管理',
    /** 顺序不再由大纲派生，改为「插入」直接指定位置 */
    orderHint: '卷章顺序可直接调整：用「插入」指定序号插入，其后各项自动顺延。',
    gotoOutline: '去大纲编辑',
    newVolume: '新建卷',
    newChapter: '新建章',
    volumeTitleLabel: '卷名',
    chapterTitleLabel: '章名',
    /** 弹窗内输入框不要提示词 */
    titlePlaceholder: '',
    summaryLabel: '卷简介',
    deleteVolumeTitle: '删除卷',
    deleteVolumeConfirm:
      '删除卷「{title}」会级联删除其下 {count} 个章节（共约 {words} 字），且不可撤销。确定删除吗？',
    deleteChapterTitle: '删除章',
    deleteChapterConfirm: '确定删除章节「{title}」吗？此操作不可撤销。',
    /** 删空卷的二次确认文案 */
    deleteLastChapterConfirm:
      '「{title}」是本卷最后一章，删除后该卷「{volume}」也会一并删除。此操作不可撤销，确定吗？',
    /** 插入弹窗 */
    insertVolumeTitle: '插入小说卷',
    insertChapterTitle: '插入小说章节',
    insertBeforeVolume: '往前插入小说卷',
    insertAfterVolume: '往后插入小说卷',
    insertBeforeChapter: '往前插入小说章节',
    insertAfterChapter: '往后插入小说章节',
    /** 卷/章序号一律自动推算，序号输入框与相关文案移除 */
    /** 插入卷必须带上第一章（不支持空卷）；标签「第一章章节名」改为「章节名」 */
    firstChapterLabel: '章节名',
    firstChapterRequired: '请填写第一章的章节名（不支持空卷）',
    /** 大纲节点只支持往后插入卷 */
    insertVolumeOutlineAfterOnly: '大纲在最前面，只能往后插入小说卷',
    /** 插入章节必须先定位到某一章 */
    insertChapterNeedsChapter: '请先在目录中打开一个章节，再插入章节',
    volumeInsertedWithChapter: '已在第 {ordinal} 位插入卷「{title}」，第一章「{chapter}」',
    renameDialogTitle: '重命名',
    /** 重命名弹窗同时含卷名（默认填当前卷名 = 不修改） */
    renameVolumeHint: '默认不修改卷名',
    appendedHint: '不填序号则追加到末尾；填序号则插入到该位置（其后各项自动顺延）。',
    noMoveHint: '章节不支持跨卷移动：需换卷请在目标卷下新建同名章并复制正文。',
    volumeCreated: '卷「{title}」已创建',
    chapterCreated: '章「{title}」已创建',
    volumeInserted: '已在第 {ordinal} 位插入卷「{title}」',
    chapterInserted: '已在第 {ordinal} 位插入章「{title}」',
    volumeDeleted: '卷已删除（含 {count} 个章节）',
    chapterDeleted: '章已删除',
    /** 删空卷时的提示 */
    chapterDeletedVolumeGone: '章已删除，该卷已无章节，卷「{volume}」一并删除',
    renamedOk: '已重命名',
    chaptersToBeDeleted: '将被删除的章节',
    /** 编辑模式主页面右上角的卷章操作 */
    editNoTarget: '请先在目录中选择要操作的卷或章节',
    deleteOutlineDenied: '大纲是整本书的创作参考，不能在此删除；如需修改请在大纲编辑器中编辑。',
  },
  outline: {
    title: '大纲',
    /** 大纲是创作参考，保存不再同步卷章顺序 */
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
  /** 回收站（入口在 AI 写作助手左边，最多 20 本，卷/章浏览，回收 or 彻底删除） */
  recycle: {
    title: '回收站',
    tooltip: '回收站：找回被删除的章节（只保留最近删除的 20 本）',
    empty: '回收站是空的',
    back: '返回回收站',
    novelDeletedTag: '整本已删除',
    /** 列表行右末尾小字「X个被删除章节」；二级视图不再显示总数 */
    chaptersUnit: '个被删除章节',
    restore: '回收',
    purge: '删除',
    askTitle: '回收还是删除？',
    askMessage: '《{title}》要回收到小说里，还是从回收站彻底删除？',
    restored: '已回收《{title}》',
    restoredNovel: '整本小说已恢复：《{title}》已回到左边栏（含大纲）',
    purged: '已彻底删除《{title}》',
    loadFailed: '回收站读取失败',
  },
  export: {
    title: '导出小说',
    formatLabel: '正文格式',
    fmtMarkdown: 'Markdown（各章 md 集合）',
    fmtTxt: 'TXT（整本纯文本）',
    fmtEpub: 'EPUB 3',
    fmtPdf: 'PDF',
    includeOutline: '附带大纲原 markdown 文件',
    start: '开始导出',
    pending: '排队中…',
    running: '正在导出，请稍候…',
    progressTitle: '正在导出小说',
    done: '导出完成',
    failed: '导出失败',
    cancelled: '已停止导出',
    stop: '停止导出',
    stopTitle: '停止导出',
    stopConfirm: '确定要停止本次导出吗？已生成的部分将被丢弃。',
    savedToDir: '导出完成，文件已保存到设定的导出地址',
    download: '下载',
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
    /** 新增自动保存开关；间隔标签去掉「（秒）」，提示改文案 */
    autosaveEnabled: '自动保存',
    autosaveInterval: '自动保存间隔',
    autosaveHint: '编辑模式下辅助主动保存，时长单位为秒',
    exportDir: '小说导出地址',
    exportDirPlaceholder: '默认为桌面（Desktop）',
    exportDirHint: '导出产物（zip）保存的文件夹绝对路径，如 D:\\novels；目录不存在会自动创建，非法路径回落桌面',
    saved: '设置已保存',
    resetDefault: '恢复默认设置',
    resetConfirmTitle: '恢复默认设置',
    resetConfirm: '将把主题、字号、字体、自动保存间隔等全部恢复为默认值，确定吗？',
    resetDone: '已恢复默认设置',
    back: '返回',
  },
  aiTools: {
    /** AI 工具 → AI 设置 */
    title: 'AI 设置',
    /** 大模型 API → 大模型 */
    tabLlm: '大模型',
    tabSubagents: '写作子代理',
    tabSkills: '写作技能',
    providerName: '名称',
    /** 类型 → 协议类型（三种） */
    providerType: '协议类型',
    /** API 地址 → Base Url */
    baseUrl: 'Base Url',
    apiKeyEnv: '密钥名',
    apiKeyValue: '密钥API',
    apiKeyValuePlaceholder: '保存后写入系统级环境变量，仅显示掩码',
    apiKeyConfigured: '已配置',
    apiKeyMissing: '未配置密钥',
    /** 添加 Provider → 添加大模型（右上角） */
    addProvider: '添加大模型',
    /** 大模型级常用参数 */
    /** 超时时长 → Time Out */
    timeoutSec: 'Time Out',
    temperature: 'Temperature',
    maxTokens: 'Max Token',
    tempRangeHint: '取值范围 {range}（不包括 0）',
    /** 数值参数通用范围提示（Max Token / Context Length 输入框右侧） */
    valueRangeHint: '取值范围 {range}',
    /** Context Length 必填参数（单位 K，默认 1024K）+ 四项参数悬浮解释 */
    contextLength: 'Context Length',
    /** 名称左侧提示 icon 点击后的说明 */
    nameHint: '模型名称必须和模型真实ID相同，不然模型无法使用',
    timeoutSecHint: '单次模型调用的超时时长（秒），超时将中断请求并按降级链重试',
    temperatureHint: '采样温度，越高回复越发散、越低越保守（不包括 0）',
    maxTokensHint: '单次回复允许生成的最大 token 数（单位 K，1K = 1024 token），超出会被截断',
    contextLengthHint: '模型上下文窗口大小（单位 K，1K = 1024 token）；已注入上下文剩余低于 20% 时自动压缩',
    /** 不再需要「模型 ID」——名称就等于模型 ID */
    maxContext: '上下文窗口',
    isDefault: '设为默认',
    testConnection: '测试连接',
    testOk: '连接成功（{ms} ms）',
    testFail: '连接失败',
    deleteProviderConfirm: '删除「{name}」后其配置与模型将一并移除。确定吗？',
    /** 文案 */
    noProviders: '还没有配置任何大模型。添加后即可与 AI 写作助手对话。',
    savedProvider: '大模型配置已保存',
    deletedProvider: '已删除',
    requiredAll: '所有配置项均为必填',
  },
  subagents: {
    /** 顶部提示改为启用/停用按钮的悬浮说明；更新为 4 个内置子代理口径 */
    enableHint: '停用后主代理不再委派任务。',
    /** （SubAgents(4)）：新建弹窗名称输入框下方的小红字提示 */
    nameDupHint: '尽量不要与内置子代理功能重复',
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
    /** 内置技能 = Agent 基础工具，展示名不带描述 */
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
    edit: '编辑',
    /** 启用开关悬浮说明 + 停用标签（去掉「查看」按钮） */
    enableHint: '停用后该技能不可被调用，也不可被子代理绑定。',
    disabledTag: '已停用',
    /** 自建技能只包含提示词，正文必填 */
    requiredNameDescBody: '名称、描述、技能正文均为必填',
    deleteConfirm: '删除技能「{name}」会同时移除其物化目录。确定吗？',
  },
  agent: {
    /** AI 助手 → AI 写作助手 */
    panelTitle: 'AI写作助手',
    panelTooltip: '来试试AI写作功能吧，它可以实现创作小说、润色大纲、检验剧情、剧情汇总等等功能',
    expand: '展开 AI 写作助手',
    collapse: '收起 AI 会话框',
    /** 右栏可向左拉伸，拉宽后会话框铺满右侧屏幕（中栏自适应收窄） */
    resizeHint: '按住拖动可调整会话框宽度（右边界固定，最多拉到屏幕宽度的 2/5）',
    /** 头部展示当前会话数量 + 当前小说项目 token 使用量（无模型提示已删除） */
    sessionCount: '{n}个会话',
    /** 头部 token 用量带单位（x.yK Token） */
    tokenTotal: '{n} Token',
    /** 头部缓存命中率（输入 token 命中 provider 前缀缓存的比例） */
    cacheHitRate: '缓存命中 {n}%',
    tokenUsage: '{used}K / {window}K',
    tokenUsageOnly: '已用 {used}K tokens',
    sessionLabel: '会话',
    newSession: '新建会话',
    renameSession: '重命名会话',
    deleteSession: '删除会话',
    /** 删除「导出会话」功能 */
    deleteSessionConfirm: '删除会话会同时删除其消息记录（不影响小说内容）。确定吗？',
    /** 空态提示 */
    noNovel: '还没有小说项目哦~',
    noSession: '快点来开启AI写作吧~',
    backToList: '返回会话列表',
    sessionListTitle: '会话记录',
    /** 会话列表中「正在思考执行」的灰色小字标记 */
    sessionRunning: 'AI思考执行中……',
    /** 输入提示文案已全部移除（聊天框任何情况下都不提示） */
    send: '发送',
    stop: '停止',
    /** 底部按键的「追加消息」形态（运行中且已输入内容） */
    appendMessage: '追加消息',
    running: 'AI 正在思考…',
    /** 运行中发送消息的三种模式（模仿主流大模型 / AI Agent） */
    sendAnyway: '停止后发送',
    steer: '添加为引导',
    queue: '添加到队列',
    /** 三种模式的悬浮说明 */
    sendAnywayHint: '立即停止当前工作，转而处理这条新消息',
    steerHint: '不打断当前工作，在下一步执行前插入这条消息作为引导',
    queueHint: '等当前工作处理完，再自动处理这条消息',
    steered: '已添加为引导，AI 会在下一步执行前看到它',
    queued: '已添加到队列，当前回答结束后自动发送',
    /** 排队消息可编辑 / 删除 / 立即发送 */
    queueCount: '队列中 {n} 条待发送',
    queueClear: '清空',
    queueSendNow: '立即发送',
    /** 报错卡片 */
    errorFoot: '本次回复已中断',
    errorDismiss: '知道了',
    /** 删除[重新生成]功能 */
    copyMessage: '复制',
    usage: '用量：入 {in} / 出 {out}',
    planTitle: '计划',
    workflowStage: '流程阶段',
    subagentLane: '子代理',
    fileChanged: 'AI 修改了文件：{path}',
    approvalTitle: '需要你审批',
    approvalRisk: '高危操作',
    approvalTool: '工具',
    approvalArgs: '参数',
    approvalReason: '原因',
    approve: '批准',
    approveSession: '本次会话允许',
    attachmentStoredHint:
      '「{label}」随本轮消息送给 AI；内容较大时先存为工作区文件、只注入摘要与路径，AI 需要全文时自行读取（节省 token）',
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
    /** 输入框内的 + / 工具 / Model */
    addContext: '添加内容',
    ctxOutline: '大纲',
    ctxLocalFile: '本地文件',
    /** 添加文件页（小说大纲或章节 / 本地文件 / 最近打开的章节） */
    ctxNovelContent: '小说项目大纲或章节',
    pickerSearchPlaceholder: '搜索小说大纲或章节…',
    pickerNoMatch: '没有匹配的大纲或章节',
    /** 小说项目二级目录为空时的居中提示 */
    pickerNoNovels: '还没有小说项目',
    pickerRecent: '最近打开的章节',
    tools: '工具',
    /** 工具弹窗（写作子代理 / 写作技能） */
    toolsWritingSubagents: '写作子代理',
    toolsWritingSkills: '写作技能',
    model: 'Model',
    modelOff: 'Model Off',
    contextUsage: '上下文使用情况',
    /** （1.4）：Model Off 右侧「剩余上下文百分比」= 1 − 已占用token ÷ 窗口总容量 */
    contextRemaining: '{n}%',
    /** 未配置大模型时的提示（只在模型弹窗内出现；会话框上方不再提示） */
    noModelsHint: '尚未配置大模型',
  },
  health: {
    backendOffline: '后端未连接（请确认 uvicorn 已在 127.0.0.1:8000 运行）',
    fts5Degraded: 'FTS5 不可用，检索已降级为 LIKE 模糊匹配',
  },
  /** HTTP 层错误的简短中文文案（替代 axios 的英文 error.message） */
  httpError: {
    badRequest: '请求不正确，请检查后重试',
    unauthorized: '登录已失效，请重新登录后再试',
    forbidden: '没有权限执行该操作',
    notFound: '请求的内容不存在或已被删除',
    conflict: '内容与最新状态冲突，请刷新后重试',
    serverError: '服务暂时不可用，请稍后重试',
    timeout: '请求超时，请检查网络后重试',
    unknown: '请求失败，请稍后重试',
    /** 后端路由缺失被 SPA 回落成页面（200 + HTML）时的提示 */
    htmlResponse: '后端接口未就绪（返回了页面而非数据）：后端可能是旧版本，请重启后端服务后重试',
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
