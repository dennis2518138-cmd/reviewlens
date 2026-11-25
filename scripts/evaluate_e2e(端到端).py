"""D9 第④步：端到端真跑 5 次取中位数（改掉 README 里"样本 n=1"那句）。

口径：
  - 同一个问题连跑 5 次（测同一负载的方差，不是测不同负载）。
  - 每次走完整闭环：/chat/stream 提问 → 拿到草稿 → /review/stream approve 发布。
    approve 不重跑模型（实测 0 段 delta），所以真正的 LLM 成本在提问那半段。
  - 记录每个节点的到达时刻：这既是"进度事件"的验收，也是【冷启动判据】的数据源——
    第 1 次的 retrieve 若明显慢于第 2~5 次，说明那笔开销是 94MB ONNX 首次加载
    （get_embedder 是 lru_cache，一个进程只有一次），而不是 Docker 网络慢。
  - 首字延迟取『撰写报告』节点到达 ~ 第一段 assistant.delta 之间，与 D7 的口径一致。

⚠️ 一个必须写进结论的局限：同一问题连跑 5 次，DeepSeek 侧可能有 prompt cache，
   方差的"下限"会偏乐观。这里只报"同一负载 5 次的中位数与区间"，不许写成"稳定 X 秒"。
"""
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8800"
Q = "物流方面用户在抱怨什么？"
N = 5
ROOT = Path(__file__).resolve().parents[1]


def sse_events(resp):
    """线上格式两行：`event: 名称` + `data: {载荷}`。事件名在 event 行，不在 JSON 里。"""
    name = None
    for raw in resp.iter_lines():
        if raw.startswith("event:"):
            name = raw[6:].strip()
        elif raw.startswith("data:"):
            yield name, json.loads(raw[5:].strip())


def one_run(i: int) -> dict:
    t = time.time()
    timeline: dict[str, float] = {}
    deltas = 0
    first_delta = None
    agent_at = None
    draft_at = None
    draft_len = 0
    tid = None
    rnd = None
    with httpx.stream("GET", f"{BASE}/chat/stream", params={"message": Q}, timeout=300) as r:
        for ev, d in sse_events(r):
            now = round(time.time() - t, 3)
            if ev == "run.started":
                tid = d["thread_id"]
            elif ev == "node.progress":
                node = d["node"]
                timeline[node] = timeline.get(node, now)
                if node == "agent":
                    agent_at = now
            elif ev == "assistant.delta":
                deltas += 1
                if first_delta is None:
                    first_delta = now
            elif ev == "review.required":
                draft_at = now
                draft_len = len(d.get("draft") or "")
                rnd = d.get("round")

    # 审批发布（不重跑模型）
    t2 = time.time()
    with httpx.stream("GET", f"{BASE}/review/stream",
                      params={"thread_id": tid, "decision": "approve"}, timeout=300) as r:
        for ev, d in sse_events(r):
            if ev == "review.required":
                draft_at = round(time.time() - t, 3)
                draft_len = len(d.get("draft") or "")
                rnd = d.get("round")
            if ev == "run.completed":
                rep = d.get("report") or ""
                status = d.get("status")
    approve_at = round(time.time() - t2, 3)

    rec = {
        "run": i, "thread_id": tid, "round": rnd,
        "retrieve": timeline.get("retrieve"),
        "orchestrator": timeline.get("orchestrator"),
        "agent": agent_at, "first_delta": first_delta,
        "draft": draft_at, "draft_chars": draft_len,
        "deltas": deltas, "approve": approve_at,
        "total": round(time.time() - t, 3),
        "status": status, "report_chars": len(rep),
        "timeline": timeline,
    }
    print(f"  #{i} retrieve={rec['retrieve']} 『撰写报告』={agent_at} 首段delta={first_delta} "
          f"出草稿={draft_at}({draft_len}字,{deltas}段) 审批={approve_at} 全程={rec['total']} {status}")
    return rec


def med(key, runs):
    vals = [r[key] for r in runs if isinstance(r[key], (int, float))]
    return round(statistics.median(vals), 3) if vals else None


def main() -> None:
    print(f"目标 = {BASE}  问题 = 「{Q}」  次数 = {N}\n" + "=" * 70)
    runs = []
    for i in range(1, N + 1):
        runs.append(one_run(i))
        if i < N:
            time.sleep(1.0)

    print("=" * 70)
    print("=== 中位数（同负载 5 次）===")
    for key, label in (("retrieve", "首个节点 retrieve"), ("agent", "『撰写报告』节点"),
                       ("first_delta", "首段 delta（首字）"), ("draft", "出草稿"),
                       ("approve", "审批发布"), ("total", "全程"),
                       ("draft_chars", "草稿字数"), ("deltas", "delta 段数")):
        vals = [r[key] for r in runs if isinstance(r[key], (int, float))]
        if not vals:
            continue
        m = round(statistics.median(vals), 3)
        lo, hi = min(vals), max(vals)
        extra = "" if key not in ("draft_chars", "deltas") else "字" if key == "draft_chars" else "段"
        print(f"  {label:<16s} 中位数={m}{extra}   区间=[{lo}, {hi}]")

    # 冷启动判据：第 1 次 vs 后 4 次 的 retrieve
    r1 = runs[0]["retrieve"]
    rest = [r["retrieve"] for r in runs[1:] if isinstance(r["retrieve"], (int, float))]
    if r1 is not None and rest:
        m_rest = statistics.median(rest)
        print("\n=== 冷启动判据（94MB ONNX 首次加载 vs Docker 网络）===")
        print(f"  第 1 次 retrieve = {r1}s   第 2~{N} 次中位数 = {round(m_rest, 3)}s   差 = {round(r1 - m_rest, 3)}s")
        verdict = ("冷启动（首次加载模型）" if r1 - m_rest > 0.5 else "不是冷启动，差异在噪声范围内")
        print(f"  ★结论：{verdict}")

    out = {"suite": "D9-e4", "base": BASE, "question": Q, "n": N,
           "runs": runs,
           "median": {k: med(k, runs) for k in
                      ("retrieve", "orchestrator", "agent", "first_delta", "draft",
                       "approve", "total", "draft_chars", "deltas")},
           "cold_start": {"first_retrieve": r1, "rest_median_retrieve": (round(statistics.median(rest), 3) if rest else None)},
           "caveat": "同负载 5 次，DeepSeek 侧可能存在 prompt cache，区间下限偏乐观，不能写成恒定值"}
    (ROOT / "data" / "eval_e2e.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n落盘 -> data/eval_e2e.json")


if __name__ == "__main__":
    main()
