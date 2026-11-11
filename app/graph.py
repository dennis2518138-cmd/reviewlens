"""D2 起自己写。这里只放接口约定，不放实现 —— 面试追问的就是这个文件里的每一行。

设计约束（来自方案文档）：
  - 编排器拆任务 → 采集/分析/对比/报告 4 个子图 → HITL 审核
  - 并行节点写同一字段必须靠 reducer，不能各自覆盖
  - thread_id 决定 checkpointer 的会话隔离
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

# ---- 待办 D2：最小图 ------------------------------------------------------
# graph = StateGraph(AnalysisState)
# graph.add_node("agent", call_model)
# graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
# app = graph.compile(checkpointer=...)   # D6 才接 checkpointer


class AnalysisState(TypedDict, total=False):
    messages: Annotated[list, add_messages]   # 对话历史（reducer 由 add_messages 提供）
    task_plan: list[str]        # 编排器拆出的任务
    raw_reviews: list[dict]     # 原始评论
    clean_reviews: list[dict]   # 清洗后
    sentiment: dict             # 情感统计
    comparison: dict            # 竞品对比
    report: str                 # 报告正文
    approved: bool              # 人工审核结果


# ---- D3：RAG -------------------------------------------------------------
def retrieve_reviews(state: AnalysisState) -> dict:
    """向量检索相似评论。用 app.config.get_embedder() + chroma collection。"""
    raise NotImplementedError("D3")


# ---- D4：三个工具（@tool 装饰）------------------------------------------
# clean_data / sentiment_stats / keyword_extract
# 注意：工具内部改写 state 时返回 dict，不要就地改


# ---- D5：子图 ------------------------------------------------------------
def build_ingest_subgraph():
    raise NotImplementedError("D5")


def build_analyze_subgraph():
    raise NotImplementedError("D5 分析子图：情感 + 关键词并行")


def build_compare_subgraph():
    raise NotImplementedError("D5 对比子图：RAG 检索历史评论 + 维度对比")


def build_report_subgraph():
    raise NotImplementedError("D5 报告子图：结构化输出，流式")


def orchestrator(state: AnalysisState) -> dict:
    """用 with_structured_output 产出 task_plan，再由条件边路由。"""
    raise NotImplementedError("D5")


# ---- D6：HITL -----------------------------------------------------------
def human_review(state: AnalysisState) -> dict:
    """interrupt() 挂起，approve → 出报告；reject → 回报告子图重写并带上驳回理由。"""
    raise NotImplementedError("D6")


def build_graph():
    raise NotImplementedError("D2/D5/D6")
