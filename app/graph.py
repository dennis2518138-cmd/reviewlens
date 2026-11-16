from langgraph.graph import END, START, StateGraph
from langchain_core.messages import SystemMessage

from app.config import get_llm
from app.rag import search
from app.state import AnalysisState

TOP_K = 3


def retrieve(state: AnalysisState):
    query = state["messages"][-1].content
    return {"retrieved": search(query, k=TOP_K)}


def call_model(state: AnalysisState):
    ctx = "\n".join(
        f"- [{h['brand']} {h['rating']}★ sim={h['similarity']:.2f}] {h['text']}"
        for h in state.get("retrieved") or []
    )
    msgs = [SystemMessage(
        content=f"只根据下列真实评论回答，引用时说明品牌与评分；评论里没有的，直接说没有。\n{ctx}"
    ), *state["messages"]]
    return {"messages": [get_llm().invoke(msgs)]}


def build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("retrieve", retrieve)
    builder.add_node("agent", call_model)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "agent")
    builder.add_edge("agent", END)
    return builder.compile()
