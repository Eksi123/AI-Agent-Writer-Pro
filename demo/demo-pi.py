'''
AI Agent Demo - pi-agent-core SDK 实现

重点介绍如何用 pi-agent-core SDK 实现 AI Agent。
核心概念：
- Agent：有状态的 Agent 类，管理对话历史和工具执行
- AgentTool：手动定义工具，需要显式编写 JSON Schema
- agent.prompt()：异步调用，通过事件订阅处理 Agent-Loop
- stream_anthropic：使用 Anthropic 协议调用大模型

与 deepagents 的差异：
- deepagents 使用 OpenAI 协议，pi-agent-core 使用 Anthropic 协议
- deepagents 用 @tool 装饰器自动注册，pi-agent-core 需手动定义 schema
- deepagents 用 invoke() 同步调用，pi-agent-core 用 prompt() 异步调用 + 事件订阅
'''

import asyncio
import json
import os
from typing import Any

import anthropic
from pi_agent_core import (
    Agent,
    AgentOptions,
    AgentTool,
    AgentToolResult,
    AgentToolSchema,
    AssistantMessage,
    Model,
    TextContent,
)
from pi_agent_core.anthropic import _build_request, _map_event


# ============================================================
# 1. 大模型配置
# ============================================================
api_key = os.environ.get("LLM_API_KEY")
if not api_key:
    raise ValueError("环境变量 LLM_API_KEY 未设置")

# 设置 DashScope 的 Anthropic 兼容接口地址
# 可通过环境变量覆盖，指向其他 Anthropic 兼容服务
os.environ.setdefault(
    "ANTHROPIC_BASE_URL",
    "https://dashscope.aliyuncs.com/apps/anthropic",
)

MODEL = "qwen3.7-plus"
SYSTEM_PROMPT = "你是一个智能助手，可以使用工具来帮助用户完成任务。请用中文回答。"


# ============================================================
# 自定义 stream_fn，支持超时配置
# ============================================================
async def stream_anthropic_with_timeout(model, context, options):
    """
    自定义的 stream_fn，包装 pi-agent-core 的 stream_anthropic，
    增加超时时间以适配 DashScope 等响应较慢的服务。
    """
    # 创建带超时配置的 Anthropic 客户端
    api_key = options.api_key or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    
    client_kwargs = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": 30.0,  # 30秒超时
    }
    
    client = anthropic.AsyncAnthropic(**client_kwargs)
    
    # 构建请求参数
    request = _build_request(model, context, options)
    
    # 使用队列和事件来管理流式响应
    queue = asyncio.Queue()
    done = asyncio.Event()
    state = {"final": None, "task": None}
    
    partial = AssistantMessage(api=model.api, provider=model.provider, model=model.id)
    block_types = {}
    tool_json = {}
    
    async def events_iter():
        while True:
            item = await queue.get()
            if item is None:
                return
            yield item
    
    async def result():
        await done.wait()
        final = state["final"]
        if final is None:
            raise RuntimeError("No result available")
        return final
    
    async def _pump():
        nonlocal partial
        try:
            async with client.messages.stream(**request) as raw_stream:
                async for event in raw_stream:
                    if options.cancel_event and options.cancel_event.is_set():
                        raise RuntimeError("Request aborted by user")
                    mapped = _map_event(event, partial, block_types, tool_json)
                    if mapped is not None:
                        await queue.put(mapped)
            
            if options.cancel_event and options.cancel_event.is_set():
                raise RuntimeError("Request aborted by user")
            
            state["final"] = partial
        except Exception as error:
            reason = "aborted" if (options.cancel_event and options.cancel_event.is_set()) else "error"
            from pi_agent_core import StreamErrorEvent
            partial.stop_reason = reason
            partial.error_message = str(error)
            await queue.put(StreamErrorEvent(reason=reason, error=partial))
            state["final"] = partial
        finally:
            done.set()
            await queue.put(None)
    
    state["task"] = asyncio.create_task(_pump())
    return {"events": events_iter(), "result": result}


# ============================================================
# 2. 工具定义 - 手动定义 AgentTool 和 JSON Schema
# ============================================================
async def get_weather(
    tool_call_id: str,
    params: dict[str, Any],
    cancel_event: asyncio.Event | None = None,
    on_update: Any = None,
) -> AgentToolResult:
    """
    查询指定城市的天气信息（Mock 实现）。
    
    pi-agent-core 的工具函数签名：
    - tool_call_id: 工具调用的唯一标识
    - params: 工具参数（从 JSON 解析而来）
    - cancel_event: 取消事件，用于中断长时间运行的工具
    - on_update: 更新回调，用于流式返回工具执行进度
    """
    # 检查是否已取消
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("天气查询已取消")
    
    # 提取参数
    city = params["city"]
    
    # Mock 数据
    mock_data = {
        "北京": {"天气": "晴", "温度": "25°C", "湿度": "40%", "风力": "微风"},
        "上海": {"天气": "多云", "温度": "22°C", "湿度": "65%", "风力": "东南风3级"},
        "广州": {"天气": "小雨", "温度": "28°C", "湿度": "80%", "风力": "南风2级"},
        "深圳": {"天气": "阴", "温度": "26°C", "湿度": "70%", "风力": "东风2级"},
    }

    data = mock_data.get(city, {"天气": "未知", "温度": "N/A", "湿度": "N/A", "风力": "N/A"})
    result = {"城市": city, **data}
    
    # 返回 AgentToolResult，内容必须是 TextContent 或 ImageContent 列表
    return AgentToolResult(
        content=[TextContent(text=json.dumps(result, ensure_ascii=False))],
    )


# 创建工具定义，需要显式指定 JSON Schema
weather_tool = AgentTool(
    name="get_weather",
    description="查询指定城市的天气信息",
    label="查询天气",
    parameters=AgentToolSchema(
        properties={
            "city": {
                "type": "string",
                "description": "城市名称，如：北京、上海、广州、深圳",
            }
        },
        required=["city"],
    ),
    execute=get_weather,  # 绑定执行函数
)


# ============================================================
# 3. 创建 AI Agent
# ============================================================
# 创建 Agent 实例，配置 stream_fn 和 API key
agent = Agent(
    AgentOptions(
        stream_fn=stream_anthropic_with_timeout,  # 使用自定义的流式调用，支持超时配置
        get_api_key=lambda provider: api_key if provider == "anthropic" else None,
    )
)

# 设置系统提示词
agent.set_system_prompt(SYSTEM_PROMPT)

# 设置模型配置
agent.set_model(
    Model(
        api="anthropic-messages",  # API 类型
        provider="anthropic",      # 提供商
        id=MODEL,                  # 模型 ID
    )
)

# 注册工具列表
agent.set_tools([weather_tool])


# ============================================================
# 4. Agent 交互循环
# ============================================================
async def chat(user_input: str) -> str:
    """
    与 Agent 对话，自动处理工具调用循环。
    
    pi-agent-core 的调用方式：
    1. 订阅 Agent 事件（工具调用、消息更新等）
    2. 调用 agent.prompt() 发送用户输入
    3. Agent 自动处理 思考 → 工具调用 → 反馈 循环
    4. 通过事件回调收集最终回答
    """
    responses: list[str] = []

    # 事件处理回调
    def handle_event(event: Any) -> None:
        # 工具开始执行
        if event.type == "tool_execution_start":
            print(f"\n[调用工具] {event.tool_name}({event.args})")
        
        # 工具执行完成
        elif event.type == "tool_execution_end":
            result_text = "".join(
                block.text
                for block in event.result.content
                if isinstance(block, TextContent)
            )
            print(f"[工具结果] {result_text}")
        
        # 消息结束（Assistant 回复完成）
        elif event.type == "message_end" and isinstance(event.message, AssistantMessage):
            message = event.message
            # 检查是否有错误
            if message.stop_reason == "error":
                raise RuntimeError(message.error_message or "Agent 调用失败")
            # 如果不是工具调用，则是最终回答
            if message.stop_reason != "toolUse":
                responses.append(
                    "".join(
                        block.text
                        for block in message.content
                        if isinstance(block, TextContent)
                    )
                )

    # 订阅事件，返回取消订阅函数
    unsubscribe = agent.subscribe(handle_event)
    try:
        # 发送用户输入，Agent 自动处理 Agent-Loop
        await agent.prompt(user_input)
    finally:
        unsubscribe()

    # 检查是否有错误
    if agent.state.error:
        raise RuntimeError(agent.state.error)
    
    # 返回最后一条回答
    return responses[-1] if responses else ""


# ============================================================
# Agent 启动
# ============================================================
async def main() -> None:
    """主循环：读取用户输入，调用 Agent，输出回答"""
    print("\n" + "=" * 50)
    print("AI Agent Demo - pi-agent-core SDK 版")
    print("已注册工具: get_weather (Mock)")
    print("输入 'exit' 或 'quit' 退出")
    print("=" * 50 + "\n")

    while True:
        try:
            user_input = input("USER: ").strip()

            # 退出命令
            if user_input.lower() in ["quit", "exit", "q", "byebye", "拜拜", "再见"]:
                print("拜拜！祝您有美好的一天！")
                break

            # 空输入检查
            if not user_input:
                print("请输入有效指令或问题。")
                continue

            # 调用 Agent
            response = await chat(user_input)
            print(f"\nAI: {response}\n")

        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as error:
            print(f"\n发生错误: {error}\n")


if __name__ == "__main__":
    asyncio.run(main())