"""D7 前置探针：流式接口的真实形状（零 LLM 调用，用 FakeListChatModel）。

要回答四件事，这四件决定 /chat/stream 和前端怎么写：
1) v3 的 stream.messages 能不能拿到 token，**子图里的 token 能不能冒出来**（我们的 workers 用
   attach() 调子图且不传 config —— 事件会不会被子图吞掉，直接决定前端是打字机还是"卡住后整段出现"）
2) 图跑到 interrupt 时，v3 给什么（stream.interrupted / stream.interrupts），
   以及 v2 的 astream_events 里中断藏在哪个事件（两套 API 形状不同，不能猜）
3) 有没有异步版可用（FastAPI 的 SSE 必须是 async generator）
4) 三个并发流共用同一个 sqlite 连接会不会炸（D6 探针只验过同步 invoke 路径）

跑法：python scripts/probe_streaming_events.py
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from app.config import CHECKPOINT_DB
from app.state import merge_dict

FAKE = FakeListChatModel(responses=["续航普遍反馈虚标，实际比标注少约两小时。"])


class S(TypedDict):
    messages: Annotated[list, add_messages]
    sentiment: Annotated[dict, merge_dict]
    note: str


def child(with_interrupt=False):
    g = StateGraph(S)

    def judge(state):
        return {"messages": [FAKE.invoke("用一句话说续航口碑")]}

    g.add_node("judge", judge)
    g.add_edge(START, "judge")
    if with_interrupt:
        g.add_node("gate", lambda s: {"note": str(interrupt({"from": "子图", "round": 1}))})
        g.add_edge("judge", "gate")
        g.add_edge("gate", END)
    else:
        g.add_edge("judge", END)
    return g.compile()


def parent(*, sub_config=False, interrupt_in_parent=False, interrupt_in_child=False):
    """interrupt_in_child 时子图自己会中断，用来测'中断能不能穿过 attach 这层'。"""
    sub = child(with_interrupt=interrupt_in_child)

    def run_sub(state, config=None):
        out = sub.invoke(state, config=config) if sub_config else sub.invoke(state)
        return {k: out[k] for k in ("messages", "note") if k in out}

    def review(state):
        if not interrupt_in_parent:
            return {"sentiment": {"done": True}}
        return {"note": str(interrupt({"from": "父图review", "draft": "草稿正文"}))}

    g = StateGraph(S)
    g.add_node("retrieve", lambda s: {"note": "retrieved"})
    g.add_node("sub", run_sub)
    g.add_node("review", review)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "sub")
    g.add_edge("sub", "review")
    g.add_edge("review", END)
    from langgraph.checkpoint.memory import MemorySaver
    return g.compile(checkpointer=MemorySaver())


def p1_v3_subgraph():
    print("=== 1) v3 stream_events：token 与子图（attach 不传 config 的现状）===")
    g = parent()
    st = g.stream_events({"messages": [HumanMessage("续航怎么样？")], "sentiment": {}, "note": ""},
                         config={"configurable": {"thread_id": "v3a"}}, version="v3")
    print("   stream 对象可用属性:", [a for a in ("messages", "interrupted", "interrupts", "output", "values")
                                   if hasattr(st, a)])
    toks = []
    for msg in st.messages:
        for piece in msg.text:
            toks.append(piece)
    print("   拿到 token 段数:", len(toks), "| 内容:", "".join(toks)[:34])
    print("   ★ 子图里那次 FAKE.invoke 的 token 有没有出现在上面的流里:",
          "有" if toks else "没有 —— 前端就得靠节点事件而不是 token")
    print("   interrupted =", st.interrupted, "| interrupts =", st.interrupts)
    out = st.output
    print("   output 键:", sorted(out))


def p2_v3_interrupt():
    print("\n=== 2) v3 遇到 interrupt 给什么 ===")
    g = parent(interrupt_in_parent=True)
    st = g.stream_events({"messages": [HumanMessage("续航怎么样？")], "sentiment": {}, "note": ""},
                         config={"configurable": {"thread_id": "v3i"}}, version="v3")
    toks = [p for m in st.messages for p in m.text]
    print("   token 段数:", len(toks))
    print("   stream.interrupted =", st.interrupted)
    print("   stream.interrupts  =", st.interrupts)
    if st.interrupts:
        i0 = st.interrupts[0]
        print("   元素类型:", type(i0).__name__, "| 属性:", [a for a in dir(i0) if not a.startswith("_")])
        print("   value:", getattr(i0, "value", None))
    print("   output（中断时是什么）:", repr(getattr(st, "output", None))[:60])


def p2b_child_interrupt():
    print("\n=== 2b) 中断发生在子图里，v3 还报不报 interrupted ===")
    g = parent(interrupt_in_child=True)
    st = g.stream_events({"messages": [HumanMessage("续航怎么样？")], "sentiment": {}, "note": ""},
                         config={"configurable": {"thread_id": "v3ci"}}, version="v3")
    toks = [p for m in st.messages for p in m.text]
    print("   token:", len(toks), "| interrupted =", st.interrupted, "| interrupts =", st.interrupts)


def p3_v2_events():
    print("\n=== 3) v2 astream_events 的事件名（前端要订阅的到底是哪些）===")
    g = parent()
    seen = []

    async def run():
        async for ev in g.astream_events(
                {"messages": [HumanMessage("续航怎么样？")], "sentiment": {}, "note": ""},
                config={"configurable": {"thread_id": "v2a"}}, version="v2"):
            seen.append(ev)

    asyncio.run(run())
    kinds = Counter(e["event"] for e in seen)
    print("   事件统计:", dict(kinds))
    stream = [e for e in seen if e["event"] == "on_chat_model_stream"]
    if stream:
        e0 = stream[0]
        print("   第一个 on_chat_model_stream: node=", e0["metadata"].get("langgraph_node"),
              "| delta=", repr(str(e0["data"]["chunk"].content))[:22])
        nodes = Counter(e["metadata"].get("langgraph_node") for e in stream)
        print("   token 来自哪些节点:", dict(nodes), "← 空或不含 sub/judge 就说明子图 token 没冒上来")


def p4_async_sqlite():
    print("\n=== 4) 异步流共用同一个 sqlite 连接并发 ===")
    from langgraph.checkpoint.sqlite import SqliteSaver
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    saver = SqliteSaver(conn)

    sub = child()

    def build():
        g = StateGraph(S)
        g.add_node("retrieve", lambda s: {"note": "x"})
        g.add_node("sub", lambda s: {k: v for k, v in sub.invoke(s).items() if k == "messages"})
        g.add_edge(START, "retrieve")
        g.add_edge("retrieve", "sub")
        g.add_edge("sub", END)
        return g.compile(checkpointer=saver)

    g = build()

    async def one(i, tid):
        try:
            n = 0
            async for ev in g.astream_events(
                    {"messages": [HumanMessage(f"问题{i}")], "sentiment": {}, "note": ""},
                    config={"configurable": {"thread_id": tid}}, version="v2"):
                n += 1
            return f"{tid}:{n}事件"
        except Exception as e:
            return f"{tid}:{type(e).__name__} {str(e)[:50]}"

    async def main():
        return await asyncio.gather(*(one(i, f"conc-{i}") for i in range(3)))

    print("   三个不同 thread 并发:", asyncio.run(main()))

    async def same():
        return await asyncio.gather(one(9, "same-thread"), one(10, "same-thread"))

    print("   同一 thread 并发:", asyncio.run(same()))
    st = g.get_state({"configurable": {"thread_id": "same-thread"}})
    print("   同 thread 最后的历史:", [str(m.content)[:14] for m in st.values.get("messages", [])]
          or "（空 —— 上面那三次并发全失败了，检查点里当然什么都没有）")
    print("   ★ 结论：SqliteSaver 只实现同步方法，任何 await/async 路径都会 NotImplementedError")
    conn.close()


if __name__ == "__main__":
    p1_v3_subgraph()
    p2_v3_interrupt()
    p2b_child_interrupt()
    p3_v2_events()
    p4_async_sqlite()
    print("\n探针跑完。这四条决定 /chat/stream 的写法与前端的订阅方式。")
