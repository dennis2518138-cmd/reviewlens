from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import SystemMessage

from app.config import get_llm
from app.rag import search
from app.state import AnalysisState
from app.tools import clean_data, keyword_extract, sentiment_stats

TOP_K = 3
MAX_TOOL_ROUNDS = 2
TOOLS = [clean_data, sentiment_stats, keyword_extract]
LLM_WITH_TOOLS = get_llm().bind_tools(TOOLS)
KNOWN_IDS = "TWS-P3, TWS-X1, TWS-M2, OB-A6, OB-B2, OB-C9"


def retrieve(state: AnalysisState):
    """检索：倒着找最后一条人类消息当查询词（挂工具后尾部可能是 ToolMessage）。"""
    query = next((m.content for m in reversed(state["messages"]) if m.type == "human"), "")
    if not query:
        return {"retrieved": []}
    return {"retrieved": search(query, k=TOP_K)}


def call_model(state: AnalysisState):
    ctx = "\n".join(
        f"- [{h['brand']} {h['rating']}★ sim={h['similarity']:.2f}] {h['text']}"
        for h in state.get("retrieved") or []
    )
    rounds = state.get("tool_rounds") or 0
    msgs = [SystemMessage(
        content="只根据下列真实评论回答，引用时说明品牌与评分；评论里没有的，直接说没有。\n"
                "如果统计或话题分布的信息不够，调用工具补充，不要凭检索到的三条猜整体。\n"
                f"工具的 product_ids 只能从这些里选：{KNOWN_IDS}\n" + ctx
    ), *state["messages"]]
    reply = LLM_WITH_TOOLS.invoke(msgs)
    out = {"messages": [reply]}
    if getattr(reply, "tool_calls", None):
        out["tool_rounds"] = rounds + 1      # 只在真的请求工具时计数，纯回答不消耗配额
    return out


def route_after_agent(state: AnalysisState):
    """成本闸：工具轮次到顶就结束，否则用官方判据决定去 tools 还是收工。"""
    if (state.get("tool_rounds") or 0) >= MAX_TOOL_ROUNDS:
        return END
    return tools_condition(state)


def build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(TOOLS))
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "agent")
    builder.add_conditional_edges("agent", route_after_agent)
    builder.add_edge("tools", "agent")
    return builder.compile()
