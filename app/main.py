from fastapi import FastAPI
from pydantic import BaseModel

from app.graph import build_graph

app = FastAPI(title="ReviewLens", version="0.2.0")
graph = build_graph()


class ChatIn(BaseModel):
    message: str
    thread_id: str = "default"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/chat")
def chat(body: ChatIn):
    result = graph.invoke({"messages": [("user", body.message)]})
    return {"reply": result["messages"][-1].content}
