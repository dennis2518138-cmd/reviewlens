from typing import Annotated, TypedDict

from langgraph.graph import add_messages


class AnalysisState(TypedDict):

    messages: Annotated[list, add_messages]
    retrieved: list[dict]
    tool_rounds: int          # 已经请求过工具的第几轮，用来做成本闸
