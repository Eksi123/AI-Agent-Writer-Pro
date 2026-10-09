'''
AI Agent Demo - OpenAI SDK 实现

重点介绍如何用 OpenAI SDK 实现 AI Agent。
核心概念：
- OpenAI Client：使用 OpenAI SDK 调用兼容 API
- Function Calling：OpenAI 的工具调用能力，支持函数定义和自动执行
- Agent-Loop：手动实现 思考 → 行动 → 反馈 循环
'''

import os
import json
from openai import OpenAI

# ============================================================
# 1. 大模型配置
# ============================================================
api_key = os.environ.get("LLM_API_KEY")
if not api_key:
    raise ValueError("环境变量 LLM_API_KEY 未设置")

# 使用 DashScope 的 OpenAI 兼容接口
client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=30.0,
)

MODEL = "qwen3.7-plus"


# ============================================================
# 2. 工具定义 - OpenAI Function Calling 格式
# ============================================================
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市的天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，如：北京、上海、广州、深圳"
                    }
                },
                "required": ["city"]
            }
        }
    }
]


def get_weather(city: str) -> str:
    """Mock 天气查询工具"""
    mock_data = {
        "北京": {"天气": "晴", "温度": "25°C", "湿度": "40%", "风力": "微风"},
        "上海": {"天气": "多云", "温度": "22°C", "湿度": "65%", "风力": "东南风3级"},
        "广州": {"天气": "小雨", "温度": "28°C", "湿度": "80%", "风力": "南风2级"},
        "深圳": {"天气": "阴", "温度": "26°C", "湿度": "70%", "风力": "东风2级"},
    }
    
    data = mock_data.get(city, {"天气": "未知", "温度": "N/A", "湿度": "N/A", "风力": "N/A"})
    result = {"城市": city, **data}
    return json.dumps(result, ensure_ascii=False)


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """执行工具调用"""
    if tool_name == "get_weather":
        return get_weather(**tool_input)
    else:
        return f"错误：未知工具 {tool_name}"


# ============================================================
# 3. Agent-Loop 实现
# ============================================================
def chat(user_input: str, max_iterations: int = 5) -> str:
    """
    与 Agent 对话，实现 Agent-Loop
    
    Agent-Loop 流程：
    1. 发送消息给大模型
    2. 检查响应中是否有 tool_calls
    3. 如果有，执行工具并将结果反馈给大模型
    4. 重复直到大模型给出最终回答
    """
    messages = [
        {
            "role": "system",
            "content": "你是一个智能助手，可以使用工具来帮助用户完成任务。请用中文回答。"
        },
        {
            "role": "user",
            "content": user_input
        }
    ]
    
    for iteration in range(max_iterations):
        # 调用 OpenAI 兼容 API
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            max_tokens=512,
        )
        
        # 提取 assistant 消息
        assistant_message = response.choices[0].message
        
        # 检查是否有工具调用
        if not assistant_message.tool_calls:
            # 没有工具调用，返回最终文本响应
            return assistant_message.content or ""
        
        # 有工具调用，执行工具并构建响应消息
        # 1. 添加 assistant 的响应（包含 tool_calls）
        messages.append(assistant_message.model_dump())
        
        # 2. 执行所有工具并收集结果
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            tool_input = json.loads(tool_call.function.arguments)
            
            print(f"[调用工具] {tool_name}({tool_input})")
            result = execute_tool(tool_name, tool_input)
            print(f"[工具结果] {result}")
            
            # 3. 添加工具结果到消息列表
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result
            })
    
    return "抱歉，处理过程超出循环次数，请重新尝试。"


# ============================================================
# Agent 启动
# ============================================================
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("AI Agent Demo - OpenAI SDK 版")
    print("已注册工具: get_weather (Mock)")
    print("输入 'exit' 或 'quit' 退出")
    print("=" * 50 + "\n")
    
    while True:
        try:
            user_input = input("USER: ").strip()
            
            if user_input.lower() in ["quit", "exit", "q", "byebye", "拜拜", "再见"]:
                print("拜拜！祝您有美好的一天！")
                break
            
            if not user_input:
                print("请输入有效指令或问题。")
                continue
            
            response = chat(user_input)
            print(f"\nAI: {response}\n")
            
        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"\n发生错误: {e}\n")
