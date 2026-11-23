from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

NODE_LABELS = {
    "retrieve": "检索相关评论",
    "orchestrator": "拆解任务",
    "rating_analysis": "分析评分口碑",
    "topic_analysis": "统计话题分布",
    "compare": "跨口径对比",
    "agent": "撰写报告",
    "tools": "调用工具",
    "human_review": "等待人工审批",
}


async def stream_run(graph, payload, config) -> AsyncIterator[dict[str, Any]]:
    tid = config["configurable"]["thread_id"]
    yield {"event": "run.started", "data": {"thread_id": tid}}
    try:
        async for ev in graph.astream_events(payload, config=config, version="v2"):
            kind = ev["event"]
            node = ev.get("metadata", {}).get("langgraph_node")
            if kind == "on_chain_start" and node in NODE_LABELS and ev.get("name") == node:
                yield {"event": "node.progress",
                       "data": {"node": node, "label": NODE_LABELS[node]}}
            elif kind == "on_chat_model_stream" and node == "agent":
                piece = str(ev["data"]["chunk"].content)
                if piece:                       # 实测有 277 段里混着空串，别发空 delta
                    yield {"event": "assistant.delta", "data": {"text": piece}}
            elif kind == "on_tool_end":
                yield {"event": "tool.progress",
                       "data": {"tool": ev.get("name"), "node": node}}
    except Exception as e:
        yield {"event": "run.failed", "data": {"error": type(e).__name__,
                                               "detail": str(e)[:160]}}
        return

    st = await graph.aget_state(config)
    pend = bool(st.tasks and any(t.interrupts for t in st.tasks))
    if pend:
        yield {"event": "review.required",
               "data": st.tasks[0].interrupts[0].value | {"thread_id": tid}}
    else:
        report = (st.values or {}).get("report") or ""
        yield {"event": "run.completed",
               "data": {"status": "published" if report else "not_published",
                        "thread_id": tid,
                        "report": report or None}}