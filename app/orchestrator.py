from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_llm
from app.state import AnalysisState, last_question

ALL_IDS = ["TWS-P3", "TWS-X1", "TWS-M2", "OB-A6", "OB-B2", "OB-C9"]
KNOWN_IDS = ", ".join(ALL_IDS)

Worker = Literal["rating", "topic", "compare", "report"]

WORKER_NODES = {"rating": "rating_analysis", "topic": "topic_analysis",
                "compare": "compare", "report": "agent"}


class Plan(BaseModel):

    workers: list[Worker] = Field(description="按执行顺序选工作者；用不上的不要选")
    reason: str = Field(description="为什么选这几个，一句话")


def pick_ids(text: str) -> list[str]:

    found = [pid for pid in ALL_IDS if pid in text]
    return found or list(ALL_IDS)


def plan_tasks(state: AnalysisState) -> dict:
    question = last_question(state)
    plan = (get_llm(temperature=0, thinking=False)
            .with_structured_output(Plan)
            .invoke([
                ("system", f"你是编排器。可用商品 ID：{KNOWN_IDS}。"
                           "只做规划，不要回答用户问题。"),
                ("user", question),
            ]))
    return {"plan": plan.workers}


def route_by_plan(state: AnalysisState):

    nodes = [WORKER_NODES[w] for w in (state.get("plan") or []) if w in WORKER_NODES]
    fan_out = [n for n in dict.fromkeys(nodes) if n not in ("agent", "compare")]
    return fan_out or ["agent"]