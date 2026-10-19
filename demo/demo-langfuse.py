'''
AI Agent 可观测性 Demo - Langfuse 集成

演示如何用 Langfuse 监控 AI Agent 的运行过程。

Langfuse 核心能力：
- Traces：追踪完整的请求链路
- Spans：记录子操作步骤
- Generations：专门追踪 LLM 调用（含 token 用量）
- Scores：对输出质量进行评分
- Sessions：关联多轮对话

环境变量配置（需要先创建 Langfuse 账号获取）：
- LANGFUSE_PUBLIC_KEY：Langfuse 公钥
- LANGFUSE_SECRET_KEY：Langfuse 私钥
- LANGFUSE_HOST：Langfuse 服务地址（默认 https://cloud.langfuse.com）
- LLM_API_KEY：大模型 API Key
'''

import os
import json
import time
from langfuse import Langfuse
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from deepagents import create_deep_agent

# ============================================================
# 1. 初始化配置
# ============================================================
api_key = os.environ.get("LLM_API_KEY")
if not api_key:
    raise ValueError("环境变量 LLM_API_KEY 未设置")

langfuse_public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
langfuse_secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
langfuse_host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

if not langfuse_public_key or not langfuse_secret_key:
    print("⚠️  警告: LANGFUSE_PUBLIC_KEY 或 LANGFUSE_SECRET_KEY 未设置")
    print("   Langfuse 追踪功能将不可用，但 Agent 仍可正常运行\n")
    langfuse = None
else:
    langfuse = Langfuse(
        public_key=langfuse_public_key,
        secret_key=langfuse_secret_key,
        host=langfuse_host,
    )
    print("✓ Langfuse 已初始化")

# ============================================================
# 2. 大模型配置
# ============================================================
llm = ChatOpenAI(
    model="qwen3.7-plus",
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    timeout=30,
    max_tokens=512,
)

# ============================================================
# 3. 工具定义
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


@tool
def calculate(expression: str) -> str:
    """计算数学表达式。

    Args:
        expression: 数学表达式，如：2+3*4、100/5
    """
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return json.dumps({"表达式": expression, "结果": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"表达式": expression, "错误": str(e)}, ensure_ascii=False)


# ============================================================
# 4. 创建 AI Agent
# ============================================================
agent = create_deep_agent(
    model=llm,
    tools=[get_weather, calculate],
    system_prompt="你是一个智能助手，可以使用工具来帮助用户完成任务。请用中文回答。",
)

# ============================================================
# 5. Langfuse 监控封装
# ============================================================
def chat_with_tracing(user_input: str, session_id: str = None) -> str:
    """
    与 Agent 对话，并使用 Langfuse 追踪完整链路。

    Langfuse 追踪层次：
    └── Trace（一次完整的用户交互）
        ├── Span: Agent 调用
        │   ├── Generation: LLM 思考 + 工具选择
        │   ├── Span: 工具执行
        │   └── Generation: 最终回复生成
        └── Score: 质量评分
    """
    if not langfuse:
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        return result["messages"][-1].content

    # 创建 Trace - 代表一次完整的用户请求
    trace = langfuse.trace(
        name="agent-chat",
        metadata={"agent_type": "deepagents", "model": "qwen3.7-plus"},
        session_id=session_id,
        user_id="demo-user",
        input=user_input,
    )

    try:
        # Span: 记录 Agent 调用过程
        agent_span = trace.span(
            name="agent-invoke",
            metadata={"tools": ["get_weather", "calculate"]},
        )

        start_time = time.time()
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        duration = time.time() - start_time

        # 提取响应
        messages = result["messages"]
        final_message = messages[-1]
        response = final_message.content

        # 统计工具调用
        tool_calls = [m for m in messages if hasattr(m, "tool_calls") and m.tool_calls]

        agent_span.end(
            output=response,
            metadata={
                "message_count": len(messages),
                "tool_call_rounds": len(tool_calls),
                "duration_seconds": round(duration, 2),
            },
        )

        # 更新 Trace
        trace.update(
            metadata={
                "total_messages": len(messages),
                "tool_calls_made": sum(len(m.tool_calls) for m in tool_calls),
            },
            output=response,
        )

        # Score: 对响应质量进行评分（示例）
        if len(response) > 50:
            trace.score(name="response_quality", value=1.0, comment="响应内容充实")
        elif len(response) > 20:
            trace.score(name="response_quality", value=0.7, comment="响应长度适中")
        else:
            trace.score(name="response_quality", value=0.4, comment="响应较短")

        return response

    except Exception as e:
        trace.update(status="ERROR", metadata={"error": str(e)})
        raise


# ============================================================
# 6. 高级功能演示
# ============================================================
def demo_advanced_features():
    """演示 Langfuse 高级功能"""

    if not langfuse:
        print("⚠️  Langfuse 未配置，跳过高级功能演示")
        return

    print("\n" + "=" * 50)
    print("Langfuse 高级功能演示")
    print("=" * 50)

    # --- 功能 1: Session 追踪（多轮对话关联）---
    print("\n📍 功能 1: Session 追踪（多轮对话）")
    session_id = f"session-{int(time.time())}"

    queries = [
        "北京今天天气怎么样？",
        "那上海呢？",
        "帮我算一下 25 * 37 等于多少",
    ]

    for i, query in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] 用户: {query}")
        response = chat_with_tracing(query, session_id=session_id)
        preview = response[:80] + "..." if len(response) > 80 else response
        print(f"       AI: {preview}")

    print(f"  ✓ Session '{session_id}' 已记录，可在 Langfuse 查看完整对话链")

    # --- 功能 2: 自定义多维度 Score ---
    print("\n📍 功能 2: 多维度评分")
    trace = langfuse.trace(name="scoring-demo", input="测试评分功能")
    trace.score(name="准确性", value=0.95, comment="回答准确无误")
    trace.score(name="完整性", value=0.85, comment="涵盖了主要信息")
    trace.score(name="响应速度", value=0.9, comment="响应及时")
    trace.end(output="评分演示完成")
    print("  ✓ 多维度评分已记录")

    # --- 功能 3: Generation（专门追踪 LLM 调用）---
    print("\n📍 功能 3: Generation 追踪（LLM 调用详情）")
    trace = langfuse.trace(name="generation-demo", input="直接追踪 LLM 调用")

    generation = trace.generation(
        name="qwen-call",
        model="qwen3.7-plus",
        input=[{"role": "user", "content": "你好"}],
        metadata={"temperature": 0.7},
    )
    generation.end(
        output=[{"role": "assistant", "content": "你好！有什么可以帮助你的吗？"}],
        usage={"input_tokens": 10, "output_tokens": 25, "total_tokens": 35},
    )
    print("  ✓ Generation 已记录，包含 token 用量详情")

    # --- 功能 4: 嵌套 Span（复杂工作流）---
    print("\n📍 功能 4: 嵌套 Span（复杂工作流）")
    trace = langfuse.trace(name="workflow-demo", input="复杂任务处理")

    parent = trace.span(name="task-orchestrator")

    child1 = parent.span(name="step-1-parse")
    time.sleep(0.1)
    child1.end(output="解析完成")

    child2 = parent.span(name="step-2-execute")
    time.sleep(0.1)
    child2.end(output="执行完成")

    child3 = parent.span(name="step-3-validate")
    time.sleep(0.1)
    child3.end(output="验证完成")

    parent.end(output="任务完成")
    print("  ✓ 嵌套 Span 已记录，展示任务编排流程")

    # 刷新确保数据发送
    langfuse.flush()

    print("\n" + "=" * 50)
    print("演示完成！请登录 Langfuse 控制台查看详细追踪信息")
    print(f"地址: {langfuse_host}")
    print("=" * 50)


# ============================================================
# 7. 主程序
# ============================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AI Agent Demo - Langfuse 可观测性集成")
    print("=" * 60)
    print("已注册工具: get_weather, calculate (Mock)")
    print("追踪平台: Langfuse" if langfuse else "追踪平台: 未配置")
    print("=" * 60)
    print("\n可用命令:")
    print("  demo   - 运行高级功能演示")
    print("  exit   - 退出程序")
    print("\n")

    # 默认 Session ID
    default_session = f"interactive-{int(time.time())}"

    while True:
        try:
            user_input = input("USER: ").strip()

            if user_input.lower() in ["quit", "exit", "q", "byebye", "拜拜", "再见"]:
                print("拜拜！祝您有美好的一天！")
                if langfuse:
                    langfuse.flush()
                break

            if user_input.lower() == "demo":
                demo_advanced_features()
                continue

            if not user_input:
                print("请输入有效指令或问题。")
                continue

            print("\n🔄 Agent 处理中...")
            response = chat_with_tracing(user_input, session_id=default_session)
            print(f"\nAI: {response}\n")

        except KeyboardInterrupt:
            print("\n再见！")
            if langfuse:
                langfuse.flush()
            break
        except Exception as e:
            print(f"\n发生错误: {e}\n")
