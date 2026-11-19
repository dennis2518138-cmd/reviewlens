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
    review_note: str        # 人写的审核意见（只留痕，节点不读它——读的是 messages 里那条）
    review_rounds: int      # 已 reject 几次，硬闸用


def last_question(state: AnalysisState) -> str:
    """取用户这次真正问的那句话。

    必须倒着找 type == "human"，不能用 messages[-1]：挂了工具之后最后一条
    往往是 ToolMessage，直接取会把工具输出当成检索词（D4 实测过的回声 bug）。
    放在 AnalysisState 之后定义，因为注解要引用这个类。
    """
    return next((m.content for m in reversed(state["messages"]) if m.type == "human"), "")

