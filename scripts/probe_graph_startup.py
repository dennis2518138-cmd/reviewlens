"""图启动耗时探针（零 LLM）：跑真图，但在 retrieve 的 on_chain_start 处 break。

retrieve 只做本地检索、不调 LLM，orchestrator 还没开始，所以全程零 token。
用途：把「从图启动到第一个节点」这段与应用无关的开销单独量出来。

用法（容器内）：
    docker compose exec -e APP_ROOT=/app -w /app web python /tmp/probe_graph_startup.py
用法（宿主）：
    APP_ROOT=D:/projects/reviewlens .venv/Scripts/python.exe scripts/probe_graph_startup.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.environ.get("APP_ROOT", "/app"))

from langchain_core.messages import HumanMessage  # noqa: E402


async def run_once(checkpointer, label: str) -> dict:
    import app.graph as g

    graph = g.build_graph(checkpointer=checkpointer)
    cfg = {"configurable": {"thread_id": f"probe-{label}"}}
    payload = {"messages": [HumanMessage(content="物流方面用户在抱怨什么？")]}
    t0 = time.monotonic()
    first_any = first_retrieve = None
    n = 0
    async for ev in graph.astream_events(payload, config=cfg, version="v2"):
        n += 1
        now = time.monotonic() - t0
        if first_any is None:
            first_any = now
        node = ev.get("metadata", {}).get("langgraph_node")
        if ev["event"] == "on_chain_start" and ev.get("name") == node == "retrieve":
            first_retrieve = now
            break
    return {"saver": label, "first_event": round(first_any or 0, 4),
            "retrieve_start": round(first_retrieve or 0, 4), "events": n}


async def main() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from app.persistence import checkpoint_lifespan

    out = []
    out.append(await run_once(MemorySaver(), "MemorySaver"))
    async with checkpoint_lifespan() as saver:
        out.append(await run_once(saver, "SqliteSaver"))
    for r in out:
        print(f"   [{r['saver']}] 首个事件={r['first_event']}s retrieve开始={r['retrieve_start']}s")


if __name__ == "__main__":
    asyncio.run(main())
