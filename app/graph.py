import json

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import SystemMessage

from app.config import get_llm
from app.rag import search
from app.state import AnalysisState, last_question
from app.tools import clean_data, keyword_extract, sentiment_stats

from app.orchestrator import KNOWN_IDS, plan_tasks, route_by_plan
from app.workers import attach, compare, rating_subgraph, topic_subgraph

TOP_K = 3
MAX_TOOL_ROUNDS = 2
TOOLS = [clean_data, sentiment_stats, keyword_extract]
LLM_WITH_TOOLS = get_llm().bind_tools(TOOLS)



def retrieve(state: AnalysisState):
    query = last_question(state)
    if not query:
        return {"retrieved": []}
    return {"retrieved": search(query, k=TOP_K)}


def _brief(state: AnalysisState) -> str:

    s = state.get("sentiment") or {}
    c = state.get("comparison") or {}
    lines = []
    for key, label in (("rating_finding", "评分结论"),
                       ("topics_finding", "话题结论"),
                       ("rating_note", "口径说明")):
        if s.get(key):
            lines.append(f"- {label}：{s[key]}")
    if s.get("coverage"):
        cov = s["coverage"]
        if isinstance(cov, dict):
            cov = json.dumps(cov, ensure_ascii=False)
        lines.append(f"- 词典可靠性：{cov}")
    if c:
        lines.append(f"- 综合结论：{c.get('finding', '')}")
        lines.append(f"- 最差/最好：{c.get('worst')} / {c.get('best')}；均分：{c.get('avg_by_sku')}")
    return "\n".join(lines)


def call_model(state: AnalysisState):
    ctx = "\n".join(
        f"- [{h['brand']} {h['rating']}★ sim={h['similarity']:.2f}] {h['text']}"
        for h in state.get("retrieved") or []
    )
    brief = _brief(state)
    rounds = state.get("tool_rounds") or 0
    sys = ("只根据下列真实评论回答，引用时说明品牌与评分；评论里没有的，直接说没有。\n"
           f"工具的 product_ids 只能从这些里选：{KNOWN_IDS}\n")
    if brief:
        sys += ("分析节点已经算出下面这些结论，直接采用，"
                "不要为了同样的数字再调一次工具；只有结论明确不够时才补调。\n"
                + brief + "\n")
    sys += "检索到的相关评论：\n" + ctx
    msgs = [SystemMessage(content=sys), *state["messages"]]
    reply = LLM_WITH_TOOLS.invoke(msgs)
    out = {"messages": [reply]}
    if getattr(reply, "tool_calls", None):
        out["tool_rounds"] = rounds + 1
    return out


def route_after_agent(state: AnalysisState):
    if (state.get("tool_rounds") or 0) >= MAX_TOOL_ROUNDS:
        return END
    return tools_condition(state)


def build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("orchestrator", plan_tasks)
    builder.add_node("rating_analysis", attach(rating_subgraph(), ("sentiment",)))
    builder.add_node("topic_analysis", attach(topic_subgraph(), ("sentiment",)))
    builder.add_node("compare", compare)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(TOOLS))

    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "orchestrator")
    builder.add_conditional_edges("orchestrator", route_by_plan,
                                  ["rating_analysis", "topic_analysis", "agent"])
    builder.add_edge("rating_analysis", "compare")
    builder.add_edge("topic_analysis", "compare")
    builder.add_edge("compare", "agent")

    builder.add_conditional_edges("agent", route_after_agent,
                                  {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")
    return builder.compile()
