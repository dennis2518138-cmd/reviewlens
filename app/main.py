import uuid

from fastapi import FastAPI, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from app.graph import build_graph
from app.persistence import get_checkpointer

app = FastAPI(title="ReviewLens", version="0.3.0")
graph = build_graph(get_checkpointer())

RECURSION_LIMIT = 20


class ChatIn(BaseModel):
    message: str
    thread_id: str | None = None


class ReviewIn(BaseModel):
    thread_id: str
    decision: str
    note: str = ""


def _cfg(thread_id: str) -> dict:
    return {"recursion_limit": RECURSION_LIMIT, "configurable": {"thread_id": thread_id}}


def _outcome(result: dict, thread_id: str) -> dict:
    it = result.get("__interrupt__")
    if it:
        return {"status": "awaiting_review",
                "thread_id": thread_id,
                "request_id": it[0].id,
                "review": it[0].value}
    report = result.get("report") or ""
    if not report:
        return {"status": "not_published",
                "thread_id": thread_id,
                "detail": (result["messages"][-1].content or "")[:200]}
    return {"status": "published", "thread_id": thread_id, "report": report}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn):
    tid = body.thread_id or uuid.uuid4().hex[:12]
    try:
        result = graph.invoke({"messages": [("user", body.message)]}, config=_cfg(tid))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析中断：{type(e).__name__}") from e
    return _outcome(result, tid)


@app.post("/review")
def review(body: ReviewIn):
    from langgraph.types import Command
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision 只能是 approve 或 reject")
    st = graph.get_state(_cfg(body.thread_id))
    if not st.next:
        raise HTTPException(status_code=409, detail="这个会话没有待审批的草稿（已结束或不存在）")
    try:
        result = graph.invoke(Command(resume={"decision": body.decision, "note": body.note}),
                               config=_cfg(body.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复失败：{type(e).__name__}") from e
    return _outcome(result, body.thread_id)


@app.get("/review/{thread_id}")
def review_status(thread_id: str):
    st = graph.get_state(_cfg(thread_id))
    pend = bool(st.tasks and any(t.interrupts for t in st.tasks))
    return {"thread_id": thread_id,
            "pending": pend,
            "next_nodes": list(st.next or ()),
            "review": st.tasks[0].interrupts[0].value if pend else None,
            "report": st.values.get("report") or ""}