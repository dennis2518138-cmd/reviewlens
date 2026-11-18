from __future__ import annotations

import json

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.config import get_llm
from app.orchestrator import pick_ids
from app.state import AnalysisState, last_question
from app.tools import keyword_extract, sentiment_stats


class Insight(BaseModel):

    finding: str = Field(description="一句话结论，只说数据支持的")
    numbers: list[str] = Field(description="支撑结论的数字，原样抄，例如 'TWS-M2 avg 3.47'")



def _judge(question: str, facts: dict, rule: str = "") -> Insight:

    return (get_llm(temperature=0, thinking=False)
            .with_structured_output(Insight)
            .invoke([
                ("system", "只根据给定的数据说话。数据里没有的数字不许出现。"
                           + rule +
                           f"数据：{json.dumps(facts, ensure_ascii=False)[:2000]}"),
                ("user", f"问题：{question}\n请给出结论和支撑结论的数字。"),
            ]))

# ---------- 子图 1：评分口碑 ----------

def fetch_rating(state: AnalysisState) -> dict:
    ids = pick_ids(last_question(state))
    raw = sentiment_stats.invoke({"product_ids": ids})
    return {"sentiment": {"rating_raw": raw.get("skus", {}),
                          "requested_ids": ids,
                          "rating_note": raw.get("note", "")}}


def judge_rating(state: AnalysisState) -> dict:
    s = state.get("sentiment") or {}
    data = s.get("rating_raw") or {}
    ins = _judge(last_question(state), {"评分分布": data})
    return {"sentiment": {"rating": data,
                          "rating_finding": ins.finding,
                          "rating_numbers": ins.numbers}}


def rating_subgraph():
    g = StateGraph(AnalysisState)
    g.add_node("fetch", fetch_rating)
    g.add_node("judge", judge_rating)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "judge")
    g.add_edge("judge", END)
    return g.compile()


# ---------- 子图 2：话题分布 ----------

def fetch_topics(state: AnalysisState) -> dict:
    ids = pick_ids(last_question(state))
    raw = keyword_extract.invoke({"product_ids": ids, "top_n": 6})
    return {"sentiment": {"topics_raw": raw.get("per_sku", {}),
                          "coverage": raw.get("topic_coverage", {})}}


def judge_topics(state: AnalysisState) -> dict:
    s = state.get("sentiment") or {}
    data = s.get("topics_raw") or {}
    ins = _judge(last_question(state), {"话题分布": data, "词典覆盖率": s.get("coverage")})
    return {"sentiment": {"topics": data,
                          "topics_finding": ins.finding,
                          "topics_numbers": ins.numbers}}


def topic_subgraph():
    g = StateGraph(AnalysisState)
    g.add_node("fetch", fetch_topics)
    g.add_node("judge", judge_topics)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "judge")
    g.add_edge("judge", END)
    return g.compile()


# ---------- 子图 3：对比（单节点，读前两个的结果）----------

CONFLICT_RULE = ("口径规则：判断整体口碑以评分均分为准；话题均分只用于定位具体维度。"
                 "两个口径结论不一致时，必须分别说清各自得出什么，不许只挑一个讲。\n")


def compare(state: AnalysisState) -> dict:
    s = state.get("sentiment") or {}
    ratings = s.get("rating") or {}
    if not ratings:

        return {}
    ranked = sorted(ratings.items(), key=lambda kv: kv[1].get("avg_rating", 0))
    facts = {
        "评分排序_从低到高": [k for k, _ in ranked],
        "各自均分": {k: v.get("avg_rating") for k, v in ratings.items()},
        "评分结论": s.get("rating_finding", ""),
        "话题结论": s.get("topics_finding", ""),
        "话题数字": s.get("topics_numbers", []),
    }
    ins = _judge(last_question(state), facts, CONFLICT_RULE)
    return {"comparison": {
        "worst": ranked[0][0] if ranked else None,
        "best": ranked[-1][0] if ranked else None,
        "avg_by_sku": {k: v.get("avg_rating") for k, v in ratings.items()},
        "finding": ins.finding,
        "numbers": ins.numbers,
    }}


# ---------- 挂到父图：只回传白名单里的键 ----------

def attach(subgraph, visible: tuple[str, ...]):

    def node(state: AnalysisState) -> dict:
        out = subgraph.invoke(state)
        return {k: out[k] for k in visible if k in out}
    return node