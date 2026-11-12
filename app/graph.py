from langgraph.graph import END, START, StateGraph

from app.config import get_llm
from app.state import AnalysisState


def call_model(state: AnalysisState):
    reply = get_llm().invoke(state["messages"])
    return {"messages": [reply]}


def build_graph():
    builder = StateGraph(AnalysisState)
    builder.add_node("agent", call_model)
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    return builder.compile()
