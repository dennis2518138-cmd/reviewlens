from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command, interrupt

from app.state import AnalysisState, last_question


MAX_REVIEW_ROUNDS = 2


def human_review(state: AnalysisState) -> dict:
    draft = state["messages"][-1].content
    rounds = state.get("review_rounds") or 0
    info = state.get("comparison") or {}
    decision = interrupt({
        "draft": draft,
        "asked": last_question(state),
        "worst": info.get("worst"),
        "best": info.get("best"),
        "round": rounds + 1,
        "max_rounds": MAX_REVIEW_ROUNDS,
    })

    if decision.get("decision") == "approve":
        return {"report": draft, "review_rounds": rounds + 1}

    note = (decision.get("note") or "").strip()
    rounds += 1
    if rounds >= MAX_REVIEW_ROUNDS:
        return {"review_rounds": rounds,
                "messages": [HumanMessage("【审核意见】" + note),
                             AIMessage("两轮审核未通过，本报告不予发布。"
                                       "已保留全部草稿与审核意见，可换口径重新提问。")]}
    return Command(goto="agent",
                   update={"review_note": note, "review_rounds": rounds,
                           "messages": [HumanMessage("【审核意见】" + note)]})
