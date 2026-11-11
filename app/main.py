"""D2 起自己写。D2 只需一个 /chat；D7 加 SSE。

D2 验收：curl -s -X POST localhost:8000/chat -H 'content-type: application/json' -d '{"message":"你好"}' | jq
D7 验收：浏览器能看到打字机效果 + 审批按钮，中断后刷新还能恢复（靠 thread_id）
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="ReviewLens", version="0.1.0")


class ChatIn(BaseModel):
    message: str
    thread_id: str = "default"


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn) -> dict:
    # TODO D2: body.message -> graph.invoke({"messages": [("user", body.message)]},
    #          config={"configurable": {"thread_id": body.thread_id}})
    raise NotImplementedError("D2")


# TODO D7: /stream 用 astream_events(version="v2") 只挑 on_chat_model_stream 的 chunk 推 SSE
# TODO D7: /resume 用 Command(resume={"approved": true/false, "note": "..."}) 恢复中断
