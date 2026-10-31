/**
 * Agent 运行报错的中文提示（v1030续 需求「Agent运行报错」）。
 *
 * 大模型或 Agent 在运行中出错时（连接不通、API 失效、额度不足等），
 * 需要给用户**中文提示 + 应该怎么做**，界面以灰色（异常不可用）呈现。
 *
 * 输入是后端 SSE `error` 事件或 HTTP 错误里的 `{code, message}`；
 * 这里按错误码归类，码不可识别时再按关键词兜底，最后回落到原始 message。
 */
import { ErrorCode } from '@/types'

/** 一条面向用户的错误说明 */
export interface AgentErrorInfo {
  /** 出了什么问题（中文，一句话） */
  title: string
  /** 用户应该怎么做（中文，可操作） */
  advice: string
}

/** 关键词兜底：后端 message 里出现这些词时归类（大小写不敏感） */
const KEYWORD_RULES: { test: RegExp; info: AgentErrorInfo }[] = [
  {
    test: /(api\s*key|apikey|密钥|unauthoriz|401|invalid[_\s-]?key|authentication)/i,
    info: {
      title: 'API 密钥无效或已过期，大模型拒绝了本次请求',
      advice: '请到「AI 设置 → 大模型配置」重新填写密钥；若密钥来自环境变量，请确认变量名与取值已更新后重启后端。',
    },
  },
  {
    test: /(connect|connection|timeout|timed out|refused|econnrefused|网络|超时|无法连接|dns)/i,
    info: {
      title: '无法连接到大模型服务（网络不通或请求超时）',
      advice: '请检查本机网络与代理设置，确认能访问该模型的服务地址；必要时到「大模型配置」调大 Timeout 后重试。',
    },
  },
  {
    test: /(rate\s*limit|429|too many requests|quota|额度|限流|insufficient|balance|余额)/i,
    info: {
      title: '模型服务限流或额度不足，本次请求未被受理',
      advice: '请稍等片刻后重试；若提示额度/余额不足，请到模型服务商后台充值，或切换到其它可用模型。',
    },
  },
  {
    test: /(model\s*not\s*found|does not exist|no such model|404|模型不存在|unsupported model)/i,
    info: {
      title: '所选模型不存在或已下线',
      advice: '请在会话框底部的 Model 里改选一个可用模型，或到「大模型配置」修正模型名称。',
    },
  },
  {
    test: /(context|token|长度|超出|exceed|too long|max_tokens|窗口)/i,
    info: {
      title: '本次上下文超出模型可处理的长度',
      advice: '请减少一次添加的文件/章节数量，或改用「新建会话」重新开始；也可切换到上下文窗口更大的模型。',
    },
  },
  {
    test: /(cancel|取消|abort)/i,
    info: {
      title: '本次运行已被取消',
      advice: '如需继续，请重新发送你的问题。',
    },
  },
]

/** 按错误码给出说明（覆盖后端已知错误码） */
const CODE_RULES: Partial<Record<number, AgentErrorInfo>> = {
  [ErrorCode.LLM_PROVIDER_FAILED]: {
    title: '大模型调用失败（服务返回错误）',
    advice: '请到「AI 设置 → 大模型配置」检查密钥、服务地址与模型名是否正确，确认额度充足后重试；也可换一个模型再试。',
  },
  [ErrorCode.DEPENDENCY_UNAVAILABLE]: {
    title: '依赖服务不可用',
    advice: '请确认相关外部服务已启动/已安装（如 MCP 服务、calibre），然后重试。',
  },
  [ErrorCode.STREAM_BUFFER_LOST]: {
    title: '实时消息流已断开，回复可能不完整',
    advice: '已自动重新拉取会话记录；如仍看不到最新回复，请刷新页面后重试。',
  },
  [ErrorCode.RUN_STATE_CONFLICT]: {
    title: '该运行已结束，无法继续操作',
    advice: '请重新发送消息以开始新的一轮对话。',
  },
  [ErrorCode.VERSION_CONFLICT]: {
    title: '内容已被其它端修改（版本冲突）',
    advice: '请在冲突提示条中选择保留本地修改或载入 AI 版本，再重新发送。',
  },
  [ErrorCode.NOT_FOUND]: {
    title: '要访问的会话或章节不存在',
    advice: '请返回会话列表重新选择；若刚删除过内容，请刷新页面。',
  },
  [ErrorCode.FORBIDDEN]: {
    title: '该操作被拒绝（超出允许范围）',
    advice: '请调整请求内容后重试；涉及写入高危操作时，请在审批卡片中选择批准。',
  },
  [ErrorCode.BAD_REQUEST]: {
    title: '请求参数不正确',
    advice: '请检查输入内容后重新发送。',
  },
  [ErrorCode.INTERNAL_ERROR]: {
    title: '服务端内部错误，AI 未能完成本次请求',
    advice: '请稍后重试；若持续失败，请查看 data/logs/error-*.jsonl 日志或重启后端服务。',
  },
}

/**
 * 把 `{code, message}` 归一成「中文提示 + 处置建议」。
 * @param code 后端错误码（可为 null）
 * @param message 后端原始错误文案（可为空）
 *
 * ★v1031：会话里只给**简短中文**提示——原始文案是英文（或未识别的外文）时不再拼接展示，
 * 避免把大模型 / 三方库的英文异常直接抛给用户。
 */
export function describeAgentError(code?: number | null, message?: string | null): AgentErrorInfo {
  const raw = (message ?? '').trim()
  const byCode = code != null ? CODE_RULES[code] : undefined
  if (byCode) return withDetail(byCode, raw)

  for (const rule of KEYWORD_RULES) {
    if (rule.test.test(raw)) return withDetail(rule.info, raw)
  }

  return {
    // 认不出来的错误一律给统一中文文案，不回显英文原文
    title: 'AI 运行出错，未能返回结果',
    advice: '请稍后重试；若反复失败，请到「AI 设置」检查大模型配置（密钥 / 地址 / 模型名）后再试。',
  }
}

/** 是否含中文（判定原始文案能否直接展示给用户） */
function hasChinese(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text)
}

/**
 * 保留后端原始文案作为补充细节，便于排查。
 * ★v1031：只在原文是**中文且足够短**时拼接；英文原文丢弃（提示仍为纯中文）。
 */
function withDetail(info: AgentErrorInfo, raw: string): AgentErrorInfo {
  if (!raw || info.title.includes(raw)) return info
  if (!hasChinese(raw)) return info
  const short = raw.length > 60 ? `${raw.slice(0, 60)}…` : raw
  return { title: `${info.title}（${short}）`, advice: info.advice }
}
