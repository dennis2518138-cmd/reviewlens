"""D6 前置探针：Checkpointer 与 interrupt 的真实语义（零 LLM 调用，秒级）。

跑法：python scripts/probe_hitl_checkpoint.py
为什么存在：HITL 这一层有几个「看着成功、其实废掉」的坑，凭记忆假定 API 必错。
每条探针都打印实测原文，不改任何项目代码。
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import sqlite3
import sys
import threading
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

TMP = Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\cache\scratch"))


class ReviewState(TypedDict):
    draft: str
    approved: str


def build_review_graph(cp):
    """一个「先写草稿、再中断等审批」的最小图。"""
    g = StateGraph(ReviewState)
    g.add_node("make", lambda s: {"draft": "草稿：M2 更差，因为虚标"})

    def review(state):
        decision = interrupt({"report": state["draft"]})
        return {"approved": str(decision)}

    g.add_node("review", review)
    g.add_edge(START, "make")
    g.add_edge("make", "review")
    g.add_edge("review", END)
    return g.compile(checkpointer=cp)


def p1_saver_shape():
    print("=== 1. SqliteSaver 的构造形状 ===")
    print("   包在不在:", importlib.util.find_spec("langgraph.checkpoint.sqlite") is not None)
    from langgraph.checkpoint.sqlite import SqliteSaver
    print("   构造签名:", str(inspect.signature(SqliteSaver.__init__))[:110])
    print("   from_conn_string 返回类型:",
          inspect.signature(SqliteSaver.from_conn_string).return_annotation,
          "← Iterator 说明它是上下文管理器，with 结束就关连接，服务不能这么用")


def p2_interrupt_shape():
    print("\n=== 2. interrupt 在 invoke 下返回什么 ===")
    g = build_review_graph(MemorySaver())
    out = g.invoke({"draft": "", "approved": ""}, config={"configurable": {"thread_id": "t1"}})
    it = out["__interrupt__"]
    print("   返回的键:", sorted(out))
    print("   __interrupt__ 类型:", type(it).__name__, "| 元素属性:",
          [a for a in dir(it[0]) if not a.startswith("_")])
    print("   payload(value):", it[0].value)
    print("   ⚠ 中断前节点写的 draft 也在返回值里:", out.get("draft"))
    print("   → 所以 /chat 一次请求就能同时拿到「草稿」和「要审批」两件事")


def p3_resume():
    print("\n=== 3. resume 之后 ===")
    g = build_review_graph(MemorySaver())
    cfg = {"configurable": {"thread_id": "t3"}}
    g.invoke({"draft": "", "approved": ""}, config=cfg)
    r = g.invoke(Command(resume="approve"), config=cfg)
    print("   approved =", r["approved"], "| draft =", r["draft"])
    print("   → 恢复时 make 不会重跑：节点从被中断处重放，靠的是检查点里的 state")


def p4_no_checkpointer():
    print("\n=== 4. 忘了配 checkpointer 会怎样（最阴的一条）===")
    g = build_review_graph(None)
    cfg = {"configurable": {"thread_id": "t4"}}
    o = g.invoke({"draft": "", "approved": ""}, config=cfg)
    print("   第一次 invoke: 不报错、正常返回 __interrupt__ =", "__interrupt__" in o)
    try:
        g.invoke(Command(resume="approve"), config=cfg)
        print("   resume 也没报错？？")
    except Exception as e:
        print("   resume 才抛:", type(e).__name__, "-", str(e)[:80])
    print("   → 症状是「审批界面能弹出来，但点通过永远报错」。interrupt 必须与 checkpointer 同批上线")


def p5_cross_process():
    print("\n=== 5. 跨进程恢复（模拟刷新页面 / 服务重启）===")
    from langgraph.checkpoint.sqlite import SqliteSaver
    path = TMP / "_probe_ckpt.db"
    if path.exists():
        path.unlink()
    cfg = {"configurable": {"thread_id": "p5"}}
    conn = sqlite3.connect(str(path), check_same_thread=False)
    build_review_graph(SqliteSaver(conn)).invoke({"draft": "", "approved": ""}, config=cfg)
    conn.close()

    conn2 = sqlite3.connect(str(path), check_same_thread=False)
    g2 = build_review_graph(SqliteSaver(conn2))
    st = g2.get_state(config=cfg)
    print("   重开连接后 values =", st.values)
    print("   next（卡在哪个节点） =", st.next, "| 中断 payload 还在:",
          bool(st.tasks and st.tasks[0].interrupts))
    r = g2.invoke(Command(resume="reject：归因写清楚"), config=cfg)
    print("   新进程里 resume 成功 → approved =", r["approved"])
    print("   .db 大小 =", path.stat().st_size, "字节")
    conn2.close()
    path.unlink()


def p6_threads():
    print("\n=== 6. uvicorn 线程池下共用一个连接（check_same_thread 必须 False）===")
    from langgraph.checkpoint.sqlite import SqliteSaver
    path = TMP / "_probe_thread.db"
    for flag in (True, False):
        if path.exists():
            path.unlink()
        conn = sqlite3.connect(str(path), check_same_thread=flag)
        g = build_review_graph(SqliteSaver(conn))
        res = []

        def run(tid):
            try:
                g.invoke({"draft": "", "approved": ""},
                         config={"configurable": {"thread_id": tid}})
                res.append("ok")
            except Exception as e:
                res.append(f"{type(e).__name__}: {str(e)[:40]}")

        ts = [threading.Thread(target=run, args=(f"th{i}",)) for i in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        print(f"   check_same_thread={flag!s:5s} → {res[0]}")
        conn.close()
    if path.exists():
        path.unlink()
    print("   → True 时三个线程全炸 ProgrammingError。FastAPI 的同步端点跑在线程池里，")
    print("     所以连接必须在应用启动时建一次、全局复用，且 check_same_thread=False")


def p7_subgraph():
    print("\n=== 7. 中断发生在子图里（我们的 attach 不传 config）===")

    def child():
        c = StateGraph(ReviewState)
        c.add_node("inner", lambda s: {"approved": str(interrupt({"from": "子图"}))})
        c.add_edge(START, "inner")
        c.add_edge("inner", END)
        return c.compile()

    sub = child()

    def make_attach(pass_config):
        def node(state, config=None):
            o = sub.invoke(state, config=config) if pass_config else sub.invoke(state)
            return {k: o[k] for k in ("approved",) if k in o}
        return node

    for label, flag in (("不传 config", False), ("透传 config", True)):
        p = StateGraph(ReviewState)
        p.add_node("sub", make_attach(flag))
        p.add_edge(START, "sub")
        p.add_edge("sub", END)
        g = p.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "sub-" + label}}
        o = g.invoke({"draft": "", "approved": ""}, config=cfg)
        got = "__interrupt__" in o
        r = g.invoke(Command(resume="ok"), config=cfg) if got else None
        print(f"   {label}: 父图能收到中断={got} | resume 后 approved={r and r.get('approved')}")
    print("   → 本地 compiled 子图两种写法都能中断+恢复（langgraph 1.2.20 实测）")
    print("     注意这与 GitHub issue #4879 说的 RemoteGraph 不是一回事；透传 config 的好处是")
    print("     子图内部也能有自己的检查点，resume 时不必从头重跑子图（少烧 LLM 调用）")


if __name__ == "__main__":
    p1_saver_shape()
    p2_interrupt_shape()
    p3_resume()
    p4_no_checkpointer()
    p5_cross_process()
    p6_threads()
    p7_subgraph()
    print("\n全部探针跑完。这些结论是 D6 五步的依据，也是 D9/面试能引用的实测出处。")
