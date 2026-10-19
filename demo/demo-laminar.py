'''
AI Agent 可观测性 Demo - Laminar (lmnr) 集成

演示如何用 Laminar 监控 AI Agent 的运行过程。

Laminar 核心能力：
- Traces：追踪完整的请求链路
- Spans：记录子操作步骤（自动 + 手动）
- 自动 Instrumentation：无需修改代码即可追踪 OpenAI/Anthropic 等调用
- 手动 Instrumentation：使用 @observe() 装饰器追踪自定义函数
- Evaluations：运行评估并记录分数

环境变量配置（需要先创建 Laminar 账号获取）：
- LMNR_PROJECT_API_KEY：Laminar 项目 API Key
- LLM_API_KEY：大模型 API Key

自部署配置（可选）：
- LMNR_BASE_URL：Laminar 服务地址（默认 http://localhost）
- LMNR_HTTP_PORT：HTTP 端口（默认 8000）
- LMNR_GRPC_PORT：gRPC 端口（默认 8001）
'''

import os
import json
import time
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from deepagents import create_deep_agent

# ============================================================
# 1. 初始化配置
# ============================================================
api_key = os.environ.get("LLM_API_KEY")
if not api_key:
    raise ValueError("环境变量 LLM_API_KEY 未设置")

lmnr_project_api_key = os.environ.get("LMNR_PROJECT_API_KEY")
lmnr_base_url = os.environ.get("LMNR_BASE_URL")
lmnr_http_port = int(os.environ.get("LMNR_HTTP_PORT", "8000"))
lmnr_grpc_port = int(os.environ.get("LMNR_GRPC_PORT", "8001"))

if not lmnr_project_api_key:
    print("⚠️  警告: LMNR_PROJECT_API_KEY 未设置")
    print("   Laminar 追踪功能将不可用，但 Agent 仍可正常运行\n")
    laminar_available = False
else:
    laminar_available = True
    # 导入并初始化 Laminar
    from lmnr import Laminar, observe
    
    if lmnr_base_url:
        # 自部署模式
        Laminar.initialize(
            project_api_key=lmnr_project_api_key,
            base_url=lmnr_base_url,
            http_port=lmnr_http_port,
            grpc_port=lmnr_grpc_port,
        )
        print(f"✓ Laminar 已初始化（自部署: {lmnr_base_url}）")
    else:
        # 云端模式
        Laminar.initialize(project_api_key=lmnr_project_api_key)
        print("✓ Laminar 已初始化（云端模式）")

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
# 5. Laminar 监控封装
# ============================================================
if laminar_available:
    @observe()
    def agent_invoke_span(user_input: str, session_id: str = None) -> str:
        """
        使用 @observe() 装饰器追踪 Agent 调用。
        
        Laminar 追踪层次：
        └── Trace（一次完整的用户交互）
            └── Span: agent_invoke_span
                ├── 自动追踪: LLM 调用（OpenAI/Anthropic）
                └── 自动追踪: 工具执行
        """
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        messages = result["messages"]
        final_message = messages[-1]
        return final_message.content
else:
    def agent_invoke_span(user_input: str, session_id: str = None) -> str:
        """无 Laminar 时的 fallback 实现"""
        result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})
        return result["messages"][-1].content


def chat_with_tracing(user_input: str, session_id: str = None) -> str:
    """
    与 Agent 对话，并使用 Laminar 追踪完整链路。
    """
    if not laminar_available:
        return agent_invoke_span(user_input, session_id)

    from lmnr import Laminar
    
    start_time = time.time()
    
    # 使用 context manager 创建顶层 span
    with Laminar.start_as_current_span(
        name="agent-chat",
        input={"user_input": user_input, "session_id": session_id},
    ):
        response = agent_invoke_span(user_input, session_id)
        duration = time.time() - start_time
        
        # 设置 span 输出和元数据
        Laminar.set_span_output(response)
        Laminar.set_span_attribute("duration_seconds", round(duration, 2))
        Laminar.set_span_attribute("agent_type", "deepagents")
        Laminar.set_span_attribute("model", "qwen3.7-plus")
    
    return response


# ============================================================
# 6. 高级功能演示
# ============================================================
def demo_advanced_features():
    """演示 Laminar 高级功能"""

    if not laminar_available:
        print("⚠️  Laminar 未配置，跳过高级功能演示")
        return

    from lmnr import Laminar, observe

    print("\n" + "=" * 50)
    print("Laminar 高级功能演示")
    print("=" * 50)

    # --- 功能 1: 自动 Instrumentation ---
    print("\n📍 功能 1: 自动 Instrumentation")
    print("  Laminar 自动追踪 OpenAI/Anthropic 等库的调用")
    print("  无需修改代码即可获得详细的 LLM 调用追踪")
    
    response = chat_with_tracing("北京天气怎么样？", session_id="auto-instrument-demo")
    preview = response[:80] + "..." if len(response) > 80 else response
    print(f"  ✓ 自动追踪完成: {preview}")

    # --- 功能 2: 手动 Instrumentation (@observe 装饰器) ---
    print("\n📍 功能 2: 手动 Instrumentation (@observe)")
    
    @observe()
    def complex_workflow():
        """模拟一个复杂工作流"""
        
        @observe()
        def step1_parse():
            time.sleep(0.1)
            return "解析完成"
        
        @observe()
        def step2_execute():
            time.sleep(0.1)
            return "执行完成"
        
        @observe()
        def step3_validate():
            time.sleep(0.1)
            return "验证完成"
        
        r1 = step1_parse()
        r2 = step2_execute()
        r3 = step3_validate()
        
        return f"工作流完成: {r1}, {r2}, {r3}"
    
    result = complex_workflow()
    print(f"  ✓ 嵌套 @observe 追踪完成: {result}")

    # --- 功能 3: Context Manager Span ---
    print("\n📍 功能 3: Context Manager Span")
    
    with Laminar.start_as_current_span(name="custom-span-demo", input={"task": "数据处理"}):
        # 模拟子任务
        with Laminar.start_as_current_span(name="subtask-1", input={"data": "sample"}):
            time.sleep(0.1)
            Laminar.set_span_output({"status": "completed"})
        
        with Laminar.start_as_current_span(name="subtask-2", input={"data": "processed"}):
            time.sleep(0.1)
            Laminar.set_span_output({"status": "completed"})
        
        Laminar.set_span_output({"final": "all done"})
    
    print("  ✓ Context Manager Span 追踪完成")

    # --- 功能 4: 设置自定义属性 ---
    print("\n📍 功能 4: 自定义属性")
    
    with Laminar.start_as_current_span(name="attributes-demo"):
        Laminar.set_span_attribute("user_id", "demo-user-123")
        Laminar.set_span_attribute("request_type", "complex_query")
        Laminar.set_span_attribute("priority", "high")
        Laminar.set_span_attribute("version", "1.0.0")
        Laminar.set_span_output("属性设置完成")
    
    print("  ✓ 自定义属性已记录")

    # --- 功能 5: Session 追踪 ---
    print("\n📍 功能 5: Session 追踪（多轮对话）")
    session_id = f"session-{int(time.time())}"
    
    queries = [
        "上海今天天气怎么样？",
        "帮我算一下 123 * 456",
        "谢谢！",
    ]
    
    for i, query in enumerate(queries, 1):
        print(f"  [{i}/{len(queries)}] 用户: {query}")
        response = chat_with_tracing(query, session_id=session_id)
        preview = response[:80] + "..." if len(response) > 80 else response
        print(f"       AI: {preview}")
    
    print(f"  ✓ Session '{session_id}' 已记录")

    print("\n" + "=" * 50)
    print("演示完成！请登录 Laminar 控制台查看详细追踪信息")
    print("云端地址: https://app.lmnr.ai")
    print("=" * 50)


# ============================================================
# 7. 主程序
# ============================================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("AI Agent Demo - Laminar (lmnr) 可观测性集成")
    print("=" * 60)
    print("已注册工具: get_weather, calculate (Mock)")
    print("追踪平台: Laminar" if laminar_available else "追踪平台: 未配置")
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
            break
        except Exception as e:
            print(f"\n发生错误: {e}\n")
