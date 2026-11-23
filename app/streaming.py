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
    """把 LangGraph 的 v2 事件流翻译成前端事件。不碰模型、不碰检查点、不碰 HTTP。

    两条实测出来的过滤条件是这段代码的全部难点：
    1) token 只认 node=="agent"。一次请求 on_chat_model_stream 共 663 段，agent 只占 277，
       其余来自 judge(197)/compare(148)/orchestrator(41)；后两者是结构化输出，
       不过滤前端就会先飘出半截 JSON。
    2) 进度只认 name==node。节点内部的多层 chain（RunnableSequence、_check_refusal、
       子图 LangGraph，甚至条件路由函数 route_by_plan / route_after_agent）都会发
       on_chain_start 且 langgraph_node 相同 —— 实测一次请求 11 条进度里只有 6 条是真的。
       这里不能用「见过就跳过」的集合去重：reject 回环时 agent 会真的跑第二遍，
       那种去重会把「正在重写」这条进度吞掉。
    """
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