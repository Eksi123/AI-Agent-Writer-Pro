'''
AI Agent Demo - Deep Agents SDK 实现

重点介绍如何用 deepagents SDK 快速实现 AI Agent 。
核心概念：
- create_deep_agent：一行代码创建 Agent，内置 Agent-Loop
- @tool 装饰器：自动注册工具，无需手写 Schema
- agent.invoke()：自动处理 思考 → 行动 → 反馈 循环
'''

import os
import json
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from deepagents import create_deep_agent

# ============================================================
# 1. 大模型配置
# ============================================================
api_key = os.environ.get("LLM_API_KEY")
if not api_key:
    raise ValueError("环境变量 LLM_API_KEY 未设置")

llm = ChatOpenAI(
    model="qwen3.7-plus",
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=30,
    max_tokens=512,
)

# ============================================================
# 2. 工具定义 - 使用 @tool 装饰器，自动注册
# ============================================================
@tool
def get_weather(city: str) -> str:
    """查询指定城市的天气信息。

    Args:
        city: 城市名称，如：北京、上海、广州、深圳
    """
    mock_data = {
        "北京": {"天气": "晴", "温度": "25°C", "湿度": "40%", "风力": "微风"},
        "上海": {"天气": "多云", "温度": "22°C", "湿度": "65%", "风力": "东南风3级"},
        "广州": {"天气": "小雨", "温度": "28°C", "湿度": "80%", "风力": "南风2级"},
        "深圳": {"天气": "阴", "温度": "26°C", "湿度": "70%", "风力": "东风2级"},
    }

    data = mock_data.get(city, {"天气": "未知", "温度": "N/A", "湿度": "N/A", "风力": "N/A"})
    result = {"城市": city, **data}
    return json.dumps(result, ensure_ascii=False)

# ============================================================
# 3. 创建 AI Agent
# ============================================================
agent = create_deep_agent(
    model=llm,
    tools=[get_weather],
    system_prompt="你是一个智能助手，可以使用工具来帮助用户完成任务。请用中文回答。",
)

# ============================================================
# 4. Agent 交互循环
# ============================================================
def chat(user_input: str) -> str:
    """与 Agent 对话，自动处理工具调用循环"""
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_input}]}
    )
    final_message = result["messages"][-1]  # 提取最后一条 AI 消息作为回复
    return final_message.content


# ============================================================
# Agent 启动
# ============================================================
if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("AI Agent Demo - Deep Agents SDK 版")
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
