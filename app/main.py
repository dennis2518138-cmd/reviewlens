import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

from app.graph import build_graph
from app.persistence import checkpoint_lifespan
from fastapi import Request
from sse_starlette.sse import EventSourceResponse
from app.streaming import stream_run

RECURSION_LIMIT = 20

graph = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graph
    async with checkpoint_lifespan() as saver:
        graph = build_graph(saver)
        yield
    graph = None


app = FastAPI(title="ReviewLens", version="0.4.0", lifespan=lifespan)


class ChatIn(BaseModel):
    message: str
    thread_id: str | None = None


class ReviewIn(BaseModel):
    thread_id: str
    decision: str
    note: str = ""


def _cfg(thread_id: str) -> dict:
    return {"recursion_limit": RECURSION_LIMIT, "configurable": {"thread_id": thread_id}}


def _g():
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="服务没按 lifespan 启动：uvicorn 直接跑不会这样；"
                   "TestClient 必须 `with TestClient(app) as c:`，否则建图的那段不会执行")
    return graph


def _check_message(message: str) -> str:

    text = (message or "").strip()
    if len(text) < 2:
        raise HTTPException(status_code=422, detail="问题太短，至少写两个字，例如「物流」")
    return text


def _sse(part: dict) -> dict:

    return {"event": part["event"],
            "data": json.dumps(part["data"], ensure_ascii=False, default=str)}


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
async def chat(body: ChatIn):
    message = _check_message(body.message)
    tid = body.thread_id or uuid.uuid4().hex[:12]
    try:
        result = await _g().ainvoke({"messages": [("user", message)]}, config=_cfg(tid))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析中断：{type(e).__name__}") from e
    return _outcome(result, tid)


@app.post("/review")
async def review(body: ReviewIn):
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision 只能是 approve 或 reject")
    st = await _g().aget_state(_cfg(body.thread_id))
    if not st.next:
        raise HTTPException(status_code=409, detail="这个会话没有待审批的草稿（已结束或不存在）")
    try:
        result = await _g().ainvoke(
            Command(resume={"decision": body.decision, "note": body.note}),
            config=_cfg(body.thread_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"恢复失败：{type(e).__name__}") from e
    return _outcome(result, body.thread_id)


@app.get("/review/stream")
async def review_stream(request: Request, thread_id: str, decision: str, note: str = ""):
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision 只能是 approve 或 reject")
    g = _g()
    st = await g.aget_state(_cfg(thread_id))
    if not st.next:
        raise HTTPException(status_code=409, detail="这个会话没有待审批的草稿（已结束或不存在）")

    payload = Command(resume={"decision": decision, "note": note})

    async def gen():
        it = stream_run(g, payload, _cfg(thread_id)).__aiter__()
        try:
            while True:
                try:
                    part = await anext(it)
                except StopAsyncIteration:
                    break
                if await request.is_disconnected():
                    break
                yield _sse(part)
        finally:
            await it.aclose()

    return EventSourceResponse(gen(), ping=15)



@app.get("/review/{thread_id}")
async def review_status(thread_id: str):
    st = await _g().aget_state(_cfg(thread_id))
    pend = bool(st.tasks and any(t.interrupts for t in st.tasks))
    return {"thread_id": thread_id,
            "pending": pend,
            "next_nodes": list(st.next or ()),
            "review": st.tasks[0].interrupts[0].value if pend else None,
            "report": st.values.get("report") or ""}


@app.get("/chat/stream")
async def chat_stream(request: Request, message: str, thread_id: str | None = None):
    message = _check_message(message)
    tid = thread_id or uuid.uuid4().hex[:12]
    g = _g()

    async def gen():
        it = stream_run(g, {"messages": [("user", message)]}, _cfg(tid)).__aiter__()
        try:
            while True:
                try:
                    part = await anext(it)
                except StopAsyncIteration:
                    break
                if await request.is_disconnected():
                    break          # 客户端已走，再 yield 没人收；停止消费就是停止跑图
                yield _sse(part)
        finally:
            await it.aclose()

    return EventSourceResponse(gen(), ping=15)


app.mount("/", StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "web"),
                           html=True), name="web")
