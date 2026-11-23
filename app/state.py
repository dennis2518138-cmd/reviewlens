from typing import Annotated, TypedDict
from langgraph.graph import add_messages


def merge_dict(old: dict | None, new: dict | None) -> dict:
    out = dict(old or {})
    out.update(new or {})
    return out


class AnalysisState(TypedDict):
    messages: Annotated[list, add_messages]
    retrieved: list[dict]
    tool_rounds: int
    plan: list[str]
    sentiment: Annotated[dict, merge_dict]
    comparison: dict
    report: str
    review_note: str
    review_rounds: int


def last_question(state: AnalysisState) -> str:
    return next((m.content for m in reversed(state["messages"]) if m.type == "human"), "")

