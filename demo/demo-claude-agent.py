'''
AI Agent Demo - Claude Agent SDK 实现

重点介绍如何用 claude-agent-sdk 快速实现 AI Agent。
核心概念：
- create_sdk_mcp_server：在进程内注册 MCP 工具服务器，无需外部进程
- @tool 装饰器：声明工具的名称、描述和参数 Schema，返回 MCP 标准结构
- ClaudeSDKClient：内置 Agent-Loop，自动处理 思考 → 行动 → 反馈 循环
- receive_response()：流式接收一轮对话的消息，直到 ResultMessage 结束
'''

import os
import json
import anyio
from claude_agent_sdk import (
    tool,
    create_sdk_mcp_server,
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    ToolUseBlock,
)

# ============================================================
# 1. 检验本地环境变量中是否设置了 ANTHROPIC_API_KEY（大模型会自行读取）
# ============================================================
if not os.environ.get("ANTHROPIC_API_KEY"):
    raise ValueError(
        "环境变量 ANTHROPIC_API_KEY 未设置，请先设置 API Key"
        "（使用兼容网关时还需设置 ANTHROPIC_BASE_URL）"
    )

# ============================================================
# 2. MCP工具定义 - 使用 @tool 装饰器，声明工具的名称、描述和参数 Schema
# ============================================================
@tool(
    name="get_weather",
    description="查询指定城市的天气信息",
    input_schema={
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，如：北京、上海",
            }
        },
        "required": ["city"],
    },
)
async def get_weather(args: dict) -> dict:
    """Mock天气查询"""
    city = args.get("city", "")
    mock_data = {
        "北京": {"天气": "晴", "温度": "25°C", "湿度": "40%", "风力": "微风"},
        "上海": {"天气": "多云", "温度": "22°C", "湿度": "65%", "风力": "东南风3级"},
        "广州": {"天气": "小雨", "温度": "28°C", "湿度": "80%", "风力": "南风2级"},
        "深圳": {"天气": "阴", "温度": "26°C", "湿度": "70%", "风力": "东风2级"},
    }

    data = mock_data.get(city, {"天气": "未知", "温度": "N/A", "湿度": "N/A", "风力": "N/A"})
    result = {"城市": city, **data}
    print(f"[调用工具] get_weather({city!r})")
    print(f"[工具结果] {json.dumps(result, ensure_ascii=False)}")
    return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}


weather_server = create_sdk_mcp_server(
    name="demo-tools",
    version="1.0.0",
    tools=[get_weather],
)

# ============================================================
# 3. 创建AI Agent和定义返回函数
# ============================================================
options = ClaudeAgentOptions(
    system_prompt="你是一个智能助手，可以使用工具来帮助用户完成任务。请用中文回答。",
    mcp_servers={"demo": weather_server},
    tools=[],
    allowed_tools=["mcp__demo__get_weather"],
    permission_mode="bypassPermissions",
    max_turns=5,
)

async def chat(client: ClaudeSDKClient, user_input: str) -> str:
    await client.query(user_input)

    final_text_parts = []
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[调用工具] {block.name}({block.input})")
        elif isinstance(message, ResultMessage):
            if message.result:
                final_text_parts.append(message.result)
            elif message.is_error:
                final_text_parts.append(f"（执行出错：{message.subtype}）")

    return "\n".join(final_text_parts) if final_text_parts else "（本轮没有返回内容）"

# ============================================================
# 4. Agent 启动
# ============================================================
async def main():
    print("\n" + "=" * 50)
    print("AI Agent Demo - Claude Agent SDK 版")
    print("已注册工具: get_weather (Mock, 进程内 MCP)")
    print("输入 'exit' 或 'quit' 退出")
    print("=" * 50 + "\n")

    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = input("USER: ").strip()

                if user_input.lower() in ["quit", "exit", "q", "byebye", "拜拜", "再见"]:
                    print("拜拜！祝您有美好的一天！")
                    break

                if not user_input:
                    print("请输入有效指令或问题。")
                    continue

                response = await chat(client, user_input)
                print(f"\nAI: {response}\n")

            except KeyboardInterrupt:
                print("\n再见！")
                break
            except Exception as e:
                print(f"\n发生错误: {e}\n")


if __name__ == "__main__":
    anyio.run(main)
